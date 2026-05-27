"""Procedural pattern builders + mapping/coord helpers.

This module builds the actual shader-node graph for each procedural
type (NOISE / VORONOI / WAVE / MARBLE / MUSGRAVE / GRADIENT / CHECKER /
BRICK / MAGIC / STRIPES / HEX_GRID / GABOR / DOTS / RIDGED / CRACKS).

Two parallel paths:
  * ``_build_procedural_node`` — colour path, returns (color_socket,
    alpha_socket). Used by the BASE_COLOR + EMISSION channel builders.
  * ``_build_proc_fac_node``   — scalar fac path, returns single
    float socket. Used by ROUGHNESS / METALLIC / TRANSMISSION /
    ALPHA / BUMP / DISPLACEMENT channel builders.

Plus the support functions:
  * Mapping helpers: _proc_mapping_scale, _apply_mapping_settings,
    _set_wave_direction, _set_voronoi_fractal_inputs, _set_wave_inputs
  * Coordinate transforms: _inject_coord_normalization (Object-coord
    auto-fit), _inject_coord_transform (polar / cylindrical / spherical
    / swirl), _inject_vector_distortion (per-layer warp)
  * Fragments: _build_emission_selector (selective region gating),
    _voronoi_fac (Voronoi → 0..1 scalar), _build_fresnel_mask (legacy
    rim modifier).
"""

import bpy

# Late imports — parent __init__ defines these before doing
# ``from .procedurals import *`` at end of body.
from . import (
    _next_id,
    _tag,
    _factor_socket,
    _a_socket,
    _b_socket,
    _result_socket,
    _enabled_socket,
    # _set_factor lives in channels.py (loaded AFTER procedurals) —
    # procedurals doesn't call it directly, only docstrings mention it.
)
from . import TLM_PREFIX  # noqa: F401


__all__ = [
    '_build_proc_color_ramp',
    '_ramp_stops',
    '_proc_mapping_scale',
    '_set_input_default',
    '_apply_mapping_settings',
    '_set_wave_direction',
    '_set_voronoi_fractal_inputs',
    '_set_wave_inputs',
    '_ALL_CHANNELS',
    '_inject_coord_normalization',
    '_inject_coord_transform',
    '_inject_vector_distortion',
    '_LEGACY_BLEND_METHOD',
    '_NEW_RENDER_METHOD',
    '_build_emission_selector',
    '_voronoi_fac',
    '_build_fresnel_mask',
    '_build_procedural_node',
    '_build_proc_fac_node',
]

def _build_proc_color_ramp(node_tree, layer, x, y, fac_out):
    """Build the standard TLM ColorRamp for a procedural layer.

    Centralised so every proc_type that has a ColorRamp behaves the
    same way — Manual Stops, color mode, interpolation, extra stops,
    Color 3 legacy. Previously the MARBLE branch had its own inline
    copy of this logic that fell out of sync as new features landed.

    Returns the ColorRamp.outputs["Color"] socket.
    """
    cr = node_tree.nodes.new("ShaderNodeValToRGB")
    cr.name = f"{TLM_PREFIX}proc_cr_{_next_id()}"
    cr.label = "Proc Color"
    cr.location = (x + 180, y)
    _tag(cr, layer.name, "proc_cr")

    # Stop positions: two modes — Manual or computed-from-contrast.
    # GRADIENT + FRESNEL force contrast=0, center=0.5 so the ColorRamp
    # stops sit at the full 0..1 range. Without this, Fresnel.Fac (which
    # follows Schlick's non-linear distribution — most pixels concentrate
    # below 0.255 except at grazing angles) maps almost entirely to
    # color1 and color2 is never visible. Treating Fresnel like a real
    # gradient gives the expected face-to-edge sweep with user-set IOR.
    if getattr(layer, 'proc_use_manual_stops', False) and layer.proc_type not in ('GRADIENT', 'FRESNEL'):
        pos1 = max(0.0, min(1.0, getattr(layer, 'proc_color1_position', 0.0)))
        pos2 = max(0.0, min(1.0, getattr(layer, 'proc_color2_position', 1.0)))
        if abs(pos1 - pos2) < 1e-4:
            pos2 = min(1.0, pos1 + 0.001)
        stop_lo, stop_hi = pos1, pos2
    else:
        contrast = getattr(layer, 'proc_contrast', 0.5)
        center = getattr(layer, 'proc_ramp_center', 0.5)
        if layer.proc_type in ('GRADIENT', 'FRESNEL'):
            contrast = 0.0
            center = 0.5
        stop_lo, stop_hi = _ramp_stops(contrast, center)

    cr.color_ramp.elements[0].position = stop_lo
    cr.color_ramp.elements[0].color = layer.proc_color1
    cr.color_ramp.elements[1].position = stop_hi
    cr.color_ramp.elements[1].color = layer.proc_color2

    # Legacy Color 3 (kept for backward compat — migrated to extras on load).
    if getattr(layer, 'use_proc_color3', False):
        el = cr.color_ramp.elements.new(_clamped_ramp_position(layer.proc_color3_position))
        el.color = layer.proc_color3

    # Extra colour stops collection — N stops beyond Color1/Color2/Color3.
    for extra in getattr(layer, 'proc_extra_color_stops', []):
        el = cr.color_ramp.elements.new(_clamped_ramp_position(extra.position))
        el.color = extra.color

    # color_mode + interpolation — apply 1:1 to ShaderNodeValToRGB.
    try:
        cr.color_ramp.color_mode = getattr(layer, 'proc_color_ramp_mode', 'RGB')
    except (TypeError, AttributeError):
        pass
    try:
        cr.color_ramp.interpolation = getattr(
            layer, 'proc_color_ramp_interpolation', 'LINEAR'
        )
    except (TypeError, AttributeError):
        pass

    node_tree.links.new(fac_out, cr.inputs["Fac"])
    return cr.outputs["Color"]


def _ramp_stops(contrast, center=0.5):
    """Compute the two outer ColorRamp stop positions from contrast + center.

    - ``contrast`` 0..1 → narrows the transition band (high contrast = sharp).
      contrast=0.0 → band spans the full 0..1 range (soft gradient).
      contrast=1.0 → band collapses to ~0.02 around ``center`` (razor sharp).
    - ``center`` 0..1 → where along Fac the band sits.
      center=0.5 → symmetric (legacy behaviour).
      center=0.05 → band near Fac=0 (e.g. thin Color1 outline at low-Fac region).
      center=0.95 → band near Fac=1 (thin highlight in high-Fac region).

    Returns (stop_lo, stop_hi) both clamped to [0.0, 1.0] with stop_lo < stop_hi.
    """
    contrast = max(0.0, min(1.0, float(contrast)))
    center = max(0.0, min(1.0, float(center)))
    half_width = 0.5 - contrast * 0.49  # 0.5 at contrast=0, 0.01 at contrast=1
    stop_lo = center - half_width
    stop_hi = center + half_width
    # Clamp to legal range and keep them ordered with at least 0.001 between.
    stop_lo = max(0.0, min(0.998, stop_lo))
    stop_hi = max(stop_lo + 0.001, min(1.0, stop_hi))
    return stop_lo, stop_hi




def _proc_mapping_scale(layer):
    """Mapping scale used by procedural types without a native Scale input."""
    return (
        getattr(layer, 'proc_mapping_scale_x', 1.0),
        getattr(layer, 'proc_mapping_scale_y', 1.0),
        getattr(layer, 'proc_mapping_scale_z', 1.0),
    )


def _set_input_default(node, input_name, value):
    sock = node.inputs.get(input_name)
    if sock is None:
        return False
    try:
        sock.default_value = value
        return True
    except Exception:
        return False


def _apply_mapping_settings(mapping, layer):
    try:
        mapping.vector_type = getattr(layer, 'proc_mapping_type', 'POINT')
    except Exception:
        pass
    _set_input_default(mapping, "Location", (
        getattr(layer, 'proc_offset_x', 0.0),
        getattr(layer, 'proc_offset_y', 0.0),
        getattr(layer, 'proc_offset_z', 0.0),
    ))
    _set_input_default(mapping, "Rotation", (
        getattr(layer, 'proc_rotation_x', 0.0),
        getattr(layer, 'proc_rotation_y', 0.0),
        getattr(layer, 'proc_rotation_z', 0.0),
    ))
    _set_input_default(mapping, "Scale", _proc_mapping_scale(layer))


def _set_wave_direction(wave, wave_type, bands_direction='X', rings_direction='X'):
    if wave_type == 'RINGS':
        try:
            wave.rings_direction = rings_direction
        except Exception:
            pass
    else:
        try:
            wave.bands_direction = bands_direction
        except Exception:
            pass


def _set_voronoi_fractal_inputs(node, layer):
    _set_input_default(node, "Detail", getattr(layer, 'proc_detail', 2.0))
    _set_input_default(node, "Roughness", getattr(layer, 'proc_roughness_proc', 0.5))
    _set_input_default(node, "Lacunarity", getattr(layer, 'proc_lacunarity', 2.0))


def _set_wave_inputs(node, layer):
    _set_input_default(node, "Scale", getattr(layer, 'proc_scale', 5.0))
    _set_input_default(node, "Distortion", getattr(layer, 'proc_distortion', 0.0))
    _set_input_default(node, "Detail", getattr(layer, 'proc_detail', 2.0))
    _set_input_default(node, "Detail Scale", getattr(layer, 'proc_wave_detail_scale', 1.0))
    _set_input_default(node, "Detail Roughness", getattr(layer, 'proc_wave_detail_roughness', 0.5))
    _set_input_default(node, "Phase Offset", getattr(layer, 'proc_wave_phase_offset', 0.0))

_ALL_CHANNELS = ("base_color", "roughness", "metallic", "normal",
                 "emission", "transmission", "alpha", "bump")
# NOTE: "alpha" was missing here for a while. _set_factor tags alpha
# mix nodes with `opacity_target_alpha`, but _hot_opacity walks this
# tuple — without "alpha" the alpha mix's Factor stayed stale on every
# opacity change (the visible symptom: layers with use_alpha enabled
# appeared to "ignore" opacity until a full rebuild ran).


def _inject_coord_normalization(node_tree, layer, coord_out, x, y, name_tag=""):
    """Optionally normalize Object coordinates by object dimensions.

    When proc_normalize_coords is True and coord_type is OBJECT, inserts a
    VectorMath MULTIPLY node that compensates for the object's bounding box
    proportions.  This makes the pattern scale-independent: a small sphere
    and a large sphere get a similar-looking Voronoi pattern.

    Returns the output socket (normalized or pass-through unchanged).
    """
    if not getattr(layer, 'proc_normalize_coords', False):
        return coord_out
    if getattr(layer, 'proc_coord_type', 'GENERATED') != 'OBJECT':
        return coord_out

    import bpy
    # Find an object that uses this material (material can be on multiple objects;
    # we pick the first one found — usually the active object during editing)
    mat = node_tree.id_data  # the Material owning this node_tree
    obj = None
    if mat:
        for o in bpy.data.objects:
            for slot in o.material_slots:
                if slot.material == mat:
                    obj = o
                    break
            if obj:
                break

    if not obj:
        return coord_out

    dims = obj.dimensions
    max_dim = max(dims.x, dims.y, dims.z, 0.001)
    # Compensation factors: multiply each axis to equalize proportions
    norm_x = max_dim / max(dims.x, 0.001)
    norm_y = max_dim / max(dims.y, 0.001)
    norm_z = max_dim / max(dims.z, 0.001)

    mul = node_tree.nodes.new("ShaderNodeVectorMath")
    mul.operation = 'MULTIPLY'
    mul.name = f"{TLM_PREFIX}coord_norm_{name_tag}_{_next_id()}"
    mul.location = (x - 400, y - 60)
    mul.inputs[1].default_value = (norm_x, norm_y, norm_z)
    node_tree.links.new(coord_out, mul.inputs[0])
    _tag(mul, layer.name, "coord_norm")

    return mul.outputs["Vector"]


