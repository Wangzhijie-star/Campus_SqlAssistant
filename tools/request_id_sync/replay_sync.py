"""Prepare/apply a reviewed replay-only deployment using the existing backup safeguards."""
import argparse
from pathlib import Path

import sync

sync.FILES = [
    'backend/common/core/question_gate.py',
    'backend/common/core/idempotency_redis.py',
    'backend/apps/chat/models/chat_model.py',
    'backend/apps/chat/api/chat.py',
    'backend/apps/chat/curd/chat.py',
    'backend/apps/chat/curd/question_replay.py',
    'backend/alembic/versions/072_question_replay.py',
    'frontend/src/views/chat/answer/ChartAnswer.vue',
    'frontend/src/views/chat/index.vue',
    'tests/test_question_gate.py',
    'tests/test_question_gate_routes.py',
    'tests/test_question_replay.py',
    'tests/test_question_replay_storage.py',
]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'apply'])
    parser.add_argument('report', nargs='?', type=Path)
    args = parser.parse_args()
    if args.mode == 'prepare':
        sync.prepare()
    else:
        if args.report is None:
            parser.error('apply requires the reviewed report directory')
        sync.apply(args.report.resolve())
