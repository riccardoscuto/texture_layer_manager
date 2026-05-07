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
  - Multi-channel routing (output_channel on every layer):
      * paint goes to base_color + roughness
      * surface scratches go to roughness only
      * rust streaks go to base_color only
  - Smart mask generators (EDGE_WEAR for paint chips, DIRT for rust pooling,
    CURVATURE_SMART for metallic patches showing through)
  - Branching (per-channel blend mode override): rust MULTIPLY-darkens
    base_color but leaves roughness on MIX
  - The new ADJUSTMENT-on-scalar feature: a BRIGHT_CONTRAST adjustment
    routed to the Roughness channel boosts the contrast of the roughness
    map for richer micro-variation
  - HUE_SAT adjustment on base_color for final colour grading
  - PAINT layer at the top for hand-tweakable damage / details
  - Dedicated Magic distortion (proc_magic_distortion) on the rust
  - Output-channel routing demonstrating the cumulative model

Layer stack (top to bottom — top is rendered last / painted on top):
   0. Hand Details      — empty PAINT layer for the user to scribble damage
   1. Color Grade       — HUE_SAT on Base Color (final tweak)
   2. Roughness Boost   — BRIGHT_CONTRAST on Roughness (NEW: scalar adjust)
   3. Metallic Patches  — Noise where the paint has chipped to bare metal
   4. Rust Streaks      — Marble veins, MULTIPLY into base, DIRT mask
   5. Paint Texture     — Noise routed to roughness (paint micro-variation)
   6. Paint Coat        — solid colour over the metal, EDGE_WEAR mask
   7. Surface Scratches — Voronoi DISTANCE_TO_EDGE on Roughness only
   8. Steel Base        — solid dark steel, metallic=1.0

Tweak the CONSTANTS below to change the colour, intensity and feel.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────
# Edit these to iterate on the look.

MATERIAL_NAME = "TLM_Painted_Weathered_Metal"
RESOLUTION = "1024"  # "512" / "1024" / "2048" / "4096"

# ── Colours (linear RGB, alpha always 1.0) ──
# Steel base: dark, slightly bluish.
STEEL_COLOR             = (0.05, 0.06, 0.07, 1.0)
# Paint: pick your poison. The default is a rusty-red / oxblood that reads
# well against the steel and the rust streaks.
PAINT_COLOR             = (0.50, 0.10, 0.08, 1.0)   # oxblood red
# Rust palette: orange-brown, slightly varying.
RUST_COLOR_DARK         = (0.18, 0.07, 0.03, 1.0)
RUST_COLOR_LIGHT        = (0.45, 0.20, 0.06, 1.0)
# Bare metal exposed under chipped paint: lighter than steel, polished.
BARE_METAL_COLOR        = (0.22, 0.22, 0.23, 1.0)
# Paint micro-variation (light/dark patches inside the paint coat).
PAINT_NOISE_DARK        = (0.05, 0.05, 0.05, 1.0)
PAINT_NOISE_LIGHT       = (0.55, 0.55, 0.55, 1.0)

# ── Layer opacities ──
PAINT_OPACITY           = 0.95
PAINT_NOISE_OPACITY     = 0.30
SCRATCH_OPACITY         = 0.55
RUST_OPACITY            = 0.85
METAL_PATCHES_OPACITY   = 0.80
ROUGH_BOOST_OPACITY     = 0.6
COLOR_GRADE_OPACITY     = 0.4

# ── Procedural scales (higher = smaller features) ──
SCRATCH_VORONOI_SCALE   = 25.0
PAINT_NOISE_SCALE       = 18.0
RUST_MARBLE_SCALE       = 4.0
METAL_PATCH_SCALE       = 8.0

# ── Smart mask params ──
EDGE_WEAR_INTENSITY     = 1.4
EDGE_WEAR_BREAKUP       = 0.55
EDGE_WEAR_BREAKUP_S     = 18.0
EDGE_WEAR_SHARPNESS     = 0.7

DIRT_INTENSITY          = 1.2
DIRT_BREAKUP            = 0.6
DIRT_BREAKUP_S          = 12.0
DIRT_SHARPNESS          = 0.5
DIRT_AO_DIST            = 1.0

