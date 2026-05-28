"""Channel builders: per-output-channel shader graph construction.

A "channel" is a TLM output target (base_color / roughness / metallic /
normal / emission / transmission / alpha / bump / displacement). Each
channel has its own builder that walks the visible layers, dispatches
to ``_build_procedural_node`` / ``_build_proc_fac_node`` / image-texture
loaders, and composites them via ``_set_factor``.

Public entry points:
  * ``_build_channel(...)`` — generic single-channel builder.
  * ``_build_base_color(...)`` — special-case base colour (handles
    PaintAlpha + ALPHA-channel branching).
  * ``_build_normal_channel(...)`` — combines per-layer Normal maps
    plus the bump-derived normal output.
  * ``_build_bump_channel(...)`` — cumulative ADD of per-layer heights
    feeding a single Bump node (or chain).
  * ``_build_displacement_channel(...)`` — same pattern but feeding
    ShaderNodeDisplacement → Material Output.Displacement.

Supporting helpers in this module:
  * Layer positioning: _layer_width, _layer_positions, _layer_x_positions
  * Compositing: _set_factor (per-mix factor wiring),
    _composite_layer_list, link helpers
  * Predicates: _layer_contributes_to, _channel_used,
    _layer_has_first_layer_modulator, _scalar_channel_default
"""

import bpy

# Late imports — parent __init__ defines these before doing
# ``from .channels import *`` at end of body.
from . import (
    _next_id, _tag, _retag_frame_owner,
    _factor_socket, _a_socket, _b_socket, _result_socket,
    _a_socket_scalar, _b_socket_scalar, _result_socket_scalar,
    _enabled_socket,
    _new_fill, _new_value, _new_mix, _new_mix_scalar, _new_mix_vector,
    _new_alpha_math_composite, _new_img_tex,
    _effective_blend_mode,
    _adj_target_channel, _apply_adjustment, _apply_adjustment_scalar,
    _assign_layer_frames,
    # From procedurals.py (loaded BEFORE channels in __init__.py order)
    _build_procedural_node, _build_proc_fac_node,
    _build_fresnel_mask, _build_emission_selector, _voronoi_fac,
    _inject_coord_normalization, _inject_coord_transform,
    _inject_vector_distortion,
    _proc_mapping_scale, _apply_mapping_settings,
    # From masks.py (loaded BEFORE procedurals)
    _apply_mask,
)
from . import TLM_PREFIX  # noqa: F401


__all__ = [
    '_layer_width',
    '_layer_positions',
    '_layer_x_positions',
    '_build_channel',
    '_set_factor',
    '_composite_layer_list',
    '_link_to_bsdf',
    '_add_channel_reroute',
    '_link_channel_to_bsdf',
    '_build_base_color',
    '_layer_has_first_layer_modulator',
    '_scalar_channel_default',
    '_make_baseline_normal',
    '_build_normal_channel',
    '_build_bump_channel',
    '_build_displacement_channel',
    '_layer_contributes_to',
    '_channel_used',
    # Lookup tables / constants that live at module level
    '_BASE_LAYER_WIDTH',
    '_PROC_LAYER_WIDTH',
    '_CHANNEL_LAYOUT_ORDER',
    '_CHANNEL_Y_GAP',
    '_CHANNEL_ROUTE_OFFSET',
    '_LAYER_COLUMN_GAP',
    '_OUTPUT_CHANNEL_TO_TARGET',
    '_CHANNEL_USE_FLAG',
]



_BASE_LAYER_WIDTH = {
    # Width = full span of sub-nodes + padding. These are intentionally tight:
    # the graph should keep the lane style without leaving giant blank gaps.
    'PAINT':        900,
    'FILL':         720,
    'ADJUSTMENT':   820,
    'PROCEDURAL':   980,
    'REFERENCE':    980,
    'GROUP':        900,
}

_PROC_LAYER_WIDTH = {
    # Most procedural types are a compact tex -> ramp chain.  Only the
    # hand-built math topologies need extra horizontal room.
    'GRADIENT':     900,
    'WHITE_NOISE':  900,
    'CHECKER':      940,
    'MAGIC':        980,
    'NOISE':       1020,
    'VORONOI':     1020,
    'WAVE':        1020,
    'MUSGRAVE':    1020,
    'BRICK':       1120,
    'DOTS':        1120,
    'CRACKS':      1120,
    'GABOR':       1120,
    'STRIPES':     1180,
    'HEX_GRID':    1180,
    'RIDGED':      1260,
    'MARBLE':      1360,
}


_CHANNEL_LAYOUT_ORDER = (
    'base_color',
    'roughness',
    'metallic',
    'normal',
    'emission',
    'transmission',
    'alpha',
    'bump',
)
_CHANNEL_Y_GAP = 560
_CHANNEL_ROUTE_OFFSET = 160
_LAYER_COLUMN_GAP = 90


def _layer_width(layer):
    """Estimate the horizontal space a generated layer block needs."""
    layer_type = getattr(layer, 'layer_type', '')
    width = _BASE_LAYER_WIDTH.get(layer_type, 1200)
    if layer_type == 'PROCEDURAL':
        width = _PROC_LAYER_WIDTH.get(getattr(layer, 'proc_type', ''), width)

    if getattr(layer, 'use_mask', False):
        width = max(width, 1180)
        source_a = getattr(layer, 'mask_source', 'IMAGE')
        source_b = getattr(layer, 'mask_source_b', 'IMAGE')
        if (source_a in {'EDGE_WEAR', 'DIRT', 'CURVATURE_SMART'}
                or (getattr(layer, 'use_mask_b', False)
                    and source_b in {'EDGE_WEAR', 'DIRT', 'CURVATURE_SMART'})):
            width = max(width, 1500)
        if getattr(layer, 'use_mask_b', False):
            width = max(width, 1320)

    if layer_type == 'PROCEDURAL':
        if getattr(layer, 'proc_vector_distortion', 0.0) > 1e-4:
            width = max(width, 1220)
        if getattr(layer, 'use_emission', False):
            width = max(width, 1260)
        if getattr(layer, 'emission_selector_type', 'NONE') != 'NONE':
            width = max(width, 1460)
    return width + _LAYER_COLUMN_GAP


def _layer_positions(layers, x0, y0):
    """Return left-to-right layer positions for readable shader-editor graphs.

    Keep the stack flowing left-to-right and use channel lanes top-to-bottom.
    Width estimates are deliberately compact so many layers stay readable
    without collapsing into the same area or leaving excessive empty space.
    """
    positions = []
    x = x0
    for layer in layers:
        positions.append((x, y0))
        x += _layer_width(layer)
    return positions


def _layer_x_positions(layers, x0):
    """Legacy helper — returns just x positions (used by end_x calculation)."""
    return [pos[0] for pos in _layer_positions(layers, x0, 0)]


# ── Per-channel composite builder ─────────────────────────────────────────────

