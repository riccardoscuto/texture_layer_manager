"""Adjustment layer builders.

ADJUSTMENT layers don't add their own colour to the stack — they remap
the OUTPUT of layers below them via Hue/Saturation, Brightness/Contrast,
Levels (input/output remap + gamma), Color Balance, or Curves. They
operate on the cumulative output of whatever channel they target.

Public entry points:
  * ``_apply_adjustment(node_tree, layer, current_output, x, y,
    channel_id="base_color")`` — wraps the current channel output in
    the appropriate adjustment node + opacity blend.
  * ``_apply_adjustment_scalar(...)`` — same for single-float channels
    (roughness / metallic / etc.) that drive a scalar pipeline.

Internal helpers:
  * _wrap_adjustment_opacity — Mix the adjusted result with the
    original via the layer's opacity slider.
  * _adj_target_channel — Resolve which channel the layer targets,
    honouring AUTO/UNUSED routing.
  * _adj_supports_channel — Lookup table of which adj_type supports
    each channel (e.g. Hue/Sat only makes sense on colour).
"""

import bpy

from . import (
    _next_id, _tag,
    _new_mix, _new_mix_scalar,
    _factor_socket,
    _a_socket, _b_socket, _result_socket,
    _a_socket_scalar, _b_socket_scalar, _result_socket_scalar,
    _enabled_socket, _USE_NEW_MIX,
)
from . import TLM_PREFIX  # noqa: F401


__all__ = [
    '_wrap_adjustment_opacity',
    '_ADJ_OUTPUT_TO_CHANNEL',
    '_ADJ_SCALAR_COMPATIBLE',
    '_adj_target_channel',
    '_adj_supports_channel',
    '_apply_adjustment_scalar',
    '_apply_adjustment',
]

def _wrap_adjustment_opacity(node_tree, layer, original_output, adjusted_output, x, y,
                             channel_id="base_color"):
    """Wrap a colour-channel adjustment chain with an opacity-driven Mix.

    Mix(A=original, B=adjusted, factor=opacity) makes layer.opacity behave
    uniformly as "strength" across every adjustment type:
      - opacity = 0 → bypass (output equals input)
      - opacity = 1 → full effect
      - 0 < opacity < 1 → linear blend
    The mix is tagged as ``opacity_target_<channel_id>`` so _hot_opacity
    finds it via the standard per-channel iteration.
    """
    mix = node_tree.nodes.new("ShaderNodeMix")
    mix.data_type = 'RGBA'
    mix.blend_type = 'MIX'
    mix.location = (x + 600, y)
    mix.name = f"{TLM_PREFIX}adj_opacity_{_next_id()}"
    _factor_socket(mix).default_value = layer.opacity
    node_tree.links.new(original_output, _a_socket(mix))
    node_tree.links.new(adjusted_output, _b_socket(mix))
    _tag(mix, layer.name, f"opacity_target_{channel_id}", opacity_input_idx=-1)
    return _result_socket(mix)


# Map output_channel ('BASE_COLOR' / 'ROUGHNESS' / 'METALLIC' / 'ALPHA')
# to the channel_id used inside the build pipeline. Adjustment layers
# default to 'base_color' for backward compatibility — older .blends /
# presets had no concept of "adjustment on roughness", so a missing
# output_channel still routes to base_color.
_ADJ_OUTPUT_TO_CHANNEL = {
    'BASE_COLOR': 'base_color',
    'ROUGHNESS':  'roughness',
    'METALLIC':   'metallic',
    'ALPHA':      'alpha',
}

# adj_type compatibility per channel kind.
# Colour channels (base_color / emission) accept everything.
# Scalar channels (roughness / metallic / alpha / transmission) only
# accept the math-on-a-float adjustment types — HUE_SAT and
# COLOR_BALANCE are HSV / RGB-Lift/Gamma/Gain, which are meaningless
# on a single-channel value.
_ADJ_SCALAR_COMPATIBLE = {'BRIGHT_CONTRAST', 'LEVELS'}


def _adj_target_channel(layer):
    """Return the channel_id this adjustment layer is meant to modify."""
    out_ch = getattr(layer, 'output_channel', 'BASE_COLOR')
    return _ADJ_OUTPUT_TO_CHANNEL.get(out_ch, 'base_color')


def _adj_supports_channel(adj_type, channel_id):
    """True when this (adj_type, channel_id) pair has a meaningful effect."""
    if channel_id in ('base_color', 'emission'):
        return True  # colour channels accept every adj type
    return adj_type in _ADJ_SCALAR_COMPATIBLE


