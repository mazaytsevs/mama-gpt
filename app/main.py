from __future__ import annotations

import asyncio

import uvicorn

from app.infra.settings import AppMode, get_settings
from app.polling import run_polling


def run_webhook_server(host: str, port: int, reload: bool) -> None:
    uvicorn.run(
        "app.server:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    settings = get_settings()
    if settings.app_mode == AppMode.POLLING:
        asyncio.run(run_polling())
    else:
        run_webhook_server(settings.app_host, settings.app_port, settings.uvicorn_reload)
