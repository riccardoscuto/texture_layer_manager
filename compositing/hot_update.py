"""Hot-update handlers for live property changes (avoid full rebuild).

This module holds the dispatch table and per-property handlers that
TLM uses to live-update shader nodes when the user drags a slider or
picks a colour. Each ``_hot_<prop>`` function inspects the node tree
for tagged nodes belonging to ``layer`` and pokes the relevant socket
default_value or property — no rebuild, no debounce.

``hot_update_property(material, layer, prop_name)`` is the public
entry called by property callbacks. ``hot_update_sun_direction()`` is
called from the depsgraph handler when the Sun light moves (drives
NDOTL / NDOTH masks).
"""

import bpy
import time as _time

# Late imports from the package's __init__.py — these resolve at call
# time because the parent __init__ defines them BEFORE doing
# ``from .hot_update import *`` at the end of its body.
from . import (
    _next_id,
    _tag,
    _find_tagged,
    _find_all_tagged,
    _set_input_default,
    _apply_mapping_settings,
    _material_from_node_tree,
    _apply_mask,
    _build_bump_channel,
    _effective_blend_mode,
    _enabled_socket,
    _factor_socket,
    _a_socket,
    _b_socket,
    # _build_proc_color_ramp + _ramp_stops + _clamped_ramp_position
    # live in procedurals.py (loaded BEFORE hot_update in __init__.py).
    _build_proc_color_ramp,
    _ramp_stops,
    _clamped_ramp_position,
)
# Module-level constants from __init__.py (TLM_PREFIX is used by tags
# the hot handlers stamp on nodes; the others are lookup tables used
# by various _hot_* functions).
from . import (
    TLM_PREFIX,
    BLEND_TO_MIX_MODE,
    _ALL_CHANNELS,
    _OUTPUT_CHANNEL_TO_TARGET,
    _PROC_INPUT_MAP,
    _USE_NEW_MIX,
)  # noqa: F401


__all__ = [
    '_hot_opacity',
    '_hot_blend_mode',
    '_hot_fill_color',
    '_ensure_scalar_fill_value_node',
    '_hot_scalar_fill',
    '_hot_emission_color',
    '_hot_emission_strength',
    '_hot_proc_tex_input',
    '_hot_proc_offset',
    '_hot_proc_brick',
    '_hot_proc_dots',
    '_hot_proc_cracks',
    '_hot_proc_ridged',
    '_hot_proc_gabor',
    '_hot_proc_stripe',
    '_hot_proc_hex',
    'hot_update_sun_direction',
    '_find_first_sun_direction',
    '_hot_proc_color',
    '_hot_vector_distortion',
    '_hot_marble_distortion',
    '_hot_adj_hue_sat',
    '_hot_adj_bc',
    '_hot_adj_levels',
    '_hot_adj_cb',
    '_hot_bump',
    '_hot_fresnel',
    '_hot_proc_fresnel_ior',
    '_hot_mask_fresnel_ior',
    '_hot_mask_wireframe',
    '_hot_normal_strength',
    '_ensure_normal_mapping_node',
    '_hot_normal_mapping',
    '_hot_paint_mapping',
    '_hot_mask_ao_distance',
    '_hot_mask_contrast',
    '_hot_mask_softness',
    '_hot_mask_levels',
    '_hot_mask_blur',
    '_hot_image_swap',
    'performance_enabled',
    'record_performance',
    '_material_mesh_stats',
    '_record_rebuild_performance',
    'hot_update_property',
    '_IMAGE_HOT_MAP',
    '_HOT_DISPATCH',
]

def _hot_opacity(node_tree, layer, prop_name):
    found = False
    for ch in _ALL_CHANNELS:
        node = _find_tagged(node_tree, layer.name, f"opacity_target_{ch}")
        if not node:
            continue
        idx = node.get("tlm_opacity_input_idx", -1)
        if idx == -1:
            _factor_socket(node).default_value = layer.opacity
        else:
            node.inputs[idx].default_value = layer.opacity
        found = True
    return found


def _hot_blend_mode(node_tree, layer, prop_name):
    found = False
    for node in node_tree.nodes:
        if node.get("tlm_layer") != layer.name:
            continue
        if not node.name.startswith(f"{TLM_PREFIX}mix_"):
            continue
        if not hasattr(node, "blend_type"):
            continue

        # Scalar mixes must be RGBA Mix + RGBToBW to make artistic blend
        # modes behave like Base Color. Older node trees built before this
        # fix need a full rebuild so the converter node is inserted.
        if node.name.startswith(f"{TLM_PREFIX}mix_scalar_"):
            if _USE_NEW_MIX and getattr(node, "data_type", None) != 'RGBA':
                return False
            if not node.get("tlm_scalar_result_node"):
                return False

        role = node.get("tlm_role", "")
        channel = "base_color"
        if role.startswith("opacity_target_"):
            channel = role[len("opacity_target_"):]
        elif role.startswith("mix_"):
            channel = role[len("mix_"):]
        try:
            node.blend_type = BLEND_TO_MIX_MODE.get(
                _effective_blend_mode(layer, channel), "MIX"
            )
        except (TypeError, AttributeError):
            node.blend_type = "MIX"
        found = True
    return found


def _hot_fill_color(node_tree, layer, prop_name):
    node = _find_tagged(node_tree, layer.name, "fill_base_color")
    if not node:
        node = _find_tagged(node_tree, layer.name, "fill")
    if not node:
        return False
    node.outputs[0].default_value = layer.fill_color
    return True


def _ensure_scalar_fill_value_node(node_tree, layer, channel, prop_name):
    """Recover pre-fix Fill scalar graphs that used color luminance by mistake."""
    if getattr(layer, 'layer_type', '') != "FILL":
        return None

    out_ch = getattr(layer, 'output_channel', 'BASE_COLOR') or 'BASE_COLOR'
    if out_ch == 'AUTO':
        out_ch = 'BASE_COLOR'
    fill_target = _OUTPUT_CHANNEL_TO_TARGET.get(out_ch, 'base_color')
    if fill_target == channel:
        return None

    lum = _find_tagged(node_tree, layer.name, f"fill_lum_{channel}")
    if not lum:
        return None
    old_socket = lum.outputs.get("Val") or lum.outputs[0]
    old_links = list(old_socket.links)
    if not old_links:
        return None

    node = node_tree.nodes.new("ShaderNodeValue")
    node.name = f"{TLM_PREFIX}val_{_next_id()}"
    node.location = (lum.location.x, lum.location.y)
    node.outputs[0].default_value = getattr(layer, prop_name, 0.0)
    _tag(node, layer.name, f"val_{channel}")
    for link in old_links:
        to_socket = link.to_socket
        node_tree.links.remove(link)
        node_tree.links.new(node.outputs[0], to_socket)
    return node


