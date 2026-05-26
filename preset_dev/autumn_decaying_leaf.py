"""
TLM Showcase Preset — Autumn Decaying Leaf (ALPHA channel showcase)
====================================================================

Hero E in the showcase rotation. Stress-tests TLM's ALPHA channel
pipeline — the 41 math operations that REPLACE artistic blend modes
when a layer is routed to alpha. Specifically uses:

  • output_channel='ALPHA' on two layers to cut "decay holes" + sfraying
  • alpha_math_operation='MULTIPLY' (the "intersect" semantic): current
    alpha gets multiplied by the layer's alpha map → black regions
    of the layer punch through to fully transparent
  • Smart mask EDGE_WEAR layered over the alpha cutout → the edge
    fraying only fires where the leaf surface curves (mesh edges)
  • Multiple proc layers for the visible "skin" of the leaf:
    base colour, autumn warmth variation, vein pattern, vein bump

The visual: a warm yellow/orange autumn leaf with realistic decay
holes naturally distributed by Perlin noise + frayed rough edges
where weathering has been most aggressive.

Renders best on a flat subdivided Plane (TLM_Leaf created automatically
if missing). For a curved drape look, apply a slight wave/cloth
simulation to the Plane before running this preset.

Layer stack (7 layers, bottom-to-top):
  01. Leaf Base Color FILL      — warm yellow, base alpha = 1
  02. Autumn Warmth NOISE       — orange/red OVERLAY for variation
  03. Vein Color VORONOI F1     — dark vein lines via MULTIPLY blend
  04. Vein Bump VORONOI F1      — cumulative bump on the veins
  05. Decay Holes NOISE → ALPHA — punches transparent holes (MULTIPLY math)
  06. Edge Fray NOISE → ALPHA   — punches the leaf perimeter (MULTIPLY math + EDGE_WEAR mask)
  07. Subsurface Tint FILL      — warm under-glow (very low opacity)
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Autumn_Leaf"
RESOLUTION = "1024"
TARGET_MESH = "TLM_Leaf"
LEAF_SUBDIV = 5     # subdivide the plane heavily so the alpha cutout
                    # gives smooth edges, not jagged polygon-edges

# ── Base colour (Layer 01) ──
LEAF_BASE_COLOR     = (0.620, 0.380, 0.090, 1.0)   # warm autumn yellow-orange
LEAF_ROUGHNESS      = 0.78                          # leaves are matte
LEAF_METALLIC       = 0.0

# ── Autumn warmth variation (Layer 02) ──
WARMTH_COLOR_RED    = (0.520, 0.150, 0.040, 1.0)   # rust red
WARMTH_COLOR_GOLD   = (0.780, 0.540, 0.140, 1.0)   # bright gold-yellow
WARMTH_NOISE_SCALE  = 1.2
WARMTH_OPACITY      = 0.55
WARMTH_BLEND_MODE   = "OVERLAY"

# ── Vein pattern (Layer 03 = color, Layer 04 = bump) ──
# Use VORONOI F1 with high randomness → organic branching pattern
# similar to real leaf venation. DTE feature for "lines along cell edges".
VEIN_COLOR_DARK     = (0.140, 0.060, 0.020, 1.0)   # near-black brown
VEIN_VORONOI_SCALE  = 6.0
VEIN_RANDOMNESS     = 0.85
VEIN_CONTRAST       = 0.92                          # very sharp lines
VEIN_OPACITY        = 0.70
VEIN_BUMP_STRENGTH  = 0.35
VEIN_BUMP_DISTANCE  = 0.005

# ── Decay holes (Layer 05) — ALPHA cutout ──
# A Perlin noise pattern with sharp threshold acts as a binary alpha mask.
# Output channel = ALPHA. Math op = MULTIPLY (intersect): current_alpha
# * layer_alpha → black noise regions punch the leaf transparent.
HOLES_NOISE_SCALE   = 4.0                           # 4 = a handful of large holes per plane
HOLES_NOISE_DETAIL  = 8.0                           # fractal detail at hole edges
HOLES_CONTRAST      = 0.95                          # near-binary on/off
# Color1/Color2 act as alpha values when routed to ALPHA:
# Color1 channel-1 (R) at LOW fac → 0.0 = transparent
# Color2 R at HIGH fac → 1.0 = opaque
HOLES_TRANSPARENT   = (0.0, 0.0, 0.0, 1.0)          # at low Fac (hole)
HOLES_OPAQUE        = (1.0, 1.0, 1.0, 1.0)          # at high Fac (leaf flesh)

# ── Edge fray (Layer 06) — ALPHA cutout + EDGE_WEAR mask ──
# Where the plane mesh is convex (EDGE_WEAR), apply an additional
# sharper alpha noise that frays the leaf perimeter into a ragged outline.
FRAY_NOISE_SCALE    = 8.0
FRAY_CONTRAST       = 0.85
FRAY_MASK_SHARPNESS = 0.40                          # mid-soft EDGE_WEAR rolloff
FRAY_MASK_BREAKUP   = 0.50

# ── Subsurface tint (Layer 07) ──
SSS_COLOR           = (0.880, 0.540, 0.110, 1.0)
SSS_OPACITY         = 0.20
SSS_BLEND_MODE      = "SOFT_LIGHT"


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_plane():
    """Return TLM_Leaf plane (subdivided & shade-smooth) — create if missing."""
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        if len(obj.data.polygons) < 2000:
            _subdivide(obj)
        return obj

    print(f"[TLM] Creating '{TARGET_MESH}' plane …")
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0, 0, 0.5))
    plane = bpy.context.active_object
    plane.name = TARGET_MESH
    _subdivide(plane)
    return plane


def _subdivide(obj):
    """Subdivide via modifier + apply, with context override for robustness."""
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new(name="TLM_Subdiv", type='SUBSURF')
    mod.levels = LEAF_SUBDIV
    mod.render_levels = LEAF_SUBDIV
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
    print(f"[TLM]   Subdivided to {len(obj.data.polygons)} polys + shade smooth")


def _get_or_create_material(obj, name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    while obj.data.materials:
        obj.data.materials.pop(index=0)
    obj.data.materials.append(mat)
    obj.active_material_index = 0
    # Enable alpha transparency on the material for Blender's compositor
    mat.blend_method = 'BLEND'
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
    try:
        scene.eevee.use_gtao = True
    except AttributeError:
        pass
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for sp in area.spaces:
                if sp.type == 'VIEW_3D' and sp.shading.type == 'SOLID':
                    sp.shading.type = 'MATERIAL'


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_autumn_leaf():
    _ensure_cycles()
    obj = _find_or_create_plane()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Autumn Decaying Leaf v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Leaf Base Color ───
    l_base = _add_fill(mat, "01 Leaf Base", LEAF_BASE_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = LEAF_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = LEAF_ROUGHNESS

    # ─── 02. Autumn Warmth Variation ───
    l_warm = _add_proc(mat, "02 Autumn Warmth", "NOISE",
                       opacity=WARMTH_OPACITY,
                       blend_mode=WARMTH_BLEND_MODE,
                       output_channel="BASE_COLOR")
    l_warm.proc_scale = WARMTH_NOISE_SCALE
    l_warm.proc_detail = 8.0
    l_warm.proc_roughness_proc = 0.55
    l_warm.proc_color1 = WARMTH_COLOR_RED
    l_warm.proc_color2 = WARMTH_COLOR_GOLD
    l_warm.proc_contrast = 0.40

    # ─── 03. Vein Color ───
    # VORONOI DTE = bright at cell edges, dark at centres → reverse: we want
    # DARK lines at edges. So color1 (low fac = edges) = dark, color2 = no-op.
    l_vein = _add_proc(mat, "03 Vein Color", "VORONOI",
                       opacity=VEIN_OPACITY,
                       blend_mode="MULTIPLY",
                       output_channel="BASE_COLOR")
    l_vein.proc_scale = VEIN_VORONOI_SCALE
    l_vein.proc_randomness = VEIN_RANDOMNESS
    l_vein.proc_voronoi_feature = 'DISTANCE_TO_EDGE'
    l_vein.proc_voronoi_distance = 'EUCLIDEAN'
    l_vein.proc_color1 = VEIN_COLOR_DARK            # at vein edges → dark
    l_vein.proc_color2 = (1.0, 1.0, 1.0, 1.0)       # MULTIPLY no-op at cell interiors
    l_vein.proc_contrast = VEIN_CONTRAST
    l_vein.proc_ramp_center = 0.10                   # thin asymmetric vein band

    # ─── 04. Vein Bump ───
    # Same Voronoi parameters → cumulative bump aligned with the vein color
    l_vbump = _add_proc(mat, "04 Vein Bump", "VORONOI",
                        opacity=0.0,                  # no base_color contribution
                        blend_mode="MIX",
                        output_channel="BASE_COLOR")
    l_vbump.proc_scale = VEIN_VORONOI_SCALE
    l_vbump.proc_randomness = VEIN_RANDOMNESS
    l_vbump.proc_voronoi_feature = 'DISTANCE_TO_EDGE'
    l_vbump.proc_voronoi_distance = 'EUCLIDEAN'
    l_vbump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_vbump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_vbump.proc_contrast = VEIN_CONTRAST
    l_vbump.proc_ramp_center = 0.10
    l_vbump.use_bump = True
    l_vbump.bump_strength = VEIN_BUMP_STRENGTH
    l_vbump.bump_distance = VEIN_BUMP_DISTANCE

    # ─── 05. DECAY HOLES → ALPHA ───
    # THE ALPHA SHOWCASE. NOISE with sharp threshold acts as a binary alpha
    # value when routed to output_channel='ALPHA'. Math = MULTIPLY: current
    # alpha (= 1 from base) multiplied by this layer's alpha map → 0 where
    # noise is dark = transparent hole, 1 where noise is bright = leaf solid.
    l_holes = _add_proc(mat, "05 Decay Holes", "NOISE",
                        opacity=1.0,
                        blend_mode="MIX",                # ignored for ALPHA channel
                        output_channel="ALPHA")
    l_holes.proc_scale = HOLES_NOISE_SCALE
    l_holes.proc_detail = HOLES_NOISE_DETAIL
    l_holes.proc_roughness_proc = 0.60
    l_holes.proc_color1 = HOLES_TRANSPARENT
    l_holes.proc_color2 = HOLES_OPAQUE
    l_holes.proc_contrast = HOLES_CONTRAST
    l_holes.alpha_math_operation = 'MULTIPLY'           # the intersect math op

    # ─── 06. EDGE FRAY → ALPHA + EDGE_WEAR mask ───
    # A finer noise + an EDGE_WEAR mask = only the convex perimeter of the
    # plane gets the secondary alpha cutout. Result: leaf edge becomes
    # ragged/torn instead of a clean rectangle border.
    l_fray = _add_proc(mat, "06 Edge Fray", "NOISE",
                       opacity=1.0,
                       blend_mode="MIX",
                       output_channel="ALPHA")
    l_fray.proc_scale = FRAY_NOISE_SCALE
    l_fray.proc_detail = 6.0
    l_fray.proc_roughness_proc = 0.50
    l_fray.proc_color1 = HOLES_TRANSPARENT
    l_fray.proc_color2 = HOLES_OPAQUE
    l_fray.proc_contrast = FRAY_CONTRAST
    l_fray.alpha_math_operation = 'MULTIPLY'
    # Mask = EDGE_WEAR → only on convex perimeter edges
    l_fray.use_mask = True
    l_fray.mask_source = 'EDGE_WEAR'
    l_fray.mask_gen_sharpness = FRAY_MASK_SHARPNESS
    l_fray.mask_gen_breakup = FRAY_MASK_BREAKUP
    l_fray.mask_gen_breakup_scale = 18.0
    l_fray.mask_gen_intensity = 1.30

    # ─── 07. Subsurface Tint ───
    l_sss = _add_fill(mat, "07 Subsurface Tint", SSS_COLOR,
                      opacity=SSS_OPACITY,
                      blend_mode=SSS_BLEND_MODE,
                      output_channel="BASE_COLOR")

    # ── Rebuild & finalise ────────────────────────────────────────────
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Autumn Decaying Leaf built — {len(tlm.layers)} layers")
    print(f"      ALPHA-routed: {sum(1 for l in tlm.layers if l.output_channel == 'ALPHA')}")
    return mat


if __name__ == "__main__":
    build_autumn_leaf()
