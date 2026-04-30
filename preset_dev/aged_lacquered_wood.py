"""
TLM Preset — Aged Lacquered Wood
=================================

Showcases:
  - Procedural NOISE (wood grain variation)
  - Procedural VORONOI (aging blotches)
  - Procedural WAVE (fine scratch bands on roughness)
  - Smart masks: AO (lacquer pooling), DIRT (dust in crevices),
    EDGE_WEAR (scratches on edges)
  - output_channel routing: WAVE → ROUGHNESS, NOISE → ROUGHNESS
  - Bump from a noise pattern (subtle wood pore micro-relief)
  - Branching (per-channel blend overrides)
  - Mask refinement: contrast + softness for tuned wear distribution

Layer stack (top → bottom). The compositor reads bottom-up, so
the Bronze Base is the foundation and the Edge Highlights are
painted last on top.

   0. Dust in cracks         FILL  · DIRT mask · MULTIPLY · base_color
   1. Edge wear (scratches)  PROC WAVE · EDGE_WEAR mask · ROUGHNESS
   2. Aged blotches          PROC VORONOI · output=BASE_COLOR
   3. Lacquer (low rough)    FILL  · AO mask · ROUGHNESS only
   4. Wood grain variation   PROC NOISE  · OVERLAY · base_color
   5. Wood base color        FILL  · solid warm brown · base_color

How to run:
  1. Open Blender 5.0, addon TLM enabled.
  2. Select the object to texture (mesh with UV).
  3. Scripting workspace → Text Editor → Open this file → Run.
  4. Material 'TLM_Aged_Lacquered_Wood' is created and assigned.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Aged_Lacquered_Wood"
RESOLUTION    = "1024"

# Colors (linear RGB, alpha always 1.0)
WOOD_BASE_COLOR    = (0.28, 0.16, 0.07, 1.0)   # warm walnut
WOOD_GRAIN_DARK    = (0.18, 0.10, 0.04, 1.0)
WOOD_GRAIN_LIGHT   = (0.42, 0.26, 0.12, 1.0)
LACQUER_TINT       = (0.32, 0.18, 0.08, 1.0)   # slight amber lacquer
AGE_BLOTCH_DARK    = (0.20, 0.12, 0.06, 1.0)
AGE_BLOTCH_LIGHT   = (0.36, 0.24, 0.14, 1.0)
DUST_COLOR         = (0.08, 0.06, 0.04, 1.0)   # near-black grime

# Opacities
GRAIN_OPACITY        = 0.70
LACQUER_AO_OPACITY   = 0.85
BLOTCH_OPACITY       = 0.40
WAVE_SCRATCH_OPACITY = 0.60
DUST_OPACITY         = 0.80

# Procedural scales (Blender convention: bigger = smaller features)
GRAIN_NOISE_SCALE   = 12.0
GRAIN_NOISE_DETAIL  = 5.0      # visible secondary grain
GRAIN_NOISE_DISTORT = 0.5      # slight warp = more organic

BLOTCH_VOR_SCALE    = 6.0      # large patches
WAVE_SCRATCH_SCALE  = 25.0     # tight bands
WAVE_SCRATCH_DIST   = 0.15     # slight wobble in scratch direction

# PBR baseline
WOOD_BASE_ROUGHNESS = 0.65     # raw wood is matte
LACQUER_ROUGHNESS   = 0.18     # lacquered = glossy
WOOD_BASE_METALLIC  = 0.0      # dielectric

# Smart-mask tuning
LACQUER_AO_DISTANCE = 1.6      # how deep the lacquer pools (m)
DUST_AO_DISTANCE    = 0.6      # tighter — dust only in fine cracks
DUST_BREAKUP        = 0.55
DUST_BREAKUP_SCALE  = 18.0
DUST_INTENSITY      = 1.3
DUST_SHARPNESS      = 0.6
EDGE_WEAR_INTENSITY = 1.1
EDGE_WEAR_SHARPNESS = 0.55     # softer scratches
EDGE_WEAR_BREAKUP   = 0.4
EDGE_WEAR_BREAKUP_S = 22.0

# Bump (subtle wood pore micro-relief from the same noise pattern)
WOOD_BUMP_STRENGTH  = 0.25
WOOD_BUMP_DISTANCE  = 0.005


# ─── BUILD ────────────────────────────────────────────────────────────────────

def _ensure_material(obj, name):
    """Create or fetch a TLM material slot, assigned to the active object."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    obj.active_material = mat
    return mat