# ── Coordinate transform helper (polar / spherical / swirl / cylindrical) ────

def _inject_coord_transform(node_tree, layer, coord_out, x, y, name_tag=""):
    """Apply a polar/spherical/swirl/cylindrical coordinate transformation.

    Converts the input Cartesian vector into a transformed space that creates
    circular, spherical, or spiral texture patterns.  The transform is applied
    BEFORE the Mapping node so that Location/Scale in Mapping act on the
    transformed coordinates (intuitive angular/radial control).

    NONE         → passthrough (returns coord_out unchanged)
    POLAR        → (atan2(y,x)/2Ï€ + 0.5, sqrt(xÂ²+yÂ²), z)
    SPHERICAL    → (atan2(y,x)/2Ï€ + 0.5, acos(z/r)/Ï€, 0)
    SWIRL        → (x,y) rotated around Z by amount*radius, z preserved
    CYLINDRICAL  → (atan2(y,x)/2Ï€ + 0.5, z, sqrt(xÂ²+yÂ²))

    Returns the vector output socket to feed into Mapping.inputs["Vector"].
    """
    import math
    transform = getattr(layer, 'proc_coord_transform', 'NONE')
    if transform == 'NONE':
        return coord_out

    # Common: SeparateXYZ
    sep = node_tree.nodes.new("ShaderNodeSeparateXYZ")
    sep.name = f"{TLM_PREFIX}ctx_sep_{name_tag}_{_next_id()}"
    sep.location = (x - 420, y - 60)
    _tag(sep, layer.name, f"ctx_sep_{name_tag}")
    node_tree.links.new(coord_out, sep.inputs["Vector"])
    x_sock = sep.outputs["X"]
    y_sock = sep.outputs["Y"]
    z_sock = sep.outputs["Z"]

    # Common: CombineXYZ at the end
    comb = node_tree.nodes.new("ShaderNodeCombineXYZ")
    comb.name = f"{TLM_PREFIX}ctx_comb_{name_tag}_{_next_id()}"
    comb.location = (x - 120, y - 60)
    _tag(comb, layer.name, f"ctx_comb_{name_tag}")

    two_pi = 2.0 * math.pi
    inv_2pi = 1.0 / two_pi

    # Helper: build atan2(y, x) → Math node, returns output socket
    def _atan2(yy, xx, ny=30):
        n = node_tree.nodes.new("ShaderNodeMath")
        n.operation = 'ARCTAN2'
        n.name = f"{TLM_PREFIX}ctx_atan_{name_tag}_{_next_id()}"
        n.location = (x - 360, y - ny)
        node_tree.links.new(yy, n.inputs[0])
        node_tree.links.new(xx, n.inputs[1])
        return n.outputs[0]

    # Helper: normalize angle atan_out → atan/(2Ï€) + 0.5, returns socket
    def _norm_angle(atan_out, ny=30):
        n = node_tree.nodes.new("ShaderNodeMath")
        n.operation = 'MULTIPLY_ADD'
        n.name = f"{TLM_PREFIX}ctx_ang_{name_tag}_{_next_id()}"
        n.location = (x - 280, y - ny)
        node_tree.links.new(atan_out, n.inputs[0])
        n.inputs[1].default_value = inv_2pi
        n.inputs[2].default_value = 0.5
        return n.outputs[0]

    # Helper: radius_xy = sqrt(xÂ² + yÂ²), returns socket
    def _radius_xy(ny=120):
        xs = node_tree.nodes.new("ShaderNodeMath")
        xs.operation = 'MULTIPLY'
        xs.name = f"{TLM_PREFIX}ctx_radxy_xs_{name_tag}_{_next_id()}"
        xs.location = (x - 380, y - ny)
        node_tree.links.new(x_sock, xs.inputs[0])
        node_tree.links.new(x_sock, xs.inputs[1])

        ys = node_tree.nodes.new("ShaderNodeMath")
        ys.operation = 'MULTIPLY'
        ys.name = f"{TLM_PREFIX}ctx_radxy_ys_{name_tag}_{_next_id()}"
        ys.location = (x - 380, y - ny - 40)
        node_tree.links.new(y_sock, ys.inputs[0])
        node_tree.links.new(y_sock, ys.inputs[1])

        ad = node_tree.nodes.new("ShaderNodeMath")
        ad.operation = 'ADD'
        ad.name = f"{TLM_PREFIX}ctx_radxy_ad_{name_tag}_{_next_id()}"
        ad.location = (x - 320, y - ny - 20)
        node_tree.links.new(xs.outputs[0], ad.inputs[0])
        node_tree.links.new(ys.outputs[0], ad.inputs[1])

        sq = node_tree.nodes.new("ShaderNodeMath")
        sq.operation = 'SQRT'
        sq.name = f"{TLM_PREFIX}ctx_rad_{name_tag}_{_next_id()}"
        sq.location = (x - 260, y - ny - 20)
        node_tree.links.new(ad.outputs[0], sq.inputs[0])
        return sq.outputs[0]

    if transform == 'POLAR':
        # X = angle/(2Ï€) + 0.5 âˆˆ [0,1]  (horizontal wrap)
        # Y = radius_xy                  (distance from Z axis)
        # Z = z                          (preserved)
        angle = _norm_angle(_atan2(y_sock, x_sock))
        radius = _radius_xy()
        node_tree.links.new(angle, comb.inputs["X"])
        node_tree.links.new(radius, comb.inputs["Y"])
        node_tree.links.new(z_sock, comb.inputs["Z"])

    elif transform == 'SPHERICAL':
        # X = phi/(2Ï€) + 0.5 âˆˆ [0,1]   (longitude / horizontal wrap)
        # Y = theta/Ï€ âˆˆ [0,1]          (latitude / pole-to-pole)
        # Z = 0                        (no third axis)
        angle = _norm_angle(_atan2(y_sock, x_sock))

        # radius3d = sqrt(xÂ² + yÂ² + zÂ²) via VectorMath LENGTH
        length = node_tree.nodes.new("ShaderNodeVectorMath")
        length.operation = 'LENGTH'
        length.name = f"{TLM_PREFIX}ctx_len_{name_tag}_{_next_id()}"
        length.location = (x - 360, y - 150)
        node_tree.links.new(coord_out, length.inputs[0])
        # LENGTH puts scalar on outputs["Value"]
        radius3d = length.outputs["Value"]

        # theta = acos(z / radius3d)
        zdr = node_tree.nodes.new("ShaderNodeMath")
        zdr.operation = 'DIVIDE'
        zdr.name = f"{TLM_PREFIX}ctx_zdr_{name_tag}_{_next_id()}"
        zdr.location = (x - 300, y - 180)
        zdr.use_clamp = True  # prevent NaN from acos if z/r slightly out of [-1,1]
        node_tree.links.new(z_sock, zdr.inputs[0])
        node_tree.links.new(radius3d, zdr.inputs[1])

        acos = node_tree.nodes.new("ShaderNodeMath")
        acos.operation = 'ARCCOSINE'
        acos.name = f"{TLM_PREFIX}ctx_acos_{name_tag}_{_next_id()}"
        acos.location = (x - 240, y - 180)
        node_tree.links.new(zdr.outputs[0], acos.inputs[0])

        theta = node_tree.nodes.new("ShaderNodeMath")
        theta.operation = 'DIVIDE'
        theta.name = f"{TLM_PREFIX}ctx_theta_{name_tag}_{_next_id()}"
        theta.location = (x - 180, y - 180)
        node_tree.links.new(acos.outputs[0], theta.inputs[0])
        theta.inputs[1].default_value = math.pi

        node_tree.links.new(angle, comb.inputs["X"])
        node_tree.links.new(theta.outputs[0], comb.inputs["Y"])
        comb.inputs["Z"].default_value = 0.0

    elif transform == 'SWIRL':
        # Rotate (x, y) around Z by (amount * radius) — spiral twist.
        # new_angle = atan2(y, x) + amount * radius
        # new_x = radius * cos(new_angle)
        # new_y = radius * sin(new_angle)
        # z preserved
        amount = getattr(layer, 'proc_swirl_amount', 2.0)
        radius = _radius_xy(ny=130)
        base_angle = _atan2(y_sock, x_sock)

        # twist = radius * amount + base_angle
        twist = node_tree.nodes.new("ShaderNodeMath")
        twist.operation = 'MULTIPLY_ADD'
        twist.name = f"{TLM_PREFIX}ctx_twist_{name_tag}_{_next_id()}"
        twist.location = (x - 280, y - 30)
        node_tree.links.new(radius, twist.inputs[0])
        twist.inputs[1].default_value = amount
        node_tree.links.new(base_angle, twist.inputs[2])

        cos_n = node_tree.nodes.new("ShaderNodeMath")
        cos_n.operation = 'COSINE'
        cos_n.name = f"{TLM_PREFIX}ctx_swirl_cos_{name_tag}_{_next_id()}"
        cos_n.location = (x - 220, y - 30)
        node_tree.links.new(twist.outputs[0], cos_n.inputs[0])

        sin_n = node_tree.nodes.new("ShaderNodeMath")
        sin_n.operation = 'SINE'
        sin_n.name = f"{TLM_PREFIX}ctx_swirl_sin_{name_tag}_{_next_id()}"
        sin_n.location = (x - 220, y - 70)
        node_tree.links.new(twist.outputs[0], sin_n.inputs[0])

        nx = node_tree.nodes.new("ShaderNodeMath")
        nx.operation = 'MULTIPLY'
        nx.name = f"{TLM_PREFIX}ctx_swirl_nx_{name_tag}_{_next_id()}"
        nx.location = (x - 170, y - 30)
        node_tree.links.new(radius, nx.inputs[0])
        node_tree.links.new(cos_n.outputs[0], nx.inputs[1])

        ny_ = node_tree.nodes.new("ShaderNodeMath")
        ny_.operation = 'MULTIPLY'
        ny_.name = f"{TLM_PREFIX}ctx_swirl_ny_{name_tag}_{_next_id()}"
        ny_.location = (x - 170, y - 70)
        node_tree.links.new(radius, ny_.inputs[0])
        node_tree.links.new(sin_n.outputs[0], ny_.inputs[1])

        node_tree.links.new(nx.outputs[0], comb.inputs["X"])
        node_tree.links.new(ny_.outputs[0], comb.inputs["Y"])
        node_tree.links.new(z_sock, comb.inputs["Z"])

    elif transform == 'CYLINDRICAL':
        # X = angle/(2Ï€) + 0.5   (horizontal wrap around axis)
        # Y = z                  (vertical along cylinder)
        # Z = radius_xy          (distance from axis — useful as depth)
        angle = _norm_angle(_atan2(y_sock, x_sock))
        radius = _radius_xy()
        node_tree.links.new(angle, comb.inputs["X"])
        node_tree.links.new(z_sock, comb.inputs["Y"])
        node_tree.links.new(radius, comb.inputs["Z"])

    return comb.outputs["Vector"]


# ── Vector distortion helper ──────────────────────────────────────────────────

