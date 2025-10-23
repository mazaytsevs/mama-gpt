from __future__ import annotations

from typing import Any, Dict

try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - redis optional during tests
    Redis = None  # type: ignore


async def check_redis(redis: Redis | None) -> Dict[str, Any]:
    if not redis:
        return {"status": "disabled"}
    try:
        pong = await redis.ping()
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok", "detail": pong}


async def healthz(redis: Redis | None = None) -> Dict[str, Any]:
    redis_status = await check_redis(redis)
    overall = "ok" if redis_status.get("status") in {"ok", "disabled"} else "error"
    return {"status": overall, "redis": redis_status}
