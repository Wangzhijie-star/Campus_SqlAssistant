"""Hash-guarded sync of the Excel/PostgreSQL SQL parsing fix."""
import argparse
from pathlib import Path
import sync

sync.FILES = ['backend/apps/db/db.py', 'tests/test_sqlglot_postgres.py']
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
