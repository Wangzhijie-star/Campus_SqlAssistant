"""Replay policy tests execute production helpers with isolated storage, never call AI."""
import asyncio
import hashlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import unittest

from test_question_request import load_function
from common.core.question_gate import GateRejected


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.record = SimpleNamespace(id=7, status='SUCCESS', request_fingerprint='fp')
        self.payload = {'replay': True, 'chat_id': 1, 'request_id': 'r', 'record_id': 7,
                        'status': 'SUCCESS', 'request_fingerprint': 'fp', 'record': {'id': 7}}
        self.ns = {'asyncio': asyncio, 'json': json, 'hashlib': hashlib, 'GateRejected': GateRejected,
                   'logger': logging.getLogger(__name__), 'REPLAY_TTL': 600,
                   'TERMINAL': ('SUCCESS', 'FAILED'), 'lookup': Mock(return_value=self.record),
                   'snapshot': Mock(return_value=self.payload)}
        for name in ('check_identity', 'fingerprint', 'cache_result', 'cached_result', 'database_result'):
            load_function('backend/apps/chat/curd/question_replay.py', name, self.ns)
        self.gate = SimpleNamespace(redis=SimpleNamespace(set=AsyncMock(), get=AsyncMock(return_value=None)),
                                    keys=SimpleNamespace(request_replay=lambda *args: 'replay:key'))

    async def test_unfinished_returns_unavailable_without_snapshot_or_cache(self):
        self.record.status = 'PROCESSING'
        with self.assertRaises(GateRejected) as caught:
            await self.ns['database_result'](self.gate, 1, 'r', 'fp')
        self.assertEqual(caught.exception.code, 'RESULT_UNAVAILABLE')
        self.assertEqual(self.record.status, 'PROCESSING')
        self.ns['snapshot'].assert_not_called()
        self.gate.redis.set.assert_not_awaited()

    async def test_success_and_failure_refill_ten_minutes(self):
        for status in ('SUCCESS', 'FAILED'):
            self.record.status = status
            self.assertEqual(await self.ns['database_result'](self.gate, 1, 'r', 'fp'), self.payload)
            self.assertEqual(self.gate.redis.set.call_args.kwargs['ex'], 600)

    async def test_cache_write_failure_still_returns_database_result(self):
        self.gate.redis.set.side_effect = ConnectionError('offline')
        self.assertEqual(await self.ns['database_result'](self.gate, 1, 'r', 'fp'), self.payload)

    async def test_cache_read_failure_is_not_a_miss(self):
        self.gate.redis.get.side_effect = ConnectionError('offline')
        with self.assertRaises(GateRejected) as caught:
            await self.ns['cached_result'](self.gate, 1, 'r', 'fp')
        self.assertEqual(caught.exception.status_code, 503)

    async def test_deleted_record_cannot_be_resurrected_by_cache(self):
        self.gate.redis.get.return_value = json.dumps(self.payload)
        self.ns['lookup'].return_value = None
        self.assertIsNone(await self.ns['cached_result'](self.gate, 1, 'r', 'fp'))

    async def test_same_id_different_content_is_rejected(self):
        with self.assertRaises(GateRejected) as caught:
            await self.ns['database_result'](self.gate, 1, 'r', 'different')
        self.assertEqual(caught.exception.code, 'REQUEST_ID_CONFLICT')

    async def test_no_record_allows_new_execution(self):
        self.ns['lookup'].return_value = None
        self.assertIsNone(await self.ns['database_result'](self.gate, 1, 'r', 'fp'))

    def test_original_regenerate_command_is_part_of_fingerprint(self):
        first = self.ns['fingerprint'](SimpleNamespace(question='/regenerate 1'), {})
        second = self.ns['fingerprint'](SimpleNamespace(question='/regenerate 2'), {})
        self.assertNotEqual(first, second)
