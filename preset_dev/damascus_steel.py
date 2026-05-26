"""
TLM Showcase Preset — Damascus Steel (Folded Blade)
=====================================================

100% procedural — NO PAINT layers — Damascus folded steel material.
Stress-tests the Wave procedural with distortion for the iconic folded
laminate pattern of damascus blades, combined with metallic PBR routing,
smart-mask edge wear, and subtle rust patina.

Anatomy of damascus visual:
  - Alternating light/dark steel layers from forge-welding two alloys
  - "Folded" swirly pattern from repeatedly folding the billet (= Wave
    proc with high distortion = swirly bands)
  - High metallicity, moderate roughness (polished but tactile)
  - Bright bevel/edge from sharpening (= EDGE_WEAR + brighter steel)
  - Slight oxidation in cavities (= DIRT + warm brown tint)

Layer stack (7 layers):
  01. Steel Base FILL              — mid-grey metallic foundation
  02. Damascus Pattern WAVE        — light/dark bands with distortion (color routing)
  03. Pattern Bump WAVE            — same wave, drives bump for tactile feel
  04. Color Warmth NOISE           — subtle hue variation (slight blue/warm shift)
  05. Microbump NOISE              — surface texture micro-imperfections
  06. Bright Bevel Edge FILL       — polished bright steel on EDGE_WEAR mask
  07. Cavity Patina FILL           — warm brown rust in DIRT cavities (subtle)

Renders best on a sphere or sculpted blade-like mesh. Uses Suzanne by
default for consistency with other anime/PBR Hero shots; the damascus
pattern wraps naturally over its curvature.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Damascus_Steel"
RESOLUTION = "1024"
TARGET_MESH = "TLM_DamascusBlade"
SUBDIV_LEVEL = 3

# ── Steel base (Layer 01) ──
STEEL_BASE_COLOR        = (0.42, 0.43, 0.46, 1.0)   # neutral mid-grey metal
STEEL_METALLIC          = 1.00
STEEL_ROUGHNESS         = 0.38                       # polished but not mirror

# ── Damascus pattern (Layer 02 — base color modulation) ──
# Wave BANDS with detail+distortion = the famous folded-laminate swirly look
PATTERN_LIGHT_STEEL     = (0.62, 0.64, 0.68, 1.0)   # bright nickel layer
PATTERN_DARK_STEEL      = (0.18, 0.18, 0.21, 1.0)   # dark high-carbon layer
PATTERN_WAVE_SCALE      = 4.0                        # ~10 bands per face
PATTERN_DETAIL          = 4.0                        # how many fold iterations
PATTERN_DETAIL_SCALE    = 1.5                        # finer detail noise
PATTERN_DETAIL_ROUGH    = 0.6
PATTERN_DISTORTION      = 6.0                        # KEY — swirly fold pattern
PATTERN_CONTRAST        = 0.82                       # sharp light/dark transitions
PATTERN_OPACITY         = 0.85
PATTERN_BLEND_MODE      = "MIX"

# ── Pattern bump (Layer 03) ──
# Same wave drives bump → the layers have physical relief like real damascus
BUMP_STRENGTH           = 0.30
BUMP_DISTANCE           = 0.0015

# ── Color warmth (Layer 04) — subtle ──
WARMTH_COLOR_COOL       = (0.36, 0.38, 0.46, 1.0)   # cool blue cast
WARMTH_COLOR_WARM       = (0.50, 0.45, 0.40, 1.0)   # warm brass cast
WARMTH_NOISE_SCALE      = 0.8                        # large-scale slow drift
WARMTH_OPACITY          = 0.18                       # very subtle
WARMTH_BLEND_MODE       = "OVERLAY"

# ── Microbump (Layer 05) ──
MICROBUMP_SCALE         = 80.0
MICROBUMP_STRENGTH      = 0.08
MICROBUMP_DISTANCE      = 0.0004

# ── Bright bevel edge (Layer 06) ──
BEVEL_COLOR             = (0.80, 0.82, 0.85, 1.0)   # polished mirror steel
BEVEL_ROUGHNESS         = 0.12                       # very polished
BEVEL_OPACITY           = 1.00
EDGE_MASK_SHARPNESS     = 0.85                       # only sharpest convex peaks
EDGE_MASK_INTENSITY     = 1.0
EDGE_MASK_BREAKUP       = 0.25
EDGE_MASK_BREAKUP_SCALE = 18.0

# ── Cavity patina (Layer 07) — subtle warm oxide ──
PATINA_COLOR            = (0.40, 0.28, 0.18, 1.0)   # warm rust brown
PATINA_OPACITY          = 0.30                       # very subtle
PATINA_MASK_AO_DIST     = 0.40
PATINA_MASK_SHARPNESS   = 0.55
PATINA_MASK_INTENSITY   = 0.9
PATINA_MASK_BREAKUP     = 0.50


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_mesh():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        if len(obj.data.polygons) < 5000:
            _subdivide(obj)
        return obj
    print(f"[TLM] Creating '{TARGET_MESH}' Suzanne for damascus blade test…")
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 1.0))
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

def build_damascus_steel():
    _ensure_cycles()
    obj = _find_or_create_mesh()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Damascus Steel v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Steel Base ───
    l_base = _add_fill(mat, "01 Steel Base", STEEL_BASE_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = STEEL_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = STEEL_ROUGHNESS

    # ─── 02. Damascus Folded Pattern ───
    # Wave BANDS + distortion = the iconic damascus look
    l_pat = _add_proc(mat, "02 Damascus Pattern", "WAVE",
                     opacity=PATTERN_OPACITY,
                     blend_mode=PATTERN_BLEND_MODE,
                     output_channel="BASE_COLOR")
    l_pat.proc_scale = PATTERN_WAVE_SCALE
    l_pat.proc_detail = PATTERN_DETAIL
    l_pat.proc_wave_type = 'BANDS'
    l_pat.proc_wave_profile = 'SIN'
    l_pat.proc_wave_bands_direction = 'DIAGONAL'
    l_pat.proc_wave_detail_scale = PATTERN_DETAIL_SCALE
    l_pat.proc_wave_detail_roughness = PATTERN_DETAIL_ROUGH
    l_pat.proc_distortion = PATTERN_DISTORTION
    l_pat.proc_color1 = PATTERN_DARK_STEEL    # color at low fac (band valley)
    l_pat.proc_color2 = PATTERN_LIGHT_STEEL   # color at high fac (band peak)
    l_pat.proc_contrast = PATTERN_CONTRAST

    # ─── 03. Pattern Bump ───
    # Same wave drives bump → physical relief at the steel layer boundaries
    l_bump = _add_proc(mat, "03 Pattern Bump", "WAVE",
                     opacity=0.0,                    # base_color contribution = 0
                     blend_mode="MIX",
                     output_channel="BASE_COLOR")
    l_bump.proc_scale = PATTERN_WAVE_SCALE
    l_bump.proc_detail = PATTERN_DETAIL
    l_bump.proc_wave_type = 'BANDS'
    l_bump.proc_wave_profile = 'SIN'
    l_bump.proc_wave_bands_direction = 'DIAGONAL'
    l_bump.proc_wave_detail_scale = PATTERN_DETAIL_SCALE
    l_bump.proc_wave_detail_roughness = PATTERN_DETAIL_ROUGH
    l_bump.proc_distortion = PATTERN_DISTORTION
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_contrast = PATTERN_CONTRAST
    l_bump.use_bump = True
    l_bump.bump_strength = BUMP_STRENGTH
    l_bump.bump_distance = BUMP_DISTANCE

    # ─── 04. Color Warmth ───
    l_warm = _add_proc(mat, "04 Color Warmth", "NOISE",
                       opacity=WARMTH_OPACITY,
                       blend_mode=WARMTH_BLEND_MODE,
                       output_channel="BASE_COLOR")
    l_warm.proc_scale = WARMTH_NOISE_SCALE
    l_warm.proc_detail = 6.0
    l_warm.proc_roughness_proc = 0.55
    l_warm.proc_color1 = WARMTH_COLOR_COOL
    l_warm.proc_color2 = WARMTH_COLOR_WARM
    l_warm.proc_contrast = 0.30

    # ─── 05. Microbump ───
    l_mb = _add_proc(mat, "05 Microbump", "NOISE",
                     opacity=0.0,
                     blend_mode="MIX",
                     output_channel="BASE_COLOR")
    l_mb.proc_scale = MICROBUMP_SCALE
    l_mb.proc_detail = 4.0
    l_mb.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_mb.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_mb.proc_contrast = 0.50
    l_mb.use_bump = True
    l_mb.bump_strength = MICROBUMP_STRENGTH
    l_mb.bump_distance = MICROBUMP_DISTANCE

    # ─── 06. Bright Bevel Edge ───
    # EDGE_WEAR mask = polished bright steel only on the convex peaks
    l_edge = _add_fill(mat, "06 Bright Bevel Edge", BEVEL_COLOR,
                       opacity=BEVEL_OPACITY,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_edge.use_metallic = True
    l_edge.metallic_fill = 1.0
    l_edge.use_roughness = True
    l_edge.roughness_fill = BEVEL_ROUGHNESS
    l_edge.use_mask = True
    l_edge.mask_source = 'EDGE_WEAR'
    l_edge.mask_gen_sharpness = EDGE_MASK_SHARPNESS
    l_edge.mask_gen_intensity = EDGE_MASK_INTENSITY
    l_edge.mask_gen_breakup = EDGE_MASK_BREAKUP
    l_edge.mask_gen_breakup_scale = EDGE_MASK_BREAKUP_SCALE

    # ─── 07. Cavity Patina ───
    # DIRT mask = subtle warm oxide accumulation in cavities/recesses
    l_pat2 = _add_fill(mat, "07 Cavity Patina", PATINA_COLOR,
                      opacity=PATINA_OPACITY,
                      blend_mode="MIX",
                      output_channel="BASE_COLOR")
    l_pat2.use_roughness = True
    l_pat2.roughness_fill = 0.70    # patina is rougher than polished steel
    l_pat2.use_mask = True
    l_pat2.mask_source = 'DIRT'
    l_pat2.mask_ao_distance = PATINA_MASK_AO_DIST
    l_pat2.mask_gen_sharpness = PATINA_MASK_SHARPNESS
    l_pat2.mask_gen_intensity = PATINA_MASK_INTENSITY
    l_pat2.mask_gen_breakup = PATINA_MASK_BREAKUP
    l_pat2.mask_gen_breakup_scale = 10.0

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Damascus Steel built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_damascus_steel()
