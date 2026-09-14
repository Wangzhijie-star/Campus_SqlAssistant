"""Back up and sync the reviewed in-flight locking implementation."""
import argparse
from pathlib import Path
import sync

sync.FILES = [
    'backend/common/core/question_gate.py',
    'backend/common/core/idempotency_redis.py',
    'backend/common/core/config.py',
    'backend/main.py',
    'backend/apps/chat/api/chat.py',
    'backend/apps/chat/task/llm.py',
    'backend/apps/mcp/mcp.py',
    'frontend/src/views/chat/index.vue',
    'frontend/src/views/chat/answer/ChartAnswer.vue',
    'frontend/src/views/chat/answer/AnalysisAnswer.vue',
    'frontend/src/views/chat/answer/PredictAnswer.vue',
    'tests/test_question_request.py',
    'tests/test_question_gate.py',
    'tests/test_question_gate_routes.py',
    'tests/check_question_gate_redis.py',
]
sync.LLM_MERGE_MARKERS = ('request_id', 'execution', 'LeaseLost', 'self.finish', 'session_maker.remove')
sync.CHECK_LLM_AST = True
parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['prepare', 'apply'])
parser.add_argument('report', nargs='?', type=Path)
args = parser.parse_args()
if args.mode == 'prepare':
    sync.prepare()
else:
    if args.report is None:
        parser.error('apply requires a prepared report')
    sync.apply(args.report.resolve())
