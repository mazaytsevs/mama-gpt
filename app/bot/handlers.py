from __future__ import annotations

import uuid
from typing import Any, Dict, List, Tuple

from app.infra.logging import get_logger
from app.infra.metrics import get_metrics
from app.infra.settings import ParseMode, Settings, get_settings
from app.llm.gigachat_client import GigaChatClient, GigaChatError, get_gigachat_client
from app.llm.history import ConversationHistory, get_history
from app.llm.prompt import MessagePayload, PromptManager, get_prompt_manager

from .auth import is_user_allowed
from .commands import CommandContext, CommandError, CommandResponse, handle_command
from .formatter import format_response
from .telegram import TelegramAPIError, TelegramClient, get_telegram_client


def _augment_follow_up(text: str, history: List[MessagePayload]) -> str:
    normalized = text.strip().lower()
    confirmations = {"да", "ага", "угу", "конечно", "да, конечно", "давай", "хочу"}
    if normalized in confirmations:
        last_assistant = next((msg["content"] for msg in reversed(history) if msg["role"] == "assistant"), "")
        last_user = next((msg["content"] for msg in reversed(history) if msg["role"] == "user"), "")
        hints = []
        if last_assistant:
            hints.append(f"Пожалуйста, продолжай отвечать на свой предыдущий вопрос: {last_assistant}")
        if last_user:
            hints.append(f"Контекст запроса: {last_user}")
        if hints:
            return f"{text}. {' '.join(hints)}"
    return text

logger = get_logger(__name__)

MAX_MESSAGE_CHARS = 3500
UNAUTHORIZED_REPLY = "Извини, бот доступен только для Маши и мамы."
UNSUPPORTED_MESSAGE = "Я пока умею отвечать только на текстовые вопросы."
LONG_MESSAGE_REPLY = "Слишком длинный текст, попробуй разделить на несколько сообщений."
VOICE_NOT_SUPPORTED = "Пока понимаю только текст. Если можешь — напиши вопрос текстом."


