"""Execute actual API function bodies with isolated model/DB dependencies."""
import asyncio
from concurrent.futures import Future
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from starlette.responses import JSONResponse, StreamingResponse

from test_question_request import load_function
from test_question_gate import MemoryRedis
from common.core.idempotency_redis import IdempotencyKeys
from common.core.question_gate import QuestionGate, GateRejected, active_question_lease


class RouteGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gate = QuestionGate(MemoryRedis(), IdempotencyKeys('test:route'))
        self.worker = Future()
        self.service = MagicMock()
        self.service.init_record.side_effect = lambda **kwargs: None

        def submit(**kwargs):
            active_question_lease.get().follow(self.worker, lambda: False)
        self.service.run_task_async.side_effect = submit
        self.service.await_result.return_value = iter(())
        self.creator = AsyncMock(return_value=self.service)
        namespace = {
            'asyncio': asyncio, 'HTTPException': HTTPException, 'JSONResponse': JSONResponse,
            'StreamingResponse': StreamingResponse, 'GateRejected': GateRejected,
            'Chat': object, 'user_ws_list': lambda *args: [SimpleNamespace(id=1)],
            'ChatFinishStep': SimpleNamespace(GENERATE_CHART=3),
            'LLMService': SimpleNamespace(create=self.creator),
            'traceback': MagicMock(),
            'fingerprint': lambda *args: 'fingerprint',
            'cached_result': AsyncMock(return_value=None),
            'database_result': AsyncMock(return_value=None),
            'complete_result': AsyncMock(),
            'orjson': SimpleNamespace(dumps=lambda value: json.dumps(value).encode()),
        }
        for name in ('gate_error_response', 'collect_result', 'stream_sql'):
            load_function('backend/apps/chat/api/chat.py', name, namespace)
        namespace['question_answer_inner'] = namespace['stream_sql']
        self.handler = load_function('backend/apps/chat/api/chat.py', 'guarded_question', namespace)
        self.namespace = namespace
        self.session = SimpleNamespace(get=lambda *args: SimpleNamespace(oid=1))
        self.user = SimpleNamespace(id=1, oid=1)
        self.question = SimpleNamespace(chat_id=1, request_id='r1')

    async def asyncTearDown(self):
        if not self.worker.done():
            self.worker.set_result(None)
        await asyncio.gather(*list(self.gate.tasks), return_exceptions=True)
        await self.gate.shutdown()

    async def test_response_returns_with_locks_until_worker_finishes(self):
        response = await self.handler(self.gate, self.session, self.user, self.question)
        self.assertIsInstance(response, StreamingResponse)
        self.assertEqual(len(self.gate.redis.values), 2)
        self.service.init_record.assert_called_once()
        self.worker.set_result(None)
        await asyncio.gather(*list(self.gate.tasks))
        self.assertEqual(self.gate.redis.values, {})

    async def test_busy_rejected_before_service_or_record_creation(self):
        async with self.gate.enter(1, 'other'):
            response = await self.handler(self.gate, self.session, self.user, self.question)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(json.loads(response.body)['code'], 'CHAT_BUSY')
            self.creator.assert_not_called()
        self.assertEqual(self.gate.redis.values, {})

    async def test_submission_failure_cleans_locks(self):
        self.service.run_task_async.side_effect = RuntimeError('submission failed')
        response = await self.handler(self.gate, self.session, self.user, self.question, stream=False)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.gate.redis.values, {})

    async def test_permission_denied_before_redis(self):
        self.session.get = lambda *args: SimpleNamespace(oid=99)
        with self.assertRaises(HTTPException) as error:
            await self.handler(self.gate, self.session, self.user, self.question)
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(self.gate.redis.values, {})
        self.creator.assert_not_called()

    async def test_unfinished_record_rejects_without_chat_lock_or_worker(self):
        self.namespace['database_result'].side_effect = GateRejected(
            'RESULT_UNAVAILABLE', '本次执行未获得完整结果，请重新提问。')
        async with self.gate.enter(1, 'old-worker'):
            response = await self.handler(self.gate, self.session, self.user, self.question)
            self.assertEqual(json.loads(response.body)['code'], 'RESULT_UNAVAILABLE')
            self.creator.assert_not_called()
            self.assertNotIn(self.gate.keys.request_processing(1, 'r1'), self.gate.redis.values)
            self.assertIn(self.gate.keys.chat_lock(1), self.gate.redis.values)

    async def test_terminal_replay_does_not_require_idle_chat(self):
        self.namespace['database_result'].return_value = {'replay': True, 'record_id': 8}
        async with self.gate.enter(1, 'other'):
            response = await self.handler(self.gate, self.session, self.user, self.question)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.body)['record_id'], 8)
            self.creator.assert_not_called()
            self.assertNotIn(self.gate.keys.request_processing(1, 'r1'), self.gate.redis.values)


if __name__ == '__main__':
    unittest.main()
