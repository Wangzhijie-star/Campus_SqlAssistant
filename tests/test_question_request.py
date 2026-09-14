"""Request contract tests, independent of database and model-provider configuration."""
import ast
import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from apps.chat.models.question_request import ChatQuestionBase


def load_function(relative_path, name, namespace, class_name=None):
    """Run the actual function body with isolated DB/provider dependencies (not a live app test)."""
    path = Path(__file__).resolve().parents[1] / relative_path
    tree = ast.parse(path.read_text(encoding='utf-8'))
    body = tree.body
    if class_name:
        body = next(node for node in body if isinstance(node, ast.ClassDef) and node.name == class_name).body
    function = next(node for node in body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == name)
    function.decorator_list = []
    module = ast.parse('from __future__ import annotations')
    module.body.append(function)
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    return namespace[name]


class QuestionRequestTests(unittest.TestCase):
    def test_request_id_required_and_nonempty(self):
        for extra in ({}, {'request_id': None}, {'request_id': ''}, {'request_id': ' \t\n '},
                      {'request_id': 123}, {'request_id': 'x' * 129}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                ChatQuestionBase(chat_id=1, question='查询成绩', **extra)

    def test_same_id_preserved_for_retry(self):
        payload = dict(chat_id=1, question='查询成绩', request_id='client-request-1')
        first = ChatQuestionBase(**payload)
        retry = ChatQuestionBase(**payload)
        self.assertEqual(first.request_id, retry.request_id)
        self.assertEqual(first.request_id, payload['request_id'])

    def test_http_handler_forwards_id_for_question_and_regenerate(self):
        for text in ('查询成绩', '/regenerate 42'):
            with self.subTest(question=text):
                inner = AsyncMock(return_value='response')
                handler = load_function('backend/apps/chat/api/chat.py', 'question_answer',
                                        {'ChatQuestion': SimpleNamespace, 'guarded_question': inner})
                payload = ChatQuestionBase(chat_id=1, question=text, request_id='operation-1')
                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(question_gate=object())))
                result = asyncio.run(handler(None, None, payload, None, request))
                self.assertEqual(result, 'response')
                forwarded = inner.call_args.args[3]
                self.assertEqual(forwarded.request_id, payload.request_id)
                self.assertEqual(forwarded.question, text)
                self.assertEqual(forwarded.chat_id, payload.chat_id)

    def test_execution_echoes_id_with_record_id(self):
        session_factory = MagicMock()
        runner = load_function('backend/apps/chat/task/llm.py', 'run_task', {
            'ChatFinishStep': SimpleNamespace(GENERATE_CHART='chart'),
            'session_maker': session_factory,
            'orjson': SimpleNamespace(dumps=lambda value: json.dumps(value).encode()),
        }, class_name='LLMService')
        service = SimpleNamespace(
            ds=None, chat_question=SimpleNamespace(request_id='operation-1'),
            get_record=lambda: SimpleNamespace(id=42), finish=MagicMock(),
        )
        stream = runner(service)
        try:
            event = json.loads(next(stream).removeprefix('data:'))
            self.assertEqual(event, {'type': 'id', 'id': 42, 'request_id': 'operation-1'})
        finally:
            stream.close()

    def test_trim_and_limit(self):
        question = ChatQuestionBase(chat_id=1, question='查询成绩', request_id='  client-1  ')
        self.assertEqual(question.request_id, 'client-1')
        self.assertEqual(len(ChatQuestionBase(chat_id=1, question='q', request_id='x' * 128).request_id), 128)

    def test_http_validation_before_handler(self):
        app = FastAPI()
        accepted = []

        @app.post('/question')
        def question(payload: ChatQuestionBase):
            accepted.append(payload.request_id)
            return payload.model_dump()

        with TestClient(app) as client:
            for extra in ({}, {'request_id': ''}, {'request_id': '   '}):
                response = client.post('/question', json={'chat_id': 1, 'question': 'q', **extra})
                self.assertEqual(response.status_code, 422)
            self.assertEqual(accepted, [])
            response = client.post('/question', json={'chat_id': 1, 'question': 'q', 'request_id': 'r1'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['request_id'], 'r1')
            self.assertEqual(accepted, ['r1'])


if __name__ == '__main__':
    unittest.main()
