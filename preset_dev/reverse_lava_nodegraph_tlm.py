"""
TLM Reverse Material - Lava Nodegraph Translation
=================================================

Goal
----
Translate the reference node graph into a TLM layer stack.

Reference graph anatomy:
  1. Shared Texture Coordinate + Mapping
  2. Rock branch:
     - low/mid noise -> ColorRamp -> Mix -> rock Principled base color
     - noise ramps also feed rock roughness and bump
  3. Mask branch:
     - two noise fields -> ramps/math -> Mix Shader factor
  4. Lava branch:
     - noise -> ColorRamp -> hot molten color
     - same family drives bump/roughness
  5. Mix Shader:
     - mask blends Rock BSDF against Lava BSDF
  6. Displacement:
     - mask/height field drives Material Output displacement

TLM limitation/translation:
  TLM currently builds one managed Principled BSDF, not two separate BSDFs
  mixed by a shader factor. So the translation uses:
    - black/charcoal rock as the base branch
    - lava color as ADD layers whose ramps are black outside openings
    - emission as a separate procedural layer aligned to the lava mask
    - roughness/bump channels aligned by duplicated noise values
    - subtle displacement, because TLM displacement uses raw fac and does
      not apply ColorRamp/masks yet.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


MATERIAL_NAME = "TLM_RE_Lava_Nodegraph"
RESOLUTION = "1024"
USE_SELECTED_MESH = True
PREVIEW_OBJECT_NAME = "TLM_RE_Lava_Nodegraph_Icosphere"


# Shared procedural scales. Keep these aligned across branches.
ROCK_LOW_SCALE = 2.6
ROCK_HIGH_SCALE = 22.0
LAVA_MASK_SCALE = 4.35
LAVA_CORE_SCALE = 10.5


# Color palette from the rendered reference: very dark crust, orange-red
# molten body, small yellow/white cores.
ROCK_BLACK = (0.006, 0.005, 0.004, 1.0)
ROCK_DARK = (0.020, 0.018, 0.016, 1.0)
ROCK_MID = (0.040, 0.037, 0.034, 1.0)
ROCK_ASH = (0.065, 0.060, 0.054, 1.0)

BURNT_RED = (0.23, 0.045, 0.010, 1.0)
LAVA_RED = (0.78, 0.045, 0.000, 1.0)
LAVA_ORANGE = (1.00, 0.285, 0.000, 1.0)
LAVA_YELLOW = (1.00, 0.740, 0.030, 1.0)
LAVA_WHITE = (1.00, 0.980, 0.520, 1.0)

BLACK = (0.0, 0.0, 0.0, 1.0)
WHITE = (1.0, 1.0, 1.0, 1.0)


def _active_mesh_or_preview_icosphere():
    obj = bpy.context.active_object
    if USE_SELECTED_MESH and obj and obj.type == "MESH":
        return obj

    existing = bpy.data.objects.get(PREVIEW_OBJECT_NAME)
    if existing and existing.type == "MESH":
        bpy.ops.object.select_all(action="DESELECT")
        existing.select_set(True)
        bpy.context.view_layer.objects.active = existing
        return existing

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1.0)
    obj = bpy.context.active_object
    obj.name = PREVIEW_OBJECT_NAME
    try:
        bpy.ops.object.shade_smooth()
    except Exception:
        for poly in obj.data.polygons:
            poly.use_smooth = True
    return obj


def _material_on_object(obj, name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj.active_material_index = 0
    bpy.context.view_layer.objects.active = obj
    return mat


def _clear_layers(mat):
    tlm = mat.tlm
    while len(tlm.layers):
        tlm.layers.remove(len(tlm.layers) - 1)
    tlm.active_layer_index = 0


def _add_fill(mat, name, color, output="BASE_COLOR", blend="MIX", opacity=1.0):
    _add_layer_common(bpy.context, "FILL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.fill_color = color
    layer.output_channel = output
    layer.blend_mode = blend
    layer.opacity = opacity
    return layer


def _add_proc(mat, name, proc_type="NOISE", output="BASE_COLOR",
              blend="MIX", opacity=1.0):
    _add_layer_common(bpy.context, "PROCEDURAL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.layer_type = "PROCEDURAL"
    layer.proc_type = proc_type
    layer.output_channel = output
    layer.blend_mode = blend
    layer.opacity = opacity
    layer.proc_coord_type = "OBJECT"
    layer.proc_mapping_type = "POINT"
    layer.proc_normalize_coords = True
    return layer


def _set_noise(layer, scale, detail, roughness, lacunarity=2.0,
               distortion=0.0, contrast=0.5, center=0.5):
    layer.proc_scale = scale
    layer.proc_detail = detail
    layer.proc_roughness_proc = roughness
    layer.proc_lacunarity = lacunarity
    layer.proc_distortion = distortion
    layer.proc_contrast = contrast
    layer.proc_ramp_center = center


def _set_manual_ramp(layer, stops):
    """Set a direct ColorRamp-style procedural ramp."""
    ordered = sorted(stops, key=lambda item: item[0])
    if len(ordered) < 2:
        return
    layer.proc_use_manual_stops = True
    layer.proc_color1_position = ordered[0][0]
    layer.proc_color1 = ordered[0][1]
    layer.proc_color2_position = ordered[-1][0]
    layer.proc_color2 = ordered[-1][1]
    layer.proc_extra_color_stops.clear()
    for pos, color in ordered[1:-1]:
        stop = layer.proc_extra_color_stops.add()
        stop.position = pos
        stop.color = color


def build_lava_nodegraph_translation():
    obj = _active_mesh_or_preview_icosphere()
    mat = _material_on_object(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    tlm.shader_editable = False
    _clear_layers(mat)

    # 01. Rock branch physical base. This plays the role of the upper
    # Principled BSDF in the reference.
    base = _add_fill(mat, "01 Rock BSDF Base", ROCK_BLACK)
    base.use_metallic = True
    base.metallic_fill = 0.0
    base.use_roughness = True
    base.roughness_fill = 0.86

    # 02. Rock low-frequency body ramp.
    rock_low = _add_proc(mat, "02 Rock Low Noise Ramp", "NOISE",
                         output="BASE_COLOR", blend="OVERLAY", opacity=0.34)
    _set_noise(rock_low, ROCK_LOW_SCALE, detail=9.0, roughness=0.62,
               lacunarity=2.0, distortion=0.10, contrast=0.34, center=0.50)
    _set_manual_ramp(rock_low, [
        (0.00, ROCK_BLACK),
        (0.36, ROCK_DARK),
        (0.72, ROCK_MID),
        (1.00, ROCK_ASH),
    ])

    # 03. Rock high-frequency soot pass, equivalent to a secondary ramp
    # that gives the cooled crust granular broken value.
    rock_hi = _add_proc(mat, "03 Rock Soot Detail", "NOISE",
                        output="BASE_COLOR", blend="MULTIPLY", opacity=0.48)
    _set_noise(rock_hi, ROCK_HIGH_SCALE, detail=10.0, roughness=0.62,
               lacunarity=2.0, distortion=0.03, contrast=0.55, center=0.50)
    rock_hi.proc_color1 = WHITE
    rock_hi.proc_color2 = (0.34, 0.32, 0.30, 1.0)

    # 04. Main lava branch. Black up to the threshold means "rock shader";
    # hot colors only appear where the branch mask opens.
    lava_field = _add_proc(mat, "04 Lava Branch Color", "NOISE",
                           output="BASE_COLOR", blend="ADD", opacity=1.0)
    _set_noise(lava_field, LAVA_MASK_SCALE * 1.08, detail=9.0, roughness=0.64,
               lacunarity=2.18, distortion=0.18, contrast=0.68, center=0.62)
    _set_manual_ramp(lava_field, [
        (0.000, BLACK),
        (0.550, BLACK),
        (0.585, BURNT_RED),
        (0.645, LAVA_RED),
        (0.720, LAVA_ORANGE),
        (0.800, LAVA_YELLOW),
        (1.000, LAVA_WHITE),
    ])

    # 05. Lava core branch. Smaller hotter islands inside the openings.
    lava_core = _add_proc(mat, "05 Lava Hot Core", "NOISE",
                          output="BASE_COLOR", blend="ADD", opacity=0.92)
    _set_noise(lava_core, LAVA_CORE_SCALE * 1.28, detail=8.0, roughness=0.53,
               lacunarity=2.35, distortion=0.10, contrast=0.82, center=0.78)
    _set_manual_ramp(lava_core, [
        (0.000, BLACK),
        (0.700, BLACK),
        (0.770, LAVA_ORANGE),
        (0.875, LAVA_YELLOW),
        (1.000, LAVA_WHITE),
    ])

    # 06. Mask branch approximation for emission. The reference uses a mask
    # to blend Lava BSDF vs Rock BSDF; here the same noise family drives
    # emission, with an additional NOISE selector to break uniformity.
    emission = _add_proc(mat, "06 Lava Mask Emission", "NOISE",
                         output="BASE_COLOR", blend="ADD", opacity=1.0)
    _set_noise(emission, LAVA_MASK_SCALE * 1.08, detail=9.0, roughness=0.64,
               lacunarity=2.18, distortion=0.18, contrast=0.68, center=0.62)
    emission.proc_color1 = BLACK
    emission.proc_color2 = BLACK
    emission.use_emission = True
    emission.emission_color = LAVA_ORANGE
    emission.emission_strength = 24.0
    emission.proc_emission_threshold = 0.55
    emission.proc_emission_falloff = 0.048
    emission.emission_selector_type = "NONE"

    # 07. Burnt transition around openings. This is a cheap TLM equivalent
    # of the darker/orange edge ramp visible in the lava branch.
    burnt_edge = _add_proc(mat, "07 Burnt Lava Edge", "NOISE",
                           output="BASE_COLOR", blend="ADD", opacity=0.10)
    _set_noise(burnt_edge, LAVA_MASK_SCALE + 0.8, detail=10.0, roughness=0.60,
               lacunarity=2.18, distortion=0.18, contrast=0.72, center=0.62)
    _set_manual_ramp(burnt_edge, [
        (0.000, BLACK),
        (0.535, BLACK),
        (0.600, BURNT_RED),
        (0.690, BLACK),
        (1.000, BLACK),
    ])

    # 08. Rock roughness, mostly matte with high-frequency breakup.
    rough_grain = _add_proc(mat, "08 Rock Roughness Grain", "NOISE",
                            output="ROUGHNESS", blend="ADD", opacity=0.10)
    _set_noise(rough_grain, ROCK_HIGH_SCALE * 1.3, detail=9.0, roughness=0.62,
               lacunarity=2.0, distortion=0.0, contrast=0.40, center=0.50)
    rough_grain.proc_color1 = BLACK
    rough_grain.proc_color2 = WHITE

    # 09. Molten regions are glossier, so subtract roughness using the same
    # mask family as the lava branch.
    rough_lava = _add_proc(mat, "09 Lava Roughness Mask", "NOISE",
                           output="ROUGHNESS", blend="SUBTRACT", opacity=0.42)
    _set_noise(rough_lava, LAVA_MASK_SCALE * 1.08, detail=9.0, roughness=0.64,
               lacunarity=2.18, distortion=0.18, contrast=0.68, center=0.62)
    _set_manual_ramp(rough_lava, [
        (0.000, BLACK),
        (0.560, BLACK),
        (0.780, WHITE),
        (1.000, WHITE),
    ])

    # 10. Macro bump from rock branch.
    bump_macro = _add_proc(mat, "10 Rock Macro Bump", "NOISE",
                           output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_noise(bump_macro, 5.8, detail=8.0, roughness=0.64,
               lacunarity=2.0, distortion=0.34, contrast=0.55, center=0.50)
    bump_macro.proc_color1 = BLACK
    bump_macro.proc_color2 = WHITE
    bump_macro.use_bump = True
    bump_macro.bump_strength = 0.34
    bump_macro.bump_distance = 0.020

    # 11. Micro bump from rock branch.
    bump_micro = _add_proc(mat, "11 Rock Micro Bump", "NOISE",
                           output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_noise(bump_micro, 115.0, detail=8.0, roughness=0.58,
               lacunarity=2.0, distortion=0.0, contrast=0.46, center=0.50)
    bump_micro.proc_color1 = BLACK
    bump_micro.proc_color2 = WHITE
    bump_micro.use_bump = True
    bump_micro.bump_strength = 0.065
    bump_micro.bump_distance = 0.003

    # 12. Charred breakup. A dark high-frequency multiply pass breaks the
    # soft lava islands into rougher pockets, closer to the reference where
    # black crust intrudes into the hot zones.
    breakup = _add_proc(mat, "12 Charred Lava Breakup", "NOISE",
                        output="BASE_COLOR", blend="MULTIPLY", opacity=0.18)
    _set_noise(breakup, 38.0, detail=10.0, roughness=0.62,
               lacunarity=2.2, distortion=0.10, contrast=0.72, center=0.52)
    _set_manual_ramp(breakup, [
        (0.000, WHITE),
        (0.550, WHITE),
        (0.720, (0.30, 0.24, 0.20, 1.0)),
        (1.000, (0.08, 0.06, 0.05, 1.0)),
    ])

    # 13. High-frequency hot flecks. This imitates the small yellow/white
    # exposed magma pockets in the reference. Mostly black ramp, so it only
    # adds tiny hot fragments rather than changing the overall coverage.
    sparks = _add_proc(mat, "13 Lava Spark Detail", "NOISE",
                       output="BASE_COLOR", blend="ADD", opacity=1.0)
    _set_noise(sparks, 24.0, detail=9.0, roughness=0.53,
               lacunarity=2.45, distortion=0.04, contrast=0.90, center=0.80)
    _set_manual_ramp(sparks, [
        (0.000, BLACK),
        (0.755, BLACK),
        (0.835, LAVA_ORANGE),
        (0.905, LAVA_YELLOW),
        (1.000, LAVA_WHITE),
    ])
    sparks.use_emission = True
    sparks.emission_color = LAVA_YELLOW
    sparks.emission_strength = 8.0
    sparks.proc_emission_threshold = 0.76
    sparks.proc_emission_falloff = 0.020

    # 14. Thin molten filaments. This adds a small amount of line-like hot
    # structure, similar to the reference's sharper exposed streaks.
    filaments = _add_proc(mat, "14 Lava Filament Highlights", "CRACKS",
                          output="BASE_COLOR", blend="ADD", opacity=0.32)
    filaments.proc_scale = 8.0
    filaments.proc_randomness = 0.88
    filaments.proc_cracks_width = 0.055
    filaments.proc_cracks_sharpness = 0.82
    filaments.proc_contrast = 0.80
    filaments.proc_ramp_center = 0.50
    filaments.proc_color1 = BLACK
    filaments.proc_color2 = LAVA_YELLOW
    filaments.use_emission = True
    filaments.emission_color = LAVA_ORANGE
    filaments.emission_strength = 5.0
    filaments.proc_emission_threshold = 0.78
    filaments.proc_emission_falloff = 0.020

    # 15. Subtle real displacement. Kept conservative because TLM's current
    # displacement stack reads raw procedural Fac, not the ColorRamp mask.
    disp = _add_proc(mat, "15 Subtle Rock Displacement", "NOISE",
                     output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_noise(disp, 4.2, detail=6.0, roughness=0.62,
               lacunarity=2.0, distortion=0.25, contrast=0.45, center=0.50)
    disp.proc_color1 = BLACK
    disp.proc_color2 = WHITE
    disp.use_displacement = True
    disp.displacement_scale = 0.22

    tlm.bsdf_ior = 1.58
    tlm.use_displacement = True
    tlm.displacement_strength = 0.035
    tlm.displacement_midlevel = 0.50
    tlm.displacement_adaptive = True

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print("")
    print("[TLM] Lava nodegraph translation built.")
    print(f"Object:   {obj.name}")
    print(f"Material: {mat.name}")
    print("Tuning after first screenshot:")
    print("  - Too little lava: layer 04 black stop 0.560 -> 0.510.")
    print("  - Too much lava: layer 04 black stop 0.560 -> 0.620.")
    print("  - Glow too weak: layer 06 strength 18.0 -> 24.0.")
    print("  - Pattern too swirly: layer 04 distortion 0.12 -> 0.03.")
    print("  - Crost too flat: layer 10 bump_strength 0.34 -> 0.48.")
    return mat


if __name__ == "__main__":
    build_lava_nodegraph_translation()