def _inject_vector_distortion(node_tree, layer, mapping_out, x, y, name_tag=""):
    """Optionally inject Noise-based vector coordinate distortion.

    Technique: Mapping → [Noise Texture → Mix(Linear Light, low Factor)] → Texture
    The Noise output is a Vector that warps the coordinates feeding the texture,
    turning geometric patterns (like Voronoi cells) into organic, natural shapes.

    Returns the vector output socket to feed into the texture node's Vector input.
    If proc_vector_distortion == 0, returns mapping_out unchanged (no extra nodes).
    """
    distortion = getattr(layer, 'proc_vector_distortion', 0.0)
    if distortion <= 0.0:
        return mapping_out

    # Noise texture to generate the distortion vector
    noise = node_tree.nodes.new("ShaderNodeTexNoise")
    noise.name = f"{TLM_PREFIX}vdist_noise_{name_tag}_{_next_id()}"
    noise.location = (x - 200, y - 180)
    # Use a scale relative to the layer's proc_scale for coherent distortion
    noise.inputs["Scale"].default_value = layer.proc_scale * 0.5
    noise.inputs["Detail"].default_value = 3.0
    noise.inputs["Roughness"].default_value = 0.6
    noise.inputs["Distortion"].default_value = 0.0
    _tag(noise, layer.name, "vdist_noise")
    node_tree.links.new(mapping_out, noise.inputs["Vector"])

    # Mix(Vector, Linear Light) with low factor to blend distortion into coordinates
    mix = node_tree.nodes.new("ShaderNodeMix")
    mix.data_type = 'VECTOR'
    mix.blend_type = 'LINEAR_LIGHT'
    mix.name = f"{TLM_PREFIX}vdist_mix_{name_tag}_{_next_id()}"
    mix.location = (x - 50, y - 180)
    mix.inputs["Factor"].default_value = distortion * 0.15  # scale down for usable range
    _tag(mix, layer.name, "vdist_mix")
    # Find the enabled Vector A and B sockets
    a_sock = _enabled_socket(mix.inputs, "A")
    b_sock = _enabled_socket(mix.inputs, "B")
    node_tree.links.new(mapping_out, a_sock)
    node_tree.links.new(noise.outputs["Color"], b_sock)

    return _enabled_socket(mix.outputs, "Result")


# ── Material-level alpha blend method ────────────────────────────────────────

# Map TLM's alpha_blend_method enum to:
#   - mat.blend_method (Blender ≤ 4.1, legacy enum)
#   - mat.surface_render_method (Blender 4.2+, replaces blend_method with
#     just DITHERED / BLENDED; CLIP and HASHED both collapse to DITHERED)
# AUTO is resolved by the caller before we get here.
_LEGACY_BLEND_METHOD = {
    'OPAQUE': 'OPAQUE',
    'CLIP':   'CLIP',
    'HASHED': 'HASHED',
    'BLEND':  'BLEND',
}
_NEW_RENDER_METHOD = {
    # Blender 4.2+ no longer exposes an OPAQUE surface_render_method. When
    # alpha is not connected, leave the new API untouched instead of writing
    # DITHERED: Cycles 5.0 can render TLM paint/fill materials black when a
    # dithered transparency mode is forced without a real alpha route.
    'OPAQUE': 'DITHERED',
    'CLIP':   'DITHERED',
    'HASHED': 'DITHERED',
    'BLEND':  'BLENDED',
}


def _build_emission_selector(node_tree, layer, uv_map, x, y, name_tag=""):
    """Build a 0..1 mask that gates where a procedural emission can glow.

    Returns a Value socket suitable for multiplying into the emission
    mask, or None when the selector is disabled (or invalid). The
    selector is applied AFTER the proc fac → invert → power → smoothstep
    pipeline, so the original threshold/falloff still shape each glow's
    falloff — the selector only decides which regions are allowed to
    light up at all.

    Three modes:
      - RANDOM_CELLS: Voronoi(F1) → WhiteNoise hash on cell position →
        threshold. Produces a per-cell on/off mask. The de-facto
        sci-fi-panel mode: "30% of the cells glow".
      - NOISE: Perlin noise → smoothstep threshold. Produces organic
        blob-shaped glow regions, good for damage / weathering hotspots.
      - IMAGE: sample a user-painted black/white image via UV. The R
        channel is the mask (white = lit). Lets the artist hand-craft
        an exact lighting pattern.
    """
    selector_type = getattr(layer, 'emission_selector_type', 'NONE')
    if selector_type == 'NONE':
        return None

    threshold = getattr(layer, 'emission_selector_threshold', 0.3)
    if threshold <= 0.0:
        # User asked for nothing lit — short-circuit with a 0 constant
        # so the multiplier produces an all-black emission mask.
        zero = node_tree.nodes.new("ShaderNodeValue")
        zero.name = f"{TLM_PREFIX}emis_sel_zero_{name_tag}_{_next_id()}"
        zero.location = (x + 480, y - 240)
        zero.outputs[0].default_value = 0.0
        return zero.outputs[0]

    scale = getattr(layer, 'emission_selector_scale', 4.0)
    seed = getattr(layer, 'emission_selector_seed', 0.0)

    if selector_type == 'IMAGE':
        img_name = getattr(layer, 'emission_selector_image_name', '')
        img = bpy.data.images.get(img_name) if img_name else None
        if img is None:
            return None  # no image assigned → treat as disabled
        uv = node_tree.nodes.new("ShaderNodeUVMap")
        uv.uv_map = uv_map
        uv.name = f"{TLM_PREFIX}emis_sel_uv_{name_tag}_{_next_id()}"
        uv.location = (x + 380, y - 260)

        tex = node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        try:
            if img.colorspace_settings.name != "Non-Color":
                img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        tex.name = f"{TLM_PREFIX}emis_sel_tex_{name_tag}_{_next_id()}"
        tex.location = (x + 540, y - 260)
        node_tree.links.new(uv.outputs["UV"], tex.inputs["Vector"])

        sep = node_tree.nodes.new("ShaderNodeSeparateColor")
        sep.name = f"{TLM_PREFIX}emis_sel_sep_{name_tag}_{_next_id()}"
        sep.location = (x + 720, y - 260)
        node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
        return sep.outputs["Red"]

    # For RANDOM_CELLS and NOISE we use the SAME UV/coord source as the
    # rest of the layer (UVMap → optional seed offset → procedural).
    uv = node_tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = uv_map
    uv.name = f"{TLM_PREFIX}emis_sel_uv_{name_tag}_{_next_id()}"
    uv.location = (x + 380, y - 260)
    vec_source = uv.outputs["UV"]
    if seed != 0.0:
        add = node_tree.nodes.new("ShaderNodeVectorMath")
        add.operation = 'ADD'
        add.name = f"{TLM_PREFIX}emis_sel_seed_{name_tag}_{_next_id()}"
        add.location = (x + 460, y - 260)
        add.inputs[1].default_value = (seed, seed * 1.7, seed * 2.3)
        node_tree.links.new(uv.outputs["UV"], add.inputs[0])
        vec_source = add.outputs["Vector"]

    if selector_type == 'RANDOM_CELLS':
        vor = node_tree.nodes.new("ShaderNodeTexVoronoi")
        try:
            vor.feature = 'F1'
        except Exception:
            pass
        try:
            vor.voronoi_dimensions = '3D'
        except Exception:
            pass
        vor.name = f"{TLM_PREFIX}emis_sel_vor_{name_tag}_{_next_id()}"
        vor.location = (x + 540, y - 260)
        vor.inputs["Scale"].default_value = scale
        if "Randomness" in vor.inputs:
            vor.inputs["Randomness"].default_value = 1.0
        node_tree.links.new(vec_source, vor.inputs["Vector"])

        # Hash the per-cell Position into a [0,1] random scalar.
        position_out = vor.outputs.get("Position") or vor.outputs[0]
        wn = node_tree.nodes.new("ShaderNodeTexWhiteNoise")
        wn.name = f"{TLM_PREFIX}emis_sel_wn_{name_tag}_{_next_id()}"
        wn.location = (x + 720, y - 260)
        node_tree.links.new(position_out, wn.inputs["Vector"])

        # GREATER_THAN(wn.Value, 1 - threshold). With threshold=0.3,
        # only cells whose hash > 0.7 light up → roughly 30% lit.
        cmp = node_tree.nodes.new("ShaderNodeMath")
        cmp.operation = 'GREATER_THAN'
        cmp.name = f"{TLM_PREFIX}emis_sel_cmp_{name_tag}_{_next_id()}"
        cmp.location = (x + 880, y - 260)
        node_tree.links.new(wn.outputs["Value"], cmp.inputs[0])
        cmp.inputs[1].default_value = 1.0 - threshold
        return cmp.outputs["Value"]

    if selector_type == 'NOISE':
        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.name = f"{TLM_PREFIX}emis_sel_noise_{name_tag}_{_next_id()}"
        noise.location = (x + 540, y - 260)
        noise.inputs["Scale"].default_value = scale
        noise.inputs["Detail"].default_value = 2.0
        if "Roughness" in noise.inputs:
            noise.inputs["Roughness"].default_value = 0.5
        node_tree.links.new(vec_source, noise.inputs["Vector"])

        # Smoothstep around (1-threshold) so threshold=fraction lit.
        # Fixed transition width keeps the blob edges soft.
        mr = node_tree.nodes.new("ShaderNodeMapRange")
        try:
            mr.interpolation_type = 'SMOOTHSTEP'
        except Exception:
            pass
        mr.clamp = True
        mr.name = f"{TLM_PREFIX}emis_sel_mr_{name_tag}_{_next_id()}"
        mr.location = (x + 720, y - 260)
        lo = max(0.0, (1.0 - threshold) - 0.07)
        hi = min(1.0, (1.0 - threshold) + 0.07)
        mr.inputs["From Min"].default_value = lo
        mr.inputs["From Max"].default_value = hi
        mr.inputs["To Min"].default_value = 0.0
        mr.inputs["To Max"].default_value = 1.0
        node_tree.links.new(noise.outputs["Fac"], mr.inputs["Value"])
        return mr.outputs.get("Result") or mr.outputs[0]

    return None


# ── Voronoi random-per-cell helper ───────────────────────────────────────────

def _voronoi_fac(node_tree, layer, tex_node, x, y, name_tag=""):
    """Return the Fac socket for a Voronoi node.

    Default: tex_node.outputs["Distance"] — smooth cell-distance gradient.
    Random-per-cell: tex_node.outputs["Position"] → WhiteNoise → Value,
    giving each cell a discrete random scalar that feeds the ColorRamp.
    """
    if not getattr(layer, 'proc_voronoi_random_color', False):
        return tex_node.outputs.get("Distance") or tex_node.outputs[0]

    position_out = tex_node.outputs.get("Position")
    if position_out is None:
        # Position not available — fall back gracefully
        return tex_node.outputs.get("Distance") or tex_node.outputs[0]

    seed = getattr(layer, 'proc_voronoi_random_seed', 0.0)

    vec_source = position_out
    if seed != 0.0:
        # Offset the position by a per-axis seed so the user can re-randomize
        # without changing the Voronoi cell layout itself.
        add = node_tree.nodes.new("ShaderNodeVectorMath")
        add.operation = 'ADD'
        add.name = f"{TLM_PREFIX}vornd_seed_{name_tag}_{_next_id()}"
        add.location = (x + 20, y - 140)
        add.inputs[1].default_value = (seed, seed * 1.7, seed * 2.3)
        node_tree.links.new(position_out, add.inputs[0])
        _tag(add, layer.name, f"vornd_seed_{name_tag}")
        vec_source = add.outputs["Vector"]

    wn = node_tree.nodes.new("ShaderNodeTexWhiteNoise")
    wn.name = f"{TLM_PREFIX}vornd_wn_{name_tag}_{_next_id()}"
    wn.location = (x + 80, y - 140)
    _tag(wn, layer.name, f"vornd_wn_{name_tag}")
    node_tree.links.new(vec_source, wn.inputs["Vector"])

    return wn.outputs["Value"]


# ── Fresnel mask helper ──────────────────────────────────────────────────────

