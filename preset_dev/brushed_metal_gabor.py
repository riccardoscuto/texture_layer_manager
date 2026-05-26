"""
TLM Showcase Preset — Brushed Metal (Gabor-based)
==================================================

Hero B in the showcase rotation. Stress-tests the new GABOR procedural
(introduced in TLM's recent procedural batch, requires Blender 4.3+).

Gabor noise produces anisotropic streaks — perfect for the directional
micro-scratches of brushed aluminium / brushed stainless steel. Combined
with metallic=1.0 + low roughness, the procedural drives a BUMP layer
whose grooves catch light at different angles → the classic "brushed"
sheen that rotates with the camera.

Layer stack (6 layers, bottom-to-top):
  01. Metal Base FILL          — bright silver, metallic 1.0, roughness 0.32
  02. Brush Streaks GABOR      — bump only (no base_color), anisotropic streaks
                                 with high frequency = thin parallel grooves
  03. Streak Tone GABOR        — color variation along the same streaks
                                 (OVERLAY low opacity) for subtle luminance
                                 variation across the brushed surface
  04. Micro Defects NOISE      — tiny random pits + scratches (bump)
  05. Surface Smudge NOISE     — large-scale fingerprint-like grime
                                 (low-opacity OVERLAY)
  06. Edge Polish FILL         — slightly brighter polish on convex edges
                                 (EDGE_WEAR mask, optional polish ring)

Visually: a satin-finished aluminium panel with the characteristic
"linear sheen" that rotates with the camera angle. Use this as a panel
material on appliances, knives, watches, automotive trim, electronics
chassis.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Brushed_Metal"
RESOLUTION = "1024"
TARGET_MESH = "TLM_Panel"
PANEL_SUBDIV = 4         # plane subdivision so EDGE_WEAR has geometry to read

# ── Metal base (Layer 01) ──
METAL_BASE_COLOR        = (0.720, 0.730, 0.745, 1.0)  # neutral silver/aluminium
METAL_METALLIC          = 1.00
METAL_ROUGHNESS         = 0.32                          # satin, not mirror

# ── Brush streaks bump (Layer 02) — THE hero parameter ──
# Anisotropy = 1.0 → maximum directional streaks (vs 0 = isotropic noise)
# Orientation in DEGREES (0° = along X-axis = horizontal streaks on a
#   front-facing panel; 90° = vertical streaks)
# Frequency: higher → thinner / more densely packed streaks
STREAK_ORIENTATION      = 0.0          # horizontal sheen (Gabor 2D maps 0° to a
                                       # diagonal axis; tweak as needed per scene)
STREAK_ANISOTROPY       = 1.00         # max directional
# IMPORTANT: rendering anti-aliasing collapses Gabor frequencies above ~20
# to a uniform grey (Nyquist limit at typical 1K render res). For visible
# hairline grooves, keep frequency in [4, 18] and let SCALE multiply the
# pattern density. Tested freq=60 → fully grey, freq=8 → clean streaks.
STREAK_FREQUENCY        = 12.0
STREAK_SCALE            = 6.0          # higher scale → more streaks even at low freq
STREAK_BUMP_STRENGTH    = 0.80
STREAK_BUMP_DISTANCE    = 0.004

# ── Streak tonal variation (Layer 03) ──
# Same Gabor direction but mapped to slight luminance variation so the
# brushed surface isn't a perfect mathematical streak field
TONE_DARKER             = (0.610, 0.620, 0.635, 1.0)
TONE_BRIGHTER           = (0.820, 0.830, 0.845, 1.0)
TONE_OPACITY            = 0.30
TONE_BLEND_MODE         = "OVERLAY"
TONE_FREQUENCY          = 6.0          # broader streaks for the tonal layer
TONE_SCALE              = 6.0

# ── Micro defects (Layer 04) ──
# Tiny random nicks / specks distributed across the surface — almost
# subliminal but kills the "perfect maths" look
DEFECTS_NOISE_SCALE     = 80.0         # very fine grain
DEFECTS_BUMP_STRENGTH   = 0.08
DEFECTS_BUMP_DISTANCE   = 0.0004

# ── Surface smudge / fingerprint (Layer 05) ──
SMUDGE_COLOR_A          = (0.640, 0.650, 0.665, 1.0)
SMUDGE_COLOR_B          = (0.760, 0.770, 0.785, 1.0)
SMUDGE_NOISE_SCALE      = 0.9          # large soft blobs
SMUDGE_OPACITY          = 0.12
SMUDGE_BLEND_MODE       = "OVERLAY"

# ── Edge polish (Layer 06) ──
EDGE_POLISH_COLOR       = (0.890, 0.895, 0.905, 1.0)   # extra-bright polish
EDGE_POLISH_OPACITY     = 0.55
EDGE_POLISH_ROUGHNESS   = 0.18                          # mirror-ish on the wear edge
EDGE_MASK_SHARPNESS     = 0.85
EDGE_MASK_INTENSITY     = 0.70
EDGE_MASK_BREAKUP       = 0.20


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_panel():
    """Return TLM_Panel — a subdivided plane sized for a panel render."""
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        if len(obj.data.polygons) < 1000:
            _subdivide(obj)
        return obj

    print(f"[TLM] Creating '{TARGET_MESH}' plane …")
    for o in bpy.context.scene.objects:
        o.select_set(False)
    # 2x2m plane — looks good as a "panel close-up" framed by the camera
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0, 0, 0.5))
    plane = bpy.context.active_object
    plane.name = TARGET_MESH

    # Slight bevel so EDGE_WEAR has a real geometry edge to bite into
    # (without bevel, a Plane's outer ring is a perfect 90° edge and
    #  EDGE_WEAR collapses to a knife-thin line — useless for layer 06)
    bevel = plane.modifiers.new("TLM_Bevel", 'BEVEL')
    bevel.width = 0.04
    bevel.segments = 3
    bevel.profile = 0.5
    try:
        with bpy.context.temp_override(active_object=plane, selected_objects=[plane]):
            bpy.ops.object.modifier_apply(modifier=bevel.name)
    except Exception:
        bpy.ops.object.modifier_apply(modifier=bevel.name)

    _subdivide(plane)
    return plane


def _subdivide(obj):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new(name="TLM_Subdiv", type='SUBSURF')
    mod.levels = PANEL_SUBDIV
    mod.render_levels = PANEL_SUBDIV
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
    print(f"[TLM]   Subdivided to {len(obj.data.polygons)} polys + shade smooth")


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
    try:
        scene.eevee.use_gtao = True
    except AttributeError:
        pass
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for sp in area.spaces:
                if sp.type == 'VIEW_3D' and sp.shading.type == 'SOLID':
                    sp.shading.type = 'MATERIAL'


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_brushed_metal():
    _ensure_cycles()
    obj = _find_or_create_panel()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Brushed Metal (Gabor) v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Metal Base ───
    l_base = _add_fill(mat, "01 Metal Base", METAL_BASE_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = METAL_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = METAL_ROUGHNESS

    # ─── 02. Brush Streaks (Gabor) — BUMP only ───
    # Anisotropic Gabor noise → micro-grooves driving the bump scalar.
    # opacity=0 so no base_color contribution; proc_color1/2 black so
    # even if the bump pipeline reads colour, it doesn't bleed brown.
    l_streak = _add_proc(mat, "02 Brush Streaks", "GABOR",
                         opacity=0.0,
                         blend_mode="MIX",
                         output_channel="BASE_COLOR")
    l_streak.proc_scale = STREAK_SCALE
    l_streak.proc_gabor_anisotropy = STREAK_ANISOTROPY
    l_streak.proc_gabor_orientation = STREAK_ORIENTATION
    l_streak.proc_gabor_frequency = STREAK_FREQUENCY
    l_streak.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_streak.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_streak.proc_contrast = 0.55
    l_streak.use_bump = True
    l_streak.bump_strength = STREAK_BUMP_STRENGTH
    l_streak.bump_distance = STREAK_BUMP_DISTANCE

    # ─── 03. Streak Tonal Variation (Gabor) — colour ───
    # Same direction as layer 02 but broader streaks (lower frequency)
    # mapped to dark/light silver via OVERLAY → subtle luminance change
    # that prevents the bump-only streaks from looking too "mathy"
    l_tone = _add_proc(mat, "03 Streak Tone", "GABOR",
                       opacity=TONE_OPACITY,
                       blend_mode=TONE_BLEND_MODE,
                       output_channel="BASE_COLOR")
    l_tone.proc_scale = TONE_SCALE
    l_tone.proc_gabor_anisotropy = STREAK_ANISOTROPY
    l_tone.proc_gabor_orientation = STREAK_ORIENTATION
    l_tone.proc_gabor_frequency = TONE_FREQUENCY
    l_tone.proc_color1 = TONE_DARKER
    l_tone.proc_color2 = TONE_BRIGHTER
    l_tone.proc_contrast = 0.50

    # ─── 04. Micro Defects (Noise) — bump ───
    l_def = _add_proc(mat, "04 Micro Defects", "NOISE",
                      opacity=0.0,
                      blend_mode="MIX",
                      output_channel="BASE_COLOR")
    l_def.proc_scale = DEFECTS_NOISE_SCALE
    l_def.proc_detail = 4.0
    l_def.proc_roughness_proc = 0.55
    l_def.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_def.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_def.proc_contrast = 0.65
    l_def.use_bump = True
    l_def.bump_strength = DEFECTS_BUMP_STRENGTH
    l_def.bump_distance = DEFECTS_BUMP_DISTANCE

    # ─── 05. Surface Smudge (large-scale fingerprint) ───
    l_smudge = _add_proc(mat, "05 Surface Smudge", "NOISE",
                         opacity=SMUDGE_OPACITY,
                         blend_mode=SMUDGE_BLEND_MODE,
                         output_channel="BASE_COLOR")
    l_smudge.proc_scale = SMUDGE_NOISE_SCALE
    l_smudge.proc_detail = 6.0
    l_smudge.proc_roughness_proc = 0.55
    l_smudge.proc_color1 = SMUDGE_COLOR_A
    l_smudge.proc_color2 = SMUDGE_COLOR_B
    l_smudge.proc_contrast = 0.40

    # ─── 06. Edge Polish (mask = EDGE_WEAR) ───
    l_edge = _add_fill(mat, "06 Edge Polish", EDGE_POLISH_COLOR,
                       opacity=EDGE_POLISH_OPACITY,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_edge.use_metallic = True
    l_edge.metallic_fill = 1.0
    l_edge.use_roughness = True
    l_edge.roughness_fill = EDGE_POLISH_ROUGHNESS
    l_edge.use_mask = True
    l_edge.mask_source = 'EDGE_WEAR'
    l_edge.mask_gen_sharpness = EDGE_MASK_SHARPNESS
    l_edge.mask_gen_intensity = EDGE_MASK_INTENSITY
    l_edge.mask_gen_breakup = EDGE_MASK_BREAKUP
    l_edge.mask_gen_breakup_scale = 14.0

    # ── Rebuild & finalise ────────────────────────────────────────────
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Brushed Metal (Gabor) built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_brushed_metal()