def _apply_adjustment_scalar(node_tree, layer, current_output, x, y, channel_id):
    """Adjustment for SCALAR channels (roughness/metallic/transmission/alpha).

    Color-only adjustments (HUE_SAT, COLOR_BALANCE) pass through
    unchanged — their HSV / RGB math has no scalar interpretation. The
    scalar-friendly types (BRIGHT_CONTRAST, LEVELS) build Math /
    MapRange node chains, then everything is wrapped in a Float Mix
    (input, adjusted, factor=opacity) so opacity behaves uniformly as
    "strength", mirroring _wrap_adjustment_opacity on the colour path.
    """
    adj = layer.adj_type

    if adj == 'BRIGHT_CONTRAST':
        # Match Blender's ShaderNodeBrightContrast formula:
        #   out = clamp(brightness + (1 + contrast) * (in - 0.5) + 0.5, 0, 1)
        sub = node_tree.nodes.new("ShaderNodeMath")
        sub.operation = 'SUBTRACT'
        sub.inputs[1].default_value = 0.5
        sub.location = (x, y)
        sub.name = f"{TLM_PREFIX}adj_bc_sub_{_next_id()}"
        node_tree.links.new(current_output, sub.inputs[0])

        mul = node_tree.nodes.new("ShaderNodeMath")
        mul.operation = 'MULTIPLY'
        mul.inputs[1].default_value = 1.0 + layer.adj_contrast
        mul.location = (x + 200, y)
        mul.name = f"{TLM_PREFIX}adj_bc_mul_{_next_id()}"
        node_tree.links.new(sub.outputs["Value"], mul.inputs[0])

        add = node_tree.nodes.new("ShaderNodeMath")
        add.operation = 'ADD'
        add.inputs[1].default_value = 0.5 + layer.adj_brightness
        add.use_clamp = True
        add.location = (x + 400, y)
        add.name = f"{TLM_PREFIX}adj_bc_add_{_next_id()}"
        _tag(add, layer.name, "adj_bc")
        node_tree.links.new(mul.outputs["Value"], add.inputs[0])
        adjusted = add.outputs["Value"]

    elif adj == 'LEVELS':
        mr_in = node_tree.nodes.new("ShaderNodeMapRange")
        mr_in.clamp = True
        mr_in.inputs["From Min"].default_value = layer.adj_in_min
        mr_in.inputs["From Max"].default_value = layer.adj_in_max
        mr_in.inputs["To Min"].default_value = 0.0
        mr_in.inputs["To Max"].default_value = 1.0
        mr_in.location = (x, y)
        mr_in.name = f"{TLM_PREFIX}adj_lvl_in_s_{_next_id()}"
        _tag(mr_in, layer.name, "adj_lvl_in")
        node_tree.links.new(current_output, mr_in.inputs["Value"])

        gamma_n = node_tree.nodes.new("ShaderNodeMath")
        gamma_n.operation = 'POWER'
        gamma_n.use_clamp = True
        # Match the colour LEVELS path which drives ShaderNodeGamma
        # with adj_levels_gamma directly (out = pow(in, gamma)).
        gamma_n.inputs[1].default_value = max(0.001, layer.adj_levels_gamma)
        gamma_n.location = (x + 200, y)
        gamma_n.name = f"{TLM_PREFIX}adj_lvl_g_s_{_next_id()}"
        _tag(gamma_n, layer.name, "adj_lvl_gamma")
        node_tree.links.new(mr_in.outputs["Result"], gamma_n.inputs[0])

        mr_out = node_tree.nodes.new("ShaderNodeMapRange")
        mr_out.clamp = True
        mr_out.inputs["From Min"].default_value = 0.0
        mr_out.inputs["From Max"].default_value = 1.0
        mr_out.inputs["To Min"].default_value = layer.adj_out_min
        mr_out.inputs["To Max"].default_value = layer.adj_out_max
        mr_out.location = (x + 400, y)
        mr_out.name = f"{TLM_PREFIX}adj_lvl_out_s_{_next_id()}"
        _tag(mr_out, layer.name, "adj_lvl_out")
        node_tree.links.new(gamma_n.outputs["Value"], mr_out.inputs["Value"])
        adjusted = mr_out.outputs["Result"]

    else:
        # HUE_SAT, COLOR_BALANCE: no scalar interpretation. Pass-through.
        return current_output

    # Wrap with Float Mix (opacity = strength)
    mix = node_tree.nodes.new("ShaderNodeMix")
    mix.data_type = 'FLOAT'
    mix.blend_type = 'MIX'
    mix.inputs["Factor"].default_value = layer.opacity
    mix.location = (x + 600, y)
    mix.name = f"{TLM_PREFIX}adj_opacity_s_{_next_id()}"
    _tag(mix, layer.name, f"opacity_target_{channel_id}", opacity_input_idx=-1)
    node_tree.links.new(current_output, _a_socket_scalar(mix))
    node_tree.links.new(adjusted, _b_socket_scalar(mix))
    return _result_socket_scalar(mix)