def _build_channel(node_tree, layers, channel_id, uv_map, x0, y_base, x_step):
    """
    Build a compositing chain for one PBR channel.
    Returns the final output socket, or None if no layer contributes.

    channel_id: 'base_color' | 'roughness' | 'metallic' | 'emission' | 'transmission'
    NOTE: 'normal' is handled by _build_normal_channel() instead.
    """
    is_scalar = channel_id in ('roughness', 'metallic', 'transmission', 'alpha')
    is_emission = channel_id == 'emission'

    # Map channel_id → attribute names on TLM_LayerItem
    img_attr  = {
        'base_color':   'image_name',
        'roughness':    'roughness_image_name',
        'metallic':     'metallic_image_name',
        'normal':       'normal_image_name',
        'emission':     'emission_image_name',
        'transmission': 'transmission_image_name',
        'alpha':        'alpha_image_name',
    }[channel_id]

    flag_attr = {
        'base_color':   None,      # base color is always enabled
        'roughness':    'use_roughness',
        'metallic':     'use_metallic',
        'normal':       'use_normal',
        'emission':     'use_emission',
        'transmission': 'use_transmission',
        'alpha':        'use_alpha',
    }[channel_id]

    current = None
    prev_alpha = None
    positions = _layer_positions(layers, x0, y_base)

    for i, layer in enumerate(layers):
        x, y = positions[i]

        # Adjustment layers operate on a single target channel, controlled
        # by output_channel ('BASE_COLOR' / 'ROUGHNESS' / 'METALLIC' /
        # 'ALPHA'). For BASE_COLOR they go through _apply_adjustment
        # (Color sockets); for the scalar targets they go through
        # _apply_adjustment_scalar (Float sockets, with HUE_SAT and
        # COLOR_BALANCE silently passing through since HSV / RGB ops
        # have no scalar interpretation).
        if layer.layer_type == "ADJUSTMENT":
            target = _adj_target_channel(layer)
            if channel_id == target and current is not None:
                if target == 'base_color':
                    current = _apply_adjustment(node_tree, layer, current, x, y,
                                                channel_id=channel_id)
                else:
                    current = _apply_adjustment_scalar(node_tree, layer, current,
                                                       x, y, channel_id)
            continue

        # ── Reference layer: reuse another layer's pattern output ─────────
        # Pattern comes from the referenced layer, blending from this layer.
        # Respects this layer's channel flags, blend_mode, opacity, mask,
        # fill values, and branching overrides — only the raw PATTERN
        # is borrowed from the referenced layer.
        if layer.layer_type == "REFERENCE":
            # contribution gate (use_<channel> vs output_channel routing) is
            # already enforced by _layer_contributes_to at the top of the loop.
            ref_name = getattr(layer, 'reference_layer_name', '')
            ref_layer = next((l for l in layers if l.name == ref_name and l != layer), None)
            if ref_layer is None or ref_layer.layer_type == "REFERENCE":
                continue  # invalid or cyclic

            # Extract pattern color from the referenced layer.
            # Snapshot node names before the copy is built so we can retag the
            # newly-created nodes with `tlm_frame_owner = layer.name` — this
            # makes frame grouping place them under the REFERENCE's own frame
            # instead of merging them with the source's frame.
            # try/finally ensures retag happens even when a branch `continue`s.
            _pre_ref_names = {n.name for n in node_tree.nodes}
            try:
                if ref_layer.layer_type == "PROCEDURAL":
                    ref_color_out, ref_alpha_out = _build_procedural_node(
                        node_tree, ref_layer, uv_map, x, y
                    )
                elif ref_layer.layer_type == "PAINT" and ref_layer.image:
                    tex = _new_img_tex(node_tree, ref_layer.image, uv_map, x, y, layer=ref_layer)
                    # _new_img_tex doesn't tag — stamp the tex node so it's
                    # findable by _assign_layer_frames.
                    _tag(tex, ref_layer.name, "ref_tex")
                    ref_color_out = tex.outputs["Color"]
                    ref_alpha_out = tex.outputs["Alpha"]
                elif ref_layer.layer_type == "FILL":
                    fn = _new_fill(node_tree, ref_layer.fill_color, x, y,
                                   layer_name=ref_layer.name, channel=channel_id)
                    ref_color_out = fn.outputs["Color"]
                    ref_alpha_out = None
                else:
                    continue
            finally:
                _retag_frame_owner(node_tree, _pre_ref_names, layer.name)

            # Convert to channel-appropriate socket type
            if is_scalar:
                sep = node_tree.nodes.new("ShaderNodeSeparateColor")
                sep.name = f"{TLM_PREFIX}ref_sep_{_next_id()}"
                sep.location = (x + 100, y - 20)
                node_tree.links.new(ref_color_out, sep.inputs["Color"])
                fill_attr_name = {
                    'roughness':    'roughness_fill',
                    'metallic':     'metallic_fill',
                    'transmission': 'transmission_fill',
                    'alpha':        'alpha_fill',
                }[channel_id]
                fill_val = getattr(layer, fill_attr_name, 1.0)
                if abs(fill_val - 1.0) > 1e-4:
                    fm = node_tree.nodes.new("ShaderNodeMath")
                    fm.operation = 'MULTIPLY'
                    fm.name = f"{TLM_PREFIX}ref_fill_mul_{_next_id()}"
                    fm.use_clamp = True
                    fm.location = (x + 200, y - 20)
                    node_tree.links.new(sep.outputs["Red"], fm.inputs[0])
                    fm.inputs[1].default_value = fill_val
                    layer_out = fm.outputs[0]
                else:
                    layer_out = sep.outputs["Red"]
                layer_alpha = None
            else:
                layer_out = ref_color_out
                layer_alpha = ref_alpha_out if not is_emission else None

            # Standard blend step (same as bottom of the generic path).
            # First-layer: if the REFERENCE has modulators (mask / opacity<1 /
            # fresnel), mix against a channel-appropriate baseline so the
            # modulator isn't silently dropped. (Bug #4)
            if channel_id == 'alpha':
                current = _new_alpha_math_composite(
                    node_tree, layer, current, layer_out, layer_alpha,
                    prev_alpha, x, y, i, uv_map
                )
                continue
            if current is None:
                if is_scalar:
                    mix_x = x + 280
                    # IMPORTANT: this is the CHANNEL's default base (e.g.
                    # 0.0 transmission, 0.5 roughness), NOT the layer's own
                    # fill value. Tag it with layer_name="" so the
                    # _hot_scalar_fill lookup (which searches by layer name
                    # + role `val_<channel>`) won't match this node by
                    # mistake and overwrite it with the layer's fill value.
                    # Bug history: with `layer_name=layer.name` here, two
                    # Value nodes were tagged identically (layer fill +
                    # channel base), so hot-update for transmission_fill /
                    # roughness_fill could update the WRONG node and
                    # leave the UI value visually inert.
                    base_val = _new_value(
                        node_tree,
                        _scalar_channel_default(channel_id),
                        x - 160,
                        y - 20,
                        layer_name="",
                        channel=channel_id,
                    ).outputs["Value"]
                    mix = _new_mix_scalar(node_tree,
                                           _effective_blend_mode(layer, channel_id),
                                           layer.opacity, mix_x, y - 40,
                                           layer_name=layer.name, channel=channel_id)
                    node_tree.links.new(base_val, _a_socket_scalar(mix))
                    node_tree.links.new(layer_out, _b_socket_scalar(mix))
                    _set_factor(node_tree, mix, layer, layer_alpha, None,
                                mix_x, y, i, uv_map, channel=channel_id)
                    current = _result_socket_scalar(mix)
                elif _layer_has_first_layer_modulator(layer):
                    mix_x = x + 280
                    bg = _new_fill(node_tree, (0.0, 0.0, 0.0, 1.0),
                                   x - 160, y + 120,
                                   layer_name=layer.name, channel=channel_id)
                    mix = _new_mix(node_tree,
                                   _effective_blend_mode(layer, channel_id),
                                   layer.opacity, mix_x, y - 40,
                                   layer_name=layer.name, channel=channel_id)
                    node_tree.links.new(bg.outputs["Color"], _a_socket(mix))
                    node_tree.links.new(layer_out, _b_socket(mix))
                    _set_factor(node_tree, mix, layer, layer_alpha, None,
                                mix_x, y, i, uv_map, channel=channel_id)
                    current = _result_socket(mix)
                else:
                    current = layer_out
                prev_alpha = layer_alpha if not is_scalar else None
                continue
            mix_x = x + 280
            if is_scalar:
                mix = _new_mix_scalar(node_tree,
                                       _effective_blend_mode(layer, channel_id),
                                       layer.opacity, mix_x, y - 40,
                                       layer_name=layer.name, channel=channel_id)
                node_tree.links.new(current, _a_socket_scalar(mix))
                node_tree.links.new(layer_out, _b_socket_scalar(mix))
                _tag(mix, layer.name, f"opacity_target_{channel_id}",
                     opacity_input_idx=-1)
                current = _result_socket_scalar(mix)
            else:
                mix = _new_mix(node_tree,
                               _effective_blend_mode(layer, channel_id),
                               layer.opacity, mix_x, y - 40,
                               layer_name=layer.name, channel=channel_id)
                node_tree.links.new(current, _a_socket(mix))
                node_tree.links.new(layer_out, _b_socket(mix))
                _set_factor(node_tree, mix, layer, layer_alpha, prev_alpha,
                            mix_x, y, i, uv_map, channel=channel_id)
                current = _result_socket(mix)
                prev_alpha = layer_alpha
            continue

        # Skip layers that don't contribute to this channel.
        # _layer_contributes_to honors:
        #   - output_channel routing (a layer pinned to ROUGHNESS only enters
        #     the roughness pass, never base_color / metallic / etc.)
        #   - the legacy use_<channel> toggles when output_channel == 'AUTO'.
        #
        # Note: the legacy "skip but still feed prev_alpha for base_color"
        # branch (`if flag_attr and not getattr(layer, flag_attr, False)`)
        # was always dead code for base_color (where flag_attr is None) and
        # caused output_channel routing to fail for non-base channels because
        # use_<channel>=False would override the routing. Removed.
        if not _layer_contributes_to(layer, channel_id):
            continue

        img_name = getattr(layer, img_attr, "")
        img = bpy.data.images.get(img_name) if img_name else None

        # ── Determine layer color/value output ────────────────────────────
        if layer.layer_type == "PAINT":
            out_ch = getattr(layer, 'output_channel', 'AUTO')
            _routed_handled = False

            # Routed PAINT: the main paint image (layer.image) becomes the
            # source for whatever target channel was selected, bypassing the
            # per-channel image_name properties (which the user typically
            # doesn't fill in for routed layers).
            # - target=Alpha → use image's native alpha output (PNG cutout)
            # - target=Roughness/Metallic/Transmission → SeparateColor.R
            # - target=Base Color → Color output
            if out_ch != 'AUTO':
                if not layer.image:
                    continue
                cs = "sRGB" if channel_id == 'base_color' else "Non-Color"
                tex = _new_img_tex(node_tree, layer.image, uv_map, x, y, cs,
                                   layer=layer,
                                   tag_role="paint_tex" if channel_id == 'base_color'
                                            else f"paint_routed_{channel_id}")
                layer_out = tex.outputs["Color"]
                layer_alpha = tex.outputs["Alpha"] if channel_id == 'base_color' else None
                if is_scalar:
                    if channel_id == 'alpha':
                        layer_out = tex.outputs["Alpha"]
                    else:
                        sep = node_tree.nodes.new("ShaderNodeSeparateColor")
                        sep.name = f"{TLM_PREFIX}routed_sep_{_next_id()}"
                        sep.location = (x + 220, y)
                        node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
                        layer_out = sep.outputs["Red"]
                    # Keep the image alpha as the Mix factor source for
                    # scalar routing too. Without this, "unpainted" areas
                    # (image alpha = 0) still override whatever was below
                    # because the Mix factor stays at opacity = 1.0
                    # everywhere → the paint's R value (typically 0 on
                    # an unpainted canvas) drives the entire surface.
                    # Symptom: paint→Roughness made the whole cube
                    # behave smooth/mirror instead of only the painted
                    # area. The alpha channel correctly says "this
                    # layer contributes here / doesn't contribute here".
                    # For channel_id='alpha' there's nothing to gate
                    # (alpha IS the layer output), so we keep None.
                    layer_alpha = (tex.outputs["Alpha"]
                                   if channel_id != 'alpha' else None)
                _routed_handled = True

            elif channel_id == 'base_color':
                if not layer.image:
                    continue
                cs = "sRGB"
            elif is_emission:
                # Use the painted image as emission source — this is the key workflow:
                # whatever you paint becomes the emission. Falls back to emission_color fill
                # if no image exists.
                if layer.image:
                    tex = _new_img_tex(node_tree, layer.image, uv_map, x, y, "sRGB",
                                       layer=layer, tag_role="paint_tex")
                    layer_out = tex.outputs["Color"]
                    layer_alpha = tex.outputs["Alpha"]
                else:
                    fn = _new_fill(node_tree, layer.emission_color, x, y, layer_name=layer.name, channel="emission")
                    layer_out = fn.outputs["Color"]
                    layer_alpha = None
                # Don't fall through to generic path below.
                # First-layer: if the PAINT emission layer has modulators
                # (mask / opacity<1 / fresnel), mix against a black baseline
                # so the modulator isn't silently dropped. (Bug #5)
                if current is None:
                    if _layer_has_first_layer_modulator(layer):
                        bg = _new_fill(node_tree, (0.0, 0.0, 0.0, 1.0),
                                       x - 160, y + 120,
                                       layer_name=layer.name, channel=channel_id)
                        mix_x = x + 280
                        mix = _new_mix(node_tree,
                                       _effective_blend_mode(layer, channel_id),
                                       layer.opacity, mix_x, y - 40,
                                       layer_name=layer.name, channel=channel_id)
                        node_tree.links.new(bg.outputs["Color"], _a_socket(mix))
                        node_tree.links.new(layer_out, _b_socket(mix))
                        _set_factor(node_tree, mix, layer, layer_alpha, None,
                                    mix_x, y, i, uv_map, channel=channel_id)
                        current = _result_socket(mix)
                    else:
                        current = layer_out
                    prev_alpha = layer_alpha
                    continue
                mix_x = x + 280
                mix = _new_mix(node_tree, _effective_blend_mode(layer, channel_id), layer.opacity, mix_x, y - 40, layer_name=layer.name, channel=channel_id)
                node_tree.links.new(current, _a_socket(mix))
                node_tree.links.new(layer_out, _b_socket(mix))
                _set_factor(node_tree, mix, layer, layer_alpha, prev_alpha, mix_x, y, i, uv_map, channel=channel_id)
                current = _result_socket(mix)
                prev_alpha = layer_alpha
                continue
            else:
                if not img:
                    continue
                cs = "Non-Color"

            if not _routed_handled:
                target_img = layer.image if channel_id == 'base_color' else img
                tex_tag = "paint_tex" if channel_id == 'base_color' else f"pbr_tex_{channel_id}"
                tex = _new_img_tex(node_tree, target_img, uv_map, x, y, cs, layer=layer,
                                   tag_role=tex_tag)
                layer_out = tex.outputs["Color"]
                layer_alpha = tex.outputs["Alpha"]

                if is_scalar:
                    # Use R channel for scalar maps
                    sep = node_tree.nodes.new("ShaderNodeSeparateColor")
                    sep.name = f"{TLM_PREFIX}sep_{_next_id()}"
                    sep.location = (x + 220, y)
                    node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
                    layer_out = sep.outputs["Red"]
                    layer_alpha = None

        elif layer.layer_type == "FILL":
            layer_alpha = None
            fill_out_ch = getattr(layer, 'output_channel', 'AUTO')
            if fill_out_ch == 'AUTO':
                fill_out_ch = 'BASE_COLOR'
            fill_target = _OUTPUT_CHANNEL_TO_TARGET.get(fill_out_ch, 'base_color')
            fill_routed = fill_target == channel_id and channel_id != 'base_color'

            if channel_id == 'base_color':
                fn = _new_fill(node_tree, layer.fill_color, x, y, layer_name=layer.name, channel="base_color")
                layer_out = fn.outputs["Color"]
            elif fill_routed and is_scalar:
                # Routed FILL on a scalar target: derive the scalar from the
                # color picker (luminance, Rec.709). The per-channel _fill
                # sliders (alpha_fill, roughness_fill, ...) are not exposed
                # in the UI for routed layers — the only thing the user sees
                # is the color swatch, so we honor that.
                # Black → 0, white → 1, grey 0.5 → 0.5; colors weighted by
                # ShaderNodeRGBToBW's built-in luminance formula.
                fn = _new_fill(node_tree, layer.fill_color, x - 100, y,
                               layer_name=layer.name, channel=channel_id)
                bw = node_tree.nodes.new("ShaderNodeRGBToBW")
                bw.name = f"{TLM_PREFIX}fill_lum_{_next_id()}"
                bw.location = (x + 80, y)
                _tag(bw, layer.name, f"fill_lum_{channel_id}")
                node_tree.links.new(fn.outputs["Color"], bw.inputs["Color"])
                layer_out = bw.outputs["Val"]
            elif is_scalar:
                img_name = getattr(layer, img_attr, "")
                img = bpy.data.images.get(img_name) if img_name else None
                if img:
                    print(f"[TLM] FILL {channel_id}: using image '{img.name}' for layer '{layer.name}'")
                    tex = _new_img_tex(node_tree, img, uv_map, x, y, "Non-Color",
                                       layer=layer, tag_role=f"pbr_tex_{channel_id}")
                    sep = node_tree.nodes.new("ShaderNodeSeparateColor")
                    sep.name = f"{TLM_PREFIX}sep_scalar_{_next_id()}"
                    sep.location = (x + 220, y)
                    node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
                    layer_out = sep.outputs["Red"]
                else:
                    fill_val = (layer.roughness_fill if channel_id == 'roughness'
                               else layer.transmission_fill if channel_id == 'transmission'
                               else layer.alpha_fill if channel_id == 'alpha'
                               else layer.metallic_fill)
                    print(f"[TLM] FILL {channel_id}: no image, using fill value {fill_val} for layer '{layer.name}'")
                    vn = _new_value(node_tree, fill_val, x, y, layer_name=layer.name, channel=channel_id)
                    layer_out = vn.outputs["Value"]
            elif is_emission:
                # Prefer an assigned image over the emission_color fill
                img_name = getattr(layer, img_attr, "")
                img = bpy.data.images.get(img_name) if img_name else None
                if img:
                    print(f"[TLM] FILL emission: using image '{img.name}' for layer '{layer.name}'")
                    tex = _new_img_tex(node_tree, img, uv_map, x, y, "sRGB",
                                       layer=layer, tag_role="pbr_tex_emission")
                    layer_out = tex.outputs["Color"]
                else:
                    print(f"[TLM] FILL emission: no image, using emission_color for layer '{layer.name}'")
                    fn = _new_fill(node_tree, layer.emission_color, x, y, layer_name=layer.name, channel="emission")
                    layer_out = fn.outputs["Color"]
            else:
                continue

        elif layer.layer_type == "PROCEDURAL":
            if channel_id == 'base_color':
                p_color, p_alpha = _build_procedural_node(node_tree, layer, uv_map, x, y)
                if p_color is None:
                    continue
                layer_out   = p_color
                layer_alpha = p_alpha
            elif is_emission:
                # Two paths to emission for a PROCEDURAL layer:
                #
                # 1) output_channel == 'EMISSION' (primary routing target):
                #    The proc's COLOUR output (post-ColorRamp) goes directly to
                #    BSDF.Emission Color, identical to how it drives Base Color.
                #    Cleaner path for cel-shading bands, neon patterns, burn
                #    effect emission, iridescent emission, etc. Skips the
                #    Fac-mask threshold pipeline entirely.
                #
                # 2) use_emission=True with output_channel ≠ 'EMISSION':
                #    Legacy "Fac mask + emission_color" path below — the proc's
                #    pattern selects WHERE to emit (threshold/falloff) and
                #    emission_color picks the colour. Required for the
                #    selective-emission workflows (RANDOM_CELLS / NOISE / IMAGE
                #    selectors) and for backward compatibility with presets
                #    saved before the EMISSION enum existed.
                if getattr(layer, 'output_channel', 'BASE_COLOR') == 'EMISSION':
                    p_color, p_alpha = _build_procedural_node(
                        node_tree, layer, uv_map, x, y
                    )
                    if p_color is None:
                        continue
                    layer_out = p_color
                    layer_alpha = p_alpha
                else:
                    fac_out = _build_proc_fac_node(
                        node_tree, layer, f"emis_{i}", x, y, uv_map
                    )
                    if fac_out is None:
                        continue

                    # Unified smooth emission mask: Invert → Power → SmoothStep
                    # No more binary LESS_THAN vs soft Power split — one
                    # continuous pipeline.
                    threshold = getattr(layer, 'proc_emission_threshold', 0.0)
                    falloff   = getattr(layer, 'proc_emission_falloff', 0.08)
                    contrast  = getattr(layer, 'proc_contrast', 0.5)
                    exponent  = 1.0 + contrast * 8.0   # range 1.0 → 9.0

                    # Step 1: edge = 1 - fac (invert so mask=1 at cell edges)
                    invert = node_tree.nodes.new("ShaderNodeMath")
                    invert.operation = 'SUBTRACT'
                    invert.name = f"{TLM_PREFIX}emis_inv_{i}"
                    invert.location = (x + 80, y - 120)
                    invert.inputs[0].default_value = 1.0
                    node_tree.links.new(fac_out, invert.inputs[1])
                    invert.use_clamp = True

                    # Step 2: shaped = edge ^ exponent (contrast sharpening)
                    power = node_tree.nodes.new("ShaderNodeMath")
                    power.operation = 'POWER'
                    power.name = f"{TLM_PREFIX}emis_pow_{i}"
                    power.location = (x + 240, y - 120)
                    node_tree.links.new(invert.outputs["Value"], power.inputs[0])
                    power.inputs[1].default_value = exponent
                    power.use_clamp = True

                    # Step 3: smoothstep(threshold, threshold + falloff, shaped)
                    mr = node_tree.nodes.new("ShaderNodeMapRange")
                    mr.name = f"{TLM_PREFIX}emis_smooth_{i}"
                    mr.location = (x + 420, y - 120)
                    mr.clamp = True
                    if hasattr(mr, 'data_type'):
                        mr.data_type = 'FLOAT'
                    if hasattr(mr, 'interpolation_type'):
                        mr.interpolation_type = 'SMOOTHSTEP'
                    node_tree.links.new(power.outputs["Value"], mr.inputs["Value"])
                    mr.inputs["From Min"].default_value = threshold
                    mr.inputs["From Max"].default_value = min(1.0, threshold + max(0.001, falloff))
                    mr.inputs["To Min"].default_value   = 0.0
                    mr.inputs["To Max"].default_value   = 1.0
                    mask_out = mr.outputs.get("Result") or mr.outputs[0]

                    # ── Selective emission ───────────────────────────────
                    # An optional second mask gates WHERE the procedural
                    # emission is allowed to light up — multiplied in here
                    # so the smoothstep above still shapes each lit
                    # region's falloff. Disabled by default (NONE).
                    selector_out = _build_emission_selector(
                        node_tree, layer, uv_map, x, y, name_tag=f"emis_{i}"
                    )
                    if selector_out is not None:
                        sel_mul = node_tree.nodes.new("ShaderNodeMath")
                        sel_mul.operation = 'MULTIPLY'
                        sel_mul.use_clamp = True
                        sel_mul.name = f"{TLM_PREFIX}emis_sel_mul_{i}"
                        sel_mul.location = (x + 580, y - 120)
                        node_tree.links.new(mask_out, sel_mul.inputs[0])
                        node_tree.links.new(selector_out, sel_mul.inputs[1])
                        mask_out = sel_mul.outputs["Value"]

                    # Mix: lerp(black, emission_color, mask)
                    emis_fill = _new_fill(node_tree, layer.emission_color, x + 160, y - 200)
                    emis_fill.name = f"{TLM_PREFIX}emis_color_{i}"
                    emis_black = _new_fill(node_tree, (0, 0, 0, 1), x + 160, y - 280)
                    emis_black.name = f"{TLM_PREFIX}emis_black2_{i}"
                    emis_mix = _new_mix(node_tree, 'MIX', 1.0, x + 340, y - 150)
                    emis_mix.name = f"{TLM_PREFIX}emis_mask_{i}"
                    node_tree.links.new(emis_black.outputs["Color"], _a_socket(emis_mix))
                    node_tree.links.new(emis_fill.outputs["Color"], _b_socket(emis_mix))
                    node_tree.links.new(mask_out, _factor_socket(emis_mix))
                    layer_out = _result_socket(emis_mix)
                    layer_alpha = None
            elif is_scalar:
                # Drive roughness/metallic/transmission/alpha from the
                # same procedural colour graph used by Base Color, then
                # convert that colour to a scalar. This keeps ColorRamp,
                # Color 3, Contrast and custom procedural colour topology
                # consistent across every routed output.
                #
                # When this channel is the layer's PRIMARY routing target
                # (output_channel matches), the user expects the procedural
                # pattern to drive the BSDF input directly — no multiplier.
                # Otherwise (additional channel reached via use_<channel>
                # toggle), the *_fill slider acts as an intensity dial.
                # The distinction matters because *_fill defaults are not
                # symmetrical: roughness_fill=0.5, metallic_fill=0.0,
                # alpha_fill=1.0. A PROCEDURAL routed to METALLIC with
                # default fill_val=0.0 was multiplying the noise by zero.
                proc_color, _proc_alpha = _build_procedural_node(
                    node_tree, layer, uv_map, x, y
                )
                if proc_color is None:
                    continue
                to_scalar = node_tree.nodes.new("ShaderNodeRGBToBW")
                to_scalar.name = f"{TLM_PREFIX}proc_scalar_lum_{channel_id}_{i}"
                to_scalar.location = (x + 520, y)
                _tag(to_scalar, layer.name, f"proc_scalar_lum_{channel_id}")
                node_tree.links.new(proc_color, to_scalar.inputs["Color"])
                scalar_out = to_scalar.outputs["Val"]

                _routing_target = _OUTPUT_CHANNEL_TO_TARGET.get(
                    getattr(layer, 'output_channel', 'BASE_COLOR'), 'base_color'
                )

                if _routing_target == channel_id:
                    # Primary target: pass the procedural value through directly.
                    layer_out = scalar_out
                else:
                    # Additional channel (use_X toggle on top of the routing
                    # target). Multiply by the per-channel fill slider so the
                    # user can dial in intensity.
                    fill_val = (layer.roughness_fill if channel_id == 'roughness'
                               else layer.transmission_fill if channel_id == 'transmission'
                               else layer.alpha_fill if channel_id == 'alpha'
                               else layer.metallic_fill)
                    scale = node_tree.nodes.new("ShaderNodeMath")
                    scale.operation = 'MULTIPLY'
                    scale.use_clamp = True
                    scale.name = f"{TLM_PREFIX}proc_scalar_{channel_id}_{i}"
                    scale.location = (x + 700, y)
                    node_tree.links.new(scalar_out, scale.inputs[0])
                    scale.inputs[1].default_value = fill_val
                    layer_out = scale.outputs["Value"]
                layer_alpha = None
            else:
                continue

        else:
            continue

        # ── Mix with current ──────────────────────────────────────────────
        if channel_id == 'alpha':
            current = _new_alpha_math_composite(
                node_tree, layer, current, layer_out, layer_alpha, prev_alpha,
                x, y, i, uv_map
            )
            continue

        if current is None:
            # Emission always mixes against black so opacity=0 → zero emission,
            # even without explicit modulators. For every other channel, only
            # create a mix when a modulator (mask / fresnel / opacity<1) would
            # otherwise be silently dropped. (Bugs #3, #4, #5 consolidation.)
            # Color channels also need a mix whenever a layer_alpha is present
            # — otherwise a PAINT layer that's mostly transparent renders its
            # raw RGB on the alpha=0 pixels (e.g. black for an empty canvas).
            # A scalar paint with image alpha needs the modulator mix too,
            # so unpainted pixels (alpha=0) pass through the channel's
            # default value instead of being overwritten with the paint's
            # R=0 → uniform smoothness / zero metallic / etc. on the
            # whole surface.
            needs_modulator_mix = _layer_has_first_layer_modulator(layer) or (
                not is_emission and layer_alpha is not None
            )
            if is_emission:
                black = _new_fill(node_tree, (0.0, 0.0, 0.0, 1.0),
                                  x - 100, y - 80)
                black.name = f"{TLM_PREFIX}emission_black_{i}"
                mix_x = x + 280
                mix = _new_mix(node_tree, 'MIX', layer.opacity, mix_x, y - 40,
                               layer_name=layer.name, channel=channel_id)
                node_tree.links.new(black.outputs["Color"], _a_socket(mix))
                node_tree.links.new(layer_out, _b_socket(mix))
                _set_factor(node_tree, mix, layer, layer_alpha, None,
                            mix_x, y, i, uv_map, channel=channel_id)
                current = _result_socket(mix)
                prev_alpha = layer_alpha
            elif is_scalar:
                # Scalar channel first-layer (roughness/metallic/transmission/
                # alpha): mix against a CHANNEL-DEFAULT baseline so the
                # unpainted pixels render with the BSDF's natural default
                # for that channel rather than a forced zero.
                #   roughness    -> 0.5 (BSDF default)
                #   metallic     -> 0.0 (BSDF default, non-metal)
                #   transmission -> 0.0 (BSDF default, opaque)
                #   alpha        -> 1.0 (BSDF default, fully opaque)
                # Mask / fresnel / opacity / alpha all still modulate
                # *between* the baseline and the layer's value.
                #
                # IMPORTANT: tag this base_val with layer_name="" so it does
                # NOT collide with the LAYER's own `val_<channel>` tag used
                # by `_hot_scalar_fill`. Otherwise the hot path may update
                # this baseline node (the default 0.0 / 0.5 / 1.0) instead
                # of the layer's actual `*_fill` value, leaving the UI
                # slider visually inert.
                base_val = _new_value(node_tree, _scalar_channel_default(channel_id),
                                      x - 100, y - 20,
                                      layer_name="",
                                      channel=channel_id).outputs["Value"]
                mix_x = x + 280
                mix = _new_mix_scalar(node_tree,
                                       _effective_blend_mode(layer, channel_id),
                                       layer.opacity, mix_x, y - 40,
                                       layer_name=layer.name, channel=channel_id)
                node_tree.links.new(base_val, _a_socket_scalar(mix))
                node_tree.links.new(layer_out, _b_socket_scalar(mix))
                # prev_alpha=None because this is the bottom layer (no clip
                # target). _set_factor handles the no-prev-alpha branch via
                # `if layer.use_clipping_mask and prev_alpha is not None`.
                _set_factor(node_tree, mix, layer, layer_alpha, None,
                            mix_x, y, i, uv_map, channel=channel_id)
                current = _result_socket_scalar(mix)
            elif needs_modulator_mix:
                # Color channel (base_color via _composite_layer_list for group
                # children) with a modulator on the bottom layer: synthesize a
                # neutral gray background so the modulator has a mix partner.
                bg = _new_fill(node_tree, (0.5, 0.5, 0.5, 1.0),
                               x - 100, y + 80,
                               layer_name=layer.name, channel=channel_id)
                bg.name = f"{TLM_PREFIX}bottom_bg_{channel_id}_{_next_id()}"
                mix_x = x + 280
                mix = _new_mix(node_tree,
                               _effective_blend_mode(layer, channel_id),
                               layer.opacity, mix_x, y - 40,
                               layer_name=layer.name, channel=channel_id)
                node_tree.links.new(bg.outputs["Color"], _a_socket(mix))
                node_tree.links.new(layer_out, _b_socket(mix))
                _set_factor(node_tree, mix, layer, layer_alpha, None,
                            mix_x, y, i, uv_map, channel=channel_id)
                current = _result_socket(mix)
                prev_alpha = layer_alpha
            else:
                current = layer_out
                prev_alpha = layer_alpha if not is_scalar else None
            continue

        mix_x = x + 280
        if is_scalar:
            mix = _new_mix_scalar(node_tree,
                                   _effective_blend_mode(layer, channel_id),
                                   layer.opacity, mix_x, y - 40,
                                   layer_name=layer.name, channel=channel_id)
            node_tree.links.new(current, _a_socket_scalar(mix))
            node_tree.links.new(layer_out, _b_socket_scalar(mix))
            # Same factor pipeline as the color path (opacity × alpha × mask
            # × fresnel × clipping). Without this call, scalar channels
            # ignored mask/fresnel/clipping and used opacity straight as
            # the Factor — feature parity break vs Base Color.
            _set_factor(node_tree, mix, layer, layer_alpha, prev_alpha, mix_x, y, i, uv_map, channel=channel_id)
            current = _result_socket_scalar(mix)
        else:
            mix = _new_mix(node_tree, _effective_blend_mode(layer, channel_id), layer.opacity, mix_x, y - 40, layer_name=layer.name, channel=channel_id)
            node_tree.links.new(current, _a_socket(mix))
            node_tree.links.new(layer_out, _b_socket(mix))
            _set_factor(node_tree, mix, layer, layer_alpha, prev_alpha, mix_x, y, i, uv_map, channel=channel_id)
            current = _result_socket(mix)
            prev_alpha = layer_alpha

    return current


