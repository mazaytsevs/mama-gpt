from __future__ import annotations

from typing import Iterable

from app.infra.settings import Settings, get_settings


class AccessError(Exception):
    """Raised when a user is not permitted to interact with the bot."""


def _ensure_settings(settings: Settings | None) -> Settings:
    return settings or get_settings()


def is_user_allowed(user_id: int, settings: Settings | None = None) -> bool:
    settings = _ensure_settings(settings)
    return user_id in settings.allowed_user_ids


def is_admin(user_id: int, settings: Settings | None = None) -> bool:
    settings = _ensure_settings(settings)
    return user_id in settings.admin_user_ids


def allowed_ids(settings: Settings | None = None) -> Iterable[int]:
    settings = _ensure_settings(settings)
    return settings.allowed_user_ids


def admin_ids(settings: Settings | None = None) -> Iterable[int]:
    settings = _ensure_settings(settings)
    return settings.admin_user_ids
