"""Prepare a reviewed, hash-guarded request_id merge into the local Ubuntu copy."""
import argparse
import ast
import difflib
import hashlib
import json
from datetime import datetime
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
TARGET = Path('/home/sqlbotdev/sqlbot-local').resolve()
LLM_MERGE_MARKERS = ('request_id',)
CHECK_LLM_AST = False
FILES = [
    'backend/apps/chat/models/question_request.py',
    'backend/apps/chat/models/chat_model.py',
    'backend/apps/chat/api/chat.py',
    'backend/apps/chat/task/llm.py',
    'backend/apps/mcp/mcp.py',
    'frontend/src/utils/questionRequest.ts',
    'frontend/src/api/chat.ts',
    'frontend/src/views/chat/index.vue',
    'frontend/src/views/chat/answer/ChartAnswer.vue',
    'tests/test_question_request.py',
    'frontend/tests/questionRequest.test.mjs',
]


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def target_path(name):
    path = (TARGET / name).resolve()
    if not path.is_relative_to(TARGET):
        raise RuntimeError(f'Path outside target: {name}')
    return path


def prepare():
    report = SOURCE / 'tools/request_id_sync' / datetime.now().strftime('prepared-%Y%m%d-%H%M%S')
    report.mkdir()
    manifest = []
    diffs = []
    for name in FILES:
        target = target_path(name)
        before = target.read_bytes() if target.exists() else None
        old = before.decode('utf-8').splitlines(keepends=True) if before is not None else []
        new = (SOURCE / name).read_text(encoding='utf-8').splitlines(keepends=True)
        if name == 'backend/apps/chat/task/llm.py':
            # Keep Ubuntu text except the request_id edits; do not copy learning comments.
            merged = []
            for tag, i, j, a, b in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
                selected = tag != 'equal' and any(marker in ''.join(old[i:j] + new[a:b])
                                                 for marker in LLM_MERGE_MARKERS)
                merged.extend(new[a:b] if selected else old[i:j])
            new = merged
        after = ''.join(new).encode('utf-8')
        if name.endswith('.py'):
            ast.parse(after.decode('utf-8'))
        if name == 'backend/apps/chat/task/llm.py' and CHECK_LLM_AST:
            if ast.dump(ast.parse(after)) != ast.dump(ast.parse((SOURCE / name).read_text(encoding='utf-8'))):
                raise RuntimeError('LLM merge omitted executable changes; review before applying')
        staged = report / 'staged' / name
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(after)
        manifest.append({'path': name, 'before': digest(before), 'after': digest(after)})
        diffs.extend(difflib.unified_diff(old, new, fromfile='ubuntu/' + name, tofile='merged/' + name))
    (report / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (report / 'changes.diff').write_text(''.join(diffs), encoding='utf-8')
    print(report)


def apply(report):
    manifest = json.loads((report / 'manifest.json').read_text(encoding='utf-8'))
    if [entry['path'] for entry in manifest] != FILES:
        raise RuntimeError('Unexpected file list')
    pending = []
    for entry in manifest:
        name = entry['path']
        target = target_path(name)
        before = target.read_bytes() if target.exists() else None
        after = (report / 'staged' / name).read_bytes()
        if digest(after) != entry['after']:
            raise RuntimeError(f'Staged file changed: {name}')
        if digest(before) == entry['after']:
            continue
        if digest(before) != entry['before']:
            raise RuntimeError(f'Ubuntu file changed since review: {name}')
        pending.append((name, target, before, after))
    backup = TARGET / '.request-id-backups' / report.name
    backup.mkdir(parents=True, exist_ok=False)
    for name, target, before, after in pending:
        if before is not None:
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(before)
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for name, target, before, after in pending:
        current = target.read_bytes() if target.exists() else None
        if digest(current) != digest(before):
            raise RuntimeError(f'Concurrent change: {name}; originals in {backup}')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(after)
        if digest(target.read_bytes()) != digest(after):
            raise RuntimeError(f'Write verification failed: {name}')
        print('Merged:', name)
    print('Backup:', backup)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'apply'])
    parser.add_argument('report', nargs='?', type=Path)
    args = parser.parse_args()
    if args.mode == 'prepare':
        prepare()
    else:
        if args.report is None:
            parser.error('apply requires a prepared report path')
        apply(args.report.resolve())