def _set_factor(node_tree, mix_node, layer, layer_alpha, prev_alpha, x, y, i, uv_map="UVMap", channel="base_color"):
    """Wire up the blend factor for a color mix node."""
    mask_applied = False
    if getattr(layer, 'use_mask', False):
        # Pass layer_alpha so the mask pipeline folds it into the final
        # factor: factor = mask × opacity × alpha. The mask alone is not
        # enough — a transparent paint stroke must remain transparent
        # even where the mask says "show".
        mult = _apply_mask(node_tree, mix_node, layer, uv_map, x, y,
                           layer_alpha=layer_alpha)
        if mult is not None:
            mult.inputs[1].default_value = layer.opacity
            _tag(mult, layer.name, f"opacity_target_{channel}", opacity_input_idx=1)
            mask_applied = True

    if mask_applied:
        pass  # mask pipeline wrote to factor socket
    elif layer.use_clipping_mask and prev_alpha is not None:
        clip = node_tree.nodes.new("ShaderNodeMath")
        clip.operation = 'MULTIPLY'
        clip.name = f"{TLM_PREFIX}clip_{i}"
        clip.location = (x - 160, y - 180)
        if layer_alpha:
            node_tree.links.new(layer_alpha, clip.inputs[0])
            node_tree.links.new(prev_alpha, clip.inputs[1])
            op = node_tree.nodes.new("ShaderNodeMath")
            op.operation = 'MULTIPLY'
            op.name = f"{TLM_PREFIX}clip_op_{i}"
            op.location = (x - 50, y - 180)
            node_tree.links.new(clip.outputs["Value"], op.inputs[0])
            op.inputs[1].default_value = layer.opacity
            _tag(op, layer.name, f"opacity_target_{channel}", opacity_input_idx=1)
            node_tree.links.new(op.outputs["Value"], _factor_socket(mix_node))
        else:
            node_tree.links.new(prev_alpha, clip.inputs[0])
            clip.inputs[1].default_value = layer.opacity
            _tag(clip, layer.name, f"opacity_target_{channel}", opacity_input_idx=1)
            node_tree.links.new(clip.outputs["Value"], _factor_socket(mix_node))
    else:
        # Factor = opacity × layer_alpha (whenever alpha exists).
        # Previous code only folded alpha into the factor when the blend
        # mode was MIX, on the (wrong) assumption that "non-MIX modes
        # ignore alpha". The alpha is COVERAGE, not a colour input — it
        # tells the Mix where the layer "exists". Without it, a paint
        # layer in DIVIDE/SCREEN/OVERLAY/etc. shows through transparent
        # pixels because Factor=opacity=1.0 ignores empty paint.
        # Now wired uniformly:
        #   layer_alpha present → MULTIPLY(alpha, opacity) → Factor
        #   no alpha            → default_value = opacity
        if layer_alpha:
            am = node_tree.nodes.new("ShaderNodeMath")
            am.operation = 'MULTIPLY'
            am.name = f"{TLM_PREFIX}alpha_mult_{i}"
            am.location = (x - 160, y - 180)
            node_tree.links.new(layer_alpha, am.inputs[0])
            am.inputs[1].default_value = layer.opacity
            _tag(am, layer.name, f"opacity_target_{channel}", opacity_input_idx=1)
            node_tree.links.new(am.outputs["Value"], _factor_socket(mix_node))
        else:
            _factor_socket(mix_node).default_value = layer.opacity
            # Tag the mix node itself as opacity target (direct factor write)
            _tag(mix_node, layer.name, f"opacity_target_{channel}",
                 opacity_input_idx=-1)  # -1 = factor socket

    # ── Fresnel mask: multiply the current factor by a Fresnel output ─────
    # This makes the layer visible only at glancing angles (edge glow / rim)
    fresnel_fac = _build_fresnel_mask(node_tree, layer, x, y, name_tag=str(i))
    if fresnel_fac is not None:
        factor_sock = _factor_socket(mix_node)
        # Read what's currently driving the factor
        existing_link = None
        for link in node_tree.links:
            if link.to_socket == factor_sock:
                existing_link = link
                break
        fmult = node_tree.nodes.new("ShaderNodeMath")
        fmult.operation = 'MULTIPLY'
        fmult.name = f"{TLM_PREFIX}fresnel_mult_{i}"
        fmult.location = (x - 80, y - 320)
        fmult.use_clamp = True
        if existing_link:
            # Factor is driven by a link — reroute through Fresnel multiply
            src = existing_link.from_socket
            node_tree.links.remove(existing_link)
            node_tree.links.new(src, fmult.inputs[0])
        else:
            # Factor is a constant — use its value
            fmult.inputs[0].default_value = factor_sock.default_value
        node_tree.links.new(fresnel_fac, fmult.inputs[1])
        node_tree.links.new(fmult.outputs["Value"], factor_sock)