def _hot_scalar_fill(node_tree, layer, prop_name):
    channel_map = {
        "roughness_fill": "roughness",
        "metallic_fill": "metallic",
        "transmission_fill": "transmission",
        "alpha_fill": "alpha",
    }
    ch = channel_map.get(prop_name)
    if not ch:
        return False
    node = _find_tagged(node_tree, layer.name, f"val_{ch}")
    if not node:
        node = _ensure_scalar_fill_value_node(node_tree, layer, ch, prop_name)
    if not node:
        return False
    node.outputs[0].default_value = getattr(layer, prop_name, 0.0)
    return True


def _hot_emission_color(node_tree, layer, prop_name):
    node = _find_tagged(node_tree, layer.name, "fill_emission")
    if not node:
        return False
    node.outputs[0].default_value = layer.emission_color
    return True


def _hot_emission_strength(node_tree, layer, prop_name):
    node = _find_tagged(node_tree, "__material__", "emission_strength")
    if not node:
        return False
    # Re-aggregate max emission strength across all visible layers
    mat = _material_from_node_tree(node_tree)
    if not mat:
        return False
    strengths = [l.emission_strength * l.opacity
                 for l in mat.tlm.layers if l.use_emission and l.visible]
    node.outputs[0].default_value = max(strengths) if strengths else 0.0
    return True


def _hot_proc_tex_input(node_tree, layer, prop_name):
    input_name = _PROC_INPUT_MAP.get(prop_name)
    if not input_name:
        return False
    nodes = _find_all_tagged(node_tree, layer.name, "proc_tex")
    if not nodes:
        return False
    val = getattr(layer, prop_name, 0.0)
    if prop_name == "proc_scale" and getattr(layer, 'proc_type', '') == 'GRADIENT':
        return True
    for n in nodes:
        if input_name in n.inputs:
            n.inputs[input_name].default_value = val
    # Marble has separate noise nodes that need scale/detail sync
    if prop_name == "proc_scale":
        for mn in _find_all_tagged(node_tree, layer.name, "marble_noise"):
            if "Scale" in mn.inputs:
                mn.inputs["Scale"].default_value = val * 2.0
    if prop_name == "proc_detail":
        for mn in _find_all_tagged(node_tree, layer.name, "marble_noise"):
            if "Detail" in mn.inputs:
                mn.inputs["Detail"].default_value = val
    if prop_name == "proc_roughness_proc":
        for mn in _find_all_tagged(node_tree, layer.name, "marble_noise"):
            if "Roughness" in mn.inputs:
                mn.inputs["Roughness"].default_value = val
    if prop_name == "proc_distortion":
        for mn in _find_all_tagged(node_tree, layer.name, "marble_noise"):
            if "Distortion" in mn.inputs:
                mn.inputs["Distortion"].default_value = val * 0.5
    return True


def _hot_proc_offset(node_tree, layer, prop_name):
    nodes = _find_all_tagged(node_tree, layer.name, "proc_map")
    if not nodes:
        return False
    for n in nodes:
        _apply_mapping_settings(n, layer)
    return True


def _hot_proc_brick(node_tree, layer, prop_name):
    nodes = _find_all_tagged(node_tree, layer.name, "proc_tex")
    if not nodes:
        return False
    found = False
    for n in nodes:
        if hasattr(n, 'offset'):
            n.offset = getattr(layer, 'proc_brick_offset', 0.5)
            found = True
        if hasattr(n, 'offset_frequency'):
            n.offset_frequency = getattr(layer, 'proc_brick_offset_freq', 2)
            found = True
        if hasattr(n, 'squash'):
            n.squash = getattr(layer, 'proc_brick_squash', 1.0)
            found = True
        if hasattr(n, 'squash_frequency'):
            n.squash_frequency = getattr(layer, 'proc_brick_squash_freq', 2)
            found = True
        found = _set_input_default(n, "Mortar Size", getattr(layer, 'proc_brick_mortar_size', 0.02)) or found
        found = _set_input_default(n, "Mortar Smooth", getattr(layer, 'proc_brick_mortar_smooth', 0.1)) or found
        found = _set_input_default(n, "Bias", getattr(layer, 'proc_brick_bias', 0.0)) or found
        found = _set_input_default(n, "Brick Width", getattr(layer, 'proc_brick_width', 0.5)) or found
        found = _set_input_default(n, "Row Height", getattr(layer, 'proc_brick_row_height', 0.25)) or found
    return found


def _hot_proc_dots(node_tree, layer, prop_name):
    nodes = _find_all_tagged(node_tree, layer.name, "proc_dots_mr")
    if not nodes:
        return False
    radius = getattr(layer, 'proc_dots_radius', 0.35)
    softness = getattr(layer, 'proc_dots_softness', 0.15)
    half_band = max(0.005, softness * 0.5)
    for n in nodes:
        n.inputs["From Min"].default_value = max(0.0, radius - half_band)
        n.inputs["From Max"].default_value = min(1.0, radius + half_band)
    return True


def _hot_proc_cracks(node_tree, layer, prop_name):
    nodes = _find_all_tagged(node_tree, layer.name, "proc_cracks_mr")
    if not nodes:
        return False
    width = getattr(layer, 'proc_cracks_width', 0.05)
    sharpness = getattr(layer, 'proc_cracks_sharpness', 0.7)
    band = max(0.003, (1.0 - sharpness) * width * 0.8)
    for n in nodes:
        n.inputs["From Min"].default_value = max(0.0, width - band)
        n.inputs["From Max"].default_value = min(1.0, width + band)
    return True


def _hot_proc_ridged(node_tree, layer, prop_name):
    tags = {
        "proc_ridged_gain": "proc_ridge_pow",
        "proc_ridged_offset": "proc_ridge_mul",
    }
    node_tag = tags.get(prop_name)
    if not node_tag:
        return False
    nodes = _find_all_tagged(node_tree, layer.name, node_tag)
    if not nodes:
        return False
    value = getattr(layer, prop_name, 1.0)
    for n in nodes:
        n.inputs[1].default_value = value
    return True


