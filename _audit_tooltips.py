"""
Tooltip audit: trova ogni `<Tipo>Property(...)` in properties.py + altri file
che NON ha `description=` non-vuoto.

Blender mostra description= nei tooltip della UI (hover-over). Senza descrizione,
il tooltip cade su `name=` o sul nome della property — UX da addon "amateur".
"""
import ast
import os

PROPERTY_KINDS = {
    'BoolProperty', 'IntProperty', 'FloatProperty', 'StringProperty',
    'EnumProperty', 'FloatVectorProperty', 'IntVectorProperty',
    'BoolVectorProperty', 'PointerProperty', 'CollectionProperty',
}


def analyze_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        src = f.read()
    tree = ast.parse(src)
    findings = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        # Blender Property declaration style:
        #   show_x: BoolProperty(name=..., description=...)
        # The Call lives in the ANNOTATION (not in the value, which is None).
        # Fall back to value-based call (`x = BoolProperty(...)`) too.
        if isinstance(node.annotation, ast.Call):
            call = node.annotation
        elif isinstance(node.value, ast.Call):
            call = node.value
        else:
            continue
        # Identify Property kind
        if isinstance(call.func, ast.Name):
            func_name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            func_name = call.func.attr
        else:
            continue
        if func_name not in PROPERTY_KINDS:
            continue

        # Identify the target (property name)
        prop_name = None
        if isinstance(node.target, ast.Name):
            prop_name = node.target.id

        # Extract kwargs
        kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg}

        # Check description
        desc_node = kwargs.get('description')
        has_description = False
        desc_value = None
        if desc_node is not None:
            if isinstance(desc_node, ast.Constant) and isinstance(desc_node.value, str):
                desc_value = desc_node.value
                has_description = bool(desc_value.strip())
            elif isinstance(desc_node, ast.JoinedStr):
                # f-string — treat as has description
                has_description = True
                desc_value = "(f-string)"
            else:
                has_description = True
                desc_value = "(non-literal)"

        # Also check `name=` for fallback context
        name_node = kwargs.get('name')
        name_value = None
        if isinstance(name_node, ast.Constant) and isinstance(name_node.value, str):
            name_value = name_node.value

        findings.append({
            'line': node.lineno,
            'kind': func_name,
            'prop_name': prop_name,
            'has_description': has_description,
            'description': desc_value,
            'name_label': name_value,
        })
    return findings


# Find all .py files at addon root and in operators/
def gather_files(root):
    targets = []
    for fname in os.listdir(root):
        if fname.endswith('.py') and not fname.startswith('_audit'):
            targets.append(os.path.join(root, fname))
    ops_dir = os.path.join(root, 'operators')
    if os.path.isdir(ops_dir):
        for fname in os.listdir(ops_dir):
            if fname.endswith('.py'):
                targets.append(os.path.join(ops_dir, fname))
    return targets


by_file = {}
for path in gather_files('.'):
    findings = analyze_file(path)
    if findings:
        by_file[path] = findings


total = 0
missing = 0
non_literal = 0
short = 0
for path, findings in by_file.items():
    for f in findings:
        total += 1
        if not f['has_description']:
            missing += 1
        elif isinstance(f['description'], str) and f['description'].startswith('('):
            non_literal += 1
        elif isinstance(f['description'], str) and len(f['description']) < 15:
            short += 1


print(f"=== TOOLTIP AUDIT — TLM ===")
print(f"Total Property definitions scanned: {total}")
print(f"  Missing description= entirely:   {missing}")
print(f"  Non-literal description:          {non_literal}")
print(f"  Description shorter than 15 chars: {short}")
print()
print("=== MISSING description= ===")
for path, findings in by_file.items():
    rel = os.path.relpath(path)
    for f in findings:
        if not f['has_description']:
            label = f['name_label'] or '(no name=)'
            print(f"  {rel}:L{f['line']}  {f['kind']}  prop={f['prop_name']!r:30s}  name={label!r}")

print()
print("=== SHORT description (<15 chars) ===")
for path, findings in by_file.items():
    rel = os.path.relpath(path)
    for f in findings:
        if (f['has_description']
                and isinstance(f['description'], str)
                and not f['description'].startswith('(')
                and len(f['description']) < 15):
            print(f"  {rel}:L{f['line']}  {f['kind']}  prop={f['prop_name']!r:30s}  desc={f['description']!r}")
