"""
TLM Showcase Preset — Carbon Fiber 3K (v0.1 — STRIPES architecture)
=====================================================================

Goal: the iconic carbon-fiber twill weave every gearhead recognises.
Glossy clearcoat finish, deep blacks, sharp regular threads in two
perpendicular passes.

Architecture pivot from v0.1 GABOR-based attempt
-------------------------------------------------
Initial v0.1 tried two perpendicular Gabor procedurals for the threads.
Gabor turned out to be the wrong primitive: it generates *wavelet
packets* — localised regions of oscillation — that read as
"concentric eyes" at any scale we tested (3 iterations, same result).
Carbon fiber is the opposite of wavelet noise: it's perfectly
periodic. The right primitive for "uniform parallel bands at any
angle" is STRIPES (Wave-bands underneath).

Gabor stays in TLM's catalogue for materials where the wavelet
randomness IS the feature: brushed metal scratches, velvet fiber
variation, hair tufts.

v0.3 = GLOSSY CLEARCOAT FINISH
--------------------------------
Architecture inherited from v0.2.1 (Carbon Base + DIAGONAL stripes
with BUMP). Two changes to land the dark glossy clearcoat look:

1. CARBON_ROUGHNESS dropped to 0.08 — proper clearcoat gloss. The
   bump from v0.2.1 still creates per-rib normal variation, so each
   thread catches its own sharp highlight at low roughness.

2. + Fresnel Edge Sheen layer: a tiny FILL with a slightly cooler
   colour masked by Fresnel. Shows only at glancing angles → rim
   highlight that simulates the "wet shine" of clearcoat resin.

  v0.1  → 2 perpendicular STRIPES (LIGHTEN)              [PLAIN WEAVE, wrong target]
  v0.2  → + diagonal overlay                              [PERFORATED LOOK, wrong]
  v0.2.1 → Pivot: DIAGONAL only + BUMP                    [TWILL VISIBLE, right]
  v0.3  → + Glossy clearcoat (low rough + Fresnel sheen)  [WHERE WE ARE]
  v0.4  → + Macro variation (clearcoat dust spots)
  v1.0  → final polish, save .tlm

Target reference (Tier-A glossy clearcoat aesthetic):
  https://fatcarbonmaterials.com/shop/carbon-fiber-twill-weave-3k/

Iterating v0.1:
  1. Open _preview_scene.blend.
  2. Run this script (Text Editor → Run Script).
  3. Render in Cycles 256 samples (Rendered viewport mode).
  4. Look at the sphere — should read as "woven carbon" not
     "concrete" or "fabric". If not, tune TUNABLES below ONE AT A
     TIME and re-run.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Carbon_Fiber_3K"
RESOLUTION = "1024"

# Which mesh to apply the carbon to. With WEAVE_COORD_TYPE='UV' the
# weave wraps cleanly around any well-unwrapped mesh — sphere, cube
# or panel all look like real carbon-cloth. Default to TLM_Sphere
# because it's the canonical hero subject.
TARGET_MESH = "TLM_Sphere"   # one of: TLM_Sphere / TLM_Cube / TLM_Panel

# ── Carbon Base ──
# Deep near-black, slight blue tint (graphite vs jet-black). Real
# carbon fiber under clearcoat reflects almost nothing diffuse, hence
# very dark base color. Metallic ~0.05 is a tiny hint of conductivity
# (carbon is mildly conductive); roughness very low for the clearcoat
# gloss.
CARBON_BASE_COLOR   = (0.020, 0.020, 0.025, 1.0)
CARBON_METALLIC     = 0.05
CARBON_ROUGHNESS    = 0.08   # clearcoat-like — high gloss

# ── Cross-fiber micro-detail (v0.3.1) ──
# Real carbon fiber 3K has visible cross-thread fibers within each
# diagonal rib — the "bundles" perpendicular to the main twill.
# STRIPES X (vertical stripes) at fine scale crosses the +45° main
# diagonal at 45°, producing the impression of cross-thread bundles.
# Low opacity LIGHTEN so it only brightens slightly — too much and
# we lose the dominant diagonal twill direction.
CROSS_FIBER_SCALE       = 90.0   # fine fibers (~90 across the unwrap)
CROSS_FIBER_DIRECTION   = 'X'    # vertical stripes — cross the +45° diagonal
CROSS_FIBER_WIDTH       = 0.50   # equal bright/dark splits
CROSS_FIBER_SHARPNESS   = 0.30   # soft edges so fibers blur into ribs
CROSS_FIBER_OPACITY     = 0.20   # subtle — accent, not dominant

# ── Fresnel Edge Sheen (v0.3) ──
# A tiny brightening layer masked by Fresnel — only visible at
# glancing angles (sphere rim, panel grazing edges). Simulates the
# "wet shine" of clearcoat resin where the camera-facing reflectance
# of the dark base is very low, but the edge reflectance approaches 1.
# A subtle cool-tinted colour reads as "clearcoat catching the sky".
FRESNEL_SHEEN_COLOR     = (0.65, 0.70, 0.80, 1.0)   # cool desaturated blue-grey
FRESNEL_SHEEN_OPACITY   = 0.12   # very subtle — only kicks in clearly at the rim
FRESNEL_SHEEN_IOR       = 1.80   # higher IOR = narrower rim (camera-facing stays dark)
FRESNEL_SHEEN_STRENGTH  = 1.00   # full Fresnel falloff

# ── Twill (DIAGONAL stripes — primary pattern) ──
# A single DIAGONAL stripe layer is enough to read as 3K twill:
# - Real carbon 3K twill weave looks like continuous diagonal ribs,
#   not a perpendicular grid. The over-2-under-2 weaving produces
#   one dominant diagonal direction.
# - TWILL_WIDTH at ~0.5 gives equal bright-thread + dark-trough
#   spacing for the classic ribbed look.
# - TWILL_SHARPNESS high (0.8+) for crisp thread edges visible.
# - Routed ALSO to BUMP channel below for thread depth — same pattern
#   drives both colour and height, so the bright ribs literally rise
#   above the dark grooves. That's where the "3D fabric" feel comes
#   from in the reference image.
TWILL_SCALE             = 22.0   # diagonal threads visible across the unwrap
TWILL_WIDTH             = 0.55   # bright thread fraction of each period
TWILL_SHARPNESS         = 0.80   # high contrast thread edges
TWILL_OPACITY           = 1.00   # primary visible pattern — full opacity
TWILL_BUMP_STRENGTH     = 0.55   # 0=flat, 1+=strong relief; 0.55 = visible at glossy
TWILL_BUMP_DISTANCE     = 0.025  # height of thread above trough

# Coordinate space for the weave projection.
# 'UV'     = use the UV unwrap → on a well-unwrapped sphere this
#           gives the proper "wrapped flat fabric" look — what we
#           want for carbon fiber.
# 'OBJECT' = use 3D object-space position → stripes are slicing planes
#           through the mesh; on a sphere these read as concentric arcs.
# 'GENERATED' = like OBJECT but normalised to bbox.
WEAVE_COORD_TYPE        = 'UV'

# Two thread colors — bright threads vs deep trough.
# v0.3 boosted contrast: trough pushed to near-pure-black so it reads
# as deep carbon, thread highlight raised to give the bright "rib top"
# that catches the clearcoat specular at low roughness.
WEAVE_COLOR_DARK    = (0.005, 0.005, 0.008, 1.0)  # near-pure-black trough
WEAVE_COLOR_LIGHT   = (0.250, 0.250, 0.275, 1.0)  # bright thread highlight



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


# ─── BUILD ────────────────────────────────────────────────────────────────────

def _find_target_mesh():
    """Find the mesh to apply the material to, with sensible fallbacks.

    Priority:
      1. The named TARGET_MESH from TUNABLES (typically TLM_Panel for
         carbon-fiber validation, since it's flat).
      2. Active object if it's a mesh.
      3. First mesh object in the scene that isn't a TLM_Ground.

    This way the script "just works" regardless of what's currently
    selected in Blender — camera, light, or nothing — so we don't
    keep hitting "Active object is not a mesh" between iterations.
    """
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


def build_carbon_fiber_3k():
    obj = _find_target_mesh()
    if obj is None:
        raise RuntimeError(
            "No mesh found in scene. Add a mesh (or run _preview_scene.py "
            "first to generate TLM_Sphere)."
        )

    # Make sure the chosen mesh is active — _add_layer_common uses the
    # active object's material.
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Carbon Fiber 3K v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False  # silent build, single rebuild at end

    _clear_layers(mat)

    # ─────────────────────────────────────────────────────────────────
    # 1. Carbon Base — deep near-black with low roughness clearcoat
    # ─────────────────────────────────────────────────────────────────
    # The "stage" the weave threads sit on. Metallic ~0 keeps it
    # dielectric (carbon under clearcoat IS dielectric for the eye),
    # roughness near zero gives the glossy reflection that defines
    # the "polished panel" look.
    l_base = _add_fill(mat, "Carbon Base", CARBON_BASE_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_roughness = True
    l_base.roughness_fill = CARBON_ROUGHNESS
    l_base.use_metallic = True
    l_base.metallic_fill = CARBON_METALLIC

    # ─────────────────────────────────────────────────────────────────
    # 2. Twill Threads — DIAGONAL STRIPES, primary pattern + BUMP depth
    # ─────────────────────────────────────────────────────────────────
    # A single DIAGONAL stripe layer is what real 3K twill actually
    # looks like: continuous diagonal ribs at ~45°, not a perpendicular
    # grid. The cumulative routing on the same layer also drives the
    # BUMP channel so the bright ribs physically rise above the dark
    # troughs — that's the "3D fabric" feel of the reference image.
    # We use LIGHTEN so the dark trough of the stripe is absorbed by
    # the carbon base (preserves the near-black background) and only
    # the bright thread reads against the base.
    l_twill = _add_procedural(mat, "Twill Threads", "STRIPES",
                              opacity=TWILL_OPACITY,
                              blend_mode="LIGHTEN",
                              output_channel="BASE_COLOR")
    l_twill.proc_scale            = TWILL_SCALE
    l_twill.proc_coord_type       = WEAVE_COORD_TYPE
    l_twill.proc_stripe_direction = 'DIAGONAL'
    l_twill.proc_stripe_width     = TWILL_WIDTH
    l_twill.proc_stripe_sharpness = TWILL_SHARPNESS
    l_twill.proc_color1 = WEAVE_COLOR_DARK
    l_twill.proc_color2 = WEAVE_COLOR_LIGHT
    # Cumulative routing: same diagonal pattern → BUMP for thread relief.
    l_twill.use_bump        = True
    l_twill.bump_strength   = TWILL_BUMP_STRENGTH
    l_twill.bump_distance   = TWILL_BUMP_DISTANCE

    # ─────────────────────────────────────────────────────────────────
    # 3. Cross-Fiber Detail — fine perpendicular stripes (v0.3.1)
    # ─────────────────────────────────────────────────────────────────
    # Add the cross-thread fibers visible within each rib of real 3K
    # twill. Uses STRIPES X (vertical) at high scale → crosses the
    # main +45° diagonal at 45° angle, looking like the bundles of
    # fibers running perpendicular to the dominant weave direction.
    # LIGHTEN at low opacity so it accents without overwhelming the
    # twill identity.
    l_fiber = _add_procedural(mat, "Cross-Fiber Detail", "STRIPES",
                              opacity=CROSS_FIBER_OPACITY,
                              blend_mode="LIGHTEN",
                              output_channel="BASE_COLOR")
    l_fiber.proc_scale            = CROSS_FIBER_SCALE
    l_fiber.proc_coord_type       = WEAVE_COORD_TYPE
    l_fiber.proc_stripe_direction = CROSS_FIBER_DIRECTION
    l_fiber.proc_stripe_width     = CROSS_FIBER_WIDTH
    l_fiber.proc_stripe_sharpness = CROSS_FIBER_SHARPNESS
    l_fiber.proc_color1 = WEAVE_COLOR_DARK
    l_fiber.proc_color2 = WEAVE_COLOR_LIGHT

    # ─────────────────────────────────────────────────────────────────
    # 4. Fresnel Edge Sheen — clearcoat "wet shine" on glancing angles
    # ─────────────────────────────────────────────────────────────────
    # A faint cool-tinted FILL masked by Fresnel. Camera-facing pixels
    # see ~zero contribution (dark base wins); edge/glancing pixels
    # see this layer at full opacity → rim brightening that mimics the
    # specular reflection of a clearcoat resin layer.
    l_sheen = _add_fill(mat, "Fresnel Edge Sheen", FRESNEL_SHEEN_COLOR,
                        opacity=FRESNEL_SHEEN_OPACITY,
                        blend_mode="SCREEN",
                        output_channel="BASE_COLOR")
    l_sheen.use_fresnel_mask = True
    l_sheen.fresnel_ior      = FRESNEL_SHEEN_IOR
    l_sheen.fresnel_strength = FRESNEL_SHEEN_STRENGTH

    # ── Rebuild & finalise ────────────────────────────────────────────
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Done. 4 layers (v0.3.1 + cross-fiber detail).\n")
    return mat


# Allow running from Text Editor's "Run Script"
if __name__ == "__main__":
    build_carbon_fiber_3k()