def _hot_proc_gabor(node_tree, layer, prop_name):
    nodes = _find_all_tagged(node_tree, layer.name, "proc_tex")
    if not nodes:
        return False
    import math as _math
    found = False
    for n in nodes:
        found = _set_input_default(n, "Frequency", getattr(layer, 'proc_gabor_frequency', 2.0)) or found
        found = _set_input_default(n, "Anisotropy", getattr(layer, 'proc_gabor_anisotropy', 1.0)) or found
        found = _set_input_default(
            n, "Orientation", _math.radians(getattr(layer, 'proc_gabor_orientation', 45.0))
        ) or found
    return found


def _hot_proc_stripe(node_tree, layer, prop_name):
    """Update the Map Range smoothstep window driving STRIPES width/sharpness.

    SAW wave fac runs 0→1 each period; the threshold below which fac is
    "background" is (1 - width), so the bright stripe spans `width` of
    the period (width=0.5 → equal stripes, width=1 → solid bright).

    Also syncs the inner Map Range (proc_stripe_mr_inner) when 3-colour
    mode is on, so the core stripe stays consistent with the main one.
    """
    nodes = _find_all_tagged(node_tree, layer.name, "proc_stripe_mr")
    if not nodes:
        return False
    width = layer.proc_stripe_width
    sharpness = layer.proc_stripe_sharpness
    threshold = 1.0 - width
    edge = (1.0 - sharpness) * 0.4
    fmin = max(0.0, threshold - edge)
    fmax = min(1.0, threshold + edge + 1e-4)
    for n in nodes:
        n.inputs["From Min"].default_value = fmin
        n.inputs["From Max"].default_value = fmax
    # Inner stripe (only present when use_proc_color3 is on)
    inner_nodes = _find_all_tagged(node_tree, layer.name, "proc_stripe_mr_inner")
    if inner_nodes:
        core_frac = max(0.001, layer.proc_color3_position)
        inner_threshold = 1.0 - width * core_frac
        i_fmin = max(0.0, inner_threshold - edge)
        i_fmax = min(1.0, inner_threshold + edge + 1e-4)
        for n in inner_nodes:
            n.inputs["From Min"].default_value = i_fmin
            n.inputs["From Max"].default_value = i_fmax
    return True


def _hot_proc_hex(node_tree, layer, prop_name):
    """Update the Map Range smoothstep window driving HEX_GRID edge width.

    Also syncs the inner Map Range (proc_hex_mr_inner) when 3-colour
    mode is on, so the core line stays consistent with the main edge.
    """
    nodes = _find_all_tagged(node_tree, layer.name, "proc_hex_mr")
    if not nodes:
        return False
    edge_w = layer.proc_hex_edge_width
    for n in nodes:
        n.inputs["From Min"].default_value = max(0.0, edge_w - 0.005)
        n.inputs["From Max"].default_value = min(1.0, edge_w + 0.005 + 1e-4)
    # Inner edge (only present when use_proc_color3 is on)
    inner_nodes = _find_all_tagged(node_tree, layer.name, "proc_hex_mr_inner")
    if inner_nodes:
        core_frac = max(0.001, layer.proc_color3_position)
        inner_w = edge_w * core_frac
        i_fmin = max(0.0, inner_w - 0.005)
        i_fmax = min(1.0, inner_w + 0.005 + 1e-4)
        for n in inner_nodes:
            n.inputs["From Min"].default_value = i_fmin
            n.inputs["From Max"].default_value = i_fmax
    return True

def hot_update_sun_direction():
    """Walk all TLM materials, find NDOTL/NDOTH dot-product nodes, and
    refresh their baked sun direction vector. Called by the depsgraph
    handler whenever the scene's Sun light moves or rotates.

    This avoids a full material rebuild on Sun changes — much cheaper.
    """
    sun_dir = _find_first_sun_direction()
    updated_materials = 0

    for mat in bpy.data.materials:
        if not mat.use_nodes or not mat.node_tree:
            continue
        tree_updated = False
        for n in mat.node_tree.nodes:
            # NDOTL: TLM_mask_ndotl_dot_*  — second input is the baked sun vector
            if 'mask_ndotl_dot' in n.name and n.type == 'VECTOR_MATH':
                try:
                    n.inputs[1].default_value = sun_dir
                    tree_updated = True
                except Exception:
                    pass
            # NDOTH: TLM_mask_ndoth_addlv_*  — input 1 holds the sun dir
            # (the ADD node summing V + L; L is the second input)
            if 'mask_ndoth_addlv' in n.name and n.type == 'VECTOR_MATH':
                try:
                    n.inputs[1].default_value = sun_dir
                    tree_updated = True
                except Exception:
                    pass
        if tree_updated:
            updated_materials += 1
    return updated_materials


def _find_first_sun_direction():
    """Return the world-space direction of the first Sun light in the scene.

    Used by mask_source='NDOTL' to bake a Sun direction into a VectorMath
    dot-product, enabling NdotL-based shading (anime cel, rim light, etc.).

    Blender Sun lamps point along the local -Z by convention. We multiply
    that by the lamp's world matrix to get the world-space direction the
    light travels FROM (so dot(normal, sun_dir) > 0 means the surface
    faces the light = lit; < 0 means facing away = shadow).

    Fallback when no Sun is present: a sensible "above-and-side" vector
    that gives plausible top-down shading on most setups.
    """
    try:
        # Prefer the first Sun light object in the scene
        for obj in bpy.context.scene.objects:
            if obj.type == 'LIGHT' and obj.data.type == 'SUN':
                # The sun's "to-light" direction in world space is
                # -world_matrix.col[2] (the negated local Z axis transformed
                # to world). We want dot(N, dir_to_light) so the lit side
                # has positive dot — hence we negate Blender's -Z.
                wm = obj.matrix_world
                # Third column (index 2) of the rotation part is the local Z axis
                # transformed to world. Sun lamps shine along -local_Z, so the
                # direction TO the light is +local_Z in world space.
                z_axis = wm.col[2].to_3d().normalized()
                return tuple(z_axis)
    except Exception:
        pass
    # Fallback: light coming from top-front-right (good portrait default)
    return (0.4, -0.3, 0.85)


