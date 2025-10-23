from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

from app.bot.handlers import get_update_handler, route
from app.infra.health import healthz
from app.infra.logging import get_logger, setup_logging
from app.infra.metrics import get_metrics
from app.infra.settings import AppMode, Settings, get_settings
from app.llm.history import get_history

logger = get_logger(__name__)

app = FastAPI(title="mama-gpt")


def get_settings_dependency() -> Settings:
    settings = get_settings()
    return settings


@app.on_event("startup")
async def on_startup() -> None:
    settings = get_settings()
    setup_logging(settings)
    if settings.app_mode != AppMode.WEBHOOK:
        logger.info("webhook_disabled", extra={"app_mode": settings.app_mode.value})
    if settings.history_enabled:
        history = get_history(settings)
        if history.enabled:
            logger.info("history_enabled", extra={"redis_url": settings.redis_url})
        else:
            logger.info("history_disabled_fallback")
    get_update_handler(settings)
    logger.info("startup_complete")


@app.post("/webhook/{secret}")
async def webhook(secret: str, request: Request, settings: Settings = Depends(get_settings_dependency)) -> Response:
    if settings.app_mode != AppMode.WEBHOOK:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook disabled")
    if secret != settings.webhook_secret_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    incoming_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if incoming_secret != settings.webhook_secret_token:
        logger.warning("webhook_secret_mismatch")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    update = await request.json()
    await route(update)
    return Response(status_code=status.HTTP_200_OK)


@app.get("/healthz")
async def health(settings: Settings = Depends(get_settings_dependency)) -> dict:
    history = get_history(settings)
    report = await healthz(history.redis_client if history.enabled else None)
    return report


@app.get("/metrics")
async def metrics(settings: Settings = Depends(get_settings_dependency)) -> Response:
    metrics_service = get_metrics(settings)
    if not metrics_service.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Metrics disabled")
    data = metrics_service.export()
    return Response(content=data, media_type="text/plain; version=0.0.4")
