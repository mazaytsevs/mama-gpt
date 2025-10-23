from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from app.bot.handlers import get_update_handler
from app.bot.telegram import TelegramAPIError, get_telegram_client
from app.infra.logging import get_logger, setup_logging
from app.infra.settings import get_settings

logger = get_logger(__name__)


async def run_polling(poll_timeout: int = 20) -> None:
    settings = get_settings()
    setup_logging(settings)
    handler = get_update_handler(settings)
    telegram = get_telegram_client(settings)
    offset: Optional[int] = None

    logger.info("polling_started")
    while True:
        try:
            response = await telegram.get_updates(offset=offset, timeout=poll_timeout)
            updates = response.result or []
            for update in updates:
                offset = max(offset or 0, update.get("update_id", 0)) + 1
                await handler.handle(update)
        except TelegramAPIError as exc:
            logger.error("polling_telegram_error", extra={"error": str(exc)})
            await asyncio.sleep(3)
        except Exception as exc:  # noqa: BLE001
            logger.error("polling_unexpected_error", extra={"error": str(exc)})
            await asyncio.sleep(3)
