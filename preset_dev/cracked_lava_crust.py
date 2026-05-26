"""
TLM Showcase Preset — Cracked Lava Crust
==========================================

100% procedural — NO PAINT layers — cooling lava with bright magma cracks.
Stress-tests three TLM systems at once:
  1. The CRACKS proc (Voronoi DTE thresholded) for visual + bump
  2. Cumulative-routing (one Voronoi-DTE proc visible in base_color AND
     emission, plus a separate CRACKS for bump — all spatially aligned
     because they share scale/randomness)
  3. The emission_threshold pipeline (Invert → Power → SmoothStep) with
     careful threshold/falloff tuning to keep the glow tight to the
     physical crack band.

Visual anatomy:
  - Dark charcoal cooled crust as base
  - CRACKS proc darkens base_color along the fissure (MULTIPLY → black)
  - SAME CRACKS pattern drives BUMP (physical relief at fissures)
  - Voronoi DTE emission layer fires bright orange-red INSIDE the cracks
  - NOISE microbump on the crust for rocky surface texture

Layer stack (6 layers):
  01. Charcoal Crust FILL          — dark cooled lava base (rough, non-metal)
  02. Surface Roughness NOISE      — large-scale color variation (charcoal range)
  03. Cracks Pattern CRACKS proc   — MULTIPLY → cracks darken the base color
  04. Cracks Bump CRACKS proc      — same crack pattern → physical depth via bump
  05. Magma Emission VORONOI-DTE   — bright orange/red emission INSIDE cracks
  06. Crust Microbump NOISE        — rocky surface micro-texture

──────────────────────────────────────────────────────────────────────────────
KEY GOTCHAS DISCOVERED 2026-05-24 (worth documenting in procedural_gotchas.md):
──────────────────────────────────────────────────────────────────────────────

GOTCHA #1 — Emission strength × opacity:
  The BSDF emission strength is `max(layer.emission_strength * layer.opacity)`
  across all emissive layers (compositing.py lines 767, 5624). Setting
  opacity=0 on an emissive layer (to suppress its base_color contribution)
  silently kills the emission strength too. Workaround: keep opacity=1.0
  and zero the base_color contribution by setting proc_color1 = proc_color2 =
  BLACK so the ColorRamp output is always (0,0,0,1).

GOTCHA #2 — CRACKS proc is INCOMPATIBLE with the emission threshold
pipeline:
  CRACKS Fac = 1 INSIDE crack, 0 OUTSIDE. But the emission pipeline does
  `1 - Fac` before SmoothStep — designed for raw Voronoi where the natural
  Fac=0 inside. Feeding CRACKS Fac through the pipeline double-inverts,
  putting emission OUTSIDE the cracks. **Use raw VORONOI with
  DISTANCE_TO_EDGE feature for emission layers.** Cells stay aligned with
  CRACKS layers because the underlying Voronoi node respects the same
  scale/randomness.

GOTCHA #3 — BSDF global emission strength is single-valued:
  Can't have a "bright core + dim halo" via two emissive layers — the
  strength is one number across the whole material. A halo layer's dim
  contribution gets amplified by the bright core's strength → halo
  overwhelms the crust. For dual-zone emission you'd need either a single
  layer with carefully shaped falloff, or hand-built emission node groups
  outside TLM.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Cracked_Lava"
RESOLUTION = "1024"
TARGET_MESH = "TLM_LavaRock"
SUBDIV_LEVEL = 3

# ── Charcoal crust (Layer 01) ──
CRUST_COLOR             = (0.030, 0.025, 0.025, 1.0)   # near-black warm charcoal
CRUST_METALLIC          = 0.0
CRUST_ROUGHNESS         = 0.85                          # very matte rocky

# ── Surface roughness variation (Layer 02) ──
SURF_COLOR_DARKER       = (0.020, 0.018, 0.018, 1.0)
SURF_COLOR_LIGHTER      = (0.060, 0.050, 0.045, 1.0)
SURF_NOISE_SCALE        = 1.5
SURF_OPACITY            = 0.65
SURF_BLEND_MODE         = "OVERLAY"

# ── Cracks pattern (Layer 03) ──
CRACKS_SCALE            = 3.5                           # ~10 cells per face → meaningful crack network
CRACKS_RANDOMNESS       = 0.85                          # organic non-grid
CRACKS_WIDTH            = 0.05                          # thin cracks (5% of cell)
CRACKS_SHARPNESS        = 0.95                          # razor-sharp crack edges
CRACKS_COLOR_BLACK      = (0.0, 0.0, 0.0, 1.0)         # cracks themselves = void black
CRACKS_OPACITY          = 0.85
CRACKS_BLEND_MODE       = "MULTIPLY"

# ── Cracks bump (Layer 04) ──
CRACK_BUMP_STRENGTH     = 1.20                          # deep physical fissures
CRACK_BUMP_DISTANCE     = 0.025

# ── Magma emission (Layer 05) ──
# Voronoi DTE drives emission_threshold pipeline. The pipeline inverts Fac
# internally: high mask at low Fac = near edges. With contrast=0.7 the
# exponent is ~6.6, which sharpens the edge band considerably. The
# threshold/falloff window then chooses how wide the glow bleeds outside
# the physical crack.
MAGMA_COLOR_HOT         = (1.00, 0.25, 0.03, 1.0)      # deep orange-red molten
MAGMA_COLOR_COOL        = (0.65, 0.10, 0.04, 1.0)      # cooler magma edges
MAGMA_EMISSION_STRENGTH = 2.2                           # tuned to keep orange not blow to yellow
MAGMA_EMISSION_THRESHOLD = 0.80                         # very tight — only deepest part of crack
MAGMA_EMISSION_FALLOFF  = 0.10                          # razor falloff
MAGMA_CONTRAST          = 0.85                          # exp≈8.0 razor-narrow edge band

# ── Microbump (Layer 06) ──
MICROBUMP_SCALE         = 40.0
MICROBUMP_STRENGTH      = 0.25
MICROBUMP_DISTANCE      = 0.005


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_mesh():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        if len(obj.data.polygons) < 5000:
            _subdivide(obj)
        return obj
    print(f"[TLM] Creating '{TARGET_MESH}' (subdivided sphere for lava rock)…")
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_ico_sphere_add(radius=1.0, subdivisions=4, location=(0, 0, 1.0))
    obj = bpy.context.active_object
    obj.name = TARGET_MESH
    _subdivide(obj)
    return obj


def _subdivide(obj):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new(name="TLM_Subdiv", type='SUBSURF')
    mod.levels = SUBDIV_LEVEL
    mod.render_levels = SUBDIV_LEVEL
    try:
        with bpy.context.temp_override(active_object=obj, selected_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    try:
        with bpy.context.temp_override(active_object=obj, selected_objects=[obj]):
            bpy.ops.object.shade_smooth()
    except Exception:
        for p in obj.data.polygons:
            p.use_smooth = True


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
    _add_layer_common(bpy.context, "FILL")
    l = mat.tlm.layers[mat.tlm.active_layer_index]
    l.name = name
    l.fill_color = color
    l.opacity = opacity
    l.blend_mode = blend_mode
    l.output_channel = output_channel
    return l


def _add_proc(mat, name, proc_type, opacity=1.0, blend_mode="MIX",
              output_channel="BASE_COLOR"):
    _add_layer_common(bpy.context, "PROCEDURAL")
    l = mat.tlm.layers[mat.tlm.active_layer_index]
    l.name = name
    l.proc_type = proc_type
    l.opacity = opacity
    l.blend_mode = blend_mode
    l.output_channel = output_channel
    l.proc_coord_type = "OBJECT"
    return l


def _ensure_cycles():
    scene = bpy.context.scene
    if scene.render.engine != 'CYCLES':
        scene.render.engine = 'CYCLES'
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for sp in area.spaces:
                if sp.type == 'VIEW_3D' and sp.shading.type == 'SOLID':
                    sp.shading.type = 'MATERIAL'


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_cracked_lava():
    _ensure_cycles()
    obj = _find_or_create_mesh()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Cracked Lava Crust v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Charcoal Crust ───
    l_base = _add_fill(mat, "01 Charcoal Crust", CRUST_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = CRUST_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = CRUST_ROUGHNESS

    # ─── 02. Surface Variation ───
    l_var = _add_proc(mat, "02 Surface Variation", "NOISE",
                      opacity=SURF_OPACITY,
                      blend_mode=SURF_BLEND_MODE,
                      output_channel="BASE_COLOR")
    l_var.proc_scale = SURF_NOISE_SCALE
    l_var.proc_detail = 10.0
    l_var.proc_roughness_proc = 0.55
    l_var.proc_color1 = SURF_COLOR_DARKER
    l_var.proc_color2 = SURF_COLOR_LIGHTER
    l_var.proc_contrast = 0.35

    # ─── 03. Cracks Pattern (CRACKS proc) ───
    # Cracks colour BLACK over the crust → fissures look "deeper"/darker than the surface
    l_cracks = _add_proc(mat, "03 Cracks Pattern", "CRACKS",
                         opacity=CRACKS_OPACITY,
                         blend_mode=CRACKS_BLEND_MODE,
                         output_channel="BASE_COLOR")
    l_cracks.proc_scale = CRACKS_SCALE
    l_cracks.proc_randomness = CRACKS_RANDOMNESS
    l_cracks.proc_cracks_width = CRACKS_WIDTH
    l_cracks.proc_cracks_sharpness = CRACKS_SHARPNESS
    l_cracks.proc_color1 = CRACKS_COLOR_BLACK    # cracks = pure black void
    l_cracks.proc_color2 = (1.0, 1.0, 1.0, 1.0)  # crust pass-through (MULTIPLY white = no-op)
    l_cracks.proc_contrast = 0.85

    # ─── 04. Cracks Bump ───
    # SAME CRACKS pattern drives bump → physical depth at fissures
    l_bump = _add_proc(mat, "04 Cracks Bump", "CRACKS",
                       opacity=0.0,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_bump.proc_scale = CRACKS_SCALE
    l_bump.proc_randomness = CRACKS_RANDOMNESS
    l_bump.proc_cracks_width = CRACKS_WIDTH
    l_bump.proc_cracks_sharpness = CRACKS_SHARPNESS
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_contrast = 0.85
    l_bump.use_bump = True
    l_bump.bump_strength = CRACK_BUMP_STRENGTH
    l_bump.bump_distance = CRACK_BUMP_DISTANCE

    # ─── 05. Magma Emission (Voronoi DTE drives the glow) ───
    #
    # CRITICAL: We use raw VORONOI with DISTANCE_TO_EDGE here, NOT the CRACKS
    # proc. Why? CRACKS pre-thresholds the Voronoi DTE so that Fac=1 INSIDE
    # the crack and Fac=0 outside. But the emission pipeline does its OWN
    # invert (1-Fac) before SmoothStep — designed for raw Voronoi where
    # Fac=0 inside is the natural "centre". Feeding CRACKS Fac (already
    # inverted) through the pipeline double-inverts and puts emission
    # OUTSIDE the cracks. Using raw Voronoi DTE here lets the pipeline put
    # emission AT EDGES (= at crack lines) where we want it. The cells
    # align spatially with Layer 03 (CRACKS) and Layer 04 (CRACKS Bump)
    # because we match scale/randomness/distortion.
    # IMPORTANT — opacity=1.0 is REQUIRED for emission to fire.
    # Inside compositing.py the BSDF emission strength is
    # `max(layer.emission_strength * layer.opacity)` across emissive
    # layers, so opacity=0 silently zeros the magma out. To prevent the
    # layer from contributing to base_color anyway we set both procedural
    # colours to BLACK — the ColorRamp output is always (0,0,0,1) so
    # whatever blend_mode we use on base_color is a no-op. Emission is
    # driven INDEPENDENTLY by the emission_threshold pipeline + the
    # `emission_color` property below.
    l_magma = _add_proc(mat, "05 Magma Emission", "VORONOI",
                        opacity=1.0,
                        blend_mode="MIX",
                        output_channel="BASE_COLOR")
    l_magma.proc_voronoi_feature = "DISTANCE_TO_EDGE"
    l_magma.proc_voronoi_distance = "EUCLIDEAN"
    l_magma.proc_scale = CRACKS_SCALE
    l_magma.proc_randomness = CRACKS_RANDOMNESS
    l_magma.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_magma.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_magma.proc_contrast = MAGMA_CONTRAST
    l_magma.use_emission = True
    l_magma.emission_color = MAGMA_COLOR_HOT
    l_magma.emission_strength = MAGMA_EMISSION_STRENGTH
    l_magma.proc_emission_threshold = MAGMA_EMISSION_THRESHOLD
    l_magma.proc_emission_falloff = MAGMA_EMISSION_FALLOFF

    # NOTE: a "halo" layer was tried (second emission pass with wider band +
    # cooler colour) but the BSDF emission strength is global (single max
    # value across all emissive layers), so the halo's dim contribution got
    # amplified by the brighter core's strength and overwhelmed the crust.
    # The proper "heat radiance" trick is to drive base_color separately
    # via Layer 03's MULTIPLY pattern + the bump physical depth — these
    # already give the cracks a visible "heated rock" appearance without
    # touching emission.

    # ─── 06. Microbump ───
    l_mb = _add_proc(mat, "06 Microbump", "NOISE",
                     opacity=0.0,
                     blend_mode="MIX",
                     output_channel="BASE_COLOR")
    l_mb.proc_scale = MICROBUMP_SCALE
    l_mb.proc_detail = 5.0
    l_mb.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_mb.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_mb.proc_contrast = 0.55
    l_mb.use_bump = True
    l_mb.bump_strength = MICROBUMP_STRENGTH
    l_mb.bump_distance = MICROBUMP_DISTANCE

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Cracked Lava Crust built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_cracked_lava()
