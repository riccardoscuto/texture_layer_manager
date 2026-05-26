"""
TLM Promo Starter - Carbon Fiber Clearcoat
=========================================

Minimal promotional material. Goal: immediately readable glossy carbon fiber,
not a heavy realism stack.

Run in Blender Text Editor. It creates a thin bevelled panel by default and
applies a 6-layer TLM material:

  01 Base graphite
  02 Generated twill weave image
  03 Clearcoat roughness breakup
  04 Fresnel edge sheen
  05 Paint clearcoat scratches
"""

import bpy
import math

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


MATERIAL_NAME = "TLM_Promo_Carbon_Clearcoat"
PREVIEW_OBJECT_NAME = "TLM_Promo_Carbon_Panel"
RESOLUTION = "1024"
USE_SELECTED_MESH = False


# Color and PBR target values.
BASE_GRAPHITE = (0.010, 0.011, 0.013, 1.0)
THREAD_DARK = (0.004, 0.004, 0.006, 1.0)
THREAD_LIGHT = (0.135, 0.140, 0.155, 1.0)
FIBER_LIGHT = (0.090, 0.095, 0.110, 1.0)
EDGE_SHEEN = (0.42, 0.48, 0.58, 1.0)

BASE_ROUGHNESS = 0.115
BASE_METALLIC = 0.0
WEAVE_IMAGE_NAME = "TLM_Carbon_Twill_Weave_1024"
WEAVE_IMAGE_SIZE = 1024


def _preview_object():
    obj = bpy.context.active_object
    if USE_SELECTED_MESH and obj and obj.type == "MESH":
        _ensure_planar_uv(obj)
        return obj

    existing = bpy.data.objects.get(PREVIEW_OBJECT_NAME)
    if existing and existing.type == "MESH":
        bpy.ops.object.select_all(action="DESELECT")
        existing.select_set(True)
        bpy.context.view_layer.objects.active = existing
        _ensure_planar_uv(existing)
        return existing

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0.05))
    obj = bpy.context.object
    obj.name = PREVIEW_OBJECT_NAME
    obj.scale = (1.8, 1.1, 0.08)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    bevel = obj.modifiers.new("Preview Soft Bevel", "BEVEL")
    bevel.width = 0.025
    bevel.segments = 3
    bevel.profile = 0.55
    try:
        obj.modifiers.new("Preview Weighted Normals", "WEIGHTED_NORMAL")
    except Exception:
        pass
    _ensure_planar_uv(obj)
    return obj


def _ensure_planar_uv(obj):
    mesh = obj.data
    uv_layer = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 0.001)
    span_y = max(max_y - min_y, 0.001)
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            loop = mesh.loops[loop_index]
            co = mesh.vertices[loop.vertex_index].co
            uv_layer.data[loop_index].uv = (
                (co.x - min_x) / span_x,
                (co.y - min_y) / span_y,
            )


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


def _frac(value):
    return value - math.floor(value)


def _smoothstep(edge0, edge1, value):
    if edge0 == edge1:
        return 1.0 if value >= edge1 else 0.0
    t = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _make_carbon_twill_image(size=WEAVE_IMAGE_SIZE):
    """Generate a compact 2x2 twill-like carbon weave bitmap."""
    img = bpy.data.images.get(WEAVE_IMAGE_NAME)
    if img is None or tuple(img.size) != (size, size):
        img = bpy.data.images.new(WEAVE_IMAGE_NAME, size, size, alpha=True)

    repeats = 14.0
    pixels = [0.0] * (size * size * 4)
    inv = 1.0 / max(size - 1, 1)
    for y in range(size):
        v = y * inv
        for x in range(size):
            u = x * inv

            gx = u * repeats
            gy = v * repeats
            cx = math.floor(gx)
            cy = math.floor(gy)
            fu = _frac(gx)
            fv = _frac(gy)

            # 2x2 twill illusion: local diagonal fibers change phase in a
            # four-cell cycle, avoiding the continuous stripe look.
            phase = (cx + cy) % 4
            slash = phase in (0, 1)
            diag = abs((fu - fv) if slash else (fu + fv - 1.0))
            tow = 1.0 - _smoothstep(0.12, 0.42, diag)

            # Fine filaments inside the visible tow.
            filament_axis = fu if slash else fv
            filament = 0.5 + 0.5 * math.sin(filament_axis * math.tau * 18.0)
            filament = filament ** 2.0

            # Dark seams around each tow cell.
            edge = min(fu, fv, 1.0 - fu, 1.0 - fv)
            groove = 1.0 - _smoothstep(0.015, 0.075, edge)

            # Broad alternating diagonal value shift gives the over/under
            # carbon tow impression.
            over = 1.0 if phase in (0, 3) else 0.0
            value = 0.010
            value += tow * (0.040 + 0.030 * over)
            value += tow * filament * 0.030
            value -= groove * 0.020
            value = max(0.002, min(0.155, value))

            # Slight cool tint, darker blue in troughs.
            i = (y * size + x) * 4
            pixels[i + 0] = value * 0.86
            pixels[i + 1] = value * 0.90
            pixels[i + 2] = value
            pixels[i + 3] = 1.0

    img.pixels.foreach_set(pixels)
    img.use_fake_user = True
    try:
        img.alpha_mode = "STRAIGHT"
        img.colorspace_settings.name = "sRGB"
        img.update()
        img.update_tag()
    except Exception:
        pass
    return img


