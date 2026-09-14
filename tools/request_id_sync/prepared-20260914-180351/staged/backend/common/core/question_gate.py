"""In-flight request exclusion. Completed-result replay is intentionally separate."""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from enum import Enum
import logging
from math import isfinite
import time
from uuid import uuid4

logger = logging.getLogger(__name__)
active_question_lease = ContextVar('active_question_lease', default=None)

RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
RENEW = """
for i, key in ipairs(KEYS) do
    if redis.call('get', key) ~= ARGV[1] then return 0 end
end
for i, key in ipairs(KEYS) do redis.call('pexpire', key, ARGV[2]) end
return 1
"""


class GateState(str, Enum):
    NEW = 'NEW'
    PROCESSING = 'PROCESSING'
    CHAT_BUSY = 'CHAT_BUSY'
    RUNNING = 'RUNNING'
    SUCCESS = 'SUCCESS'
    FAILED = 'FAILED'
    LOST = 'LOST'


class GateRejected(Exception):
    def __init__(self, code, message, status_code=409):
        super().__init__(message)
        self.code, self.status_code = code, status_code


class LeaseLostError(RuntimeError):
    pass


class QuestionLease:
    def __init__(self, gate, keys):
        self.gate, self.keys = gate, keys
        self.token = uuid4().hex
        self.state = GateState.NEW
        self.deadline = 0.0
        self.started = time.monotonic()
        self.renewal = None
        self.observer = None
        self.closed = False
        self.on_completed = None
        self.record_id = None
        self.request_id = None
        self.request_fingerprint = None

    def ensure_active(self):
        # This check is also called in worker threads; it does no async Redis I/O.
        if (self.closed or self.state == GateState.LOST or time.monotonic() >= self.deadline
                or time.monotonic() - self.started >= self.gate.max_runtime):
            self.state = GateState.LOST
            raise LeaseLostError('Execution lease lost or task time limit reached')

    async def renew_once(self):
        self.ensure_active()
        started = time.monotonic()
        result = await self.gate.redis.eval(
            RENEW, len(self.keys), *self.keys, self.token, int(self.gate.ttl * 1000)
        )
        # Never revive a locally expired lease, even if a delayed reply says success.
        self.ensure_active()
        if not result:
            raise LeaseLostError('Execution lease is no longer owned by this task')
        self.deadline = started + self.gate.ttl

    async def _renew(self):
        try:
            while True:
                await asyncio.sleep(self.gate.renew_interval)
                await self.renew_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.state = GateState.LOST
            logger.warning('Question lease lost; subsequent execution must stop')

    def follow(self, future, failed):
        """Transfer cleanup to the real executor future, before the HTTP request returns."""
        if self.observer is not None:
            raise RuntimeError('Lease already assigned to a worker')
        if self.state != GateState.LOST:
            self.state = GateState.RUNNING
        self.observer = asyncio.create_task(self._observe(future, failed))
        self.gate.track(self.observer)

    async def _observe(self, future, failed):
        completed = False
        try:
            await asyncio.shield(asyncio.wrap_future(future))
            completed = True
            if self.state != GateState.LOST:
                self.state = GateState.FAILED if failed() else GateState.SUCCESS
        except asyncio.CancelledError:
            if future.done():
                # A cancelled queued future has no running worker and can be cleaned up.
                completed = True
                if self.state != GateState.LOST:
                    self.state = GateState.FAILED
            else:
                # Cancelling an observer does not stop a Python thread. Do not unlock it early.
                self.state = GateState.LOST
                raise
        except Exception:
            completed = True
            if self.state != GateState.LOST:
                self.state = GateState.FAILED
            logger.exception('Question worker failed')
        finally:
            if completed:
                try:
                    if self.on_completed and self.state != GateState.LOST:
                        await self.on_completed(self.state != GateState.SUCCESS)
                except Exception:
                    logger.exception('Question result finalization failed; record remains unavailable')
                finally:
                    await self.close()
            else:
                await self.stop_renewal()

    async def stop_renewal(self):
        if self.renewal is not None:
            self.renewal.cancel()
            await asyncio.gather(self.renewal, return_exceptions=True)
            self.renewal = None

    async def close(self):
        if self.closed:
            return
        self.closed = True
        await self.stop_renewal()
        # Attempt both releases even if the first fails; TTL remains the fallback.
        for key in reversed(self.keys):
            try:
                await self.gate.redis.eval(RELEASE, 1, key, self.token)
            except Exception:
                logger.warning('Question lock release failed; relying on expiry', exc_info=True)
        self.gate.leases.discard(self)


