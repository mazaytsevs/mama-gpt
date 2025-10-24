from __future__ import annotations

import json
from enum import Enum
from functools import lru_cache
from typing import Optional, Set

from pydantic import AnyHttpUrl, Field, HttpUrl, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ParseMode(str, Enum):
    MARKDOWN = "Markdown"
    HTML = "HTML"


class BotMode(str, Enum):
    FRIENDLY = "friendly"
    CONCISE = "concise"


class AppMode(str, Enum):
    WEBHOOK = "webhook"
    POLLING = "polling"


def _parse_int_set(value: str | None) -> Set[int]:
    if value is None:
        return set()
    if isinstance(value, (set, frozenset)):
        return {int(item) for item in value}
    if isinstance(value, (list, tuple)):
        return {int(item) for item in value}
    text = str(value).strip()
    if not text:
        return set()
    if text.startswith("[") or text.startswith("{"):
        try:
            parsed = json.loads(text)
            return _parse_int_set(parsed)
        except json.JSONDecodeError:
            pass
    items = [item.strip() for item in text.split(",")]
    return {int(item) for item in items if item}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    webhook_secret_path: Optional[str] = Field(None, alias="WEBHOOK_SECRET_PATH")
    webhook_secret_token: Optional[str] = Field(None, alias="WEBHOOK_SECRET_TOKEN")
    webhook_external_url: Optional[AnyHttpUrl] = Field(None, alias="WEBHOOK_EXTERNAL_URL")

    allowed_user_ids_raw: str = Field(..., alias="ALLOWED_USER_IDS")
    admin_user_ids_raw: Optional[str] = Field(None, alias="ADMIN_USER_IDS")

    reply_parse_mode: ParseMode = Field(ParseMode.HTML, alias="REPLY_PARSE_MODE")
    history_enabled: bool = Field(True, alias="HISTORY_ENABLED")
    history_turns: int = Field(6, alias="HISTORY_TURNS")
    redis_url: Optional[str] = Field(default=None, alias="REDIS_URL")

    gigachat_base_url: HttpUrl = Field(..., alias="GIGACHAT_BASE_URL")
    gigachat_auth_url: HttpUrl = Field(..., alias="GIGACHAT_AUTH_URL")
    gigachat_client_id: str = Field(..., alias="GIGACHAT_CLIENT_ID")
    gigachat_client_secret: str = Field(..., alias="GIGACHAT_CLIENT_SECRET")
    gigachat_model: str = Field(..., alias="LLM_MODEL")
    gigachat_chat_path: str = Field("/chat/completions", alias="GIGACHAT_CHAT_PATH")
    gigachat_token_refresh_reserve: int = Field(60, alias="GIGACHAT_TOKEN_REFRESH_RESERVE")
    gigachat_token_force_refresh_interval: int = Field(300, alias="GIGACHAT_TOKEN_FORCE_REFRESH_INTERVAL")
    gigachat_scope: str = Field("GIGACHAT_API_PERS", alias="GIGACHAT_SCOPE")
    gigachat_verify_ssl: bool = Field(True, alias="GIGACHAT_VERIFY_SSL")

    request_timeout_sec: float = Field(60.0, alias="REQUEST_TIMEOUT_SEC")
    connect_timeout_sec: float = Field(3.0, alias="CONNECT_TIMEOUT_SEC")

    log_level: str = Field("INFO", alias="LOG_LEVEL")
    metrics_enabled: bool = Field(True, alias="METRICS_ENABLED")
    process_edited_messages: bool = Field(False, alias="PROCESS_EDITED_MESSAGES")
    default_mode: BotMode = Field(BotMode.FRIENDLY, alias="DEFAULT_MODE")
    app_mode: AppMode = Field(AppMode.POLLING, alias="APP_MODE")
    app_host: str = Field("0.0.0.0", alias="APP_HOST")
    app_port: int = Field(8080, alias="APP_PORT")
    uvicorn_reload: bool = Field(False, alias="UVICORN_RELOAD")

    _allowed_user_ids: Set[int] = PrivateAttr(default_factory=set)
    _admin_user_ids: Set[int] = PrivateAttr(default_factory=set)

    @field_validator("reply_parse_mode", mode="before")
    @classmethod
    def validate_parse_mode(cls, value: str | ParseMode) -> ParseMode:
        if isinstance(value, ParseMode):
            return value
        normalized = value.strip()
        try:
            return ParseMode(normalized)
        except ValueError as exc:
            raise ValueError("REPLY_PARSE_MODE must be 'Markdown' or 'HTML'") from exc

    @field_validator("history_turns")
    @classmethod
    def validate_history_turns(cls, value: int) -> int:
        if value < 0:
            raise ValueError("HISTORY_TURNS must be non-negative")
        if value > 20:
            raise ValueError("HISTORY_TURNS must be <= 20 to limit context size")
        return value

    @field_validator("default_mode", mode="before")
    @classmethod
    def validate_default_mode(cls, value: str | BotMode) -> BotMode:
        if isinstance(value, BotMode):
            return value
        normalized = value.strip().lower()
        try:
            return BotMode(normalized)
        except ValueError as exc:
            raise ValueError("DEFAULT_MODE must be 'friendly' or 'concise'") from exc

    @field_validator("gigachat_token_force_refresh_interval")
    @classmethod
    def validate_gigachat_token_force_refresh_interval(cls, value: int) -> int:
        if value < 0:
            raise ValueError("GIGACHAT_TOKEN_FORCE_REFRESH_INTERVAL must be non-negative")
        return value

    @field_validator("app_mode", mode="before")
    @classmethod
    def validate_app_mode(cls, value: str | AppMode) -> AppMode:
        if isinstance(value, AppMode):
            return value
        normalized = value.strip().lower()
        try:
            return AppMode(normalized)
        except ValueError as exc:
            raise ValueError("APP_MODE must be 'webhook' or 'polling'") from exc

    @model_validator(mode="after")
    def finalize(self) -> "Settings":
        allowed = _parse_int_set(self.allowed_user_ids_raw)
        admin = _parse_int_set(self.admin_user_ids_raw)
        if admin and not admin.issubset(allowed):
            raise ValueError("ADMIN_USER_IDS must be a subset of ALLOWED_USER_IDS")
        if not admin:
            admin = set(allowed)
        object.__setattr__(self, "_allowed_user_ids", allowed)
        object.__setattr__(self, "_admin_user_ids", admin)
        if self.app_mode == AppMode.WEBHOOK:
            if not self.webhook_secret_path or not self.webhook_secret_token:
                raise ValueError("WEBHOOK_SECRET_PATH and WEBHOOK_SECRET_TOKEN are required in webhook mode")
        return self

    @property
    def allowed_user_ids(self) -> Set[int]:
        return self._allowed_user_ids

    @property
    def admin_user_ids(self) -> Set[int]:
        return self._admin_user_ids


@lru_cache()
def get_settings() -> Settings:
    return Settings()