def _make_context_override(mat):
    """Operators that read context.active_material need this override."""
    return {"active_object": bpy.context.active_object, "object": bpy.context.active_object}


def build():
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        raise RuntimeError("Select a mesh object first")

    mat = _ensure_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.layers.clear()
    tlm.resolution = RESOLUTION
    tlm.uv_map = "UVMap"
    # Suppress rebuild on every layer add — we'll do one rebuild at the end.
    tlm.auto_composite = False

    # The _add_layer_common helper reads bpy.context.active_object — make sure
    # selection is current.
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    # Helper: build a layer and return the LayerItem reference. layers.add()
    # appends at the end; index = len-1 right after.
    def add(layer_type):
        _add_layer_common(bpy.context, layer_type)
        return tlm.layers[-1]

    # ── Layer 5 (bottom): Wood base color — warm walnut Fill ─────────────
    base = add("FILL")
    base.name = "Wood Base"
    base.fill_color   = WOOD_BASE_COLOR
    base.output_channel = 'BASE_COLOR'
    base.use_roughness = True
    base.roughness_fill = WOOD_BASE_ROUGHNESS
    base.use_metallic  = True
    base.metallic_fill = WOOD_BASE_METALLIC
    base.opacity = 1.0
    base.blend_mode = "MIX"

    # ── Layer 4: Wood grain variation — NOISE OVERLAY on base color ──────
    grain = add("PROCEDURAL")
    grain.name = "Wood Grain"
    grain.proc_type = "NOISE"
    grain.proc_scale         = GRAIN_NOISE_SCALE
    grain.proc_detail        = GRAIN_NOISE_DETAIL
    grain.proc_distortion    = GRAIN_NOISE_DISTORT
    grain.proc_color1        = WOOD_GRAIN_DARK
    grain.proc_color2        = WOOD_GRAIN_LIGHT
    grain.proc_contrast      = 0.55
    grain.output_channel = 'BASE_COLOR'
    grain.blend_mode = "OVERLAY"
    grain.opacity = GRAIN_OPACITY
    # Same noise also drives subtle bump (use_bump piggybacks the proc fac)
    grain.use_bump = True
    grain.bump_strength = WOOD_BUMP_STRENGTH
    grain.bump_distance = WOOD_BUMP_DISTANCE

    # ── Layer 3: Lacquer — drops roughness in crevices via AO mask ───────
    # output=ROUGHNESS so this layer ONLY drives roughness, leaving the
    # color from the layers below untouched. Mask=AO so the lacquer
    # "pools" in concave regions (like real varnish flowing into details).
    lacquer = add("FILL")
    lacquer.name = "Lacquer"
    lacquer.fill_color = LACQUER_TINT  # used as luminance scalar by routed-FILL path
    lacquer.output_channel = 'ROUGHNESS'
    lacquer.opacity = LACQUER_AO_OPACITY
    lacquer.blend_mode = "MIX"
    # Mask: AO inverted so the lacquer pools in concave crevices
    lacquer.use_mask = True
    lacquer.mask_source = 'AO'
    lacquer.mask_ao_distance = LACQUER_AO_DISTANCE
    lacquer.mask_invert = True   # AO is bright in cavities → invert keeps lacquer where AO < 1
    lacquer.mask_contrast = 0.6  # firm transition
    # Drive scalar value via fill_color luminance — tint is dark
    # (Rec.709 ≈ 0.18) so when active the channel reads ≈0.18 = quite glossy.

    # ── Layer 2: Aging blotches — VORONOI patches on base color ──────────
    blotches = add("PROCEDURAL")
    blotches.name = "Aged Blotches"
    blotches.proc_type = "VORONOI"
    blotches.proc_voronoi_feature = "F1"
    blotches.proc_voronoi_distance = "EUCLIDEAN"
    blotches.proc_scale = BLOTCH_VOR_SCALE
    blotches.proc_randomness = 1.0
    blotches.proc_voronoi_random_color = True
    blotches.proc_voronoi_random_seed = 3.0
    blotches.proc_color1 = AGE_BLOTCH_DARK
    blotches.proc_color2 = AGE_BLOTCH_LIGHT
    blotches.proc_contrast = 0.30  # smooth gradients between cells
    blotches.output_channel = 'BASE_COLOR'
    blotches.blend_mode = "OVERLAY"
    blotches.opacity = BLOTCH_OPACITY

    # ── Layer 1: Edge wear scratches — WAVE bands on ROUGHNESS, EDGE mask ─
    # output=ROUGHNESS bumps the gloss in scratched zones.
    # Mask=EDGE_WEAR so scratches appear on convex edges (where wear is real).
    scratches = add("PROCEDURAL")
    scratches.name = "Edge Scratches"
    scratches.proc_type = "WAVE"
    scratches.proc_wave_type = "BANDS"
    scratches.proc_wave_profile = "SIN"
    scratches.proc_scale = WAVE_SCRATCH_SCALE
    scratches.proc_distortion = WAVE_SCRATCH_DIST
    scratches.proc_detail = 1.0
    scratches.proc_contrast = 0.85   # sharp scratch edges
    scratches.output_channel = 'ROUGHNESS'
    scratches.opacity = WAVE_SCRATCH_OPACITY
    scratches.blend_mode = "ADD"     # scratches ADD to base roughness (more matte)
    # Mask: EDGE_WEAR isolates convex edges; tune intensity/sharpness/breakup
    # for organic distribution.
    scratches.use_mask = True
    scratches.mask_source = 'EDGE_WEAR'
    scratches.mask_gen_intensity = EDGE_WEAR_INTENSITY
    scratches.mask_gen_sharpness = EDGE_WEAR_SHARPNESS
    scratches.mask_gen_breakup = EDGE_WEAR_BREAKUP
    scratches.mask_gen_breakup_scale = EDGE_WEAR_BREAKUP_S
    scratches.mask_contrast = 0.6
    scratches.mask_softness = 0.2

    # ── Layer 0 (top): Dust in crevices — FILL · DIRT smart mask · MULTIPLY ─
    dust = add("FILL")
    dust.name = "Dust"
    dust.fill_color = DUST_COLOR
    dust.output_channel = 'BASE_COLOR'
    dust.opacity = DUST_OPACITY
    dust.blend_mode = "MULTIPLY"     # darkens what's underneath
    dust.use_mask = True
    dust.mask_source = 'DIRT'
    dust.mask_ao_distance = DUST_AO_DISTANCE
    dust.mask_gen_intensity = DUST_INTENSITY
    dust.mask_gen_breakup = DUST_BREAKUP
    dust.mask_gen_breakup_scale = DUST_BREAKUP_SCALE
    dust.mask_gen_sharpness = DUST_SHARPNESS
    dust.mask_contrast = 0.55
    # Branching: dust also bumps roughness up (matte grime) without
    # contributing to base color twice. Demonstrates per-channel blend
    # mode override.
    dust.blend_mode_roughness = "ADD"
    dust.use_roughness = True
    dust.roughness_fill = 0.4

    # ── Final rebuild: turn auto_composite back on and rebuild once ──────
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Built '{MATERIAL_NAME}' with {len(tlm.layers)} layers.")
    return mat


if __name__ == "__main__":
    build()
