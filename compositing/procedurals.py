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
    '_clamped_ramp_position',
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

def _clamped_ramp_position(value):
    return min(max(float(value), 0.001), 0.999)


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
    #
    # FRESNEL forces contrast=0, center=0.5 (default 0..1 range) because
    # Fresnel.Fac follows Schlick's non-linear distribution — most pixels
    # concentrate below 0.255 except at grazing angles. Manual stops are
    # accepted on FRESNEL but the auto path uses neutral defaults so the
    # full angular sweep maps cleanly.
    #
    # GRADIENT used to be excluded from manual stops because the auto-path
    # gives a clean 0..1 linear ramp, which works for most procedural-as-
    # color uses. But this prevented users from tuning where the cutoff
    # sits when routing the gradient to a single output channel — e.g.
    # output_channel='ALPHA' for burn-dissolve effects, where you want the
    # transparent band at a specific FAC range. Now GRADIENT honours
    # manual stops too. Fix 2026-05-28.
    if getattr(layer, 'proc_use_manual_stops', False) and layer.proc_type != 'FRESNEL':
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


# ── View-driven UV parallax helper ────────────────────────────────────────────

def _inject_view_uv_shift(node_tree, layer, coord_out, x, y, name_tag=""):
    """Optionally offset the coords by the CAMERA-SPACE surface normal so a
    procedural pattern *slides* across the surface as it is reoriented —
    the defining behaviour of a real holographic / diffractive foil.

    WHY camera-space normal and NOT the view vector:
    A flat card has a uniform normal, and the world-space view vector
    (Geometry.Incoming) barely changes when you rotate the card in front
    of a fixed camera — so offsetting by Incoming produced a constant
    shift and the bands never moved (they only changed hue via the
    separate FRESNEL layer). The orientation-dependent quantity is the
    surface normal expressed in CAMERA space: it rotates *with* the card,
    so as you tilt/spin the card its X/Y components sweep, and adding them
    to the coords translates the band phase → the stripes physically
    scroll across the surface. That IS the foil "the light moves the
    pattern" effect.

    Chain inserted (when proc_uv_view_shift > 0):
        Geometry.Normal → VectorTransform(NORMAL, World→Camera)
                        → VectorMath(SCALE, k) ─┐
                                                 ├→ VectorMath(ADD) → out
                                    coord_out ──┘

    The spatial band structure still comes from the pattern's own
    UV/scale; this only adds a uniform, orientation-driven phase offset.
    Returns the (possibly offset) coord socket. Passthrough when amount
    is 0 → zero cost / no graph change for materials that don't use it.
    """
    amt = float(getattr(layer, 'proc_uv_view_shift', 0.0))
    if amt <= 0.0:
        return coord_out
    geom = node_tree.nodes.new("ShaderNodeNewGeometry")
    geom.name = f"{TLM_PREFIX}view_geom_{name_tag}_{_next_id()}"
    geom.location = (x - 760, y - 220)
    _tag(geom, layer.name, f"view_geom_{name_tag}")
    # Normal → camera space: rotates with the surface, so it responds to
    # the object being reoriented (or viewed through the render camera).
    vt = node_tree.nodes.new("ShaderNodeVectorTransform")
    vt.vector_type = 'NORMAL'
    vt.convert_from = 'WORLD'
    vt.convert_to = 'CAMERA'
    vt.name = f"{TLM_PREFIX}view_xform_{name_tag}_{_next_id()}"
    vt.location = (x - 580, y - 220)
    _tag(vt, layer.name, f"view_xform_{name_tag}")
    sc = node_tree.nodes.new("ShaderNodeVectorMath")
    sc.operation = 'SCALE'
    sc.name = f"{TLM_PREFIX}view_scale_{name_tag}_{_next_id()}"
    sc.location = (x - 400, y - 220)
    sc.inputs["Scale"].default_value = amt
    _tag(sc, layer.name, f"view_scale_{name_tag}")
    add = node_tree.nodes.new("ShaderNodeVectorMath")
    add.operation = 'ADD'
    add.name = f"{TLM_PREFIX}view_add_{name_tag}_{_next_id()}"
    add.location = (x - 320, y - 120)
    _tag(add, layer.name, f"view_add_{name_tag}")
    node_tree.links.new(geom.outputs["Normal"], vt.inputs[0])
    node_tree.links.new(vt.outputs["Vector"], sc.inputs[0])
    node_tree.links.new(coord_out, add.inputs[0])
    node_tree.links.new(sc.outputs["Vector"], add.inputs[1])
    return add.outputs["Vector"]


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

    # Random-per-cell needs a DISCRETE per-cell Position to hash. The
    # smoothed features (SMOOTH_F1) and N_SPHERE_RADIUS produce a
    # continuous/blended Position that the White Noise hashes to a
    # near-constant value → the whole surface renders one uniform colour.
    # Since random mode discards the Distance output entirely (we only use
    # Position), coercing the node to F1 here changes nothing visible
    # except making the per-cell hash work. F1 / F2 / DISTANCE_TO_EDGE
    # already have a valid discrete Position and are left untouched, so no
    # existing F1-random preset (e.g. Crystal Geode) changes appearance.
    if getattr(tex_node, 'feature', 'F1') in ('SMOOTH_F1', 'N_SPHERE_RADIUS'):
        try:
            tex_node.feature = 'F1'
        except Exception:
            pass

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