# ── Coordinate normalization helper ──────────────────────────────────────────

def _composite_layer_list(node_tree, layers, uv_map, start_x, y_base, x_step):
    """Composite a flat list for the base color channel only (used for groups)."""
    return _build_channel(node_tree, layers, 'base_color', uv_map, start_x, y_base, x_step)


# ── Main rebuild ──────────────────────────────────────────────────────────────

def _link_to_bsdf(node_tree, output_socket, bsdf, input_names, channel_label):
    """Try to link output_socket to one of the BSDF input_names. Logs on failure."""
    for name in input_names:
        if name in bsdf.inputs:
            node_tree.links.new(output_socket, bsdf.inputs[name])
            print(f"[TLM] Connected {channel_label} → BSDF.{name}")
            return
    print(f"[TLM] ERROR: could not connect {channel_label} — "
          f"tried {input_names}, BSDF inputs: {[i.name for i in bsdf.inputs]}")


def _add_channel_reroute(node_tree, output_socket, channel_label, x, y):
    """Add a material-level reroute so long channel links stay horizontal."""
    rr = node_tree.nodes.new("NodeReroute")
    rr.name = f"{TLM_PREFIX}route_{channel_label}_{_next_id()}"
    rr.label = channel_label.replace("_", " ").title()
    rr.location = (x, y)
    _tag(rr, "__material__", f"route_{channel_label}")
    node_tree.links.new(output_socket, rr.inputs[0])
    return rr.outputs[0]