class QuestionGate:
    def __init__(self, redis, keys, ttl=60, renew_interval=20, max_runtime=600):
        if (not all(isfinite(value) for value in (ttl, renew_interval, max_runtime))
                or not 0 < renew_interval < ttl or ttl < 0.001 or max_runtime <= 0):
            raise ValueError('Require 0 < renewal interval < lease TTL and positive task limit')
        self.redis, self.keys = redis, keys
        self.ttl, self.renew_interval, self.max_runtime = ttl, renew_interval, max_runtime
        self.tasks = set()
        self.leases = set()
        self.closing = False

    def track(self, task):
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def acquire(self, chat_id, request_id, request_only=False):
        if self.redis is None or self.closing:
            raise GateRejected('COORDINATION_UNAVAILABLE', 'Request coordination is unavailable', 503)
        # Legacy record actions share the chat lock without inventing a request_id.
        keys = ([self.keys.request_processing(chat_id, request_id)] if request_id else [])
        if not request_only:
            keys.append(self.keys.chat_lock(chat_id))
        lease = QuestionLease(self, keys)
        started = time.monotonic()
        try:
            for index, key in enumerate(keys):
                acquired = await self.redis.set(key, lease.token, nx=True, px=int(self.ttl * 1000))
                if not acquired:
                    code = GateState.PROCESSING if request_id and index == 0 else GateState.CHAT_BUSY
                    message = ('This request is already processing' if code == GateState.PROCESSING
                               else 'Another request is running in this chat; please try again when it finishes')
                    raise GateRejected(code.value, message)
            lease.deadline = started + self.ttl
            lease.ensure_active()
            self.leases.add(lease)
            lease.renewal = asyncio.create_task(lease._renew())
            return lease
        except BaseException as error:
            # A SET may have succeeded even when its response was lost. Token-checked cleanup is safe.
            cleanup = asyncio.create_task(lease.close())
            self.track(cleanup)
            await asyncio.shield(cleanup)
            if isinstance(error, (GateRejected, asyncio.CancelledError)):
                raise
            raise GateRejected('COORDINATION_UNAVAILABLE', 'Request coordination is unavailable', 503) from None

    @asynccontextmanager
    async def enter(self, chat_id, request_id, request_only=False):
        lease = await self.acquire(chat_id, request_id, request_only=request_only)
        context_token = active_question_lease.set(lease)
        try:
            yield lease
        finally:
            active_question_lease.reset(context_token)
            if lease.observer is None:
                try:
                    if lease.on_completed and lease.state != GateState.LOST:
                        await lease.on_completed(True)
                except Exception:
                    logger.exception('Question submission finalization failed')
                finally:
                    cleanup = asyncio.create_task(lease.close())
                    self.track(cleanup)
                    await asyncio.shield(cleanup)

    async def acquire_chat(self, lease, chat_id):
        """Promote a request-only lease; ambiguous SETs are included in owner-safe cleanup."""
        lease.ensure_active()
        await lease.stop_renewal()
        key = self.keys.chat_lock(chat_id)
        lease.keys.append(key)
        try:
            acquired = await self.redis.set(key, lease.token, nx=True, px=int(self.ttl * 1000))
            if not acquired:
                raise GateRejected('CHAT_BUSY', 'Another request is running in this chat; please try again when it finishes')
            lease.ensure_active()
            await lease.renew_once()
            lease.renewal = asyncio.create_task(lease._renew())
        except (GateRejected, asyncio.CancelledError):
            raise
        except Exception:
            raise GateRejected('COORDINATION_UNAVAILABLE', 'Request coordination is unavailable', 503) from None

    async def shutdown(self):
        self.closing = True
        # During application shutdown, revoke execution and stop renewal. A live thread is
        # not proof of completion: leave its keys to expire instead of deleting them.
        for lease in list(self.leases):
            lease.state = GateState.LOST
            await lease.stop_renewal()
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
