from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx

from app.infra.logging import get_logger
from app.infra.metrics import get_metrics
from app.infra.settings import Settings, get_settings

from .prompt import MessagePayload

logger = get_logger(__name__)


class GigaChatError(Exception):
    pass


@dataclass(slots=True)
class GigaChatUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(slots=True)
class GigaChatResult:
    text: str
    usage: GigaChatUsage


class GigaChatClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        timeout = httpx.Timeout(
            timeout=self._settings.request_timeout_sec,
            connect=self._settings.connect_timeout_sec,
        )
        base_url = str(self._settings.gigachat_base_url).rstrip("/")
        self._verify_ssl = self._settings.gigachat_verify_ssl
        if not self._verify_ssl:
            logger.warning("gigachat_ssl_verification_disabled")

        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            verify=self._verify_ssl,
        )
        self._auth_client = httpx.AsyncClient(timeout=timeout, verify=self._verify_ssl)
        self._metrics = get_metrics(self._settings)

        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._token_force_refresh_interval = max(0, int(self._settings.gigachat_token_force_refresh_interval))
        self._next_forced_refresh: float = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()
        await self._auth_client.aclose()

    async def chat(self, messages: List[MessagePayload], session_id: str | None = None) -> GigaChatResult:
        await self._ensure_token()
        assert self._access_token, "Token must be available"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if session_id:
            headers["X-Session-ID"] = session_id
        payload = {
            "model": self._settings.gigachat_model,
            "messages": list(messages),
        }
        data = await self._request_with_retry(
            method="POST",
            url=self._settings.gigachat_chat_path,
            json=payload,
            headers=headers,
        )
        result_text = self._extract_text(data)
        usage = self._extract_usage(data)
        if usage.prompt_tokens:
            self._metrics.add_tokens(tokens_in=usage.prompt_tokens)
        if usage.completion_tokens:
            self._metrics.add_tokens(tokens_out=usage.completion_tokens)
        return GigaChatResult(text=result_text, usage=usage)

    async def _ensure_token(self) -> None:
        async with self._lock:
            now = time.time()
            if self._access_token:
                refresh_deadline = self._expires_at - self._settings.gigachat_token_refresh_reserve
                if now < refresh_deadline:
                    if not self._next_forced_refresh or now < self._next_forced_refresh:
                        return
            await self._refresh_token()

    async def _refresh_token(self) -> None:
        logger.info("gigachat_refresh_token")
        auth_header = base64.b64encode(
            f"{self._settings.gigachat_client_id}:{self._settings.gigachat_client_secret}".encode("utf-8")
        ).decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid4()),
        }
        payload = {
            "scope": self._settings.gigachat_scope,
            "grant_type": "client_credentials",
        }
        auth_url = str(self._settings.gigachat_auth_url)
        try:
            response = await self._auth_client.post(auth_url, headers=headers, data=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            resp = exc.response
            detail = ""
            if resp is not None:
                snippet = resp.text[:200] if resp.text else ""
                detail = f" status={resp.status_code} body={snippet!r}"
            raise GigaChatError(f"Не удалось запросить токен GigaChat: {exc}.{detail}") from exc
        except httpx.HTTPError as exc:
            raise GigaChatError(f"Не удалось запросить токен GigaChat: {exc}") from exc
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise GigaChatError("Авторизация GigaChat не вернула access_token")
        now = time.time()
        expires_at_raw = data.get("expires_at")
        expires_in_raw = data.get("expires_in")
        if expires_at_raw:
            try:
                self._expires_at = float(expires_at_raw)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "gigachat_invalid_expires_at",
                    extra={"value": expires_at_raw, "error": str(exc)},
                )
                self._expires_at = now + int(expires_in_raw or 600)
        elif expires_in_raw:
            self._expires_at = now + int(expires_in_raw)
        else:
            self._expires_at = now + 600
        self._access_token = token
        if self._token_force_refresh_interval > 0:
            self._next_forced_refresh = now + self._token_force_refresh_interval
        else:
            self._next_forced_refresh = 0.0

    async def _handle_unauthorized(self) -> None:
        logger.warning("gigachat_unauthorized_refresh")
        async with self._lock:
            self._access_token = None
            self._expires_at = 0.0
            self._next_forced_refresh = 0.0
            await self._refresh_token()

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        json: Dict[str, Any],
        headers: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        backoff = [0.5, 1.0, 2.0]
        last_error: Exception | None = None
        for attempt, delay in enumerate(backoff, start=1):
            try:
                response = await self._client.request(method, url, json=json, headers=headers)
                if response.status_code == 401:
                    last_error = httpx.HTTPStatusError(
                        "Unauthorized",
                        request=response.request,
                        response=response,
                    )
                    logger.warning(
                        "gigachat_request_unauthorized",
                        extra={"attempt": attempt},
                    )
                    self._metrics.inc_error("gigachat")
                    await self._handle_unauthorized()
                    await asyncio.sleep(delay)
                    continue
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        "Retryable GigaChat error",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                logger.warning(
                    "gigachat_request_retry",
                    extra={
                        "status_code": exc.response.status_code if exc.response else None,
                        "attempt": attempt,
                    },
                )
                self._metrics.inc_error("gigachat")
                await asyncio.sleep(delay)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.error("gigachat_http_error", extra={"error": str(exc)})
                self._metrics.inc_error("gigachat")
                break
        raise GigaChatError(str(last_error)) from last_error

    def _extract_text(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices")
        if not choices:
            raise GigaChatError("Ответ GigaChat не содержит поля choices")
        first = choices[0]
        message = first.get("message") or first.get("delta") or {}
        content = message.get("content")
        if isinstance(content, list):
            # некоторые модели возвращают список частей
            text_parts = [part.get("text", "") if isinstance(part, dict) else str(part) for part in content]
            content = "".join(text_parts)
        if not isinstance(content, str):
            raise GigaChatError("Не удалось извлечь текст из ответа GigaChat")
        return content

    def _extract_usage(self, data: Dict[str, Any]) -> GigaChatUsage:
        usage_data = data.get("usage", {})
        prompt_tokens = usage_data.get("prompt_tokens")
        completion_tokens = usage_data.get("completion_tokens")
        return GigaChatUsage(
            prompt_tokens=int(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else None,
            completion_tokens=int(completion_tokens) if isinstance(completion_tokens, (int, float)) else None,
        )


_gigachat_client: GigaChatClient | None = None


def get_gigachat_client(settings: Settings | None = None) -> GigaChatClient:
    global _gigachat_client
    if _gigachat_client:
        return _gigachat_client
    settings = settings or get_settings()
    _gigachat_client = GigaChatClient(settings)
    return _gigachat_client
