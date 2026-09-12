"""Real tokenizer and rejection-path checks. No database writes or inference."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path.cwd()))
from apps.terminology.embedding_validation import (
    build_terminology_text, tokenizer_and_limit, validate_terminology_group,
)
from apps.terminology.curd.terminology import create_terminology, update_terminology
from apps.terminology.models.terminology_model import TerminologyInfo

tokenizer, limit = tokenizer_and_limit()
print('实际有效上限：', limit)

def length(word, description):
    return len(tokenizer(build_terminology_text(word, description),
                         add_special_tokens=True, truncation=False)['input_ids'])

descriptions = {}
for n in range(1, limit * 2):
    description = '学' * n
    size = length('测试', description)
    if size in (limit - 1, limit, limit + 1):
        descriptions[size] = description
assert len(descriptions) == 3, '不能生成边界样本，请保留输出排查'

for size in (limit - 1, limit):
    result = validate_terminology_group('测试', descriptions[size])
    assert result['counts'] == [size]
    print(f'PASS：{size} tokens 允许保存')

def rejects(call, label):
    try:
        call()
    except ValueError as error:
        assert '超过模型上限' in str(error)
        print('PASS：' + label + '；' + str(error))
    else:
        raise AssertionError(label + '未拒绝')

rejects(lambda: validate_terminology_group('测试', descriptions[limit + 1]), '超长主术语被拒绝')
rejects(lambda: validate_terminology_group('测试', descriptions[limit], ['测试学学']), '仅同义词超长也整组拒绝')

class NoDatabase:
    def __getattr__(self, name):
        raise AssertionError('拒绝前不应操作数据库：' + name)

bad = TerminologyInfo(id=1, word='测试', description=descriptions[limit + 1])
rejects(lambda: create_terminology(NoDatabase(), bad, 1, lambda key: key), '新增在数据库操作前拒绝')
rejects(lambda: update_terminology(NoDatabase(), bad, 1, lambda key: key), '编辑在数据库操作前拒绝')

# If validation accidentally invokes the encoder, fail this check.
from apps.ai_model.embedding import EmbeddingModelCache
model = EmbeddingModelCache.get_model()
with patch.object(type(model), 'embed_documents', side_effect=AssertionError('不能执行向量推理')):
    validate_terminology_group('通过率', '有效成绩中成绩大于等于60分的学生占比。', ['合格率'])
print('PASS：长度校验没有调用 embed_documents')
print('第一阶段只读验证完成。网页报错、混合导入和向量重建仍需后续验证。')