def _set_bsdf_input(mat, socket_names, value):
    if isinstance(socket_names, str):
        socket_names = [socket_names]
    nt = mat.node_tree
    if not nt:
        return
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if not bsdf:
        return
    for name in socket_names:
        socket = bsdf.inputs.get(name)
        if socket is not None and not socket.is_linked:
            try:
                socket.default_value = value
            except Exception:
                pass


def build_carbon_clearcoat():
    obj = _preview_object()
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    mat = _material(obj)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    try:
        tlm.uv_map = "UVMap"
    except Exception:
        pass
    tlm.auto_composite = False
    _clear_layers(mat)
    weave_img = _make_carbon_twill_image()

    base = _add_fill(mat, "01 Base Graphite", BASE_GRAPHITE)
    base.use_roughness = True
    base.roughness_fill = BASE_ROUGHNESS
    base.use_metallic = True
    base.metallic_fill = BASE_METALLIC

    weave = _add_paint(mat, "02 Generated Twill Weave", opacity=1.0,
                       blend="MIX", output="BASE_COLOR")
    weave.image_name = weave_img.name
    weave.paint_extension = "REPEAT"
    weave.paint_interpolation = "Cubic"
    weave.paint_projection = "FLAT"
    weave.use_bump = True
    weave.bump_strength = 0.09
    weave.bump_distance = 0.0035

    rough_breakup = _add_proc(mat, "03 Clearcoat Roughness Breakup", "NOISE",
                              opacity=0.045, blend="ADD",
                              output="ROUGHNESS")
    rough_breakup.proc_coord_type = "GENERATED"
    rough_breakup.proc_scale = 32.0
    rough_breakup.proc_detail = 8.0
    rough_breakup.proc_roughness_proc = 0.56
    rough_breakup.proc_lacunarity = 2.2
    rough_breakup.proc_distortion = 0.15

    sheen = _add_fill(mat, "04 Fresnel Clearcoat Sheen", EDGE_SHEEN,
                      opacity=0.075, blend="SCREEN")
    sheen.use_fresnel_mask = True
    sheen.fresnel_ior = 1.72
    sheen.fresnel_strength = 1.0

    paint = _add_paint(mat, "05 Paint Clearcoat Scratches",
                       opacity=0.55, blend="ADD",
                       output="ROUGHNESS")
    paint.paint_interpolation = "Linear"
    paint.paint_projection = "FLAT"

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    # Direct Principled clearcoat controls. TLM owns the main channels; these
    # sockets stay as material-level polish for the preview.
    _set_bsdf_input(mat, ["Coat Weight", "Clearcoat"], 0.65)
    _set_bsdf_input(mat, ["Coat Roughness", "Clearcoat Roughness"], 0.045)
    _set_bsdf_input(mat, ["Specular IOR Level", "Specular"], 0.72)

    print("[TLM] Carbon fiber clearcoat starter built.")
    print(f"Object:   {obj.name}")
    print(f"Material: {mat.name}")
    print("Paint layer: 05 Paint Clearcoat Scratches")
    print("  Use thin white/gray strokes only where you want visible clearcoat wear.")


if __name__ == "__main__":
    build_carbon_clearcoat()