def _build_fresnel_mask(node_tree, layer, x, y, name_tag=""):
    """Build a Fresnel node that outputs a 0-1 mask based on viewing angle.

    Returns the Fac output socket, or None if Fresnel is not enabled.
    Edge-on faces → 1.0 (visible), face-on → 0.0 (hidden).
    """
    if not getattr(layer, 'use_fresnel_mask', False):
        return None

    fresnel = node_tree.nodes.new("ShaderNodeFresnel")
    fresnel.name = f"{TLM_PREFIX}fresnel_{name_tag}_{_next_id()}"
    fresnel.location = (x - 200, y - 300)
    fresnel.inputs["IOR"].default_value = getattr(layer, 'fresnel_ior', 1.45)
    _tag(fresnel, layer.name, "fresnel")

    # ALWAYS create the strength multiplier node, even when strength==1.0.
    # Earlier we skipped node creation when strength was 1.0 (no-op), but
    # then _hot_fresnel() couldn't find a tagged node to update when the
    # user dragged the strength slider down — it returned True (silently
    # claiming success) so no fallback rebuild happened either, and the
    # slider had no visible effect. Creating the node unconditionally
    # makes the hot path work for the entire range.
    strength = getattr(layer, 'fresnel_strength', 1.0)
    mult = node_tree.nodes.new("ShaderNodeMath")
    mult.operation = 'MULTIPLY'
    mult.name = f"{TLM_PREFIX}fresnel_str_{name_tag}_{_next_id()}"
    mult.location = (x - 50, y - 300)
    mult.use_clamp = True
    _tag(mult, layer.name, "fresnel_str")
    node_tree.links.new(fresnel.outputs["Fac"], mult.inputs[0])
    mult.inputs[1].default_value = strength
    return mult.outputs["Value"]


# ── Procedural node builder ───────────────────────────────────────────────────