def _hot_proc_color(node_tree, layer, prop_name):
    nodes = _find_all_tagged(node_tree, layer.name, "proc_cr")
    if nodes:
        # ── Build the target (position, color) pairs in data order ──
        # Same logic as _build_proc_color_ramp so hot path stays in sync
        # with the build path.
        pairs = []  # list of (pos, color) tuples

        # Stop positions for color1 + color2 — manual or computed.
        # Same special-case as the build path: GRADIENT and FRESNEL bypass
        # contrast/center to give a full 0..1 sweep.
        if getattr(layer, 'proc_use_manual_stops', False) and layer.proc_type not in ('GRADIENT', 'FRESNEL'):
            pos1 = max(0.0, min(1.0, getattr(layer, 'proc_color1_position', 0.0)))
            pos2 = max(0.0, min(1.0, getattr(layer, 'proc_color2_position', 1.0)))
            if abs(pos1 - pos2) < 1e-4:
                pos2 = min(1.0, pos1 + 0.001)
            pairs.append((pos1, tuple(layer.proc_color1)))
            pairs.append((pos2, tuple(layer.proc_color2)))
        else:
            contrast = getattr(layer, 'proc_contrast', 0.5)
            center = getattr(layer, 'proc_ramp_center', 0.5)
            if layer.proc_type in ('GRADIENT', 'FRESNEL'):
                contrast = 0.0
                center = 0.5
            stop_lo, stop_hi = _ramp_stops(contrast, center)
            pairs.append((stop_lo, tuple(layer.proc_color1)))
            pairs.append((stop_hi, tuple(layer.proc_color2)))

        # Legacy color3
        if getattr(layer, 'use_proc_color3', False):
            pairs.append((
                _clamped_ramp_position(layer.proc_color3_position),
                tuple(layer.proc_color3),
            ))

        # Extra color stops
        for extra in getattr(layer, 'proc_extra_color_stops', []):
            pairs.append((
                _clamped_ramp_position(extra.position),
                tuple(extra.color),
            ))

        # Sort by position — CRITICAL. Blender's ColorRamp elements MUST
        # be in monotonic increasing position order for the renderer
        # (and for color_ramp.evaluate) to produce correct results. The
        # OLD hot path mutated elements in arbitrary order which left
        # the array non-monotonic → wrong evaluate output and possible
        # wrong shader output. Sorting before assignment guarantees a
        # well-formed ramp.
        pairs.sort(key=lambda p: p[0])

        for cr in nodes:
            if len(cr.color_ramp.elements) != len(pairs):
                return False  # Topology changed → caller will rebuild

            # Snapshot element references — assigning via this list is
            # stable even if Blender re-sorts the internal collection
            # while we're mid-update. Each ref points to the underlying
            # element struct, so modifying .position / .color affects
            # that specific element regardless of array reordering.
            elem_refs = list(cr.color_ramp.elements)
            for i, (pos, col) in enumerate(pairs):
                elem_refs[i].position = pos
                elem_refs[i].color = col

            # color_mode + interpolation are also live-updatable
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
        return True

    # ── Mix-topology path (STRIPES / HEX_GRID) ────────────────────────────
    # These procs produce a binary fac so they bypass ColorRamp and
    # store their colours on dedicated Mix nodes. proc_color3_position
    # here means "core fraction" (0..1) of the inner stripe/edge.
    mix_main = _find_tagged(node_tree, layer.name, "proc_cmix_main")
    if not mix_main:
        return False  # nothing to update — fall back to rebuild

    has_color3 = getattr(layer, 'use_proc_color3', False)
    mix_inner = _find_tagged(node_tree, layer.name, "proc_cmix_inner")
    # Topology mismatch — let _on_layer_update do a full rebuild.
    if has_color3 and not mix_inner:
        return False
    if not has_color3 and mix_inner:
        return False

    if has_color3:
        # main: A=color1, B=inner.Result; inner: A=color2, B=color3
        _a_socket(mix_main).default_value  = layer.proc_color1
        _a_socket(mix_inner).default_value = layer.proc_color2
        _b_socket(mix_inner).default_value = layer.proc_color3
        # When proc_color3_position changes, re-thread the inner Map Range.
        if prop_name == "proc_color3_position":
            core_frac = max(0.001, layer.proc_color3_position)
            if layer.proc_type == 'STRIPES':
                width = layer.proc_stripe_width
                sharpness = layer.proc_stripe_sharpness
                edge = (1.0 - sharpness) * 0.4
                inner_threshold = 1.0 - width * core_frac
                fmin = max(0.0, inner_threshold - edge)
                fmax = min(1.0, inner_threshold + edge + 1e-4)
                for n in _find_all_tagged(node_tree, layer.name, "proc_stripe_mr_inner"):
                    n.inputs["From Min"].default_value = fmin
                    n.inputs["From Max"].default_value = fmax
            elif layer.proc_type == 'HEX_GRID':
                edge_w = layer.proc_hex_edge_width
                inner_w = edge_w * core_frac
                fmin = max(0.0, inner_w - 0.005)
                fmax = min(1.0, inner_w + 0.005 + 1e-4)
                for n in _find_all_tagged(node_tree, layer.name, "proc_hex_mr_inner"):
                    n.inputs["From Min"].default_value = fmin
                    n.inputs["From Max"].default_value = fmax
    else:
        _a_socket(mix_main).default_value = layer.proc_color1
        _b_socket(mix_main).default_value = layer.proc_color2
    return True


def _hot_vector_distortion(node_tree, layer, prop_name):
    distortion = getattr(layer, 'proc_vector_distortion', 0.0)
    if distortion <= 0.0:
        return False  # need rebuild to remove nodes
    noise_nodes = _find_all_tagged(node_tree, layer.name, "vdist_noise")
    mix_nodes = _find_all_tagged(node_tree, layer.name, "vdist_mix")
    if not noise_nodes and not mix_nodes:
        return False
    for n in noise_nodes:
        n.inputs["Scale"].default_value = layer.proc_scale * 0.5
    for m in mix_nodes:
        m.inputs["Factor"].default_value = distortion * 0.15
    return True


def _hot_marble_distortion(node_tree, layer, prop_name):
    nodes = _find_all_tagged(node_tree, layer.name, "marble_mult")
    if not nodes:
        return False
    for n in nodes:
        n.inputs[1].default_value = layer.proc_marble_distortion
    return True


def _hot_adj_hue_sat(node_tree, layer, prop_name):
    node = _find_tagged(node_tree, layer.name, "adj_hue_sat")
    if not node:
        return False
    node.inputs["Hue"].default_value = layer.adj_hue
    node.inputs["Saturation"].default_value = layer.adj_saturation
    node.inputs["Value"].default_value = layer.adj_value
    return True


def _hot_adj_bc(node_tree, layer, prop_name):
    node = _find_tagged(node_tree, layer.name, "adj_bc")
    if not node:
        return False
    node.inputs["Bright"].default_value = layer.adj_brightness
    node.inputs["Contrast"].default_value = layer.adj_contrast
    return True


