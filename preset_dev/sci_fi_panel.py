"""
TLM Flagship Preset — Sci-Fi Panel (dark metal hull with emissive seams)
=========================================================================

Recreates the centre material from the reference grid: a dark
metallic hull broken up into rectangular panels, with bright orange
emissive light strips running along the panel seams.

How to use:
  1. Open Blender 5.0 with the TLM addon enabled.
  2. Select a mesh — a Cube reads the panel pattern crisply, but
     Suzanne and a Sphere also work.
  3. Open the Scripting editor → New → paste this file → Run.
  4. Build a material called "TLM_Sci_Fi_Panel" on the active object.
  5. View in Material Preview / Rendered. Cycles emission is brighter
     than Eevee — both work.

What this preset showcases:
  - The new Brick procedural with offset / squash / mortar
  - PROCEDURAL layer routed to EMISSION via use_emission (the seam
    glow comes from a Brick at the same scale as the panel pattern;
    the addon's emission pipeline inverts the Brick's fac so the
    glow lands on the MORTAR — i.e. the seams — not the faces)
  - Cumulative routing: the emissive Brick's primary target is
    ROUGHNESS (so the seams are slightly more polished than the
    panel face), and use_emission adds the orange glow on top
  - Voronoi DISTANCE_TO_EDGE on Roughness for surface scuffing
  - Voronoi F1 with random_color for per-panel tonal variation
  - BRIGHT_CONTRAST adjustment routed to ROUGHNESS for crunch
  - Empty PAINT layer at the top so you can add stickers / decals /
    glow text by hand without scaffolding

Layer stack (top to bottom — top is rendered last):
   0. Hand Details      — empty PAINT
   1. Color Grade       — HUE_SAT on Base Color
   2. Roughness Boost   — BRIGHT_CONTRAST on Roughness
   3. Emissive Strips   — Brick aligned with Panel Pattern, use_emission
                          drives orange glow at the seams
   4. Surface Wear      — Voronoi DISTANCE_TO_EDGE on Roughness
   5. Panel Variation   — Voronoi F1 random per cell, OVERLAY on base
   6. Panel Pattern     — Brick on Base Color (defines the rectangles)
   7. Steel Base        — solid dark steel, metallic=1.0

Tweak the CONSTANTS below to change the look.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Sci_Fi_Panel"
RESOLUTION = "1024"

# ── Colours (linear RGB) ──
STEEL_COLOR              = (0.04, 0.05, 0.06, 1.0)   # near-black, cool
PANEL_FACE_DARK          = (0.04, 0.05, 0.06, 1.0)
PANEL_FACE_LIGHT         = (0.08, 0.09, 0.10, 1.0)
PANEL_MORTAR_COLOR       = (0.01, 0.01, 0.01, 1.0)   # almost black seams
PANEL_VARIATION_COLOR_1  = (0.05, 0.06, 0.07, 1.0)
PANEL_VARIATION_COLOR_2  = (0.10, 0.12, 0.14, 1.0)
PANEL_VARIATION_COLOR_3  = (0.07, 0.07, 0.10, 1.0)   # slightly purple cell
EMISSIVE_COLOR           = (1.00, 0.30, 0.05, 1.0)   # bright orange
EMISSIVE_STRENGTH        = 6.0

# ── Layer opacities ──
PANEL_OPACITY            = 0.95
PANEL_VAR_OPACITY        = 0.45
SCRATCH_OPACITY          = 0.30
EMISSIVE_OPACITY         = 0.85
ROUGH_BOOST_OPACITY      = 0.5
COLOR_GRADE_OPACITY      = 0.3

# ── Procedural scales (higher = smaller features) ──
PANEL_SCALE              = 4.0    # bigger = more panels per unit
PANEL_VARIATION_SCALE    = 5.5
SCRATCH_SCALE            = 22.0

# Brick panel parameters — kept consistent between the visible Panel
# Pattern (Base Color) and the Emissive Strips so they line up.
PANEL_BRICK_OFFSET       = 0.5
PANEL_BRICK_OFFSET_FREQ  = 2
PANEL_BRICK_SQUASH       = 1.6
PANEL_BRICK_SQUASH_FREQ  = 3

PANEL_MORTAR_SIZE        = 0.04   # visible seam gap
PANEL_MORTAR_SMOOTH      = 0.05

# Emissive Brick mortar — slightly thinner than the Panel Pattern's
# seam so the glow reads as a bright thin line along the dark seam
# rather than fully filling it.
EMISSIVE_MORTAR_SIZE     = 0.025
EMISSIVE_MORTAR_SMOOTH   = 0.02

# Emission shape: threshold pushes the soft falloff toward the
# centre of the seam (sharper, brighter strip); falloff widens it.
EMISSIVE_THRESHOLD       = 0.0
EMISSIVE_FALLOFF         = 0.05

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

    # ── 6: Panel Pattern — Brick on Base Color ─────────────────────────────
    # Defines the rectangular panel grid. Color1 / Color2 give a subtle
    # tonal shift between alternating bricks; mortar is darker still
    # so the seam reads as a recessed gap.
    l_panels = _add_procedural(mat, "Panel Pattern", "BRICK",
                                opacity=PANEL_OPACITY, blend_mode="MIX",
                                output_channel="BASE_COLOR")
    l_panels.proc_scale = PANEL_SCALE
    l_panels.proc_color1 = PANEL_FACE_DARK
    l_panels.proc_color2 = PANEL_FACE_LIGHT
    l_panels.use_proc_color3 = True
    l_panels.proc_color3 = PANEL_MORTAR_COLOR
    l_panels.proc_color3_position = 0.5
    l_panels.proc_brick_offset = PANEL_BRICK_OFFSET
    l_panels.proc_brick_offset_freq = PANEL_BRICK_OFFSET_FREQ
    l_panels.proc_brick_squash = PANEL_BRICK_SQUASH
    l_panels.proc_brick_squash_freq = PANEL_BRICK_SQUASH_FREQ
    l_panels.proc_brick_mortar_size = PANEL_MORTAR_SIZE
    l_panels.proc_brick_mortar_smooth = PANEL_MORTAR_SMOOTH

    # ── 5: Panel Variation — Voronoi F1 random per cell, OVERLAY ───────────
    # Slight per-panel tonal shift so adjacent panels read as
    # distinct rather than as one tiled material.
    l_var = _add_procedural(mat, "Panel Variation", "VORONOI",
                             opacity=PANEL_VAR_OPACITY, blend_mode="OVERLAY",
                             output_channel="BASE_COLOR")
    l_var.proc_voronoi_feature = 'F1'
    l_var.proc_voronoi_distance = 'EUCLIDEAN'
    l_var.proc_scale = PANEL_VARIATION_SCALE
    l_var.proc_randomness = 1.0
    l_var.proc_voronoi_random_color = True
    l_var.proc_voronoi_random_seed = 4.2
    l_var.proc_color1 = PANEL_VARIATION_COLOR_1
    l_var.proc_color2 = PANEL_VARIATION_COLOR_2
    l_var.use_proc_color3 = True
    l_var.proc_color3 = PANEL_VARIATION_COLOR_3
    l_var.proc_color3_position = 0.5

    # ── 4: Surface Wear — Voronoi DISTANCE_TO_EDGE on Roughness ────────────
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

    # ── 3: Emissive Strips — Brick at panel scale, use_emission ────────────
    # The trick: same Brick parameters as the Panel Pattern so the
    # bricks line up. output_channel = ROUGHNESS (cosmetic — slightly
    # smoother seams) and use_emission = True drives the actual
    # orange glow. The emission build path (_build_proc_fac_node →
    # invert → smoothstep) automatically inverts Brick's fac, so the
    # glow lands on the MORTAR (seams), not the faces.
    l_emit = _add_procedural(mat, "Emissive Strips", "BRICK",
                              opacity=EMISSIVE_OPACITY, blend_mode="MIX",
                              output_channel="ROUGHNESS")
    l_emit.proc_scale = PANEL_SCALE
    l_emit.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_emit.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_emit.use_proc_color3 = True
    l_emit.proc_color3 = (0.0, 0.0, 0.0, 1.0)
    l_emit.proc_color3_position = 0.5
    l_emit.proc_brick_offset = PANEL_BRICK_OFFSET
    l_emit.proc_brick_offset_freq = PANEL_BRICK_OFFSET_FREQ
    l_emit.proc_brick_squash = PANEL_BRICK_SQUASH
    l_emit.proc_brick_squash_freq = PANEL_BRICK_SQUASH_FREQ
    l_emit.proc_brick_mortar_size = EMISSIVE_MORTAR_SIZE
    l_emit.proc_brick_mortar_smooth = EMISSIVE_MORTAR_SMOOTH
    # Emission contribution
    l_emit.use_emission = True
    l_emit.emission_color = EMISSIVE_COLOR
    l_emit.emission_strength = EMISSIVE_STRENGTH
    l_emit.proc_emission_threshold = EMISSIVE_THRESHOLD
    l_emit.proc_emission_falloff = EMISSIVE_FALLOFF

    # ── 2: Roughness Boost — ADJUSTMENT BRIGHT_CONTRAST on Roughness ───────
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
    # Add LED dots, decals, warning text, ID numbers etc. by hand.
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
