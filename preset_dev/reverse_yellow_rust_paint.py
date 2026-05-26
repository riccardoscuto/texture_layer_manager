"""
Reverse-engineering study: yellow worn paint, rust, exposed metal.

Run in Blender with Texture Layer Manager enabled. Select a mesh first, or let
the script create a small bevelled cube. This is intentionally a compact study
material: few layers, clear values, and two empty paint layers for art direction.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


MATERIAL_NAME = "TLM_RE_Yellow_Rust_Paint"
RESOLUTION = "1024"
USE_SELECTED_MESH = True
PREVIEW_OBJECT_NAME = "TLM_RE_Yellow_Rust_Block"


PAINT_YELLOW = (0.72, 0.46, 0.105, 1.0)
PAINT_DARK = (0.44, 0.275, 0.060, 1.0)
PAINT_LIGHT = (0.88, 0.60, 0.165, 1.0)

RUST_DARK = (0.16, 0.045, 0.020, 1.0)
RUST_RED = (0.46, 0.105, 0.055, 1.0)
RUST_ORANGE = (0.82, 0.315, 0.075, 1.0)

STEEL_DARK = (0.18, 0.18, 0.17, 1.0)
STEEL_GLEAM = (0.68, 0.70, 0.68, 1.0)


def _active_mesh_or_preview_cube():
    obj = bpy.context.active_object
    if USE_SELECTED_MESH and obj and obj.type == "MESH":
        return obj

    existing = bpy.data.objects.get(PREVIEW_OBJECT_NAME)
    if existing and existing.type == "MESH":
        bpy.ops.object.select_all(action="DESELECT")
        existing.select_set(True)
        bpy.context.view_layer.objects.active = existing
        return existing

    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.active_object
    obj.name = PREVIEW_OBJECT_NAME

    bevel = obj.modifiers.new("Preview Bevel", "BEVEL")
    bevel.width = 0.055
    bevel.segments = 3
    bevel.profile = 0.62
    try:
        obj.modifiers.new("Preview Weighted Normals", "WEIGHTED_NORMAL")
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


def _ensure_uv(obj):
    if obj.type != "MESH" or obj.data.uv_layers:
        return
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=1.15192, island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass


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


def _add_paint(mat, name, output="BASE_COLOR", blend="MIX", opacity=1.0):
    _add_layer_common(bpy.context, "PAINT")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.output_channel = output
    layer.blend_mode = blend
    layer.opacity = opacity
    return layer


def _smoothstep(edge0, edge1, value):
    value = (value - edge0) / max(1e-6, edge1 - edge0)
    value = value.clip(0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def _wear_field(size, seed=42):
    import numpy as np

    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) / float(size)
    field = np.zeros((size, size), dtype=np.float32)

    # Medium islands: these define the large rust blooms.
    for _ in range(34):
        cx, cy = rng.random(2)
        rx = rng.uniform(0.018, 0.070)
        ry = rng.uniform(0.010, 0.045)
        angle = rng.uniform(0.0, 6.28318)
        amp = rng.uniform(0.45, 1.0)
        dx = xx - cx
        dy = yy - cy
        ca, sa = np.cos(angle), np.sin(angle)
        xr = dx * ca + dy * sa
        yr = -dx * sa + dy * ca
        blob = np.exp(-0.5 * ((xr / rx) ** 2 + (yr / ry) ** 2))
        field = np.maximum(field, blob * amp)

    # A few larger, weaker clouds keep the shapes from reading like dots.
    for _ in range(12):
        cx, cy = rng.random(2)
        r = rng.uniform(0.070, 0.145)
        dx = xx - cx
        dy = yy - cy
        blob = np.exp(-0.5 * ((dx / r) ** 2 + (dy / (r * 0.65)) ** 2))
        field = np.maximum(field, blob * rng.uniform(0.18, 0.38))

    grain = (
        0.5
        + 0.25 * np.sin(xx * 95.0 + np.sin(yy * 31.0) * 2.6)
        + 0.25 * np.sin(yy * 83.0 + np.sin(xx * 29.0) * 2.0)
    ).astype(np.float32)
    grain = grain.clip(0.0, 1.0)
    return (field * (0.78 + grain * 0.22)).clip(0.0, 1.0), grain


def _make_wear_images(size):
    import numpy as np

    field, grain = _wear_field(size)
    rust_alpha = _smoothstep(0.36, 0.68, field)
    rust_alpha *= 0.82 + grain * 0.18
    rust_alpha = rust_alpha.clip(0.0, 0.92)

    core = _smoothstep(0.58, 0.92, field)
    metal_alpha = _smoothstep(0.76, 0.965, field) * rust_alpha
    metal_alpha = (metal_alpha * 0.96).clip(0.0, 0.96)

    rust_rgb = np.zeros((size, size, 3), dtype=np.float32)
    dark = np.array(RUST_DARK[:3], dtype=np.float32)
    red = np.array(RUST_RED[:3], dtype=np.float32)
    orange = np.array(RUST_ORANGE[:3], dtype=np.float32)
    warm = red * (1.0 - grain[..., None]) + orange * grain[..., None]
    rust_rgb[:] = warm * (1.0 - core[..., None] * 0.55) + dark * (core[..., None] * 0.55)

    rust_color = np.zeros((size, size, 4), dtype=np.float32)
    rust_color[..., :3] = rust_rgb
    rust_color[..., 3] = rust_alpha

    rust_rough = np.zeros((size, size, 4), dtype=np.float32)
    rust_rough[..., :3] = (0.70 + core[..., None] * 0.30)
    rust_rough[..., 3] = rust_alpha

    steel = np.array(STEEL_DARK[:3], dtype=np.float32)
    gleam = np.array(STEEL_GLEAM[:3], dtype=np.float32)
    metal_rgb = steel * (1.0 - core[..., None] * 0.32) + gleam * (core[..., None] * 0.32)

    metal_color = np.zeros((size, size, 4), dtype=np.float32)
    metal_color[..., :3] = metal_rgb
    metal_color[..., 3] = metal_alpha

    metal_scalar = np.zeros((size, size, 4), dtype=np.float32)
    metal_scalar[..., :3] = 1.0
    metal_scalar[..., 3] = metal_alpha

    metal_polish = np.zeros((size, size, 4), dtype=np.float32)
    metal_polish[..., :3] = 0.72
    metal_polish[..., 3] = metal_alpha

    return {
        "rust_color": rust_color,
        "rust_rough": rust_rough,
        "metal_color": metal_color,
        "metal_scalar": metal_scalar,
        "metal_polish": metal_polish,
    }


def _write_layer_image(layer, image_name, rgba):
    import numpy as np

    height, width = rgba.shape[:2]
    img = layer.image
    if img is None or tuple(img.size) != (width, height):
        img = bpy.data.images.new(image_name, width=width, height=height,
                                  alpha=True, float_buffer=False)
        layer.image_name = img.name
    else:
        img.name = image_name
        layer.image_name = img.name

    try:
        img.alpha_mode = "STRAIGHT"
    except Exception:
        pass
    try:
        img.colorspace_settings.name = "sRGB"
    except Exception:
        pass
    img.use_fake_user = True
    img.pixels.foreach_set(np.asarray(rgba, dtype=np.float32).ravel())
    img.update()
    try:
        img.update_tag()
    except Exception:
        pass
    return img


def _set_noise(layer, scale, detail, roughness, lacunarity=2.0,
               distortion=0.0, contrast=0.5):
    layer.proc_scale = scale
    layer.proc_detail = detail
    layer.proc_roughness_proc = roughness
    layer.proc_lacunarity = lacunarity
    layer.proc_distortion = distortion
    layer.proc_contrast = contrast


def build_reverse_yellow_rust_paint():
    obj = _active_mesh_or_preview_cube()
    _ensure_uv(obj)
    mat = _material_on_object(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    tlm.shader_editable = False
    _clear_layers(mat)
    maps = _make_wear_images(int(RESOLUTION))

    # Bottom to top. Keep this sparse: each layer should earn its cost.

    base = _add_fill(mat, "01 Paint Base - ochre yellow", PAINT_YELLOW)
    base.use_roughness = True
    base.roughness_fill = 0.62
    base.use_metallic = True
    base.metallic_fill = 0.0

    paint_var = _add_proc(mat, "02 Paint Clouding - broad uneven coat",
                          "NOISE", "BASE_COLOR", "OVERLAY", 0.12)
    _set_noise(paint_var, scale=2.4, detail=4.0, roughness=0.50,
               lacunarity=2.05, distortion=0.10, contrast=0.08)
    paint_var.proc_color1 = PAINT_DARK
    paint_var.proc_color2 = PAINT_LIGHT
    paint_var.use_roughness = True
    paint_var.roughness_fill = 0.08
    paint_var.blend_mode_roughness = "ADD"

    rust_color = _add_paint(mat, "03 PAINT generated rust islands",
                            "BASE_COLOR", "MIX", 0.96)
    _write_layer_image(rust_color, "TLM_RE_rust_islands_color",
                       maps["rust_color"])
    rust_color.use_bump = True
    rust_color.bump_strength = 0.085
    rust_color.bump_distance = 0.012

    rust_rough = _add_paint(mat, "04 PAINT generated rust roughness",
                            "ROUGHNESS", "ADD", 0.38)
    _write_layer_image(rust_rough, "TLM_RE_rust_islands_roughness",
                       maps["rust_rough"])

    metal_color = _add_paint(mat, "05 PAINT exposed steel color",
                             "BASE_COLOR", "MIX", 0.92)
    _write_layer_image(metal_color, "TLM_RE_exposed_steel_color",
                       maps["metal_color"])

    metal_m = _add_paint(mat, "06 PAINT exposed steel metallic",
                         "METALLIC", "MIX", 1.0)
    _write_layer_image(metal_m, "TLM_RE_exposed_steel_metallic",
                       maps["metal_scalar"])

    metal_polish = _add_paint(mat, "07 PAINT exposed steel polish",
                              "ROUGHNESS", "SUBTRACT", 0.26)
    _write_layer_image(metal_polish, "TLM_RE_exposed_steel_polish",
                       maps["metal_polish"])

    micro = _add_proc(mat, "08 Paint Micro Roughness Grain",
                      "WHITE_NOISE", "ROUGHNESS", "ADD", 0.055)
    micro.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    micro.proc_color2 = (1.0, 1.0, 1.0, 1.0)

    manual_rough = _add_paint(mat, "09 PAINT manual rough scratches",
                              "ROUGHNESS", "MIX", 0.85)
    manual_rough.paint_interpolation = "Linear"

    manual_color = _add_paint(mat, "10 PAINT manual color accents",
                              "BASE_COLOR", "MIX", 0.88)
    manual_color.paint_interpolation = "Linear"

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print("")
    print("[TLM] Reverse yellow rust paint material built.")
    print(f"Object:   {obj.name}")
    print(f"Material: {mat.name}")
    print("Manual paint pass:")
    print("  09 rough scratches: paint white/grey where rust should feel chalkier.")
    print("  10 color accents: add rust halos, silver chips, labels, or dark drips.")
    print("First tuning targets:")
    print("  If rust is too broad: lower layer 03 opacity to 0.75.")
    print("  If metal is too visible: lower layers 05/06/07 opacity.")
    print("  If cube looks flat: increase layer 03 bump distance to 0.020.")


if __name__ == "__main__":
    build_reverse_yellow_rust_paint()