def _hot_adj_levels(node_tree, layer, prop_name):
    mr_in = _find_tagged(node_tree, layer.name, "adj_lvl_in")
    gamma = _find_tagged(node_tree, layer.name, "adj_lvl_gamma")
    mr_out = _find_tagged(node_tree, layer.name, "adj_lvl_out")
    if not (mr_in and gamma and mr_out):
        return False
    if hasattr(mr_in, 'data_type') and mr_in.data_type == 'FLOAT_VECTOR':
        mr_in.inputs["From Min"].default_value = (layer.adj_in_min,) * 3
        mr_in.inputs["From Max"].default_value = (layer.adj_in_max,) * 3
        mr_out.inputs["To Min"].default_value = (layer.adj_out_min,) * 3
        mr_out.inputs["To Max"].default_value = (layer.adj_out_max,) * 3
    else:
        mr_in.inputs["From Min"].default_value = layer.adj_in_min
        mr_in.inputs["From Max"].default_value = layer.adj_in_max
        mr_out.inputs["To Min"].default_value = layer.adj_out_min
        mr_out.inputs["To Max"].default_value = layer.adj_out_max
    gamma.inputs["Gamma"].default_value = layer.adj_levels_gamma
    return True


def _hot_adj_cb(node_tree, layer, prop_name):
    lift = _find_tagged(node_tree, layer.name, "adj_cb_lift")
    gamma = _find_tagged(node_tree, layer.name, "adj_cb_gamma")
    gain = _find_tagged(node_tree, layer.name, "adj_cb_gain")
    if not (lift and gamma and gain):
        return False
    if _USE_NEW_MIX:
        _enabled_socket(lift.inputs, "B").default_value = (*layer.adj_lift, 1.0)
        _enabled_socket(gain.inputs, "B").default_value = (*layer.adj_gain, 1.0)
    else:
        lift.inputs["Color2"].default_value = (*layer.adj_lift, 1.0)
        gain.inputs["Color2"].default_value = (*layer.adj_gain, 1.0)
    g = layer.adj_gamma
    gamma.inputs["Gamma"].default_value = max(0.001, (g[0] + g[1] + g[2]) / 3.0)
    return True


def _hot_bump(node_tree, layer, prop_name):
    """Update a per-layer Bump node's Strength/Distance without rebuilding.

    Each use_bump layer now gets its own Bump node tagged with the layer name
    (see _build_bump_channel refactor). If the tagged node is missing — either
    the rebuild hasn't run yet or the layer has no bump — fall through to a
    full rebuild.
    """
    node = _find_tagged(node_tree, layer.name, "bump_node")
    if not node:
        return False
    node.inputs["Strength"].default_value = layer.bump_strength
    node.inputs["Distance"].default_value = layer.bump_distance
    return True


def _hot_fresnel(node_tree, layer, prop_name):
    fr = _find_tagged(node_tree, layer.name, "fresnel")
    if not fr:
        return False
    fr.inputs["IOR"].default_value = getattr(layer, 'fresnel_ior', 1.45)
    fstr = _find_tagged(node_tree, layer.name, "fresnel_str")
    if fstr:
        fstr.inputs[1].default_value = getattr(layer, 'fresnel_strength', 1.0)
    return True


def _hot_proc_fresnel_ior(node_tree, layer, prop_name):
    """Update the IOR of a Fresnel PROCEDURAL's tex node without rebuilding.
    The proc_type='FRESNEL' build path tags the Fresnel node as 'proc_tex'
    (shared tag with other procedural tex nodes), so a layer can have at
    most one Fresnel proc_tex. Update both color + scalar fac paths.
    """
    if getattr(layer, 'proc_type', '') != 'FRESNEL':
        return False
    ior = getattr(layer, 'proc_fresnel_ior', 1.45)
    nodes = _find_all_tagged(node_tree, layer.name, "proc_tex")
    found = False
    for n in nodes:
        if n.bl_idname == 'ShaderNodeFresnel' and "IOR" in n.inputs:
            n.inputs["IOR"].default_value = ior
            found = True
    return found  # False if no Fresnel proc_tex found → fall back to rebuild


def _hot_mask_fresnel_ior(node_tree, layer, prop_name):
    """Update the IOR of a Fresnel mask source node without rebuilding."""
    ior = getattr(layer, 'mask_fresnel_ior', 1.45)
    updated = False
    # Slot A
    fr_a = _find_tagged(node_tree, layer.name, "mask_fresnel_a")
    if fr_a:
        fr_a.inputs["IOR"].default_value = ior
        updated = True
    # Slot B
    fr_b = _find_tagged(node_tree, layer.name, "mask_fresnel_b")
    if fr_b:
        fr_b.inputs["IOR"].default_value = ior
        updated = True
    return updated


def _hot_mask_wireframe(node_tree, layer, prop_name):
    """Update shared Wireframe mask settings for mask slots A/B without rebuilding."""
    size = getattr(layer, 'mask_wireframe_size', 0.01)
    use_pixel_size = getattr(layer, 'mask_wireframe_use_pixel_size', True)
    updated = False
    for tag_name in ("mask_wireframe_a", "mask_wireframe_b"):
        wire = _find_tagged(node_tree, layer.name, tag_name)
        if not wire:
            continue
        try:
            wire.inputs["Size"].default_value = size
        except Exception:
            pass
        if hasattr(wire, "use_pixel_size"):
            wire.use_pixel_size = use_pixel_size
        updated = True
    return updated
def _hot_normal_strength(node_tree, layer, prop_name):
    """Update a per-layer NormalMap node's Strength without rebuilding."""
    node = _find_tagged(node_tree, layer.name, "normal_map_node")
    if not node:
        return False
    node.inputs["Strength"].default_value = getattr(layer, 'normal_strength', 1.0)
    return True


