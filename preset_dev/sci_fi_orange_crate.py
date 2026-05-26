"""
TLM Showcase Preset — Sci-Fi Orange-Lit Crate
================================================

100% procedural — NO PAINT layers — dark cyberpunk cargo container with
hot-orange power conduits glowing through panel seams. Reference: the
#3 material in the user's reference grid (top row) — a charcoal-black
cube with bright orange neon seams running between rectangular hull
panels, "only some panels powered on" reading.

Showcases:

  1. **BRICK proc with sentinel WHITE/BLACK Fac** — the recent fix
     to `_build_proc_fac_node` for BRICK lets us use Brick for both
     visible panel pattern AND mortar/seam emission, aligned by
     identical scale + offset. Compare with V1 fail in sci_fi_panel.py
     where Brick's natural Fac alternates brick FACES.
  2. **Selective emission (RANDOM_CELLS)** — only ~25% of seam
     regions are powered on, giving the "live system with dormant
     modules" reading. Without this every seam glows uniformly → looks
     like "lit grout", not power conduits.
  3. **EDGE_WEAR mask layer** — bright steel highlights on convex hull
     edges (where the paint has worn off from handling).
  4. **Roughness routing from the panel pattern** — panels are slightly
     less rough than the mortar seams, so reflections break at the
     seams. Single Brick proc drives base_color AND roughness with
     different colour mappings.

Visual anatomy:
  - Charcoal-black metallic hull (the cargo container body)
  - Rectangular panels in subtle tonal variation
  - Bright orange neon glowing between panels in ~25% of cells
  - Worn convex edges showing scratched steel
  - Slight microbump for "industrial metal" feel (not plastic)

Layer stack (7 layers):
  01. Steel Hull FILL                  — dark charcoal metallic base
  02. Panel Pattern BRICK              — rectangular panel grid (base_color)
  03. Panel Tonal Variation NOISE      — subtle per-area tonal shift
  04. Seam Emission BRICK + selector   — orange glow on ~25% of panel seams
  05. Edge Wear FILL + EDGE_WEAR mask  — bright scratched steel on edges
  06. Hull Roughness BRICK → ROUGHNESS — panels glossy, mortar matte
  07. Industrial Microbump NOISE → BUMP — subtle metal texture (opacity=0!)

⚠ Engine: Cycles required for EDGE_WEAR mask + selective emission
spatial alignment (per Trap 8c — POINTINESS/AO collapse in Eevee Next).
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_SciFi_Orange_Crate"
RESOLUTION = "1024"
TARGET_MESH = "TLM_SciFiCrate"

# ── Hull base ──
HULL_COLOR             = (0.012, 0.014, 0.020, 1.0)      # charcoal-black with slight blue
HULL_METALLIC          = 1.0                             # full metal
HULL_ROUGHNESS         = 0.55                            # mid-rough industrial finish

# ── Panel pattern (BRICK proc, UV-mapped) ──
# With cube_project UVs (0..1 per face on a 2m cube), proc_scale=2 gives
# ~2 brick units across a face. With PANEL_WIDTH=0.5, PANEL_ROW_HEIGHT=0.4
# the result is ~2-3 bricks per row + 2 rows per face — readable big panels.
PANEL_SCALE            = 1.0                             # fewer larger panels per face
PANEL_MORTAR_SIZE      = 0.035                            # thin seam
PANEL_MORTAR_SMOOTH    = 0.0                             # razor-sharp transition
PANEL_OFFSET           = 0.5                             # staggered brick layout
PANEL_SQUASH           = 1.0
PANEL_BIAS             = 0.0
PANEL_WIDTH            = 0.55                            # landscape panels
PANEL_ROW_HEIGHT       = 0.40                            # 2-3 rows per face
PANEL_FACE_DARK        = (0.018, 0.022, 0.028, 1.0)      # darker panel
PANEL_FACE_LIGHT       = (0.035, 0.040, 0.050, 1.0)      # lighter panel
PANEL_MORTAR_COLOR     = (0.002, 0.002, 0.003, 1.0)      # near-black recessed seam
PANEL_OPACITY          = 1.0
PANEL_BLEND            = "MIX"
PANEL_CONTRAST         = 0.25                            # smooth tonal range

# ── Panel tonal variation (NOISE OVERLAY) ──
TONAL_NOISE_SCALE      = 2.0
TONAL_OPACITY          = 0.40
TONAL_BLEND            = "OVERLAY"
TONAL_DARK             = (0.010, 0.012, 0.018, 1.0)
TONAL_LIGHT            = (0.080, 0.085, 0.110, 1.0)

# ── Seam emission (BRICK + selective) ──
EMISSION_COLOR         = (1.00, 0.25, 0.04, 1.0)         # saturated orange-red
EMISSION_STRENGTH      = 2.5                             # bright but stays orange (no yellow blowout)
EMISSION_THRESHOLD     = 0.50                            # tight to the mortar
EMISSION_FALLOFF       = 0.08                            # sharp seam edge
EMISSION_CONTRAST      = 0.85                            # tight band → razor seam

# Selector: only some cells are powered on
SELECTOR_TYPE          = 'RANDOM_CELLS'
SELECTOR_SCALE         = 5.0                             # higher = smaller selector cells → finer variation
SELECTOR_THRESHOLD     = 0.45                            # ~45% lit (the unlit regions read as "dormant")
SELECTOR_SEED          = 2.3                             # specific roll

# ── Edge wear (FILL + EDGE_WEAR mask) ──
EDGE_WEAR_COLOR        = (0.155, 0.160, 0.170, 1.0)      # scratched bright steel
EDGE_WEAR_OPACITY      = 1.0
EDGE_WEAR_GEN_INTENSITY = 1.20
EDGE_WEAR_GEN_BREAKUP  = 0.45
EDGE_WEAR_GEN_BREAKUP_SCALE = 14.0
EDGE_WEAR_GEN_SHARPNESS = 0.55
EDGE_WEAR_LEVELS_IN_MIN = 0.35
EDGE_WEAR_LEVELS_IN_MAX = 0.75

# ── Hull roughness (BRICK → ROUGHNESS) ──
PANEL_ROUGH_LO         = 0.42                            # panels — slightly less rough
PANEL_ROUGH_HI         = 0.75                            # mortar/seam — rougher

# ── Industrial microbump ──
BUMP_SCALE             = 50.0
BUMP_STRENGTH          = 0.35
BUMP_DISTANCE          = 0.0025


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_mesh():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        return obj
    print(f"[TLM] Creating '{TARGET_MESH}' (beveled cube)…")
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1.0))
    obj = bpy.context.active_object
    obj.name = TARGET_MESH
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    # Slight bevel + subdiv to get nice edges
    bev = obj.modifiers.new(name="Bevel", type='BEVEL')
    bev.width = 0.04
    bev.segments = 3
    bev.profile = 0.7
    sub = obj.modifiers.new(name="Subsurf", type='SUBSURF')
    sub.levels = 2
    sub.render_levels = 2
    try:
        with bpy.context.temp_override(active_object=obj, selected_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier=bev.name)
            bpy.ops.object.modifier_apply(modifier=sub.name)
    except Exception:
        bpy.ops.object.modifier_apply(modifier=bev.name)
        bpy.ops.object.modifier_apply(modifier=sub.name)
    try:
        with bpy.context.temp_override(active_object=obj, selected_objects=[obj]):
            bpy.ops.object.shade_smooth()
    except Exception:
        for p in obj.data.polygons:
            p.use_smooth = True

    # Cube-project UVs so all 6 faces get a clean 0..1 UV → BRICK pattern
    # appears on every face (Object coord only gives a 2D XY projection,
    # leaving the side faces with vertical-stripes-only).
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.cube_project(cube_size=1.0)
        bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        bpy.ops.object.mode_set(mode='OBJECT')
    return obj


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


def _apply_edge_wear_mask(layer):
    """Configure EDGE_WEAR smart mask."""
    layer.use_mask = True
    layer.mask_source = 'EDGE_WEAR'
    layer.mask_gen_intensity = EDGE_WEAR_GEN_INTENSITY
    layer.mask_gen_breakup = EDGE_WEAR_GEN_BREAKUP
    layer.mask_gen_breakup_scale = EDGE_WEAR_GEN_BREAKUP_SCALE
    layer.mask_gen_sharpness = EDGE_WEAR_GEN_SHARPNESS
    layer.use_mask_levels = True
    layer.mask_levels_in_min = EDGE_WEAR_LEVELS_IN_MIN
    layer.mask_levels_in_max = EDGE_WEAR_LEVELS_IN_MAX
    layer.mask_levels_gamma = 1.0


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

def build_scifi_crate():
    _ensure_cycles()
    obj = _find_or_create_mesh()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Sci-Fi Orange Crate v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Steel Hull ───
    l_hull = _add_fill(mat, "01 Steel Hull", HULL_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_hull.use_metallic = True
    l_hull.metallic_fill = HULL_METALLIC
    l_hull.use_roughness = True
    l_hull.roughness_fill = HULL_ROUGHNESS

    # ─── 02. Panel Pattern (BRICK → BASE_COLOR with use_color3 for mortar) ───
    l_panel = _add_proc(mat, "02 Panel Pattern", "BRICK",
                        opacity=PANEL_OPACITY,
                        blend_mode=PANEL_BLEND,
                        output_channel="BASE_COLOR")
    # CRITICAL: UV coord for the brick — Object coord projects only on
    # XY giving stripe-only side faces. UV with a cube_project unwrap
    # makes every face show the full grid.
    l_panel.proc_coord_type = "UV"
    l_panel.proc_scale = PANEL_SCALE
    l_panel.proc_brick_mortar_size = PANEL_MORTAR_SIZE
    l_panel.proc_brick_mortar_smooth = PANEL_MORTAR_SMOOTH
    l_panel.proc_brick_offset = PANEL_OFFSET
    l_panel.proc_brick_squash = PANEL_SQUASH
    l_panel.proc_brick_bias = PANEL_BIAS
    l_panel.proc_brick_width = PANEL_WIDTH
    l_panel.proc_brick_row_height = PANEL_ROW_HEIGHT
    l_panel.proc_color1 = PANEL_FACE_DARK
    l_panel.proc_color2 = PANEL_FACE_LIGHT
    l_panel.use_proc_color3 = True
    l_panel.proc_color3 = PANEL_MORTAR_COLOR
    l_panel.proc_color3_position = 0.5
    l_panel.proc_contrast = PANEL_CONTRAST

    # ─── 03. Panel Tonal Variation (NOISE OVERLAY) ───
    l_tonal = _add_proc(mat, "03 Tonal Variation", "NOISE",
                        opacity=TONAL_OPACITY,
                        blend_mode=TONAL_BLEND,
                        output_channel="BASE_COLOR")
    l_tonal.proc_scale = TONAL_NOISE_SCALE
    l_tonal.proc_detail = 8.0
    l_tonal.proc_roughness_proc = 0.55
    l_tonal.proc_color1 = TONAL_DARK
    l_tonal.proc_color2 = TONAL_LIGHT
    l_tonal.proc_contrast = 0.35

    # ─── 04. Seam Emission (BRICK + RANDOM_CELLS selector) ───
    # Use ADD blend with BLACK proc colors to avoid the Trap 8p BLACK-replace
    # bug — the emission pipeline fires independently from base_color.
    l_emis = _add_proc(mat, "04 Seam Emission", "BRICK",
                       opacity=1.0,
                       blend_mode="ADD",                # ADD with BLACK = passthrough
                       output_channel="BASE_COLOR")
    l_emis.proc_coord_type = "UV"                        # same UV as panel layer
    l_emis.proc_scale = PANEL_SCALE                     # MUST match panel scale
    l_emis.proc_brick_mortar_size = PANEL_MORTAR_SIZE
    l_emis.proc_brick_mortar_smooth = PANEL_MORTAR_SMOOTH
    l_emis.proc_brick_offset = PANEL_OFFSET
    l_emis.proc_brick_squash = PANEL_SQUASH
    l_emis.proc_brick_bias = PANEL_BIAS
    l_emis.proc_brick_width = PANEL_WIDTH
    l_emis.proc_brick_row_height = PANEL_ROW_HEIGHT
    l_emis.proc_color1 = (0.0, 0.0, 0.0, 1.0)            # BLACK — invisible in base_color
    l_emis.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_emis.proc_contrast = EMISSION_CONTRAST
    l_emis.use_emission = True
    l_emis.emission_color = EMISSION_COLOR
    l_emis.emission_strength = EMISSION_STRENGTH
    l_emis.proc_emission_threshold = EMISSION_THRESHOLD
    l_emis.proc_emission_falloff = EMISSION_FALLOFF
    l_emis.emission_selector_type = SELECTOR_TYPE
    l_emis.emission_selector_scale = SELECTOR_SCALE
    l_emis.emission_selector_threshold = SELECTOR_THRESHOLD
    l_emis.emission_selector_seed = SELECTOR_SEED

    # ─── 05. Edge Wear (FILL + EDGE_WEAR mask) ───
    l_wear = _add_fill(mat, "05 Edge Wear", EDGE_WEAR_COLOR,
                       opacity=EDGE_WEAR_OPACITY,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    _apply_edge_wear_mask(l_wear)

    # ─── 06. Hull Roughness (BRICK → ROUGHNESS) ───
    l_rough = _add_proc(mat, "06 Hull Roughness", "BRICK",
                        opacity=1.0,
                        blend_mode="MIX",
                        output_channel="ROUGHNESS")
    l_rough.proc_coord_type = "UV"
    l_rough.proc_scale = PANEL_SCALE
    l_rough.proc_brick_mortar_size = PANEL_MORTAR_SIZE
    l_rough.proc_brick_mortar_smooth = PANEL_MORTAR_SMOOTH
    l_rough.proc_brick_offset = PANEL_OFFSET
    l_rough.proc_brick_squash = PANEL_SQUASH
    l_rough.proc_brick_bias = PANEL_BIAS
    l_rough.proc_brick_width = PANEL_WIDTH
    l_rough.proc_brick_row_height = PANEL_ROW_HEIGHT
    # Panel faces = lower roughness, mortar = higher
    l_rough.proc_color1 = (PANEL_ROUGH_LO, PANEL_ROUGH_LO, PANEL_ROUGH_LO, 1.0)
    l_rough.proc_color2 = (PANEL_ROUGH_LO, PANEL_ROUGH_LO, PANEL_ROUGH_LO, 1.0)
    l_rough.use_proc_color3 = True
    l_rough.proc_color3 = (PANEL_ROUGH_HI, PANEL_ROUGH_HI, PANEL_ROUGH_HI, 1.0)
    l_rough.proc_color3_position = 0.5
    l_rough.proc_contrast = PANEL_CONTRAST

    # ─── 07. Industrial Microbump (opacity=0 to avoid BLACK-replace bug) ───
    l_bump = _add_proc(mat, "07 Industrial Microbump", "NOISE",
                       opacity=0.0,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_bump.proc_scale = BUMP_SCALE
    l_bump.proc_detail = 5.0
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_contrast = 0.50
    l_bump.use_bump = True
    l_bump.bump_strength = BUMP_STRENGTH
    l_bump.bump_distance = BUMP_DISTANCE

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Sci-Fi Orange Crate built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_scifi_crate()
