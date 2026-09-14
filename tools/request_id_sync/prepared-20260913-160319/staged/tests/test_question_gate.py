"""Ownership, concurrency, cancellation and worker-lifetime tests without model/DB calls."""
import asyncio
from concurrent.futures import Future
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from common.core.idempotency_redis import IdempotencyKeys
from common.core.question_gate import QuestionGate, GateRejected, GateState, LeaseLostError, RELEASE, RENEW


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.expiries = {}
        self.release_calls = []
        self.fail_release = set()
        self.fail_renew = False
        self.cancel_set = False

    async def set(self, key, value, nx, px):
        if key in self.values:
            return False
        self.values[key] = value
        self.expiries[key] = time.monotonic() + px / 1000
        if self.cancel_set:
            raise asyncio.CancelledError()
        return True

    async def eval(self, script, numkeys, *args):
        keys, token = args[:numkeys], args[numkeys]
        if script == RELEASE:
            self.release_calls.append(keys[0])
            if keys[0] in self.fail_release:
                raise ConnectionError('simulated release failure')
            if self.values.get(keys[0]) == token:
                del self.values[keys[0]]
                self.expiries.pop(keys[0], None)
                return 1
            return 0
        if script == RENEW:
            if self.fail_renew:
                raise ConnectionError('simulated renewal failure')
            if any(self.values.get(key) != token for key in keys):
                return 0
            for key in keys:
                self.expiries[key] = time.monotonic() + int(args[numkeys + 1]) / 1000
            return 1
        raise AssertionError('Unexpected Lua script')


class QuestionGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = MemoryRedis()
        self.keys = IdempotencyKeys('sqlbot:test')
        self.gate = QuestionGate(self.redis, self.keys)

    async def asyncTearDown(self):
        await self.gate.shutdown()

    async def test_duplicate_request_and_chat_busy(self):
        first = await self.gate.acquire(1, 'r1')
        for request_id, code in [('r1', 'PROCESSING'), ('r2', 'CHAT_BUSY')]:
            with self.assertRaises(GateRejected) as caught:
                await self.gate.acquire(1, request_id)
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn(self.keys.request_processing(1, 'r2'), self.redis.values)
        self.assertEqual(self.redis.values[self.keys.chat_lock(1)], first.token)
        await first.close()
        self.assertEqual(self.redis.values, {})

    async def test_concurrent_different_requests_have_one_winner(self):
        results = await asyncio.gather(*(self.gate.acquire(1, f'r{i}') for i in range(20)),
                                       return_exceptions=True)
        winners = [result for result in results if not isinstance(result, BaseException)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(self.redis.values), 2)
        await winners[0].close()

    async def test_different_chats_can_execute(self):
        one, two = await asyncio.gather(self.gate.acquire(1, 'r1'), self.gate.acquire(2, 'r1'))
        self.assertNotEqual(one.token, two.token)
        await one.close()
        await two.close()

    async def test_legacy_action_uses_only_chat_lock(self):
        one = await self.gate.acquire(1, None)
        self.assertEqual(one.keys, [self.keys.chat_lock(1)])
        with self.assertRaises(GateRejected) as error:
            await self.gate.acquire(1, 'r1')
        self.assertEqual(error.exception.code, 'CHAT_BUSY')
        await one.close()

    async def test_renew_both_and_old_owner_cannot_renew_or_release(self):
        lease = await self.gate.acquire(1, 'r1')
        before = self.redis.expiries.copy()
        await lease.renew_once()
        self.assertTrue(all(self.redis.expiries[key] >= before[key] for key in lease.keys))
        self.redis.values[lease.keys[-1]] = 'replacement-owner'
        before = self.redis.expiries.copy()
        with self.assertRaises(LeaseLostError):
            await lease.renew_once()
        self.assertEqual(before, self.redis.expiries)
        await lease.close()
        self.assertEqual(self.redis.values[lease.keys[-1]], 'replacement-owner')

    async def test_cancelled_acquisition_cleans_ambiguous_write(self):
        self.redis.cancel_set = True
        with self.assertRaises(asyncio.CancelledError):
            await self.gate.acquire(1, 'r1')
        self.assertEqual(self.redis.values, {})

    async def test_submission_failure_releases(self):
        with self.assertRaises(RuntimeError):
            async with self.gate.enter(1, 'r1'):
                raise RuntimeError('executor submission failed')
        self.assertEqual(self.redis.values, {})

    async def test_http_exit_does_not_release_running_worker(self):
        future = Future()
        async with self.gate.enter(1, 'r1') as lease:
            lease.follow(future, lambda: False)
        self.assertEqual(len(self.redis.values), 2)
        self.assertFalse(lease.closed)
        future.set_result(None)
        await lease.observer
        self.assertEqual(lease.state, GateState.SUCCESS)
        self.assertEqual(self.redis.values, {})

    async def test_http_cancellation_does_not_cancel_worker_or_release(self):
        future = Future()
        with self.assertRaises(asyncio.CancelledError):
            async with self.gate.enter(1, 'r1') as lease:
                lease.follow(future, lambda: False)
                raise asyncio.CancelledError()
        self.assertFalse(future.cancelled())
        self.assertEqual(len(self.redis.values), 2)
        future.set_result(None)
        await lease.observer
        self.assertEqual(self.redis.values, {})

    async def test_worker_error_releases_both(self):
        future = Future()
        async with self.gate.enter(1, 'r1') as lease:
            lease.follow(future, lambda: False)
        future.set_exception(ValueError('model failed'))
        with self.assertLogs('common.core.question_gate', level='ERROR'):
            await lease.observer
        self.assertEqual(lease.state, GateState.FAILED)
        self.assertEqual(self.redis.values, {})

    async def test_cancelled_queued_future_releases_both(self):
        future = Future()
        async with self.gate.enter(1, 'r1') as lease:
            lease.follow(future, lambda: False)
        future.cancel()
        await lease.observer
        self.assertEqual(lease.state, GateState.FAILED)
        self.assertEqual(self.redis.values, {})

    async def test_release_failure_still_attempts_other_key(self):
        lease = await self.gate.acquire(1, 'r1')
        self.redis.fail_release.add(lease.keys[-1])
        with self.assertLogs('common.core.question_gate', level='WARNING'):
            await lease.close()
        self.assertNotIn(lease.keys[0], self.redis.values)
        self.assertEqual(self.redis.release_calls, list(reversed(lease.keys)))

    async def test_lost_renewal_stops_advancement(self):
        self.gate.renew_interval = 0.005
        self.redis.fail_renew = True
        lease = await self.gate.acquire(1, 'r1')
        with self.assertLogs('common.core.question_gate', level='WARNING'):
            await asyncio.wait_for(lease.renewal, timeout=1)
        with self.assertRaises(LeaseLostError):
            lease.ensure_active()
        await lease.close()

    async def test_local_expiry_and_task_limit(self):
        for expire_task in (False, True):
            lease = await self.gate.acquire(1, 'r1')
            if expire_task:
                lease.started -= 601
            else:
                lease.deadline = time.monotonic() - 1
            with self.assertRaises(LeaseLostError):
                lease.ensure_active()
            await lease.close()

    async def test_missing_redis_is_not_processing(self):
        self.gate.redis = None
        with self.assertRaises(GateRejected) as error:
            await self.gate.acquire(1, 'r1')
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(error.exception.code, 'COORDINATION_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