def _ensure_normal_mapping_node(node_tree, layer):
    """Return/create the Mapping node between a layer's normal UV and texture."""
    node = _find_tagged(node_tree, layer.name, "normal_mapping")
    if node:
        return node

    tex = _find_tagged(node_tree, layer.name, "normal_tex")
    if not tex:
        return None

    vec_input = tex.inputs.get("Vector")
    if vec_input is None:
        return None

    source_socket = None
    if vec_input.links:
        link = vec_input.links[0]
        if link.from_node.type == 'MAPPING':
            _tag(link.from_node, layer.name, "normal_mapping")
            return link.from_node
        source_socket = link.from_socket
        node_tree.links.remove(link)
    else:
        uv_node = _find_tagged(node_tree, layer.name, "normal_uv")
        if uv_node:
            source_socket = uv_node.outputs.get("UV")

    if source_socket is None:
        return None

    mapping = node_tree.nodes.new("ShaderNodeMapping")
    mapping.name = f"{TLM_PREFIX}normal_mapping_{_next_id()}"
    mapping.location = (tex.location.x - 150, tex.location.y)
    _tag(mapping, layer.name, "normal_mapping")
    node_tree.links.new(source_socket, mapping.inputs["Vector"])
    node_tree.links.new(mapping.outputs["Vector"], vec_input)
    return mapping


def _hot_normal_mapping(node_tree, layer, prop_name):
    """Update per-layer Normal Mapping node (tile scale + rotation).

    Normal layers always own a Mapping node, even at default values. That keeps
    tile/rotation sliders hot-updatable instead of rebuilding the whole graph
    when the value crosses the 1.0 / 0.0 defaults.
    """
    import math
    tile = getattr(layer, 'normal_tile_scale', 1.0)
    rot  = getattr(layer, 'normal_rotation', 0.0)
    node = _ensure_normal_mapping_node(node_tree, layer)
    if not node:
        return False
    node.inputs["Scale"].default_value = (tile, tile, 1.0)
    node.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(rot))
    return True


def _hot_paint_mapping(node_tree, layer, prop_name):
    """Update per-layer Paint Mapping node (Location/Rotation/Scale × XYZ).

    The Mapping node exists ONLY when at least one of the 9 values differs
    from default (loc=0, rot=0, scale=1). Crossing the threshold changes
    topology → fallback rebuild.
    """
    loc = (
        getattr(layer, 'paint_location_x', 0.0),
        getattr(layer, 'paint_location_y', 0.0),
        getattr(layer, 'paint_location_z', 0.0),
    )
    rot = (
        getattr(layer, 'paint_rotation_x', 0.0),
        getattr(layer, 'paint_rotation_y', 0.0),
        getattr(layer, 'paint_rotation_z', 0.0),
    )
    scl = (
        getattr(layer, 'paint_scale_x', 1.0),
        getattr(layer, 'paint_scale_y', 1.0),
        getattr(layer, 'paint_scale_z', 1.0),
    )
    needs = (
        any(abs(v) > 1e-6 for v in loc)
        or any(abs(v) > 1e-6 for v in rot)
        or any(abs(v - 1.0) > 1e-6 for v in scl)
    )
    nodes = _find_all_tagged(node_tree, layer.name, "paint_mapping")
    if needs != bool(nodes):
        return False  # topology change — fallback rebuild
    if not nodes:
        return True
    for n in nodes:
        n.inputs["Location"].default_value = loc
        n.inputs["Rotation"].default_value = rot
        n.inputs["Scale"].default_value    = scl
    return True


def _hot_mask_ao_distance(node_tree, layer, prop_name):
    """Update Ambient Occlusion Distance on mask slot A or B."""
    slot = "b" if prop_name.endswith("_b") else "a"
    node = _find_tagged(node_tree, layer.name, f"mask_ao_{slot}")
    if not node:
        return False
    node.inputs["Distance"].default_value = getattr(layer, prop_name, 0.5)
    return True


def _hot_mask_contrast(node_tree, layer, prop_name):
    """Update mask contrast Power exponent. Topology changes when crossing 0.5 → rebuild."""
    contrast = getattr(layer, 'mask_contrast', 0.5)
    node = _find_tagged(node_tree, layer.name, "mask_contrast")
    near_neutral = abs(contrast - 0.5) <= 1e-4
    if near_neutral:
        # Power node should NOT exist. If it does, rebuild to remove it.
        return not bool(node)
    # Non-neutral: Power node must exist to host the new exponent.
    if not node:
        return False
    if contrast < 0.5:
        exp = 0.25 + (contrast / 0.5) * 0.75
    else:
        exp = 1.0 + ((contrast - 0.5) / 0.5) * 3.0
    node.inputs[1].default_value = exp
    return True


def _hot_mask_softness(node_tree, layer, prop_name):
    """Update mask softness SMOOTHSTEP range. Topology changes when crossing 0 → rebuild."""
    softness = getattr(layer, 'mask_softness', 0.0)
    node = _find_tagged(node_tree, layer.name, "mask_soft")
    active = softness > 1e-4
    if active != bool(node):
        return False
    if not active:
        return True
    half = softness * 0.5
    node.inputs["From Min"].default_value = max(0.0, 0.5 - half)
    node.inputs["From Max"].default_value = min(1.0, 0.5 + half)
    return True


def _hot_mask_levels(node_tree, layer, prop_name):
    """Update mask Levels stack (In MapRange, Gamma Power, Out MapRange).

    Each of the 3 sub-nodes is created only when its values are non-default.
    We check that the current topology matches current values before hot-updating;
    otherwise fall back to rebuild.
    """
    if not getattr(layer, 'use_mask_levels', False):
        return False  # whole stack is gated — let rebuild handle on/off
    in_min  = layer.mask_levels_in_min
    in_max  = layer.mask_levels_in_max
    gamma   = layer.mask_levels_gamma
    out_min = layer.mask_levels_out_min
    out_max = layer.mask_levels_out_max

    should_have_in  = (in_max > in_min + 1e-4) and (in_min > 1e-4 or in_max < 1 - 1e-4)
    should_have_g   = abs(gamma - 1.0) > 1e-4
    should_have_out = out_min > 1e-4 or out_max < 1 - 1e-4

    lv_in  = _find_tagged(node_tree, layer.name, "mask_lv_in")
    lv_g   = _find_tagged(node_tree, layer.name, "mask_lv_gamma")
    lv_out = _find_tagged(node_tree, layer.name, "mask_lv_out")

    if bool(lv_in) != should_have_in:   return False
    if bool(lv_g) != should_have_g:     return False
    if bool(lv_out) != should_have_out: return False

    if lv_in:
        lv_in.inputs["From Min"].default_value = in_min
        lv_in.inputs["From Max"].default_value = in_max
    if lv_g:
        lv_g.inputs[1].default_value = 1.0 / max(0.05, gamma)
    if lv_out:
        lv_out.inputs["To Min"].default_value = out_min
        lv_out.inputs["To Max"].default_value = out_max
    return True


