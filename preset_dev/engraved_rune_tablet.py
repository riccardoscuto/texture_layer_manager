"""
TLM Showcase Preset — Engraved Rune Tablet (v0.1)
====================================================

THE killer preset that justifies buying TLM over a free node setup.

Concept: dark slate stone tablet with magical runes carved into the
surface. The runes glow with internal light AND are inlaid with gold.
A SINGLE MASK drives FOUR different physical effects simultaneously:
  - GOLD INLAY (base color + metallic + reduced roughness)
  - INTERNAL EMISSION (cyan magical light inside the carving)
  - CARVED DEPTH (negative bump — runes are below the stone surface)
  - EDGE HIGHLIGHT (curvature mask brightens the inlay rim)

Why this preset sells TLM:
  - REFERENCE × 3 layers all pointing to the SAME mask driver
  - Cumulative routing on each REFERENCE drives 2-3 channels at once
  - In v0.2, the VORONOI driver is replaced with a PAINT layer
    → the user literally PAINTS the rune mask with a brush, and gold
       + glow + depth + edge highlight appear automatically.
    → no other Blender addon offers this workflow.

v0.1 = MINIMAL FOUNDATION (4 layers, VORONOI-driven for instant visual)
------------------------------------------------------------------------
Uses VORONOI DISTANCE_TO_EDGE as the rune-shape driver — produces
organic "carved line" patterns naturally. In v0.2 the VORONOI is
swapped for a PAINT layer (the killer feature) so users paint their
own runes.

  v0.1 → Stone Base + VORONOI driver + 2 REFERENCEs (Gold + Glow)  [WHERE WE ARE]
  v0.2 → Replace VORONOI driver with PAINT (the killer feature)
  v0.3 → + Carved Depth REFERENCE (negative bump) + Curvature edge highlight
  v0.4 → + Dust accumulation in cavities (DIRT smart mask)
  v0.5 → + ADJUSTMENT HUE_SAT color grade
  v1.0 → final polish, save .tlm

How to run:
  1. Open _preview_scene.blend.
  2. Open this file in Text Editor.
  3. Run Script.
  4. Render in Cycles (Rendered viewport mode).

Feature flex (v1.0 target):
  REFERENCE × 3 ▪ Cumulative routing × 4 channels ▪ Smart masks ▪
  PAINT → multi-channel ▪ ADJUSTMENT routed ▪ GROUP nesting
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Engraved_Rune_Tablet"
RESOLUTION = "2048"   # higher res — fine paint detail matters in v0.2+

TARGET_MESH = "TLM_Sphere"   # one of: TLM_Sphere / TLM_Cube / TLM_Panel

# ── Slate Stone Base ──
# Dark blue-grey slate. Dielectric, medium roughness, slightly
# textured by a macro variation noise in v0.5.
STONE_COLOR             = (0.045, 0.050, 0.060, 1.0)
STONE_METALLIC          = 0.0
STONE_ROUGHNESS         = 0.55

# ── Rune Mask Driver (VORONOI distance-to-edge) ──
# A single hidden procedural that all REFERENCE layers below sample
# from. proc_color2 holds the "mask bright" value — set to white so
# REFERENCEs inherit a clean 0-1 mask range that they can then re-
# colour through their own fill_color (for scalar channels) or take
# verbatim (for colour channels — see GOLD_COLOR comment).
RUNE_MASK_SCALE         = 4.0    # ~4 voronoi cells across the unwrap
RUNE_MASK_RANDOMNESS    = 1.0    # full random — organic rune-line layout
RUNE_MASK_DISTORTION    = 0.4    # Voronoi vector distortion → wavy rune lines
RUNE_MASK_CONTRAST      = 0.85   # razor-sharp edges (binary mask-like)
# How "wide" the rune lines are. The DTE output is 0 on cell edges
# and rises towards 1 in cell interiors. We use this to set color2
# (bright) at the edge band and color1 (dark) elsewhere.
RUNE_MASK_DARK          = (0.0, 0.0, 0.0, 1.0)   # cell interior (no rune)
RUNE_MASK_LIGHT         = (1.0, 1.0, 1.0, 1.0)   # cell edges (RUNE)

# Driver opacity. Near-zero so the source pattern is invisible in
# the composite — but REFERENCE picks up the FULL pattern regardless
# (source opacity doesn't affect REFERENCE sampling).
RUNE_MASK_OPACITY       = 0.001

# ── Gold Inlay (REFERENCE → Rune Mask) ──
# Warm gold filling the rune lines. The colour we set as a FILL on
# the REFERENCE layer drives the SCALAR channels (metallic, rough)
# but the BASE_COLOR comes from the source's color directly. We
# trick this by setting the source's color2 = GOLD instead of white,
# then ALL channels are tinted gold at rune pixels.
# Actually no — better: keep source pattern as B/W mask (clean),
# tint the gold at the source level by changing color2 below.
# See _set_driver_colors_for_gold(); the same mask is sampled twice
# anyway (one for gold visual, one for glow), so they get tinted
# independently per REFERENCE via the Voronoi colour swap.
GOLD_COLOR              = (0.92, 0.70, 0.22, 1.0)   # rich warm gold
GOLD_METALLIC           = 1.0   # gold IS metal — full conductor
GOLD_ROUGHNESS          = 0.28  # polished gold under "engraving wear"

# ── Rune Glow (REFERENCE → Rune Mask, with emission) ──
# Magical cyan glow emanating from "inside" the carving. The same
# rune mask drives both the GOLD INLAY and the EMISSION channel —
# that's why TLM's REFERENCE+cumulative routing is the killer
# feature: ONE mask, FOUR physical effects, no manual node wiring.
GLOW_COLOR              = (0.40, 0.85, 1.00, 1.0)   # cyan magical
GLOW_STRENGTH           = 5.0


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_target_mesh():
    target = bpy.data.objects.get(TARGET_MESH)
    if target is not None and target.type == 'MESH':
        return target
    active = bpy.context.active_object
    if active is not None and active.type == 'MESH':
        return active
    for obj in bpy.context.scene.objects:
        if obj.type == 'MESH' and obj.name != "TLM_Ground":
            return obj
    return None


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
    print(f"  + FILL        '{name}'")
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
    print(f"  + PROCEDURAL  '{name}'  ({proc_type})")
    _add_layer_common(bpy.context, "PROCEDURAL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.proc_type = proc_type
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    return layer


def _add_reference(mat, name, target_layer_name, opacity=1.0,
                   blend_mode="MIX", output_channel="BASE_COLOR"):
    print(f"  + REFERENCE   '{name}' → '{target_layer_name}'")
    _add_layer_common(bpy.context, "REFERENCE")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.reference_layer_name = target_layer_name
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    return layer


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_engraved_rune_tablet():
    obj = _find_target_mesh()
    if obj is None:
        raise RuntimeError(
            "No mesh found. Run _preview_scene.py first or open "
            "_preview_scene.blend."
        )

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Engraved Rune Tablet v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─────────────────────────────────────────────────────────────────
    # 1. Slate Stone Base — the dark stone tablet
    # ─────────────────────────────────────────────────────────────────
    l_stone = _add_fill(mat, "Slate Stone Base", STONE_COLOR,
                        opacity=1.0, output_channel="BASE_COLOR")
    l_stone.use_roughness = True
    l_stone.roughness_fill = STONE_ROUGHNESS
    l_stone.use_metallic = True
    l_stone.metallic_fill = STONE_METALLIC

    # ─────────────────────────────────────────────────────────────────
    # 2. Rune Mask Driver — PROCEDURAL VORONOI distance-to-edge
    # ─────────────────────────────────────────────────────────────────
    # The PATTERN SOURCE that all the REFERENCEs sample. Voronoi DTE
    # gives a value near 0 on cell edges (= where runes will appear)
    # and rises towards 1 inside cells (no-rune areas). With proc_
    # contrast high + proc_distortion adding organic vector warping,
    # the edges read as wavy carved lines.
    #
    # The driver's OWN composite contribution is op=0.001 — invisible.
    # But REFERENCE layers sampling it get the FULL pattern (source
    # opacity doesn't affect REFERENCE sampling).
    l_mask = _add_procedural(mat, "Rune Mask Driver", "VORONOI",
                             opacity=RUNE_MASK_OPACITY,
                             output_channel="BASE_COLOR")
    l_mask.proc_scale         = RUNE_MASK_SCALE
    l_mask.proc_randomness    = RUNE_MASK_RANDOMNESS
    l_mask.proc_distortion    = RUNE_MASK_DISTORTION
    l_mask.proc_contrast      = RUNE_MASK_CONTRAST
    l_mask.proc_voronoi_feature  = 'DISTANCE_TO_EDGE'
    # Color1 = dark (cell interior, NO rune). Color2 = light (edge, RUNE).
    # The mask is a plain B/W signal; REFERENCEs below tint it.
    l_mask.proc_color1 = RUNE_MASK_DARK
    l_mask.proc_color2 = RUNE_MASK_LIGHT
    # Vector distortion makes the rune lines organic instead of perfect
    # Voronoi mathematical curves.
    l_mask.proc_vector_distortion = 0.25

    # ─────────────────────────────────────────────────────────────────
    # 3. Gold Inlay — REFERENCE → Rune Mask Driver
    # ─────────────────────────────────────────────────────────────────
    # Where the mask is BRIGHT (rune lines), this layer brings:
    #   - BASE_COLOR = gold tint (via LIGHTEN blend over the dark stone)
    #   - METALLIC = 1.0 (gold IS metal)
    #   - ROUGHNESS = 0.28 (polished gold)
    # All three driven by the SAME mask pattern via cumulative routing.
    # This is the move that's impossible to do clean with vanilla nodes.
    l_gold = _add_reference(mat, "Gold Inlay", "Rune Mask Driver",
                            opacity=1.0,
                            blend_mode="LIGHTEN",
                            output_channel="BASE_COLOR")
    # Tint the inherited B/W mask towards GOLD by overriding the
    # source layer's color2 *just before sampling* would be ideal,
    # but easier: have the REFERENCE multiply by gold via its fill.
    # For COLOR channels REFERENCE doesn't apply fill_color, so the
    # cleanest approach is: set the SOURCE's color2 = GOLD directly.
    # Since the source is only sampled by REFERENCEs and contributes
    # ~0 to composite, this is safe.
    # See "Recolour driver for gold" below — happens after the layer
    # creations so the colour change applies on rebuild.
    l_gold.use_metallic = True
    l_gold.metallic_fill = GOLD_METALLIC
    l_gold.use_roughness = True
    l_gold.roughness_fill = GOLD_ROUGHNESS

    # ─────────────────────────────────────────────────────────────────
    # 4. Rune Glow — REFERENCE → Rune Mask Driver (cumulative emission)
    # ─────────────────────────────────────────────────────────────────
    # Same mask, different role: emit cyan magical light. Cumulative
    # routing modulates the emission strength by the source pattern,
    # so the glow appears only inside the rune lines.
    # output_channel = BASE_COLOR because EMISSION isn't a valid
    # output target — the use_emission flag below is what actually
    # routes the cumulative emission contribution.
    l_glow = _add_reference(mat, "Rune Glow", "Rune Mask Driver",
                            opacity=1.0,
                            blend_mode="LIGHTEN",
                            output_channel="BASE_COLOR")
    l_glow.use_emission = True
    l_glow.emission_color = GLOW_COLOR
    l_glow.emission_strength = GLOW_STRENGTH

    # ── Recolour driver for gold-tinted REFERENCE sampling ──────────
    # We DO want the GOLD layer to read gold, not just bright white.
    # Since REFERENCEs sample the source's color directly for base_color,
    # we set the source's color2 to GOLD here, AFTER both REFERENCEs
    # have been declared. The Rune Glow REFERENCE samples the same
    # gold-tinted source; with cumulative emission routing the strong
    # cyan emission overpowers any baked colour tint at glow pixels.
    l_mask.proc_color2 = GOLD_COLOR

    # ── Rebuild & finalise ────────────────────────────────────────────
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Done. 4 layers built (v0.1 — Voronoi-driven runes).")
    print("[TLM] Same mask drives Gold Inlay AND Cyan Glow simultaneously.")
    print("[TLM] In v0.2 we'll swap the Voronoi driver for a PAINT layer")
    print("[TLM] so you can paint custom runes and watch all 4 channels respond.\n")
    return mat


if __name__ == "__main__":
    build_engraved_rune_tablet()