def _get_truchet_node_group():
    """Build (once) and return the shared Truchet Fac node group.
    Inputs: Vector, Scale, Width.  Output: Fac (0-1, 1 on the path).

    Truchet's per-cell logic needs ~20 math nodes; building that inline on
    every rebuild trips Blender's node-reference staleness at high node
    counts (see the TILES history). So it lives in a reusable node group
    created a SINGLE time and merely instanced thereafter — the per-material
    rebuild only adds one ShaderNodeGroup node, which is robust.
    """
    name = "TLM_Truchet_v1"
    ng = bpy.data.node_groups.get(name)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    itf = ng.interface
    itf.new_socket("Vector", in_out='INPUT', socket_type='NodeSocketVector')
    itf.new_socket("Scale",  in_out='INPUT', socket_type='NodeSocketFloat')
    itf.new_socket("Width",  in_out='INPUT', socket_type='NodeSocketFloat')
    itf.new_socket("Fac",    in_out='OUTPUT', socket_type='NodeSocketFloat')
    nd = ng.nodes
    lk = ng.links
    gin = nd.new('NodeGroupInput');  gin.location = (-900, 0)
    gout = nd.new('NodeGroupOutput'); gout.location = (900, 0)

    def _m(op, xy):
        n = nd.new('ShaderNodeMath'); n.operation = op; n.location = xy; return n

    sep = nd.new('ShaderNodeSeparateXYZ'); sep.location = (-720, 0)
    lk.new(gin.outputs["Vector"], sep.inputs[0])
    sx = _m('MULTIPLY', (-560, 80)); lk.new(sep.outputs["X"], sx.inputs[0]); lk.new(gin.outputs["Scale"], sx.inputs[1])
    sy = _m('MULTIPLY', (-560, -80)); lk.new(sep.outputs["Y"], sy.inputs[0]); lk.new(gin.outputs["Scale"], sy.inputs[1])
    fu = _m('FRACT', (-400, 120)); lk.new(sx.outputs[0], fu.inputs[0])
    fv = _m('FRACT', (-400, -40)); lk.new(sy.outputs[0], fv.inputs[0])
    cu = _m('FLOOR', (-400, 260)); lk.new(sx.outputs[0], cu.inputs[0])
    cv = _m('FLOOR', (-400, -200)); lk.new(sy.outputs[0], cv.inputs[0])
    comb = nd.new('ShaderNodeCombineXYZ'); comb.location = (-240, 260)
    lk.new(cu.outputs[0], comb.inputs["X"]); lk.new(cv.outputs[0], comb.inputs["Y"])
    wn = nd.new('ShaderNodeTexWhiteNoise'); wn.location = (-80, 260)
    lk.new(comb.outputs["Vector"], wn.inputs["Vector"])
    bit = _m('GREATER_THAN', (80, 260)); bit.inputs[1].default_value = 0.5
    lk.new(wn.outputs["Value"], bit.inputs[0])
    # "/" distance = |fu - fv|
    s1 = _m('SUBTRACT', (-240, 120)); lk.new(fu.outputs[0], s1.inputs[0]); lk.new(fv.outputs[0], s1.inputs[1])
    dA = _m('ABSOLUTE', (-80, 120)); lk.new(s1.outputs[0], dA.inputs[0])
    # "\" distance = |fu + fv - 1|
    a1 = _m('ADD', (-240, -40)); lk.new(fu.outputs[0], a1.inputs[0]); lk.new(fv.outputs[0], a1.inputs[1])
    s2 = _m('SUBTRACT', (-80, -40)); lk.new(a1.outputs[0], s2.inputs[0]); s2.inputs[1].default_value = 1.0
    dB = _m('ABSOLUTE', (80, -40)); lk.new(s2.outputs[0], dB.inputs[0])
    # select per cell: d = dA + bit*(dB - dA)
    diff = _m('SUBTRACT', (240, 40)); lk.new(dB.outputs[0], diff.inputs[0]); lk.new(dA.outputs[0], diff.inputs[1])
    sel = _m('MULTIPLY', (400, 120)); lk.new(bit.outputs[0], sel.inputs[0]); lk.new(diff.outputs[0], sel.inputs[1])
    d = _m('ADD', (560, 40)); lk.new(dA.outputs[0], d.inputs[0]); lk.new(sel.outputs[0], d.inputs[1])
    mr = nd.new('ShaderNodeMapRange'); mr.interpolation_type = 'SMOOTHSTEP'; mr.clamp = True; mr.location = (720, 40)
    mr.inputs["From Min"].default_value = 0.0
    mr.inputs["To Min"].default_value = 1.0
    mr.inputs["To Max"].default_value = 0.0
    lk.new(gin.outputs["Width"], mr.inputs["From Max"])
    lk.new(d.outputs[0], mr.inputs["Value"])
    lk.new(mr.outputs["Result"], gout.inputs["Fac"])
    return ng


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
    # View-vector UV offset (parallax) — enabled per-layer via
    # proc_uv_view_shift > 0. Lets bands scroll as the camera moves.
    coord_out = _inject_view_uv_shift(
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

    elif pt == 'WOOD':
        # Concentric growth rings (Wave RINGS) + organic ring wobble +
        # optional fine grain. proc_scale=ring frequency, proc_detail=ring
        # detail, proc_wood_distortion=wobble, proc_wood_grain=grain amount.
        # Color1 = early wood (light), Color2 = late-wood ring line (dark).
        wave = node_tree.nodes.new("ShaderNodeTexWave")
        wave.wave_type = 'RINGS'
        try:
            wave.rings_direction = 'Z'
        except Exception:
            pass
        try:
            wave.wave_profile = 'SIN'
        except Exception:
            pass
        wave.inputs["Scale"].default_value      = layer.proc_scale
        wave.inputs["Detail"].default_value     = layer.proc_detail
        wave.inputs["Distortion"].default_value = getattr(layer, 'proc_wood_distortion', 1.5)
        wave.name = f"{TLM_PREFIX}proc_wood_wave_{_next_id()}"
        wave.location = (x - 100, y)
        _tag(wave, layer.name, "proc_tex")
        node_tree.links.new(vec_out, wave.inputs["Vector"])
        fac_out = wave.outputs["Fac"]

        grain_amt = getattr(layer, 'proc_wood_grain', 0.12)
        if grain_amt > 0.0:
            grain = node_tree.nodes.new("ShaderNodeTexNoise")
            grain.inputs["Scale"].default_value     = layer.proc_scale * 7.0
            grain.inputs["Detail"].default_value    = 5.0
            grain.inputs["Roughness"].default_value = 0.7
            grain.name = f"{TLM_PREFIX}proc_wood_grain_{_next_id()}"
            grain.location = (x - 350, y - 160)
            _tag(grain, layer.name, "proc_wood_grain")
            node_tree.links.new(vec_out, grain.inputs["Vector"])
            cen = node_tree.nodes.new("ShaderNodeMath")
            cen.operation = 'SUBTRACT'
            cen.inputs[1].default_value = 0.5
            cen.location = (x - 180, y - 160)
            _tag(cen, layer.name, "proc_wood_cen")
            node_tree.links.new(grain.outputs["Fac"], cen.inputs[0])
            scl = node_tree.nodes.new("ShaderNodeMath")
            scl.operation = 'MULTIPLY'
            scl.inputs[1].default_value = grain_amt
            scl.location = (x - 20, y - 160)
            _tag(scl, layer.name, "proc_wood_scl")
            node_tree.links.new(cen.outputs["Value"], scl.inputs[0])
            add = node_tree.nodes.new("ShaderNodeMath")
            add.operation = 'ADD'
            add.use_clamp = True
            add.location = (x + 140, y)
            _tag(add, layer.name, "proc_wood_add")
            node_tree.links.new(wave.outputs["Fac"], add.inputs[0])
            node_tree.links.new(scl.outputs["Value"], add.inputs[1])
            fac_out = add.outputs["Value"]

        return _build_proc_color_ramp(node_tree, layer, x, y, fac_out), None

    elif pt == 'SCRATCHES':
        # Anisotropic fine scratches: rotate coords, stretch hard along one
        # axis so noise smears into streaks, then threshold the high peaks
        # into thin scratch lines. proc_scale=density, proc_scratches_angle=
        # direction, proc_scratches_aniso=streak length, proc_scratches_width
        # =line thickness. Color1=surface, Color2=scratch streaks.
        rot = node_tree.nodes.new("ShaderNodeVectorRotate")
        rot.rotation_type = 'Z_AXIS'
        rot.inputs["Angle"].default_value = getattr(layer, 'proc_scratches_angle', 25.0) * 0.0174532925
        rot.location = (x - 380, y)
        rot.name = f"{TLM_PREFIX}proc_scr_rot_{_next_id()}"
        _tag(rot, layer.name, "proc_scr_rot")
        node_tree.links.new(vec_out, rot.inputs["Vector"])
        stretch = node_tree.nodes.new("ShaderNodeVectorMath")
        stretch.operation = 'MULTIPLY'
        stretch.inputs[1].default_value = (1.0, getattr(layer, 'proc_scratches_aniso', 8.0), 1.0)
        stretch.location = (x - 220, y)
        stretch.name = f"{TLM_PREFIX}proc_scr_str_{_next_id()}"
        _tag(stretch, layer.name, "proc_scr_str")
        node_tree.links.new(rot.outputs["Vector"], stretch.inputs[0])
        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value     = layer.proc_scale
        noise.inputs["Detail"].default_value    = max(2.0, layer.proc_detail)
        noise.inputs["Roughness"].default_value = 0.55
        noise.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        noise.location = (x - 60, y)
        _tag(noise, layer.name, "proc_tex")
        node_tree.links.new(stretch.outputs["Vector"], noise.inputs["Vector"])
        # Ridge transform 1-|2n-1| peaks along the noise mid-contours which,
        # because the coords are stretched, run as long thin streaks. POWER
        # sharpens them into fine scratch lines (thinner as Width decreases).
        w = getattr(layer, 'proc_scratches_width', 0.12)
        m2 = node_tree.nodes.new("ShaderNodeMath")
        m2.operation = 'MULTIPLY_ADD'
        m2.inputs[1].default_value = 2.0
        m2.inputs[2].default_value = -1.0
        m2.location = (x + 90, y)
        _tag(m2, layer.name, "proc_scr_m2")
        node_tree.links.new(noise.outputs["Fac"], m2.inputs[0])
        ab = node_tree.nodes.new("ShaderNodeMath")
        ab.operation = 'ABSOLUTE'
        ab.location = (x + 240, y)
        _tag(ab, layer.name, "proc_scr_ab")
        node_tree.links.new(m2.outputs["Value"], ab.inputs[0])
        inv = node_tree.nodes.new("ShaderNodeMath")
        inv.operation = 'SUBTRACT'
        inv.inputs[0].default_value = 1.0
        inv.location = (x + 390, y)
        _tag(inv, layer.name, "proc_scr_inv")
        node_tree.links.new(ab.outputs["Value"], inv.inputs[1])
        pw = node_tree.nodes.new("ShaderNodeMath")
        pw.operation = 'POWER'
        pw.use_clamp = True
        pw.inputs[1].default_value = max(1.0, 1.0 / max(0.04, w))
        pw.location = (x + 540, y)
        _tag(pw, layer.name, "proc_scr_pw")
        node_tree.links.new(inv.outputs["Value"], pw.inputs[0])
        return _build_proc_color_ramp(node_tree, layer, x, y, pw.outputs["Value"]), None

    elif pt == 'CAUSTICS':
        # Water-caustics / interference web: a smooth Voronoi distance field
        # rippled by sin(distance*freq), ridged so thin bright contour lines
        # remain — the shimmering light web on a pool floor / oil film.
        vor = node_tree.nodes.new("ShaderNodeTexVoronoi")
        try:
            vor.feature = 'SMOOTH_F1'
        except Exception:
            pass
        try:
            vor.voronoi_dimensions = '3D'
        except Exception:
            pass
        if hasattr(vor, 'normalize'):
            vor.normalize = True
        vor.inputs["Scale"].default_value = layer.proc_scale
        if "Randomness" in vor.inputs:
            vor.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(vor, layer)
        vor.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        vor.location = (x - 100, y)
        _tag(vor, layer.name, "proc_tex")
        node_tree.links.new(vec_out, vor.inputs["Vector"])
        mul = node_tree.nodes.new("ShaderNodeMath")
        mul.operation = 'MULTIPLY'
        mul.inputs[1].default_value = getattr(layer, 'proc_caustics_freq', 12.0)
        mul.location = (x + 60, y)
        _tag(mul, layer.name, "proc_cau_mul")
        node_tree.links.new(vor.outputs["Distance"], mul.inputs[0])
        sin = node_tree.nodes.new("ShaderNodeMath")
        sin.operation = 'SINE'
        sin.location = (x + 210, y)
        _tag(sin, layer.name, "proc_cau_sin")
        node_tree.links.new(mul.outputs["Value"], sin.inputs[0])
        ab = node_tree.nodes.new("ShaderNodeMath")
        ab.operation = 'ABSOLUTE'
        ab.location = (x + 360, y)
        _tag(ab, layer.name, "proc_cau_ab")
        node_tree.links.new(sin.outputs["Value"], ab.inputs[0])
        inv = node_tree.nodes.new("ShaderNodeMath")
        inv.operation = 'SUBTRACT'
        inv.inputs[0].default_value = 1.0
        inv.location = (x + 510, y)
        _tag(inv, layer.name, "proc_cau_inv")
        node_tree.links.new(ab.outputs["Value"], inv.inputs[1])
        pw = node_tree.nodes.new("ShaderNodeMath")
        pw.operation = 'POWER'
        pw.use_clamp = True
        pw.inputs[1].default_value = 3.0
        pw.location = (x + 660, y)
        _tag(pw, layer.name, "proc_cau_pw")
        node_tree.links.new(inv.outputs["Value"], pw.inputs[0])
        return _build_proc_color_ramp(node_tree, layer, x, y, pw.outputs["Value"]), None

    elif pt == 'WEAVE':
        # Over-under woven threads: vertical (warp) + horizontal (weft) thread
        # highlights from abs(sin), with a checker deciding which is on top
        # per cell. proc_scale=thread density, proc_weave_width=thickness.
        # Color1=gap/shadow, Color2=thread.
        sep = node_tree.nodes.new("ShaderNodeSeparateXYZ")
        sep.location = (x - 280, y)
        _tag(sep, layer.name, "proc_wv_sep")
        node_tree.links.new(vec_out, sep.inputs[0])
        freq = layer.proc_scale * 3.14159265
        # warp = abs(sin(X * freq))
        mux = node_tree.nodes.new("ShaderNodeMath"); mux.operation = 'MULTIPLY'
        mux.inputs[1].default_value = freq; mux.location = (x - 120, y + 130)
        _tag(mux, layer.name, "proc_wv_mux")
        node_tree.links.new(sep.outputs["X"], mux.inputs[0])
        six = node_tree.nodes.new("ShaderNodeMath"); six.operation = 'SINE'
        six.location = (x + 20, y + 130); _tag(six, layer.name, "proc_wv_six")
        node_tree.links.new(mux.outputs["Value"], six.inputs[0])
        abx = node_tree.nodes.new("ShaderNodeMath"); abx.operation = 'ABSOLUTE'
        abx.location = (x + 160, y + 130); _tag(abx, layer.name, "proc_wv_abx")
        node_tree.links.new(six.outputs["Value"], abx.inputs[0])
        # weft = abs(sin(Y * freq))
        muy = node_tree.nodes.new("ShaderNodeMath"); muy.operation = 'MULTIPLY'
        muy.inputs[1].default_value = freq; muy.location = (x - 120, y - 130)
        _tag(muy, layer.name, "proc_wv_muy")
        node_tree.links.new(sep.outputs["Y"], muy.inputs[0])
        siy = node_tree.nodes.new("ShaderNodeMath"); siy.operation = 'SINE'
        siy.location = (x + 20, y - 130); _tag(siy, layer.name, "proc_wv_siy")
        node_tree.links.new(muy.outputs["Value"], siy.inputs[0])
        aby = node_tree.nodes.new("ShaderNodeMath"); aby.operation = 'ABSOLUTE'
        aby.location = (x + 160, y - 130); _tag(aby, layer.name, "proc_wv_aby")
        node_tree.links.new(siy.outputs["Value"], aby.inputs[0])
        # checker over-under selector
        checker = node_tree.nodes.new("ShaderNodeTexChecker")
        checker.inputs["Scale"].default_value = layer.proc_scale
        checker.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        checker.location = (x - 120, y - 290)
        _tag(checker, layer.name, "proc_tex")
        node_tree.links.new(vec_out, checker.inputs["Vector"])
        # mix via math: weft + checker*(warp-weft)
        diff = node_tree.nodes.new("ShaderNodeMath"); diff.operation = 'SUBTRACT'
        diff.location = (x + 320, y); _tag(diff, layer.name, "proc_wv_diff")
        node_tree.links.new(abx.outputs["Value"], diff.inputs[0])
        node_tree.links.new(aby.outputs["Value"], diff.inputs[1])
        cm = node_tree.nodes.new("ShaderNodeMath"); cm.operation = 'MULTIPLY'
        cm.location = (x + 460, y - 90); _tag(cm, layer.name, "proc_wv_cm")
        node_tree.links.new(checker.outputs["Fac"], cm.inputs[0])
        node_tree.links.new(diff.outputs["Value"], cm.inputs[1])
        add = node_tree.nodes.new("ShaderNodeMath"); add.operation = 'ADD'
        add.location = (x + 600, y); _tag(add, layer.name, "proc_wv_add")
        node_tree.links.new(aby.outputs["Value"], add.inputs[0])
        node_tree.links.new(cm.outputs["Value"], add.inputs[1])
        pw = node_tree.nodes.new("ShaderNodeMath"); pw.operation = 'POWER'; pw.use_clamp = True
        pw.inputs[1].default_value = max(0.5, (1.0 - getattr(layer, 'proc_weave_width', 0.5)) * 5.0)
        pw.location = (x + 740, y); _tag(pw, layer.name, "proc_wv_pw")
        node_tree.links.new(add.outputs["Value"], pw.inputs[0])
        return _build_proc_color_ramp(node_tree, layer, x, y, pw.outputs["Value"]), None

    elif pt == 'TILES':
        # Tile generator on the Brick node: layout (Running Bond / Stack) via
        # row offset, mortar lines, and per-tile 2-tone random shade via the
        # brick Color1/Color2 variation. proc_scale=density,
        # proc_tiles_aspect=w/h, proc_tiles_mortar=line width,
        # proc_tiles_random=shade variation. Color1=mortar, Color2=tile.
        aspect = max(0.05, getattr(layer, 'proc_tiles_aspect', 2.0))
        amt = getattr(layer, 'proc_tiles_random', 0.5)
        running = getattr(layer, 'proc_tiles_layout', 'RUNNING_BOND') == 'RUNNING_BOND'
        brick = node_tree.nodes.new("ShaderNodeTexBrick")
        brick.offset = 0.5 if running else 0.0
        brick.offset_frequency = 2
        brick.inputs["Scale"].default_value = layer.proc_scale
        brick.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
        g = max(0.0, 1.0 - amt)
        brick.inputs["Color2"].default_value = (g, g, g, 1.0)
        brick.inputs["Mortar"].default_value = (0.0, 0.0, 0.0, 1.0)
        brick.inputs["Mortar Size"].default_value = max(0.0, getattr(layer, 'proc_tiles_mortar', 0.06) * 0.5)
        brick.inputs["Mortar Smooth"].default_value = 0.1
        brick.inputs["Bias"].default_value = 0.0
        _set_input_default(brick, "Brick Width", aspect * 0.2)
        _set_input_default(brick, "Row Height", 0.2)
        brick.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        brick.location = (x - 100, y)
        _tag(brick, layer.name, "proc_tex")
        node_tree.links.new(vec_out, brick.inputs["Vector"])
        bw = node_tree.nodes.new("ShaderNodeRGBToBW")
        bw.location = (x + 140, y); _tag(bw, layer.name, "proc_til_bw")
        node_tree.links.new(brick.outputs["Color"], bw.inputs["Color"])
        return _build_proc_color_ramp(node_tree, layer, x, y, bw.outputs["Val"]), None

    elif pt == 'SCATTER':
        # Texture-bombing scatter: Voronoi cells, each with a random value;
        # cells below the density threshold get a soft disc dot. Random
        # placement breaks tiling — spots, freckles, rivets, leaves, gravel.
        # proc_scale=cell density, proc_scatter_density=fraction with a dot,
        # proc_scatter_size=dot radius. Color1=background, Color2=dot.
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
        vor.inputs["Scale"].default_value = layer.proc_scale
        if "Randomness" in vor.inputs:
            vor.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(vor, layer)
        vor.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        vor.location = (x - 100, y); _tag(vor, layer.name, "proc_tex")
        node_tree.links.new(vec_out, vor.inputs["Vector"])
        cellrand = node_tree.nodes.new("ShaderNodeRGBToBW")
        cellrand.location = (x + 80, y - 180); _tag(cellrand, layer.name, "proc_sct_rand")
        node_tree.links.new(vor.outputs["Color"], cellrand.inputs["Color"])
        pres = node_tree.nodes.new("ShaderNodeMath"); pres.operation = 'LESS_THAN'
        pres.inputs[1].default_value = getattr(layer, 'proc_scatter_density', 0.5)
        pres.location = (x + 240, y - 180); _tag(pres, layer.name, "proc_sct_pres")
        node_tree.links.new(cellrand.outputs["Val"], pres.inputs[0])
        size = getattr(layer, 'proc_scatter_size', 0.4)
        dot = node_tree.nodes.new("ShaderNodeMapRange"); dot.interpolation_type = 'SMOOTHSTEP'; dot.clamp = True
        dot.inputs["From Min"].default_value = max(0.0, size - 0.12)
        dot.inputs["From Max"].default_value = max(0.01, size)
        dot.inputs["To Min"].default_value = 1.0
        dot.inputs["To Max"].default_value = 0.0
        dot.location = (x + 240, y); _tag(dot, layer.name, "proc_sct_dot")
        node_tree.links.new(vor.outputs["Distance"], dot.inputs["Value"])
        facn = node_tree.nodes.new("ShaderNodeMath"); facn.operation = 'MULTIPLY'; facn.use_clamp = True
        facn.location = (x + 420, y); _tag(facn, layer.name, "proc_sct_fac")
        node_tree.links.new(dot.outputs["Result"], facn.inputs[0])
        node_tree.links.new(pres.outputs["Value"], facn.inputs[1])
        return _build_proc_color_ramp(node_tree, layer, x, y, facn.outputs["Value"]), None

    elif pt == 'TRUCHET':
        # Connected-path Truchet via a reusable node group (per-cell random
        # diagonal orientation → continuous maze paths). proc_scale=cell
        # density, proc_truchet_width=line thickness. Color1=background,
        # Color2=path. (UV coords recommended.)
        grp = node_tree.nodes.new("ShaderNodeGroup")
        grp.node_tree = _get_truchet_node_group()
        grp.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        grp.location = (x - 100, y)
        _tag(grp, layer.name, "proc_tex")
        node_tree.links.new(vec_out, grp.inputs["Vector"])
        grp.inputs["Scale"].default_value = layer.proc_scale
        grp.inputs["Width"].default_value = getattr(layer, 'proc_truchet_width', 0.15)
        return _build_proc_color_ramp(node_tree, layer, x, y, grp.outputs["Fac"]), None

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
    # View-vector UV offset (parallax) — keep the Fac chain in sync with
    # the main pattern so both shift together when the camera moves.
    coord_out = _inject_view_uv_shift(
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

    elif pt == 'WOOD':
        wave = node_tree.nodes.new("ShaderNodeTexWave")
        wave.wave_type = 'RINGS'
        try:
            wave.rings_direction = 'Z'
        except Exception:
            pass
        try:
            wave.wave_profile = 'SIN'
        except Exception:
            pass
        wave.inputs["Scale"].default_value      = layer.proc_scale
        wave.inputs["Detail"].default_value     = layer.proc_detail
        wave.inputs["Distortion"].default_value = getattr(layer, 'proc_wood_distortion', 1.5)
        wave.name = f"{TLM_PREFIX}pfac_wood_wave_{name_suffix}"
        wave.location = (x - 100, y)
        _tag(wave, layer.name, "proc_tex")
        node_tree.links.new(vec_out, wave.inputs["Vector"])
        grain_amt = getattr(layer, 'proc_wood_grain', 0.12)
        if grain_amt <= 0.0:
            return wave.outputs["Fac"]
        grain = node_tree.nodes.new("ShaderNodeTexNoise")
        grain.inputs["Scale"].default_value     = layer.proc_scale * 7.0
        grain.inputs["Detail"].default_value    = 5.0
        grain.inputs["Roughness"].default_value = 0.7
        grain.name = f"{TLM_PREFIX}pfac_wood_grain_{name_suffix}"
        grain.location = (x - 350, y - 160)
        _tag(grain, layer.name, "proc_wood_grain")
        node_tree.links.new(vec_out, grain.inputs["Vector"])
        cen = node_tree.nodes.new("ShaderNodeMath")
        cen.operation = 'SUBTRACT'
        cen.inputs[1].default_value = 0.5
        cen.location = (x - 180, y - 160)
        _tag(cen, layer.name, "proc_wood_cen")
        node_tree.links.new(grain.outputs["Fac"], cen.inputs[0])
        scl = node_tree.nodes.new("ShaderNodeMath")
        scl.operation = 'MULTIPLY'
        scl.inputs[1].default_value = grain_amt
        scl.location = (x - 20, y - 160)
        _tag(scl, layer.name, "proc_wood_scl")
        node_tree.links.new(cen.outputs["Value"], scl.inputs[0])
        add = node_tree.nodes.new("ShaderNodeMath")
        add.operation = 'ADD'
        add.use_clamp = True
        add.location = (x + 140, y)
        _tag(add, layer.name, "proc_wood_add")
        node_tree.links.new(wave.outputs["Fac"], add.inputs[0])
        node_tree.links.new(scl.outputs["Value"], add.inputs[1])
        return add.outputs["Value"]

    elif pt == 'SCRATCHES':
        rot = node_tree.nodes.new("ShaderNodeVectorRotate")
        rot.rotation_type = 'Z_AXIS'
        rot.inputs["Angle"].default_value = getattr(layer, 'proc_scratches_angle', 25.0) * 0.0174532925
        rot.location = (x - 380, y)
        rot.name = f"{TLM_PREFIX}pfac_scr_rot_{name_suffix}"
        _tag(rot, layer.name, "proc_scr_rot")
        node_tree.links.new(vec_out, rot.inputs["Vector"])
        stretch = node_tree.nodes.new("ShaderNodeVectorMath")
        stretch.operation = 'MULTIPLY'
        stretch.inputs[1].default_value = (1.0, getattr(layer, 'proc_scratches_aniso', 8.0), 1.0)
        stretch.location = (x - 220, y)
        stretch.name = f"{TLM_PREFIX}pfac_scr_str_{name_suffix}"
        _tag(stretch, layer.name, "proc_scr_str")
        node_tree.links.new(rot.outputs["Vector"], stretch.inputs[0])
        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value     = layer.proc_scale
        noise.inputs["Detail"].default_value    = max(2.0, layer.proc_detail)
        noise.inputs["Roughness"].default_value = 0.55
        noise.name = f"{TLM_PREFIX}pfac_scr_tex_{name_suffix}"
        noise.location = (x - 60, y)
        _tag(noise, layer.name, "proc_tex")
        node_tree.links.new(stretch.outputs["Vector"], noise.inputs["Vector"])
        w = getattr(layer, 'proc_scratches_width', 0.12)
        m2 = node_tree.nodes.new("ShaderNodeMath")
        m2.operation = 'MULTIPLY_ADD'
        m2.inputs[1].default_value = 2.0
        m2.inputs[2].default_value = -1.0
        m2.location = (x + 90, y)
        _tag(m2, layer.name, "proc_scr_m2")
        node_tree.links.new(noise.outputs["Fac"], m2.inputs[0])
        ab = node_tree.nodes.new("ShaderNodeMath")
        ab.operation = 'ABSOLUTE'
        ab.location = (x + 240, y)
        _tag(ab, layer.name, "proc_scr_ab")
        node_tree.links.new(m2.outputs["Value"], ab.inputs[0])
        inv = node_tree.nodes.new("ShaderNodeMath")
        inv.operation = 'SUBTRACT'
        inv.inputs[0].default_value = 1.0
        inv.location = (x + 390, y)
        _tag(inv, layer.name, "proc_scr_inv")
        node_tree.links.new(ab.outputs["Value"], inv.inputs[1])
        pw = node_tree.nodes.new("ShaderNodeMath")
        pw.operation = 'POWER'
        pw.use_clamp = True
        pw.inputs[1].default_value = max(1.0, 1.0 / max(0.04, w))
        pw.location = (x + 540, y)
        _tag(pw, layer.name, "proc_scr_pw")
        node_tree.links.new(inv.outputs["Value"], pw.inputs[0])
        return pw.outputs["Value"]

    elif pt == 'CAUSTICS':
        vor = node_tree.nodes.new("ShaderNodeTexVoronoi")
        try:
            vor.feature = 'SMOOTH_F1'
        except Exception:
            pass
        try:
            vor.voronoi_dimensions = '3D'
        except Exception:
            pass
        if hasattr(vor, 'normalize'):
            vor.normalize = True
        vor.inputs["Scale"].default_value = layer.proc_scale
        if "Randomness" in vor.inputs:
            vor.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(vor, layer)
        vor.name = f"{TLM_PREFIX}pfac_cau_tex_{name_suffix}"
        vor.location = (x - 100, y)
        _tag(vor, layer.name, "proc_tex")
        node_tree.links.new(vec_out, vor.inputs["Vector"])
        mul = node_tree.nodes.new("ShaderNodeMath"); mul.operation = 'MULTIPLY'
        mul.inputs[1].default_value = getattr(layer, 'proc_caustics_freq', 12.0)
        mul.location = (x + 60, y); _tag(mul, layer.name, "proc_cau_mul")
        node_tree.links.new(vor.outputs["Distance"], mul.inputs[0])
        sin = node_tree.nodes.new("ShaderNodeMath"); sin.operation = 'SINE'
        sin.location = (x + 210, y); _tag(sin, layer.name, "proc_cau_sin")
        node_tree.links.new(mul.outputs["Value"], sin.inputs[0])
        ab = node_tree.nodes.new("ShaderNodeMath"); ab.operation = 'ABSOLUTE'
        ab.location = (x + 360, y); _tag(ab, layer.name, "proc_cau_ab")
        node_tree.links.new(sin.outputs["Value"], ab.inputs[0])
        inv = node_tree.nodes.new("ShaderNodeMath"); inv.operation = 'SUBTRACT'
        inv.inputs[0].default_value = 1.0
        inv.location = (x + 510, y); _tag(inv, layer.name, "proc_cau_inv")
        node_tree.links.new(ab.outputs["Value"], inv.inputs[1])
        pw = node_tree.nodes.new("ShaderNodeMath"); pw.operation = 'POWER'; pw.use_clamp = True
        pw.inputs[1].default_value = 3.0
        pw.location = (x + 660, y); _tag(pw, layer.name, "proc_cau_pw")
        node_tree.links.new(inv.outputs["Value"], pw.inputs[0])
        return pw.outputs["Value"]

    elif pt == 'WEAVE':
        sep = node_tree.nodes.new("ShaderNodeSeparateXYZ")
        sep.location = (x - 280, y); _tag(sep, layer.name, "proc_wv_sep")
        node_tree.links.new(vec_out, sep.inputs[0])
        freq = layer.proc_scale * 3.14159265
        mux = node_tree.nodes.new("ShaderNodeMath"); mux.operation = 'MULTIPLY'
        mux.inputs[1].default_value = freq; mux.location = (x - 120, y + 130)
        _tag(mux, layer.name, "proc_wv_mux")
        node_tree.links.new(sep.outputs["X"], mux.inputs[0])
        six = node_tree.nodes.new("ShaderNodeMath"); six.operation = 'SINE'
        six.location = (x + 20, y + 130); _tag(six, layer.name, "proc_wv_six")
        node_tree.links.new(mux.outputs["Value"], six.inputs[0])
        abx = node_tree.nodes.new("ShaderNodeMath"); abx.operation = 'ABSOLUTE'
        abx.location = (x + 160, y + 130); _tag(abx, layer.name, "proc_wv_abx")
        node_tree.links.new(six.outputs["Value"], abx.inputs[0])
        muy = node_tree.nodes.new("ShaderNodeMath"); muy.operation = 'MULTIPLY'
        muy.inputs[1].default_value = freq; muy.location = (x - 120, y - 130)
        _tag(muy, layer.name, "proc_wv_muy")
        node_tree.links.new(sep.outputs["Y"], muy.inputs[0])
        siy = node_tree.nodes.new("ShaderNodeMath"); siy.operation = 'SINE'
        siy.location = (x + 20, y - 130); _tag(siy, layer.name, "proc_wv_siy")
        node_tree.links.new(muy.outputs["Value"], siy.inputs[0])
        aby = node_tree.nodes.new("ShaderNodeMath"); aby.operation = 'ABSOLUTE'
        aby.location = (x + 160, y - 130); _tag(aby, layer.name, "proc_wv_aby")
        node_tree.links.new(siy.outputs["Value"], aby.inputs[0])
        checker = node_tree.nodes.new("ShaderNodeTexChecker")
        checker.inputs["Scale"].default_value = layer.proc_scale
        checker.name = f"{TLM_PREFIX}pfac_wv_chk_{name_suffix}"
        checker.location = (x - 120, y - 290); _tag(checker, layer.name, "proc_tex")
        node_tree.links.new(vec_out, checker.inputs["Vector"])
        diff = node_tree.nodes.new("ShaderNodeMath"); diff.operation = 'SUBTRACT'
        diff.location = (x + 320, y); _tag(diff, layer.name, "proc_wv_diff")
        node_tree.links.new(abx.outputs["Value"], diff.inputs[0])
        node_tree.links.new(aby.outputs["Value"], diff.inputs[1])
        cm = node_tree.nodes.new("ShaderNodeMath"); cm.operation = 'MULTIPLY'
        cm.location = (x + 460, y - 90); _tag(cm, layer.name, "proc_wv_cm")
        node_tree.links.new(checker.outputs["Fac"], cm.inputs[0])
        node_tree.links.new(diff.outputs["Value"], cm.inputs[1])
        add = node_tree.nodes.new("ShaderNodeMath"); add.operation = 'ADD'
        add.location = (x + 600, y); _tag(add, layer.name, "proc_wv_add")
        node_tree.links.new(aby.outputs["Value"], add.inputs[0])
        node_tree.links.new(cm.outputs["Value"], add.inputs[1])
        pw = node_tree.nodes.new("ShaderNodeMath"); pw.operation = 'POWER'; pw.use_clamp = True
        pw.inputs[1].default_value = max(0.5, (1.0 - getattr(layer, 'proc_weave_width', 0.5)) * 5.0)
        pw.location = (x + 740, y); _tag(pw, layer.name, "proc_wv_pw")
        node_tree.links.new(add.outputs["Value"], pw.inputs[0])
        return pw.outputs["Value"]

    elif pt == 'TILES':
        aspect = max(0.05, getattr(layer, 'proc_tiles_aspect', 2.0))
        amt = getattr(layer, 'proc_tiles_random', 0.5)
        running = getattr(layer, 'proc_tiles_layout', 'RUNNING_BOND') == 'RUNNING_BOND'
        brick = node_tree.nodes.new("ShaderNodeTexBrick")
        brick.offset = 0.5 if running else 0.0
        brick.offset_frequency = 2
        brick.inputs["Scale"].default_value = layer.proc_scale
        brick.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
        g = max(0.0, 1.0 - amt)
        brick.inputs["Color2"].default_value = (g, g, g, 1.0)
        brick.inputs["Mortar"].default_value = (0.0, 0.0, 0.0, 1.0)
        brick.inputs["Mortar Size"].default_value = max(0.0, getattr(layer, 'proc_tiles_mortar', 0.06) * 0.5)
        brick.inputs["Mortar Smooth"].default_value = 0.1
        brick.inputs["Bias"].default_value = 0.0
        _set_input_default(brick, "Brick Width", aspect * 0.2)
        _set_input_default(brick, "Row Height", 0.2)
        brick.name = f"{TLM_PREFIX}pfac_til_brick_{name_suffix}"
        brick.location = (x - 100, y)
        _tag(brick, layer.name, "proc_tex")
        node_tree.links.new(vec_out, brick.inputs["Vector"])
        bw = node_tree.nodes.new("ShaderNodeRGBToBW")
        bw.location = (x + 140, y); _tag(bw, layer.name, "proc_til_bw")
        node_tree.links.new(brick.outputs["Color"], bw.inputs["Color"])
        return bw.outputs["Val"]

    elif pt == 'SCATTER':
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
        vor.inputs["Scale"].default_value = layer.proc_scale
        if "Randomness" in vor.inputs:
            vor.inputs["Randomness"].default_value = layer.proc_randomness
        _set_voronoi_fractal_inputs(vor, layer)
        vor.name = f"{TLM_PREFIX}pfac_sct_tex_{name_suffix}"
        vor.location = (x - 100, y); _tag(vor, layer.name, "proc_tex")
        node_tree.links.new(vec_out, vor.inputs["Vector"])
        cellrand = node_tree.nodes.new("ShaderNodeRGBToBW")
        cellrand.location = (x + 80, y - 180); _tag(cellrand, layer.name, "proc_sct_rand")
        node_tree.links.new(vor.outputs["Color"], cellrand.inputs["Color"])
        pres = node_tree.nodes.new("ShaderNodeMath"); pres.operation = 'LESS_THAN'
        pres.inputs[1].default_value = getattr(layer, 'proc_scatter_density', 0.5)
        pres.location = (x + 240, y - 180); _tag(pres, layer.name, "proc_sct_pres")
        node_tree.links.new(cellrand.outputs["Val"], pres.inputs[0])
        size = getattr(layer, 'proc_scatter_size', 0.4)
        dot = node_tree.nodes.new("ShaderNodeMapRange"); dot.interpolation_type = 'SMOOTHSTEP'; dot.clamp = True
        dot.inputs["From Min"].default_value = max(0.0, size - 0.12)
        dot.inputs["From Max"].default_value = max(0.01, size)
        dot.inputs["To Min"].default_value = 1.0
        dot.inputs["To Max"].default_value = 0.0
        dot.location = (x + 240, y); _tag(dot, layer.name, "proc_sct_dot")
        node_tree.links.new(vor.outputs["Distance"], dot.inputs["Value"])
        facn = node_tree.nodes.new("ShaderNodeMath"); facn.operation = 'MULTIPLY'; facn.use_clamp = True
        facn.location = (x + 420, y); _tag(facn, layer.name, "proc_sct_fac")
        node_tree.links.new(dot.outputs["Result"], facn.inputs[0])
        node_tree.links.new(pres.outputs["Value"], facn.inputs[1])
        return facn.outputs["Value"]

    elif pt == 'TRUCHET':
        grp = node_tree.nodes.new("ShaderNodeGroup")
        grp.node_tree = _get_truchet_node_group()
        grp.name = f"{TLM_PREFIX}pfac_tru_{name_suffix}"
        grp.location = (x - 100, y)
        _tag(grp, layer.name, "proc_tex")
        node_tree.links.new(vec_out, grp.inputs["Vector"])
        grp.inputs["Scale"].default_value = layer.proc_scale
        grp.inputs["Width"].default_value = getattr(layer, 'proc_truchet_width', 0.15)
        return grp.outputs["Fac"]

    if tex is None or fac_out is None:
        return None

    tex.name = f"{TLM_PREFIX}pfac_tex_{name_suffix}"
    tex.location = (x - 100, y)
    _tag(tex, layer.name, "proc_tex")
    return fac_out


# ── Normal map channel builder ───────────────────────────────────────────────