def _hot_mask_blur(node_tree, layer, prop_name):
    """Update the Blur input on the shared TLM_maskblur_<img> NodeGroup instance(s).

    The blur pipeline is one ShaderNodeGroup per IMAGE-source mask slot (A and/or
    B), tagged as ``mask_blur_group_a`` / ``mask_blur_group_b``. Writing directly
    to the Group's Blur input avoids rebuilding the 25-node kernel on every
    slider tick.

    Topology change detection: the Group instance exists only when
    ``mask_blur > 1e-4``. If the slider crosses zero in either direction, the
    mask-slot topology switches between plain image-tex and the Group variant
    → fall back to rebuild so the other branch is created / torn down.
    """
    new_blur = getattr(layer, 'mask_blur', 0.0)
    should_have_group = new_blur > 1e-4

    # Check both slots — either can have an IMAGE source using the blur group.
    nodes_a = _find_all_tagged(node_tree, layer.name, "mask_blur_group_a")
    nodes_b = _find_all_tagged(node_tree, layer.name, "mask_blur_group_b")
    all_groups = nodes_a + nodes_b

    # Determine which slots currently use an IMAGE source (the only source
    # that ever spawns a blur group). If use_mask itself is off, the whole
    # _apply_mask pipeline is skipped → no groups on either slot.
    use_mask = getattr(layer, 'use_mask', False)
    source_a = use_mask \
               and getattr(layer, 'mask_source', 'IMAGE') == 'IMAGE' \
               and bool(getattr(layer, 'mask_image_name', ""))
    source_b = use_mask \
               and getattr(layer, 'use_mask_b', False) \
               and getattr(layer, 'mask_source_b', 'IMAGE') == 'IMAGE' \
               and bool(getattr(layer, 'mask_image_name_b', ""))

    expected_a = should_have_group and source_a
    expected_b = should_have_group and source_b
    actual_a = bool(nodes_a)
    actual_b = bool(nodes_b)

    if expected_a != actual_a or expected_b != actual_b:
        return False  # topology change → rebuild

    if not all_groups:
        return True  # nothing to update, nothing expected → no-op OK

    for g in all_groups:
        try:
            g.inputs["Blur"].default_value = new_blur
        except (KeyError, AttributeError):
            return False  # Group instance in a bad state — fall back to rebuild
    return True


# ── Image-swap hot path ───────────────────────────────────────────────────────
# Maps image-name properties → (tag role to find the tex node, expected colorspace).
_IMAGE_HOT_MAP = {
    "image_name":              ("paint_tex",            "sRGB"),
    "roughness_image_name":    ("pbr_tex_roughness",    "Non-Color"),
    "metallic_image_name":     ("pbr_tex_metallic",     "Non-Color"),
    "transmission_image_name": ("pbr_tex_transmission", "Non-Color"),
    "emission_image_name":     ("pbr_tex_emission",     "sRGB"),
    "normal_image_name":       ("normal_tex",           "Non-Color"),
    "alpha_image_name":        ("pbr_tex_alpha",        "Non-Color"),
}


def _hot_image_swap(node_tree, layer, prop_name):
    """Swap the image on existing tex nodes for this layer+channel.

    Falls back to full rebuild when:
    - the property is not in our map (defensive)
    - the new image-name is empty (clearing → topology change, fill node needed)
    - the image datablock isn't found
    - no tagged tex node exists (first-time assignment after build → structural)
    - any matched node isn't TEX_IMAGE (unexpected node type → structural)
    """
    info = _IMAGE_HOT_MAP.get(prop_name)
    if not info:
        return False
    role, expected_cs = info
    new_name = getattr(layer, prop_name, "")
    if not new_name:
        return False
    new_img = bpy.data.images.get(new_name)
    if not new_img:
        return False
    nodes = _find_all_tagged(node_tree, layer.name, role)
    if not nodes:
        return False
    for n in nodes:
        if n.type != 'TEX_IMAGE':
            return False
    for n in nodes:
        n.image = new_img
        if expected_cs == "Non-Color" and new_img.colorspace_settings.name != "Non-Color":
            try:
                new_img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
    return True


