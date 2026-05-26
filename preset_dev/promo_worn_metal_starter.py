"""
TLM Promo Starter - Worn Painted Metal
=====================================

Run from Blender's Text Editor with a mesh selected. The script creates a
commercial starter material for screenshots and iteration:

  - painted dielectric coating
  - procedural paint variation
  - primer halo under chipped paint
  - metallic exposed steel only inside sharper chips
  - rust/dirt in cavities
  - roughness-only scratches
  - dust and final color grade
  - two empty paint layers for manual art direction

This is intentionally not a final asset. Use it as the fast base, then send
screenshots and tune the layer values/art direction.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


MATERIAL_NAME = "TLM_Promo_Worn_Painted_Metal"
RESOLUTION = "1024"
USE_SELECTED_MESH = False
PREVIEW_OBJECT_NAME = "TLM_Promo_Worn_Metal_Block"


# ---------------------------------------------------------------------------
# Palette and physical values
# ---------------------------------------------------------------------------

PAINT_BASE = (0.30, 0.045, 0.035, 1.0)
PAINT_DARK = (0.16, 0.022, 0.018, 1.0)
PAINT_LIGHT = (0.40, 0.085, 0.060, 1.0)

PRIMER = (0.30, 0.145, 0.080, 1.0)
STEEL = (0.42, 0.44, 0.47, 1.0)

RUST_DARK = (0.115, 0.040, 0.012, 1.0)
RUST_LIGHT = (0.48, 0.205, 0.055, 1.0)

DUST = (0.55, 0.52, 0.47, 1.0)

PAINT_ROUGHNESS = 0.68
PRIMER_ROUGHNESS = 0.78
STEEL_ROUGHNESS = 0.36
RUST_ROUGHNESS = 0.92
DUST_ROUGHNESS = 0.88


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _active_mesh_or_new_cube():
    obj = bpy.context.active_object
    if USE_SELECTED_MESH and obj and obj.type == "MESH":
        return obj
    existing = bpy.data.objects.get(PREVIEW_OBJECT_NAME)
    if existing and existing.type == "MESH":
        bpy.ops.object.select_all(action="DESELECT")
        existing.select_set(True)
        bpy.context.view_layer.objects.active = existing
        return existing
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    obj.name = PREVIEW_OBJECT_NAME
    bevel = obj.modifiers.new("Preview Bevel", "BEVEL")
    bevel.width = 0.055
    bevel.segments = 4
    bevel.profile = 0.65
    try:
        obj.modifiers.new("Preview Weighted Normals", "WEIGHTED_NORMAL")
    except Exception:
        pass
    return obj


def _get_or_create_material(obj, name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj.active_material_index = 0
    return mat


def _clear_tlm_layers(mat):
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
    layer.proc_type = proc_type
    layer.opacity = opacity
    layer.blend_mode = blend
    layer.output_channel = output
    return layer


def _add_paint(mat, name, opacity=1.0, blend="MIX",
               output="BASE_COLOR"):
    _add_layer_common(bpy.context, "PAINT")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.opacity = opacity
    layer.blend_mode = blend
    layer.output_channel = output
    return layer


def _add_adjustment(mat, name, adj_type, opacity=1.0,
                    output="BASE_COLOR"):
    _add_layer_common(bpy.context, "ADJUSTMENT")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.adj_type = adj_type
    layer.opacity = opacity
    layer.output_channel = output
    return layer


def _edge_wear(layer, intensity, breakup, scale, sharpness,
               contrast=0.50):
    layer.use_mask = True
    layer.mask_source = "EDGE_WEAR"
    layer.mask_gen_intensity = intensity
    layer.mask_gen_breakup = breakup
    layer.mask_gen_breakup_scale = scale
    layer.mask_gen_sharpness = sharpness
    layer.mask_contrast = contrast


def _dirt_mask(layer, intensity, breakup, scale, sharpness,
               ao_distance=1.0, contrast=0.55):
    layer.use_mask = True
    layer.mask_source = "DIRT"
    layer.mask_ao_distance = ao_distance
    layer.mask_gen_intensity = intensity
    layer.mask_gen_breakup = breakup
    layer.mask_gen_breakup_scale = scale
    layer.mask_gen_sharpness = sharpness
    layer.mask_contrast = contrast


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_promo_worn_metal():
    obj = _active_mesh_or_new_cube()
    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_tlm_layers(mat)

    # Layers are added bottom to top. Last added is top of the UI list.

    base = _add_fill(mat, "01 Paint Base", PAINT_BASE)
    base.use_roughness = True
    base.roughness_fill = PAINT_ROUGHNESS
    base.use_metallic = True
    base.metallic_fill = 0.0

    var = _add_proc(mat, "02 Paint Clouding", "NOISE",
                    opacity=0.30, blend="OVERLAY")
    var.proc_scale = 2.2
    var.proc_detail = 4.0
    var.proc_roughness_proc = 0.62
    var.proc_lacunarity = 2.1
    var.proc_distortion = 0.35
    var.proc_color1 = PAINT_DARK
    var.proc_color2 = PAINT_LIGHT
    var.proc_contrast = 0.28
    var.use_bump = True
    var.bump_strength = 0.025
    var.bump_distance = 0.002

    primer = _add_fill(mat, "03 Primer Halo", PRIMER, opacity=0.92)
    primer.use_roughness = True
    primer.roughness_fill = PRIMER_ROUGHNESS
    primer.use_metallic = True
    primer.metallic_fill = 0.0
    _edge_wear(primer, intensity=0.42, breakup=0.50,
               scale=18.0, sharpness=0.55, contrast=0.62)
    primer.use_mask_levels = True
    primer.mask_levels_in_min = 0.22
    primer.mask_levels_in_max = 0.88
    primer.mask_levels_gamma = 1.15
    primer.mask_softness = 0.06

    steel = _add_fill(mat, "04 Exposed Steel Chips", STEEL, opacity=1.0)
    steel.use_roughness = True
    steel.roughness_fill = STEEL_ROUGHNESS
    steel.use_metallic = True
    steel.metallic_fill = 1.0
    _edge_wear(steel, intensity=0.62, breakup=0.58,
               scale=30.0, sharpness=0.88, contrast=0.76)
    steel.use_mask_levels = True
    steel.mask_levels_in_min = 0.58
    steel.mask_levels_in_max = 0.96
    steel.mask_levels_gamma = 1.35
    steel.mask_softness = 0.02

    rust = _add_proc(mat, "05 Rust Bloom", "NOISE",
                     opacity=0.34, blend="MIX")
    rust.proc_scale = 4.6
    rust.proc_detail = 7.0
    rust.proc_roughness_proc = 0.66
    rust.proc_lacunarity = 2.25
    rust.proc_distortion = 1.65
    rust.proc_color1 = RUST_DARK
    rust.proc_color2 = RUST_LIGHT
    rust.proc_contrast = 0.58
    rust.blend_mode_base_color = "MULTIPLY"
    rust.use_roughness = True
    rust.roughness_fill = RUST_ROUGHNESS
    rust.use_metallic = True
    rust.metallic_fill = 0.0
    rust.use_bump = True
    rust.bump_strength = 0.12
    rust.bump_distance = 0.010
    _dirt_mask(rust, intensity=0.56, breakup=0.68,
               scale=14.0, sharpness=0.50,
               ao_distance=1.15, contrast=0.56)

    stains = _add_proc(mat, "06 Soft Rust Staining", "GRADIENT",
                       opacity=0.08, blend="MULTIPLY")
    stains.proc_gradient_type = "LINEAR"
    stains.proc_offset_x = -0.18
    stains.proc_color1 = (1.0, 1.0, 1.0, 1.0)
    stains.proc_color2 = (0.52, 0.23, 0.08, 1.0)
    stains.use_mask = True
    stains.mask_source = "DIRT"
    stains.mask_gen_intensity = 0.55
    stains.mask_gen_breakup = 0.72
    stains.mask_gen_breakup_scale = 7.0
    stains.mask_gen_sharpness = 0.35
    stains.mask_ao_distance = 1.4

    scratches = _add_proc(mat, "07 Fine Scratch Roughness", "GABOR",
                          opacity=0.11, blend="ADD",
                          output="ROUGHNESS")
    scratches.proc_scale = 42.0
    scratches.proc_gabor_anisotropy = 1.0
    scratches.proc_gabor_orientation = 14.0
    scratches.proc_gabor_frequency = 7.0
    scratches.proc_distortion = 0.10
    scratches.proc_randomness = 0.70
    scratches.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    scratches.proc_color2 = (1.0, 1.0, 1.0, 1.0)

    dust = _add_proc(mat, "08 Dry Dust", "WHITE_NOISE",
                     opacity=0.08, blend="OVERLAY")
    dust.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    dust.proc_color2 = DUST
    dust.use_roughness = True
    dust.roughness_fill = DUST_ROUGHNESS
    dust.use_metallic = True
    dust.metallic_fill = 0.0
    dust.use_mask = True
    dust.mask_source = "POINTINESS"
    dust.mask_invert = True
    dust.mask_contrast = 0.42

    rough_grade = _add_adjustment(mat, "09 Roughness Crunch",
                                  "BRIGHT_CONTRAST",
                                  opacity=0.28,
                                  output="ROUGHNESS")
    rough_grade.adj_brightness = 0.025
    rough_grade.adj_contrast = 0.20

    color_grade = _add_adjustment(mat, "10 Final Color Grade",
                                  "HUE_SAT",
                                  opacity=0.28,
                                  output="BASE_COLOR")
    color_grade.adj_hue = 0.50
    color_grade.adj_saturation = 1.06
    color_grade.adj_value = 0.96

    manual_rough = _add_paint(mat, "11 Paint Manual Rough Scratches",
                              opacity=0.75, blend="MIX",
                              output="ROUGHNESS")
    manual_rough.paint_interpolation = "Linear"
    manual_rough.paint_projection = "FLAT"

    manual_color = _add_paint(mat, "12 Paint Decals And Damage",
                              opacity=0.85, blend="MIX",
                              output="BASE_COLOR")
    manual_color.paint_interpolation = "Linear"
    manual_color.paint_projection = "FLAT"

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print("")
    print("[TLM] Promo worn painted metal starter built.")
    print(f"Object:   {obj.name}")
    print(f"Material: {mat.name}")
    print("Paint layers:")
    print("  11 Paint Manual Rough Scratches: paint grayscale roughness marks")
    print("     black/transparent = no change, mid gray = satin scratch, white = chalky rough scratch")
    print("  12 Paint Decals And Damage: paint labels, colored scuffs, logo marks, extra grime")
    print("")
    print("This preset creates/uses a bevelled cube by default.")
    print("For your own mesh set USE_SELECTED_MESH=True at the top of the script.")
    print("Send a viewport screenshot after the first run; tune masks first, paint second.")


if __name__ == "__main__":
    build_promo_worn_metal()
