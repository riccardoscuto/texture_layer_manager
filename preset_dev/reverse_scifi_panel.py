"""
TLM Reverse Engineering — Sci-Fi Detailed Material
=====================================================

Source: user-provided "Sci-Fi Detailed Material" reference (Free download).
Dark charcoal metal panels with deep plate seams, fine cracks, and bright
cyan emission distributed across cell interiors.

Reverse engineering approach: this is a 100% procedural material in the
reference, so this TLM reconstruction must be 100% procedural too —
NO PAINT layers, no "manual art direction" slots. We map every node
section of the reference to a TLM layer.

Analyzed reference sections:
  - Mapping: Point type, Scale 1,1,1 (world coords)
  - Small Details: Noise + Voronoi F2 Chebychev combined
  - Large Plates: Voronoi F2 Chebychev low-randomness → bump + color
  - Base Color: Mix + HSV → ColorRamp threshold
  - Roughness: HSV controlling rough boost in cracks
  - Bump: dual Bump node chain
  - Emission: Voronoi F2 Chebychev → sharp threshold × cyan colour
  - Displacement: true geometric displacement

TLM mapping:
  Six procedural layers, stacked bottom-to-top.  Each section of the
  reference node graph corresponds to one TLM layer with cumulative
  channel routing where relevant.

Layer stack:
  01. Metal Base FILL                — dark charcoal, metallic high
  02. Metal Variation NOISE          — broad colour breakup OVERLAY
  03. Large Plates VORONOI DTE Cheb  — blocky plate seams (bump + color)
  04. Small Details VORONOI DTE Cheb — fine cracks (bump + color)
  05. Damage Wear NOISE              — roughness boost only
  06. Emission Cells VORONOI Cheb    — cyan emission inside cell centres
                                       (cumulative emission, no selector)
  07. Cell Outline VORONOI DTE Cheb  — thin asymmetric band at cell edges
                                       (uses new proc_ramp_center=0.05)

v0.11: introduces proc_ramp_center — asymmetric ColorRamp positioning so
the outline band can hug Fac=0 instead of being centred at Fac=0.5.
This is the new TLM feature that finally enables a TRUE thin outline.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_RE_SciFi_Panel"
RESOLUTION = "1024"
TARGET_MESH = "TLM_Cube"

# ── Metal Base ──
METAL_COLOR_DARK        = (0.040, 0.042, 0.050, 1.0)
METAL_COLOR_LIGHT       = (0.080, 0.082, 0.092, 1.0)
METAL_METALLIC          = 0.85
METAL_ROUGHNESS         = 0.50

# ── Metal Variation ──
METAL_VAR_SCALE         = 3.0
METAL_VAR_OPACITY       = 0.35

# ── Large Plates (blocky cells, low randomness) ──
PLATES_SCALE            = 3.0
PLATES_RANDOMNESS       = 0.25    # restored from v0.11 — user preferred the look
                                  # at this jitter level (slight rockiness vs CNC clean)
PLATES_DETAIL           = 0.5
PLATES_CONTRAST         = 0.55
PLATES_OPACITY          = 0.60
PLATES_BUMP_STRENGTH    = 0.90
PLATES_BUMP_DISTANCE    = 0.045
PLATES_EDGE_COLOR       = (0.012, 0.012, 0.016, 1.0)

# ── Small Details (fine Voronoi cracks) ──
DETAILS_SCALE           = 18.0
DETAILS_RANDOMNESS      = 0.85
DETAILS_CONTRAST        = 0.55
DETAILS_OPACITY         = 0.35
DETAILS_BUMP_STRENGTH   = 0.45
DETAILS_BUMP_DISTANCE   = 0.008
DETAILS_CRACK_COLOR     = (0.018, 0.020, 0.024, 1.0)

# ── Damage Wear (roughness variation noise) ──
WEAR_SCALE              = 5.0
WEAR_DETAIL             = 8.0
WEAR_OPACITY            = 0.40
WEAR_ROUGHNESS_BOOST    = 0.85

# ── Emission Cells (KEY visual signature) ──
# v0.4 — uses TLM's built-in emission pipeline (Invert → Power → SmoothStep)
# instead of trying to force emission via colour saturation.
#
# KEY INSIGHT from studying compositing.py:
# - Voronoi F1 outputs Fac=0 at cell centres, Fac=1 at cell edges.
# v0.19 — RESTORED v0.11 configuration. The user preferred the dense
# "wireframe + scattered emission" look over the sparse "panel grid + few
# accents" architecture. Key differences from later iterations:
#   • Scale 9 (was tweaked to 4-5) → many small cells, busy pattern
#   • Randomness 0.70 → organic irregular cells (NOT clean squares)
#   • Object coord (not Generated) → cells follow object-space directly
#   • Outline scale = EMISSION scale → outline traces every small cell
#     (NOT just the large plate seams) → wireframe-over-noise look
EMISSION_CELL_SCALE       = 9.0
EMISSION_CELL_RANDOMNESS  = 0.70
EMISSION_VECTOR_DISTORTION = 0.0
EMISSION_CONTRAST         = 0.50
EMISSION_COLOR            = (0.04, 0.45, 1.00, 1.0)
EMISSION_STRENGTH         = 1.2
EMISSION_THRESHOLD        = 0.42
EMISSION_FALLOFF          = 0.05
EMISSION_BASE_OPACITY     = 1.00

# ── Cell Outline (v0.19 — RESTORED v0.11: outline aligned to EMISSION cells) ──
# The user preferred this look: thin cyan band traces EVERY small emission
# cell (not just the large plate seams). Result: dense wireframe-over-noise
# texture, not panel-grid-with-trim. Both layers use same Voronoi params
# so the cells perfectly overlap — cyan rim around each cell.
OUTLINE_SCALE            = EMISSION_CELL_SCALE   # 9.0 — outline + emission share cells
OUTLINE_RANDOMNESS       = EMISSION_CELL_RANDOMNESS
OUTLINE_COLOR            = (0.05, 0.55, 1.00, 1.0)
OUTLINE_CONTRAST         = 0.95    # razor-sharp band edge
OUTLINE_RAMP_CENTER      = 0.05    # asymmetric ramp → band at cell EDGES (Fac=0 for DTE)
OUTLINE_OPACITY          = 0.55


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


def _add_proc(mat, name, proc_type, opacity=1.0, blend_mode="MIX",
              output_channel="BASE_COLOR"):
    print(f"  + PROCEDURAL  '{name}'  ({proc_type})")
    _add_layer_common(bpy.context, "PROCEDURAL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.proc_type = proc_type
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    layer.proc_coord_type = "OBJECT"
    return layer


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_scifi_panel():
    obj = _find_target_mesh()
    if obj is None:
        raise RuntimeError("No mesh found.")

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building RE Sci-Fi Panel v0.2 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─────────────────────────────────────────────────────────────────
    # 01. Metal Base — dark charcoal substrate
    # ─────────────────────────────────────────────────────────────────
    l_base = _add_fill(mat, "01 Metal Base", METAL_COLOR_DARK,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = METAL_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = METAL_ROUGHNESS

    # ─────────────────────────────────────────────────────────────────
    # 02. Metal Variation — broad colour breakup
    # ─────────────────────────────────────────────────────────────────
    l_var = _add_proc(mat, "02 Metal Variation", "NOISE",
                      opacity=METAL_VAR_OPACITY,
                      blend_mode="OVERLAY",
                      output_channel="BASE_COLOR")
    l_var.proc_scale         = METAL_VAR_SCALE
    l_var.proc_detail        = 12.0
    l_var.proc_roughness_proc = 0.50
    l_var.proc_lacunarity    = 2.0
    l_var.proc_distortion    = 0.0
    l_var.proc_color1 = METAL_COLOR_DARK
    l_var.proc_color2 = METAL_COLOR_LIGHT
    l_var.proc_contrast = 0.30

    # ─────────────────────────────────────────────────────────────────
    # 03. Large Plates — Voronoi DTE Chebychev low-randomness blocky cells
    # ─────────────────────────────────────────────────────────────────
    # Plate seams = thin dark lines where Voronoi DTE = 0 (cell edges).
    # With MULTIPLY blend and color1 = near-black at edges, color2 =
    # WHITE in interiors, the seams darken the underlying metal while
    # cell interiors pass through. Cumulative bump drives the
    # geometric seam grooves.
    l_plates = _add_proc(mat, "03 Large Plates", "VORONOI",
                         opacity=PLATES_OPACITY,
                         blend_mode="MULTIPLY",
                         output_channel="BASE_COLOR")
    l_plates.proc_scale            = PLATES_SCALE
    l_plates.proc_detail           = PLATES_DETAIL
    l_plates.proc_randomness       = PLATES_RANDOMNESS
    l_plates.proc_voronoi_feature  = 'DISTANCE_TO_EDGE'
    l_plates.proc_voronoi_distance = 'CHEBYCHEV'
    l_plates.proc_color1 = PLATES_EDGE_COLOR
    l_plates.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    l_plates.proc_contrast = PLATES_CONTRAST
    l_plates.use_bump      = True
    l_plates.bump_strength = PLATES_BUMP_STRENGTH
    l_plates.bump_distance = PLATES_BUMP_DISTANCE

    # ─────────────────────────────────────────────────────────────────
    # 04. Small Details — fine Voronoi cracks at higher scale
    # ─────────────────────────────────────────────────────────────────
    l_details = _add_proc(mat, "04 Small Details", "VORONOI",
                          opacity=DETAILS_OPACITY,
                          blend_mode="MULTIPLY",
                          output_channel="BASE_COLOR")
    l_details.proc_scale            = DETAILS_SCALE
    l_details.proc_detail           = 1.5
    l_details.proc_randomness       = DETAILS_RANDOMNESS
    l_details.proc_voronoi_feature  = 'DISTANCE_TO_EDGE'
    l_details.proc_voronoi_distance = 'CHEBYCHEV'
    l_details.proc_color1 = DETAILS_CRACK_COLOR
    l_details.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    l_details.proc_contrast = DETAILS_CONTRAST
    l_details.use_bump      = True
    l_details.bump_strength = DETAILS_BUMP_STRENGTH
    l_details.bump_distance = DETAILS_BUMP_DISTANCE

    # ─────────────────────────────────────────────────────────────────
    # 05. Damage Wear — Noise → ROUGHNESS only (no colour change)
    # ─────────────────────────────────────────────────────────────────
    l_wear = _add_proc(mat, "05 Damage Wear", "NOISE",
                       opacity=WEAR_OPACITY,
                       blend_mode="ADD",
                       output_channel="ROUGHNESS")
    l_wear.proc_scale         = WEAR_SCALE
    l_wear.proc_detail        = WEAR_DETAIL
    l_wear.proc_roughness_proc = 0.55
    l_wear.proc_distortion    = 0.20
    l_wear.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_wear.proc_color2 = (WEAR_ROUGHNESS_BOOST,
                          WEAR_ROUGHNESS_BOOST,
                          WEAR_ROUGHNESS_BOOST, 1.0)

    # ─────────────────────────────────────────────────────────────────
    # 06. Emission Cells — VORONOI F1 + Chebychev for cyan cell cores
    # ─────────────────────────────────────────────────────────────────
    # Voronoi F1 outputs distance to nearest cell POINT:
    #   - At cell centres (where the point is): Fac = 0
    #   - At cell edges (far from the point):  Fac = 1 (normalized)
    #
    # Visible colour via ColorRamp:
    #   - color1 at LOW Fac = at cell CENTRES → CYAN ✓
    #   - color2 at HIGH Fac = at cell EDGES → BLACK ✓
    #
    # Emission via TLM's built-in pipeline (compositing.py ~line 3023):
    #   Fac → 1-Fac → ^(1+contrast*8) → SmoothStep(thr, thr+falloff)
    #   - With F1 + invert, mask peaks at cell CENTRES (originally Fac=0)
    #   - proc_emission_threshold controls how big each lit zone is
    #     (higher = smaller lit zone, restricted to deepest cell core)
    #   - proc_emission_falloff controls the soft transition edge
    #
    # No selector_type — the procedural's own pattern IS the emission gate.
    # ADD blend with both procedural colours = BLACK means the layer's
    # base_color contribution is `current + 0 = current` — no change to the
    # underlying metal base. The emission pipeline (separate from base_color
    # mapping) drives the bright cyan via cumulative emission routing.
    l_glow = _add_proc(mat, "06 Emission Cells", "VORONOI",
                       opacity=EMISSION_BASE_OPACITY,
                       blend_mode="ADD",
                       output_channel="BASE_COLOR")
    # v0.19: restored v0.11 — Object coord (default), no GENERATED override.
    l_glow.proc_scale            = EMISSION_CELL_SCALE
    l_glow.proc_detail           = 0.5
    l_glow.proc_randomness       = EMISSION_CELL_RANDOMNESS
    l_glow.proc_voronoi_feature  = 'F1'
    l_glow.proc_voronoi_distance = 'CHEBYCHEV'
    l_glow.proc_voronoi_random_color = True
    l_glow.proc_voronoi_random_seed = 0.0
    l_glow.proc_vector_distortion = EMISSION_VECTOR_DISTORTION
    l_glow.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_glow.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_glow.proc_contrast = EMISSION_CONTRAST
    l_glow.use_emission = True
    l_glow.emission_color = EMISSION_COLOR
    l_glow.emission_strength = EMISSION_STRENGTH
    l_glow.proc_emission_threshold = EMISSION_THRESHOLD
    l_glow.proc_emission_falloff = EMISSION_FALLOFF
    l_glow.emission_selector_type = 'NONE'

    # ── Rebuild & finalise ────────────────────────────────────────────
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    # ─────────────────────────────────────────────────────────────────
    # 07. Cell Outline — thin cyan trim on the LARGE PLATE seams
    # ─────────────────────────────────────────────────────────────────
    # ALIGNMENT CRITICAL: every Voronoi sampling parameter must match
    # layer 03 (Large Plates) so the outline runs on the IDENTICAL
    # cell boundaries — same scale, same randomness, same detail, same
    # distance metric, same coord type. Differences would shift the
    # cells and the cyan outline would float untethered from the seams.
    l_outline = _add_proc(mat, "07 Cell Outline", "VORONOI",
                          opacity=OUTLINE_OPACITY,
                          blend_mode="ADD",
                          output_channel="BASE_COLOR")
    l_outline.proc_scale            = OUTLINE_SCALE       # = PLATES_SCALE
    l_outline.proc_detail           = 0.5                 # v0.19: detail fixed at 0.5
    l_outline.proc_randomness       = OUTLINE_RANDOMNESS  # = PLATES_RANDOMNESS
    l_outline.proc_voronoi_feature  = 'DISTANCE_TO_EDGE'
    l_outline.proc_voronoi_distance = 'CHEBYCHEV'
    l_outline.proc_color1 = OUTLINE_COLOR              # at LOW Fac (cell edges)
    l_outline.proc_color2 = (0.0, 0.0, 0.0, 1.0)       # at HIGH Fac (cell centres)
    l_outline.proc_contrast = OUTLINE_CONTRAST
    l_outline.proc_ramp_center = OUTLINE_RAMP_CENTER   # NEW: band near Fac=0 → thin edge

    print(f"[TLM] Done. 7 layers (RE Sci-Fi Panel v0.11 — thin asymmetric outlines).")
    print("[TLM] No PAINT layers — pure reverse engineering of the reference.")
    print("[TLM] If cell cores too bright: lower EMISSION_BASE_OPACITY.")
    print("[TLM] If too few/many cells: tune EMISSION_CELL_SCALE.\n")
    return mat


if __name__ == "__main__":
    build_scifi_panel()
