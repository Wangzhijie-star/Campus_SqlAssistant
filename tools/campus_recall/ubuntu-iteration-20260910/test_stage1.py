"""Dependency-free unit checks for validation policy and patch structure."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent / 'stage1/backend/apps/terminology'
code = (ROOT / 'embedding_validation.py').read_text(encoding='utf-8')
code = code.replace('from apps.ai_model.embedding import EmbeddingModelCache', '')
namespace = {}
exec(compile(code, 'embedding_validation.py', 'exec'), namespace)

class Tokenizer:
    def __call__(self, texts, **kwargs):
        assert kwargs == dict(add_special_tokens=True, truncation=False, padding=False)
        return {'input_ids': [[0] * (len(text) + 2) for text in texts]}

namespace['tokenizer_and_limit'] = lambda: (Tokenizer(), 8)
validate = namespace['validate_terminology_group']

class Checks(unittest.TestCase):
    def test_boundary(self):
        self.assertEqual(validate('名', '学学学学')['counts'], [8])
        with self.assertRaisesRegex(ValueError, '9 tokens'):
            validate('名', '学学学学学')

    def test_alias(self):
        with self.assertRaisesRegex(ValueError, '同义词'):
            validate('名', '学学学', ['别名名字'])

    def test_blank(self):
        with self.assertRaises(ValueError):
            validate('名', ' ')

    def test_entrypoints(self):
        tree = ast.parse((ROOT / 'curd/terminology.py').read_text(encoding='utf-8'))
        for name in ('create_terminology', 'update_terminology'):
            fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
            self.assertEqual(sum(isinstance(n.func, ast.Name) and n.func.id == 'validate_terminology_group' for n in calls), 1)
            self.assertEqual(sum(isinstance(n.func, ast.Attribute) and n.func.attr == 'commit' for n in calls), 1)

if __name__ == '__main__':
    unittest.main()
