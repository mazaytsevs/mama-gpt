from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from app.infra.logging import get_logger
from app.infra.metrics import get_metrics
from app.infra.settings import ParseMode, Settings, get_settings

logger = get_logger(__name__)


class TelegramAPIError(Exception):
    pass


TELEGRAM_API_BASE = "https://api.telegram.org"


def _build_base_url(settings: Settings) -> str:
    return f"{TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}"


@dataclass
class TelegramResponse:
    ok: bool
    result: Any | None = None
    description: str | None = None


class TelegramClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        timeout = httpx.Timeout(
            timeout=self._settings.request_timeout_sec,
            connect=self._settings.connect_timeout_sec,
        )
        self._client = httpx.AsyncClient(timeout=timeout)
        self._base_url = _build_base_url(self._settings)
        self._metrics = get_metrics(self._settings)

    async def close(self) -> None:
        await self._client.aclose()

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: ParseMode | None = None,
        reply_to_message_id: int | None = None,
        disable_web_page_preview: bool = True,
    ) -> TelegramResponse:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode.value
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        response = await self._post("/sendMessage", json=payload)
        return response

    async def get_updates(self, offset: int | None = None, timeout: int = 10) -> TelegramResponse:
        payload: Dict[str, Any] = {"timeout": timeout}
        if offset:
            payload["offset"] = offset
        response = await self._post("/getUpdates", json=payload)
        return response

    async def _post(self, path: str, json: Dict[str, Any]) -> TelegramResponse:
        url = f"{self._base_url}{path}"
        backoff = [0.5, 1.0, 2.0]
        last_error: Exception | None = None
        for attempt, delay in enumerate(backoff, start=1):
            try:
                response = await self._client.post(url, json=json)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        "Retryable Telegram error",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                data = response.json()
                if not data.get("ok", False):
                    raise TelegramAPIError(data.get("description", "Unknown Telegram error"))
                return TelegramResponse(ok=True, result=data.get("result"), description=data.get("description"))
            except httpx.HTTPStatusError as exc:
                last_error = exc
                logger.warning(
                    "telegram_request_failed",
                    extra={
                        "status_code": exc.response.status_code if exc.response else None,
                        "attempt": attempt,
                    },
                )
                self._metrics.inc_error("telegram")
                await asyncio.sleep(delay)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.error("telegram_http_error", extra={"error": str(exc)})
                self._metrics.inc_error("telegram")
                break
            except TelegramAPIError as exc:
                last_error = exc
                logger.error("telegram_api_error", extra={"error": str(exc)})
                self._metrics.inc_error("telegram")
                break
        raise TelegramAPIError(str(last_error)) from last_error


_telegram_client: TelegramClient | None = None


def get_telegram_client(settings: Settings | None = None) -> TelegramClient:
    global _telegram_client
    if _telegram_client:
        return _telegram_client
    settings = settings or get_settings()
    _telegram_client = TelegramClient(settings)
    return _telegram_client
