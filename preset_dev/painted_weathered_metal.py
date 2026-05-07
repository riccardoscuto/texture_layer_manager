"""
TLM Flagship Preset — Painted Weathered Metal (industrial / vehicle / prop)
============================================================================

How to use:
  1. Open Blender 5.0 with the TLM addon enabled.
  2. Select a mesh with a UV map (a Suzanne / Cube / Cylinder is fine).
  3. Open the Scripting editor → New → paste this file → Run.
  4. The script builds a material called "TLM_Painted_Weathered_Metal" on
     the active object and populates it with a multi-layer TLM stack.
  5. Look at the viewport in Material Preview or Rendered.
  6. Tweak the CONSTANTS at the top to iterate.

What this preset showcases:
  - Multi-channel routing (output_channel + use_X cumulative toggles):
      * Paint Coat drives base_color + roughness + metallic (a single
        FILL with three contributions)
      * Surface Scratches go to ROUGHNESS only
      * Paint Chips drive base_color + roughness + metallic
        (the bare-metal look exposed under chipped paint)
      * Rust Streaks go to BASE_COLOR only with MULTIPLY
  - Smart mask generators: EDGE_WEAR for paint chips, DIRT for rust
    pooling
  - Branching (per-channel blend mode override): rust MULTIPLY-darkens
    base_color while staying MIX on every other channel
  - The ADJUSTMENT-on-scalar feature: BRIGHT_CONTRAST on Roughness for
    extra micro-variation contrast
  - HUE_SAT adjustment on base_color for final colour grading
  - PAINT layer at the top for hand-tweakable damage / details

Layer stack (top to bottom — top is rendered last):
   0. Hand Details      — empty PAINT for user scribbles
   1. Color Grade       — HUE_SAT on Base Color
   2. Roughness Boost   — BRIGHT_CONTRAST on Roughness (scalar adjust)
   3. Rust Streaks      — Marble, MULTIPLY into base, DIRT mask
   4. Paint Chips       — bare-metal FILL, EDGE_WEAR mask (NOT inverted)
                          → metal shows ONLY at edges (where mask is
                          bright). Paint underneath stays everywhere
                          else. Replaces the fragile "invert EDGE_WEAR
                          on the paint" approach which collapsed on
                          curvy meshes like Suzanne.
   5. Paint Texture     — Noise routed to Roughness (paint micro-var)
   6. Paint Coat        — solid colour, FULL coverage (no mask)
   7. Surface Scratches — Voronoi DISTANCE_TO_EDGE on Roughness only
   8. Steel Base        — solid dark steel, metallic=1.0

Tweak the CONSTANTS below to change colour, intensity and feel.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────
# Edit these to iterate on the look.

MATERIAL_NAME = "TLM_Painted_Weathered_Metal"
RESOLUTION = "1024"  # "512" / "1024" / "2048" / "4096"

# ── Colours (linear RGB, alpha always 1.0) ──
STEEL_COLOR             = (0.05, 0.06, 0.07, 1.0)   # dark, slightly bluish
PAINT_COLOR             = (0.50, 0.10, 0.08, 1.0)   # oxblood red
RUST_COLOR_DARK         = (0.18, 0.07, 0.03, 1.0)
RUST_COLOR_LIGHT        = (0.45, 0.20, 0.06, 1.0)
BARE_METAL_COLOR        = (0.35, 0.34, 0.33, 1.0)   # lighter, slightly warm
PAINT_NOISE_DARK        = (0.05, 0.05, 0.05, 1.0)
PAINT_NOISE_LIGHT       = (0.55, 0.55, 0.55, 1.0)

# ── Layer opacities ──
PAINT_OPACITY           = 1.0
PAINT_NOISE_OPACITY     = 0.30
SCRATCH_OPACITY         = 0.55
PAINT_CHIPS_OPACITY     = 0.95   # how strongly chips replace paint at edges
RUST_OPACITY            = 0.55   # gentler than before — was 0.85
ROUGH_BOOST_OPACITY     = 0.6
COLOR_GRADE_OPACITY     = 0.4

# ── Procedural scales (higher = smaller features) ──
SCRATCH_VORONOI_SCALE   = 25.0
PAINT_NOISE_SCALE       = 18.0
RUST_MARBLE_SCALE       = 4.0

# ── Smart mask params ──
# Paint Chips uses EDGE_WEAR with MODERATE settings — strong intensity +
# noisy breakup on a curvy mesh (e.g. Suzanne) used to collapse the
# inverted version of this mask, hiding the paint everywhere. Now the
# mask drives the CHIPS layer directly (not inverted), so we want it
# selective: only the genuinely sharp edges should chip.
PAINT_CHIPS_INTENSITY   = 0.8
PAINT_CHIPS_BREAKUP     = 0.45
PAINT_CHIPS_BREAKUP_S   = 22.0
PAINT_CHIPS_SHARPNESS   = 0.75   # higher = tighter to the actual edges

DIRT_INTENSITY          = 0.9
DIRT_BREAKUP            = 0.55
DIRT_BREAKUP_S          = 12.0
DIRT_SHARPNESS          = 0.55
DIRT_AO_DIST            = 1.0

# ── Adjustment params ──
HUE_GRADE_HUE           = 0.50  # 0.5 = no rotation
HUE_GRADE_SAT           = 1.10  # slight saturation boost
HUE_GRADE_VAL           = 0.95  # tiny darkening for grime feel

ROUGH_BOOST_BRIGHT      =  0.10
ROUGH_BOOST_CONTRAST    =  0.35


# ─── HELPERS (mirrored from weathered_bronze.py for consistency) ──────────────

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


# ─── BUILD THE PRESET ─────────────────────────────────────────────────────────

def build_painted_weathered_metal():
    obj = bpy.context.active_object
    if obj is None:
        raise RuntimeError("No active object. Select a mesh first.")
    if obj.type != 'MESH':
        raise RuntimeError(f"Active object '{obj.name}' is not a mesh.")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False  # build silently, rebuild once at end

    _clear_layers(mat)

    # _add_layer_common places the new layer ABOVE the active one and the
    # composite reverses tlm.layers when iterating, so the FIRST add is
    # composited FIRST (= bottom of the stack). We build bottom → top.

    # ── 8 (bottom): Steel base ─────────────────────────────────────────────
    l_steel = _add_fill(mat, "Steel Base", STEEL_COLOR,
                        opacity=1.0, output_channel="BASE_COLOR")
    l_steel.use_roughness = True
    l_steel.roughness_fill = 0.45
    l_steel.use_metallic = True
    l_steel.metallic_fill = 1.0

    # ── 7: Surface scratches — Voronoi DISTANCE_TO_EDGE on Roughness ───────
    # The Voronoi cell edges become brighter (= rougher) lines, so the
    # surface reads as scuffed/wiped under the paint and at the chips.
    l_scratch = _add_procedural(mat, "Surface Scratches", "VORONOI",
                                opacity=SCRATCH_OPACITY, blend_mode="ADD",
                                output_channel="ROUGHNESS")
    l_scratch.proc_voronoi_feature  = 'DISTANCE_TO_EDGE'
    l_scratch.proc_voronoi_distance = 'EUCLIDEAN'
    l_scratch.proc_scale     = SCRATCH_VORONOI_SCALE
    l_scratch.proc_randomness = 1.0
    l_scratch.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_scratch.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    l_scratch.proc_contrast = 0.7

    # ── 6: Paint coat — solid colour, NO MASK (full coverage) ──────────────
    # Drives base_color + roughness + metallic in one layer (cumulative
    # routing via the use_<channel> toggles). No mask means the paint
    # covers everything; the chip effect is added by the layer ABOVE,
    # not subtracted from this one — robust on any geometry, including
    # high-curvature meshes where an inverted EDGE_WEAR mask collapses.
    l_paint = _add_fill(mat, "Paint Coat", PAINT_COLOR,
                        opacity=PAINT_OPACITY, output_channel="BASE_COLOR")
    l_paint.use_roughness = True
    l_paint.roughness_fill = 0.55
    l_paint.use_metallic = True
    l_paint.metallic_fill = 0.0   # paint is non-metallic

    # ── 5: Paint texture — Noise routed to Roughness ───────────────────────
    # High-frequency variation on the paint's roughness so it doesn't
    # read like a flat plastic film.
    l_paint_tex = _add_procedural(mat, "Paint Texture", "NOISE",
                                   opacity=PAINT_NOISE_OPACITY,
                                   blend_mode="ADD",
                                   output_channel="ROUGHNESS")
    l_paint_tex.proc_scale = PAINT_NOISE_SCALE
    l_paint_tex.proc_detail = 4.0
    l_paint_tex.proc_roughness_proc = 0.6
    l_paint_tex.proc_color1 = PAINT_NOISE_DARK
    l_paint_tex.proc_color2 = PAINT_NOISE_LIGHT
    l_paint_tex.proc_contrast = 0.4

    # ── 4: Paint chips — bare metal exposed at edges ───────────────────────
    # FILL with the bare-metal colour, mask EDGE_WEAR (NOT inverted) so
    # the chips show ONLY where the EDGE_WEAR generator says "edge". Drives
    # base_color (the bare metal colour), roughness (polished, lower than
    # paint), and metallic (1.0, since bare steel under the paint).
    l_chips = _add_fill(mat, "Paint Chips", BARE_METAL_COLOR,
                        opacity=PAINT_CHIPS_OPACITY,
                        output_channel="BASE_COLOR")
    l_chips.use_roughness = True
    l_chips.roughness_fill = 0.20    # exposed metal is more polished than paint
    l_chips.use_metallic = True
    l_chips.metallic_fill = 1.0
    l_chips.use_mask = True
    l_chips.mask_source = 'EDGE_WEAR'
    l_chips.mask_gen_intensity = PAINT_CHIPS_INTENSITY
    l_chips.mask_gen_breakup = PAINT_CHIPS_BREAKUP
    l_chips.mask_gen_breakup_scale = PAINT_CHIPS_BREAKUP_S
    l_chips.mask_gen_sharpness = PAINT_CHIPS_SHARPNESS
    # mask_invert intentionally False — bright in the mask = chip is here.
    l_chips.mask_contrast = 0.65

    # ── 3: Rust streaks — Marble, MULTIPLY into base, DIRT mask ────────────
    # The DIRT mask makes rust pool in cavities. MULTIPLY blend means the
    # rust tints whatever is below (paint or chip) rather than fully
    # replacing it — much more like real corrosion bleed-through.
    l_rust = _add_procedural(mat, "Rust Streaks", "MARBLE",
                              opacity=RUST_OPACITY, blend_mode="MIX",
                              output_channel="BASE_COLOR")
    l_rust.proc_marble_wave_type = 'BANDS'
    l_rust.proc_scale = RUST_MARBLE_SCALE
    l_rust.proc_detail = 8.0
    l_rust.proc_roughness_proc = 0.7
    l_rust.proc_marble_distortion = 6.0
    l_rust.proc_color1 = RUST_COLOR_DARK
    l_rust.proc_color2 = RUST_COLOR_LIGHT
    l_rust.proc_contrast = 0.65
    # Branching: tint base_color via MULTIPLY (rust is a stain, not an
    # opaque overlay); the layer doesn't contribute to other channels
    # because output_channel pins it to BASE_COLOR only.
    l_rust.blend_mode_base_color = 'MULTIPLY'
    l_rust.use_mask = True
    l_rust.mask_source = 'DIRT'
    l_rust.mask_gen_intensity = DIRT_INTENSITY
    l_rust.mask_gen_breakup = DIRT_BREAKUP
    l_rust.mask_gen_breakup_scale = DIRT_BREAKUP_S
    l_rust.mask_gen_sharpness = DIRT_SHARPNESS
    l_rust.mask_ao_distance = DIRT_AO_DIST
    l_rust.mask_contrast = 0.55

    # ── 2: Roughness boost — ADJUSTMENT routed to Roughness ────────────────
    # BRIGHT_CONTRAST on the assembled roughness map adds contrast so the
    # wet/dry split is more readable. Demonstrates the
    # adjustment-on-scalar-channel feature shipped in commit 059ec9b.
    l_rough_boost = _add_adjustment(mat, "Roughness Boost", "BRIGHT_CONTRAST",
                                    opacity=ROUGH_BOOST_OPACITY,
                                    output_channel="ROUGHNESS")
    l_rough_boost.adj_brightness = ROUGH_BOOST_BRIGHT
    l_rough_boost.adj_contrast   = ROUGH_BOOST_CONTRAST

    # ── 1: Color grade — HUE_SAT on Base Color ─────────────────────────────
    # Final colour tweak: slight saturation bump and a tiny value drop
    # for a grimier feel. opacity is the adjustment STRENGTH thanks to
    # the wrap-mix introduced in commit ac6868e.
    l_grade = _add_adjustment(mat, "Color Grade", "HUE_SAT",
                              opacity=COLOR_GRADE_OPACITY,
                              output_channel="BASE_COLOR")
    l_grade.adj_hue = HUE_GRADE_HUE
    l_grade.adj_saturation = HUE_GRADE_SAT
    l_grade.adj_value = HUE_GRADE_VAL

    # ── 0 (top): Hand details — empty PAINT layer ──────────────────────────
    # The user can scribble extra damage / scratches / stickers here.
    l_hand = _add_paint(mat, "Hand Details",
                        opacity=1.0, output_channel="BASE_COLOR")

    # ── Finalize ──
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"\n[TLM] Painted Weathered Metal built on '{obj.name}'.")
    print(f"      Material: {mat.name}")
    print(f"      Layers: {len(tlm.layers)} (index 0 = top of UIList = "
          f"composited LAST)")
    for i, l in enumerate(tlm.layers):
        out = getattr(l, 'output_channel', '-')
        print(f"        [{i}] {l.layer_type:10s} {l.name:20s} "
              f"blend={l.blend_mode:8s} opacity={l.opacity:.2f}  "
              f"→ {out}")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    build_painted_weathered_metal()
