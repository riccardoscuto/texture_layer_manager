"""
TLM Showcase Preset — Frozen Ice Glass
========================================

100% procedural — NO PAINT layers — translucent blue-tinted ice block
with internal fractures and frost imperfections. Reference: the #2
material in the user's reference grid (top row) — an ice cube + ice
sphere with refractive transparency, faint blue absorption tint, and
internal cracks visible through the body.

Showcases TLM's TRANSMISSION channel (which routes to BSDF v2 's
"Transmission Weight"). This is the first hero preset to drive the
transmission path:

  1. **`use_transmission = True` + `transmission_fill = 0.92`** on the
     base FILL layer → most light passes through, but a slight
     absorption gives the blue tint.
  2. **CRACKS proc → BASE_COLOR** for internal fractures (the cracks
     darken the tinted base, simulating refractive scatter at the
     fracture surfaces).
  3. **Low roughness (~0.08)** for sharp refractive reflections, with
     a NOISE roughness layer adding subtle "frozen-not-perfect" variation.
  4. **NOISE microbump → BUMP** for the surface frost texture that
     scatters light at glancing angles.

Visual anatomy:
  - Translucent body with light-cyan absorption tint
  - Internal CRACKS proc fractures (slightly darker than the tint)
  - Glossy near-mirror surface with gentle roughness variation
  - Subtle bump texture for "frost feel"
  - Heavy transmission → light passes through, refracts at edges

Layer stack (5 layers):
  01. Ice Body FILL                — light cyan tint, high transmission, low rough
  02. Internal Cracks CRACKS proc  — fracture lines visible inside the body
  03. Cloudy Variation NOISE       → BASE_COLOR — subtle inhomogeneity
  04. Frost Roughness NOISE        → ROUGHNESS — non-uniform roughness for character
  05. Surface Microbump NOISE      → BUMP — frost texture (opacity=0!)

⚠ Renderer: Cycles required for proper refraction. Eevee will show the
transmission but without true refraction physics.
⚠ Set the mesh smooth-shaded — a low-poly facet edges will spoil the
glass look.
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
ICE_TINT               = (0.520, 0.770, 0.920, 1.0)      # light cyan
ICE_TRANSMISSION       = 0.92                            # mostly transmits, slight absorption
ICE_ROUGHNESS          = 0.05                            # near-mirror — clear glass
ICE_METALLIC           = 0.0
ICE_IOR                = 1.31                            # ice IOR

# ── Internal fractures (CRACKS proc) ──
FRACTURE_SCALE         = 1.8                             # sparse big cracks
FRACTURE_RANDOMNESS    = 0.95
FRACTURE_WIDTH         = 0.04                            # thin
FRACTURE_SHARPNESS     = 0.85                            # crisp edges
FRACTURE_TINT_OUTSIDE  = ICE_TINT                        # surface stays tinted
FRACTURE_TINT_INSIDE   = (0.250, 0.380, 0.510, 1.0)      # darker tint in fractures
FRACTURE_OPACITY       = 0.55
FRACTURE_BLEND         = "MIX"

# ── Cloudy variation (NOISE OVERLAY) ──
CLOUD_SCALE            = 1.3                             # big soft clouds
CLOUD_OPACITY          = 0.35
CLOUD_BLEND            = "OVERLAY"
CLOUD_DARK             = (0.250, 0.520, 0.700, 1.0)
CLOUD_LIGHT            = (0.880, 0.960, 1.000, 1.0)

# ── Frost roughness (NOISE → ROUGHNESS) ──
ROUGH_LO               = 0.04                            # mirror-clear regions
ROUGH_HI               = 0.22                            # slightly frosted regions
ROUGH_NOISE_SCALE      = 2.5
ROUGH_CONTRAST         = 0.40

# ── Surface microbump ──
BUMP_SCALE             = 45.0
BUMP_STRENGTH          = 0.18                            # very subtle (we want it CLEAR)
BUMP_DISTANCE          = 0.0015


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

    print(f"\n[TLM] Building Frozen Ice Glass v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Ice Body ───
    # Single FILL with transmission + low roughness. The transmission_fill
    # value is what drives the BSDF Transmission Weight, NOT the alpha.
    l_ice = _add_fill(mat, "01 Ice Body", ICE_TINT,
                      opacity=1.0, output_channel="BASE_COLOR")
    l_ice.use_metallic = True
    l_ice.metallic_fill = ICE_METALLIC
    l_ice.use_roughness = True
    l_ice.roughness_fill = ICE_ROUGHNESS
    l_ice.use_transmission = True
    l_ice.transmission_fill = ICE_TRANSMISSION

    # ─── 02. Internal Fractures (CRACKS → darken in cracks) ───
    l_frac = _add_proc(mat, "02 Internal Fractures", "CRACKS",
                       opacity=FRACTURE_OPACITY,
                       blend_mode=FRACTURE_BLEND,
                       output_channel="BASE_COLOR")
    l_frac.proc_scale = FRACTURE_SCALE
    l_frac.proc_randomness = FRACTURE_RANDOMNESS
    l_frac.proc_cracks_width = FRACTURE_WIDTH
    l_frac.proc_cracks_sharpness = FRACTURE_SHARPNESS
    # CRACKS Fac = 1 INSIDE crack, 0 OUTSIDE. So color2 (Fac=1) = inside fracture.
    l_frac.proc_color1 = FRACTURE_TINT_OUTSIDE
    l_frac.proc_color2 = FRACTURE_TINT_INSIDE
    l_frac.proc_contrast = 0.30

    # ─── 03. Cloudy Variation (NOISE OVERLAY) ───
    l_cloud = _add_proc(mat, "03 Cloudy Variation", "NOISE",
                        opacity=CLOUD_OPACITY,
                        blend_mode=CLOUD_BLEND,
                        output_channel="BASE_COLOR")
    l_cloud.proc_scale = CLOUD_SCALE
    l_cloud.proc_detail = 7.0
    l_cloud.proc_roughness_proc = 0.55
    l_cloud.proc_color1 = CLOUD_DARK
    l_cloud.proc_color2 = CLOUD_LIGHT
    l_cloud.proc_contrast = 0.30

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
    l_bump.proc_detail = 5.0
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_contrast = 0.45
    l_bump.use_bump = True
    l_bump.bump_strength = BUMP_STRENGTH
    l_bump.bump_distance = BUMP_DISTANCE

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    # The BSDF v2 has an "IOR" input — set to ice IOR for proper refraction
    # (TLM doesn't manage IOR yet — set it directly on the BSDF after rebuild).
    bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf and "IOR" in bsdf.inputs:
        bsdf.inputs["IOR"].default_value = ICE_IOR

    print(f"[TLM] Frozen Ice Glass built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_frozen_ice_glass()
