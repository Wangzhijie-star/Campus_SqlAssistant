"""Install only the acceptance pause onto the reviewed replay deployment, with backups."""
import contextlib
import io
import json
from pathlib import Path

import sync

sync.FILES = ['backend/common/core/question_gate.py', 'backend/apps/chat/api/chat.py']

if __name__ == '__main__':
    baseline = sync.SOURCE / 'tools/request_id_sync/prepared-20260914-180351/manifest.json'
    entries = {entry['path']: entry for entry in json.loads(baseline.read_text())}
    for name in sync.FILES:
        target = sync.target_path(name)
        current = sync.digest(target.read_bytes())
        desired = sync.digest((sync.SOURCE / name).read_text(encoding='utf-8').encode())
        if current not in (entries[name]['after'], desired):
            raise SystemExit(f'Unexpected Ubuntu changes; stop and review: {name}')
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        sync.prepare()
    report = Path(output.getvalue().strip())
    print('Prepared:', report)
    sync.apply(report)
