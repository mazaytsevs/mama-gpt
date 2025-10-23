from __future__ import annotations

from asyncio import Lock
from dataclasses import dataclass
from typing import Iterable, List, TypedDict

from app.infra.settings import BotMode, get_settings


class MessagePayload(TypedDict):
    role: str
    content: str


BASE_PROMPT = (
    "Ты — дружелюбный помощник для Юлии (мамы Маши). Объясняй по делу простыми словами, "
    "но не обрезай полезные подробности: если человек просит рецепт или инструкцию — дай полный, понятный план с шагами, "
    "ингредиентами и временными подсказками. Если вопрос короткий и не требует деталей, отвечай кратко. "
    "Если вопрос неполный — уточни одно, самое важное, и дождись ответа; не задавай несколько уточнений подряд. "
    "Никогда не обещай того, чего не можешь сделать. Если не уверена — честно скажи и предложи безопасный совет. "
    "Никакой токсичности и категоричности в медицине и праве. Пиши по-русски, уважительно и тепло, допускается один-два дружелюбных эмодзи в ответе, если они уместны."
)

CONCISE_SUFFIX = (
    " Если задан режим concise — держи ответы ясными и короче обычного, без дополнительных уточнений, если пользователь прямо об этом не просит."
)


@dataclass
class PromptState:
    mode: BotMode


class PromptManager:
    def __init__(self, default_mode: BotMode):
        self._state = PromptState(mode=default_mode)
        self._lock = Lock()

    @property
    def mode(self) -> BotMode:
        return self._state.mode

    async def set_mode(self, mode: BotMode) -> None:
        async with self._lock:
            self._state.mode = mode

    async def get_system_prompt(self) -> str:
        mode = self.mode
        if mode == BotMode.CONCISE:
            return f"{BASE_PROMPT}{CONCISE_SUFFIX}"
        return BASE_PROMPT

    async def build_messages(
        self,
        history: Iterable[MessagePayload],
        user_message: str,
    ) -> List[MessagePayload]:
        system_prompt = await self.get_system_prompt()
        messages: List[MessagePayload] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        return messages


_prompt_manager: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    global _prompt_manager
    if _prompt_manager:
        return _prompt_manager
    settings = get_settings()
    _prompt_manager = PromptManager(default_mode=settings.default_mode)
    return _prompt_manager
