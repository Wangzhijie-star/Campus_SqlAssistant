"""Run from backend/ using its venv. Reads ../.env and sends PING only."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from common.core.config import settings
from common.core.idempotency_redis import idempotency_redis_lifespan


async def main():
    if not settings.IDEMPOTENCY_REDIS_URL:
        raise RuntimeError('IDEMPOTENCY_REDIS_URL is missing; check ../.env and current directory')
    app = SimpleNamespace(state=SimpleNamespace())
    async with idempotency_redis_lifespan(
        app, settings.IDEMPOTENCY_REDIS_URL, settings.IDEMPOTENCY_REDIS_KEY_PREFIX
    ):
        print('Configured Redis connection: OK')
        print('Request key:', app.state.idempotency_keys.request_processing(1, 'example-request'))
        print('Chat key:', app.state.idempotency_keys.chat_lock(1))
    assert app.state.idempotency_redis is None
    print('Client cleanup: OK; no lock keys written')


if __name__ == '__main__':
    asyncio.run(main())
