"""One-time source edit, preserving comments/formatting around execution checkpoints."""
import ast
from pathlib import Path

path = Path(__file__).resolve().parents[2] / 'backend/apps/chat/task/llm.py'
text = path.read_text(encoding='utf-8')
lines = text.splitlines(keepends=True)
tree = ast.parse(text)
cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'LLMService')
insertions = {}
for method in cls.body:
    if not isinstance(method, ast.FunctionDef) or not method.args.args or method.args.args[0].arg != 'self':
        continue
    for node in ast.walk(method):
        if not isinstance(node, (ast.Assign, ast.Expr, ast.Return)) or not getattr(node, 'value', None):
            continue
        calls = [item for item in ast.walk(node.value) if isinstance(item, ast.Call)]
        guarded = any(
            isinstance(call.func, ast.Name) and (
                call.func.id.startswith('save_') or call.func.id in ('start_log', 'end_log', 'rename_chat')
            ) or isinstance(call.func, ast.Attribute) and call.func.attr in ('commit', 'stream', 'invoke', 'execute_sql')
            for call in calls
        )
        if guarded and (node.lineno < 2 or 'self.ensure_execution()' not in lines[node.lineno - 2]):
            indent = len(lines[node.lineno - 1]) - len(lines[node.lineno - 1].lstrip())
            insertions[node.lineno - 1] = ' ' * indent + 'self.ensure_execution()\n'
    if method.name in ('run_task', 'run_analysis_or_predict_task'):
        outer_try = next(node for node in method.body if isinstance(node, ast.Try))
        for handler in outer_try.handlers:
            if isinstance(handler.type, ast.Name) and handler.type.id == 'Exception':
                line = handler.body[0].lineno - 1
                if 'self.execution_failed' not in lines[line]:
                    indent = len(lines[line]) - len(lines[line].lstrip())
                    insertions[line] = ' ' * indent + 'self.execution_failed = True\n'
for line, addition in sorted(insertions.items(), reverse=True):
    lines.insert(line, addition)
result = ''.join(lines)
ast.parse(result)
path.write_text(result, encoding='utf-8')
print('Inserted execution checkpoints:', len(insertions))
