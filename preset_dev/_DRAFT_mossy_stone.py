"""
TLM Showcase Preset — Mossy Stone
====================================

100% procedural — NO PAINT layers — weathered stone with moss growing
on the top (sun-facing) side, where rain accumulates and the surface
stays damp. Demonstrates TWO under-used TLM systems at once:

  1. **NDOTL mask source** — gates moss to surfaces facing the scene's
     first Sun light, like real moss after rain (moss grows where it's
     wet, which is where the rain hits and lingers). The mask follows
     the sun direction live via the depsgraph hot-update handler — try
     rotating the sun in the viewport, the moss zone rotates with it.
  2. **Cumulative scalar routing** — moss and stone independently drive
     Roughness via two layers (NOISE proc on stone, FILL on moss) with
     the moss layer gated by NDOTL → a clean "matte moss on top,
     semi-rough stone elsewhere" transition that no single roughness
     texture could reproduce.

Why not DIRT/POINTINESS for "moss in cavities"? Smart masks rely on
geometry features (AO sampling distance, local pointiness deltas) which
don't differentiate well on procedurally-displaced rocky meshes — the
features are too small and uniformly distributed. NDOTL gives a
predictable, visually-distinctive mask that maps to the natural "moss
on top" pattern people recognise instantly.

Visual anatomy:
  - Warm grey-brown stone base (slight tan)
  - Voronoi cells give the surface a natural "stone fragment" pattern
  - Noise creates organic colour variation across the stone
  - NDOTL mask isolates sun-facing top → moss only grows there
  - Saturated green moss with internal hue variation
  - Moss is VERY MATTE (rough≈0.97), stone is mid-rough (≈0.65)
  - High-detail microbump for stone roughness; softer moss microbump

Layer stack (9 layers):
  01. Stone Base FILL                — warm grey-brown stone base
  02. Stone Cells VORONOI F1         — fragmented stone block pattern
  03. Stone Tint NOISE OVERLAY       — large-scale colour variation
  04. Stone Roughness NOISE          → ROUGHNESS — vary roughness 0.55-0.80
  05. Stone Microbump NOISE          → BUMP — fine surface detail
  06. Moss Base FILL + NDOTL mask    — green moss on top of stone
  07. Moss Hue Variation NOISE       — internal moss colour variety
  08. Moss Roughness FILL            → ROUGHNESS — moss is matte (0.97) on top
  09. Moss Bump NOISE                → BUMP — soft moss texture

Layers 06-09 all share the same NDOTL mask config so the moss zone
reads as a single coherent overlay following the sun direction.

⚠ Scene requirement: there must be at least one Sun light. NDOTL takes
the direction of the first Sun in the scene. Without a Sun, the mask
defaults to (0.4, -0.3, 0.85) — a top-front-right portrait fallback.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Mossy_Stone"
RESOLUTION = "1024"
TARGET_MESH = "TLM_MossyStone"
SUBDIV_LEVEL = 3

# ── Stone base palette ──
STONE_BASE             = (0.420, 0.380, 0.330, 1.0)     # warm tan-grey (slightly brighter to survive shadow side)
STONE_DARK             = (0.225, 0.205, 0.175, 1.0)     # deep stone joint colour
STONE_LIGHT            = (0.555, 0.510, 0.450, 1.0)     # lighter stone highlights

# ── Voronoi cells ──
CELL_SCALE             = 4.5                            # mid-size stone fragments
CELL_RANDOMNESS        = 0.80                           # organic non-grid
CELL_CONTRAST          = 0.20                           # soft cell edges (not hard tiles)
CELL_OPACITY           = 0.45
CELL_BLEND             = "MULTIPLY"                     # cells darken the joints

# ── Stone tint noise ──
TINT_SCALE             = 2.0
TINT_OPACITY           = 0.45
TINT_BLEND             = "OVERLAY"

# ── Stone roughness ──
STONE_ROUGH_LO         = 0.55                           # locally smoother (recently weathered)
STONE_ROUGH_HI         = 0.80                           # locally rougher (porous)
STONE_ROUGH_NOISE_SCALE = 5.0
STONE_ROUGH_CONTRAST   = 0.20                           # soft variation

# ── Stone microbump ──
STONE_BUMP_SCALE       = 35.0
STONE_BUMP_STRENGTH    = 0.60
STONE_BUMP_DISTANCE    = 0.0035

# ── Moss colours (saturated forest greens, slightly brighter) ──
MOSS_MID               = (0.165, 0.340, 0.105, 1.0)     # main moss green
MOSS_LIGHT             = (0.280, 0.450, 0.155, 1.0)     # sunlit moss highlights
MOSS_DARK              = (0.080, 0.170, 0.060, 1.0)     # deep moss shadow

# ── NDOTL mask config (shared by moss layers) ──
# NdotL ∈ [0,1] where 1 = surface fully facing the sun (top of stone),
# 0 = surface facing away (bottom). We map a narrow band of high-NdotL
# to moss coverage, with a soft edge for natural transition.
NDOTL_LEVELS_IN_MIN    = 0.45                           # where moss starts to appear
NDOTL_LEVELS_IN_MAX    = 0.75                           # where moss is fully present
NDOTL_LEVELS_GAMMA     = 1.0

# ── Alternative: DIRT mask config (kept for reference) ──
# These would gate moss to cavities/joints rather than the top. Not used
# by default because smart masks don't differentiate well on
# procedurally-displaced rocky meshes — pointiness/AO produce near-uniform
# values when the mesh has lots of evenly-distributed small features.
DIRT_AO_DISTANCE       = 0.45
DIRT_GEN_INTENSITY     = 1.30
DIRT_GEN_BREAKUP       = 0.55
DIRT_GEN_BREAKUP_SCALE = 18.0
DIRT_GEN_SHARPNESS     = 0.60

# ── Moss variation noise ──
MOSS_VAR_SCALE         = 8.0
MOSS_VAR_OPACITY       = 0.55
MOSS_VAR_BLEND         = "OVERLAY"

# ── Moss roughness (very matte = absorptive) ──
MOSS_ROUGHNESS         = 0.97

# ── Moss bump (soft textured) ──
MOSS_BUMP_SCALE        = 28.0
MOSS_BUMP_STRENGTH     = 0.35
MOSS_BUMP_DISTANCE     = 0.0030


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_mesh():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        if len(obj.data.polygons) < 5000:
            _subdivide(obj)
        return obj
    print(f"[TLM] Creating '{TARGET_MESH}' (subdivided ico sphere)…")
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_ico_sphere_add(radius=1.0, subdivisions=4, location=(0, 0, 1.0))
    obj = bpy.context.active_object
    obj.name = TARGET_MESH
    _subdivide(obj)
    return obj


def _subdivide(obj):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new(name="TLM_Subdiv", type='SUBSURF')
    mod.levels = SUBDIV_LEVEL
    mod.render_levels = SUBDIV_LEVEL
    try:
        with bpy.context.temp_override(active_object=obj, selected_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    try:
        with bpy.context.temp_override(active_object=obj, selected_objects=[obj]):
            bpy.ops.object.shade_smooth()
    except Exception:
        for p in obj.data.polygons:
            p.use_smooth = True


def _get_or_create_material(obj, name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    while obj.data.materials:
        obj.data.materials.pop(index=0)
    obj.data.materials.append(mat)
    obj.active_material_index = 0
    return mat


def _clear_layers(mat):
    tlm = mat.tlm
    while len(tlm.layers) > 0:
        tlm.layers.remove(len(tlm.layers) - 1)
    tlm.active_layer_index = 0


def _add_fill(mat, name, color, opacity=1.0, blend_mode="MIX",
              output_channel="BASE_COLOR"):
    _add_layer_common(bpy.context, "FILL")
    l = mat.tlm.layers[mat.tlm.active_layer_index]
    l.name = name
    l.fill_color = color
    l.opacity = opacity
    l.blend_mode = blend_mode
    l.output_channel = output_channel
    return l


def _add_proc(mat, name, proc_type, opacity=1.0, blend_mode="MIX",
              output_channel="BASE_COLOR"):
    _add_layer_common(bpy.context, "PROCEDURAL")
    l = mat.tlm.layers[mat.tlm.active_layer_index]
    l.name = name
    l.proc_type = proc_type
    l.opacity = opacity
    l.blend_mode = blend_mode
    l.output_channel = output_channel
    l.proc_coord_type = "OBJECT"
    return l


def _apply_moss_mask(layer):
    """Configure the layer's mask slot A with NDOTL — moss on the top
    (sun-facing) side of the stone, like real moss after rain. This was
    chosen over POINTINESS/AO/DIRT because those smart masks don't
    differentiate well on procedurally-displaced rocky meshes (verified
    empirically — see Trap 8o in procedural_gotchas.md).

    The DIRT_* constants below are preserved as alternative configuration
    for users who want to switch to cavity-based moss accumulation.
    """
    layer.use_mask = True
    layer.mask_source = 'NDOTL'
    layer.mask_invert = False               # NDOTL=1 on sun-facing surfaces → moss there
    layer.use_mask_levels = True
    layer.mask_levels_in_min = NDOTL_LEVELS_IN_MIN
    layer.mask_levels_in_max = NDOTL_LEVELS_IN_MAX
    layer.mask_levels_gamma = NDOTL_LEVELS_GAMMA


def _ensure_cycles():
    scene = bpy.context.scene
    if scene.render.engine != 'CYCLES':
        scene.render.engine = 'CYCLES'
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for sp in area.spaces:
                if sp.type == 'VIEW_3D' and sp.shading.type == 'SOLID':
                    sp.shading.type = 'MATERIAL'


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_mossy_stone():
    _ensure_cycles()
    obj = _find_or_create_mesh()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Mossy Stone v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Stone Base ───
    l_base = _add_fill(mat, "01 Stone Base", STONE_BASE,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = 0.0
    l_base.use_roughness = True
    l_base.roughness_fill = 0.70

    # ─── 02. Stone Cells (Voronoi F1 → fragment pattern) ───
    l_cells = _add_proc(mat, "02 Stone Cells", "VORONOI",
                        opacity=CELL_OPACITY,
                        blend_mode=CELL_BLEND,
                        output_channel="BASE_COLOR")
    l_cells.proc_voronoi_feature = "F1"
    l_cells.proc_voronoi_distance = "EUCLIDEAN"
    l_cells.proc_scale = CELL_SCALE
    l_cells.proc_randomness = CELL_RANDOMNESS
    l_cells.proc_color1 = STONE_LIGHT      # cell centres = light
    l_cells.proc_color2 = STONE_DARK       # cell edges = dark joints
    l_cells.proc_contrast = CELL_CONTRAST

    # ─── 03. Stone Tint Noise ───
    l_tint = _add_proc(mat, "03 Stone Tint", "NOISE",
                       opacity=TINT_OPACITY,
                       blend_mode=TINT_BLEND,
                       output_channel="BASE_COLOR")
    l_tint.proc_scale = TINT_SCALE
    l_tint.proc_detail = 8.0
    l_tint.proc_roughness_proc = 0.6
    l_tint.proc_color1 = STONE_DARK
    l_tint.proc_color2 = STONE_LIGHT
    l_tint.proc_contrast = 0.30

    # ─── 04. Stone Roughness (primary → ROUGHNESS) ───
    l_rough = _add_proc(mat, "04 Stone Roughness", "NOISE",
                        opacity=1.0,
                        blend_mode="MIX",
                        output_channel="ROUGHNESS")    # PRIMARY routing
    l_rough.proc_scale = STONE_ROUGH_NOISE_SCALE
    l_rough.proc_detail = 6.0
    l_rough.proc_roughness_proc = 0.5
    # ColorRamp output is RGBToBW'd to a scalar — color1=darker → low roughness,
    # color2=lighter → high roughness. With contrast=0.20 we get a soft gradient.
    l_rough.proc_color1 = (STONE_ROUGH_LO, STONE_ROUGH_LO, STONE_ROUGH_LO, 1.0)
    l_rough.proc_color2 = (STONE_ROUGH_HI, STONE_ROUGH_HI, STONE_ROUGH_HI, 1.0)
    l_rough.proc_contrast = STONE_ROUGH_CONTRAST

    # ─── 05. Stone Microbump (additional → BUMP) ───
    # opacity=0.0 keeps the BLACK proc colors out of the base_color chain
    # (otherwise MIX+full-opacity = full BLACK replace). Bump still fires
    # via use_bump=True independent of base_color opacity.
    l_bump = _add_proc(mat, "05 Stone Microbump", "NOISE",
                       opacity=0.0,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_bump.proc_scale = STONE_BUMP_SCALE
    l_bump.proc_detail = 5.0
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)         # invisible to base_color
    l_bump.proc_contrast = 0.55
    l_bump.use_bump = True
    l_bump.bump_strength = STONE_BUMP_STRENGTH
    l_bump.bump_distance = STONE_BUMP_DISTANCE

    # ─── 06. Moss Base (FILL with DIRT mask) ───
    l_moss = _add_fill(mat, "06 Moss Base", MOSS_MID,
                       opacity=1.0,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    _apply_moss_mask(l_moss)

    # ─── 07. Moss Hue Variation (NOISE with DIRT mask) ───
    l_moss_var = _add_proc(mat, "07 Moss Variation", "NOISE",
                           opacity=MOSS_VAR_OPACITY,
                           blend_mode=MOSS_VAR_BLEND,
                           output_channel="BASE_COLOR")
    l_moss_var.proc_scale = MOSS_VAR_SCALE
    l_moss_var.proc_detail = 7.0
    l_moss_var.proc_roughness_proc = 0.6
    l_moss_var.proc_color1 = MOSS_DARK
    l_moss_var.proc_color2 = MOSS_LIGHT
    l_moss_var.proc_contrast = 0.35
    _apply_moss_mask(l_moss_var)

    # ─── 08. Moss Roughness (FILL → ROUGHNESS, DIRT-masked) ───
    # In cavities (mask=1): contribution = 0.97 (moss matte)
    # Outside (mask=0): contribution = 0 → underlying stone roughness wins
    l_moss_rough = _add_fill(mat, "08 Moss Roughness",
                             (MOSS_ROUGHNESS, MOSS_ROUGHNESS, MOSS_ROUGHNESS, 1.0),
                             opacity=1.0,
                             blend_mode="MIX",
                             output_channel="ROUGHNESS")
    l_moss_rough.roughness_fill = MOSS_ROUGHNESS
    _apply_moss_mask(l_moss_rough)

    # ─── 09. Moss Bump (NOISE → BUMP, NDOTL-masked) ───
    # opacity=0.0 prevents BLACK proc colors from contributing to
    # base_color. The bump still fires via use_bump=True.
    l_moss_bump = _add_proc(mat, "09 Moss Bump", "NOISE",
                            opacity=0.0,
                            blend_mode="MIX",
                            output_channel="BASE_COLOR")
    l_moss_bump.proc_scale = MOSS_BUMP_SCALE
    l_moss_bump.proc_detail = 6.0
    l_moss_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_moss_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_moss_bump.proc_contrast = 0.45
    l_moss_bump.use_bump = True
    l_moss_bump.bump_strength = MOSS_BUMP_STRENGTH
    l_moss_bump.bump_distance = MOSS_BUMP_DISTANCE
    _apply_moss_mask(l_moss_bump)

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Mossy Stone built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_mossy_stone()
