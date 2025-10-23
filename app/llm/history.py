from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Iterable, List, Optional

try:
    from redis.asyncio import Redis
    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback when redis is unavailable
    Redis = None  # type: ignore
    _REDIS_AVAILABLE = False

from app.infra.settings import Settings, get_settings

from .prompt import MessagePayload

HISTORY_TTL_SECONDS = 7 * 24 * 60 * 60


class ConversationHistory:
    def __init__(self, settings: Settings):
        self._enabled = settings.history_enabled and bool(settings.redis_url) and _REDIS_AVAILABLE
        self._turns = settings.history_turns
        self._redis: Optional[Redis] = None
        if self._enabled and settings.redis_url and _REDIS_AVAILABLE:
            self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._memory_store: dict[int, List[MessagePayload]] = defaultdict(list)

    @property
    def enabled(self) -> bool:
        return self._enabled and self._redis is not None

    @property
    def redis_client(self) -> Redis | None:
        return self._redis

    def _key(self, user_id: int) -> str:
        return f"ctx:{user_id}"

    def _max_messages(self) -> int:
        return self._turns * 2

    async def load(self, user_id: int) -> List[MessagePayload]:
        if self.enabled and self._redis:
            entries = await self._redis.lrange(self._key(user_id), -self._max_messages(), -1)
            messages: List[MessagePayload] = []
            for entry in entries:
                try:
                    payload = json.loads(entry)
                    if "role" in payload and "content" in payload:
                        messages.append(MessagePayload(role=payload["role"], content=payload["content"]))
                except json.JSONDecodeError:
                    continue
            self._memory_store[user_id] = list(messages)
            return messages
        return list(self._memory_store.get(user_id, []))

    async def append(self, user_id: int, role: str, content: str) -> None:
        payload = {"role": role, "content": content, "ts": int(time.time())}
        if self.enabled and self._redis:
            key = self._key(user_id)
            await self._redis.rpush(key, json.dumps(payload, ensure_ascii=False))
            await self._redis.ltrim(key, -self._max_messages(), -1)
            await self._redis.expire(key, HISTORY_TTL_SECONDS)
        memory_messages = self._memory_store[user_id]
        memory_messages.append(MessagePayload(role=role, content=content))
        if len(memory_messages) > self._max_messages():
            self._memory_store[user_id] = memory_messages[-self._max_messages():]

    async def clear(self, user_id: int) -> None:
        if self.enabled and self._redis:
            await self._redis.delete(self._key(user_id))
        self._memory_store.pop(user_id, None)

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()


_history: ConversationHistory | None = None


def get_history(settings: Settings | None = None) -> ConversationHistory:
    global _history
    if _history:
        return _history
    settings = settings or get_settings()
    _history = ConversationHistory(settings)
    return _history