# Dispatch table: property name -> handler function
_HOT_DISPATCH = {
    "opacity": _hot_opacity,
    "blend_mode": _hot_blend_mode,
    "fill_color": _hot_fill_color,
    "roughness_fill": _hot_scalar_fill,
    "metallic_fill": _hot_scalar_fill,
    "transmission_fill": _hot_scalar_fill,
    "emission_color": _hot_emission_color,
    "emission_strength": _hot_emission_strength,
    "proc_scale": _hot_proc_tex_input,
    "proc_detail": _hot_proc_tex_input,
    "proc_roughness_proc": _hot_proc_tex_input,
    "proc_distortion": _hot_proc_tex_input,
    "proc_magic_distortion": _hot_proc_tex_input,
    "proc_lacunarity": _hot_proc_tex_input,
    "proc_randomness": _hot_proc_tex_input,
    "proc_wave_detail_scale": _hot_proc_tex_input,
    "proc_wave_detail_roughness": _hot_proc_tex_input,
    "proc_wave_phase_offset": _hot_proc_tex_input,
    "proc_offset_x": _hot_proc_offset,
    "proc_offset_y": _hot_proc_offset,
    "proc_offset_z": _hot_proc_offset,
    "proc_rotation_x": _hot_proc_offset,
    "proc_rotation_y": _hot_proc_offset,
    "proc_rotation_z": _hot_proc_offset,
    "proc_mapping_scale_x": _hot_proc_offset,
    "proc_mapping_scale_y": _hot_proc_offset,
    "proc_mapping_scale_z": _hot_proc_offset,
    "proc_mapping_type": _hot_proc_offset,
    "proc_brick_offset": _hot_proc_brick,
    "proc_brick_offset_freq": _hot_proc_brick,
    "proc_brick_squash": _hot_proc_brick,
    "proc_brick_squash_freq": _hot_proc_brick,
    "proc_brick_mortar_size": _hot_proc_brick,
    "proc_brick_mortar_smooth": _hot_proc_brick,
    "proc_brick_bias": _hot_proc_brick,
    "proc_brick_width": _hot_proc_brick,
    "proc_brick_row_height": _hot_proc_brick,
    "proc_dots_radius": _hot_proc_dots,
    "proc_dots_softness": _hot_proc_dots,
    "proc_cracks_width": _hot_proc_cracks,
    "proc_cracks_sharpness": _hot_proc_cracks,
    "proc_ridged_offset": _hot_proc_ridged,
    "proc_ridged_gain": _hot_proc_ridged,
    "proc_gabor_frequency": _hot_proc_gabor,
    "proc_gabor_anisotropy": _hot_proc_gabor,
    "proc_gabor_orientation": _hot_proc_gabor,
    "proc_stripe_width":     _hot_proc_stripe,
    "proc_stripe_sharpness": _hot_proc_stripe,
    "proc_hex_edge_width":   _hot_proc_hex,
    "proc_color1": _hot_proc_color,
    "proc_color2": _hot_proc_color,
    "proc_color3": _hot_proc_color,
    "proc_color3_position": _hot_proc_color,
    "proc_contrast": _hot_proc_color,
    "proc_ramp_center": _hot_proc_color,
    "proc_color1_position": _hot_proc_color,
    "proc_color2_position": _hot_proc_color,
    "proc_color_ramp_mode": _hot_proc_color,
    "proc_color_ramp_interpolation": _hot_proc_color,
    "proc_vector_distortion": _hot_vector_distortion,
    "proc_marble_distortion": _hot_marble_distortion,
    "adj_hue": _hot_adj_hue_sat,
    "adj_saturation": _hot_adj_hue_sat,
    "adj_value": _hot_adj_hue_sat,
    "adj_brightness": _hot_adj_bc,
    "adj_contrast": _hot_adj_bc,
    "adj_in_min": _hot_adj_levels,
    "adj_in_max": _hot_adj_levels,
    "adj_levels_gamma": _hot_adj_levels,
    "adj_out_min": _hot_adj_levels,
    "adj_out_max": _hot_adj_levels,
    "adj_lift": _hot_adj_cb,
    "adj_gamma": _hot_adj_cb,
    "adj_gain": _hot_adj_cb,
    "bump_strength": _hot_bump,
    "bump_distance": _hot_bump,
    "fresnel_ior": _hot_fresnel,
    "fresnel_strength": _hot_fresnel,
    "mask_fresnel_ior": _hot_mask_fresnel_ior,
    "proc_fresnel_ior": _hot_proc_fresnel_ior,
    "mask_wireframe_size": _hot_mask_wireframe,
    "mask_wireframe_use_pixel_size": _hot_mask_wireframe,
    "normal_strength": _hot_normal_strength,
    "normal_tile_scale": _hot_normal_mapping,
    "normal_rotation": _hot_normal_mapping,
    "paint_location_x": _hot_paint_mapping,
    "paint_location_y": _hot_paint_mapping,
    "paint_location_z": _hot_paint_mapping,
    "paint_rotation_x": _hot_paint_mapping,
    "paint_rotation_y": _hot_paint_mapping,
    "paint_rotation_z": _hot_paint_mapping,
    "paint_scale_x":    _hot_paint_mapping,
    "paint_scale_y":    _hot_paint_mapping,
    "paint_scale_z":    _hot_paint_mapping,
    "mask_ao_distance": _hot_mask_ao_distance,
    "mask_ao_distance_b": _hot_mask_ao_distance,
    "mask_contrast": _hot_mask_contrast,
    "mask_softness": _hot_mask_softness,
    "mask_levels_in_min": _hot_mask_levels,
    "mask_levels_in_max": _hot_mask_levels,
    "mask_levels_gamma": _hot_mask_levels,
    "mask_levels_out_min": _hot_mask_levels,
    "mask_levels_out_max": _hot_mask_levels,
    "mask_blur": _hot_mask_blur,
    # Image swap (avoids full rebuild on PBR / paint / normal texture change)
    "image_name":              _hot_image_swap,
    "roughness_image_name":    _hot_image_swap,
    "metallic_image_name":     _hot_image_swap,
    "transmission_image_name": _hot_image_swap,
    "emission_image_name":     _hot_image_swap,
    "normal_image_name":       _hot_image_swap,
    "alpha_image_name":        _hot_image_swap,
    "alpha_fill":              _hot_scalar_fill,
}


_hot_updating = False


def performance_enabled(material):
    """True when the material should collect lightweight timing metrics."""
    try:
        return bool(material and getattr(material.tlm, "performance_debug", False))
    except Exception:
        return False


def record_performance(material, **values):
    """Store performance metrics without affecting normal rebuild behavior."""
    if not performance_enabled(material):
        return
    tlm = material.tlm
    for attr, value in values.items():
        try:
            setattr(tlm, attr, value)
        except Exception:
            pass


def _material_mesh_stats(material):
    """Count mesh users of a material. Runs only while performance debug is on."""
    objects = 0
    vertices = 0
    faces = 0
    try:
        for obj in bpy.data.objects:
            if obj.type != 'MESH':
                continue
            if not any(slot.material == material for slot in obj.material_slots):
                continue
            objects += 1
            mesh = obj.data
            vertices += len(mesh.vertices)
            faces += len(mesh.polygons)
    except Exception:
        return 0, 0, 0
    return vertices, faces, objects


def _record_rebuild_performance(material, started_at, node_tree, visible_layers):
    if started_at is None:
        return
    vertices, faces, objects = _material_mesh_stats(material)
    record_performance(
        material,
        perf_last_rebuild_ms=(_time.perf_counter() - started_at) * 1000.0,
        perf_last_node_count=len(node_tree.nodes) if node_tree else 0,
        perf_last_layer_count=len(material.tlm.layers),
        perf_last_visible_layer_count=len(visible_layers or []),
        perf_last_mesh_vertices=vertices,
        perf_last_mesh_faces=faces,
        perf_last_mesh_objects=objects,
    )

def hot_update_property(material, layer, prop_name):
    """Attempt to hot-update a single property without full rebuild.
    Returns True on success, False if fallback rebuild is needed."""
    global _hot_updating
    if _hot_updating:
        return True  # re-entrant call from depsgraph — suppress
    node_tree = material.node_tree
    if not node_tree:
        return False
    handler = _HOT_DISPATCH.get(prop_name)
    if not handler:
        return False
    perf_started = _time.perf_counter() if performance_enabled(material) else None
    ok = False
    try:
        _hot_updating = True
        ok = handler(node_tree, layer, prop_name)
        return ok
    except Exception:
        import traceback
        traceback.print_exc()
        return False
    finally:
        if perf_started is not None:
            record_performance(
                material,
                perf_last_hot_update_ms=(_time.perf_counter() - perf_started) * 1000.0,
                perf_last_hot_update_prop=prop_name,
                perf_last_hot_update_ok=bool(ok),
            )
        _hot_updating = False


