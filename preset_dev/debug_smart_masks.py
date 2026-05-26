"""
TLM Diagnostic v2 — Isolated Smart Mask Sanity Check
====================================================

Each smart-mask type gets its own dedicated material with only TWO layers:

    01. WHITE base (opacity 1)
    02. BLACK on top, gated by ONE smart mask (opacity 1, MIX)

This way you read the mask as a literal B/W map:
  • Black zones  → mask = 1 (mask is firing there)
  • White zones  → mask = 0 (mask not firing there)
  • Uniform grey → mask = constant value (engine isn't sampling geometry)

Three materials are created and assigned as separate slots on Suzanne so
you can switch between them via the Material panel dropdown:
  • TLM_DBG_MASK_DIRT      → black should fill eye sockets, mouth corners
  • TLM_DBG_MASK_EDGE_WEAR → black should hit nose tip, brow ridges, lobes
  • TLM_DBG_MASK_POINTINESS → black on raised features, white in valleys

Usage:
    from texture_layer_manager.preset_dev import debug_smart_masks
    debug_smart_masks.build_all()                # creates 3 mats + assigns
    debug_smart_masks.set_active_slot('DIRT')    # show DIRT material
    debug_smart_masks.set_active_slot('EDGE_WEAR')
    debug_smart_masks.set_active_slot('POINTINESS')
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


TARGET_MESH = "Suzanne"

MAT_DIRT       = "TLM_DBG_MASK_DIRT"
MAT_EDGE       = "TLM_DBG_MASK_EDGE_WEAR"
MAT_POINTINESS = "TLM_DBG_MASK_POINTINESS"


def _ensure_cycles():
    scene = bpy.context.scene
    if scene.render.engine != 'CYCLES':
        print(f"[DBG] Switching engine '{scene.render.engine}' → 'CYCLES'")
        scene.render.engine = 'CYCLES'
    try:
        scene.eevee.use_gtao = True
    except AttributeError:
        pass
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for sp in area.spaces:
                if sp.type == 'VIEW_3D' and sp.shading.type == 'SOLID':
                    sp.shading.type = 'MATERIAL'


def _get_or_create_material(name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    return mat


def _clear_layers(mat):
    tlm = mat.tlm
    while len(tlm.layers) > 0:
        tlm.layers.remove(len(tlm.layers) - 1)
    tlm.active_layer_index = 0


def _add_fill(mat, name, color):
    _add_layer_common(bpy.context, "FILL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.fill_color = color
    layer.opacity = 1.0
    layer.blend_mode = "MIX"
    layer.output_channel = "BASE_COLOR"
    return layer


def _build_isolated_mask_mat(mat_name, mask_source, mask_extras=None):
    """Single material: white base + black layer gated by ONE mask source."""
    suz = bpy.data.objects.get(TARGET_MESH)
    if suz is None:
        raise RuntimeError(f"No '{TARGET_MESH}' found")
    bpy.context.view_layer.objects.active = suz

    mat = _get_or_create_material(mat_name)
    tlm = mat.tlm
    tlm.resolution = "512"
    tlm.auto_composite = False
    _clear_layers(mat)

    # Need a temporary "active material" so _add_layer_common knows where
    # to add layers — make this mat active on Suzanne, build, restore later.
    # First we ensure Suzanne has this material in a slot.
    if mat_name not in [m.name for m in suz.data.materials if m]:
        suz.data.materials.append(mat)
    suz.active_material_index = list(suz.data.materials).index(mat)

    # Layer 1 — WHITE base (no mask, full coverage)
    _add_fill(mat, "01 White", (1.0, 1.0, 1.0, 1.0))

    # Layer 2 — BLACK gated by the mask
    l = _add_fill(mat, f"02 Black via {mask_source}", (0.02, 0.02, 0.02, 1.0))
    # CRITICAL: use_mask must be set to True. mask_source by itself is
    # ignored — the mask is only sampled if use_mask=True. Setting only
    # mask_source leaves the layer applying everywhere (mask = 1).
    l.use_mask = True
    l.mask_source = mask_source
    if mask_extras:
        for k, v in mask_extras.items():
            setattr(l, k, v)

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)
    print(f"[DBG] Built '{mat_name}' (mask = {mask_source})")
    return mat


def build_all():
    _ensure_cycles()

    suz = bpy.data.objects.get(TARGET_MESH)
    if suz is None:
        raise RuntimeError(f"No '{TARGET_MESH}' object found in scene")

    print(f"\n[DBG] Building isolated mask diagnostics on '{suz.name}'")
    print(f"      Engine: {bpy.context.scene.render.engine}")
    print(f"      Polys:  {len(suz.data.polygons)}")

    # Clear any existing material slots so we can lay these 3 in order
    while suz.data.materials:
        suz.data.materials.pop(index=0)

    _build_isolated_mask_mat(MAT_DIRT, 'DIRT', {
        'mask_ao_distance': 0.40,
        'mask_gen_breakup': 0.0,        # no noise — read mask cleanly
        'mask_gen_sharpness': 0.5,
        'mask_gen_intensity': 1.0,
    })
    _build_isolated_mask_mat(MAT_EDGE, 'EDGE_WEAR', {
        'mask_gen_breakup': 0.0,
        'mask_gen_sharpness': 0.5,
        'mask_gen_intensity': 1.0,
    })
    _build_isolated_mask_mat(MAT_POINTINESS, 'POINTINESS', {})

    # Default to DIRT visible
    set_active_slot('DIRT')
    print(f"[DBG] All 3 materials built. Switch via Material Properties dropdown.")
    print(f"      Or call set_active_slot('DIRT' | 'EDGE_WEAR' | 'POINTINESS')")


def set_active_slot(which):
    """Switch which debug material Suzanne renders with."""
    name = {
        'DIRT': MAT_DIRT,
        'EDGE_WEAR': MAT_EDGE,
        'POINTINESS': MAT_POINTINESS,
    }.get(which)
    if name is None:
        raise ValueError(f"Unknown mask kind '{which}' — use DIRT/EDGE_WEAR/POINTINESS")
    suz = bpy.data.objects.get(TARGET_MESH)
    if suz is None:
        return
    for i, m in enumerate(suz.data.materials):
        if m and m.name == name:
            suz.active_material_index = i
            print(f"[DBG] Suzanne now showing '{name}'")
            return
    print(f"[DBG] Material '{name}' not on Suzanne — call build_all() first")


if __name__ == "__main__":
    build_all()
