"""
TLM Showcase Preset — Space Universe Galaxy
============================================

Reverse-engineered from a wrapped "Space Universe Galaxy" node group
(dark-matter void + 2 nebula colours via distorted noise + multi-layer
Voronoi star fields + fresnel falloff + faint glossy reflection).

The original outputs a full *Shader*. TLM builds PBR channels, so we
rebuild the look as an EMISSION-routed colour stack + a black, low-rough
body for the faint glossy sheen. 100% procedural, no UVs, no hand-wired
nodes — every layer is a standard TLM layer routed to a channel.

Layer stack (bottom → top):
  01. Void Body      FILL  → BASE_COLOR  pure black, rough 0.22 (faint gloss only)
  02. Dark Matter    FILL  → EMISSION    near-black deep-blue emission base
  03. Nebula Cyan    NOISE → EMISSION    ADD, distorted, sparse high-end band
  04. Nebula Magenta NOISE → EMISSION    ADD, different scale/distortion
  05. Stars Fine     SCATTER → EMISSION  ADD, small dense white points
  06. Stars Bright   SCATTER → EMISSION  ADD, few large glowing points

This is the *vibrant* dial (saturated, high-contrast wisps). For the
subtler "realistic distant" reference look: drop emission_strength ~1.0,
lower nebula opacity + saturation, soften contrast to ~0.5, raise star
density.

──────────────────────────────────────────────────────────────────────────────
GOTCHAS DISCOVERED 2026-06-01 (cost several blown-white renders):
──────────────────────────────────────────────────────────────────────────────

GOTCHA #1 — a FILL layer routed to EMISSION uses `layer.emission_color`,
NOT `layer.fill_color`. channels.py FILL-emission path falls back to
emission_color (default WHITE 1,1,1). Setting only fill_color leaves a
uniform WHITE emission flooding the whole mesh. PROCEDURAL layers are
fine — they drive emission from their ColorRamp (proc_color1/2). So:
  FILL → emission  ⇒ set emission_color
  PROC → emission  ⇒ set proc_color1/proc_color2

GOTCHA #2 — `proc_use_manual_stops` DEFAULTS TO True. In manual mode the
ColorRamp stops come from proc_color1_position / proc_color2_position
(defaults 0.0 / 1.0 = full linear ramp) and proc_contrast + proc_ramp_center
are IGNORED. A new procedural layer therefore ignores the Contrast/Center
sliders until you set proc_use_manual_stops=False (or set the positions
directly). Every proc layer below sets manual=False so contrast/center
shape the transition band:
  _ramp_stops(contrast, center) → [center ± (0.5 - contrast*0.49)]
  e.g. contrast 0.78, center 0.66 → stops [0.546, 0.774] (sparse high-end
  band = wisps only where noise peaks, void elsewhere).
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Space_Galaxy"
RESOLUTION    = "1024"
TARGET_MESH   = "TLM_GalaxyBall"

EMISSION_STRENGTH = 1.5   # global (BSDF Emission Strength = max over emissive layers)

# 01 Void Body (BASE_COLOR) — pure black so no gray diffuse pickup from the
# world; roughness gives only a faint dielectric specular = the reference's
# subtle reflection.
VOID_BASE_COLOR = (0.0, 0.0, 0.0, 1.0)
VOID_ROUGHNESS  = 0.22

# 02 Dark Matter (EMISSION base) — near-black deep blue (NOT white! see gotcha #1)
DARK_MATTER_EMIS = (0.003, 0.004, 0.013, 1.0)

# 03/04 Nebula — distorted NOISE, ADD onto the void. Sparse high-end band.
NEB_CYAN    = (0.12, 0.58, 0.82, 1.0)
NEB_MAGENTA = (0.62, 0.08, 0.66, 1.0)

# 05/06 Stars — SCATTER (Voronoi-cell dots). Fine = small/dense, Bright = few/big.
STAR_WARM  = (1.0, 0.98, 0.94, 1.0)
STAR_WHITE = (1.0, 1.0, 1.0, 1.0)


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_mesh():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        return obj
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=64, ring_count=32,
                                          location=(0.0, 0.0, 1.1))
    obj = bpy.context.active_object
    obj.name = TARGET_MESH
    for p in obj.data.polygons:
        p.use_smooth = True
    return obj


def _get_or_create_material(obj, name):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
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


def _add_fill(mat, name, output_channel="BASE_COLOR", blend="MIX", opacity=1.0):
    _add_layer_common(bpy.context, "FILL")
    l = mat.tlm.layers[mat.tlm.active_layer_index]
    l.name = name
    l.output_channel = output_channel
    l.blend_mode = blend
    l.opacity = opacity
    return l


def _add_proc(mat, name, proc_type, output_channel="EMISSION", blend="ADD", opacity=1.0):
    _add_layer_common(bpy.context, "PROCEDURAL")
    l = mat.tlm.layers[mat.tlm.active_layer_index]
    l.name = name
    l.proc_type = proc_type
    l.output_channel = output_channel
    l.blend_mode = blend
    l.opacity = opacity
    l.proc_coord_type = "OBJECT"
    l.proc_use_manual_stops = False   # GOTCHA #2 — let Contrast/Center shape the ramp
    return l


def _ensure_cycles():
    scene = bpy.context.scene
    if scene.render.engine != 'CYCLES':
        scene.render.engine = 'CYCLES'


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_space_galaxy():
    _ensure_cycles()
    obj = _find_or_create_mesh()
    bpy.context.view_layer.objects.active = obj
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)

    print(f"\n[TLM] Building Space Universe Galaxy on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    S = EMISSION_STRENGTH

    # ─── 01. Void Body — pure black body, faint gloss ───
    l = _add_fill(mat, "01 Void Body", output_channel="BASE_COLOR")
    l.fill_color = VOID_BASE_COLOR
    l.use_roughness = True;  l.roughness_fill = VOID_ROUGHNESS
    l.use_metallic = True;   l.metallic_fill = 0.0

    # ─── 02. Dark Matter — emission base (note: emission_color, see gotcha #1) ───
    l = _add_fill(mat, "02 Dark Matter Glow", output_channel="EMISSION", blend="MIX")
    l.use_emission = True
    l.emission_color = DARK_MATTER_EMIS
    l.emission_strength = S

    # ─── 03. Nebula Cyan ───
    l = _add_proc(mat, "03 Nebula Cyan", "NOISE", opacity=0.82)
    l.proc_scale = 2.3; l.proc_detail = 3.0; l.proc_roughness_proc = 0.5; l.proc_distortion = 1.4
    l.proc_contrast = 0.78; l.proc_ramp_center = 0.66
    l.proc_color1 = (0.0, 0.0, 0.0, 1.0); l.proc_color2 = NEB_CYAN
    l.use_emission = True; l.emission_strength = S

    # ─── 04. Nebula Magenta ───
    l = _add_proc(mat, "04 Nebula Magenta", "NOISE", opacity=0.95)
    l.proc_scale = 3.0; l.proc_detail = 3.0; l.proc_roughness_proc = 0.5; l.proc_distortion = 1.8
    l.proc_contrast = 0.80; l.proc_ramp_center = 0.68
    l.proc_color1 = (0.0, 0.0, 0.0, 1.0); l.proc_color2 = NEB_MAGENTA
    l.use_emission = True; l.emission_strength = S

    # ─── 05. Stars Fine — small dense points ───
    l = _add_proc(mat, "05 Stars Fine", "SCATTER")
    l.proc_scale = 14.0; l.proc_randomness = 1.0
    l.proc_scatter_density = 0.30; l.proc_scatter_size = 0.22
    l.proc_contrast = 0.95; l.proc_ramp_center = 0.5
    l.proc_color1 = (0.0, 0.0, 0.0, 1.0); l.proc_color2 = STAR_WARM
    l.use_emission = True; l.emission_strength = S

    # ─── 06. Stars Bright — few large glowing points ───
    l = _add_proc(mat, "06 Stars Bright", "SCATTER")
    l.proc_scale = 9.0; l.proc_randomness = 1.0
    l.proc_scatter_density = 0.07; l.proc_scatter_size = 0.24
    l.proc_contrast = 0.90; l.proc_ramp_center = 0.5
    l.proc_color1 = (0.0, 0.0, 0.0, 1.0); l.proc_color2 = STAR_WHITE
    l.use_emission = True; l.emission_strength = S

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Space Universe Galaxy built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_space_galaxy()
