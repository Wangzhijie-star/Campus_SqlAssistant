import asyncio
import os
from unittest.mock import AsyncMock, patch

from test_question_gate_routes import RouteGateTests


class PauseTests(RouteGateTests):
    async def test_pause_keeps_locks_and_refuses_duplicates(self):
        reached = asyncio.Event()
        resume = asyncio.Event()
        original_sleep = asyncio.sleep

        async def controlled_sleep(seconds):
            if seconds == 120:
                reached.set()
                await resume.wait()
            else:
                await original_sleep(seconds)

        with patch.dict(os.environ, {'SQLBOT_TEST_PAUSE_SECONDS': '120'}), \
                patch('common.core.question_gate.asyncio.sleep', side_effect=controlled_sleep):
            first = asyncio.create_task(self.handler(self.gate, self.session, self.user, self.question))
            try:
                await asyncio.wait_for(reached.wait(), 2)
                self.creator.assert_not_called()
                self.assertEqual(len(self.gate.redis.values), 2)
                duplicate = await self.handler(self.gate, self.session, self.user, self.question)
                self.assertEqual(duplicate.status_code, 409)
                self.assertIn(b'PROCESSING', duplicate.body)
                lease = next(iter(self.gate.leases))
                await lease.renew_once()
                self.assertEqual(len(self.gate.redis.values), 2)
            finally:
                resume.set()
                await first
            self.creator.assert_called_once()

    async def test_disabled_pause_does_not_sleep(self):
        with patch.dict(os.environ, {'SQLBOT_TEST_PAUSE_SECONDS': '0'}), \
                patch('common.core.question_gate.asyncio.sleep', new_callable=AsyncMock) as sleep:
            await self.gate.pause_for_test(None)
            sleep.assert_not_awaited()
