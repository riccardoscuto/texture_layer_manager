"""Rigoroso scope-aware audit per cogliere NameError latenti nei sub-module."""
import ast, os, builtins

COMP_DIR = 'compositing'
FILES = ['__init__.py', 'masks.py', 'procedurals.py', 'adjustments.py',
         'frames.py', 'output.py', 'channels.py', 'hot_update.py', 'flatten.py']


def module_level_defs(path):
    with open(path, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    defs = set()
    imports = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defs.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defs.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != '*':
                    imports.add(alias.asname or alias.name)
    return defs, imports


all_defs = {}
file_defs = {}
file_imports = {}
for fn in FILES:
    d, i = module_level_defs(os.path.join(COMP_DIR, fn))
    file_defs[fn] = d
    file_imports[fn] = i
    for name in d:
        all_defs.setdefault(name, set()).add(fn)


class ScopeAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.scopes = [set()]
        self.free = []

    def _bind(self, name):
        self.scopes[-1].add(name)

    def _is_bound(self, name):
        for scope in self.scopes:
            if name in scope:
                return True
        return False

    def _bind_target(self, t):
        if isinstance(t, ast.Name):
            self._bind(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                self._bind_target(e)
        elif isinstance(t, ast.Starred):
            self._bind_target(t.value)

    def visit_FunctionDef(self, node):
        self._bind(node.name)
        for d in node.decorator_list:
            self.visit(d)
        for d in node.args.defaults + node.args.kw_defaults:
            if d is not None:
                self.visit(d)
        local = set()
        for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
            local.add(a.arg)
        if node.args.vararg:
            local.add(node.args.vararg.arg)
        if node.args.kwarg:
            local.add(node.args.kwarg.arg)
        self.scopes.append(local)
        for stmt in node.body:
            self.visit(stmt)
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        for d in node.args.defaults + node.args.kw_defaults:
            if d is not None:
                self.visit(d)
        local = set()
        for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
            local.add(a.arg)
        if node.args.vararg:
            local.add(node.args.vararg.arg)
        if node.args.kwarg:
            local.add(node.args.kwarg.arg)
        self.scopes.append(local)
        self.visit(node.body)
        self.scopes.pop()

    def visit_ClassDef(self, node):
        self._bind(node.name)
        for d in node.decorator_list:
            self.visit(d)
        for b in node.bases:
            self.visit(b)
        self.scopes.append(set())
        for stmt in node.body:
            self.visit(stmt)
        self.scopes.pop()

    def visit_Assign(self, node):
        self.visit(node.value)
        for t in node.targets:
            self._bind_target(t)

    def visit_AugAssign(self, node):
        self.visit(node.value)
        self._bind_target(node.target)

    def visit_AnnAssign(self, node):
        if node.value:
            self.visit(node.value)
        self._bind_target(node.target)

    def visit_For(self, node):
        self.visit(node.iter)
        self._bind_target(node.target)
        for s in node.body:
            self.visit(s)
        for s in node.orelse:
            self.visit(s)

    visit_AsyncFor = visit_For

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self._bind_target(item.optional_vars)
        for s in node.body:
            self.visit(s)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node):
        if node.type:
            self.visit(node.type)
        if node.name:
            self._bind(node.name)
        for s in node.body:
            self.visit(s)

    def visit_Import(self, node):
        for alias in node.names:
            self._bind(alias.asname or alias.name.split('.')[0])

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name != '*':
                self._bind(alias.asname or alias.name)

    def visit_comprehension(self, node):
        self.visit(node.iter)
        self._bind_target(node.target)
        for if_ in node.ifs:
            self.visit(if_)

    def visit_ListComp(self, node):
        self.scopes.append(set())
        for gen in node.generators:
            self.visit_comprehension(gen)
        self.visit(node.elt)
        self.scopes.pop()

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node):
        self.scopes.append(set())
        for gen in node.generators:
            self.visit_comprehension(gen)
        self.visit(node.key)
        self.visit(node.value)
        self.scopes.pop()

    def visit_Global(self, node):
        for n in node.names:
            self._bind(n)

    def visit_Nonlocal(self, node):
        for n in node.names:
            self._bind(n)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            if len(self.scopes) > 1 and not self._is_bound(node.id):
                self.free.append((node.id, node.lineno))


BUILTINS = set(dir(builtins))
SAFE_NAMES = {'True', 'False', 'None', 'self', 'cls', 'NotImplemented'}

found_any = False
for fn in FILES:
    if fn == '__init__.py':
        continue
    path = os.path.join(COMP_DIR, fn)
    with open(path, 'r', encoding='utf-8') as fp:
        tree = ast.parse(fp.read())
    analyzer = ScopeAnalyzer()
    analyzer.scopes[0] |= file_defs[fn] | file_imports[fn]
    analyzer.visit(tree)

    unique = {}
    for name, ln in analyzer.free:
        unique.setdefault(name, ln)

    missing = []
    for name, ln in sorted(unique.items()):
        if name in BUILTINS or name in SAFE_NAMES:
            continue
        if name.startswith('__') and name.endswith('__'):
            continue
        resolves_in = sorted(all_defs.get(name, set()))
        if not resolves_in:
            # Maybe imported at __init__.py level — check there too
            if name in file_imports.get('__init__.py', set()):
                missing.append((name, ln, '__init__.py IMPORT (stdlib/3rd-party alias — needs to be imported here too)'))
            else:
                missing.append((name, ln, 'NOT FOUND IN PACKAGE'))
        elif fn not in resolves_in:
            missing.append((name, ln, f'defined in: {resolves_in}'))

    if missing:
        found_any = True
        print(f"\n=== {fn} ===")
        for name, ln, where in missing:
            print(f"  L{ln}: {name}  ({where})")

if not found_any:
    print("CLEAN: no unresolved free names in any submodule")
