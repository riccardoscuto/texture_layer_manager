"""TLM profile script — measure rebuild cost and graph size.

How to run (in Blender):
  1. Select the object whose material you want to profile.
  2. Workspace 'Scripting' → Text Editor → Open this file → Run.
  3. Output goes to the System Console.

Reports:
  - Layer count + breakdown by type
  - Node tree size (TLM-tagged nodes vs total)
  - Rebuild time over 5 runs (mean + min)
  - Hot path coverage (which properties bypass the full rebuild)
  - Memory: image datablocks owned by the material

Use this BEFORE making optimisations to know where to spend time.
Run it again AFTER the fix to confirm impact.
"""

import bpy
import time


def _human(t):
    if t < 1e-3:
        return f"{t*1e6:.0f} µs"
    if t < 1.0:
        return f"{t*1e3:.1f} ms"
    return f"{t:.2f} s"


def main():
    obj = bpy.context.active_object
    if obj is None or obj.active_material is None:
        print("[profile] Select an object with a TLM material first.")
        return
    mat = obj.active_material
    if not hasattr(mat, "tlm"):
        print("[profile] Material has no .tlm — addon not loaded?")
        return

    tlm = mat.tlm
    nt = mat.node_tree

    # ── Section 1: Stack ─────────────────────────────────────────────────
    print("=" * 64)
    print(f"[profile] Material: {mat.name}    Object: {obj.name}")
    print("=" * 64)
    types = {}
    for l in tlm.layers:
        types[l.layer_type] = types.get(l.layer_type, 0) + 1
    print(f"layers     : {len(tlm.layers)}  " + ", ".join(
        f"{k}={v}" for k, v in sorted(types.items())
    ))

    # ── Section 2: Node tree size ────────────────────────────────────────
    total_nodes = len(nt.nodes)
    tlm_nodes = sum(1 for n in nt.nodes if n.name.startswith("TLM_"))
    non_tlm = total_nodes - tlm_nodes
    print(f"nodes      : {total_nodes} total ({tlm_nodes} TLM-managed, {non_tlm} user)")
    total_links = len(nt.links)
    print(f"links      : {total_links}")

    # Breakdown by node type for TLM-managed
    by_type = {}
    for n in nt.nodes:
        if n.name.startswith("TLM_"):
            by_type[n.type] = by_type.get(n.type, 0) + 1
    print("node types : " + ", ".join(
        f"{k}={v}" for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])[:6]
    ))

    # ── Section 3: Rebuild timing ────────────────────────────────────────
    from texture_layer_manager import compositing

    runs = []
    for _ in range(5):
        t0 = time.perf_counter()
        compositing.rebuild_node_tree(mat)
        t1 = time.perf_counter()
        runs.append(t1 - t0)

    runs.sort()
    print("rebuild    : "
          f"mean={_human(sum(runs)/len(runs))}  "
          f"min={_human(runs[0])}  "
          f"max={_human(runs[-1])}")

    # Per-layer cost estimate (mean / layer count)
    if tlm.layers:
        per_layer = (sum(runs) / len(runs)) / len(tlm.layers)
        print(f"           : ~{_human(per_layer)} per layer")

    # ── Section 4: Hot path coverage ─────────────────────────────────────
    from texture_layer_manager.compositing import _HOT_DISPATCH
    print(f"hot props  : {len(_HOT_DISPATCH)} entries in _HOT_DISPATCH")
    sample = sorted(_HOT_DISPATCH.keys())[:10]
    print(f"           : {', '.join(sample)}, ... ({len(_HOT_DISPATCH)-len(sample)} more)")

    # ── Section 5: Image memory ─────────────────────────────────────────
    img_total = 0
    img_count = 0
    for img in bpy.data.images:
        if not img.has_data:
            continue
        img_count += 1
        # 4 channels * 4 bytes * w * h (rough estimate, ignores compression)
        w, h = img.size
        img_total += w * h * 4 * 4

    print(f"images     : {img_count} datablocks, ~{img_total / (1024*1024):.1f} MB pixel buffers")

    # ── Section 6: Material settings ────────────────────────────────────
    print(f"auto_comp  : {tlm.auto_composite}")
    print(f"resolution : {tlm.resolution}")
    if hasattr(tlm, "use_base_color_alpha"):
        print(f"alpha-auto : {tlm.use_base_color_alpha}")

    # ── Section 7: Suggestions ──────────────────────────────────────────
    print("-" * 64)
    if (sum(runs) / len(runs)) > 0.25:
        print("⚠  Rebuild > 250 ms — slider drags will feel sticky.")
        print("   Suggestions: reduce layer count, simplify procedurals,")
        print("   or raise _REBUILD_DEBOUNCE_S in properties.py to absorb")
        print("   more changes per rebuild.")
    elif tlm_nodes > 200:
        print("⚠  Large graph (200+ TLM nodes). Acceptable but Cycles/")
        print("   Eevee material compile gets noticeable. Consider Flatten")
        print("   on the older / non-edited layers.")
    else:
        print("✓  Graph size and rebuild time look healthy.")
    print("=" * 64)


main()
