"""Real Redis integration check using a unique test namespace, no database/model calls."""
import asyncio
from concurrent.futures import Future
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from redis.asyncio import Redis
from common.core.config import settings
from common.core.idempotency_redis import IdempotencyKeys
from common.core.question_gate import QuestionGate, GateRejected, LeaseLostError


async def main():
    if not settings.IDEMPOTENCY_REDIS_URL:
        raise RuntimeError('Configure IDEMPOTENCY_REDIS_URL first; run from backend/')
    redis = Redis.from_url(settings.IDEMPOTENCY_REDIS_URL, decode_responses=True,
                           socket_connect_timeout=3, socket_timeout=3)
    keys = IdempotencyKeys('sqlbot:test:question-gate:' + uuid4().hex)
    gate = QuestionGate(redis, keys, ttl=0.6, renew_interval=0.1, max_runtime=10)
    touched = [keys.request_processing(1, f'r{i}') for i in range(12)] + [keys.chat_lock(1)]
    try:
        results = await asyncio.gather(*(gate.acquire(1, f'r{i}') for i in range(12)), return_exceptions=True)
        winners = [result for result in results if not isinstance(result, BaseException)]
        assert len(winners) == 1, results
        assert all(isinstance(result, GateRejected) and result.code == 'CHAT_BUSY'
                   for result in results if isinstance(result, BaseException)), results
        lease = winners[0]
        print('Concurrent chat lock and losing-request cleanup: OK')
        assert sum(value is not None for value in await redis.mget(touched)) == 2
        try:
            await gate.acquire(1, lease.keys[0].rsplit(':', 1)[1])
            raise AssertionError('Duplicate request acquired a lock')
        except GateRejected as error:
            assert error.code == 'PROCESSING'
        print('Duplicate request returns PROCESSING: OK')
        await asyncio.sleep(0.8)
        assert all(await redis.mget(lease.keys))
        print('Automatic renewal outlives initial lease: OK')
        await lease.stop_renewal()
        await redis.set(lease.keys[-1], 'replacement-owner', px=3000)
        try:
            await lease.renew_once()
            raise AssertionError('Old owner renewed replacement lock')
        except LeaseLostError:
            pass
        await lease.close()
        assert await redis.get(lease.keys[-1]) == 'replacement-owner'
        print('Old owner cannot renew/delete replacement lock: OK')
        await redis.delete(lease.keys[-1])  # This script's own isolated test key only.
        future = Future()
        async with gate.enter(1, 'r0') as lease:
            lease.follow(future, lambda: False)
        assert all(await redis.mget(lease.keys))
        future.set_result(None)
        await lease.observer
        assert not any(await redis.mget(lease.keys))
        print('HTTP return retains locks; actual task completion releases them: OK')
    finally:
        for lease in list(gate.leases):
            await lease.close()
        await gate.shutdown()
        await redis.delete(*touched)
        await redis.aclose()
    print('Unique test keys cleaned up')


if __name__ == '__main__':
    asyncio.run(main())