METAL_PATCH_INTENSITY   = 0.9
METAL_PATCH_SHARPNESS   = 0.55

# ── Adjustment params ──
HUE_GRADE_HUE           = 0.50  # 0.5 = no rotation
HUE_GRADE_SAT           = 1.10  # slight saturation boost
HUE_GRADE_VAL           = 0.95  # tiny darkening for grime feel

ROUGH_BOOST_BRIGHT      =  0.10
ROUGH_BOOST_CONTRAST    =  0.35


# ─── HELPERS (mirrored from weathered_bronze.py for consistency) ──────────────

def _get_or_create_material(obj, name):
    """Return existing material `name` or create new. Replace ALL slots with it."""
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
    """Remove all TLM layers from this material so the script is idempotent."""
    tlm = mat.tlm
    while len(tlm.layers) > 0:
        tlm.layers.remove(len(tlm.layers) - 1)
    tlm.active_layer_index = 0


def _add_fill(mat, name, color, opacity=1.0, blend_mode="MIX",
              output_channel="BASE_COLOR"):
    """Add a FILL layer."""
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
    """Add a PROCEDURAL layer."""
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
    """Add an empty PAINT layer for the user to scribble on."""
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
    """Add an ADJUSTMENT layer routed to the given channel."""
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

    # NOTE on layer order: _add_layer_common places the new layer ABOVE the
    # current active one. We start with NO layers, so the first add lands at
    # the bottom; each subsequent add lands above. Result: build order =
    # bottom-to-top, which matches the rendering order we want (base first,
    # details on top).

    # ── 8 (bottom): Steel base ─────────────────────────────────────────────
    l_steel = _add_fill(mat, "Steel Base", STEEL_COLOR,
                        opacity=1.0, output_channel="BASE_COLOR")
    l_steel.use_roughness = True
    l_steel.roughness_fill = 0.45
    l_steel.use_metallic = True
    l_steel.metallic_fill = 1.0

    # ── 7: Surface scratches — Voronoi DISTANCE_TO_EDGE on Roughness ───────
    # The Voronoi edges become brighter (= rougher) lines all over the metal,
    # so the surface reads as having been used / wiped / scuffed.
    l_scratch = _add_procedural(mat, "Surface Scratches", "VORONOI",
                                opacity=SCRATCH_OPACITY, blend_mode="ADD",
                                output_channel="ROUGHNESS")
    l_scratch.proc_voronoi_feature  = 'DISTANCE_TO_EDGE'
    l_scratch.proc_voronoi_distance = 'EUCLIDEAN'
    l_scratch.proc_scale     = SCRATCH_VORONOI_SCALE
    l_scratch.proc_randomness = 1.0
    # Color1/2 control the grayscale fac that ROUGHNESS extracts from the
    # red channel. Black→smooth, white→rough.
    l_scratch.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_scratch.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    l_scratch.proc_contrast = 0.7

    # ── 6: Paint coat — solid colour with EDGE_WEAR mask ───────────────────
    # The paint sits over the metal everywhere EXCEPT at chipped edges.
    # Same layer drives both base_color (paint colour) AND roughness
    # (paint is rougher than the polished bare metal under it).
    l_paint = _add_fill(mat, "Paint Coat", PAINT_COLOR,
                        opacity=PAINT_OPACITY, output_channel="BASE_COLOR")
    l_paint.use_roughness = True
    l_paint.roughness_fill = 0.55  # painted surface — moderate roughness
    l_paint.use_metallic = True
    l_paint.metallic_fill = 0.0    # paint is non-metallic
    l_paint.use_mask = True
    l_paint.mask_source = 'EDGE_WEAR'
    l_paint.mask_gen_intensity = EDGE_WEAR_INTENSITY
    l_paint.mask_gen_breakup = EDGE_WEAR_BREAKUP
    l_paint.mask_gen_breakup_scale = EDGE_WEAR_BREAKUP_S
    l_paint.mask_gen_sharpness = EDGE_WEAR_SHARPNESS
    l_paint.mask_invert = True   # EDGE_WEAR is bright at edges → invert so
                                 # paint shows EVERYWHERE EXCEPT the edges
    l_paint.mask_contrast = 0.6

    # ── 5: Paint texture — Noise routed to Roughness ───────────────────────
    # Subtle high-frequency variation in the paint's roughness — so the
    # paint doesn't look like a flat plastic film.
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

    # ── 4: Rust streaks — Marble veins routed to Base Color ────────────────
    # Branching: the rust MULTIPLY-darkens base_color but stays MIX on
    # roughness — the rust stripes need to bleed into the paint colour
    # without fully replacing it. The DIRT mask makes the streaks pool in
    # crevices and recesses the way real rust runs do.
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
    # Branching override: on Base Color, MULTIPLY (so rust tints rather
    # than replaces). On every other channel the layer doesn't contribute
    # because output_channel is BASE_COLOR.
    l_rust.blend_mode_base_color = 'MULTIPLY'
    # Mask: DIRT — rust accumulates where dirt would.
    l_rust.use_mask = True
    l_rust.mask_source = 'DIRT'
    l_rust.mask_gen_intensity = DIRT_INTENSITY
    l_rust.mask_gen_breakup = DIRT_BREAKUP
    l_rust.mask_gen_breakup_scale = DIRT_BREAKUP_S
    l_rust.mask_gen_sharpness = DIRT_SHARPNESS
    l_rust.mask_ao_distance = DIRT_AO_DIST
    l_rust.mask_contrast = 0.55

    # ── 3: Metallic patches — chipped paint exposes bare metal ─────────────
    # Routed to METALLIC so the patches drive the metallic channel bright
    # again where the paint is gone. The same idea applies to base_color
    # (we'd want the BARE_METAL_COLOR there), but to keep the demo focused
    # on a single routing target we drive METALLIC only — the EDGE_WEAR
    # mask on the Paint Coat already handles the base_color side.
    l_metal_patch = _add_procedural(mat, "Metallic Patches", "NOISE",
                                     opacity=METAL_PATCHES_OPACITY,
                                     blend_mode="ADD",
                                     output_channel="METALLIC")
    l_metal_patch.proc_scale = METAL_PATCH_SCALE
    l_metal_patch.proc_detail = 5.0
    l_metal_patch.proc_roughness_proc = 0.65
    l_metal_patch.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_metal_patch.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    l_metal_patch.proc_contrast = 0.75
    l_metal_patch.use_mask = True
    l_metal_patch.mask_source = 'CURVATURE_SMART'
    l_metal_patch.mask_gen_intensity = METAL_PATCH_INTENSITY
    l_metal_patch.mask_gen_sharpness = METAL_PATCH_SHARPNESS
    l_metal_patch.mask_contrast = 0.55

    # ── 2: Roughness boost — ADJUSTMENT routed to Roughness (NEW!) ─────────
    # BRIGHT_CONTRAST on the Roughness channel — it lifts the dark parts
    # of the roughness map and adds contrast so the wet/dry split is more
    # readable. This demonstrates the adjustment-on-scalar-channel feature
    # we just shipped (commit 059ec9b).
    l_rough_boost = _add_adjustment(mat, "Roughness Boost", "BRIGHT_CONTRAST",
                                    opacity=ROUGH_BOOST_OPACITY,
                                    output_channel="ROUGHNESS")
    l_rough_boost.adj_brightness = ROUGH_BOOST_BRIGHT
    l_rough_boost.adj_contrast   = ROUGH_BOOST_CONTRAST

    # ── 1: Color grade — HUE_SAT on Base Color ─────────────────────────────
    # Final colour grading on the assembled base_color — slight saturation
    # bump and a tiny value drop for a grimier feel. opacity acts as the
    # adjustment STRENGTH (the wrap mix below the HSV node).
    l_grade = _add_adjustment(mat, "Color Grade", "HUE_SAT",
                              opacity=COLOR_GRADE_OPACITY,
                              output_channel="BASE_COLOR")
    l_grade.adj_hue = HUE_GRADE_HUE
    l_grade.adj_saturation = HUE_GRADE_SAT
    l_grade.adj_value = HUE_GRADE_VAL

    # ── 0 (top): Hand details — empty PAINT layer ──────────────────────────
    # The user can scribble extra damage / scratches / stickers here.
    # output_channel='BASE_COLOR' so strokes go on top of the colour grade.
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
