import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from common.core.idempotency_redis import IdempotencyKeys, idempotency_redis_lifespan


class RedisFoundationTests(unittest.IsolatedAsyncioTestCase):
    def test_keys(self):
        keys = IdempotencyKeys('sqlbot:local')
        self.assertEqual(keys.request_processing(1, 'r1'), 'sqlbot:local:request:processing:1:r1')
        self.assertEqual(keys.chat_lock(1), 'sqlbot:local:chat:lock:1')
        self.assertNotEqual(keys.request_processing(1, 'r1'), keys.request_processing(1, 'r2'))
        self.assertNotEqual(keys.chat_lock(1), keys.chat_lock(2))
        for prefix in ('', ' ', 'sqlbot:local:'):
            with self.assertRaises(ValueError):
                IdempotencyKeys(prefix)

    async def test_client_closed_after_success_or_body_failure(self):
        for fail in (False, True):
            app = SimpleNamespace(state=SimpleNamespace())
            client = AsyncMock()
            with patch('common.core.idempotency_redis.Redis.from_url', return_value=client) as factory:
                try:
                    async with idempotency_redis_lifespan(app, 'redis://localhost/1', 'sqlbot:local'):
                        self.assertIs(app.state.idempotency_redis, client)
                        if fail:
                            raise ValueError('later startup failure')
                except ValueError:
                    self.assertTrue(fail)
                client.ping.assert_awaited_once()
                client.aclose.assert_awaited_once()
                self.assertIsNone(app.state.idempotency_redis)
                self.assertEqual(factory.call_args.kwargs['socket_timeout'], 3)

    async def test_connection_failure_closes_client(self):
        app = SimpleNamespace(state=SimpleNamespace())
        client = AsyncMock()
        client.ping.side_effect = ConnectionError('unreachable')
        with patch('common.core.idempotency_redis.Redis.from_url', return_value=client):
            with self.assertRaisesRegex(RuntimeError, 'connection failed'):
                async with idempotency_redis_lifespan(app, 'redis://localhost/1', 'sqlbot:local'):
                    self.fail('Startup must not continue')
        client.aclose.assert_awaited_once()

    async def test_unconfigured_skips_connection(self):
        app = SimpleNamespace(state=SimpleNamespace())
        with patch('common.core.idempotency_redis.Redis.from_url') as factory:
            async with idempotency_redis_lifespan(app, None, 'sqlbot:local'):
                self.assertIsNone(app.state.idempotency_redis)
            factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
