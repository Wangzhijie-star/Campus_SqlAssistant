"""Connection and key names only; no locking or replay is implemented here."""
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdempotencyKeys:
    prefix: str

    def __post_init__(self):
        if not self.prefix or self.prefix != self.prefix.strip() or self.prefix.endswith(':'):
            raise ValueError('Redis key prefix must be nonempty, trimmed and have no trailing colon')

    def request_processing(self, chat_id: int, request_id: str) -> str:
        if not request_id or request_id != request_id.strip():
            raise ValueError('request_id must be nonempty and trimmed')
        return f'{self.prefix}:request:processing:{chat_id}:{request_id}'

    def chat_lock(self, chat_id: int) -> str:
        return f'{self.prefix}:chat:lock:{chat_id}'


@asynccontextmanager
async def idempotency_redis_lifespan(app, url: str | None, prefix: str):
    """Create one async client/pool per worker, and close it even if startup fails.

    Use this client on the application event loop, not from LLM worker threads.
    A configured but unreachable Redis fails startup; there is no memory fallback.
    """
    app.state.idempotency_keys = IdempotencyKeys(prefix)
    app.state.idempotency_redis = None
    if not url:
        logger.info('Idempotency Redis not configured; connection initialization skipped')
        yield
        return

    client = Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    try:
        try:
            await client.ping()
        except Exception:
            # Do not expose a Redis URL that may contain credentials.
            raise RuntimeError('Idempotency Redis connection failed; check configuration and service') from None
        app.state.idempotency_redis = client
        logger.info('Idempotency Redis connected; request locks are not enabled yet')
        yield
    finally:
        app.state.idempotency_redis = None
        await client.aclose()
