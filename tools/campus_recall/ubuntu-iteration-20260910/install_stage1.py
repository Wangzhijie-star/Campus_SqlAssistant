"""Run in Ubuntu backend with its venv; refuses unexpected source changes."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

package = Path(__file__).resolve().parent / 'stage1'
root = Path.home() / 'sqlbot-local'
manifest = json.loads((package / 'manifest.json').read_text())
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
for rel, hashes in manifest.items():
    if digest(package / rel) != hashes['after']:
        sys.exit(f'补丁文件校验失败：{rel}')
    if digest(root / rel) not in (hashes['before'], hashes['after']):
        sys.exit(f'源码已有其他修改，停止覆盖：{rel}')
for rel in manifest:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(package / rel, target)
print('第一阶段补丁已安装：4 个文件。未修改参数、已有向量或数据库。')