def _link_channel_to_bsdf(node_tree, output_socket, bsdf, input_names,
                          channel_label, route_x, route_y):
    routed = _add_channel_reroute(
        node_tree, output_socket, channel_label, route_x, route_y
    )
    _link_to_bsdf(node_tree, routed, bsdf, input_names, channel_label)


def _build_base_color(node_tree, root_layers, group_children, uv_map, start_x, y_base, x_step):
    """
    Build the base color chain from root_layers, treating each GROUP as a
    composited unit. This preserves proper alpha for Clipping Mask support —
    the alpha of the group's output is used as prev_alpha for layers above it.
    """
    current = None
    prev_alpha = None
    positions = _layer_positions(root_layers, start_x, y_base)

    for i, layer in enumerate(root_layers):
        x, y = positions[i]

        if layer.layer_type == "ADJUSTMENT":
            if current is None:
                continue
            # Only apply adjustments whose output_channel targets
            # base_color. Adjustments routed to ROUGHNESS / METALLIC /
            # ALPHA are handled inside _build_channel for those
            # channels — applying them here would pollute Base Color.
            if _adj_target_channel(layer) == 'base_color':
                current = _apply_adjustment(node_tree, layer, current, x, y,
                                            channel_id='base_color')
            continue

        # Output channel routing: a layer pinned to a non-base channel must
        # NOT pollute Base Color. Skip here. (ADJUSTMENT above is exempt
        # because it modifies `current` rather than feeding into a channel.)
        if not _layer_contributes_to(layer, 'base_color'):
            continue

        if layer.layer_type == "GROUP":
            children = [l for l in group_children.get(layer.name, []) if l.visible]
            if not children:
                continue
            group_out = _composite_layer_list(
                node_tree, children, uv_map, x - 200, y - 200, 220
            )
            if group_out is None:
                continue
            layer_color_out = group_out
            # For groups, synthesize an alpha using a Value node = 1.0
            # so layers above with Clipping Mask can use it
            alpha_node = node_tree.nodes.new("ShaderNodeValue")
            alpha_node.name = f"{TLM_PREFIX}group_alpha_{i}"
            alpha_node.outputs[0].default_value = 1.0
            alpha_node.location = (x, y - 150)
            layer_alpha_out = alpha_node.outputs[0]

        elif layer.layer_type == "PAINT" and layer.image:
            tex = _new_img_tex(node_tree, layer.image, uv_map, x, y, layer=layer,
                               tag_role="paint_tex")
            layer_color_out = tex.outputs["Color"]
            layer_alpha_out = tex.outputs["Alpha"]

        elif layer.layer_type == "FILL":
            fn = _new_fill(node_tree, layer.fill_color, x, y, layer_name=layer.name, channel="base_color")
            layer_color_out = fn.outputs["Color"]
            layer_alpha_out = None

        elif layer.layer_type == "PROCEDURAL":
            p_color, p_alpha = _build_procedural_node(node_tree, layer, uv_map, x, y)
            if p_color is None:
                continue
            layer_color_out = p_color
            layer_alpha_out = p_alpha

        elif layer.layer_type == "REFERENCE":
            # Reuse another layer's pattern, then apply THIS layer's blend/opacity/mask.
            # Uses the SAME pattern-extraction logic as _build_channel (lines ~1530),
            # kept in sync with it — the generic _build_channel handles Reference for
            # roughness/metallic/etc., but base_color has its own dedicated builder
            # (_build_base_color) to preserve group alpha for Clipping Mask.
            ref_name = getattr(layer, 'reference_layer_name', '')
            # Search root layers first, then inside groups (Reference can point
            # at a layer nested in a group).
            ref_layer = next(
                (l for l in root_layers if l.name == ref_name and l != layer),
                None,
            )
            if ref_layer is None:
                for _gname, _children in group_children.items():
                    found = next(
                        (l for l in _children if l.name == ref_name and l != layer),
                        None,
                    )
                    if found is not None:
                        ref_layer = found
                        break
            if ref_layer is None or ref_layer.layer_type == "REFERENCE":
                continue  # target not found or cyclic — silently skip

            # Snapshot node names before the copy is built so we can retag the
            # newly-created nodes with `tlm_frame_owner = layer.name` — this
            # makes frame grouping place them under the REFERENCE's own frame
            # instead of merging them with the source's frame.
            # try/finally ensures retag happens even when a branch `continue`s
            # (e.g. `ref_color is None` for an unknown procedural type).
            _pre_ref_names = {n.name for n in node_tree.nodes}
            try:
                if ref_layer.layer_type == "PROCEDURAL":
                    ref_color, ref_alpha = _build_procedural_node(
                        node_tree, ref_layer, uv_map, x, y
                    )
                    if ref_color is None:
                        continue
                    layer_color_out = ref_color
                    layer_alpha_out = ref_alpha
                elif ref_layer.layer_type == "PAINT" and ref_layer.image:
                    tex = _new_img_tex(node_tree, ref_layer.image, uv_map, x, y, layer=ref_layer)
                    # _new_img_tex doesn't tag — stamp the tex node so it's
                    # findable by _assign_layer_frames.
                    _tag(tex, ref_layer.name, "ref_tex")
                    layer_color_out = tex.outputs["Color"]
                    layer_alpha_out = tex.outputs["Alpha"]
                elif ref_layer.layer_type == "FILL":
                    fn = _new_fill(node_tree, ref_layer.fill_color, x, y,
                                   layer_name=ref_layer.name, channel="base_color")
                    layer_color_out = fn.outputs["Color"]
                    layer_alpha_out = None
                else:
                    continue  # unsupported ref type (e.g. GROUP, ADJUSTMENT)
            finally:
                _retag_frame_owner(node_tree, _pre_ref_names, layer.name)

        else:
            continue

        if current is None:
            # Bottom-layer modulators:
            #   - opacity / blend_mode: no-op for non-GROUP layers (nothing
            #     below to blend with), so we skip the mix for efficiency.
            #   - use_mask: ALWAYS meaningful — a mask constrains where the
            #     layer is visible, revealing the "background" underneath.
            #   - layer_alpha: a PAINT layer with transparent areas (alpha<1)
            #     also needs a background to show through, otherwise alpha=0
            #     pixels render whatever raw RGB the image carries (often
            #     black for a freshly-created paint canvas) and the user
            #     sees a black cube instead of an empty layer.
            # For GROUPs we additionally apply opacity/blend_mode against
            # the synthesized background so the group as a whole fades.
            has_mask = getattr(layer, 'use_mask', False)
            has_fresnel = getattr(layer, 'use_fresnel_mask', False)
            has_alpha_socket = layer_alpha_out is not None
            group_mods = layer.layer_type == "GROUP" and (
                abs(layer.opacity - 1.0) > 1e-4
                or layer.blend_mode != "MIX"
            )
            needs_modulator = has_mask or has_fresnel or has_alpha_socket or group_mods
            if needs_modulator:
                # Neutral white background — visible as "empty" wherever the
                # layer's alpha (or mask) drops to zero. Better UX than black.
                bg = _new_fill(node_tree, (1.0, 1.0, 1.0, 1.0),
                               x - 180, y + 120,
                               layer_name=layer.name, channel="base_color")
                bg.name = f"{TLM_PREFIX}bottom_bg_{_next_id()}"
                mix_x = x + 280
                mix = _new_mix(node_tree,
                               _effective_blend_mode(layer, "base_color"),
                               layer.opacity, mix_x, y - 40,
                               layer_name=layer.name, channel="base_color")
                node_tree.links.new(bg.outputs["Color"], _a_socket(mix))
                node_tree.links.new(layer_color_out, _b_socket(mix))
                _set_factor(node_tree, mix, layer, layer_alpha_out, None,
                            mix_x, y, i, uv_map, channel="base_color")
                current = _result_socket(mix)
            else:
                current = layer_color_out
            prev_alpha = layer_alpha_out
            continue

        mix_x = x + 280
        mix = _new_mix(node_tree, _effective_blend_mode(layer, "base_color"), layer.opacity, mix_x, y - 40, layer_name=layer.name, channel="base_color")
        node_tree.links.new(current, _a_socket(mix))
        node_tree.links.new(layer_color_out, _b_socket(mix))
        _set_factor(node_tree, mix, layer, layer_alpha_out, prev_alpha, mix_x, y, i, uv_map, channel="base_color")
        current = _result_socket(mix)
        prev_alpha = layer_alpha_out

    # Return both the color result AND the final alpha. The caller can wire
    # alpha to BSDF.Alpha automatically when no explicit alpha layer is
    # present — gives the natural "PAINT image with transparency = cube
    # transparent" workflow without requiring a dedicated Output=Alpha layer.
    return current, prev_alpha