class UpdateHandler:
    def __init__(
        self,
        settings: Settings,
        prompt_manager: PromptManager,
        history: ConversationHistory,
        llm: GigaChatClient,
        telegram_client: TelegramClient | None = None,
    ):
        self._settings = settings
        self._prompt_manager = prompt_manager
        self._history = history
        self._llm = llm
        self._telegram = telegram_client or get_telegram_client(self._settings)
        self._metrics = get_metrics(self._settings)
        self._parse_mode = self._settings.reply_parse_mode
        self._session_ids: Dict[int, str] = {}

    async def handle(self, update: Dict[str, Any]) -> None:
        request_id = str(update.get("update_id", uuid.uuid4()))
        message, source_type = self._extract_message(update)
        if not message:
            logger.info(
                "update_skipped",
                extra={"reason": "no_message", "request_id": request_id},
            )
            return

        user = message.get("from") or {}
        user_id = user.get("id")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if user_id is None or chat_id is None:
            logger.warning("update_missing_ids", extra={"request_id": request_id})
            return

        self._metrics.inc_request("tg", source_type)
        logger.info(
            "incoming_update",
            extra={
                "request_id": request_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "type": source_type,
            },
        )

        if not is_user_allowed(user_id, self._settings):
            self._metrics.inc_error("auth")
            await self._reply(chat_id, UNAUTHORIZED_REPLY, message.get("message_id"))
            return

        # Commands
        if text := message.get("text"):
            text = text.strip()
            if not text:
                await self._reply(chat_id, "Пока вижу только пустое сообщение.", message.get("message_id"))
                return
            if text.startswith("/"):
                await self._handle_command(text, chat_id, user_id, message)
                return
            await self._handle_text(text, user_id, chat_id, message, request_id)
            return

        if "voice" in message:
            await self._reply(chat_id, VOICE_NOT_SUPPORTED, message.get("message_id"))
            return

        if "photo" in message or "document" in message:
            caption = message.get("caption")
            reply = UNSUPPORTED_MESSAGE
            if caption:
                reply += " Я вижу подпись, попробую ответить по ней."
                await self._handle_text(caption, user_id, chat_id, message, request_id)
                return
            await self._reply(chat_id, UNSUPPORTED_MESSAGE, message.get("message_id"))
            return

        logger.info(
            "update_unhandled_type",
            extra={"request_id": request_id, "keys": list(message.keys())},
        )
        await self._reply(chat_id, UNSUPPORTED_MESSAGE, message.get("message_id"))

    def _extract_message(self, update: Dict[str, Any]) -> Tuple[Dict[str, Any] | None, str]:
        if "message" in update:
            return update["message"], self._detect_kind(update["message"])
        if "edited_message" in update and self._settings.process_edited_messages:
            return update["edited_message"], self._detect_kind(update["edited_message"])
        if "callback_query" in update:
            callback = update["callback_query"]
            message = callback.get("message")
            if message:
                return message, "callback"
        return None, "unknown"

    def _detect_kind(self, message: Dict[str, Any]) -> str:
        if "voice" in message:
            return "voice"
        if "photo" in message:
            return "photo"
        if "document" in message:
            return "document"
        if message.get("text"):
            return "text"
        return "other"

    async def _handle_command(
        self,
        text: str,
        chat_id: int,
        user_id: int,
        message: Dict[str, Any],
    ) -> None:
        parts = text.split()
        name = parts[0]
        args = parts[1:]
        ctx = CommandContext(
            user_id=user_id,
            chat_id=chat_id,
            request_id=str(message.get("message_id", "")),
            settings=self._settings,
            parse_mode=self._parse_mode,
            prompt_manager=self._prompt_manager,
            history=self._history,
            metrics=self._metrics,
        )
        try:
            response: CommandResponse = await handle_command(name, args, ctx)
        except CommandError as exc:
            await self._reply(chat_id, str(exc), message.get("message_id"))
            return
        parsed_mode = response.parse_mode or self._parse_mode
        await self._reply(chat_id, response.text, message.get("message_id"), parse_mode=parsed_mode)

    async def _handle_text(
        self,
        text: str,
        user_id: int,
        chat_id: int,
        message: Dict[str, Any],
        request_id: str,
    ) -> None:
        if len(text) > MAX_MESSAGE_CHARS:
            await self._reply(chat_id, LONG_MESSAGE_REPLY, message.get("message_id"))
            return
        history_messages: list[MessagePayload] = await self._history.load(user_id)

        text = _augment_follow_up(text, history_messages)

        await self._history.append(user_id, "user", text)

        messages = await self._prompt_manager.build_messages(history_messages, text)
        session_id = self._session_ids.setdefault(user_id, uuid.uuid4().hex)

        try:
            with self._metrics.latency_timer():
                result = await self._llm.chat(messages, session_id=session_id)
        except GigaChatError as exc:
            logger.error(
                "gigachat_failed",
                extra={"error": str(exc), "request_id": request_id},
            )
            self._metrics.inc_error("gigachat")
            await self._reply(
                chat_id,
                "Я сейчас не могу получить ответ. Давай попробуем ещё раз через пару минут.",
                message.get("message_id"),
            )
            return

        response_text = format_response(result.text, self._parse_mode)
        await self._reply(chat_id, response_text, message.get("message_id"), parse_mode=self._parse_mode)
        await self._history.append(user_id, "assistant", result.text)

    async def _reply(
        self,
        chat_id: int,
        text: str,
        reply_to: int | None,
        parse_mode: ParseMode | None = None,
    ) -> None:
        try:
            await self._telegram.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode or self._parse_mode,
                reply_to_message_id=reply_to,
            )
        except TelegramAPIError as exc:
            logger.error(
                "telegram_send_failed",
                extra={"chat_id": chat_id, "error": str(exc)},
            )


_handler: UpdateHandler | None = None


def get_update_handler(settings: Settings | None = None) -> UpdateHandler:
    global _handler
    if _handler:
        return _handler
    settings = settings or get_settings()
    prompt_manager = get_prompt_manager()
    history = get_history(settings)
    gigachat = get_gigachat_client(settings)
    _handler = UpdateHandler(settings, prompt_manager, history, gigachat)
    return _handler


async def route(update: Dict[str, Any]) -> None:
    handler = get_update_handler()
    await handler.handle(update)
