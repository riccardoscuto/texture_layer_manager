"""
TLM Reverse Material - Burnt Sand Rock
======================================

This preset is a closer translation of the reference node graph structure.
The reference is not just "orange base + dark spots": it is built from
three conceptual branches:

  1. Sand branch  - a noise remapped to two sand colors
  2. Noise branch - a second noise that modulates the sand body
  3. Rock branch  - noise + Voronoi distance-to-edge shaping, also reused
                    for bump/displacement feel

This script mirrors that logic with TLM layers instead of trying to fake the
look with one or two generic noise overlays.

Layer stack (bottom -> top):
  01. Sand Base Fill
  02. Sand Branch
  03. Noise Branch
  04. Rock Branch A
  05. Rock Branch B
  06. Rock Height Noise
  07. Rock Height Voronoi
  08. Roughness Body
  09. Roughness Grain
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


MATERIAL_NAME = "TLM_RE_Burnt_Sand_Rock"
RESOLUTION = "1024"
USE_SELECTED_MESH = True
PREVIEW_OBJECT_NAME = "TLM_RE_Burnt_Sand_Rock_Icosphere"


SAND_FILL = (0.34, 0.14, 0.07, 1.0)
SAND_1 = (0.50, 0.21, 0.10, 1.0)
SAND_2 = (0.28, 0.11, 0.05, 1.0)
NOISE_1 = (0.56, 0.25, 0.12, 1.0)
NOISE_2 = (0.20, 0.08, 0.04, 1.0)
ROCK_1 = (1.0, 1.0, 1.0, 1.0)
ROCK_2 = (0.11, 0.10, 0.10, 1.0)
ROCK_EDGE = (0.05, 0.05, 0.05, 1.0)


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

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=1.0)
    obj = bpy.context.active_object
    obj.name = PREVIEW_OBJECT_NAME
    try:
        bpy.ops.object.shade_smooth()
    except Exception:
        try:
            for poly in obj.data.polygons:
                poly.use_smooth = True
        except Exception:
            pass
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


def _set_voronoi_dte(layer, scale, randomness=1.0, contrast=0.5, center=0.5):
    layer.proc_voronoi_feature = "DISTANCE_TO_EDGE"
    layer.proc_voronoi_distance = "EUCLIDEAN"
    layer.proc_scale = scale
    layer.proc_randomness = randomness
    layer.proc_contrast = contrast
    layer.proc_ramp_center = center


def build_burnt_sand_rock():
    obj = _active_mesh_or_preview_icosphere()
    mat = _material_on_object(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    tlm.shader_editable = False
    _clear_layers(mat)

    # 01. Physical base. This is mostly there to anchor roughness.
    base = _add_fill(mat, "01 Sand Base Fill", SAND_FILL)
    base.use_roughness = True
    base.roughness_fill = 0.82
    base.use_metallic = True
    base.metallic_fill = 0.0

    # 02. Sand branch: corresponds to the top branch in the reference.
    sand = _add_proc(mat, "02 Sand Branch", "NOISE",
                     output="BASE_COLOR", blend="MIX", opacity=1.0)
    _set_noise(sand, scale=58.0, detail=2.0, roughness=0.50,
               lacunarity=2.0, distortion=0.0, contrast=0.26, center=0.50)
    sand.proc_color1 = SAND_1
    sand.proc_color2 = SAND_2

    # 03. Noise branch: second branch that modulates the body before rock.
    noise = _add_proc(mat, "03 Noise Branch", "NOISE",
                      output="BASE_COLOR", blend="OVERLAY", opacity=0.32)
    _set_noise(noise, scale=14.0, detail=15.0, roughness=0.50,
               lacunarity=2.0, distortion=0.0, contrast=0.44, center=0.48)
    noise.proc_color1 = NOISE_1
    noise.proc_color2 = NOISE_2

    # 04. Rock branch A: first dark shaping stage, analogous to the first
    # linear-light rock operation in the reference.
    rock_a = _add_proc(mat, "04 Rock Branch A", "NOISE",
                       output="BASE_COLOR", blend="LINEAR_LIGHT", opacity=0.34)
    _set_noise(rock_a, scale=14.0, detail=15.0, roughness=0.50,
               lacunarity=2.0, distortion=0.0, contrast=0.60, center=0.56)
    rock_a.proc_color1 = ROCK_1
    rock_a.proc_color2 = ROCK_2

    # 05. Rock branch B: Voronoi DTE breakup, analogous to the second shaping
    # stage in the rock branch from the reference.
    rock_b = _add_proc(mat, "05 Rock Branch B", "VORONOI",
                       output="BASE_COLOR", blend="LINEAR_LIGHT", opacity=0.22)
    _set_voronoi_dte(rock_b, scale=12.0, randomness=1.0,
                     contrast=0.78, center=0.36)
    rock_b.proc_color1 = ROCK_1
    rock_b.proc_color2 = ROCK_EDGE

    # 06. Rock height from noise branch.
    bump_noise = _add_proc(mat, "06 Rock Height Noise", "NOISE",
                           output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_noise(bump_noise, scale=14.0, detail=15.0, roughness=0.50,
               lacunarity=2.0, distortion=0.0, contrast=0.60, center=0.56)
    bump_noise.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    bump_noise.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    bump_noise.use_bump = True
    bump_noise.bump_strength = 0.12
    bump_noise.bump_distance = 0.020

    # 07. Rock height from Voronoi branch.
    bump_voro = _add_proc(mat, "07 Rock Height Voronoi", "VORONOI",
                          output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_voronoi_dte(bump_voro, scale=12.0, randomness=1.0,
                     contrast=0.78, center=0.36)
    bump_voro.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    bump_voro.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    bump_voro.use_bump = True
    bump_voro.bump_strength = 0.18
    bump_voro.bump_distance = 0.028

    # 08. Body roughness from the same family as the middle branch.
    rough_body = _add_proc(mat, "08 Roughness Body", "NOISE",
                           output="ROUGHNESS", blend="MIX", opacity=0.32)
    _set_noise(rough_body, scale=20.0, detail=8.0, roughness=0.50,
               lacunarity=2.0, distortion=0.0, contrast=0.30, center=0.50)
    rough_body.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    rough_body.proc_color2 = (1.0, 1.0, 1.0, 1.0)

    # 09. Fine sand grain to restore the sandy read.
    rough_grain = _add_proc(mat, "09 Roughness Grain", "WHITE_NOISE",
                            output="ROUGHNESS", blend="ADD", opacity=0.10)
    rough_grain.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    rough_grain.proc_color2 = (1.0, 1.0, 1.0, 1.0)

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print("")
    print("[TLM] Reverse burnt sand rock material built.")
    print(f"Object:   {obj.name}")
    print(f"Material: {mat.name}")
    print("If it still reads too wet, reduce bump on 06/07 and raise base roughness.")
    print("If the dark coverage is still too even, lower layer 03 and raise layer 04 contrast.")


if __name__ == "__main__":
    build_burnt_sand_rock()
