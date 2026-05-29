"""
TLM Showcase Preset — Iridescent Oil Slick (Petrol Puddle)
============================================================

A dark, wet, glossy surface swimming with organic rainbow thin-film
swirls — the look of petrol/oil on water. Distinct from the existing
Iridescent Hologram Foil (which is CLEAN Fresnel rings): here the
rainbow is driven by a heavily-distorted MARBLE Fac mapped through a
FULL 7-stop spectral ColorRamp, so the colours swirl like flowing oil
film rather than sitting in concentric angular bands.

Showcases:
  1. **N-stop ColorRamp** — Color1/2/3 + 4 `proc_extra_color_stops` give
     a complete violet→blue→cyan→green→gold→orange→magenta hue cycle from
     a single Fac signal (`proc_use_manual_stops=True` so my absolute stop
     positions are honoured, not remapped by contrast/center).
  2. **MARBLE + heavy distortion** — flowing swirl topology (distortion 14)
     that reads hand-marbled / oil-on-water, not cellular.
  3. **FRESNEL rim shimmer** — a second proc adds an angle-dependent icy
     highlight at the silhouette (thin-film shifts hue with view angle).
  4. **Wet PBR** — very low roughness (0.03–0.14) so the bright sky
     reflects sharply; the rainbow lives in the mid-tones between specular
     hot-spots, exactly like real petrol.

Layer stack (7):
  01. Wet Base FILL                  — near-black blue, glossy
  02. Oil Film Rainbow MARBLE        — 7-stop spectral swirl (BASE_COLOR)
  03. Rim Shimmer FRESNEL  SCREEN    — icy angle highlight at silhouette
  04. Film Breakup NOISE   MULTIPLY  — thickness unevenness / oily blotches
  05. Wet Gloss NOISE     → ROUGHNESS— near-mirror with faint variation
  06. Ripples NOISE       → BUMP     — surface-tension ripples
  07. Microbump NOISE     → BUMP     — fine detail
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Oil_Slick"
RESOLUTION = "1024"
TARGET_MESH = "TLM_OilSlick"
SPHERE_LOC = (0.0, 0.0, 1.2)
SUBDIV_LEVEL = 3

# ── Wet base (L01) ──
BASE_COLOR        = (0.006, 0.010, 0.018, 1.0)   # near-black, faint teal-blue
BASE_ROUGHNESS    = 0.06
BASE_METALLIC     = 0.0

# ── Oil film rainbow (L02 — MARBLE, 7-stop spectral) ──
# LOWER frequency than v1: oil film shows LARGE flowing colour zones, not
# high-frequency confetti. Big scale + low detail + moderate distortion.
FILM_SCALE        = 1.1
FILM_DISTORTION   = 6.5
FILM_DETAIL       = 2.5
FILM_OPACITY      = 0.95          # full film — the FRESNEL mask does the gating
FILM_BLEND        = "MIX"
FILM_MASK_IOR     = 1.60          # Fresnel mask: rainbow blooms at rim, dark wet centre
FILM_MASK_CONTRAST = 0.42         # 0.5 = neutral; <0.5 softens (wider bloom toward centre)
# Full hue cycle (deep oil tones, not neon):
OIL_VIOLET        = (0.34, 0.03, 0.62, 1.0)   # stop 0.00  (color1)
OIL_BLUE          = (0.02, 0.12, 0.85, 1.0)   # stop 0.17  (extra)
OIL_CYAN          = (0.00, 0.78, 0.85, 1.0)   # stop 0.34  (color3)
OIL_GREEN         = (0.12, 0.72, 0.22, 1.0)   # stop 0.50  (extra)
OIL_GOLD          = (0.95, 0.72, 0.10, 1.0)   # stop 0.66  (extra)
OIL_ORANGE        = (0.92, 0.30, 0.10, 1.0)   # stop 0.83  (extra)
OIL_MAGENTA       = (0.85, 0.05, 0.62, 1.0)   # stop 1.00  (color2)

# ── Rim shimmer (L03 — FRESNEL) ──
RIM_IOR           = 1.25
RIM_FACE          = (0.0, 0.0, 0.0, 1.0)      # face: add nothing (SCREEN)
RIM_GRAZE         = (0.10, 0.85, 1.00, 1.0)   # icy cyan at silhouette
RIM_CONTRAST      = 0.50
RIM_RAMP_CENTER   = 0.58
RIM_OPACITY       = 0.18
RIM_BLEND         = "SCREEN"

# ── Film breakup (L04 — NOISE MULTIPLY) ──
BREAK_SCALE       = 2.2
BREAK_OPACITY     = 0.38
BREAK_LO          = (0.22, 0.22, 0.26, 1.0)   # darkens patches → oily blotches
BREAK_HI          = (1.00, 1.00, 1.00, 1.0)

# ── Wet gloss (L05 → ROUGHNESS) ──
ROUGH_LO          = 0.02
ROUGH_HI          = 0.10
ROUGH_SCALE       = 4.0

# ── Ripples (L06 → BUMP) ──
RIPPLE_SCALE      = 7.0
RIPPLE_STRENGTH   = 0.16
RIPPLE_DISTANCE   = 0.0022

# ── Microbump (L07 → BUMP) ──
MICRO_SCALE       = 70.0
MICRO_STRENGTH    = 0.05
MICRO_DISTANCE    = 0.0008


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_sphere():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        return obj
    print(f"[TLM] Creating '{TARGET_MESH}' (subdivided ico sphere)…")
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_ico_sphere_add(radius=1.1, subdivisions=4,
                                          location=SPHERE_LOC)
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


def _reset_material_flags(tlm):
    """Make sure no persistent material-level flag leaks in from a prior
    material (the bronze-white bug: use_emission_output stayed True)."""
    for attr, val in (
        ("use_emission_output", False),
        ("use_volume_absorption", False),
        ("use_volume_scatter", False),
        ("use_displacement", False),
        ("bsdf_ior", 1.45),
    ):
        if hasattr(tlm, attr):
            try:
                setattr(tlm, attr, val)
            except Exception:
                pass


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


def _add_stop(layer, color, position):
    s = layer.proc_extra_color_stops.add()
    s.color = color
    s.position = position
    return s


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

def build_oil_slick():
    _ensure_cycles()
    obj = _find_or_create_sphere()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Iridescent Oil Slick on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _reset_material_flags(tlm)
    _clear_layers(mat)

    # ─── 01. Wet Base ───
    l_base = _add_fill(mat, "01 Wet Base", BASE_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = BASE_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = BASE_ROUGHNESS

    # ─── 02. Oil Film Rainbow (MARBLE → 7-stop spectral ramp) ───
    l_film = _add_proc(mat, "02 Oil Film Rainbow", "MARBLE",
                       opacity=FILM_OPACITY, blend_mode=FILM_BLEND,
                       output_channel="BASE_COLOR")
    l_film.proc_scale = FILM_SCALE
    l_film.proc_marble_wave_type = "BANDS"
    l_film.proc_marble_wave_profile = "SIN"
    l_film.proc_marble_bands_direction = "DIAGONAL"
    l_film.proc_detail = FILM_DETAIL
    l_film.proc_distortion = FILM_DISTORTION
    l_film.proc_marble_distortion = FILM_DISTORTION
    # 7-stop spectral ColorRamp — absolute positions
    l_film.proc_use_manual_stops = True
    l_film.proc_color1 = OIL_VIOLET                  # 0.00
    l_film.proc_color2 = OIL_MAGENTA                 # 1.00
    l_film.use_proc_color3 = True
    l_film.proc_color3 = OIL_CYAN                    # mid
    l_film.proc_color3_position = 0.34
    _add_stop(l_film, OIL_BLUE,   0.17)
    _add_stop(l_film, OIL_GREEN,  0.50)
    _add_stop(l_film, OIL_GOLD,   0.66)
    _add_stop(l_film, OIL_ORANGE, 0.83)
    # FRESNEL mask: the rainbow sheen concentrates toward the silhouette
    # (thin-film thins toward the facing centre) → dark wet glossy centre
    # that mirrors the sky, organic rainbow blooming at the rim.
    l_film.use_mask = True
    l_film.mask_source = "FRESNEL"
    l_film.mask_fresnel_ior = FILM_MASK_IOR
    l_film.mask_contrast = FILM_MASK_CONTRAST

    # ─── 03. Rim Shimmer (FRESNEL, SCREEN) ───
    l_rim = _add_proc(mat, "03 Rim Shimmer", "FRESNEL",
                      opacity=RIM_OPACITY, blend_mode=RIM_BLEND,
                      output_channel="BASE_COLOR")
    l_rim.proc_fresnel_ior = RIM_IOR
    l_rim.proc_color1 = RIM_FACE
    l_rim.proc_color2 = RIM_GRAZE
    l_rim.proc_contrast = RIM_CONTRAST
    l_rim.proc_ramp_center = RIM_RAMP_CENTER

    # ─── 04. Film Breakup (NOISE, MULTIPLY) ───
    l_break = _add_proc(mat, "04 Film Breakup", "NOISE",
                        opacity=BREAK_OPACITY, blend_mode="MULTIPLY",
                        output_channel="BASE_COLOR")
    l_break.proc_scale = BREAK_SCALE
    l_break.proc_detail = 6.0
    l_break.proc_roughness_proc = 0.6
    l_break.proc_color1 = BREAK_LO
    l_break.proc_color2 = BREAK_HI
    l_break.proc_contrast = 0.35

    # ─── 05. Wet Gloss (→ ROUGHNESS) ───
    l_rough = _add_proc(mat, "05 Wet Gloss", "NOISE",
                        opacity=1.0, blend_mode="MIX",
                        output_channel="ROUGHNESS")
    l_rough.proc_scale = ROUGH_SCALE
    l_rough.proc_detail = 4.0
    l_rough.proc_color1 = (ROUGH_LO, ROUGH_LO, ROUGH_LO, 1.0)
    l_rough.proc_color2 = (ROUGH_HI, ROUGH_HI, ROUGH_HI, 1.0)
    l_rough.proc_contrast = 0.3

    # ─── 06. Ripples (→ BUMP) ───
    l_rip = _add_proc(mat, "06 Ripples", "NOISE",
                      opacity=0.0, blend_mode="MIX",
                      output_channel="BASE_COLOR")
    l_rip.proc_scale = RIPPLE_SCALE
    l_rip.proc_detail = 5.0
    l_rip.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_rip.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_rip.use_bump = True
    l_rip.bump_strength = RIPPLE_STRENGTH
    l_rip.bump_distance = RIPPLE_DISTANCE

    # ─── 07. Microbump (→ BUMP) ───
    l_micro = _add_proc(mat, "07 Microbump", "NOISE",
                        opacity=0.0, blend_mode="MIX",
                        output_channel="BASE_COLOR")
    l_micro.proc_scale = MICRO_SCALE
    l_micro.proc_detail = 3.0
    l_micro.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_micro.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_micro.use_bump = True
    l_micro.bump_strength = MICRO_STRENGTH
    l_micro.bump_distance = MICRO_DISTANCE

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Oil Slick built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_oil_slick()
