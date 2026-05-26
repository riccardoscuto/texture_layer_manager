"""
TLM Showcase Preset — Polished Burl Walnut
============================================

100% procedural — NO PAINT layers — high-gloss walnut burl wood with
swirling figured grain. Showcases:

  1. **MARBLE proc as wood grain** — sin bands distorted by noise
     produce organic swirly grain patterns indistinguishable from real
     burl wood (much more natural than MAGIC or pure WAVE).
  2. **Asymmetric ColorRamp** (`proc_ramp_center=0.32`) — pushes the
     ramp's transition toward the LOW Fac side, so the dark grain
     lines stay THIN against a warm caramel background. The classic
     wood look is "narrow dark grain on broad warm field", which a
     symmetric 50/50 ramp can't produce.
  3. **Cumulative roughness routing** — the same grain pattern that
     drives base_color ALSO drives roughness (slightly rougher on the
     grain lines, mirror-glossy on the polished wood). One procedural,
     two channels.
  4. **Mid-frequency NOISE layer** for the "porosity" — small dark
     specks scattered through the wood, the visible end-grain pores.

Visual anatomy:
  - Rich warm caramel/cognac base (mid-saturation umber)
  - Dark coffee-brown swirly grain rings (burl, irregular)
  - Mid-frequency dark speck noise (end-grain pores)
  - High-gloss polished finish (roughness 0.10) — sharp reflections
  - Subtle micro bump for the "wood feel" — without bump the surface
    looks like plastic instead of wood

Layer stack (6 layers):
  01. Wood Base FILL                  — warm caramel base
  02. Grain Swirl MARBLE              — burl grain rings (3-color ramp tinted)
  03. Pore Specks NOISE OVERLAY       — small dark pores from end-grain
  04. Grain Roughness MARBLE          → ROUGHNESS — slightly rougher on grain
  05. Pore Roughness NOISE            → ROUGHNESS — pores accumulate roughness
  06. Surface Microbump NOISE         → BUMP — wood texture under polish

⚠ Microbump opacity=0 is required to keep its BLACK proc colours out of
the base_color chain (Trap 8p in procedural_gotchas.md).
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Burl_Walnut"
RESOLUTION = "1024"
TARGET_MESH = "TLM_BurlWalnut"
SUBDIV_LEVEL = 3

# ── Wood base palette ──
WOOD_LIGHT             = (0.380, 0.235, 0.115, 1.0)      # warm caramel
WOOD_MID               = (0.235, 0.125, 0.055, 1.0)      # cognac
WOOD_DARK              = (0.060, 0.030, 0.015, 1.0)      # deep coffee grain
BASE_ROUGHNESS         = 0.10                            # gloss polished
BASE_METALLIC          = 0.0

# ── Grain swirl (MARBLE proc) ──
GRAIN_SCALE            = 1.5
GRAIN_DETAIL           = 10.0
GRAIN_DISTORTION       = 6.0                             # heavy swirl for burl
GRAIN_DIRECTION        = "DIAGONAL"
GRAIN_CONTRAST         = 0.40                            # moderate band → soft grain
GRAIN_RAMP_CENTER      = 0.32                            # asymmetric: dark grain thin
GRAIN_OPACITY          = 1.0
GRAIN_BLEND            = "MIX"

# ── Pore specks (NOISE) ──
PORE_SCALE             = 12.0                            # high freq dots
PORE_DETAIL            = 4.0
PORE_OPACITY           = 0.40
PORE_BLEND             = "OVERLAY"
PORE_LIGHT             = (0.45, 0.30, 0.15, 1.0)         # warm pore highlight
PORE_DARK              = (0.04, 0.02, 0.01, 1.0)         # dark pore core
PORE_CONTRAST          = 0.55

# ── Grain roughness ──
GRAIN_ROUGH_LO         = 0.08                            # polished on the wood field
GRAIN_ROUGH_HI         = 0.25                            # rougher on grain lines
GRAIN_ROUGH_RAMP_CENTER = 0.32                           # same asymmetric ramp
GRAIN_ROUGH_CONTRAST   = 0.50

# ── Pore roughness ──
PORE_ROUGH_LO          = 0.08
PORE_ROUGH_HI          = 0.35                            # pores are even rougher (recessed)
PORE_ROUGH_CONTRAST    = 0.55

# ── Surface microbump ──
BUMP_SCALE             = 80.0
BUMP_STRENGTH          = 0.18
BUMP_DISTANCE          = 0.0015


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

def build_burl_walnut():
    _ensure_cycles()
    obj = _find_or_create_mesh()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Burl Walnut v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Wood Base ───
    l_base = _add_fill(mat, "01 Wood Base", WOOD_LIGHT,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = BASE_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = BASE_ROUGHNESS

    # ─── 02. Grain Swirl (MARBLE → 3-color burl) ───
    l_grain = _add_proc(mat, "02 Grain Swirl", "MARBLE",
                        opacity=GRAIN_OPACITY,
                        blend_mode=GRAIN_BLEND,
                        output_channel="BASE_COLOR")
    l_grain.proc_scale = GRAIN_SCALE
    l_grain.proc_marble_wave_type = "BANDS"
    l_grain.proc_marble_wave_profile = "SIN"
    l_grain.proc_marble_bands_direction = GRAIN_DIRECTION
    l_grain.proc_detail = GRAIN_DETAIL
    l_grain.proc_marble_distortion = GRAIN_DISTORTION
    l_grain.proc_distortion = GRAIN_DISTORTION
    # 3-color: dark grain at low Fac, mid wood in middle, light wood at high Fac
    l_grain.proc_color1 = WOOD_DARK       # at Fac=0 (grain lines)
    l_grain.proc_color2 = WOOD_LIGHT      # at Fac=1 (lightest wood)
    l_grain.use_proc_color3 = True
    l_grain.proc_color3 = WOOD_MID         # mid Fac → mid tone
    l_grain.proc_color3_position = 0.55
    l_grain.proc_contrast = GRAIN_CONTRAST
    l_grain.proc_ramp_center = GRAIN_RAMP_CENTER

    # ─── 03. Pore Specks (NOISE OVERLAY) ───
    l_pore = _add_proc(mat, "03 Pore Specks", "NOISE",
                       opacity=PORE_OPACITY,
                       blend_mode=PORE_BLEND,
                       output_channel="BASE_COLOR")
    l_pore.proc_scale = PORE_SCALE
    l_pore.proc_detail = PORE_DETAIL
    l_pore.proc_roughness_proc = 0.6
    l_pore.proc_color1 = PORE_DARK
    l_pore.proc_color2 = PORE_LIGHT
    l_pore.proc_contrast = PORE_CONTRAST

    # ─── 04. Grain Roughness (MARBLE → ROUGHNESS, same pattern as Layer 02) ───
    l_grough = _add_proc(mat, "04 Grain Roughness", "MARBLE",
                         opacity=1.0,
                         blend_mode="MIX",
                         output_channel="ROUGHNESS")
    l_grough.proc_scale = GRAIN_SCALE
    l_grough.proc_marble_wave_type = "BANDS"
    l_grough.proc_marble_wave_profile = "SIN"
    l_grough.proc_marble_bands_direction = GRAIN_DIRECTION
    l_grough.proc_detail = GRAIN_DETAIL
    l_grough.proc_marble_distortion = GRAIN_DISTORTION
    l_grough.proc_distortion = GRAIN_DISTORTION
    l_grough.proc_color1 = (GRAIN_ROUGH_HI, GRAIN_ROUGH_HI, GRAIN_ROUGH_HI, 1.0)
    l_grough.proc_color2 = (GRAIN_ROUGH_LO, GRAIN_ROUGH_LO, GRAIN_ROUGH_LO, 1.0)
    l_grough.proc_contrast = GRAIN_ROUGH_CONTRAST
    l_grough.proc_ramp_center = GRAIN_ROUGH_RAMP_CENTER

    # ─── 05. Pore Roughness (NOISE → ROUGHNESS, same pattern as Layer 03) ───
    l_prough = _add_proc(mat, "05 Pore Roughness", "NOISE",
                         opacity=0.4,                    # blend with the grain roughness
                         blend_mode="LIGHTEN",            # take MAX → pores are roughest
                         output_channel="ROUGHNESS")
    l_prough.proc_scale = PORE_SCALE
    l_prough.proc_detail = PORE_DETAIL
    l_prough.proc_roughness_proc = 0.6
    l_prough.proc_color1 = (PORE_ROUGH_LO, PORE_ROUGH_LO, PORE_ROUGH_LO, 1.0)
    l_prough.proc_color2 = (PORE_ROUGH_HI, PORE_ROUGH_HI, PORE_ROUGH_HI, 1.0)
    l_prough.proc_contrast = PORE_ROUGH_CONTRAST

    # ─── 06. Surface Microbump (opacity=0 to keep BLACK out of base_color) ───
    l_bump = _add_proc(mat, "06 Surface Microbump", "NOISE",
                       opacity=0.0,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_bump.proc_scale = BUMP_SCALE
    l_bump.proc_detail = 5.0
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_contrast = 0.45
    l_bump.use_bump = True
    l_bump.bump_strength = BUMP_STRENGTH
    l_bump.bump_distance = BUMP_DISTANCE

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Burl Walnut built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_burl_walnut()