def _layer_has_first_layer_modulator(layer):
    """True if a first-layer contributor needs a mix against a synthetic baseline.

    Modulators = mask, fresnel, or opacity < 1. Clipping mask is excluded
    because it requires a ``prev_alpha`` which only exists from layer i > 0.
    Applies to every channel (color / scalar / vector): when True and this is
    the first contributing layer, we must mix against a channel-appropriate
    baseline instead of silently dropping the modulator (Bugs #3, #4, #5).
    """
    return (
        getattr(layer, 'use_mask', False)
        or getattr(layer, 'use_fresnel_mask', False)
        or layer.opacity < 0.9999
    )


def _scalar_channel_default(channel_id):
    return {
        'roughness': 0.5,
        'metallic': 0.0,
        'transmission': 0.0,
        'alpha': 1.0,
    }.get(channel_id, 0.0)


def _make_baseline_normal(node_tree, x, y):
    """Create a ShaderNodeNewGeometry and return its Normal output socket.

    Used as the A-side baseline when the very first Normal/Bump layer has a
    modulator but no incoming normal exists yet — mixing against the shading
    normal preserves the intended 'at mask=0 / opacity=0, keep surface flat'.
    """
    geo = node_tree.nodes.new("ShaderNodeNewGeometry")
    geo.name = f"{TLM_PREFIX}normal_baseline_{_next_id()}"
    geo.location = (x, y)
    return geo.outputs["Normal"]


