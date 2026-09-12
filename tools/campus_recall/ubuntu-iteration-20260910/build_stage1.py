"""Build a guarded, reviewable patch from the user's Ubuntu snapshot."""
from pathlib import Path
import hashlib
import json
import ast

ROOT = Path(__file__).resolve().parent
SRC = ROOT / 'source'
OUT = ROOT / 'stage1'
manifest = {}

def emit(rel, content):
    ast.parse(content)
    target = OUT / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    original = SRC / rel
    manifest[rel] = {
        'before': hashlib.sha256(original.read_bytes()).hexdigest() if original.exists() else None,
        'after': hashlib.sha256(target.read_bytes()).hexdigest(),
    }

def read(rel):
    return (SRC / rel).read_text(encoding='utf-8')

def replace_once(s, old, new):
    assert s.count(old) == 1, (old[:80], s.count(old))
    return s.replace(old, new, 1)

helper = '''"""Terminology length validation; no embedding forward pass is performed here."""
from apps.ai_model.embedding import EmbeddingModelCache


def build_terminology_text(word, description):
    # Same separator as the evaluated description strategy.
    return word.strip() + '。' + description.strip()


def tokenizer_and_limit():
    wrapper = EmbeddingModelCache.get_model()
    # Isolate the LangChain adapter dependency in one place and fail explicitly.
    client = getattr(wrapper, '_client', None)
    if client is None or not hasattr(client, 'tokenizer'):
        raise RuntimeError('无法取得当前向量模型的 tokenizer，请检查模型适配器')
    limits = [getattr(client, 'max_seq_length', None),
              getattr(client.tokenizer, 'model_max_length', None)]
    limits = [int(n) for n in limits if isinstance(n, (int, float)) and 0 < n < 1000000]
    if not limits:
        raise RuntimeError('无法确定当前向量模型的有效 token 上限')
    return client.tokenizer, min(limits)


def validate_terminology_group(word, description, other_words=None):
    if not word or not word.strip():
        raise ValueError('术语名称不能为空')
    if not description or not description.strip():
        raise ValueError('术语解释不能为空')
    names = [word.strip()] + [w.strip() for w in (other_words or []) if w and w.strip()]
    tokenizer, limit = tokenizer_and_limit()
    texts = [build_terminology_text(w, description) for w in names]
    encoded = tokenizer(texts, add_special_tokens=True, truncation=False, padding=False)
    counts = [len(ids) for ids in encoded['input_ids']]
    errors = []
    for index, (name, count) in enumerate(zip(names, counts)):
        if count > limit:
            kind = '主术语' if index == 0 else '同义词'
            errors.append(f'{kind}“{name}”与解释拼接后为 {count} tokens，超过模型上限 {limit}'
                          '（包含特殊 token），请缩减名称或解释；本组未保存')
    if errors:
        raise ValueError('；'.join(errors))
    return {'limit': limit, 'counts': counts, 'names': names}
'''
emit('backend/apps/terminology/embedding_validation.py', helper)

rel = 'backend/apps/terminology/curd/terminology.py'
s = read(rel)
s = replace_once(s, 'from apps.ai_model.embedding import EmbeddingModelCache',
                 'from apps.ai_model.embedding import EmbeddingModelCache\nfrom apps.terminology.embedding_validation import validate_terminology_group')
s = replace_once(s, '    create_time = datetime.datetime.now()\n\n    specific_ds',
                 '    validate_terminology_group(info.word, info.description, info.other_words)\n\n    create_time = datetime.datetime.now()\n\n    specific_ds')
s = replace_once(s, 'def update_terminology(session: SessionDep, info: TerminologyInfo, oid: int, trans: Trans):\n',
                 'def update_terminology(session: SessionDep, info: TerminologyInfo, oid: int, trans: Trans):\n    validate_terminology_group(info.word, info.description, info.other_words)\n')
# Preserve the Excel source location through the existing batch normalization.
s = replace_once(s, '            advanced_application_name=info.advanced_application_name,\n        )',
                 '            advanced_application_name=info.advanced_application_name,\n            source_sheet=info.source_sheet,\n            source_row=info.source_row,\n        )')
start = s.index('def update_terminology(')
end = s.index('\ndef delete_terminology(', start)
section = s[start:end]
assert section.count('    session.commit()') == 3
section = section.replace('    session.commit()\n', '', 2)
s = s[:start] + section + s[end:]
emit(rel, s)

rel = 'backend/apps/terminology/models/terminology_model.py'
s = read(rel)
s = replace_once(s, 'class TerminologyInfo(BaseModel):\n',
                 'class TerminologyInfo(BaseModel):\n    # Import diagnostics only; not database columns.\n    source_sheet: Optional[str] = None\n    source_row: Optional[int] = None\n')
emit(rel, s)

rel = 'backend/apps/terminology/api/terminology.py'
s = read(rel)
s = replace_once(s, 'advanced_application_name=advanced_application_name))',
                 'advanced_application_name=advanced_application_name,\n                                                   source_sheet=sheet_name, source_row=int(index) + 2))')
s = replace_once(s, '                    "errors": obj[\'errors\']',
                 '''                    "errors": [f"工作表 {obj['data'].source_sheet}，第 {obj['data'].source_row} 行：{err}"
                               for err in obj['errors']]''')
emit(rel, s)
(OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('Prepared stage1:', len(manifest), 'files; syntax parsed. No live source modified.')
