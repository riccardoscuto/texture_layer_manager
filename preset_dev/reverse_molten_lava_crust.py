"""
TLM Reverse Material - Molten Lava Crust
========================================

Reverse-engineered from a node reference with a black cooled crust and
large irregular molten openings. This is intentionally different from
the older cracked_lava_crust preset: that one makes thin fissures; this
one aims for broken plates of dark rock with broad hot magma islands.

Layer stack:
  01. Obsidian Crust Base
  02. Charcoal Plate Variation
  03. Sooty Rock Grain
  04. Lava Field Color
  05. Lava Hot Core
  06. Lava Emission
  07. Rock Edge Singe
  08. Crust Macro Bump
  09. Crust Micro Bump
  10. Lava Roughness Cut
  11. Rock Roughness Grain
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


MATERIAL_NAME = "TLM_RE_Molten_Lava_Crust"
RESOLUTION = "1024"
USE_SELECTED_MESH = True
PREVIEW_OBJECT_NAME = "TLM_RE_Molten_Lava_Crust_Icosphere"


# Palette.
ROCK_BASE = (0.010, 0.009, 0.008, 1.0)
ROCK_COAL = (0.035, 0.032, 0.030, 1.0)
ROCK_ASH = (0.055, 0.050, 0.044, 1.0)
ROCK_SOOT = (0.000, 0.000, 0.000, 1.0)

LAVA_RED = (0.46, 0.045, 0.010, 1.0)
LAVA_ORANGE = (1.00, 0.205, 0.018, 1.0)
LAVA_GOLD = (1.00, 0.68, 0.055, 1.0)
LAVA_WHITE_HOT = (1.00, 0.94, 0.46, 1.0)
BURNT_EDGE = (0.18, 0.040, 0.010, 1.0)

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


def _set_cracks(layer, scale, width, sharpness, randomness=0.85):
    layer.proc_scale = scale
    layer.proc_randomness = randomness
    layer.proc_cracks_width = width
    layer.proc_cracks_sharpness = sharpness
    layer.proc_contrast = 0.75
    layer.proc_ramp_center = 0.50


def _set_manual_ramp(layer, stops):
    """Set a direct ColorRamp-like list: [(pos, rgba), ...]."""
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


def build_molten_lava_crust():
    obj = _active_mesh_or_preview_icosphere()
    mat = _material_on_object(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    tlm.shader_editable = False
    _clear_layers(mat)

    # 01. Physical base: dark, non-metal, mostly rough.
    base = _add_fill(mat, "01 Obsidian Crust Base", ROCK_BASE)
    base.use_metallic = True
    base.metallic_fill = 0.0
    base.use_roughness = True
    base.roughness_fill = 0.78

    # 02. Broad charcoal plate variation. Low contrast, not decorative.
    plates = _add_proc(mat, "02 Charcoal Plate Variation", "NOISE",
                       output="BASE_COLOR", blend="OVERLAY", opacity=0.36)
    _set_noise(plates, scale=2.4, detail=7.0, roughness=0.62,
               lacunarity=2.10, distortion=0.32, contrast=0.38, center=0.50)
    plates.proc_color1 = ROCK_SOOT
    plates.proc_color2 = ROCK_ASH

    # 03. Fine soot/rock grain on the black crust.
    grain = _add_proc(mat, "03 Sooty Rock Grain", "NOISE",
                      output="BASE_COLOR", blend="SOFT_LIGHT", opacity=0.38)
    _set_noise(grain, scale=62.0, detail=11.0, roughness=0.62,
               lacunarity=2.0, distortion=0.08, contrast=0.50, center=0.50)
    grain.proc_color1 = BLACK
    grain.proc_color2 = ROCK_COAL

    # 04. Main broad magma openings. The manual ramp keeps most of the
    # surface black and lets only high noise islands become lava.
    lava = _add_proc(mat, "04 Lava Field Color", "NOISE",
                     output="BASE_COLOR", blend="ADD", opacity=1.0)
    _set_noise(lava, scale=3.6, detail=8.0, roughness=0.68,
               lacunarity=2.10, distortion=0.28, contrast=0.62, center=0.68)
    _set_manual_ramp(lava, [
        (0.000, BLACK),
        (0.645, BLACK),
        (0.690, LAVA_RED),
        (0.800, LAVA_ORANGE),
        (0.920, LAVA_GOLD),
        (1.000, LAVA_WHITE_HOT),
    ])

    # 05. Smaller white-hot cores inside only some lava islands.
    core = _add_proc(mat, "05 Lava Hot Core", "NOISE",
                     output="BASE_COLOR", blend="ADD", opacity=0.88)
    _set_noise(core, scale=11.5, detail=8.0, roughness=0.55,
               lacunarity=2.25, distortion=0.35, contrast=0.74, center=0.78)
    _set_manual_ramp(core, [
        (0.000, BLACK),
        (0.835, BLACK),
        (0.900, LAVA_ORANGE),
        (0.965, LAVA_GOLD),
        (1.000, LAVA_WHITE_HOT),
    ])

    # 06. Emission uses the same broad-family noise. Keep base color black
    # here so this layer contributes light, not an extra visible texture.
    glow = _add_proc(mat, "06 Lava Emission", "NOISE",
                     output="BASE_COLOR", blend="ADD", opacity=1.0)
    _set_noise(glow, scale=3.6, detail=8.0, roughness=0.68,
               lacunarity=2.10, distortion=0.28, contrast=0.62, center=0.68)
    glow.proc_color1 = BLACK
    glow.proc_color2 = BLACK
    glow.use_emission = True
    glow.emission_color = LAVA_ORANGE
    glow.emission_strength = 6.4
    glow.proc_emission_threshold = 0.70
    glow.proc_emission_falloff = 0.035

    # 07. Burnt red edge scatter. This helps lava openings feel like hot
    # fractures rather than stickers pasted on top of the crust.
    edge = _add_proc(mat, "07 Rock Edge Singe", "CRACKS",
                     output="BASE_COLOR", blend="ADD", opacity=0.13)
    _set_cracks(edge, scale=5.6, width=0.085, sharpness=0.80, randomness=0.95)
    edge.proc_color1 = BURNT_EDGE
    edge.proc_color2 = BLACK

    # 08. Macro crust bump: broad rock relief.
    bump_macro = _add_proc(mat, "08 Crust Macro Bump", "NOISE",
                           output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_noise(bump_macro, scale=5.0, detail=8.0, roughness=0.62,
               lacunarity=2.15, distortion=0.75, contrast=0.56, center=0.50)
    bump_macro.proc_color1 = BLACK
    bump_macro.proc_color2 = WHITE
    bump_macro.use_bump = True
    bump_macro.bump_strength = 0.42
    bump_macro.bump_distance = 0.030

    # 09. Fine pitted bump, very small distance.
    bump_micro = _add_proc(mat, "09 Crust Micro Bump", "NOISE",
                           output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_noise(bump_micro, scale=95.0, detail=10.0, roughness=0.58,
               lacunarity=2.0, distortion=0.0, contrast=0.45, center=0.50)
    bump_micro.proc_color1 = BLACK
    bump_micro.proc_color2 = WHITE
    bump_micro.use_bump = True
    bump_micro.bump_strength = 0.10
    bump_micro.bump_distance = 0.004

    # 10. Lower roughness on molten parts. Same broad mask family as lava.
    rough_lava = _add_proc(mat, "10 Lava Roughness Cut", "NOISE",
                           output="ROUGHNESS", blend="SUBTRACT", opacity=0.36)
    _set_noise(rough_lava, scale=3.6, detail=8.0, roughness=0.68,
               lacunarity=2.10, distortion=0.28, contrast=0.62, center=0.68)
    _set_manual_ramp(rough_lava, [
        (0.000, BLACK),
        (0.645, BLACK),
        (0.820, WHITE),
        (1.000, WHITE),
    ])

    # 11. Restore high-frequency roughness on the cooled crust.
    rough_grain = _add_proc(mat, "11 Rock Roughness Grain", "NOISE",
                            output="ROUGHNESS", blend="ADD", opacity=0.08)
    _set_noise(rough_grain, scale=88.0, detail=8.0, roughness=0.60,
               lacunarity=2.0, distortion=0.0, contrast=0.35, center=0.50)
    rough_grain.proc_color1 = BLACK
    rough_grain.proc_color2 = WHITE

    tlm.bsdf_ior = 1.52
    tlm.use_displacement = False

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print("")
    print("[TLM] Reverse molten lava crust material built.")
    print(f"Object:   {obj.name}")
    print(f"Material: {mat.name}")
    print("First tuning pass:")
    print("  - Too much lava: raise layer 04 black stop from 0.645 to 0.70.")
    print("  - Not enough lava: lower layer 04 black stop from 0.645 to 0.58.")
    print("  - Too flat: raise layer 08 bump_strength to 0.55.")
    print("  - Too glossy overall: lower layer 10 opacity to 0.30.")
    return mat


if __name__ == "__main__":
    build_molten_lava_crust()