def _build_procedural_node(node_tree, layer, uv_map, x, y):
    """
    Build the shader nodes for a PROCEDURAL layer.
    Returns (color_out, alpha_out) sockets.
    The output is always a color: proc_color1 → proc_color2 mapped via the
    texture's Fac output through a ColorRamp for maximum control.
    """
    # ── Texture coordinate + mapping ─────────────────────────────────────
    tc = node_tree.nodes.new("ShaderNodeTexCoord")
    tc.name = f"{TLM_PREFIX}proc_tc_{_next_id()}"
    tc.location = (x - 500, y)
    _tag(tc, layer.name, "proc_tc")

    mapping = node_tree.nodes.new("ShaderNodeMapping")
    mapping.name = f"{TLM_PREFIX}proc_map_{_next_id()}"
    mapping.location = (x - 300, y)
    _apply_mapping_settings(mapping, layer)
    _tag(mapping, layer.name, "proc_map")
    # Select coordinate space based on layer setting
    coord_type = getattr(layer, 'proc_coord_type', 'GENERATED')
    if coord_type == 'UV':
        uv_node = node_tree.nodes.new("ShaderNodeUVMap")
        uv_node.name = f"{TLM_PREFIX}proc_uv_{_next_id()}"
        uv_node.uv_map = uv_map
        uv_node.location = (x - 500, y - 50)
        coord_out = uv_node.outputs["UV"]
    elif coord_type == 'OBJECT':
        coord_out = tc.outputs["Object"]
        coord_out = _inject_coord_normalization(
            node_tree, layer, coord_out, x, y, name_tag="proc"
        )
    else:  # GENERATED
        coord_out = tc.outputs["Generated"]
    # Apply polar/spherical/swirl/cylindrical coordinate transform
    coord_out = _inject_coord_transform(
        node_tree, layer, coord_out, x, y, name_tag="proc"
    )
    node_tree.links.new(coord_out, mapping.inputs["Vector"])

    # ── Vector distortion (organic coordinate warping) ────────────────────
    vec_out = _inject_vector_distortion(
        node_tree, layer, mapping.outputs["Vector"], x, y, name_tag="proc"
    )

    # ── Texture node ──────────────────────────────────────────────────────
    pt = layer.proc_type
    tex_node = None
    fac_out = None

    if pt == 'NOISE':
        tex_node = node_tree.nodes.new("ShaderNodeTexNoise")
        tex_node.inputs["Scale"].default_value      = layer.proc_scale
        tex_node.inputs["Detail"].default_value     = layer.proc_detail
        tex_node.inputs["Roughness"].default_value  = layer.proc_roughness_proc
        tex_node.inputs["Lacunarity"].default_value = layer.proc_lacunarity
        tex_node.inputs["Distortion"].default_value = layer.proc_distortion
        fac_out = tex_node.outputs["Fac"]

    elif pt == 'VORONOI':
        tex_node = node_tree.nodes.new("ShaderNodeTexVoronoi")
        tex_node.feature   = layer.proc_voronoi_feature
        tex_node.distance  = layer.proc_voronoi_distance
        tex_node.inputs["Scale"].default_value      = layer.proc_scale
        tex_node.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(tex_node, layer)
        # Normalize Distance output to 0-1 range (critical for ColorRamp mapping)
        if hasattr(tex_node, 'normalize'):
            tex_node.normalize = True
        # Fac: distance gradient (default) OR random-per-cell via WhiteNoise(Position)
        fac_out = _voronoi_fac(node_tree, layer, tex_node, x, y, name_tag="proc")

    elif pt == 'WAVE':
        tex_node = node_tree.nodes.new("ShaderNodeTexWave")
        tex_node.wave_type    = layer.proc_wave_type
        _set_wave_direction(
            tex_node,
            layer.proc_wave_type,
            getattr(layer, 'proc_wave_bands_direction', 'X'),
            getattr(layer, 'proc_wave_rings_direction', 'X'),
        )
        try:
            tex_node.wave_profile = layer.proc_wave_profile
        except Exception:
            pass
        _set_wave_inputs(tex_node, layer)
        fac_out = tex_node.outputs["Fac"]

    elif pt == 'GRADIENT':
        tex_node = node_tree.nodes.new("ShaderNodeTexGradient")
        tex_node.gradient_type = layer.proc_gradient_type
        fac_out = tex_node.outputs["Fac"]

    elif pt == 'MUSGRAVE':
        # Blender 4.1+ merged Musgrave into Noise
        try:
            tex_node = node_tree.nodes.new("ShaderNodeTexMusgrave")
            tex_node.inputs["Scale"].default_value      = layer.proc_scale
            tex_node.inputs["Detail"].default_value     = layer.proc_detail
            tex_node.inputs["Lacunarity"].default_value = layer.proc_lacunarity
            tex_node.inputs["Roughness"].default_value  = layer.proc_roughness_proc
            fac_out = tex_node.outputs["Fac"]
        except Exception:
            # Fallback to Noise if Musgrave unavailable
            tex_node = node_tree.nodes.new("ShaderNodeTexNoise")
            tex_node.inputs["Scale"].default_value      = layer.proc_scale
            tex_node.inputs["Detail"].default_value     = layer.proc_detail
            tex_node.inputs["Roughness"].default_value  = layer.proc_roughness_proc
            fac_out = tex_node.outputs["Fac"]

    elif pt == 'CHECKER':
        tex_node = node_tree.nodes.new("ShaderNodeTexChecker")
        tex_node.inputs["Scale"].default_value  = layer.proc_scale
        tex_node.inputs["Color1"].default_value = layer.proc_color1
        tex_node.inputs["Color2"].default_value = layer.proc_color2
        tex_node.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        tex_node.location = (x - 100, y)
        _tag(tex_node, layer.name, "proc_tex")
        node_tree.links.new(vec_out, tex_node.inputs["Vector"])
        # Checker already outputs Color directly
        return tex_node.outputs["Color"], None

    elif pt == 'BRICK':
        # ShaderNodeTexBrick — color1/color2 are brick variants, color3
        # (if use_proc_color3) is the mortar color. Otherwise mortar
        # falls back to a sensible default dark grey.
        tex_node = node_tree.nodes.new("ShaderNodeTexBrick")
        tex_node.offset           = layer.proc_brick_offset
        tex_node.offset_frequency = layer.proc_brick_offset_freq
        tex_node.squash           = layer.proc_brick_squash
        tex_node.squash_frequency = layer.proc_brick_squash_freq
        tex_node.inputs["Scale"].default_value         = layer.proc_scale
        tex_node.inputs["Color1"].default_value        = layer.proc_color1
        tex_node.inputs["Color2"].default_value        = layer.proc_color2
        if getattr(layer, 'use_proc_color3', False):
            tex_node.inputs["Mortar"].default_value    = layer.proc_color3
        else:
            tex_node.inputs["Mortar"].default_value    = (0.05, 0.05, 0.05, 1.0)
        tex_node.inputs["Mortar Size"].default_value   = layer.proc_brick_mortar_size
        tex_node.inputs["Mortar Smooth"].default_value = layer.proc_brick_mortar_smooth
        tex_node.inputs["Bias"].default_value          = layer.proc_brick_bias
        _set_input_default(tex_node, "Brick Width", getattr(layer, 'proc_brick_width', 0.5))
        _set_input_default(tex_node, "Row Height", getattr(layer, 'proc_brick_row_height', 0.25))
        tex_node.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        tex_node.location = (x - 100, y)
        _tag(tex_node, layer.name, "proc_tex")
        node_tree.links.new(vec_out, tex_node.inputs["Vector"])
        return tex_node.outputs["Color"], None

    elif pt == 'MAGIC':
        # ShaderNodeTexMagic — kaleidoscopic colored swirl. depth
        # controls fractal iterations; distortion warps the swirls.
        # Note: Magic uses its own dedicated proc_magic_distortion (not
        # the shared proc_distortion) because the canonical "swirl" look
        # needs distortion ≈ 1.0, while the shared default is 0.0 which
        # produces flat vertical bands instead of swirls.
        tex_node = node_tree.nodes.new("ShaderNodeTexMagic")
        tex_node.turbulence_depth = layer.proc_magic_depth
        tex_node.inputs["Scale"].default_value      = layer.proc_scale
        tex_node.inputs["Distortion"].default_value = layer.proc_magic_distortion
        tex_node.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        tex_node.location = (x - 100, y)
        _tag(tex_node, layer.name, "proc_tex")
        node_tree.links.new(vec_out, tex_node.inputs["Vector"])
        # Magic outputs Color directly (already saturated/coloured)
        return tex_node.outputs["Color"], None

    elif pt == 'WHITE_NOISE':
        # ShaderNodeTexWhiteNoise — pure per-pixel random. Uses the
        # standard Value output (scalar) as fac so the ColorRamp
        # downstream maps it via Color1 → Color2.
        tex_node = node_tree.nodes.new("ShaderNodeTexWhiteNoise")
        tex_node.noise_dimensions = '3D'
        fac_out = tex_node.outputs["Value"]

    elif pt == 'FRESNEL':
        # View-angle gradient procedural — the layer's fac comes from a
        # Fresnel node (0 facing the camera, 1 at grazing silhouette). The
        # standard ColorRamp downstream maps that gradient via Color1→Color2
        # with proc_contrast + proc_ramp_center shaping the transition band.
        # This unlocks iridescent / oil-slick / bubble / mother-of-pearl /
        # hologram materials where colour shifts with the viewing angle.
        # IOR controlled by proc_fresnel_ior (low = wide sweep, high = narrow).
        tex_node = node_tree.nodes.new("ShaderNodeFresnel")
        tex_node.inputs["IOR"].default_value = getattr(layer, 'proc_fresnel_ior', 1.45)
        fac_out = tex_node.outputs["Fac"]

    elif pt == 'DOTS':
        # Packed circles: Voronoi F1 distance thresholded into discs.
        # The Distance output is the Euclidean distance to the nearest
        # cell centre, normalized; a smoothstep around `radius` carves
        # out circular dots inside each cell. `softness` is the half-
        # width of the smoothstep, so 0 = hard, 1 = full halo.
        vor = node_tree.nodes.new("ShaderNodeTexVoronoi")
        try:
            vor.feature = 'F1'
        except Exception:
            pass
        try:
            vor.voronoi_dimensions = '3D'
        except Exception:
            pass
        if hasattr(vor, 'normalize'):
            vor.normalize = True
        vor.inputs["Scale"].default_value      = layer.proc_scale
        vor.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(vor, layer)
        vor.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        vor.location = (x - 100, y)
        _tag(vor, layer.name, "proc_tex")
        node_tree.links.new(vec_out, vor.inputs["Vector"])

        radius = getattr(layer, 'proc_dots_radius', 0.35)
        softness = getattr(layer, 'proc_dots_softness', 0.15)
        half_band = max(0.005, softness * 0.5)

        mr = node_tree.nodes.new("ShaderNodeMapRange")
        try:
            mr.interpolation_type = 'SMOOTHSTEP'
        except Exception:
            pass
        mr.clamp = True
        # Inside dot (distance < radius) → 1.0; outside (> radius) → 0.0.
        mr.inputs["From Min"].default_value = max(0.0, radius - half_band)
        mr.inputs["From Max"].default_value = min(1.0, radius + half_band)
        mr.inputs["To Min"].default_value   = 1.0
        mr.inputs["To Max"].default_value   = 0.0
        mr.location = (x + 50, y)
        mr.name = f"{TLM_PREFIX}proc_dots_mr_{_next_id()}"
        _tag(mr, layer.name, "proc_dots_mr")
        node_tree.links.new(vor.outputs["Distance"], mr.inputs["Value"])
        # The post-elif code (line ~4336) does:
        #   if tex_node is None: return None, None
        # so we MUST point tex_node at the source texture or the whole
        # branch silently fails to produce a color output. The post-elif
        # then re-renames/relocates/links the texture (idempotent with
        # what we already did) and builds the ColorRamp around fac_out.
        tex_node = vor
        fac_out = mr.outputs["Result"]

    elif pt == 'RIDGED':
        # Classic ridged fractal: 1 - |2*noise - 1|, raised to a power,
        # multiplied by an intensity offset. Produces clean razor-like
        # crests — ideal for mountain ridges, rock veins, lightning,
        # crackle. Five-stage math chain; idempotent under tweaks.
        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value      = layer.proc_scale
        noise.inputs["Detail"].default_value     = layer.proc_detail
        noise.inputs["Roughness"].default_value  = layer.proc_roughness_proc
        noise.inputs["Lacunarity"].default_value = layer.proc_lacunarity
        noise.inputs["Distortion"].default_value = layer.proc_distortion
        noise.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        noise.location = (x - 100, y)
        _tag(noise, layer.name, "proc_tex")
        node_tree.links.new(vec_out, noise.inputs["Vector"])

        fold = node_tree.nodes.new("ShaderNodeMath")
        fold.operation = 'MULTIPLY_ADD'
        fold.name = f"{TLM_PREFIX}proc_ridge_fold_{_next_id()}"
        fold.location = (x + 60, y)
        fold.inputs[1].default_value = 2.0   # *2
        fold.inputs[2].default_value = -1.0  # -1 → range -1..1
        node_tree.links.new(noise.outputs["Fac"], fold.inputs[0])
        _tag(fold, layer.name, "proc_ridge_fold")

        abs_n = node_tree.nodes.new("ShaderNodeMath")
        abs_n.operation = 'ABSOLUTE'
        abs_n.name = f"{TLM_PREFIX}proc_ridge_abs_{_next_id()}"
        abs_n.location = (x + 160, y)
        node_tree.links.new(fold.outputs[0], abs_n.inputs[0])
        _tag(abs_n, layer.name, "proc_ridge_abs")

        inv = node_tree.nodes.new("ShaderNodeMath")
        inv.operation = 'SUBTRACT'
        inv.use_clamp = True
        inv.name = f"{TLM_PREFIX}proc_ridge_inv_{_next_id()}"
        inv.location = (x + 260, y)
        inv.inputs[0].default_value = 1.0   # 1 - |…|
        node_tree.links.new(abs_n.outputs[0], inv.inputs[1])
        _tag(inv, layer.name, "proc_ridge_inv")

        pow_n = node_tree.nodes.new("ShaderNodeMath")
        pow_n.operation = 'POWER'
        pow_n.use_clamp = True
        pow_n.name = f"{TLM_PREFIX}proc_ridge_pow_{_next_id()}"
        pow_n.location = (x + 360, y)
        pow_n.inputs[1].default_value = getattr(layer, 'proc_ridged_gain', 2.0)
        node_tree.links.new(inv.outputs[0], pow_n.inputs[0])
        _tag(pow_n, layer.name, "proc_ridge_pow")

        mult = node_tree.nodes.new("ShaderNodeMath")
        mult.operation = 'MULTIPLY'
        mult.use_clamp = True
        mult.name = f"{TLM_PREFIX}proc_ridge_mul_{_next_id()}"
        mult.location = (x + 460, y)
        mult.inputs[1].default_value = getattr(layer, 'proc_ridged_offset', 1.0)
        node_tree.links.new(pow_n.outputs[0], mult.inputs[0])
        _tag(mult, layer.name, "proc_ridge_mul")

        # See DOTS branch for the tex_node-must-be-set rationale.
        tex_node = noise
        fac_out = mult.outputs[0]

    elif pt == 'CRACKS':
        # Crack / vein network = Voronoi distance-to-edge thresholded
        # into narrow lines. Reuses the same primitive as HEX_GRID but
        # with sharpness control and irregular cells via proc_randomness
        # (HEX_GRID defaults to Randomness 0 for clean honeycomb; CRACKS
        # defaults higher for organic veins).
        vor = node_tree.nodes.new("ShaderNodeTexVoronoi")
        try:
            vor.feature = 'DISTANCE_TO_EDGE'
        except Exception:
            pass
        try:
            vor.voronoi_dimensions = '3D'
        except Exception:
            pass
        vor.inputs["Scale"].default_value = layer.proc_scale
        if "Randomness" in vor.inputs:
            vor.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(vor, layer)
        vor.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        vor.location = (x - 100, y)
        _tag(vor, layer.name, "proc_tex")
        node_tree.links.new(vec_out, vor.inputs["Vector"])

        width = getattr(layer, 'proc_cracks_width', 0.05)
        sharpness = getattr(layer, 'proc_cracks_sharpness', 0.7)
        # sharpness=1.0 → minimum smoothstep band (razor edge).
        # sharpness=0.0 → full-width gradient (soft fissure).
        band = max(0.003, (1.0 - sharpness) * width * 0.8)

        mr = node_tree.nodes.new("ShaderNodeMapRange")
        try:
            mr.interpolation_type = 'SMOOTHSTEP'
        except Exception:
            pass
        mr.clamp = True
        # Inside crack (distance < width) → 1.0; outside → 0.0.
        mr.inputs["From Min"].default_value = max(0.0, width - band)
        mr.inputs["From Max"].default_value = min(1.0, width + band)
        mr.inputs["To Min"].default_value   = 1.0
        mr.inputs["To Max"].default_value   = 0.0
        mr.location = (x + 50, y)
        mr.name = f"{TLM_PREFIX}proc_cracks_mr_{_next_id()}"
        _tag(mr, layer.name, "proc_cracks_mr")
        node_tree.links.new(vor.outputs["Distance"], mr.inputs["Value"])
        # See DOTS branch for the tex_node-must-be-set rationale.
        tex_node = vor
        fac_out = mr.outputs["Result"]

    elif pt == 'GABOR':
        # ShaderNodeTexGabor (Blender 4.3+) — anisotropic Gabor noise.
        # Produces directional streaks: brushed metal, fiber weaves,
        # hairline scratches, anisotropic surfaces. Falls back to a
        # Wave-bands texture on older Blender that doesn't expose the
        # node, so .tlm presets stay portable.
        import math as _math
        try:
            tex_node = node_tree.nodes.new("ShaderNodeTexGabor")
        except RuntimeError:
            # Fallback: a single-direction Wave(BANDS) with detail and
            # distortion gives a passable brushed-metal look.
            tex_node = node_tree.nodes.new("ShaderNodeTexWave")
            try:
                tex_node.wave_type = 'BANDS'
                tex_node.bands_direction = 'X'
                tex_node.wave_profile = 'SIN'
            except Exception:
                pass
            tex_node.inputs["Scale"].default_value      = layer.proc_scale
            tex_node.inputs["Detail"].default_value     = layer.proc_detail
            tex_node.inputs["Distortion"].default_value = layer.proc_distortion
            fac_out = tex_node.outputs["Fac"]
        else:
            # 2D Gabor — orientation is a single angle in radians.
            # On 3D the orientation socket is a vector; we keep 2D for
            # the simpler UI (single rotation slider).
            try:
                tex_node.gabor_type = '2D'
            except (AttributeError, TypeError):
                pass
            tex_node.inputs["Scale"].default_value = layer.proc_scale
            # Some inputs may rename across Blender minor versions;
            # guard each setter individually so a rename doesn't abort
            # the whole branch.
            def _set_in(node, name, value):
                sock = node.inputs.get(name)
                if sock is not None:
                    try:
                        sock.default_value = value
                    except Exception:
                        pass
            _set_in(tex_node, "Frequency",
                    getattr(layer, 'proc_gabor_frequency', 2.0))
            _set_in(tex_node, "Anisotropy",
                    getattr(layer, 'proc_gabor_anisotropy', 1.0))
            _set_in(tex_node, "Orientation",
                    _math.radians(getattr(layer, 'proc_gabor_orientation', 45.0)))
            # Output socket name: typically "Value" (4.3-5.0) but
            # protect against future renames.
            fac_out = (tex_node.outputs.get("Value")
                       or tex_node.outputs.get("Fac")
                       or tex_node.outputs[0])

    elif pt == 'STRIPES':
        # Hard stripes = Wave(BANDS, SAW) thresholded by Map Range.
        # SAW gives a clean 0→1 ramp per period; Map Range with a
        # smoothstep around proc_stripe_width then makes a binary stripe
        # whose edge softness is controlled by proc_stripe_sharpness.
        #
        # The standard ColorRamp fall-through doesn't work here because
        # the resulting fac is binary (0 or 1) and a 3-stop ColorRamp
        # never samples its middle stop. Instead we build a Mix-node
        # topology that genuinely uses Color3 as a "core" colour inside
        # each stripe, with proc_color3_position controlling the core
        # fraction within the stripe.
        wave = node_tree.nodes.new("ShaderNodeTexWave")
        wave.wave_type = 'BANDS'
        try:
            wave.bands_direction = layer.proc_stripe_direction
        except Exception:
            wave.bands_direction = 'Y'
        try:
            wave.wave_profile = 'SAW'
        except Exception:
            pass
        wave.inputs["Scale"].default_value      = layer.proc_scale
        wave.inputs["Distortion"].default_value = 0.0
        wave.inputs["Detail"].default_value     = 0.0
        wave.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        wave.location = (x - 100, y)
        _set_wave_inputs(wave, layer)
        _tag(wave, layer.name, "proc_tex")
        node_tree.links.new(vec_out, wave.inputs["Vector"])

        width = layer.proc_stripe_width
        sharpness = layer.proc_stripe_sharpness
        threshold = 1.0 - width
        edge = (1.0 - sharpness) * 0.4

        mr = node_tree.nodes.new("ShaderNodeMapRange")
        mr.interpolation_type = 'SMOOTHSTEP'
        mr.clamp = True
        mr.inputs["From Min"].default_value = max(0.0, threshold - edge)
        mr.inputs["From Max"].default_value = min(1.0, threshold + edge + 1e-4)
        mr.inputs["To Min"].default_value   = 0.0
        mr.inputs["To Max"].default_value   = 1.0
        mr.location = (x + 50, y)
        mr.name = f"{TLM_PREFIX}proc_stripe_mr_{_next_id()}"
        _tag(mr, layer.name, "proc_stripe_mr")
        node_tree.links.new(wave.outputs["Fac"], mr.inputs["Value"])

        if getattr(layer, 'use_proc_color3', False):
            # Inner core stripe — narrower than the main stripe by
            # `proc_color3_position` (0 = no core, 1 = core fills stripe).
            core_frac = max(0.001, layer.proc_color3_position)
            inner_threshold = 1.0 - width * core_frac
            mr_inner = node_tree.nodes.new("ShaderNodeMapRange")
            mr_inner.interpolation_type = 'SMOOTHSTEP'
            mr_inner.clamp = True
            mr_inner.inputs["From Min"].default_value = max(0.0, inner_threshold - edge)
            mr_inner.inputs["From Max"].default_value = min(1.0, inner_threshold + edge + 1e-4)
            mr_inner.inputs["To Min"].default_value   = 0.0
            mr_inner.inputs["To Max"].default_value   = 1.0
            mr_inner.location = (x + 50, y - 100)
            mr_inner.name = f"{TLM_PREFIX}proc_stripe_mri_{_next_id()}"
            _tag(mr_inner, layer.name, "proc_stripe_mr_inner")
            node_tree.links.new(wave.outputs["Fac"], mr_inner.inputs["Value"])

            # mix_inner: A=color2 (halo), B=color3 (core), factor=inner_fac
            mix_inner = node_tree.nodes.new("ShaderNodeMix")
            mix_inner.data_type = 'RGBA'
            mix_inner.blend_type = 'MIX'
            mix_inner.location = (x + 250, y - 100)
            _factor_socket(mix_inner).default_value = 0.0
            _a_socket(mix_inner).default_value = layer.proc_color2
            _b_socket(mix_inner).default_value = layer.proc_color3
            mix_inner.name = f"{TLM_PREFIX}proc_cmix_inner_{_next_id()}"
            _tag(mix_inner, layer.name, "proc_cmix_inner")
            node_tree.links.new(mr_inner.outputs["Result"], _factor_socket(mix_inner))

            # mix_main: A=color1 (bg), B=inner.Result, factor=main_fac
            mix_main = node_tree.nodes.new("ShaderNodeMix")
            mix_main.data_type = 'RGBA'
            mix_main.blend_type = 'MIX'
            mix_main.location = (x + 450, y)
            _factor_socket(mix_main).default_value = 0.0
            _a_socket(mix_main).default_value = layer.proc_color1
            mix_main.name = f"{TLM_PREFIX}proc_cmix_main_{_next_id()}"
            _tag(mix_main, layer.name, "proc_cmix_main")
            node_tree.links.new(mr.outputs["Result"], _factor_socket(mix_main))
            node_tree.links.new(_result_socket(mix_inner), _b_socket(mix_main))
            return _result_socket(mix_main), None

        # 2-colour path: simple Mix(color1, color2) by main fac
        mix_main = node_tree.nodes.new("ShaderNodeMix")
        mix_main.data_type = 'RGBA'
        mix_main.blend_type = 'MIX'
        mix_main.location = (x + 250, y)
        _factor_socket(mix_main).default_value = 0.0
        _a_socket(mix_main).default_value = layer.proc_color1
        _b_socket(mix_main).default_value = layer.proc_color2
        mix_main.name = f"{TLM_PREFIX}proc_cmix_main_{_next_id()}"
        _tag(mix_main, layer.name, "proc_cmix_main")
        node_tree.links.new(mr.outputs["Result"], _factor_socket(mix_main))
        return _result_socket(mix_main), None

    elif pt == 'HEX_GRID':
        # Honeycomb = Voronoi(DISTANCE_TO_EDGE) thresholded.
        # DISTANCE_TO_EDGE returns 0 right on a cell boundary and rises
        # toward each cell's centre, so a Map Range that promotes the
        # low-distance band gives us the grid lines.
        # NOTE: true regular hexagons need a custom UV transform; with
        # proc_randomness=0 the Voronoi cells form a passable honeycomb,
        # higher values give Voronoi-style irregular cells.
        #
        # Same Mix-topology trick as STRIPES: the binary fac defeats a
        # 3-stop ColorRamp, so we build dedicated Mix nodes instead.
        # When use_proc_color3 is on, proc_color3_position controls how
        # much of the edge band is the inner "core" colour.
        vor = node_tree.nodes.new("ShaderNodeTexVoronoi")
        try:
            vor.feature = 'DISTANCE_TO_EDGE'
        except Exception:
            pass
        try:
            vor.voronoi_dimensions = '3D'
        except Exception:
            pass
        vor.inputs["Scale"].default_value      = layer.proc_scale
        if "Randomness" in vor.inputs:
            vor.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(vor, layer)
        vor.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        vor.location = (x - 100, y)
        _tag(vor, layer.name, "proc_tex")
        node_tree.links.new(vec_out, vor.inputs["Vector"])

        edge_w = layer.proc_hex_edge_width
        mr = node_tree.nodes.new("ShaderNodeMapRange")
        mr.interpolation_type = 'SMOOTHSTEP'
        mr.clamp = True
        # main fac: 1 inside the edge band (distance < edge_w), 0 in cell
        mr.inputs["From Min"].default_value = max(0.0, edge_w - 0.005)
        mr.inputs["From Max"].default_value = min(1.0, edge_w + 0.005 + 1e-4)
        mr.inputs["To Min"].default_value   = 1.0
        mr.inputs["To Max"].default_value   = 0.0
        mr.location = (x + 50, y)
        mr.name = f"{TLM_PREFIX}proc_hex_mr_{_next_id()}"
        _tag(mr, layer.name, "proc_hex_mr")
        node_tree.links.new(vor.outputs["Distance"], mr.inputs["Value"])

        if getattr(layer, 'use_proc_color3', False):
            core_frac = max(0.001, layer.proc_color3_position)
            inner_w = edge_w * core_frac
            mr_inner = node_tree.nodes.new("ShaderNodeMapRange")
            mr_inner.interpolation_type = 'SMOOTHSTEP'
            mr_inner.clamp = True
            # inner fac: 1 in the deepest part of the edge (distance < inner_w)
            mr_inner.inputs["From Min"].default_value = max(0.0, inner_w - 0.005)
            mr_inner.inputs["From Max"].default_value = min(1.0, inner_w + 0.005 + 1e-4)
            mr_inner.inputs["To Min"].default_value   = 1.0
            mr_inner.inputs["To Max"].default_value   = 0.0
            mr_inner.location = (x + 50, y - 100)
            mr_inner.name = f"{TLM_PREFIX}proc_hex_mri_{_next_id()}"
            _tag(mr_inner, layer.name, "proc_hex_mr_inner")
            node_tree.links.new(vor.outputs["Distance"], mr_inner.inputs["Value"])

            # mix_inner: A=color2 (halo), B=color3 (core), factor=inner_fac
            mix_inner = node_tree.nodes.new("ShaderNodeMix")
            mix_inner.data_type = 'RGBA'
            mix_inner.blend_type = 'MIX'
            mix_inner.location = (x + 250, y - 100)
            _factor_socket(mix_inner).default_value = 0.0
            _a_socket(mix_inner).default_value = layer.proc_color2
            _b_socket(mix_inner).default_value = layer.proc_color3
            mix_inner.name = f"{TLM_PREFIX}proc_cmix_inner_{_next_id()}"
            _tag(mix_inner, layer.name, "proc_cmix_inner")
            node_tree.links.new(mr_inner.outputs["Result"], _factor_socket(mix_inner))

            # mix_main: A=color1 (cell), B=inner.Result, factor=main_fac
            mix_main = node_tree.nodes.new("ShaderNodeMix")
            mix_main.data_type = 'RGBA'
            mix_main.blend_type = 'MIX'
            mix_main.location = (x + 450, y)
            _factor_socket(mix_main).default_value = 0.0
            _a_socket(mix_main).default_value = layer.proc_color1
            mix_main.name = f"{TLM_PREFIX}proc_cmix_main_{_next_id()}"
            _tag(mix_main, layer.name, "proc_cmix_main")
            node_tree.links.new(mr.outputs["Result"], _factor_socket(mix_main))
            node_tree.links.new(_result_socket(mix_inner), _b_socket(mix_main))
            return _result_socket(mix_main), None

        # 2-colour path
        mix_main = node_tree.nodes.new("ShaderNodeMix")
        mix_main.data_type = 'RGBA'
        mix_main.blend_type = 'MIX'
        mix_main.location = (x + 250, y)
        _factor_socket(mix_main).default_value = 0.0
        _a_socket(mix_main).default_value = layer.proc_color1
        _b_socket(mix_main).default_value = layer.proc_color2
        mix_main.name = f"{TLM_PREFIX}proc_cmix_main_{_next_id()}"
        _tag(mix_main, layer.name, "proc_cmix_main")
        node_tree.links.new(mr.outputs["Result"], _factor_socket(mix_main))
        return _result_socket(mix_main), None

    elif pt == 'MARBLE':
        # Marble = Wave base + Noise turbulence on phase
        wave = node_tree.nodes.new("ShaderNodeTexWave")
        wave.wave_type = layer.proc_marble_wave_type
        _set_wave_direction(
            wave,
            layer.proc_marble_wave_type,
            getattr(layer, 'proc_marble_bands_direction', 'X'),
            getattr(layer, 'proc_marble_rings_direction', 'X'),
        )
        wave.name = f"{TLM_PREFIX}proc_marble_wave_{_next_id()}"
        wave.location = (x - 100, y)
        wave.inputs["Scale"].default_value = layer.proc_scale
        wave.inputs["Detail"].default_value = layer.proc_detail
        wave.inputs["Distortion"].default_value = layer.proc_distortion
        _tag(wave, layer.name, "proc_tex")
        try:
            wave.wave_profile = getattr(layer, 'proc_marble_wave_profile', 'SIN')
        except Exception:
            pass
        node_tree.links.new(vec_out, wave.inputs["Vector"])

        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.name = f"{TLM_PREFIX}proc_marble_noise_{_next_id()}"
        noise.location = (x - 350, y - 150)
        noise.inputs["Scale"].default_value = layer.proc_scale * 2.0
        noise.inputs["Detail"].default_value = layer.proc_detail
        noise.inputs["Roughness"].default_value = layer.proc_roughness_proc
        noise.inputs["Distortion"].default_value = layer.proc_distortion * 0.5
        _tag(noise, layer.name, "marble_noise")
        node_tree.links.new(vec_out, noise.inputs["Vector"])

        mult = node_tree.nodes.new("ShaderNodeMath")
        mult.operation = 'MULTIPLY'
        mult.name = f"{TLM_PREFIX}proc_marble_mult_{_next_id()}"
        mult.location = (x - 200, y - 150)
        node_tree.links.new(noise.outputs["Fac"], mult.inputs[0])
        mult.inputs[1].default_value = layer.proc_marble_distortion
        _tag(mult, layer.name, "marble_mult")

        phase_input = wave.inputs.get("Phase Offset")
        if phase_input:
            node_tree.links.new(mult.outputs["Value"], phase_input)
        else:
            add = node_tree.nodes.new("ShaderNodeMath")
            add.operation = 'ADD'
            add.name = f"{TLM_PREFIX}proc_marble_adddist_{_next_id()}"
            add.location = (x, y - 150)
            add.inputs[0].default_value = layer.proc_distortion
            node_tree.links.new(mult.outputs["Value"], add.inputs[1])
            node_tree.links.new(add.outputs["Value"], wave.inputs["Distortion"])

        fac_out = wave.outputs["Fac"]

        # Use the shared ramp builder so MARBLE gets Manual Stops, color
        # mode, interpolation, and extra stops just like every other
        # proc_type. Previously this branch had its own inline copy that
        # fell out of sync.
        return _build_proc_color_ramp(node_tree, layer, x, y, fac_out), None

    if tex_node is None:
        return None, None

    tex_node.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
    tex_node.location = (x - 100, y)
    _tag(tex_node, layer.name, "proc_tex")
    # FRESNEL has no Vector input (only IOR + Normal). All other texture
    # nodes do — link the procedural coord chain into the Vector socket
    # except for FRESNEL which is angle-based, not spatial.
    if "Vector" in tex_node.inputs:
        node_tree.links.new(vec_out, tex_node.inputs["Vector"])

    # ── ColorRamp: map Fac → Color1..Color2 ──────────────────────────────
    # Delegated to _build_proc_color_ramp so all proc_types share the
    # same ramp construction (Manual Stops, color mode, interpolation,
    # extra stops, legacy Color 3). See helper near top of file.
    return _build_proc_color_ramp(node_tree, layer, x, y, fac_out), None

