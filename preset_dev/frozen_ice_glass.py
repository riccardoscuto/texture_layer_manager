"""
TLM Showcase Preset — Frozen Ice Glass (v2 — reverse-engineered)
==================================================================

100% procedural — NO PAINT layers — translucent blue-tinted ice with
cloudy volumetric internal structure (NO sharp CRACKS — those read as
glass shards, not real ice). Reverse-engineered from the user's
reference node group:

    Group Input → NOISE 1 → ColorRamp → Bright/Contrast → BSDF.Base Color
                  NOISE 2 → ColorRamp → Bump (height) → BSDF.Normal
                                                       → BSDF.IOR = 1.31

The "frosted ice not clear glass" effect is achieved by:
  - HIGH transmission (0.92) so light passes through
  - BUT base_color carries cloudy NOISE variation, so refracted rays
    pick up subtle gray-blue tints through the volume → "fog inside"
  - Two noise scales (large macroclouds + finer detail) layered via
    OVERLAY blend, both with custom multi-stop ColorRamp gradients
  - Low overall roughness (0.05) for sharp surface reflections
  - **mat.tlm.bsdf_ior = 1.31** (ice physical IOR) for proper refraction
  - Microbump for "frozen surface micro-pits" texture

This preset showcases the recently-added features:
  - mat.tlm.bsdf_ior (material-level IOR)
  - proc_extra_color_stops collection (multi-color cloud gradient)
  - proc_use_manual_stops (precise control over ColorRamp positions)
  - use_transmission + transmission_fill (fixed hot-update bug 8q)

Layer stack (5 layers):
  01. Ice Body FILL                  — translucent base, IOR drives at mat level
  02. Cloud Macroscale NOISE         — large cloudy color variation
  03. Cloud Detail NOISE (OVERLAY)   — finer noise on top
  04. Frost Roughness NOISE          → ROUGHNESS — non-uniform roughness
  05. Surface Microbump NOISE        → BUMP — frost feel (opacity=0!)

⚠ Engine: Cycles required for proper refraction.
⚠ Set the mesh smooth-shaded.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Frozen_Ice_Glass"
RESOLUTION = "1024"
TARGET_MESH = "TLM_IceBlock"
SUBDIV_LEVEL = 4

# ── Ice body (FILL) ──
# Real ice is TRANSPARENT with subtle absorption. The "internal cloudy"
# look in the reference comes from base_color NOISE shifting subtly +
# HIGH transmission letting light pass through with the tinted refraction.
# NOT from baked-in "frost" diffuse texture (that's the trap I fell into).
ICE_BODY_COLOR         = (0.700, 0.840, 0.940, 1.0)      # mid cyan-blue (body shows through)
ICE_TRANSMISSION       = 0.50                            # MID — body colour stays visible, no clear see-through
ICE_ROUGHNESS          = 0.08                            # low — sharp reflections
ICE_METALLIC           = 0.0
ICE_IOR                = 1.31                            # ice physical IOR

# ── Cloud macroscale (NOISE — large internal cloudy variation) ──
# Reference architecture: ONE noise at MEDIUM scale, LOW detail, MILD
# distortion. The trick is LOW detail (5-7, not 14) so the noise has
# SOFT round patches, not grainy frost. ColorRamp is gentle (close
# tones) so the variation reads as "subtle volumetric depth" not
# "painted frost stripes".
CLOUD_MACRO_SCALE      = 3.0                             # medium patches
CLOUD_MACRO_DETAIL     = 8.0                             # medium-high detail for organic texture
CLOUD_MACRO_ROUGH      = 0.60
CLOUD_MACRO_DISTORT    = 0.80                            # organic warp
CLOUD_MACRO_OPACITY    = 0.85                            # strong cloud contribution
CLOUD_MACRO_BLEND      = "MIX"
# HIGH CONTRAST cloud colors — dark blue ↔ bright white. This creates
# the visible "internal cloudy patches" the reference shows.
CLOUD_MACRO_C1         = (0.300, 0.510, 0.700, 1.0)      # MUCH darker blue (deep zones)
CLOUD_MACRO_C2         = (0.980, 0.995, 1.000, 1.0)      # bright white (highlights / clear veins)
CLOUD_MACRO_C3         = (0.700, 0.840, 0.940, 1.0)      # mid blue (transitions)
CLOUD_MACRO_C3_POS     = 0.50
# Manual stops: MID width band → noticeable contrast without harsh edges.
CLOUD_MACRO_POS1       = 0.25
CLOUD_MACRO_POS2       = 0.75

# ── Cloud detail (NOISE OVERLAY — fine variation) ──
# Smaller scale + lower opacity for very subtle micro-variation.
CLOUD_DETAIL_SCALE     = 7.0
CLOUD_DETAIL_DETAIL    = 6.0
CLOUD_DETAIL_ROUGH     = 0.55
CLOUD_DETAIL_DISTORT   = 0.20
CLOUD_DETAIL_OPACITY   = 0.18                            # very subtle
CLOUD_DETAIL_BLEND     = "OVERLAY"
CLOUD_DETAIL_C1        = (0.700, 0.860, 0.940, 1.0)
CLOUD_DETAIL_C2        = (0.950, 0.985, 1.000, 1.0)

# ── Frost roughness (NOISE → ROUGHNESS, PRIMARY) ──
# Very subtle roughness variation — most of the surface is mirror-clear.
ROUGH_LO               = 0.03
ROUGH_HI               = 0.10
ROUGH_NOISE_SCALE      = 2.0
ROUGH_CONTRAST         = 0.30

# ── Surface microbump ──
# Almost NONE — the reference has clean ice surface, no orange-peel frost.
BUMP_SCALE             = 50.0
BUMP_STRENGTH          = 0.05
BUMP_DISTANCE          = 0.0005


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_mesh():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        return obj
    print(f"[TLM] Creating '{TARGET_MESH}' (subdivided ico sphere)…")
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

def build_frozen_ice_glass():
    _ensure_cycles()
    obj = _find_or_create_mesh()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Frozen Ice Glass v2 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Ice Body ───
    # Single FILL — colour, metallic, roughness, transmission.
    # IOR is now MATERIAL-level (mat.tlm.bsdf_ior), set below after the build.
    l_ice = _add_fill(mat, "01 Ice Body", ICE_BODY_COLOR,
                      opacity=1.0, output_channel="BASE_COLOR")
    l_ice.use_metallic = True
    l_ice.metallic_fill = ICE_METALLIC
    l_ice.use_roughness = True
    l_ice.roughness_fill = ICE_ROUGHNESS
    l_ice.use_transmission = True
    l_ice.transmission_fill = ICE_TRANSMISSION

    # ─── 02. Cloud Macroscale (NOISE, large internal clouds) ───
    # Uses the new multi-color stops system: Color1 dark, Color3 mid,
    # Color2 light. Manual Stops with custom positions for full control
    # of the gradient. With OBJECT coord, the noise is anchored to the
    # mesh — rotating the camera doesn't change the cloud pattern.
    l_macro = _add_proc(mat, "02 Cloud Macroscale", "NOISE",
                        opacity=CLOUD_MACRO_OPACITY,
                        blend_mode=CLOUD_MACRO_BLEND,
                        output_channel="BASE_COLOR")
    l_macro.proc_scale = CLOUD_MACRO_SCALE
    l_macro.proc_detail = CLOUD_MACRO_DETAIL
    l_macro.proc_roughness_proc = CLOUD_MACRO_ROUGH
    l_macro.proc_distortion = CLOUD_MACRO_DISTORT
    l_macro.proc_color1 = CLOUD_MACRO_C1
    l_macro.proc_color2 = CLOUD_MACRO_C2
    # Add Color3 (rose) as an extra stop via the new collection
    extra = l_macro.proc_extra_color_stops.add()
    extra.color = CLOUD_MACRO_C3
    extra.position = CLOUD_MACRO_C3_POS
    # Manual stops for precise positioning
    l_macro.proc_use_manual_stops = True
    l_macro.proc_color1_position = CLOUD_MACRO_POS1
    l_macro.proc_color2_position = CLOUD_MACRO_POS2

    # ─── 03. Cloud Detail (NOISE OVERLAY) ───
    # Finer noise scale for subtle micro-texture inside the ice volume.
    l_detail = _add_proc(mat, "03 Cloud Detail", "NOISE",
                         opacity=CLOUD_DETAIL_OPACITY,
                         blend_mode=CLOUD_DETAIL_BLEND,
                         output_channel="BASE_COLOR")
    l_detail.proc_scale = CLOUD_DETAIL_SCALE
    l_detail.proc_detail = CLOUD_DETAIL_DETAIL
    l_detail.proc_roughness_proc = CLOUD_DETAIL_ROUGH
    l_detail.proc_distortion = CLOUD_DETAIL_DISTORT
    l_detail.proc_color1 = CLOUD_DETAIL_C1
    l_detail.proc_color2 = CLOUD_DETAIL_C2
    l_detail.proc_contrast = 0.30

    # ─── 04. Frost Roughness (NOISE → ROUGHNESS, PRIMARY) ───
    l_rough = _add_proc(mat, "04 Frost Roughness", "NOISE",
                        opacity=1.0,
                        blend_mode="MIX",
                        output_channel="ROUGHNESS")
    l_rough.proc_scale = ROUGH_NOISE_SCALE
    l_rough.proc_detail = 6.0
    l_rough.proc_roughness_proc = 0.6
    l_rough.proc_color1 = (ROUGH_LO, ROUGH_LO, ROUGH_LO, 1.0)
    l_rough.proc_color2 = (ROUGH_HI, ROUGH_HI, ROUGH_HI, 1.0)
    l_rough.proc_contrast = ROUGH_CONTRAST

    # ─── 05. Surface Microbump (opacity=0 to keep BLACK out of base_color) ───
    l_bump = _add_proc(mat, "05 Surface Microbump", "NOISE",
                       opacity=0.0,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_bump.proc_scale = BUMP_SCALE
    l_bump.proc_detail = 6.0
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_contrast = 0.45
    l_bump.use_bump = True
    l_bump.bump_strength = BUMP_STRENGTH
    l_bump.bump_distance = BUMP_DISTANCE

    # Set the material-level IOR BEFORE rebuild — rebuild reads
    # mat.tlm.bsdf_ior and applies it to BSDF.IOR.
    tlm.bsdf_ior = ICE_IOR

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Frozen Ice Glass v2 built — {len(tlm.layers)} layers, IOR={ICE_IOR}")
    return mat


if __name__ == "__main__":
    build_frozen_ice_glass()
