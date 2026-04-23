"""
TLM Flagship Preset — Weathered Bronze (scaffold, iteration #1)
================================================================

How to use:
  1. Open Blender 5.0 with TLM addon enabled.
  2. Select the object you want to texture (must be a mesh with a UV map).
  3. Open the Scripting editor → New → paste this file → Run.
  4. The script builds a material called "TLM_Weathered_Bronze" on the active
     object and populates it with a 6-layer TLM stack.
  5. Look at the viewport (Material Preview or Rendered, Cycles or Eevee).
  6. Tweak values below and re-run to iterate.

What this preset showcases:
  - Non-destructive layer stacking (6 layers: base → variation → patina → wear)
  - Procedural textures (Noise, Voronoi with per-cell random color)
  - Smart mask generators (DIRT, EDGE_WEAR, CURVATURE_SMART)
  - Per-layer PBR channels (roughness, metallic vary per layer)
  - Branching (per-channel blend mode override): dirt MULTIPLY-darkens
    base_color but ADDs roughness
  - Mask contrast / softness for fine control over wear distribution

Layer stack (top to bottom — top is rendered last / painted on top):
   0. Edge highlights  — polished bronze where edges have worn through patina
   1. Dirt in crevices — dark grime accumulating in cavities
   2. Patina patches   — Voronoi teal-green oxidation blobs
   3. Patina base      — solid teal wash masked by AO (more in recesses)
   4. Bronze variation — noisy copper tonal variation (OVERLAY on base)
   5. Bronze base      — solid warm copper-brown (bottom, 100%)

Tweak the CONSTANTS at the top to iterate on color / intensity / scale.
"""

import bpy
import math

# Ensure we can import the addon's _add_layer_common helper.
# The addon must be enabled in Blender preferences for this to work.
from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────
# Edit these to iterate on the look.

MATERIAL_NAME = "TLM_Weathered_Bronze"
RESOLUTION = "1024"  # "512" / "1024" / "2048" / "4096"

# Colors (linear RGB, alpha always 1.0).
# Bronze copper base, warm brown-orange.
BRONZE_BASE_COLOR        = (0.55, 0.30, 0.12, 1.0)
BRONZE_VARIATION_COLOR_1 = (0.40, 0.22, 0.08, 1.0)  # dark copper
BRONZE_VARIATION_COLOR_2 = (0.70, 0.42, 0.18, 1.0)  # lit copper
# Patina teals: range of oxidation colors
PATINA_WASH_COLOR        = (0.12, 0.45, 0.38, 1.0)  # solid teal-green
PATINA_CELL_COLOR_1      = (0.10, 0.40, 0.35, 1.0)  # darker teal
PATINA_CELL_COLOR_2      = (0.20, 0.55, 0.45, 1.0)  # brighter teal
PATINA_CELL_COLOR_3      = (0.15, 0.50, 0.30, 1.0)  # greener teal
# Dirt in crevices: dark brown, nearly black
DIRT_COLOR               = (0.06, 0.04, 0.02, 1.0)
# Edge highlights: fresh exposed bronze (brighter + warmer than base)
EDGE_HIGHLIGHT_COLOR     = (0.78, 0.50, 0.22, 1.0)

# Intensities (0-1 opacity, opacity per layer)
PATINA_WASH_OPACITY   = 0.55
PATINA_CELL_OPACITY   = 0.75
BRONZE_VAR_OPACITY    = 0.65
DIRT_OPACITY          = 0.90
EDGE_OPACITY          = 0.85

# Procedural scales (higher = smaller features)
BRONZE_NOISE_SCALE    = 6.0
PATINA_VORONOI_SCALE  = 8.0

# Smart generator params
DIRT_INTENSITY   = 1.4
DIRT_BREAKUP     = 0.55
DIRT_BREAKUP_SC  = 15.0
DIRT_SHARPNESS   = 0.55
DIRT_AO_DIST     = 1.2

EDGE_INTENSITY   = 1.1
EDGE_BREAKUP     = 0.35
EDGE_BREAKUP_SC  = 22.0
EDGE_SHARPNESS   = 0.75

