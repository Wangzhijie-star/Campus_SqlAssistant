"""Real SQLModel persistence in isolated SQLite; PostgreSQL DDL is checked separately."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine
from test_question_request import load_function  # also establishes backend import path
from apps.chat.models.chat_model import ChatRecord
from apps.chat.curd import question_replay
from common.core.question_gate import LeaseLostError


class ReplayStorageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        ChatRecord.__table__.create(self.engine)
        self.patcher = patch.object(question_replay, 'engine', self.engine)
        self.patcher.start()
        self.lease = SimpleNamespace(ensure_active=Mock())
        with Session(self.engine) as session:
            session.add(ChatRecord(id=1, chat_id=1, request_id='r1', status='PROCESSING',
                                   request_fingerprint='fp', finish=True, create_time=datetime.now()))
            session.commit()

    def tearDown(self):
        self.patcher.stop()
        self.engine.dispose()

    def test_completion_is_durable_and_findable_by_request(self):
        question_replay.finalize(1, False, self.lease)
        record = question_replay.lookup(1, 'r1')
        self.assertEqual(record.status, 'SUCCESS')
        self.assertIsNotNone(record.finish_time)

    def test_saved_error_wins_over_finish_true(self):
        with Session(self.engine) as session:
            record = session.get(ChatRecord, 1)
            record.error = 'SQL parsing failed'
            session.add(record)
            session.commit()
        result = question_replay.finalize(1, False, self.lease)
        self.assertEqual(result.status, 'FAILED')
        self.assertEqual(result.error, 'SQL parsing failed')

    def test_submission_failure_has_replayable_message(self):
        result = question_replay.finalize(1, True, self.lease)
        self.assertEqual(result.status, 'FAILED')
        self.assertTrue(result.error)

    def test_lost_lease_does_not_publish_terminal_state(self):
        self.lease.ensure_active.side_effect = LeaseLostError('lost')
        with self.assertRaises(LeaseLostError):
            question_replay.finalize(1, False, self.lease)
        self.assertEqual(question_replay.lookup(1, 'r1').status, 'PROCESSING')

    def test_unique_request_and_nullable_legacy_records(self):
        with Session(self.engine) as session:
            session.add(ChatRecord(id=2, chat_id=1, request_id='r1'))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            session.add_all([ChatRecord(id=3, chat_id=1), ChatRecord(id=4, chat_id=1)])
            session.commit()
