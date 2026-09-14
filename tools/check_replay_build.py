"""Read workspace source and check with Ubuntu's existing dependencies; no DB mutation."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
if sys.argv[1] == 'frontend':
    target = Path(tempfile.mkdtemp(prefix='sqlbot-replay-ui-'))
    shutil.copytree(root / 'frontend/src', target / 'src')
    for path in (root / 'frontend').glob('tsconfig*.json'):
        (target / path.name).write_text(path.read_text().replace('./node_modules/.tmp/', './.cache/'))
    for name in ('auto-imports.d.ts', 'vite.config.ts'):
        shutil.copy2(root / 'frontend' / name, target / name)
    shutil.copy2(root / 'frontend/package.json', target / 'package.json')
    (target / 'node_modules').symlink_to('/home/sqlbotdev/sqlbot-local/frontend/node_modules')
    result = subprocess.run([str(target / 'node_modules/.bin/vue-tsc'), '-b'], cwd=target)
    print(f'Typecheck workspace: {target}')
    sys.exit(result.returncode)
else:
    sys.path.insert(0, str(root / 'backend'))
    from apps.chat.curd.question_replay import lookup, snapshot
    from apps.chat.models.chat_model import ChatRecord
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql
    ddl = str(CreateTable(ChatRecord.__table__).compile(dialect=postgresql.dialect()))
    assert 'UNIQUE (chat_id, request_id)' in ddl
    print('Replay imports and PostgreSQL unique constraint compilation OK')
    import importlib.util
    import io
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    buffer = io.StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={
        'as_sql': True, 'output_buffer': buffer})
    spec = importlib.util.spec_from_file_location('replay_migration',
        root / 'backend/alembic/versions/072_question_replay.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(context):
        migration.upgrade()
    assert 'uq_chat_record_request' in buffer.getvalue()
    print('PostgreSQL migration offline SQL compilation OK (not executed)')
    import main
    print('Full application import OK (lifespan not started)')
    from datetime import datetime, timedelta
    from unittest.mock import patch
    from apps.chat.curd import question_replay
    started = datetime.now()
    record = ChatRecord(id=7, chat_id=1, request_id='smoke', status='SUCCESS',
                        create_time=started, finish_time=started + timedelta(seconds=2),
                        finish=True, data='[{"score":90}]', chart='{"type":"table"}')
    with patch.object(question_replay, 'Session') as session:
        session.return_value.__enter__.return_value.execute.return_value.scalars.return_value.all.return_value = [
            {'total_tokens': 12}, 3, None]
        payload = snapshot(record)
    assert payload['record']['total_tokens'] == 15
    assert payload['record']['duration'] == 2
    assert payload['record']['data'] == [{'score': 90}]
    print('Snapshot formatting and original usage aggregation smoke check OK')
