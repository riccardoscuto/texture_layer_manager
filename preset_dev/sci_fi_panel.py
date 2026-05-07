"""
TLM Flagship Preset — Sci-Fi Panel (dark hull with emissive seams)
====================================================================

Recreates the centre material from the reference grid: a dark
metallic hull broken into irregular armoured cells, with bright
orange emissive light strips running along the cell joints.

How to use:
  1. Open Blender 5.0 with the TLM addon enabled.
  2. Select a mesh — a Cube reads the panel pattern crisply, a
     Sphere or Suzanne also work.
  3. Open the Scripting editor → New → paste this file → Run.
  4. Build a material called "TLM_Sci_Fi_Panel" on the active object.
  5. View in Material Preview / Rendered. Cycles emission is brighter
     than Eevee — both work.

Why HEX_GRID + Voronoi DTE instead of Brick:
  The first iteration of this preset used a Brick procedural for the
  panel grid and another Brick for the emissive seams. That broke
  because Blender's Brick `Fac` output distinguishes Color1 vs Color2
  (i.e., alternating bricks), NOT brick-vs-mortar. So the emission
  pipeline (which inverts the fac) put glow on alternating bricks
  instead of the seams — visible as orange stripes covering full
  bricks rather than thin lines along the joints.

  HEX_GRID is purpose-built for cell-with-bordered-line patterns: its
  internal Voronoi(DISTANCE_TO_EDGE) + Map Range gives a clean fac of
  1 at edges, 0 at cell interior, with proc_hex_edge_width controlling
  line thickness. For the emissive layer we use raw Voronoi
  DISTANCE_TO_EDGE at the same proc_scale and proc_randomness, so the
  cell boundaries align — and the emission pipeline's
  invert + smoothstep then carves a clean thin glow at the joints.

What this preset showcases:
  - HEX_GRID procedural for the panel structure
  - Voronoi F1 with random_color for per-panel tonal variation
  - Voronoi DISTANCE_TO_EDGE on ROUGHNESS for surface scuffing
  - PROCEDURAL routed to EMISSION via use_emission, threshold-tuned
    so the orange glow lives on the joints only
  - Cumulative routing: the emissive layer's primary target is
    ROUGHNESS (so the joints are slightly more polished), and
    use_emission adds the orange glow on top — Base Color is left
    alone
  - BRIGHT_CONTRAST adjustment routed to ROUGHNESS
  - Empty PAINT layer at the top for hand-added LEDs / decals

Layer stack (top to bottom — top is rendered last):
   0. Hand Details      — empty PAINT
   1. Color Grade       — HUE_SAT on Base Color
   2. Roughness Boost   — BRIGHT_CONTRAST on Roughness
   3. Emissive Joints   — Voronoi DTE, use_emission, glow at joints
   4. Surface Wear      — Voronoi DTE on Roughness, fine scuffing
   5. Panel Variation   — Voronoi F1 random per cell, OVERLAY on base
   6. Panel Pattern     — HEX_GRID on Base Color (cells + joints)
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
PANEL_FACE_COLOR         = (0.06, 0.07, 0.09, 1.0)   # slightly bluer than steel
PANEL_JOINT_COLOR        = (0.005, 0.005, 0.008, 1.0)  # near-black recessed seam
PANEL_VARIATION_DARK     = (0.04, 0.05, 0.07, 1.0)
PANEL_VARIATION_LIGHT    = (0.10, 0.12, 0.16, 1.0)
PANEL_VARIATION_ALT      = (0.07, 0.07, 0.11, 1.0)   # subtle violet tint
EMISSIVE_COLOR           = (1.00, 0.30, 0.05, 1.0)   # bright orange
EMISSIVE_STRENGTH        = 6.0

# ── Layer opacities ──
PANEL_OPACITY            = 1.0    # let the HEX_GRID fully replace base color
PANEL_VAR_OPACITY        = 0.55
SCRATCH_OPACITY          = 0.30
EMISSIVE_OPACITY         = 1.0
ROUGH_BOOST_OPACITY      = 0.5
COLOR_GRADE_OPACITY      = 0.3

# ── Procedural scales ──
# Panel scale and Emissive scale must match for joints to align.
PANEL_SCALE              = 5.0
PANEL_RANDOMNESS         = 1.0    # 0 = honeycomb, 1 = irregular Voronoi cells
SCRATCH_SCALE            = 22.0

# Joint thickness on the visible panel pattern (HEX_GRID)
HEX_EDGE_WIDTH           = 0.05

# Emission shape: threshold close to 1 → only the very-near-edge
# fragments glow. With Voronoi DTE the raw fac is the distance; the
# emission build path inverts this (1 - distance), so high threshold
# keeps the glow on a thin band around the cell edge.
EMISSIVE_THRESHOLD       = 0.78
EMISSIVE_FALLOFF         = 0.08
EMISSIVE_CONTRAST        = 0.7    # sharpens the glow band edge

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

    # ── 6: Panel Pattern — HEX_GRID on Base Color ──────────────────────────
    # Defines the cell grid. HEX_GRID is a Voronoi(DTE) thresholded by a
    # Map Range, so proc_hex_edge_width directly controls the joint
    # thickness. Color1 = panel face, Color2 = darker joint.
    l_panels = _add_procedural(mat, "Panel Pattern", "HEX_GRID",
                                opacity=PANEL_OPACITY, blend_mode="MIX",
                                output_channel="BASE_COLOR")
    l_panels.proc_scale = PANEL_SCALE
    l_panels.proc_randomness = PANEL_RANDOMNESS
    l_panels.proc_hex_edge_width = HEX_EDGE_WIDTH
    # HEX_GRID's Mix-topology uses Color1 for the cell, Color2 for the line.
    l_panels.proc_color1 = PANEL_FACE_COLOR
    l_panels.proc_color2 = PANEL_JOINT_COLOR

    # ── 5: Panel Variation — Voronoi F1 random per cell, OVERLAY ───────────
    # Same proc_scale + proc_randomness as the Panel Pattern so cells line
    # up. random_color = True gives each cell a random tonal shift inside
    # the [Color1..Color2..Color3] palette.
    l_var = _add_procedural(mat, "Panel Variation", "VORONOI",
                             opacity=PANEL_VAR_OPACITY, blend_mode="OVERLAY",
                             output_channel="BASE_COLOR")
    l_var.proc_voronoi_feature = 'F1'
    l_var.proc_voronoi_distance = 'EUCLIDEAN'
    l_var.proc_scale = PANEL_SCALE
    l_var.proc_randomness = PANEL_RANDOMNESS
    l_var.proc_voronoi_random_color = True
    l_var.proc_voronoi_random_seed = 4.2
    l_var.proc_color1 = PANEL_VARIATION_DARK
    l_var.proc_color2 = PANEL_VARIATION_LIGHT
    l_var.use_proc_color3 = True
    l_var.proc_color3 = PANEL_VARIATION_ALT
    l_var.proc_color3_position = 0.5

    # ── 4: Surface Wear — Voronoi DTE on Roughness ─────────────────────────
    # Smaller scale than the panels so this reads as fine scuffing /
    # micro-scratches, not as a second tier of cells.
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

    # ── 3: Emissive Joints — Voronoi DTE driving emission ──────────────────
    # SAME proc_scale + proc_randomness as the Panel Pattern so the glow
    # falls exactly on the visible joints. The emission build path
    # (_build_proc_fac_node + invert + smoothstep) takes the raw distance
    # fac (0 at edges, ~0.5 at cell centres), inverts it, then carves a
    # thin glowing band via the threshold/falloff.
    #
    # output_channel = ROUGHNESS keeps Base Color untouched. The
    # Voronoi DTE on roughness barely changes the channel because the
    # adjusted fac is mostly 0.
    l_emit = _add_procedural(mat, "Emissive Joints", "VORONOI",
                              opacity=EMISSIVE_OPACITY, blend_mode="MIX",
                              output_channel="ROUGHNESS")
    l_emit.proc_voronoi_feature = 'DISTANCE_TO_EDGE'
    l_emit.proc_voronoi_distance = 'EUCLIDEAN'
    l_emit.proc_scale = PANEL_SCALE
    l_emit.proc_randomness = PANEL_RANDOMNESS
    # Colours don't visually affect emission (the emission pipeline uses
    # the FAC, not the Color output), but they DO drive the roughness
    # contribution. Keep both at 0 so roughness barely shifts.
    l_emit.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_emit.proc_color2 = (0.0, 0.0, 0.0, 1.0)
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
