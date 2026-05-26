"""
TLM Promo Starter - Procedural Frosted Ice Glass
================================================

Run in Blender Text Editor with Texture Layer Manager enabled. Select a mesh
first, or let the script create a bevelled preview cube.

This version is 100% procedural: no Paint layers and no generated images.

Goal:
  - transparent blue ice body;
  - broad cloudy volume, not a noisy surface map;
  - sparse larger cracks instead of dense cellular webbing;
  - procedural bubbles/dust through Dots and White Noise;
  - roughness, transmission, alpha, bump and fresnel all active.

Layer stack, bottom to top:
  01 Ice Body
  02 Spherical Depth Glow
  03 Soft Frozen Clouds
  04 Slow Internal Flow
  05 Soft Crystal Facets
  06 Directional Frost Fibers
  07 Trapped Air Bubbles
  08 Sparse Hairline Cracks
  09 Frosted Fresnel Rim
  10 Micro Rough Sparkle
  11 Final Ice Grade
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


MATERIAL_NAME = "TLM_Promo_Frosted_Ice_Glass"
PREVIEW_OBJECT_NAME = "TLM_Promo_Ice_Block"
RESOLUTION = "1024"
USE_SELECTED_MESH = True


ICE_DEEP = (0.040, 0.155, 0.255, 1.0)
ICE_MID = (0.145, 0.430, 0.610, 1.0)
ICE_LIGHT = (0.620, 0.910, 1.000, 1.0)
FROST_WHITE = (0.840, 0.970, 1.000, 1.0)
CRACK_WHITE = (0.940, 0.995, 1.000, 1.0)
RIM_CYAN = (0.600, 0.920, 1.000, 1.0)


def _preview_object():
    obj = bpy.context.active_object
    if USE_SELECTED_MESH and obj and obj.type == "MESH":
        _ensure_uv(obj)
        return obj

    existing = bpy.data.objects.get(PREVIEW_OBJECT_NAME)
    if existing and existing.type == "MESH":
        bpy.ops.object.select_all(action="DESELECT")
        existing.select_set(True)
        bpy.context.view_layer.objects.active = existing
        _ensure_uv(existing)
        return existing

    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.object
    obj.name = PREVIEW_OBJECT_NAME

    bevel = obj.modifiers.new("Preview Ice Bevel", "BEVEL")
    bevel.width = 0.075
    bevel.segments = 6
    bevel.profile = 0.60
    try:
        obj.modifiers.new("Preview Weighted Normals", "WEIGHTED_NORMAL")
    except Exception:
        pass
    try:
        bpy.ops.object.shade_smooth()
    except Exception:
        pass
    _ensure_uv(obj)
    return obj


def _ensure_uv(obj):
    if obj.type != "MESH" or obj.data.uv_layers:
        return
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=1.15192, island_margin=0.03)
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass


def _material(obj):
    mat = bpy.data.materials.get(MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj.active_material_index = 0
    return mat


def _clear_layers(mat):
    tlm = mat.tlm
    while len(tlm.layers):
        tlm.layers.remove(len(tlm.layers) - 1)
    tlm.active_layer_index = 0


def _add_fill(mat, name, color, opacity=1.0, blend="MIX",
              output="BASE_COLOR"):
    _add_layer_common(bpy.context, "FILL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.fill_color = color
    layer.opacity = opacity
    layer.blend_mode = blend
    layer.output_channel = output
    return layer


def _add_proc(mat, name, proc_type, opacity=1.0, blend="MIX",
              output="BASE_COLOR"):
    _add_layer_common(bpy.context, "PROCEDURAL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.layer_type = "PROCEDURAL"
    layer.proc_type = proc_type
    layer.opacity = opacity
    layer.blend_mode = blend
    layer.output_channel = output
    layer.proc_coord_type = "OBJECT"
    layer.proc_mapping_type = "POINT"
    return layer


def _add_adjustment(mat, name, adj_type="HUE_SAT", output="BASE_COLOR",
                    opacity=1.0):
    _add_layer_common(bpy.context, "ADJUSTMENT")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.layer_type = "ADJUSTMENT"
    layer.adj_type = adj_type
    layer.output_channel = output
    layer.opacity = opacity
    return layer


def _set_bsdf_input(mat, socket_names, value):
    if isinstance(socket_names, str):
        socket_names = [socket_names]
    nt = mat.node_tree
    if not nt:
        return
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return
    for name in socket_names:
        socket = bsdf.inputs.get(name)
        if socket is None or socket.is_linked:
            continue
        try:
            socket.default_value = value
        except Exception:
            pass


def build_frosted_ice_glass():
    obj = _preview_object()
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    mat = _material(obj)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    tlm.shader_editable = False
    tlm.alpha_blend_method = "BLEND"
    try:
        tlm.uv_map = "UVMap"
    except Exception:
        pass
    _clear_layers(mat)

    # 01. Body: physically transparent, blue, low roughness.
    body = _add_fill(mat, "01 Ice Body", ICE_DEEP)
    body.use_roughness = True
    body.roughness_fill = 0.040
    body.use_metallic = True
    body.metallic_fill = 0.0
    body.use_transmission = True
    body.transmission_fill = 0.76
    body.use_alpha = True
    body.alpha_fill = 0.50
    body.alpha_math_operation = "MULTIPLY"

    # 02. Spherical gradient: gives a soft internal depth shift.
    depth = _add_proc(mat, "02 Spherical Depth Glow", "GRADIENT",
                      opacity=0.24, blend="SCREEN")
    depth.proc_gradient_type = "SPHERICAL"
    depth.proc_color1 = ICE_DEEP
    depth.proc_color2 = ICE_LIGHT
    depth.use_proc_color3 = True
    depth.proc_color3 = ICE_MID
    depth.proc_color3_position = 0.36
    depth.proc_contrast = 0.0
    depth.proc_offset_x = -0.26
    depth.proc_offset_y = 0.12
    depth.proc_mapping_scale_x = 0.70
    depth.proc_mapping_scale_y = 0.74
    depth.proc_mapping_scale_z = 0.82
    depth.use_alpha = True
    depth.alpha_fill = 0.030
    depth.alpha_math_operation = "ADD"

    # 03. Broad frost clouds: low contrast, low frequency, physically milky.
    clouds = _add_proc(mat, "03 Soft Frozen Clouds", "NOISE",
                       opacity=0.205, blend="SCREEN")
    clouds.proc_color1 = (0.010, 0.070, 0.115, 1.0)
    clouds.proc_color2 = (0.560, 0.850, 0.960, 1.0)
    clouds.proc_scale = 1.55
    clouds.proc_detail = 7.0
    clouds.proc_roughness_proc = 0.58
    clouds.proc_lacunarity = 2.0
    clouds.proc_distortion = 0.20
    clouds.proc_contrast = 0.12
    clouds.proc_vector_distortion = 0.28
    clouds.use_roughness = True
    clouds.roughness_fill = 0.180
    clouds.blend_mode_roughness = "ADD"
    clouds.use_transmission = True
    clouds.transmission_fill = 0.060
    clouds.blend_mode_transmission = "SUBTRACT"
    clouds.use_alpha = True
    clouds.alpha_fill = 0.035
    clouds.alpha_math_operation = "ADD"
    clouds.use_bump = True
    clouds.bump_strength = 0.032
    clouds.bump_distance = 0.004

    # 04. Slow internal flow: directional frozen veining, not surface cracks.
    flow = _add_proc(mat, "04 Slow Internal Flow", "MARBLE",
                     opacity=0.205, blend="SCREEN")
    flow.proc_color1 = (0.006, 0.070, 0.120, 1.0)
    flow.proc_color2 = (0.520, 0.830, 0.960, 1.0)
    flow.use_proc_color3 = True
    flow.proc_color3 = FROST_WHITE
    flow.proc_color3_position = 0.58
    flow.proc_scale = 2.35
    flow.proc_detail = 7.0
    flow.proc_roughness_proc = 0.56
    flow.proc_distortion = 0.32
    flow.proc_marble_distortion = 5.6
    flow.proc_marble_wave_type = "BANDS"
    flow.proc_marble_wave_profile = "SIN"
    flow.proc_marble_bands_direction = "DIAGONAL"
    flow.proc_contrast = 0.20
    flow.proc_vector_distortion = 0.15
    flow.proc_rotation_z = 0.35
    flow.use_roughness = True
    flow.roughness_fill = 0.095
    flow.blend_mode_roughness = "ADD"
    flow.use_transmission = True
    flow.transmission_fill = 0.040
    flow.blend_mode_transmission = "SUBTRACT"
    flow.use_bump = True
    flow.bump_strength = 0.050
    flow.bump_distance = 0.011

    # 05. Soft facets: low-frequency cell variation, not cell borders.
    facets = _add_proc(mat, "05 Soft Crystal Facets", "VORONOI",
                       opacity=0.135, blend="OVERLAY")
    facets.proc_voronoi_feature = "F1"
    facets.proc_voronoi_distance = "EUCLIDEAN"
    facets.proc_voronoi_random_color = True
    facets.proc_voronoi_random_seed = 7.0
    facets.proc_color1 = (0.030, 0.145, 0.230, 1.0)
    facets.proc_color2 = (0.430, 0.760, 0.920, 1.0)
    facets.use_proc_color3 = True
    facets.proc_color3 = ICE_MID
    facets.proc_color3_position = 0.50
    facets.proc_scale = 4.2
    facets.proc_randomness = 0.42
    facets.proc_detail = 1.0
    facets.proc_roughness_proc = 0.35
    facets.proc_contrast = 0.08
    facets.proc_vector_distortion = 0.12
    facets.use_roughness = True
    facets.roughness_fill = 0.045
    facets.blend_mode_roughness = "ADD"
    facets.use_transmission = True
    facets.transmission_fill = 0.035
    facets.blend_mode_transmission = "SUBTRACT"

    # 06. Fine frost fibers: directional striation using Gabor.
    fibers = _add_proc(mat, "06 Directional Frost Fibers", "GABOR",
                       opacity=0.115, blend="SCREEN")
    fibers.proc_color1 = (0.000, 0.045, 0.075, 1.0)
    fibers.proc_color2 = (0.700, 0.940, 1.000, 1.0)
    fibers.proc_scale = 18.0
    fibers.proc_detail = 5.0
    fibers.proc_distortion = 0.18
    fibers.proc_contrast = 0.42
    fibers.proc_gabor_frequency = 5.5
    fibers.proc_gabor_anisotropy = 0.86
    fibers.proc_gabor_orientation = 24.0
    fibers.proc_vector_distortion = 0.08
    fibers.use_roughness = True
    fibers.roughness_fill = 0.085
    fibers.blend_mode_roughness = "ADD"
    fibers.use_bump = True
    fibers.bump_strength = 0.026
    fibers.bump_distance = 0.0025

    # 07. Procedural air bubbles: Dots, soft and readable.
    bubbles = _add_proc(mat, "07 Trapped Air Bubbles", "DOTS",
                        opacity=0.275, blend="SCREEN")
    bubbles.proc_color1 = (0.000, 0.060, 0.090, 1.0)
    bubbles.proc_color2 = (0.880, 0.990, 1.000, 1.0)
    bubbles.use_proc_color3 = True
    bubbles.proc_color3 = FROST_WHITE
    bubbles.proc_color3_position = 0.32
    bubbles.proc_scale = 7.2
    bubbles.proc_randomness = 0.86
    bubbles.proc_detail = 2.0
    bubbles.proc_roughness_proc = 0.40
    bubbles.proc_lacunarity = 2.0
    bubbles.proc_dots_radius = 0.070
    bubbles.proc_dots_softness = 0.34
    bubbles.proc_contrast = 0.36
    bubbles.proc_vector_distortion = 0.18
    bubbles.use_roughness = True
    bubbles.roughness_fill = 0.135
    bubbles.blend_mode_roughness = "ADD"
    bubbles.use_transmission = True
    bubbles.transmission_fill = 0.075
    bubbles.blend_mode_transmission = "SUBTRACT"
    bubbles.use_alpha = True
    bubbles.alpha_fill = 0.050
    bubbles.alpha_math_operation = "ADD"
    bubbles.use_bump = True
    bubbles.bump_strength = 0.046
    bubbles.bump_distance = 0.006

    # 08. Sparse cracks: lower scale, higher contrast, no dense web.
    cracks = _add_proc(mat, "08 Sparse Hairline Cracks", "CRACKS",
                       opacity=0.255, blend="SCREEN")
    cracks.proc_color1 = (0.000, 0.025, 0.040, 1.0)
    cracks.proc_color2 = CRACK_WHITE
    cracks.proc_scale = 3.7
    cracks.proc_randomness = 0.92
    cracks.proc_detail = 2.0
    cracks.proc_roughness_proc = 0.58
    cracks.proc_lacunarity = 2.0
    cracks.proc_cracks_width = 0.008
    cracks.proc_cracks_sharpness = 0.985
    cracks.proc_contrast = 0.98
    cracks.proc_vector_distortion = 0.70
    cracks.use_roughness = True
    cracks.roughness_fill = 0.180
    cracks.blend_mode_roughness = "ADD"
    cracks.use_transmission = True
    cracks.transmission_fill = 0.105
    cracks.blend_mode_transmission = "SUBTRACT"
    cracks.use_alpha = True
    cracks.alpha_fill = 0.065
    cracks.alpha_math_operation = "ADD"
    cracks.use_bump = True
    cracks.bump_strength = 0.090
    cracks.bump_distance = 0.010

    # 09. Frosted silhouette: present, but no longer the whole material.
    rim = _add_fill(mat, "09 Frosted Fresnel Rim", RIM_CYAN,
                    opacity=0.155, blend="SCREEN")
    rim.use_fresnel_mask = True
    rim.fresnel_ior = 1.18
    rim.fresnel_strength = 1.0
    rim.use_roughness = True
    rim.roughness_fill = 0.120
    rim.blend_mode_roughness = "ADD"
    rim.use_transmission = True
    rim.transmission_fill = 0.050
    rim.blend_mode_transmission = "SUBTRACT"
    rim.use_alpha = True
    rim.alpha_fill = 0.055
    rim.alpha_math_operation = "ADD"

    # 10. Micro sparkle: very light roughness/bump only.
    sparkle = _add_proc(mat, "10 Micro Rough Sparkle", "WHITE_NOISE",
                        opacity=0.012, blend="ADD", output="ROUGHNESS")
    sparkle.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    sparkle.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    sparkle.use_bump = True
    sparkle.bump_strength = 0.006
    sparkle.bump_distance = 0.0008

    # 11. Final grade: restore color depth lost through transparency.
    grade = _add_adjustment(mat, "11 Final Ice Grade", "HUE_SAT",
                            output="BASE_COLOR", opacity=0.80)
    grade.adj_hue = 0.0
    grade.adj_saturation = 1.10
    grade.adj_value = 0.965

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    _set_bsdf_input(mat, ["IOR"], 1.31)
    _set_bsdf_input(mat, ["Specular IOR Level", "Specular"], 0.86)
    _set_bsdf_input(mat, ["Coat Weight", "Clearcoat"], 0.32)
    _set_bsdf_input(mat, ["Coat Roughness", "Clearcoat Roughness"], 0.030)

    try:
        mat.use_screen_refraction = True
    except Exception:
        pass
    try:
        mat.show_transparent_back = True
    except Exception:
        pass

    print("")
    print("[TLM] Procedural frosted ice glass built.")
    print(f"Object:   {obj.name}")
    print(f"Material: {mat.name}")
    print("No Paint layers are used.")
    print("First tuning targets:")
    print("  Too web-like: lower layer 08 opacity to 0.12.")
    print("  Too flat: raise layer 05 opacity to 0.20 or layer 07 to 0.34.")
    print("  Too milky: lower layer 03 opacity to 0.12.")
    print("  Too invisible: raise layer 01 alpha_fill to 0.58.")


if __name__ == "__main__":
    build_frosted_ice_glass()