PATINA_CURVSMART_INTENSITY = 1.0
PATINA_CURVSMART_SHARPNESS = 0.4


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _get_or_create_material(obj, name):
    """Return existing material `name` or create new. Replace ALL slots with it."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True

    # Wipe all existing material slots so TLM_Weathered_Bronze is the *only*
    # material on the object (avoids confusion if the sphere already had
    # Material.002, Material.003, etc.).
    while obj.data.materials:
        obj.data.materials.pop(index=0)
    obj.data.materials.append(mat)
    obj.active_material_index = 0
    return mat


def _clear_layers(mat):
    """Remove all TLM layers from this material so the script is idempotent."""
    tlm = mat.tlm
    # Collection properties: remove from end to keep indices stable.
    while len(tlm.layers) > 0:
        tlm.layers.remove(len(tlm.layers) - 1)
    tlm.active_layer_index = 0


def _add_fill(mat, name, color, opacity=1.0, blend_mode="MIX"):
    """Add a FILL layer to `mat`, set its color/opacity/blend."""
    print(f"  + adding FILL '{name}'...")
    ctx = bpy.context
    _add_layer_common(ctx, "FILL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.fill_color = color
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    return layer


def _add_procedural(mat, name, proc_type, opacity=1.0, blend_mode="MIX"):
    """Add a PROCEDURAL layer, set type/opacity/blend. Caller configures further."""
    print(f"  + adding PROCEDURAL '{name}' ({proc_type})...")
    ctx = bpy.context
    _add_layer_common(ctx, "PROCEDURAL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.proc_type = proc_type
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    return layer


# ─── BUILD THE PRESET ─────────────────────────────────────────────────────────

def build_weathered_bronze():
    obj = bpy.context.active_object
    if obj is None:
        raise RuntimeError("No active object. Select a mesh first.")
    if obj.type != 'MESH':
        raise RuntimeError(f"Active object '{obj.name}' is not a mesh.")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False  # disable auto-rebuild while we build; we'll rebuild once at end

    _clear_layers(mat)

    # ── Layer 5 (bottom): Bronze base — solid warm copper-brown ──
    l_base = _add_fill(mat, "Bronze Base", BRONZE_BASE_COLOR, opacity=1.0, blend_mode="MIX")
    l_base.use_roughness = True
    l_base.roughness_fill = 0.35   # bronze is semi-polished
    l_base.use_metallic = True
    l_base.metallic_fill = 1.0     # full metal

    # ── Layer 4: Bronze variation — noisy tonal shift (OVERLAY) ──
    l_var = _add_procedural(mat, "Bronze Variation", "NOISE",
                            opacity=BRONZE_VAR_OPACITY, blend_mode="OVERLAY")
    l_var.proc_scale = BRONZE_NOISE_SCALE
    l_var.proc_detail = 6.0
    l_var.proc_roughness_proc = 0.55  # procedural noise internal roughness (NOT the PBR channel)
    l_var.proc_color1 = BRONZE_VARIATION_COLOR_1
    l_var.proc_color2 = BRONZE_VARIATION_COLOR_2

    # ── Layer 3: Patina base wash — solid teal with AO mask ──
    l_patina_wash = _add_fill(mat, "Patina Wash", PATINA_WASH_COLOR,
                              opacity=PATINA_WASH_OPACITY, blend_mode="MIX")
    l_patina_wash.use_roughness = True
    l_patina_wash.roughness_fill = 0.75  # patina is rough
    l_patina_wash.use_metallic = True
    l_patina_wash.metallic_fill = 0.0    # patina is non-metal
    # Mask: AO — patina accumulates in cavities more than open surfaces
    l_patina_wash.use_mask = True
    l_patina_wash.mask_source = 'AO'
    l_patina_wash.mask_ao_distance = 0.8
    l_patina_wash.mask_invert = True  # invert: recesses become bright (mask shows layer)
    l_patina_wash.mask_contrast = 0.55

    # ── Layer 2: Patina patches — Voronoi with random per-cell color ──
    l_patina_patches = _add_procedural(mat, "Patina Patches", "VORONOI",
                                        opacity=PATINA_CELL_OPACITY, blend_mode="MIX")
    l_patina_patches.proc_scale = PATINA_VORONOI_SCALE
    l_patina_patches.proc_distortion = 0.8
    l_patina_patches.proc_color1 = PATINA_CELL_COLOR_1
    l_patina_patches.proc_color2 = PATINA_CELL_COLOR_2
    l_patina_patches.use_proc_color3 = True
    l_patina_patches.proc_color3 = PATINA_CELL_COLOR_3
    l_patina_patches.proc_color3_position = 0.5
    l_patina_patches.proc_voronoi_random_color = True  # key feature: different teal per cell
    l_patina_patches.proc_voronoi_random_seed = 3.7
    l_patina_patches.use_roughness = True
    l_patina_patches.roughness_fill = 0.8
    l_patina_patches.use_metallic = True
    l_patina_patches.metallic_fill = 0.0
    # Mask: CURVATURE_SMART — patina concentrates where surface is neither flat nor sharp edge
    # (think: medium-curvature grooves and molding features)
    l_patina_patches.use_mask = True
    l_patina_patches.mask_source = 'CURVATURE_SMART'
    l_patina_patches.mask_gen_intensity = PATINA_CURVSMART_INTENSITY
    l_patina_patches.mask_gen_sharpness = PATINA_CURVSMART_SHARPNESS
    l_patina_patches.mask_contrast = 0.5

    # ── Layer 1: Dirt in crevices — DIRT smart mask, MULTIPLY on base_color ──
    l_dirt = _add_fill(mat, "Dirt Crevices", DIRT_COLOR,
                       opacity=DIRT_OPACITY, blend_mode="MIX")
    l_dirt.use_roughness = True
    l_dirt.roughness_fill = 0.95  # dirt is very rough
    l_dirt.use_metallic = True
    l_dirt.metallic_fill = 0.0
    # Smart DIRT mask
    l_dirt.use_mask = True
    l_dirt.mask_source = 'DIRT'
    l_dirt.mask_gen_intensity = DIRT_INTENSITY
    l_dirt.mask_gen_breakup = DIRT_BREAKUP
    l_dirt.mask_gen_breakup_scale = DIRT_BREAKUP_SC
    l_dirt.mask_gen_sharpness = DIRT_SHARPNESS
    l_dirt.mask_ao_distance = DIRT_AO_DIST
    l_dirt.mask_contrast = 0.55
    # BRANCHING: dirt darkens base_color by MULTIPLY but doesn't mix as MIX
    # (MIX would just stamp brown; MULTIPLY preserves the underlying pattern
    # while darkening it — much more realistic grime).
    l_dirt.blend_mode_base_color = 'MULTIPLY'

    # ── Layer 0 (top): Edge highlights — EDGE_WEAR mask, polished bronze peeking through ──
    l_edge = _add_fill(mat, "Edge Highlights", EDGE_HIGHLIGHT_COLOR,
                       opacity=EDGE_OPACITY, blend_mode="MIX")
    l_edge.use_roughness = True
    l_edge.roughness_fill = 0.18   # very polished (friction has worn off patina)
    l_edge.use_metallic = True
    l_edge.metallic_fill = 1.0
    # EDGE_WEAR smart mask — convex edges with noise breakup
    l_edge.use_mask = True
    l_edge.mask_source = 'EDGE_WEAR'
    l_edge.mask_gen_intensity = EDGE_INTENSITY
    l_edge.mask_gen_breakup = EDGE_BREAKUP
    l_edge.mask_gen_breakup_scale = EDGE_BREAKUP_SC
    l_edge.mask_gen_sharpness = EDGE_SHARPNESS
    l_edge.mask_contrast = 0.6

    # ── Finalize ──
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Weathered Bronze built on '{obj.name}'.")
    print(f"      Material: {mat.name}")
    print(f"      Layers: {len(tlm.layers)} (index 0 = top)")
    for i, l in enumerate(tlm.layers):
        print(f"        [{i}] {l.layer_type:11s} {l.name:24s} "
              f"blend={l.blend_mode:8s} opacity={l.opacity:.2f}")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    build_weathered_bronze()