def _apply_adjustment(node_tree, layer, current_output, x, y, channel_id="base_color"):
    adj = layer.adj_type

    if adj == 'HUE_SAT':
        node = node_tree.nodes.new("ShaderNodeHueSaturation")
        node.name = f"{TLM_PREFIX}adj_huesat_{_next_id()}"
        node.label = "Hue/Saturation"
        node.location = (x, y)
        _tag(node, layer.name, "adj_hue_sat")
        node.inputs["Hue"].default_value        = layer.adj_hue
        node.inputs["Saturation"].default_value = layer.adj_saturation
        node.inputs["Value"].default_value      = layer.adj_value
        # Fac stays at full strength here — the wrapping Mix below
        # provides the opacity-as-strength behaviour uniformly.
        node.inputs["Fac"].default_value        = 1.0
        node_tree.links.new(current_output, node.inputs["Color"])
        return _wrap_adjustment_opacity(node_tree, layer,
                                        current_output, node.outputs["Color"], x, y,
                                        channel_id=channel_id)

    elif adj == 'BRIGHT_CONTRAST':
        node = node_tree.nodes.new("ShaderNodeBrightContrast")
        node.name = f"{TLM_PREFIX}adj_bc_{_next_id()}"
        node.label = "Brightness/Contrast"
        node.location = (x, y)
        _tag(node, layer.name, "adj_bc")
        node.inputs["Bright"].default_value   = layer.adj_brightness
        node.inputs["Contrast"].default_value = layer.adj_contrast
        node_tree.links.new(current_output, node.inputs["Color"])
        return _wrap_adjustment_opacity(node_tree, layer,
                                        current_output, node.outputs["Color"], x, y,
                                        channel_id=channel_id)

    elif adj == 'LEVELS':
        mr_in = node_tree.nodes.new("ShaderNodeMapRange")
        mr_in.name = f"{TLM_PREFIX}adj_lvl_in_{_next_id()}"
        mr_in.label = "Levels In"
        mr_in.location = (x, y)
        mr_in.clamp = True
        _tag(mr_in, layer.name, "adj_lvl_in")
        if hasattr(mr_in, 'data_type'):
            mr_in.data_type = 'FLOAT_VECTOR'
            mr_in.inputs["From Min"].default_value = (layer.adj_in_min,) * 3
            mr_in.inputs["From Max"].default_value = (layer.adj_in_max,) * 3
            mr_in.inputs["To Min"].default_value   = (0.0, 0.0, 0.0)
            mr_in.inputs["To Max"].default_value   = (1.0, 1.0, 1.0)
            node_tree.links.new(current_output, mr_in.inputs["Vector"])
            mr_in_out = mr_in.outputs["Vector"]
        else:
            mr_in.inputs["From Min"].default_value = layer.adj_in_min
            mr_in.inputs["From Max"].default_value = layer.adj_in_max
            mr_in.inputs["To Min"].default_value   = 0.0
            mr_in.inputs["To Max"].default_value   = 1.0
            node_tree.links.new(current_output, mr_in.inputs["Value"])
            mr_in_out = mr_in.outputs["Result"]

        gamma_node = node_tree.nodes.new("ShaderNodeGamma")
        gamma_node.name = f"{TLM_PREFIX}adj_gamma_{_next_id()}"
        gamma_node.location = (x + 200, y)
        _tag(gamma_node, layer.name, "adj_lvl_gamma")
        # FIX: use adj_levels_gamma (renamed from adj_gamma which was overwritten
        # by the Color Balance FloatVectorProperty in properties.py)
        gamma_node.inputs["Gamma"].default_value = layer.adj_levels_gamma
        node_tree.links.new(mr_in_out, gamma_node.inputs["Color"])

        mr_out = node_tree.nodes.new("ShaderNodeMapRange")
        mr_out.name = f"{TLM_PREFIX}adj_lvl_out_{_next_id()}"
        mr_out.location = (x + 400, y)
        mr_out.clamp = True
        _tag(mr_out, layer.name, "adj_lvl_out")
        if hasattr(mr_out, 'data_type'):
            mr_out.data_type = 'FLOAT_VECTOR'
            mr_out.inputs["From Min"].default_value = (0.0, 0.0, 0.0)
            mr_out.inputs["From Max"].default_value = (1.0, 1.0, 1.0)
            mr_out.inputs["To Min"].default_value   = (layer.adj_out_min,) * 3
            mr_out.inputs["To Max"].default_value   = (layer.adj_out_max,) * 3
            node_tree.links.new(gamma_node.outputs["Color"], mr_out.inputs["Vector"])
            adjusted = mr_out.outputs["Vector"]
        else:
            mr_out.inputs["From Min"].default_value = 0.0
            mr_out.inputs["From Max"].default_value = 1.0
            mr_out.inputs["To Min"].default_value   = layer.adj_out_min
            mr_out.inputs["To Max"].default_value   = layer.adj_out_max
            node_tree.links.new(gamma_node.outputs["Color"], mr_out.inputs["Value"])
            adjusted = mr_out.outputs["Result"]
        return _wrap_adjustment_opacity(node_tree, layer,
                                        current_output, adjusted, x, y,
                                        channel_id=channel_id)

    elif adj == 'COLOR_BALANCE':
        # Lift/Gamma/Gain implemented as:
        # output = gain * (color * lift)^(1/gamma)
        # Using Blender's Mix + Gamma + Math nodes

        # Step 1: Apply Lift (multiply)
        lift_node = node_tree.nodes.new("ShaderNodeMixRGB") if not _USE_NEW_MIX else node_tree.nodes.new("ShaderNodeMix")
        lift_node.name = f"{TLM_PREFIX}adj_lift_{_next_id()}"
        lift_node.label = "Lift"
        lift_node.location = (x, y)
        _tag(lift_node, layer.name, "adj_cb_lift")
        if _USE_NEW_MIX:
            lift_node.data_type = 'RGBA'
            lift_node.blend_type = 'MULTIPLY'
            lift_node.inputs["Factor"].default_value = 1.0
            _enabled_socket(lift_node.inputs, "B").default_value = (*layer.adj_lift, 1.0)
            node_tree.links.new(current_output, _enabled_socket(lift_node.inputs, "A"))
            lift_out = _enabled_socket(lift_node.outputs, "Result")
        else:
            lift_node.blend_type = 'MULTIPLY'
            lift_node.inputs["Fac"].default_value = 1.0
            lift_node.inputs["Color2"].default_value = (*layer.adj_lift, 1.0)
            node_tree.links.new(current_output, lift_node.inputs["Color1"])
            lift_out = lift_node.outputs["Color"]

        # Step 2: Apply Gamma (per-channel via RGB Curves approximation using HueSat Value)
        gamma_node = node_tree.nodes.new("ShaderNodeGamma")
        gamma_node.name = f"{TLM_PREFIX}adj_gamma_cb_{_next_id()}"
        gamma_node.label = "Gamma"
        gamma_node.location = (x + 220, y)
        _tag(gamma_node, layer.name, "adj_cb_gamma")
        # Use average of gamma RGB as scalar gamma
        g = layer.adj_gamma
        gamma_val = (g[0] + g[1] + g[2]) / 3.0
        gamma_node.inputs["Gamma"].default_value = max(0.001, gamma_val)
        node_tree.links.new(lift_out, gamma_node.inputs["Color"])

        # Step 3: Apply Gain (multiply again)
        gain_node = node_tree.nodes.new("ShaderNodeMixRGB") if not _USE_NEW_MIX else node_tree.nodes.new("ShaderNodeMix")
        gain_node.name = f"{TLM_PREFIX}adj_gain_{_next_id()}"
        gain_node.label = "Gain"
        gain_node.location = (x + 440, y)
        _tag(gain_node, layer.name, "adj_cb_gain")
        if _USE_NEW_MIX:
            gain_node.data_type = 'RGBA'
            gain_node.blend_type = 'MULTIPLY'
            gain_node.inputs["Factor"].default_value = 1.0
            _enabled_socket(gain_node.inputs, "B").default_value = (*layer.adj_gain, 1.0)
            node_tree.links.new(gamma_node.outputs["Color"], _enabled_socket(gain_node.inputs, "A"))
            adjusted = _enabled_socket(gain_node.outputs, "Result")
        else:
            gain_node.blend_type = 'MULTIPLY'
            gain_node.inputs["Fac"].default_value = 1.0
            gain_node.inputs["Color2"].default_value = (*layer.adj_gain, 1.0)
            node_tree.links.new(gamma_node.outputs["Color"], gain_node.inputs["Color1"])
            adjusted = gain_node.outputs["Color"]
        return _wrap_adjustment_opacity(node_tree, layer,
                                        current_output, adjusted, x, y,
                                        channel_id=channel_id)

    return current_output


# ── Group compositing ─────────────────────────────────────────────────────────