def _build_normal_channel(node_tree, layers, uv_map, x0, y_base, x_step):
    """Build the normal map chain with per-layer NormalMap nodes and vector blending.

    Unlike _build_channel (which mixes tangent-space colors then converts once),
    this function:
      1. Creates a NormalMap node per layer (each with its own Strength)
      2. Inserts optional Mapping node for tiling/rotation
      3. Blends the resulting Normal VECTORS via Mix(VECTOR), not Mix(RGBA)

    Returns the final Normal vector socket, or None if no layer contributes.
    """
    import math as _math
    current = None
    positions = _layer_positions(layers, x0, y_base)

    for i, layer in enumerate(layers):
        x, y = positions[i]

        if layer.layer_type == "ADJUSTMENT":
            continue
        if not _layer_contributes_to(layer, 'normal'):
            continue

        img_name = getattr(layer, 'normal_image_name', "")
        img = bpy.data.images.get(img_name) if img_name else None
        if not img:
            continue

        # ── UV + optional Mapping (tiling/rotation) ──────────────────────
        uv_node = node_tree.nodes.new("ShaderNodeUVMap")
        uv_node.uv_map = uv_map
        uv_node.name = f"{TLM_PREFIX}normal_uv_{_next_id()}"
        uv_node.location = (x - 200, y)
        _tag(uv_node, layer.name, "normal_uv")

        tile_scale = getattr(layer, 'normal_tile_scale', 1.0)
        rotation = getattr(layer, 'normal_rotation', 0.0)
        vec_out = uv_node.outputs["UV"]

        mapping = node_tree.nodes.new("ShaderNodeMapping")
        mapping.name = f"{TLM_PREFIX}normal_mapping_{_next_id()}"
        mapping.location = (x - 50, y)
        mapping.inputs["Scale"].default_value = (tile_scale, tile_scale, 1.0)
        mapping.inputs["Rotation"].default_value = (
            0.0, 0.0, _math.radians(rotation)
        )
        _tag(mapping, layer.name, "normal_mapping")
        node_tree.links.new(vec_out, mapping.inputs["Vector"])
        vec_out = mapping.outputs["Vector"]

        # ── Image Texture (Non-Color) ────────────────────────────────────
        tex = node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        if img.colorspace_settings.name != "Non-Color":
            img.colorspace_settings.name = "Non-Color"
        tex.name = f"{TLM_PREFIX}normal_tex_{_next_id()}"
        tex.location = (x + 100, y)
        _tag(tex, layer.name, "normal_tex")
        node_tree.links.new(vec_out, tex.inputs["Vector"])

        # ── Per-layer NormalMap node with individual Strength ─────────────
        nm = node_tree.nodes.new("ShaderNodeNormalMap")
        nm.name = f"{TLM_PREFIX}normalmap_{_next_id()}"
        nm.location = (x + 300, y)
        nm.inputs["Strength"].default_value = getattr(layer, 'normal_strength', 1.0)
        _tag(nm, layer.name, "normal_map_node")
        node_tree.links.new(tex.outputs["Color"], nm.inputs["Color"])

        layer_normal = nm.outputs["Normal"]

        # ── Blend with previous (vector space, not color!) ───────────────
        # First layer: if the layer has a modulator (mask / opacity<1 / fresnel)
        # we must still mix against a baseline shading normal — otherwise the
        # modulator is silently dropped and the user's mask is ignored.
        if current is None:
            if not _layer_has_first_layer_modulator(layer):
                current = layer_normal
                continue
            current = _make_baseline_normal(node_tree, x + 200, y - 150)

        mix_x = x + 500
        mix = _new_mix_vector(node_tree, layer.opacity, mix_x, y - 40,
                              layer_name=layer.name, channel="normal")
        node_tree.links.new(current, _a_socket(mix))
        node_tree.links.new(layer_normal, _b_socket(mix))

        # Reuse _set_factor for mask / clipping mask / fresnel support
        _set_factor(node_tree, mix, layer, None, None, mix_x, y, i, uv_map,
                    channel="normal")

        current = _result_socket(mix)

    return current