def _build_proc_fac_node(node_tree, layer, name_suffix, x, y, uv_map="UVMap"):
    """
    Build TexCoord → Mapping → Texture nodes for a PROCEDURAL layer and
    return the Fac output socket (greyscale 0-1).

    This is the shared primitive used by both the bump channel and the
    scalar PBR channels (roughness/metallic).  Returns None if the layer's
    proc_type produces no usable Fac.
    """
    pt = layer.proc_type

    tc = node_tree.nodes.new("ShaderNodeTexCoord")
    tc.name = f"{TLM_PREFIX}pfac_tc_{name_suffix}"
    tc.location = (x - 500, y)
    _tag(tc, layer.name, "proc_tc")

    mapping = node_tree.nodes.new("ShaderNodeMapping")
    mapping.name = f"{TLM_PREFIX}pfac_map_{name_suffix}"
    mapping.location = (x - 300, y)
    _tag(mapping, layer.name, "proc_map")
    # NOTE: Scale is applied at the texture node level, not the Mapping node,
    # to match _build_procedural_node and avoid doubling the scale.
    _apply_mapping_settings(mapping, layer)
    # Select coordinate space based on layer setting
    coord_type = getattr(layer, 'proc_coord_type', 'GENERATED')
    if coord_type == 'UV':
        uv_node = node_tree.nodes.new("ShaderNodeUVMap")
        uv_node.name = f"{TLM_PREFIX}pfac_uv_{name_suffix}"
        uv_node.uv_map = uv_map
        uv_node.location = (x - 500, y - 50)
        coord_out = uv_node.outputs["UV"]
    elif coord_type == 'OBJECT':
        coord_out = tc.outputs["Object"]
        coord_out = _inject_coord_normalization(
            node_tree, layer, coord_out, x, y, name_tag=f"pfac_{name_suffix}"
        )
    else:  # GENERATED
        coord_out = tc.outputs["Generated"]
    # Apply polar/spherical/swirl/cylindrical coordinate transform
    coord_out = _inject_coord_transform(
        node_tree, layer, coord_out, x, y, name_tag=f"pfac_{name_suffix}"
    )
    node_tree.links.new(coord_out, mapping.inputs["Vector"])

    # ── Vector distortion (organic coordinate warping) ────────────────────
    vec_out = _inject_vector_distortion(
        node_tree, layer, mapping.outputs["Vector"], x, y, name_tag=f"pfac_{name_suffix}"
    )

    tex = None
    fac_out = None

    if pt == 'NOISE':
        tex = node_tree.nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value      = layer.proc_scale
        tex.inputs["Detail"].default_value     = layer.proc_detail
        tex.inputs["Roughness"].default_value  = layer.proc_roughness_proc
        tex.inputs["Lacunarity"].default_value = layer.proc_lacunarity
        tex.inputs["Distortion"].default_value = layer.proc_distortion
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs["Fac"]

    elif pt == 'MUSGRAVE':
        try:
            tex = node_tree.nodes.new("ShaderNodeTexMusgrave")
            tex.inputs["Scale"].default_value      = layer.proc_scale
            tex.inputs["Detail"].default_value     = layer.proc_detail
            tex.inputs["Lacunarity"].default_value = layer.proc_lacunarity
            tex.inputs["Roughness"].default_value  = layer.proc_roughness_proc
            node_tree.links.new(vec_out, tex.inputs["Vector"])
            fac_out = tex.outputs["Fac"]
        except Exception:
            tex = node_tree.nodes.new("ShaderNodeTexNoise")
            tex.inputs["Scale"].default_value = layer.proc_scale
            node_tree.links.new(vec_out, tex.inputs["Vector"])
            fac_out = tex.outputs["Fac"]

    elif pt == 'VORONOI':
        tex = node_tree.nodes.new("ShaderNodeTexVoronoi")
        tex.feature  = layer.proc_voronoi_feature
        tex.distance = layer.proc_voronoi_distance
        tex.inputs["Scale"].default_value      = layer.proc_scale
        tex.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(tex, layer)
        if hasattr(tex, 'normalize'):
            tex.normalize = True
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        # Fac: distance gradient (default) OR random-per-cell via WhiteNoise(Position)
        fac_out = _voronoi_fac(node_tree, layer, tex, x, y, name_tag=f"pfac_{name_suffix}")

    elif pt == 'WAVE':
        tex = node_tree.nodes.new("ShaderNodeTexWave")
        tex.wave_type = layer.proc_wave_type
        _set_wave_direction(
            tex,
            layer.proc_wave_type,
            getattr(layer, 'proc_wave_bands_direction', 'X'),
            getattr(layer, 'proc_wave_rings_direction', 'X'),
        )
        try:
            tex.wave_profile = layer.proc_wave_profile
        except Exception:
            pass
        _set_wave_inputs(tex, layer)
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs["Fac"]

    elif pt == 'CHECKER':
        tex = node_tree.nodes.new("ShaderNodeTexChecker")
        tex.inputs["Scale"].default_value = layer.proc_scale
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs.get("Fac") or tex.outputs[0]

    elif pt == 'BRICK':
        tex = node_tree.nodes.new("ShaderNodeTexBrick")
        tex.offset           = layer.proc_brick_offset
        tex.offset_frequency = layer.proc_brick_offset_freq
        tex.squash           = layer.proc_brick_squash
        tex.squash_frequency = layer.proc_brick_squash_freq
        tex.inputs["Scale"].default_value         = layer.proc_scale
        tex.inputs["Mortar Size"].default_value   = layer.proc_brick_mortar_size
        tex.inputs["Mortar Smooth"].default_value = layer.proc_brick_mortar_smooth
        tex.inputs["Bias"].default_value          = layer.proc_brick_bias
        _set_input_default(tex, "Brick Width", getattr(layer, 'proc_brick_width', 0.5))
        _set_input_default(tex, "Row Height", getattr(layer, 'proc_brick_row_height', 0.25))
        # Sentinel colours: WHITE bricks + BLACK mortar so Color.R is a
        # 0/1 brick-vs-mortar mask. Brick.Fac would be a 0/1 between
        # Color1 and Color2 bricks (alternation), which is rarely the
        # mask people actually want — and worse, when fed through the
        # emission pipeline (which inverts the fac to put glow on the
        # "low" side) it puts the glow on alternating brick FACES
        # instead of the seams. Encoding the mortar via Color sidesteps
        # that and gives the intuitive default: routed-to-roughness
        # makes bricks rougher than mortar; routed-to-emission glows on
        # the seams.
        tex.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
        tex.inputs["Color2"].default_value = (1.0, 1.0, 1.0, 1.0)
        tex.inputs["Mortar"].default_value = (0.0, 0.0, 0.0, 1.0)
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        sep = node_tree.nodes.new("ShaderNodeSeparateColor")
        sep.name = f"{TLM_PREFIX}pfac_brick_sep_{name_suffix}"
        sep.location = (x + 100, y)
        node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
        fac_out = sep.outputs["Red"]

    elif pt == 'MAGIC':
        tex = node_tree.nodes.new("ShaderNodeTexMagic")
        tex.turbulence_depth = layer.proc_magic_depth
        tex.inputs["Scale"].default_value      = layer.proc_scale
        tex.inputs["Distortion"].default_value = layer.proc_magic_distortion
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs.get("Fac") or tex.outputs[0]

    elif pt == 'WHITE_NOISE':
        tex = node_tree.nodes.new("ShaderNodeTexWhiteNoise")
        tex.noise_dimensions = '3D'
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs["Value"]

    elif pt == 'FRESNEL':
        # Mirror of FRESNEL path in _build_procedural_node — angle-based
        # fac for scalar channels (roughness, metallic, bump) so iridescent-
        # style angle-dependent values can also drive non-colour channels.
        tex = node_tree.nodes.new("ShaderNodeFresnel")
        tex.name = f"{TLM_PREFIX}pfac_fresnel_{name_suffix}"
        tex.location = (x - 100, y)
        tex.inputs["IOR"].default_value = getattr(layer, 'proc_fresnel_ior', 1.45)
        _tag(tex, layer.name, "proc_tex")  # so _hot_proc_fresnel_ior can find it
        fac_out = tex.outputs["Fac"]

    elif pt == 'DOTS':
        # Mirror of DOTS path in _build_procedural_node — scalar fac
        # for routing to roughness / metallic / bump.
        vor = node_tree.nodes.new("ShaderNodeTexVoronoi")
        try:
            vor.feature = 'F1'
        except Exception:
            pass
        try:
            vor.voronoi_dimensions = '3D'
        except Exception:
            pass
        if hasattr(vor, 'normalize'):
            vor.normalize = True
        vor.inputs["Scale"].default_value      = layer.proc_scale
        vor.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(vor, layer)
        vor.name = f"{TLM_PREFIX}pfac_dots_vor_{name_suffix}"
        vor.location = (x - 100, y)
        _tag(vor, layer.name, "proc_tex")
        node_tree.links.new(vec_out, vor.inputs["Vector"])

        radius = getattr(layer, 'proc_dots_radius', 0.35)
        softness = getattr(layer, 'proc_dots_softness', 0.15)
        half_band = max(0.005, softness * 0.5)

        mr = node_tree.nodes.new("ShaderNodeMapRange")
        try:
            mr.interpolation_type = 'SMOOTHSTEP'
        except Exception:
            pass
        mr.clamp = True
        mr.inputs["From Min"].default_value = max(0.0, radius - half_band)
        mr.inputs["From Max"].default_value = min(1.0, radius + half_band)
        mr.inputs["To Min"].default_value   = 1.0
        mr.inputs["To Max"].default_value   = 0.0
        mr.location = (x + 50, y)
        mr.name = f"{TLM_PREFIX}pfac_dots_mr_{name_suffix}"
        _tag(mr, layer.name, "proc_dots_mr")
        node_tree.links.new(vor.outputs["Distance"], mr.inputs["Value"])
        return mr.outputs["Result"]

    elif pt == 'RIDGED':
        # Mirror of RIDGED path — 5-stage math chain that produces
        # razor-like crests from a Noise base.
        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value      = layer.proc_scale
        noise.inputs["Detail"].default_value     = layer.proc_detail
        noise.inputs["Roughness"].default_value  = layer.proc_roughness_proc
        noise.inputs["Lacunarity"].default_value = layer.proc_lacunarity
        noise.inputs["Distortion"].default_value = layer.proc_distortion
        noise.name = f"{TLM_PREFIX}pfac_ridge_noise_{name_suffix}"
        noise.location = (x - 100, y)
        _tag(noise, layer.name, "proc_tex")
        node_tree.links.new(vec_out, noise.inputs["Vector"])

        fold = node_tree.nodes.new("ShaderNodeMath")
        fold.operation = 'MULTIPLY_ADD'
        fold.name = f"{TLM_PREFIX}pfac_ridge_fold_{name_suffix}"
        fold.location = (x + 60, y)
        fold.inputs[1].default_value = 2.0
        fold.inputs[2].default_value = -1.0
        node_tree.links.new(noise.outputs["Fac"], fold.inputs[0])

        abs_n = node_tree.nodes.new("ShaderNodeMath")
        abs_n.operation = 'ABSOLUTE'
        abs_n.name = f"{TLM_PREFIX}pfac_ridge_abs_{name_suffix}"
        abs_n.location = (x + 160, y)
        node_tree.links.new(fold.outputs[0], abs_n.inputs[0])

        inv = node_tree.nodes.new("ShaderNodeMath")
        inv.operation = 'SUBTRACT'
        inv.use_clamp = True
        inv.name = f"{TLM_PREFIX}pfac_ridge_inv_{name_suffix}"
        inv.location = (x + 260, y)
        inv.inputs[0].default_value = 1.0
        node_tree.links.new(abs_n.outputs[0], inv.inputs[1])

        pow_n = node_tree.nodes.new("ShaderNodeMath")
        pow_n.operation = 'POWER'
        pow_n.use_clamp = True
        pow_n.name = f"{TLM_PREFIX}pfac_ridge_pow_{name_suffix}"
        pow_n.location = (x + 360, y)
        pow_n.inputs[1].default_value = getattr(layer, 'proc_ridged_gain', 2.0)
        node_tree.links.new(inv.outputs[0], pow_n.inputs[0])

        mult = node_tree.nodes.new("ShaderNodeMath")
        mult.operation = 'MULTIPLY'
        mult.use_clamp = True
        mult.name = f"{TLM_PREFIX}pfac_ridge_mul_{name_suffix}"
        mult.location = (x + 460, y)
        mult.inputs[1].default_value = getattr(layer, 'proc_ridged_offset', 1.0)
        node_tree.links.new(pow_n.outputs[0], mult.inputs[0])

        return mult.outputs[0]

    elif pt == 'CRACKS':
        # Mirror of CRACKS path — Voronoi DISTANCE_TO_EDGE thresholded.
        vor = node_tree.nodes.new("ShaderNodeTexVoronoi")
        try:
            vor.feature = 'DISTANCE_TO_EDGE'
        except Exception:
            pass
        try:
            vor.voronoi_dimensions = '3D'
        except Exception:
            pass
        vor.inputs["Scale"].default_value = layer.proc_scale
        if "Randomness" in vor.inputs:
            vor.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(vor, layer)
        vor.name = f"{TLM_PREFIX}pfac_cracks_vor_{name_suffix}"
        vor.location = (x - 100, y)
        _tag(vor, layer.name, "proc_tex")
        node_tree.links.new(vec_out, vor.inputs["Vector"])

        width = getattr(layer, 'proc_cracks_width', 0.05)
        sharpness = getattr(layer, 'proc_cracks_sharpness', 0.7)
        band = max(0.003, (1.0 - sharpness) * width * 0.8)

        mr = node_tree.nodes.new("ShaderNodeMapRange")
        try:
            mr.interpolation_type = 'SMOOTHSTEP'
        except Exception:
            pass
        mr.clamp = True
        mr.inputs["From Min"].default_value = max(0.0, width - band)
        mr.inputs["From Max"].default_value = min(1.0, width + band)
        mr.inputs["To Min"].default_value   = 1.0
        mr.inputs["To Max"].default_value   = 0.0
        mr.location = (x + 50, y)
        mr.name = f"{TLM_PREFIX}pfac_cracks_mr_{name_suffix}"
        _tag(mr, layer.name, "proc_cracks_mr")
        node_tree.links.new(vor.outputs["Distance"], mr.inputs["Value"])
        return mr.outputs["Result"]

    elif pt == 'GABOR':
        # Mirror of the GABOR path in _build_procedural_node — provides
        # the scalar fac for routing to roughness / metallic / bump.
        import math as _math
        try:
            tex = node_tree.nodes.new("ShaderNodeTexGabor")
        except RuntimeError:
            tex = node_tree.nodes.new("ShaderNodeTexWave")
            try:
                tex.wave_type = 'BANDS'
                tex.bands_direction = 'X'
                tex.wave_profile = 'SIN'
            except Exception:
                pass
            tex.inputs["Scale"].default_value      = layer.proc_scale
            tex.inputs["Detail"].default_value     = layer.proc_detail
            tex.inputs["Distortion"].default_value = layer.proc_distortion
            node_tree.links.new(vec_out, tex.inputs["Vector"])
            fac_out = tex.outputs["Fac"]
        else:
            try:
                tex.gabor_type = '2D'
            except (AttributeError, TypeError):
                pass
            tex.inputs["Scale"].default_value = layer.proc_scale
            def _set_in(node, name, value):
                sock = node.inputs.get(name)
                if sock is not None:
                    try:
                        sock.default_value = value
                    except Exception:
                        pass
            _set_in(tex, "Frequency",
                    getattr(layer, 'proc_gabor_frequency', 2.0))
            _set_in(tex, "Anisotropy",
                    getattr(layer, 'proc_gabor_anisotropy', 1.0))
            _set_in(tex, "Orientation",
                    _math.radians(getattr(layer, 'proc_gabor_orientation', 45.0)))
            node_tree.links.new(vec_out, tex.inputs["Vector"])
            fac_out = (tex.outputs.get("Value")
                       or tex.outputs.get("Fac")
                       or tex.outputs[0])

    elif pt == 'STRIPES':
        # Mirror of the STRIPES path in _build_procedural_node — Wave
        # SAW thresholded by Map Range smoothstep.
        wave = node_tree.nodes.new("ShaderNodeTexWave")
        wave.wave_type = 'BANDS'
        try:
            wave.bands_direction = layer.proc_stripe_direction
        except Exception:
            wave.bands_direction = 'Y'
        try:
            wave.wave_profile = 'SAW'
        except Exception:
            pass
        wave.name = f"{TLM_PREFIX}pfac_stripe_wave_{name_suffix}"
        wave.location = (x - 100, y)
        wave.inputs["Scale"].default_value      = layer.proc_scale
        _set_wave_inputs(wave, layer)
        _tag(wave, layer.name, "proc_tex")
        node_tree.links.new(vec_out, wave.inputs["Vector"])

        mr = node_tree.nodes.new("ShaderNodeMapRange")
        mr.interpolation_type = 'SMOOTHSTEP'
        mr.clamp = True
        width = layer.proc_stripe_width
        sharpness = layer.proc_stripe_sharpness
        threshold = 1.0 - width
        edge = (1.0 - sharpness) * 0.4
        mr.inputs["From Min"].default_value = max(0.0, threshold - edge)
        mr.inputs["From Max"].default_value = min(1.0, threshold + edge + 1e-4)
        mr.inputs["To Min"].default_value   = 0.0
        mr.inputs["To Max"].default_value   = 1.0
        mr.name = f"{TLM_PREFIX}pfac_stripe_mr_{name_suffix}"
        mr.location = (x + 50, y)
        _tag(mr, layer.name, "proc_stripe_mr")
        node_tree.links.new(wave.outputs["Fac"], mr.inputs["Value"])
        return mr.outputs["Result"]

    elif pt == 'HEX_GRID':
        # Mirror of the HEX_GRID path in _build_procedural_node.
        vor = node_tree.nodes.new("ShaderNodeTexVoronoi")
        try:
            vor.feature = 'DISTANCE_TO_EDGE'
        except Exception:
            pass
        try:
            vor.voronoi_dimensions = '3D'
        except Exception:
            pass
        vor.name = f"{TLM_PREFIX}pfac_hex_vor_{name_suffix}"
        vor.location = (x - 100, y)
        vor.inputs["Scale"].default_value = layer.proc_scale
        if "Randomness" in vor.inputs:
            vor.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(vor, layer)
        _tag(vor, layer.name, "proc_tex")
        node_tree.links.new(vec_out, vor.inputs["Vector"])

        mr = node_tree.nodes.new("ShaderNodeMapRange")
        mr.interpolation_type = 'SMOOTHSTEP'
        mr.clamp = True
        edge_w = layer.proc_hex_edge_width
        mr.inputs["From Min"].default_value = max(0.0, edge_w - 0.005)
        mr.inputs["From Max"].default_value = min(1.0, edge_w + 0.005 + 1e-4)
        mr.inputs["To Min"].default_value   = 1.0
        mr.inputs["To Max"].default_value   = 0.0
        mr.name = f"{TLM_PREFIX}pfac_hex_mr_{name_suffix}"
        mr.location = (x + 50, y)
        _tag(mr, layer.name, "proc_hex_mr")
        node_tree.links.new(vor.outputs["Distance"], mr.inputs["Value"])
        return mr.outputs["Result"]

    elif pt == 'GRADIENT':
        tex = node_tree.nodes.new("ShaderNodeTexGradient")
        tex.gradient_type = layer.proc_gradient_type
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs["Fac"]

    elif pt == 'MARBLE':
        wave = node_tree.nodes.new("ShaderNodeTexWave")
        wave.wave_type = layer.proc_marble_wave_type
        _set_wave_direction(
            wave,
            layer.proc_marble_wave_type,
            getattr(layer, 'proc_marble_bands_direction', 'X'),
            getattr(layer, 'proc_marble_rings_direction', 'X'),
        )
        wave.name = f"{TLM_PREFIX}pfac_marble_wave_{name_suffix}"
        wave.location = (x - 100, y)
        wave.inputs["Scale"].default_value = layer.proc_scale
        wave.inputs["Detail"].default_value = layer.proc_detail
        wave.inputs["Distortion"].default_value = layer.proc_distortion
        _tag(wave, layer.name, "proc_tex")
        try:
            wave.wave_profile = getattr(layer, 'proc_marble_wave_profile', 'SIN')
        except Exception:
            pass
        node_tree.links.new(vec_out, wave.inputs["Vector"])

        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.name = f"{TLM_PREFIX}pfac_marble_noise_{name_suffix}"
        noise.location = (x - 350, y - 150)
        noise.inputs["Scale"].default_value = layer.proc_scale * 2.0
        noise.inputs["Detail"].default_value = layer.proc_detail
        noise.inputs["Roughness"].default_value = layer.proc_roughness_proc
        noise.inputs["Distortion"].default_value = layer.proc_distortion * 0.5
        _tag(noise, layer.name, "marble_noise")
        node_tree.links.new(vec_out, noise.inputs["Vector"])

        mult = node_tree.nodes.new("ShaderNodeMath")
        mult.operation = 'MULTIPLY'
        mult.name = f"{TLM_PREFIX}pfac_marble_mult_{name_suffix}"
        mult.location = (x - 200, y - 150)
        node_tree.links.new(noise.outputs["Fac"], mult.inputs[0])
        mult.inputs[1].default_value = layer.proc_marble_distortion
        _tag(mult, layer.name, "marble_mult")

        phase_input = wave.inputs.get("Phase Offset")
        if phase_input:
            node_tree.links.new(mult.outputs["Value"], phase_input)
        else:
            add = node_tree.nodes.new("ShaderNodeMath")
            add.operation = 'ADD'
            add.name = f"{TLM_PREFIX}pfac_marble_adddist_{name_suffix}"
            add.location = (x, y - 150)
            add.inputs[0].default_value = layer.proc_distortion
            node_tree.links.new(mult.outputs["Value"], add.inputs[1])
            node_tree.links.new(add.outputs["Value"], wave.inputs["Distortion"])

        return wave.outputs["Fac"]

    if tex is None or fac_out is None:
        return None

    tex.name = f"{TLM_PREFIX}pfac_tex_{name_suffix}"
    tex.location = (x - 100, y)
    _tag(tex, layer.name, "proc_tex")
    return fac_out


# ── Normal map channel builder ───────────────────────────────────────────────

