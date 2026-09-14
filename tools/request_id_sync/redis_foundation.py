"""Reuse the backup/hash-guarded installer for the Redis connection foundation."""
import argparse
from pathlib import Path
import sync

sync.FILES = [
    'backend/common/core/config.py',
    'backend/common/core/idempotency_redis.py',
    'backend/main.py',
    'tests/test_idempotency_redis.py',
    'tests/check_idempotency_redis_connection.py',
]

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