def _build_bump_channel(node_tree, layers, uv_map, start_x, y_base, x_step,
                        incoming_normal=None):
    """Build the bump chain with per-layer Bump nodes and vector-space blending.

    Each layer with use_bump gets its own ShaderNodeBump with its own Strength
    and Distance. The resulting Normal vectors are blended via Mix(VECTOR) with
    _set_factor support — so per-layer opacity, masks, clipping and fresnel all
    behave identically to the Normal channel. This mirrors _build_normal_channel.

    ``incoming_normal``: Normal vector socket coming from _build_normal_channel
    (or None). When provided, it feeds the first Bump node's Normal input so
    bumps perturb the normal-mapped surface instead of the flat shading normal,
    and also acts as the A-side baseline for the first mix when the first layer
    has modulators (mask / opacity < 1 / fresnel / clipping).

    Returns the final Normal vector socket, or None if no layer contributes.
    """
    current = incoming_normal
    positions = _layer_positions(layers, start_x, y_base)

    for i, layer in enumerate(layers):
        if layer.layer_type == "ADJUSTMENT":
            continue
        if not _layer_contributes_to(layer, 'bump'):
            continue

        x, y = positions[i]
        fac_out = None

        if layer.layer_type == "PROCEDURAL":
            # Reuse shared Fac helper — avoids duplicating all texture branches.
            fac_out = _build_proc_fac_node(node_tree, layer, f"bump_{i}", x, y, uv_map)

        elif layer.layer_type == "PAINT" and layer.image:
            # Use R channel of the paint image as height.
            tex = node_tree.nodes.new("ShaderNodeTexImage")
            tex.name = f"{TLM_PREFIX}bump_img_{_next_id()}"
            tex.image = layer.image
            tex.location = (x, y)
            try:
                tex.image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
            _tag(tex, layer.name, "bump_tex")
            uv = node_tree.nodes.new("ShaderNodeUVMap")
            uv.name = f"{TLM_PREFIX}bump_uv_{_next_id()}"
            uv.uv_map = uv_map
            uv.location = (x - 220, y)
            node_tree.links.new(uv.outputs["UV"], tex.inputs["Vector"])
            sep = node_tree.nodes.new("ShaderNodeSeparateColor")
            sep.name = f"{TLM_PREFIX}bump_sep_{_next_id()}"
            sep.location = (x + 220, y)
            node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
            fac_out = sep.outputs["Red"]

        if not fac_out:
            continue

        # ── Per-layer Bump node with individual Strength + Distance ──────
        bump = node_tree.nodes.new("ShaderNodeBump")
        bump.name = f"{TLM_PREFIX}bump_node_{_next_id()}"
        bump.location = (x + 400, y)
        bump.inputs["Strength"].default_value = layer.bump_strength
        bump.inputs["Distance"].default_value = layer.bump_distance
        _tag(bump, layer.name, "bump_node")
        node_tree.links.new(fac_out, bump.inputs["Height"])

        # Feed the incoming normal (or previous bump/mix output) into the Bump
        # Normal input so bumps perturb that baseline rather than flat shading.
        if current is not None:
            node_tree.links.new(current, bump.inputs["Normal"])

        layer_normal = bump.outputs["Normal"]

        # ── Blend with previous (vector space) ───────────────────────────
        # First bump layer AND no incoming normal → no baseline to mix
        # against. If the layer has any modulator (mask / opacity<1 / fresnel)
        # synthesize a shading-normal baseline so the modulator is respected.
        if current is None:
            if not _layer_has_first_layer_modulator(layer):
                current = layer_normal
                continue
            current = _make_baseline_normal(node_tree, x + 300, y - 150)

        mix_x = x + 600
        mix = _new_mix_vector(node_tree, layer.opacity, mix_x, y - 40,
                              layer_name=layer.name, channel="bump")
        node_tree.links.new(current, _a_socket(mix))
        node_tree.links.new(layer_normal, _b_socket(mix))

        # Reuse _set_factor for mask / clipping mask / fresnel support.
        _set_factor(node_tree, mix, layer, None, None, mix_x, y, i, uv_map,
                    channel="bump")

        current = _result_socket(mix)

    return current



_OUTPUT_CHANNEL_TO_TARGET = {
    'BASE_COLOR':   'base_color',
    'ROUGHNESS':    'roughness',
    'METALLIC':     'metallic',
    'ALPHA':        'alpha',
    # Added 2026-05-28 — direct routing to emission / transmission without
    # the use_emission+threshold workaround. The layer's primary output
    # (procedural color, paint color, fill color) drives BSDF.Emission Color
    # or BSDF.Transmission Weight respectively.
    'EMISSION':     'emission',
    'TRANSMISSION': 'transmission',
}

# Per-channel "use_<channel>" property name. Used by both routable
# (roughness/metallic/alpha) and non-routable (normal/emission/transmission/
# bump) channels — see _layer_contributes_to for the OR-logic.
_CHANNEL_USE_FLAG = {
    'roughness':    'use_roughness',
    'metallic':     'use_metallic',
    'alpha':        'use_alpha',
    'normal':       'use_normal',
    'emission':     'use_emission',
    'transmission': 'use_transmission',
    'bump':         'use_bump',
    'displacement': 'use_displacement',
}


def _build_displacement_channel(node_tree, layers, uv_map, start_x, y_base, x_step):
    """Build the displacement height stack. Each layer with use_displacement=True
    contributes a scalar height; heights are SUMMED across the stack (per-layer
    `displacement_scale` multiplies the contribution). Returns the final summed
    height socket, or None if no layer contributes.

    Unlike _build_bump_channel (which has per-layer Bump nodes with their own
    Strength/Distance and blends as Normal vectors), displacement uses a single
    cumulative ADD chain on the scalar fac. Modulators (mask / opacity / fresnel)
    are NOT applied here — they'd require treating displacement as a full
    channel pipeline with per-layer mix nodes. Future enhancement.
    """
    current = None
    positions = _layer_positions(layers, start_x, y_base)

    for i, layer in enumerate(layers):
        if layer.layer_type == "ADJUSTMENT":
            continue
        if not _layer_contributes_to(layer, 'displacement'):
            continue

        x, y = positions[i]
        fac_out = None

        if layer.layer_type == "PROCEDURAL":
            fac_out = _build_proc_fac_node(node_tree, layer, f"disp_{i}", x, y, uv_map)
        elif layer.layer_type == "PAINT" and layer.image:
            tex = node_tree.nodes.new("ShaderNodeTexImage")
            tex.name = f"{TLM_PREFIX}disp_img_{_next_id()}"
            tex.image = layer.image
            tex.location = (x, y)
            try:
                tex.image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
            uv = node_tree.nodes.new("ShaderNodeUVMap")
            uv.name = f"{TLM_PREFIX}disp_uv_{_next_id()}"
            uv.uv_map = uv_map
            uv.location = (x - 220, y)
            node_tree.links.new(uv.outputs["UV"], tex.inputs["Vector"])
            sep = node_tree.nodes.new("ShaderNodeSeparateColor")
            sep.name = f"{TLM_PREFIX}disp_sep_{_next_id()}"
            sep.location = (x + 220, y)
            node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
            fac_out = sep.outputs["Red"]

        if fac_out is None:
            continue

        # Apply per-layer displacement_scale via a Multiply (skip when 1.0)
        scale = getattr(layer, 'displacement_scale', 1.0)
        if abs(scale - 1.0) > 1e-4:
            scl = node_tree.nodes.new("ShaderNodeMath")
            scl.operation = 'MULTIPLY'
            scl.name = f"{TLM_PREFIX}disp_scale_{_next_id()}"
            scl.location = (x + 380, y)
            node_tree.links.new(fac_out, scl.inputs[0])
            scl.inputs[1].default_value = scale
            fac_out = scl.outputs["Value"]

        # Accumulate via ADD
        if current is None:
            current = fac_out
        else:
            add = node_tree.nodes.new("ShaderNodeMath")
            add.operation = 'ADD'
            add.use_clamp = False  # heights can exceed [0,1]; midlevel clamps later
            add.name = f"{TLM_PREFIX}disp_add_{_next_id()}"
            add.location = (x + 540, y)
            node_tree.links.new(current, add.inputs[0])
            node_tree.links.new(fac_out, add.inputs[1])
            current = add.outputs["Value"]

    return current


def _layer_contributes_to(layer, channel_id):
    """Resolve whether `layer` contributes to `channel_id`.

    Cumulative semantics (OR-logic):
    - output_channel = main routing target. Always contributes to that
      one channel (BASE_COLOR / ROUGHNESS / METALLIC / ALPHA).
    - use_<channel> toggles = additional channels. A layer routed to
      ROUGHNESS with use_metallic=True drives BOTH roughness AND metallic.
      A layer routed to BASE_COLOR with use_normal=True drives base color
      AND normal map.

    base_color has no use_base_color flag — it's reachable only as a
    routing target (the most common case anyway).

    Legacy 'AUTO' (older .blend files) maps to 'BASE_COLOR'.
    """
    out_ch = getattr(layer, 'output_channel', 'BASE_COLOR')
    if out_ch == 'AUTO':
        out_ch = 'BASE_COLOR'

    # Main routing target — always contributes here.
    target = _OUTPUT_CHANNEL_TO_TARGET.get(out_ch, 'base_color')
    if target == channel_id:
        return True

    # Additional channel via use_<channel> toggle (cumulative).
    flag = _CHANNEL_USE_FLAG.get(channel_id)
    if flag is not None:
        return getattr(layer, flag, False)

    return False


def _channel_used(layers, flag_attr):
    """Check if any layer enables the channel.

    With output_channel routing, a layer with `use_metallic=False` but
    `output_channel='METALLIC'` still drives metallic. Resolve via
    _layer_contributes_to so the build-or-skip decision matches reality.
    """
    flag_to_channel = {
        'use_roughness': 'roughness', 'use_metallic': 'metallic',
        'use_normal': 'normal', 'use_emission': 'emission',
        'use_transmission': 'transmission', 'use_alpha': 'alpha',
        'use_bump': 'bump', 'use_displacement': 'displacement',
    }
    channel_id = flag_to_channel.get(flag_attr)
    if channel_id is None:
        # Unknown flag — fall back to legacy direct attribute check.
        return any(getattr(l, flag_attr, False) for l in layers)
    return any(_layer_contributes_to(l, channel_id) for l in layers)


