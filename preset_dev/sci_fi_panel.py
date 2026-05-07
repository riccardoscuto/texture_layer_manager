"""
TLM Flagship Preset — Sci-Fi Panel (dark hull with rectangular panels +
emissive seams)
=========================================================================

Recreates the centre material from the reference grid: a dark
metallic hull broken into RECTANGULAR brick-style panels with bright
orange emissive light strips running along the panel seams.

How to use:
  1. Open Blender 5.0 with the TLM addon enabled.
  2. Select a mesh — a Cube reads the brick pattern crisply, a Sphere
     or Suzanne also work but the rectangles wrap to the UV.
  3. Open the Scripting editor → New → paste this file → Run.
  4. Build a material called "TLM_Sci_Fi_Panel" on the active object.
  5. View in Material Preview / Rendered. Cycles emission is brighter
     than Eevee — both work.

Two iterations of this preset failed before getting here. Notes:
  V1: Used Brick for both panels and emission. Brick's Fac output
      distinguishes Color1 vs Color2 BRICKS (alternation), not
      brick-vs-mortar — so the emission pipeline (which inverts the
      fac to place the glow on the "low" side) lit alternating brick
      FACES with full orange instead of seam lines.
  V2: Switched to HEX_GRID + Voronoi DTE. Worked correctly but
      produced ORGANIC Voronoi cells, not the rectangular panel look
      of the reference.

V3 (this file): The TLM addon was extended so `_build_proc_fac_node`
for BRICK now uses sentinel colours (WHITE brick + BLACK mortar) and
SeparateColor.R to derive a brick-vs-mortar mask. With that fix in
place we can use Brick for both:
  - Visible Panel Pattern: layer.proc_color1/2/3 give the panel
    colours (Color1 / Color2 alternating for cell tonal variation,
    Mortar = darker recessed seam)
  - Emissive Strips: same scale + offset/squash so the seams align
    perfectly. The emission pipeline now correctly carves the glow
    along the mortar.

Layer stack (top to bottom):
   0. Hand Details      — empty PAINT
   1. Color Grade       — HUE_SAT on Base Color
   2. Roughness Boost   — BRIGHT_CONTRAST on Roughness
   3. Emissive Strips   — Brick aligned with Panel Pattern, glow at seams
   4. Surface Wear      — Voronoi DISTANCE_TO_EDGE on Roughness
   5. Panel Variation   — Voronoi F1 random per cell, OVERLAY on base
   6. Panel Pattern     — Brick on Base Color (defines rectangles)
   7. Steel Base        — solid dark steel, metallic=1.0
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Sci_Fi_Panel"
RESOLUTION = "1024"

# ── Colours (linear RGB) ──
STEEL_COLOR              = (0.04, 0.05, 0.06, 1.0)
PANEL_FACE_DARK          = (0.05, 0.06, 0.08, 1.0)
PANEL_FACE_LIGHT         = (0.08, 0.09, 0.11, 1.0)
PANEL_MORTAR_COLOR       = (0.005, 0.005, 0.008, 1.0)  # near-black recessed seam
PANEL_VARIATION_DARK     = (0.04, 0.05, 0.07, 1.0)
PANEL_VARIATION_LIGHT    = (0.10, 0.12, 0.15, 1.0)
PANEL_VARIATION_ALT      = (0.07, 0.07, 0.10, 1.0)   # subtle violet tint
EMISSIVE_COLOR           = (1.00, 0.30, 0.05, 1.0)   # bright orange
EMISSIVE_STRENGTH        = 2.5    # dim accent — was 6.0 (overwhelming)

# ── Layer opacities ──
PANEL_OPACITY            = 1.0
PANEL_VAR_OPACITY        = 0.50
SCRATCH_OPACITY          = 0.30
EMISSIVE_OPACITY         = 0.55   # let the dark panels read first — was 1.0
ROUGH_BOOST_OPACITY      = 0.5
COLOR_GRADE_OPACITY      = 0.3

# ── Brick params ──
# Same scale + offset/squash on the visible Panel Pattern AND the
# Emissive Strips so they align. Tweak these together.
#
# Goal is a FEW BIG sci-fi hull panels per face, not a uniform tile
# of small bricks. Lower scale = fewer / bigger bricks.
PANEL_SCALE              = 1.8    # ~3-5 panels per face on a unit Cube
BRICK_OFFSET             = 0.5
BRICK_OFFSET_FREQ        = 2
BRICK_SQUASH             = 2.4    # high so adjacent rows have very different widths
BRICK_SQUASH_FREQ        = 5

PANEL_MORTAR_SIZE        = 0.02   # thin seam — was 0.04 (too thick)
PANEL_MORTAR_SMOOTH      = 0.05

# Emissive strips — even thinner mortar so the glow is a hairline
# accent at the very centre of each seam, not a bright stripe.
EMISSIVE_MORTAR_SIZE     = 0.012
EMISSIVE_MORTAR_SMOOTH   = 0.02

# Emission shape: with the brick mortar mask, fac is 1 at bricks and
# 0 at mortar. The emission pipeline inverts to (1-fac), so mortar
# pixels arrive as 1.0 and brick pixels as 0.0 — the smoothstep
# threshold then decides where the on/off boundary sits.
# Higher threshold = thinner glow band centred on the seam.
EMISSIVE_THRESHOLD       = 0.7
EMISSIVE_FALLOFF         = 0.08
EMISSIVE_CONTRAST        = 0.55

# ── Surface scratch params ──
SCRATCH_SCALE            = 22.0

# ── Adjustment params ──
HUE_GRADE_HUE            = 0.51   # tiny cool tilt
HUE_GRADE_SAT            = 1.05
HUE_GRADE_VAL            = 1.00

ROUGH_BOOST_BRIGHT       = 0.05
ROUGH_BOOST_CONTRAST     = 0.30


# ─── HELPERS ──────────────────────────────────────────────────────────────────

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
    print(f"  + FILL        '{name}'  (out={output_channel})")
    _add_layer_common(bpy.context, "FILL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.fill_color = color
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    return layer


def _add_procedural(mat, name, proc_type, opacity=1.0, blend_mode="MIX",
                    output_channel="BASE_COLOR"):
    print(f"  + PROCEDURAL  '{name}'  ({proc_type}, out={output_channel})")
    _add_layer_common(bpy.context, "PROCEDURAL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.proc_type = proc_type
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    return layer


def _add_paint(mat, name, opacity=1.0, blend_mode="MIX",
               output_channel="BASE_COLOR"):
    print(f"  + PAINT       '{name}'  (out={output_channel})")
    _add_layer_common(bpy.context, "PAINT")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    return layer


def _add_adjustment(mat, name, adj_type, opacity=1.0,
                    output_channel="BASE_COLOR"):
    print(f"  + ADJUSTMENT  '{name}'  ({adj_type}, out={output_channel})")
    _add_layer_common(bpy.context, "ADJUSTMENT")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.adj_type = adj_type
    layer.opacity = opacity
    layer.output_channel = output_channel
    return layer


def _setup_brick(layer, mortar_size, mortar_smooth):
    """Apply the shared Brick parameters to a procedural layer."""
    layer.proc_scale = PANEL_SCALE
    layer.proc_brick_offset = BRICK_OFFSET
    layer.proc_brick_offset_freq = BRICK_OFFSET_FREQ
    layer.proc_brick_squash = BRICK_SQUASH
    layer.proc_brick_squash_freq = BRICK_SQUASH_FREQ
    layer.proc_brick_mortar_size = mortar_size
    layer.proc_brick_mortar_smooth = mortar_smooth


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_sci_fi_panel():
    obj = bpy.context.active_object
    if obj is None:
        raise RuntimeError("No active object. Select a mesh first.")
    if obj.type != 'MESH':
        raise RuntimeError(f"Active object '{obj.name}' is not a mesh.")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False

    _clear_layers(mat)

    # ── 7 (bottom): Steel base ─────────────────────────────────────────────
    l_steel = _add_fill(mat, "Steel Base", STEEL_COLOR,
                        opacity=1.0, output_channel="BASE_COLOR")
    l_steel.use_roughness = True
    l_steel.roughness_fill = 0.30
    l_steel.use_metallic = True
    l_steel.metallic_fill = 1.0

    # ── 6: Panel Pattern — Brick on Base Color ─────────────────────────────
    # Defines the rectangular panel grid via Brick. Color1/Color2 give a
    # subtle tonal alternation between adjacent rows / bricks; Mortar is
    # the darker recessed seam.
    l_panels = _add_procedural(mat, "Panel Pattern", "BRICK",
                                opacity=PANEL_OPACITY, blend_mode="MIX",
                                output_channel="BASE_COLOR")
    _setup_brick(l_panels, PANEL_MORTAR_SIZE, PANEL_MORTAR_SMOOTH)
    l_panels.proc_color1 = PANEL_FACE_DARK
    l_panels.proc_color2 = PANEL_FACE_LIGHT
    l_panels.use_proc_color3 = True
    l_panels.proc_color3 = PANEL_MORTAR_COLOR
    l_panels.proc_color3_position = 0.5

    # ── 5: Panel Variation — Voronoi F1 random per cell, OVERLAY ───────────
    # Voronoi cells don't strictly align with brick cells, but the
    # OVERLAY blend at moderate opacity gives each rough region a
    # subtly different tonal cast — reads as panel-to-panel variation
    # without locking to the exact brick grid.
    l_var = _add_procedural(mat, "Panel Variation", "VORONOI",
                             opacity=PANEL_VAR_OPACITY, blend_mode="OVERLAY",
                             output_channel="BASE_COLOR")
    l_var.proc_voronoi_feature = 'F1'
    l_var.proc_voronoi_distance = 'EUCLIDEAN'
    l_var.proc_scale = PANEL_SCALE * 0.7   # slightly larger cells than bricks
    l_var.proc_randomness = 1.0
    l_var.proc_voronoi_random_color = True
    l_var.proc_voronoi_random_seed = 4.2
    l_var.proc_color1 = PANEL_VARIATION_DARK
    l_var.proc_color2 = PANEL_VARIATION_LIGHT
    l_var.use_proc_color3 = True
    l_var.proc_color3 = PANEL_VARIATION_ALT
    l_var.proc_color3_position = 0.5

    # ── 4: Surface Wear — Voronoi DTE on Roughness ─────────────────────────
    l_wear = _add_procedural(mat, "Surface Wear", "VORONOI",
                              opacity=SCRATCH_OPACITY, blend_mode="ADD",
                              output_channel="ROUGHNESS")
    l_wear.proc_voronoi_feature = 'DISTANCE_TO_EDGE'
    l_wear.proc_voronoi_distance = 'EUCLIDEAN'
    l_wear.proc_scale = SCRATCH_SCALE
    l_wear.proc_randomness = 1.0
    l_wear.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_wear.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    l_wear.proc_contrast = 0.6

    # ── 3: Emissive Strips — Brick aligned with Panel Pattern ──────────────
    # Same scale + offset/squash as the visible Panel Pattern so the
    # mortar lines align exactly. output_channel=ROUGHNESS keeps Base
    # Color untouched; use_emission adds the orange glow on top.
    #
    # Now relies on the addon's brick mortar mask (commit immediately
    # before this preset rewrite): _build_proc_fac_node for BRICK uses
    # sentinel WHITE/BLACK colours and SeparateColor.R to give a clean
    # 1-at-bricks / 0-at-mortar mask. The emission pipeline inverts
    # that, so the orange glow correctly lands on the seams.
    l_emit = _add_procedural(mat, "Emissive Strips", "BRICK",
                              opacity=EMISSIVE_OPACITY, blend_mode="MIX",
                              output_channel="ROUGHNESS")
    _setup_brick(l_emit, EMISSIVE_MORTAR_SIZE, EMISSIVE_MORTAR_SMOOTH)
    # Colours are visually irrelevant for emission (the pipeline uses
    # the fac, not the Color output). The fac path uses sentinel
    # colours internally — our user-facing colours go to the
    # roughness contribution, kept all black so roughness barely
    # shifts.
    l_emit.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_emit.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_emit.use_proc_color3 = True
    l_emit.proc_color3 = (0.0, 0.0, 0.0, 1.0)
    l_emit.proc_color3_position = 0.5
    l_emit.proc_contrast = EMISSIVE_CONTRAST
    # Emission contribution
    l_emit.use_emission = True
    l_emit.emission_color = EMISSIVE_COLOR
    l_emit.emission_strength = EMISSIVE_STRENGTH
    l_emit.proc_emission_threshold = EMISSIVE_THRESHOLD
    l_emit.proc_emission_falloff = EMISSIVE_FALLOFF

    # ── 2: Roughness Boost — BRIGHT_CONTRAST on Roughness ──────────────────
    l_rough_boost = _add_adjustment(mat, "Roughness Boost", "BRIGHT_CONTRAST",
                                    opacity=ROUGH_BOOST_OPACITY,
                                    output_channel="ROUGHNESS")
    l_rough_boost.adj_brightness = ROUGH_BOOST_BRIGHT
    l_rough_boost.adj_contrast   = ROUGH_BOOST_CONTRAST

    # ── 1: Color Grade — HUE_SAT on Base Color ─────────────────────────────
    l_grade = _add_adjustment(mat, "Color Grade", "HUE_SAT",
                              opacity=COLOR_GRADE_OPACITY,
                              output_channel="BASE_COLOR")
    l_grade.adj_hue = HUE_GRADE_HUE
    l_grade.adj_saturation = HUE_GRADE_SAT
    l_grade.adj_value = HUE_GRADE_VAL

    # ── 0 (top): Hand Details — empty PAINT ────────────────────────────────
    l_hand = _add_paint(mat, "Hand Details",
                        opacity=1.0, output_channel="BASE_COLOR")

    # ── Finalize ──
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"\n[TLM] Sci-Fi Panel built on '{obj.name}'.")
    print(f"      Material: {mat.name}")
    print(f"      Layers: {len(tlm.layers)} (index 0 = top of UIList = "
          f"composited LAST)")
    for i, l in enumerate(tlm.layers):
        out = getattr(l, 'output_channel', '-')
        emit = " [EMIT]" if getattr(l, 'use_emission', False) else ""
        print(f"        [{i}] {l.layer_type:10s} {l.name:20s} "
              f"blend={l.blend_mode:8s} opacity={l.opacity:.2f}  "
              f"→ {out}{emit}")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    build_sci_fi_panel()
