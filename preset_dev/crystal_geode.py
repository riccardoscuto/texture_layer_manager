"""
TLM Showcase Preset — Crystal Geode
=====================================

100% procedural — NO PAINT layers — a clustered crystal geode with
per-cell randomly-coloured crystals glowing in dark rock matrix.
Showcases:

  1. **VORONOI `random_color = True`** — each Voronoi cell gets its own
     hash-based Fac value, so each crystal cluster takes a different
     position along the ColorRamp → per-cell colour variation in ONE
     procedural layer (vs. needing 8+ FILL layers each manually
     coloured).
  2. **VORONOI F1 + tight contrast** — produces "crystal cluster"
     pattern (one bright cell, dark edges around it) rather than the
     soft fade of F2 or DTE.
  3. **Emission via selective emission** — `emission_selector_type =
     'RANDOM_CELLS'` makes ~30% of the crystals glow, leaving the
     others dark — the classic "some crystals are active, some are
     dormant" geode look.
  4. **Roughness routing from the same Voronoi cells** — crystals are
     mirror-glossy (rough ≈ 0.05), the dark matrix between cells is
     matte (rough ≈ 0.85). Same proc drives both base_color and
     roughness in different layers.

Visual anatomy:
  - Dark grey-violet rock matrix (the geode shell exterior)
  - Voronoi-cell crystal clusters in random hues (amethyst, citrine,
    rose quartz, smoky topaz) via random_color hash
  - ~30% of cells emit a soft inner glow
  - Glossy reflective crystals on matte rock matrix

Layer stack (6 layers):
  01. Rock Matrix FILL                — dark grey-violet matrix
  02. Crystal Clusters VORONOI F1     — per-cell random colours
  03. Glow Emission VORONOI F1        → use_emission + RANDOM_CELLS selector
  04. Crystal Roughness VORONOI F1    → ROUGHNESS — glossy crystals, matte matrix
  05. Matrix Tint NOISE OVERLAY       — subtle rock colour variation
  06. Surface Microbump NOISE         → BUMP — rocky micro-texture (opacity=0)
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Crystal_Geode"
RESOLUTION = "1024"
TARGET_MESH = "TLM_CrystalGeode"
SUBDIV_LEVEL = 3

# ── Rock matrix ──
MATRIX_COLOR           = (0.045, 0.035, 0.060, 1.0)      # dark grey-violet
MATRIX_ROUGHNESS       = 0.85                            # matte rock
MATRIX_METALLIC        = 0.0

# ── Crystal clusters (VORONOI F1 + random color) ──
CRYSTAL_SCALE          = 2.2                             # bigger cells → readable crystals
CRYSTAL_RANDOMNESS     = 0.90                            # organic cells
CRYSTAL_CONTRAST       = 0.10                            # soft Fac sweep — smooth jewel-tone gradient
CRYSTAL_OPACITY        = 1.0
CRYSTAL_BLEND          = "MIX"
# Two end-colors for the per-cell ramp. With random_color=True, each
# cell picks a random Fac → samples a different point on this ramp.
# Use saturated jewel tones for "amethyst → citrine → topaz → rose" sweep.
CRYSTAL_COLOR_LO       = (0.580, 0.180, 0.880, 1.0)      # amethyst purple at Fac=0
CRYSTAL_COLOR_HI       = (0.980, 0.760, 0.180, 1.0)      # citrine gold at Fac=1
CRYSTAL_COLOR3         = (0.940, 0.350, 0.520, 1.0)      # rose quartz at mid
CRYSTAL_COLOR3_POSITION = 0.55

# ── Emission (glowing crystals) ──
EMISSION_COLOR         = (1.0, 0.85, 0.55, 1.0)          # warm crystal glow
EMISSION_STRENGTH      = 3.0
EMISSION_THRESHOLD     = 0.55                            # tight band — only inside cells
EMISSION_FALLOFF       = 0.18

# Selective emission — only some cells emit
EMISSION_SELECTOR_SCALE     = 4.0                        # match crystal scale for alignment
EMISSION_SELECTOR_THRESHOLD = 0.35                       # ~35% lit

# ── Crystal roughness routing ──
CRYSTAL_ROUGH_LO       = 0.05                            # mirror-glossy crystal facets
CRYSTAL_ROUGH_HI       = 0.85                            # rough rock matrix between cells
CRYSTAL_ROUGH_CONTRAST = 0.50

# ── Matrix tint variation ──
TINT_SCALE             = 3.0
TINT_OPACITY           = 0.35
TINT_BLEND             = "OVERLAY"
TINT_DARK              = (0.025, 0.018, 0.040, 1.0)
TINT_LIGHT             = (0.085, 0.065, 0.110, 1.0)

# ── Surface microbump (rocky matrix texture) ──
BUMP_SCALE             = 40.0
BUMP_STRENGTH          = 0.55
BUMP_DISTANCE          = 0.0030


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_mesh():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
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

def build_crystal_geode():
    _ensure_cycles()
    obj = _find_or_create_mesh()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Crystal Geode v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Rock Matrix ───
    l_matrix = _add_fill(mat, "01 Rock Matrix", MATRIX_COLOR,
                         opacity=1.0, output_channel="BASE_COLOR")
    l_matrix.use_metallic = True
    l_matrix.metallic_fill = MATRIX_METALLIC
    l_matrix.use_roughness = True
    l_matrix.roughness_fill = MATRIX_ROUGHNESS

    # ─── 02. Crystal Clusters (VORONOI F1 + random_color) ───
    l_cryst = _add_proc(mat, "02 Crystal Clusters", "VORONOI",
                        opacity=CRYSTAL_OPACITY,
                        blend_mode=CRYSTAL_BLEND,
                        output_channel="BASE_COLOR")
    l_cryst.proc_voronoi_feature = "F1"
    l_cryst.proc_voronoi_distance = "EUCLIDEAN"
    l_cryst.proc_scale = CRYSTAL_SCALE
    l_cryst.proc_randomness = CRYSTAL_RANDOMNESS
    l_cryst.proc_voronoi_random_color = True              # CRITICAL — per-cell random Fac
    l_cryst.proc_color1 = CRYSTAL_COLOR_LO
    l_cryst.proc_color2 = CRYSTAL_COLOR_HI
    l_cryst.use_proc_color3 = True
    l_cryst.proc_color3 = CRYSTAL_COLOR3
    l_cryst.proc_color3_position = CRYSTAL_COLOR3_POSITION
    l_cryst.proc_contrast = CRYSTAL_CONTRAST

    # ─── 03. Glow Emission (RANDOM_CELLS selector — ~30% of crystals glow) ───
    # IMPORTANT: use ADD blend mode (not MIX) so the BLACK ColorRamp
    # output doesn't REPLACE the upstream crystal colors. ADD with
    # BLACK = pass-through, leaving the crystals visible. Emission
    # fires independently via the emission pipeline.
    # opacity=1.0 keeps emission_strength × opacity → full strength.
    l_glow = _add_proc(mat, "03 Glow Emission", "VORONOI",
                       opacity=1.0,
                       blend_mode="ADD",
                       output_channel="BASE_COLOR")
    l_glow.proc_voronoi_feature = "F1"
    l_glow.proc_voronoi_distance = "EUCLIDEAN"
    l_glow.proc_scale = CRYSTAL_SCALE
    l_glow.proc_randomness = CRYSTAL_RANDOMNESS
    l_glow.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_glow.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_glow.proc_contrast = 0.55
    l_glow.use_emission = True
    l_glow.emission_color = EMISSION_COLOR
    l_glow.emission_strength = EMISSION_STRENGTH
    l_glow.proc_emission_threshold = EMISSION_THRESHOLD
    l_glow.proc_emission_falloff = EMISSION_FALLOFF
    l_glow.emission_selector_type = 'RANDOM_CELLS'
    l_glow.emission_selector_scale = EMISSION_SELECTOR_SCALE
    l_glow.emission_selector_threshold = EMISSION_SELECTOR_THRESHOLD
    l_glow.emission_selector_seed = 0.0

    # ─── 04. Crystal Roughness (VORONOI → ROUGHNESS, primary routing) ───
    # Crystal cells = low roughness (glossy), matrix between = high roughness (matte)
    l_rough = _add_proc(mat, "04 Crystal Roughness", "VORONOI",
                        opacity=1.0,
                        blend_mode="MIX",
                        output_channel="ROUGHNESS")
    l_rough.proc_voronoi_feature = "F1"
    l_rough.proc_voronoi_distance = "EUCLIDEAN"
    l_rough.proc_scale = CRYSTAL_SCALE
    l_rough.proc_randomness = CRYSTAL_RANDOMNESS
    # F1: Fac=0 at cell centres (crystals = low rough), Fac=1 at cell edges (matrix = high rough)
    l_rough.proc_color1 = (CRYSTAL_ROUGH_LO, CRYSTAL_ROUGH_LO, CRYSTAL_ROUGH_LO, 1.0)
    l_rough.proc_color2 = (CRYSTAL_ROUGH_HI, CRYSTAL_ROUGH_HI, CRYSTAL_ROUGH_HI, 1.0)
    l_rough.proc_contrast = CRYSTAL_ROUGH_CONTRAST

    # ─── 05. Matrix Tint Variation ───
    l_tint = _add_proc(mat, "05 Matrix Tint", "NOISE",
                       opacity=TINT_OPACITY,
                       blend_mode=TINT_BLEND,
                       output_channel="BASE_COLOR")
    l_tint.proc_scale = TINT_SCALE
    l_tint.proc_detail = 7.0
    l_tint.proc_roughness_proc = 0.55
    l_tint.proc_color1 = TINT_DARK
    l_tint.proc_color2 = TINT_LIGHT
    l_tint.proc_contrast = 0.30

    # ─── 06. Surface Microbump (opacity=0 to keep BLACK out of base_color) ───
    l_bump = _add_proc(mat, "06 Surface Microbump", "NOISE",
                       opacity=0.0,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_bump.proc_scale = BUMP_SCALE
    l_bump.proc_detail = 5.0
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_contrast = 0.55
    l_bump.use_bump = True
    l_bump.bump_strength = BUMP_STRENGTH
    l_bump.bump_distance = BUMP_DISTANCE

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Crystal Geode built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_crystal_geode()
