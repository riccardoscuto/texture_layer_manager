"""
compositing.py
The core engine of Texture Layer Manager.

Builds a node tree per PBR channel (Base Color, Roughness, Metallic,
Normal Map, Emission) and connects each chain to the correct input on
the Principled BSDF. Channels are opt-in per layer — only channels that
have at least one layer using them are built.
"""

import bpy

TLM_PREFIX = "TLM_"

# Deterministic node counter — resets each rebuild so names are stable
_node_counter = 0

def _next_id():
    """Return a sequential int that's stable across rebuilds."""
    global _node_counter
    _node_counter += 1
    return _node_counter

# ── Blend mode mapping ────────────────────────────────────────────────────────

BLEND_TO_MIX_MODE = {
    "MIX": "MIX", "MULTIPLY": "MULTIPLY", "SCREEN": "SCREEN",
    "OVERLAY": "OVERLAY", "ADD": "ADD", "SUBTRACT": "SUBTRACT",
    "DIFFERENCE": "DIFFERENCE", "DIVIDE": "DIVIDE", "DARKEN": "DARKEN",
    "LIGHTEN": "LIGHTEN", "COLOR_DODGE": "DODGE", "COLOR_BURN": "BURN",
    "SOFT_LIGHT": "SOFT_LIGHT", "HARD_LIGHT": "HARD_LIGHT",
    "LINEAR_LIGHT": "LINEAR_LIGHT", "EXCLUSION": "EXCLUSION",
    "HUE": "HUE", "SATURATION": "SATURATION", "COLOR": "COLOR",
    "LUMINOSITY": "VALUE",
}

# PBR channel descriptors: (property_flag, image_prop, bsdf_input, is_normal, is_scalar)
CHANNELS = [
    # id           flag             img_prop               bsdf_input      normal scalar
    ("base_color", None,            "image_name",          "Base Color",   False, False),
    ("roughness",  "use_roughness", "roughness_image_name","Roughness",    False, True),
    ("metallic",   "use_metallic",  "metallic_image_name", "Metallic",     False, True),
    ("normal",     "use_normal",    "normal_image_name",   "Normal",       True,  False),
    ("emission",   "use_emission",  "emission_image_name", "Emission Color",False, False),
]


def _use_new_mix():
    return bpy.app.version >= (4, 0, 0)


# ── Node helpers ──────────────────────────────────────────────────────────────

def _save_node_positions(node_tree):
    """Save positions of all TLM nodes before rebuild."""
    positions = {}
    for n in node_tree.nodes:
        if n.name.startswith(TLM_PREFIX):
            positions[n.name] = (n.location.x, n.location.y)
    return positions


def _restore_node_positions(node_tree, positions):
    """Restore saved positions to TLM nodes after rebuild."""
    if not positions:
        return
    for n in node_tree.nodes:
        if n.name in positions:
            n.location.x, n.location.y = positions[n.name]


def _save_custom_links(node_tree):
    """Save links between custom (non-TLM) nodes and TLM nodes.

    Returns a list of tuples:
        (custom_node_name, custom_socket_name, custom_is_output,
         tlm_node_name, tlm_socket_name)
    so they can be restored after TLM nodes are rebuilt.

    Links to/from Principled BSDF and Material Output are EXCLUDED because
    those are managed by TLM itself — restoring them would overwrite the
    newly built chain connections (e.g. Fill→BSDF overwriting Mix→BSDF).
    """
    # Node types that TLM manages connections to — never restore these
    _MANAGED_TYPES = {'BSDF_PRINCIPLED', 'OUTPUT_MATERIAL'}

    saved = []
    for link in node_tree.links:
        from_tlm = link.from_node.name.startswith(TLM_PREFIX)
        to_tlm   = link.to_node.name.startswith(TLM_PREFIX)

        if from_tlm and not to_tlm:
            # TLM output → custom input — skip if target is BSDF/MatOutput
            if link.to_node.type in _MANAGED_TYPES:
                continue
            saved.append((
                link.to_node.name,   link.to_socket.name,   False,
                link.from_node.name, link.from_socket.name,
            ))
        elif to_tlm and not from_tlm:
            # Custom output → TLM input — skip if source is BSDF/MatOutput
            if link.from_node.type in _MANAGED_TYPES:
                continue
            saved.append((
                link.from_node.name, link.from_socket.name, True,
                link.to_node.name,   link.to_socket.name,
            ))
    return saved


def _restore_custom_links(node_tree, saved_links):
    """Re-create links between custom nodes and rebuilt TLM nodes."""
    for (cust_name, cust_sock, cust_is_output,
         tlm_name, tlm_sock) in saved_links:
        cust_node = node_tree.nodes.get(cust_name)
        tlm_node  = node_tree.nodes.get(tlm_name)
        if cust_node is None or tlm_node is None:
            continue
        try:
            if cust_is_output:
                # custom output → TLM input
                node_tree.links.new(
                    cust_node.outputs[cust_sock],
                    tlm_node.inputs[tlm_sock],
                )
            else:
                # TLM output → custom input
                node_tree.links.new(
                    tlm_node.outputs[tlm_sock],
                    cust_node.inputs[cust_sock],
                )
        except (KeyError, IndexError):
            # Socket no longer exists — skip silently
            pass


def _clear_tlm_nodes(node_tree):
    to_remove = [n for n in node_tree.nodes if n.name.startswith(TLM_PREFIX)]
    for n in to_remove:
        node_tree.nodes.remove(n)


def _new_img_tex(node_tree, image, uv_map, x, y, colorspace="sRGB", layer=None):
    """Create an Image Texture node. If layer.use_triplanar, uses triplanar projection."""
    if layer and getattr(layer, 'use_triplanar', False):
        return _new_triplanar_tex(node_tree, image, x, y, colorspace,
                                  layer.triplanar_scale, layer.triplanar_sharpness)

    node = node_tree.nodes.new("ShaderNodeTexImage")
    node.name = f"{TLM_PREFIX}img_{image.name}_{_next_id()}"
    node.image = image
    node.location = (x, y)
    if colorspace == "Non-Color":
        try:
            node.image.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
    uv = node_tree.nodes.new("ShaderNodeUVMap")
    uv.name = f"{TLM_PREFIX}uv_{_next_id()}"
    uv.uv_map = uv_map
    uv.location = (x - 220, y)
    node_tree.links.new(uv.outputs["UV"], node.inputs["Vector"])
    return node


def _new_triplanar_tex(node_tree, image, x, y, colorspace="sRGB", scale=1.0, sharpness=2.0):
    """
    Triplanar projection: samples the image from X, Y, Z axes and blends
    them based on the surface normal. Works on any mesh without UV unwrap.

    Returns a node whose "Color" output is the blended triplanar result.
    We use a frame node as a pseudo-container and return the final Mix node.
    """
    # Geometry node for normal and position
    geo = node_tree.nodes.new("ShaderNodeNewGeometry")
    geo.name = f"{TLM_PREFIX}tri_geo_{_next_id()}"
    geo.location = (x - 700, y)
    # Separate XYZ from normal for blending weights
    sep_n = node_tree.nodes.new("ShaderNodeSeparateXYZ")
    sep_n.name = f"{TLM_PREFIX}tri_sep_n_{_next_id()}"
    sep_n.location = (x - 550, y)
    node_tree.links.new(geo.outputs["Normal"], sep_n.inputs["Vector"])

    # Separate XYZ from position for texture coords
    sep_p = node_tree.nodes.new("ShaderNodeSeparateXYZ")
    sep_p.name = f"{TLM_PREFIX}tri_sep_p_{_next_id()}"
    sep_p.location = (x - 550, y - 150)
    node_tree.links.new(geo.outputs["Position"], sep_p.inputs["Vector"])

    def make_axis_sample(ax_u_out, ax_v_out, label, offset_x):
        """Combine two position components into a UV vector and sample the image."""
        combine = node_tree.nodes.new("ShaderNodeCombineXYZ")
        combine.name = f"{TLM_PREFIX}tri_comb_{label}_{_next_id()}"
        combine.location = (x - 400 + offset_x, y - 300)
        combine.inputs["Z"].default_value = 0.0
        node_tree.links.new(ax_u_out, combine.inputs["X"])
        node_tree.links.new(ax_v_out, combine.inputs["Y"])

        # Scale via Mapping
        mapping = node_tree.nodes.new("ShaderNodeMapping")
        mapping.name = f"{TLM_PREFIX}tri_map_{label}_{_next_id()}"
        mapping.location = (x - 250 + offset_x, y - 300)
        mapping.inputs["Scale"].default_value = (scale, scale, scale)
        node_tree.links.new(combine.outputs["Vector"], mapping.inputs["Vector"])

        tex = node_tree.nodes.new("ShaderNodeTexImage")
        tex.name = f"{TLM_PREFIX}tri_tex_{label}_{_next_id()}"
        tex.image = image
        tex.location = (x - 80 + offset_x, y - 300)
        if colorspace == "Non-Color":
            try:
                tex.image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
        node_tree.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        return tex.outputs["Color"]

    # Sample from X axis (use Y,Z as UV)
    col_x = make_axis_sample(sep_p.outputs["Y"], sep_p.outputs["Z"], "X", 0)
    # Sample from Y axis (use X,Z as UV)
    col_y = make_axis_sample(sep_p.outputs["X"], sep_p.outputs["Z"], "Y", 20)
    # Sample from Z axis (use X,Y as UV)
    col_z = make_axis_sample(sep_p.outputs["X"], sep_p.outputs["Y"], "Z", 40)

    # Compute blending weights: abs(normal) ^ sharpness, then normalize
    def abs_pow(val_out, label, offset_x):
        abs_node = node_tree.nodes.new("ShaderNodeMath")
        abs_node.operation = 'ABSOLUTE'
        abs_node.name = f"{TLM_PREFIX}tri_abs_{label}_{_next_id()}"
        abs_node.location = (x + 100 + offset_x, y - 150)
        node_tree.links.new(val_out, abs_node.inputs[0])
        pow_node = node_tree.nodes.new("ShaderNodeMath")
        pow_node.operation = 'POWER'
        pow_node.name = f"{TLM_PREFIX}tri_pow_{label}_{_next_id()}"
        pow_node.location = (x + 220 + offset_x, y - 150)
        pow_node.inputs[1].default_value = sharpness
        node_tree.links.new(abs_node.outputs["Value"], pow_node.inputs[0])
        return pow_node.outputs["Value"]

    w_x = abs_pow(sep_n.outputs["X"], "X", 0)
    w_y = abs_pow(sep_n.outputs["Y"], "Y", 20)
    w_z = abs_pow(sep_n.outputs["Z"], "Z", 40)

    # Normalize weights: divide each by (wx + wy + wz)
    add_xy = node_tree.nodes.new("ShaderNodeMath")
    add_xy.operation = 'ADD'
    add_xy.name = f"{TLM_PREFIX}tri_addxy_{_next_id()}"
    add_xy.location = (x + 340, y - 150)
    node_tree.links.new(w_x, add_xy.inputs[0])
    node_tree.links.new(w_y, add_xy.inputs[1])

    add_xyz = node_tree.nodes.new("ShaderNodeMath")
    add_xyz.operation = 'ADD'
    add_xyz.name = f"{TLM_PREFIX}tri_addxyz_{_next_id()}"
    add_xyz.location = (x + 460, y - 150)
    node_tree.links.new(add_xy.outputs["Value"], add_xyz.inputs[0])
    node_tree.links.new(w_z, add_xyz.inputs[1])

    def norm_weight(w_out, label, offset_x):
        div = node_tree.nodes.new("ShaderNodeMath")
        div.operation = 'DIVIDE'
        div.name = f"{TLM_PREFIX}tri_div_{label}_{_next_id()}"
        div.location = (x + 580 + offset_x, y - 150)
        node_tree.links.new(w_out, div.inputs[0])
        node_tree.links.new(add_xyz.outputs["Value"], div.inputs[1])
        return div.outputs["Value"]

    nw_x = norm_weight(w_x, "X", 0)
    nw_y = norm_weight(w_y, "Y", 20)
    nw_z = norm_weight(w_z, "Z", 40)

    # Blend: result = col_x*nw_x + col_y*nw_y + col_z*nw_z
    def weighted_color(col_out, w_out, label, offset_x):
        mix = node_tree.nodes.new("ShaderNodeMixRGB") if not _use_new_mix() else node_tree.nodes.new("ShaderNodeMix")
        mix.name = f"{TLM_PREFIX}tri_wmix_{label}_{_next_id()}"
        mix.location = (x + 700 + offset_x, y - 200)
        if _use_new_mix():
            mix.data_type = 'RGBA'
            mix.blend_type = 'MIX'
            node_tree.links.new(w_out, mix.inputs["Factor"])
            _enabled_socket(mix.inputs, "A").default_value = (0, 0, 0, 1)
            node_tree.links.new(col_out, _enabled_socket(mix.inputs, "B"))
            return _enabled_socket(mix.outputs, "Result")
        else:
            node_tree.links.new(w_out, mix.inputs["Fac"])
            mix.inputs["Color1"].default_value = (0, 0, 0, 1)
            node_tree.links.new(col_out, mix.inputs["Color2"])
            return mix.outputs["Color"]

    wc_x = weighted_color(col_x, nw_x, "X", 0)
    wc_y = weighted_color(col_y, nw_y, "Y", 20)
    wc_z = weighted_color(col_z, nw_z, "Z", 40)

    # Add the three weighted colors together
    add1 = node_tree.nodes.new("ShaderNodeMixRGB") if not _use_new_mix() else node_tree.nodes.new("ShaderNodeMix")
    add1.name = f"{TLM_PREFIX}tri_add1_{_next_id()}"
    add1.location = (x + 880, y - 200)
    if _use_new_mix():
        add1.data_type = 'RGBA'
        add1.blend_type = 'ADD'
        add1.inputs["Factor"].default_value = 1.0
        node_tree.links.new(wc_x, _enabled_socket(add1.inputs, "A"))
        node_tree.links.new(wc_y, _enabled_socket(add1.inputs, "B"))
        add1_out = _enabled_socket(add1.outputs, "Result")
    else:
        add1.blend_type = 'ADD'
        add1.inputs["Fac"].default_value = 1.0
        node_tree.links.new(wc_x, add1.inputs["Color1"])
        node_tree.links.new(wc_y, add1.inputs["Color2"])
        add1_out = add1.outputs["Color"]

    add2 = node_tree.nodes.new("ShaderNodeMixRGB") if not _use_new_mix() else node_tree.nodes.new("ShaderNodeMix")
    add2.name = f"{TLM_PREFIX}tri_add2_{_next_id()}"
    add2.location = (x + 1000, y - 200)
    if _use_new_mix():
        add2.data_type = 'RGBA'
        add2.blend_type = 'ADD'
        add2.inputs["Factor"].default_value = 1.0
        node_tree.links.new(add1_out, _enabled_socket(add2.inputs, "A"))
        node_tree.links.new(wc_z, _enabled_socket(add2.inputs, "B"))
        final_out = _enabled_socket(add2.outputs, "Result")
    else:
        add2.blend_type = 'ADD'
        add2.inputs["Fac"].default_value = 1.0
        node_tree.links.new(add1_out, add2.inputs["Color1"])
        node_tree.links.new(wc_z, add2.inputs["Color2"])
        final_out = add2.outputs["Color"]

    # Return a fake "node" object with .outputs["Color"] interface.
    # Alpha is None because triplanar projection has no per-pixel alpha —
    # previously returning the Color output as "Alpha" caused the blend factor
    # to be driven by the pixel color instead of a proper alpha mask.
    class _FakeNode:
        def __init__(self, color_out):
            self.outputs = {"Color": color_out, "Alpha": None}
    return _FakeNode(final_out)


def _new_mix(node_tree, blend_mode, opacity, x, y):
    if _use_new_mix():
        node = node_tree.nodes.new("ShaderNodeMix")
        node.data_type = 'RGBA'
        node.blend_type = BLEND_TO_MIX_MODE.get(blend_mode, "MIX")
        node.inputs["Factor"].default_value = opacity
    else:
        node = node_tree.nodes.new("ShaderNodeMixRGB")
        node.blend_type = BLEND_TO_MIX_MODE.get(blend_mode, "MIX")
        node.inputs["Fac"].default_value = opacity
    node.name = f"{TLM_PREFIX}mix_{_next_id()}"
    node.location = (x, y)
    return node


def _new_mix_scalar(node_tree, opacity, x, y):
    """Mix node for scalar channels (Roughness, Metallic) — Float type."""
    if _use_new_mix():
        node = node_tree.nodes.new("ShaderNodeMix")
        node.data_type = 'FLOAT'
        node.blend_type = 'MIX'
        node.inputs["Factor"].default_value = opacity
    else:
        node = node_tree.nodes.new("ShaderNodeMixRGB")
        node.blend_type = 'MIX'
        node.inputs["Fac"].default_value = opacity
    node.name = f"{TLM_PREFIX}mix_scalar_{_next_id()}"
    node.location = (x, y)
    return node


def _enabled_socket(sockets, name):
    """Find the first *enabled* socket with the given name.

    ShaderNodeMix has multiple sockets named 'A', 'B', 'Result' — one per
    data-type (Float, Vector, Color).  Only the one matching the active
    data_type is enabled.  Falling back to sockets[name] would silently
    return the hidden Float variant, corrupting the entire chain.
    """
    for s in sockets:
        if s.name == name and s.enabled:
            return s
    # Fallback — should not happen but avoids crash
    return sockets[name]


def _factor_socket(node):
    if _use_new_mix():
        return node.inputs["Factor"]
    return node.inputs["Fac"]


def _a_socket(node):
    if _use_new_mix():
        return _enabled_socket(node.inputs, "A")
    return node.inputs["Color1"]


def _b_socket(node):
    if _use_new_mix():
        return _enabled_socket(node.inputs, "B")
    return node.inputs["Color2"]


def _result_socket(node):
    if _use_new_mix():
        return _enabled_socket(node.outputs, "Result")
    return node.outputs["Color"]


def _a_socket_scalar(node):
    if _use_new_mix():
        return _enabled_socket(node.inputs, "A")
    return node.inputs["Color1"]


def _b_socket_scalar(node):
    if _use_new_mix():
        return _enabled_socket(node.inputs, "B")
    return node.inputs["Color2"]


def _result_socket_scalar(node):
    if _use_new_mix():
        return _enabled_socket(node.outputs, "Result")
    return node.outputs["Color"]


def _new_fill(node_tree, color, x, y):
    node = node_tree.nodes.new("ShaderNodeRGB")
    node.name = f"{TLM_PREFIX}fill_{_next_id()}"
    node.outputs[0].default_value = color
    node.location = (x, y)
    return node


def _new_value(node_tree, value, x, y):
    """Single float Value node for scalar fill."""
    node = node_tree.nodes.new("ShaderNodeValue")
    node.name = f"{TLM_PREFIX}val_{_next_id()}"
    node.outputs[0].default_value = value
    node.location = (x, y)
    return node


def _apply_mask(node_tree, mix_node, mask_image, uv_map, x, y):
    mask_tex = _new_img_tex(node_tree, mask_image, uv_map, x - 440, y - 100, "Non-Color")
    sep = node_tree.nodes.new("ShaderNodeSeparateColor")
    sep.name = f"{TLM_PREFIX}sep_{_next_id()}"
    sep.location = (x - 220, y - 100)
    node_tree.links.new(mask_tex.outputs["Color"], sep.inputs["Color"])
    mult = node_tree.nodes.new("ShaderNodeMath")
    mult.operation = 'MULTIPLY'
    mult.name = f"{TLM_PREFIX}mask_mult_{_next_id()}"
    mult.location = (x - 50, y - 100)
    node_tree.links.new(sep.outputs["Red"], mult.inputs[0])
    node_tree.links.new(mult.outputs["Value"], _factor_socket(mix_node))
    return mult


# ── Layer width helper ────────────────────────────────────────────────────────

_LAYER_WIDTH = {
    # Width = full span of sub-nodes + padding.  Mix nodes now at x+280,
    # sub-nodes extend LEFT of x (UV at x-220, TC at x-500).
    'PAINT':       750,   # UV(-220)…Mix(+480) ≈ 700 + pad
    'FILL':        600,   # Fill(0)…Mix(+480)  ≈ 480 + next-layer left margin
    'ADJUSTMENT':  650,   # Adj node chains up to ~440 wide + pad
    'PROCEDURAL': 1200,   # TC(-500)…CR(+380)  ≈ 880 + pad
    'GROUP':       700,
}


_COLS_PER_ROW = 4     # layers per row before wrapping to next line
_ROW_HEIGHT   = 400   # vertical drop between rows


def _layer_positions(layers, x0, y0):
    """Return list of (x, y) positions, one per layer, wrapping into rows."""
    positions = []
    x = x0
    y = y0
    col = 0
    for layer in layers:
        positions.append((x, y))
        col += 1
        if col >= _COLS_PER_ROW:
            # Wrap: new row below, reset x
            col = 0
            x = x0
            y -= _ROW_HEIGHT
        else:
            x += _LAYER_WIDTH.get(layer.layer_type, 300)
    return positions


def _layer_x_positions(layers, x0):
    """Legacy helper — returns just x positions (used by end_x calculation)."""
    return [pos[0] for pos in _layer_positions(layers, x0, 0)]


# ── Per-channel composite builder ─────────────────────────────────────────────

def _build_channel(node_tree, layers, channel_id, uv_map, x0, y_base, x_step):
    """
    Build a compositing chain for one PBR channel.
    Returns the final output socket, or None if no layer contributes.

    channel_id: 'base_color' | 'roughness' | 'metallic' | 'normal' | 'emission'
    """
    is_scalar = channel_id in ('roughness', 'metallic')
    is_normal = channel_id == 'normal'
    is_emission = channel_id == 'emission'

    # Map channel_id → attribute names on TLM_LayerItem
    img_attr  = {
        'base_color': 'image_name',
        'roughness':  'roughness_image_name',
        'metallic':   'metallic_image_name',
        'normal':     'normal_image_name',
        'emission':   'emission_image_name',
    }[channel_id]

    flag_attr = {
        'base_color': None,      # base color is always enabled
        'roughness':  'use_roughness',
        'metallic':   'use_metallic',
        'normal':     'use_normal',
        'emission':   'use_emission',
    }[channel_id]

    current = None
    prev_alpha = None
    positions = _layer_positions(layers, x0, y_base)

    for i, layer in enumerate(layers):
        x, y = positions[i]

        # Adjustment layers only affect base color
        if layer.layer_type == "ADJUSTMENT":
            if channel_id == 'base_color' and current is not None:
                current = _apply_adjustment(node_tree, layer, current, x, y)
            continue

        # Skip layers that don't contribute to this channel
        if flag_attr and not getattr(layer, flag_attr, False):
            # Still update prev_alpha from base color image
            if channel_id == 'base_color' and layer.layer_type == "PAINT" and layer.image:
                tex = _new_img_tex(node_tree, layer.image, uv_map, x, y, layer=layer)
                if current is None:
                    current = tex.outputs["Color"]
                    prev_alpha = tex.outputs["Alpha"]
                else:
                    mix = _new_mix(node_tree, layer.blend_mode, layer.opacity, x + 280, y - 40)
                    node_tree.links.new(current, _a_socket(mix))
                    node_tree.links.new(tex.outputs["Color"], _b_socket(mix))
                    _set_factor(node_tree, mix, layer, tex.outputs["Alpha"], prev_alpha, x + 280, y, i, uv_map)
                    current = _result_socket(mix)
                    prev_alpha = tex.outputs["Alpha"]
            continue

        img_name = getattr(layer, img_attr, "")
        img = bpy.data.images.get(img_name) if img_name else None

        # ── Determine layer color/value output ────────────────────────────
        if layer.layer_type == "PAINT":
            if channel_id == 'base_color':
                if not layer.image:
                    continue
                cs = "sRGB"
            elif is_emission:
                # Use the painted image as emission source — this is the key workflow:
                # whatever you paint becomes the emission. Falls back to emission_color fill
                # if no image exists.
                if layer.image:
                    tex = _new_img_tex(node_tree, layer.image, uv_map, x, y, "sRGB", layer=layer)
                    layer_out = tex.outputs["Color"]
                    layer_alpha = tex.outputs["Alpha"]
                else:
                    fn = _new_fill(node_tree, layer.emission_color, x, y)
                    layer_out = fn.outputs["Color"]
                    layer_alpha = None
                # Don't fall through to generic path below
                if current is None:
                    current = layer_out
                    prev_alpha = layer_alpha
                    continue
                mix_x = x + 280
                mix = _new_mix(node_tree, layer.blend_mode, layer.opacity, mix_x, y - 40)
                node_tree.links.new(current, _a_socket(mix))
                node_tree.links.new(layer_out, _b_socket(mix))
                _set_factor(node_tree, mix, layer, layer_alpha, prev_alpha, mix_x, y, i, uv_map)
                current = _result_socket(mix)
                prev_alpha = layer_alpha
                continue
            else:
                if not img:
                    continue
                cs = "Non-Color"

            target_img = layer.image if channel_id == 'base_color' else img
            tex = _new_img_tex(node_tree, target_img, uv_map, x, y, cs, layer=layer)
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
            if channel_id == 'base_color':
                fn = _new_fill(node_tree, layer.fill_color, x, y)
                layer_out = fn.outputs["Color"]
            elif is_scalar:
                # Prefer an assigned image over the scalar fill value.
                # "New" button in the PBR panel creates and assigns an image;
                # if one exists we must read it or the user sees no effect.
                img_name = getattr(layer, img_attr, "")
                img = bpy.data.images.get(img_name) if img_name else None
                if img:
                    print(f"[TLM] FILL {channel_id}: using image '{img.name}' for layer '{layer.name}'")
                    tex = _new_img_tex(node_tree, img, uv_map, x, y, "Non-Color", layer=layer)
                    sep = node_tree.nodes.new("ShaderNodeSeparateColor")
                    sep.name = f"{TLM_PREFIX}sep_scalar_{_next_id()}"
                    sep.location = (x + 220, y)
                    node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
                    layer_out = sep.outputs["Red"]
                else:
                    fill_val = layer.roughness_fill if channel_id == 'roughness' else layer.metallic_fill
                    print(f"[TLM] FILL {channel_id}: no image, using fill value {fill_val} for layer '{layer.name}'")
                    vn = _new_value(node_tree, fill_val, x, y)
                    layer_out = vn.outputs["Value"]
            elif is_emission:
                # Prefer an assigned image over the emission_color fill
                img_name = getattr(layer, img_attr, "")
                img = bpy.data.images.get(img_name) if img_name else None
                if img:
                    print(f"[TLM] FILL emission: using image '{img.name}' for layer '{layer.name}'")
                    tex = _new_img_tex(node_tree, img, uv_map, x, y, "sRGB", layer=layer)
                    layer_out = tex.outputs["Color"]
                else:
                    print(f"[TLM] FILL emission: no image, using emission_color for layer '{layer.name}'")
                    fn = _new_fill(node_tree, layer.emission_color, x, y)
                    layer_out = fn.outputs["Color"]
            elif is_normal:
                # Normal map image on a Fill layer
                img_name = getattr(layer, img_attr, "")
                img = bpy.data.images.get(img_name) if img_name else None
                if img:
                    print(f"[TLM] FILL normal: using image '{img.name}' for layer '{layer.name}'")
                    tex = _new_img_tex(node_tree, img, uv_map, x, y, "Non-Color", layer=layer)
                    layer_out = tex.outputs["Color"]
                else:
                    print(f"[TLM] FILL normal: no image assigned, skipping layer '{layer.name}'")
                    continue
            else:
                continue

        elif layer.layer_type == "PROCEDURAL":
            if channel_id == 'base_color':
                p_color, p_alpha = _build_procedural_node(node_tree, layer, uv_map, x, y)
                if p_color is None:
                    continue
                layer_out   = p_color
                layer_alpha = p_alpha
            elif is_emission and getattr(layer, 'use_emission', False):
                # Build Fac mask from procedural pattern, then use emission_color
                # as the glow color. The Fac controls WHERE it glows, not what color.
                fac_out = _build_proc_fac_node(
                    node_tree, layer, f"emis_{i}", x, y, uv_map
                )
                if fac_out is None:
                    continue

                threshold = getattr(layer, 'proc_emission_threshold', 0.0)
                if threshold > 0.0:
                    # Less Than binary mask: Distance < threshold → 1, else → 0
                    # This creates sharp, clean crack emission without Power falloff
                    lt = node_tree.nodes.new("ShaderNodeMath")
                    lt.operation = 'LESS_THAN'
                    lt.name = f"{TLM_PREFIX}emis_lt_{i}"
                    lt.location = (x + 100, y - 100)
                    node_tree.links.new(fac_out, lt.inputs[0])
                    lt.inputs[1].default_value = threshold
                    mask_out = lt.outputs["Value"]
                else:
                    # Original approach: Invert + Power sharpening
                    # Invert Fac: for Distance to Edge, Fac=0 at edges (where we
                    # WANT emission), Fac=1 at centers. Invert so mask=1 at edges.
                    invert = node_tree.nodes.new("ShaderNodeMath")
                    invert.operation = 'SUBTRACT'
                    invert.name = f"{TLM_PREFIX}emis_inv_{i}"
                    invert.location = (x + 100, y - 100)
                    invert.inputs[0].default_value = 1.0
                    node_tree.links.new(fac_out, invert.inputs[1])
                    invert.use_clamp = True
                    # Sharpen mask with Power (contrast controls sharpness)
                    contrast = getattr(layer, 'proc_contrast', 0.5)
                    exponent = 1.0 + contrast * 8.0  # 1.0 -> 9.0
                    power = node_tree.nodes.new("ShaderNodeMath")
                    power.operation = 'POWER'
                    power.name = f"{TLM_PREFIX}emis_pow_{i}"
                    power.location = (x + 240, y - 100)
                    node_tree.links.new(invert.outputs["Value"], power.inputs[0])
                    power.inputs[1].default_value = exponent
                    power.use_clamp = True
                    mask_out = power.outputs["Value"]

                # Mix: lerp(black, emission_color, mask) = emission_color * mask
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
            elif is_scalar and getattr(layer, flag_attr, False):
                # Drive roughness/metallic from the procedural Fac output.
                # The fill value acts as a multiplier so the user can dial in intensity.
                fac_out = _build_proc_fac_node(node_tree, layer, f"scalar_{channel_id}_{i}", x, y, uv_map)
                if fac_out is None:
                    continue
                fill_val = layer.roughness_fill if channel_id == 'roughness' else layer.metallic_fill
                scale = node_tree.nodes.new("ShaderNodeMath")
                scale.operation = 'MULTIPLY'
                scale.use_clamp = True
                scale.name = f"{TLM_PREFIX}proc_scalar_{channel_id}_{i}"
                scale.location = (x + 120, y)
                node_tree.links.new(fac_out, scale.inputs[0])
                scale.inputs[1].default_value = fill_val
                layer_out   = scale.outputs["Value"]
                layer_alpha = None
            else:
                continue

        else:
            continue

        # ── Mix with current ──────────────────────────────────────────────
        if current is None:
            # For emission: mix with black so opacity=0 produces no emission
            if is_emission:
                black = _new_fill(node_tree, (0.0, 0.0, 0.0, 1.0),
                                  x - 100, y - 80)
                black.name = f"{TLM_PREFIX}emission_black_{i}"
                mix_x = x + 280
                mix = _new_mix(node_tree, 'MIX', layer.opacity, mix_x, y - 40)
                node_tree.links.new(black.outputs["Color"], _a_socket(mix))
                node_tree.links.new(layer_out, _b_socket(mix))
                current = _result_socket(mix)
                prev_alpha = layer_alpha
            else:
                current = layer_out
                prev_alpha = layer_alpha if not is_scalar else None
            continue

        mix_x = x + 280
        if is_scalar:
            mix = _new_mix_scalar(node_tree, layer.opacity, mix_x, y - 40)
            node_tree.links.new(current, _a_socket_scalar(mix))
            node_tree.links.new(layer_out, _b_socket_scalar(mix))
            current = _result_socket_scalar(mix)
        else:
            mix = _new_mix(node_tree, layer.blend_mode, layer.opacity, mix_x, y - 40)
            node_tree.links.new(current, _a_socket(mix))
            node_tree.links.new(layer_out, _b_socket(mix))
            _set_factor(node_tree, mix, layer, layer_alpha, prev_alpha, mix_x, y, i, uv_map)
            current = _result_socket(mix)
            prev_alpha = layer_alpha

    # Normal map needs a Normal Map node wrapper
    if is_normal and current is not None:
        nm = node_tree.nodes.new("ShaderNodeNormalMap")
        nm.name = f"{TLM_PREFIX}normalmap_{_next_id()}"
        # Place after the last layer column
        last_x = positions[-1][0] if positions else x0
        end_pos = last_x + _LAYER_WIDTH.get(layers[-1].layer_type, 300)
        nm.location = (end_pos, y_base)
        nm.inputs["Strength"].default_value = 1.0
        node_tree.links.new(current, nm.inputs["Color"])
        current = nm.outputs["Normal"]

    return current


def _set_factor(node_tree, mix_node, layer, layer_alpha, prev_alpha, x, y, i, uv_map="UVMap"):
    """Wire up the blend factor for a color mix node."""
    if layer.use_mask and layer.mask_image:
        # FIX: pass uv_map instead of hardcoded None — previously caused UVMap node
        # to be created with uv.uv_map = None (silently used active UV instead of
        # the material's configured UV map).
        mult = _apply_mask(node_tree, mix_node, layer.mask_image, uv_map, x, y)
        if mult:
            mult.inputs[1].default_value = layer.opacity
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
            node_tree.links.new(op.outputs["Value"], _factor_socket(mix_node))
        else:
            node_tree.links.new(prev_alpha, clip.inputs[0])
            clip.inputs[1].default_value = layer.opacity
            node_tree.links.new(clip.outputs["Value"], _factor_socket(mix_node))
    else:
        if layer_alpha and layer.blend_mode == "MIX":
            am = node_tree.nodes.new("ShaderNodeMath")
            am.operation = 'MULTIPLY'
            am.name = f"{TLM_PREFIX}alpha_mult_{i}"
            am.location = (x - 160, y - 180)
            node_tree.links.new(layer_alpha, am.inputs[0])
            am.inputs[1].default_value = layer.opacity
            node_tree.links.new(am.outputs["Value"], _factor_socket(mix_node))
        else:
            _factor_socket(mix_node).default_value = layer.opacity

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
    node_tree.links.new(mapping_out, noise.inputs["Vector"])

    # Mix(Vector, Linear Light) with low factor to blend distortion into coordinates
    # Formula: result = lerp(original_coords, LinearLight(original, noise), Factor)
    # Low factor = subtle warping, high factor = strong warping
    mix = node_tree.nodes.new("ShaderNodeMix")
    mix.data_type = 'VECTOR'
    mix.blend_type = 'LINEAR_LIGHT'
    mix.name = f"{TLM_PREFIX}vdist_mix_{name_tag}_{_next_id()}"
    mix.location = (x - 50, y - 180)
    mix.inputs["Factor"].default_value = distortion * 0.15  # scale down for usable range
    # Find the enabled Vector A and B sockets
    a_sock = _enabled_socket(mix.inputs, "A")
    b_sock = _enabled_socket(mix.inputs, "B")
    node_tree.links.new(mapping_out, a_sock)
    node_tree.links.new(noise.outputs["Color"], b_sock)

    return _enabled_socket(mix.outputs, "Result")


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

    strength = getattr(layer, 'fresnel_strength', 1.0)
    if strength < 1.0:
        # Scale the Fresnel output: multiply by strength
        mult = node_tree.nodes.new("ShaderNodeMath")
        mult.operation = 'MULTIPLY'
        mult.name = f"{TLM_PREFIX}fresnel_str_{name_tag}_{_next_id()}"
        mult.location = (x - 50, y - 300)
        mult.use_clamp = True
        node_tree.links.new(fresnel.outputs["Fac"], mult.inputs[0])
        mult.inputs[1].default_value = strength
        return mult.outputs["Value"]

    return fresnel.outputs["Fac"]


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

    mapping = node_tree.nodes.new("ShaderNodeMapping")
    mapping.name = f"{TLM_PREFIX}proc_map_{_next_id()}"
    mapping.location = (x - 300, y)
    mapping.inputs["Location"].default_value = (
        layer.proc_offset_x, layer.proc_offset_y, layer.proc_offset_z
    )
    # Select coordinate space based on layer setting
    coord_type = getattr(layer, 'proc_coord_type', 'GENERATED')
    if coord_type == 'UV':
        uv_node = node_tree.nodes.new("ShaderNodeUVMap")
        uv_node.name = f"{TLM_PREFIX}proc_uv_{_next_id()}"
        uv_node.uv_map = uv_map
        uv_node.location = (x - 500, y - 50)
        node_tree.links.new(uv_node.outputs["UV"], mapping.inputs["Vector"])
    elif coord_type == 'OBJECT':
        node_tree.links.new(tc.outputs["Object"], mapping.inputs["Vector"])
    else:  # GENERATED (default)
        node_tree.links.new(tc.outputs["Generated"], mapping.inputs["Vector"])

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
        tex_node.inputs["Distortion"].default_value = layer.proc_distortion
        fac_out = tex_node.outputs["Fac"]

    elif pt == 'VORONOI':
        tex_node = node_tree.nodes.new("ShaderNodeTexVoronoi")
        tex_node.feature   = layer.proc_voronoi_feature
        tex_node.distance  = layer.proc_voronoi_distance
        tex_node.inputs["Scale"].default_value      = layer.proc_scale
        tex_node.inputs["Randomness"].default_value = layer.proc_randomness
        # Normalize Distance output to 0-1 range (critical for ColorRamp mapping)
        if hasattr(tex_node, 'normalize'):
            tex_node.normalize = True
        # Blender 4+ uses Distance output for F1/F2
        fac_out = tex_node.outputs.get("Distance") or tex_node.outputs[0]

    elif pt == 'WAVE':
        tex_node = node_tree.nodes.new("ShaderNodeTexWave")
        tex_node.wave_type    = layer.proc_wave_type
        tex_node.bands_direction = 'X'
        try:
            tex_node.wave_profile = layer.proc_wave_profile
        except Exception:
            pass
        tex_node.inputs["Scale"].default_value        = layer.proc_scale
        tex_node.inputs["Distortion"].default_value   = layer.proc_distortion
        tex_node.inputs["Detail"].default_value       = layer.proc_detail
        tex_node.inputs["Detail Scale"].default_value = layer.proc_wave_detail_scale
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
        tex_node.inputs["Scale"].default_value  = layer.proc_checker_scale
        tex_node.inputs["Color1"].default_value = layer.proc_color1
        tex_node.inputs["Color2"].default_value = layer.proc_color2
        tex_node.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
        tex_node.location = (x - 100, y)
        node_tree.links.new(vec_out, tex_node.inputs["Vector"])
        # Checker already outputs Color directly
        return tex_node.outputs["Color"], None

    elif pt == 'MARBLE':
        # Wave + Noise distortion → marble veins
        wave = node_tree.nodes.new("ShaderNodeTexWave")
        wave.wave_type = layer.proc_marble_wave_type
        wave.bands_direction = 'X'
        wave.name = f"{TLM_PREFIX}proc_marble_wave_{_next_id()}"
        wave.location = (x - 100, y)
        wave.inputs["Scale"].default_value = layer.proc_scale
        wave.inputs["Detail"].default_value = layer.proc_detail
        wave.inputs["Distortion"].default_value = 0.0
        node_tree.links.new(vec_out, wave.inputs["Vector"])

        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.name = f"{TLM_PREFIX}proc_marble_noise_{_next_id()}"
        noise.location = (x - 350, y - 150)
        noise.inputs["Scale"].default_value = layer.proc_scale * 2.0
        noise.inputs["Detail"].default_value = layer.proc_detail
        noise.inputs["Roughness"].default_value = layer.proc_roughness_proc
        node_tree.links.new(vec_out, noise.inputs["Vector"])

        mult = node_tree.nodes.new("ShaderNodeMath")
        mult.operation = 'MULTIPLY'
        mult.name = f"{TLM_PREFIX}proc_marble_mult_{_next_id()}"
        mult.location = (x - 200, y - 150)
        node_tree.links.new(noise.outputs["Fac"], mult.inputs[0])
        mult.inputs[1].default_value = layer.proc_marble_distortion

        # Feed noise into wave Phase Offset for organic distortion
        phase_input = wave.inputs.get("Phase Offset")
        if phase_input:
            node_tree.links.new(mult.outputs["Value"], phase_input)
        else:
            # Fallback: feed into Distortion input
            node_tree.links.new(mult.outputs["Value"], wave.inputs["Distortion"])

        fac_out = wave.outputs["Fac"]
        cr = node_tree.nodes.new("ShaderNodeValToRGB")
        cr.name = f"{TLM_PREFIX}proc_cr_{_next_id()}"
        cr.label = "Proc Color"
        cr.location = (x + 180, y)
        contrast = getattr(layer, 'proc_contrast', 0.5)
        half = contrast * 0.49
        cr.color_ramp.elements[0].position = half
        cr.color_ramp.elements[0].color = layer.proc_color1
        cr.color_ramp.elements[1].position = 1.0 - half
        cr.color_ramp.elements[1].color = layer.proc_color2
        node_tree.links.new(fac_out, cr.inputs["Fac"])
        return cr.outputs["Color"], None

    elif pt == 'CLOUDS':
        tex_node = node_tree.nodes.new("ShaderNodeTexNoise")
        tex_node.inputs["Scale"].default_value      = layer.proc_scale
        tex_node.inputs["Detail"].default_value     = layer.proc_detail
        tex_node.inputs["Roughness"].default_value  = layer.proc_roughness_proc
        tex_node.inputs["Distortion"].default_value = 0.0
        fac_out = tex_node.outputs["Fac"]

    if tex_node is None:
        return None, None

    tex_node.name = f"{TLM_PREFIX}proc_tex_{_next_id()}"
    tex_node.location = (x - 100, y)
    node_tree.links.new(vec_out, tex_node.inputs["Vector"])

    # ── ColorRamp: map Fac → Color1..Color2 ──────────────────────────────
    cr = node_tree.nodes.new("ShaderNodeValToRGB")
    cr.name = f"{TLM_PREFIX}proc_cr_{_next_id()}"
    cr.label = "Proc Color"
    cr.location = (x + 180, y)

    # Contrast controls ColorRamp stop positions:
    # contrast=0.0 → stops at (0.0, 1.0) = full soft gradient
    # contrast=0.5 → stops at (0.25, 0.75) = moderate
    # contrast=1.0 → stops at (0.49, 0.51) = razor-sharp edge
    contrast = getattr(layer, 'proc_contrast', 0.5)
    half = contrast * 0.49  # max half-range = 0.49 (never fully collapse)
    stop_lo = half
    stop_hi = 1.0 - half

    cr.color_ramp.elements[0].position = stop_lo
    cr.color_ramp.elements[0].color = layer.proc_color1
    cr.color_ramp.elements[1].position = stop_hi
    cr.color_ramp.elements[1].color = layer.proc_color2
    node_tree.links.new(fac_out, cr.inputs["Fac"])

    return cr.outputs["Color"], None

def _apply_adjustment(node_tree, layer, current_output, x, y):
    adj = layer.adj_type

    if adj == 'HUE_SAT':
        node = node_tree.nodes.new("ShaderNodeHueSaturation")
        node.name = f"{TLM_PREFIX}adj_huesat_{_next_id()}"
        node.label = "Hue/Saturation"
        node.location = (x, y)
        node.inputs["Hue"].default_value        = layer.adj_hue
        node.inputs["Saturation"].default_value = layer.adj_saturation
        node.inputs["Value"].default_value      = layer.adj_value
        node.inputs["Fac"].default_value        = 1.0
        node_tree.links.new(current_output, node.inputs["Color"])
        return node.outputs["Color"]

    elif adj == 'BRIGHT_CONTRAST':
        node = node_tree.nodes.new("ShaderNodeBrightContrast")
        node.name = f"{TLM_PREFIX}adj_bc_{_next_id()}"
        node.label = "Brightness/Contrast"
        node.location = (x, y)
        node.inputs["Bright"].default_value   = layer.adj_brightness
        node.inputs["Contrast"].default_value = layer.adj_contrast
        node_tree.links.new(current_output, node.inputs["Color"])
        return node.outputs["Color"]

    elif adj == 'LEVELS':
        mr_in = node_tree.nodes.new("ShaderNodeMapRange")
        mr_in.name = f"{TLM_PREFIX}adj_lvl_in_{_next_id()}"
        mr_in.label = "Levels In"
        mr_in.location = (x, y)
        mr_in.clamp = True
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
        # FIX: use adj_levels_gamma (renamed from adj_gamma which was overwritten
        # by the Color Balance FloatVectorProperty in properties.py)
        gamma_node.inputs["Gamma"].default_value = layer.adj_levels_gamma
        node_tree.links.new(mr_in_out, gamma_node.inputs["Color"])

        mr_out = node_tree.nodes.new("ShaderNodeMapRange")
        mr_out.name = f"{TLM_PREFIX}adj_lvl_out_{_next_id()}"
        mr_out.location = (x + 400, y)
        mr_out.clamp = True
        if hasattr(mr_out, 'data_type'):
            mr_out.data_type = 'FLOAT_VECTOR'
            mr_out.inputs["From Min"].default_value = (0.0, 0.0, 0.0)
            mr_out.inputs["From Max"].default_value = (1.0, 1.0, 1.0)
            mr_out.inputs["To Min"].default_value   = (layer.adj_out_min,) * 3
            mr_out.inputs["To Max"].default_value   = (layer.adj_out_max,) * 3
            node_tree.links.new(gamma_node.outputs["Color"], mr_out.inputs["Vector"])
            return mr_out.outputs["Vector"]
        else:
            mr_out.inputs["From Min"].default_value = 0.0
            mr_out.inputs["From Max"].default_value = 1.0
            mr_out.inputs["To Min"].default_value   = layer.adj_out_min
            mr_out.inputs["To Max"].default_value   = layer.adj_out_max
            node_tree.links.new(gamma_node.outputs["Color"], mr_out.inputs["Value"])
            return mr_out.outputs["Result"]

    elif adj == 'COLOR_BALANCE':
        # Lift/Gamma/Gain implemented as:
        # output = gain * (color * lift)^(1/gamma)
        # Using Blender's Mix + Gamma + Math nodes

        # Step 1: Apply Lift (multiply)
        lift_node = node_tree.nodes.new("ShaderNodeMixRGB") if not _use_new_mix() else node_tree.nodes.new("ShaderNodeMix")
        lift_node.name = f"{TLM_PREFIX}adj_lift_{_next_id()}"
        lift_node.label = "Lift"
        lift_node.location = (x, y)
        if _use_new_mix():
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
        # Use average of gamma RGB as scalar gamma
        g = layer.adj_gamma
        gamma_val = (g[0] + g[1] + g[2]) / 3.0
        gamma_node.inputs["Gamma"].default_value = max(0.001, gamma_val)
        node_tree.links.new(lift_out, gamma_node.inputs["Color"])

        # Step 3: Apply Gain (multiply again)
        gain_node = node_tree.nodes.new("ShaderNodeMixRGB") if not _use_new_mix() else node_tree.nodes.new("ShaderNodeMix")
        gain_node.name = f"{TLM_PREFIX}adj_gain_{_next_id()}"
        gain_node.label = "Gain"
        gain_node.location = (x + 440, y)
        if _use_new_mix():
            gain_node.data_type = 'RGBA'
            gain_node.blend_type = 'MULTIPLY'
            gain_node.inputs["Factor"].default_value = 1.0
            _enabled_socket(gain_node.inputs, "B").default_value = (*layer.adj_gain, 1.0)
            node_tree.links.new(gamma_node.outputs["Color"], _enabled_socket(gain_node.inputs, "A"))
            return _enabled_socket(gain_node.outputs, "Result")
        else:
            gain_node.blend_type = 'MULTIPLY'
            gain_node.inputs["Fac"].default_value = 1.0
            gain_node.inputs["Color2"].default_value = (*layer.adj_gain, 1.0)
            node_tree.links.new(gamma_node.outputs["Color"], gain_node.inputs["Color1"])
            return gain_node.outputs["Color"]

    elif adj == 'CURVES':
        node = node_tree.nodes.new("ShaderNodeRGBCurve")
        node.name = f"{TLM_PREFIX}adj_curves_{_next_id()}"
        node.label = "Curves"
        node.location = (x, y)
        node.inputs["Fac"].default_value = 1.0
        node_tree.links.new(current_output, node.inputs["Color"])

        # Manipulate the combined (C) curve — index 3
        curve = node.mapping.curves[3]
        # Default has 2 points: (0,0) and (1,1)
        p0 = curve.points[0]
        p1 = curve.points[1]
        p0.location = (0.0, layer.adj_curve_black_point)
        p1.location = (1.0, layer.adj_curve_white_point)

        contrast = layer.adj_curve_contrast
        brightness = layer.adj_curve_brightness
        if abs(contrast) > 0.001 or abs(brightness) > 0.001:
            shadow_y = max(0.0, min(1.0, 0.25 - contrast * 0.25 + brightness * 0.25))
            highlight_y = max(0.0, min(1.0, 0.75 + contrast * 0.25 + brightness * 0.25))
            curve.points.new(0.25, shadow_y)
            curve.points.new(0.75, highlight_y)

        node.mapping.update()
        return node.outputs["Color"]

    return current_output


# ── Group compositing ─────────────────────────────────────────────────────────

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


def rebuild_node_tree(material):
    # Cancel any pending deferred rebuild — this explicit call supersedes it.
    from . import properties
    properties.cancel_pending_rebuild()

    # Reset deterministic counter so node names match between rebuilds
    global _node_counter
    _node_counter = 0

    tlm = material.tlm
    node_tree = material.node_tree

    if node_tree is None:
        material.use_nodes = True
        node_tree = material.node_tree

    # Protect all referenced images BEFORE clearing nodes — prevents Blender GC
    # from collecting images that become temporarily unreferenced during rebuild
    import bpy as _bpy
    for layer in tlm.layers:
        if layer.layer_type == "PAINT" and layer.image_name:
            img = _bpy.data.images.get(layer.image_name)
            if img:
                img.use_fake_user = True
        # Also protect PBR channel images (roughness, metallic, normal, emission)
        for attr in ('roughness_image_name', 'metallic_image_name',
                     'normal_image_name', 'emission_image_name'):
            iname = getattr(layer, attr, "")
            if iname:
                pimg = _bpy.data.images.get(iname)
                if pimg:
                    pimg.use_fake_user = True

    _saved_positions = _save_node_positions(node_tree)
    _saved_custom_links = _save_custom_links(node_tree)
    _clear_tlm_nodes(node_tree)

    all_layers = list(reversed(tlm.layers))
    group_children = {}
    for layer in all_layers:
        if layer.group_name:
            group_children.setdefault(layer.group_name, []).append(layer)

    # Build root layer list — Groups are composited as a unit (not expanded flat)
    # This preserves group alpha for Clipping Mask support
    root_layers = [l for l in all_layers if l.visible and not l.group_name]

    if not root_layers:
        return

    uv_map  = tlm.uv_map or "UVMap"
    start_x = -1200
    x_step  = 300  # default for _build_base_color / _build_bump_channel

    # For PBR channels we still need a fully expanded list
    expanded = []
    for layer in root_layers:
        if layer.layer_type == "GROUP":
            children = [l for l in group_children.get(layer.name, []) if l.visible]
            expanded.extend(children)
        else:
            expanded.append(layer)

    bsdf = _find_bsdf(node_tree)
    if not bsdf:
        return

    # Calculate where the chain ends so BSDF + passthrough nodes go to the right
    # With grid layout, find the maximum x + width across ALL rows
    def _max_end_x(layers, x0):
        if not layers:
            return x0
        positions = _layer_positions(layers, x0, 0)
        return max(px + _LAYER_WIDTH.get(layers[j].layer_type, 300)
                   for j, (px, _py) in enumerate(positions))

    end_x = max(_max_end_x(root_layers, start_x),
                _max_end_x(expanded, start_x)) + 100

    # ── Channel y positions — spaced 400px apart ──
    ch_y = {
        'base_color': 400,
        'roughness':    0,
        'metallic':  -400,
        'normal':    -800,
        'emission': -1200,
        'bump':     -1600,
    }

    # ── Base Color — built from root_layers to preserve GROUP alpha for clipping mask ─
    bc_out = _build_base_color(node_tree, root_layers, group_children, uv_map, start_x, ch_y['base_color'], x_step)
    if bc_out:
        node_tree.links.new(bc_out, bsdf.inputs["Base Color"])

    # ── Roughness ─────────────────────────────────────────────────────────────
    if _channel_used(expanded, 'use_roughness'):
        r_out = _build_channel(node_tree, expanded, 'roughness', uv_map, start_x, ch_y['roughness'], x_step)
        if r_out:
            passthrough = node_tree.nodes.new("ShaderNodeMath")
            passthrough.operation = 'ADD'
            passthrough.name = f"{TLM_PREFIX}rough_pass"
            passthrough.inputs[1].default_value = 0.0
            passthrough.use_clamp = True
            passthrough.location = (end_x, ch_y['roughness'])
            node_tree.links.new(r_out, passthrough.inputs[0])
            _link_to_bsdf(node_tree, passthrough.outputs["Value"], bsdf,
                          ["Roughness", "Specular Roughness"], "roughness")

    # ── Metallic ──────────────────────────────────────────────────────────────
    if _channel_used(expanded, 'use_metallic'):
        m_out = _build_channel(node_tree, expanded, 'metallic', uv_map, start_x, ch_y['metallic'], x_step)
        if m_out:
            passthrough = node_tree.nodes.new("ShaderNodeMath")
            passthrough.operation = 'ADD'
            passthrough.name = f"{TLM_PREFIX}metal_pass"
            passthrough.inputs[1].default_value = 0.0
            passthrough.use_clamp = True
            passthrough.location = (end_x, ch_y['metallic'])
            node_tree.links.new(m_out, passthrough.inputs[0])
            _link_to_bsdf(node_tree, passthrough.outputs["Value"], bsdf,
                          ["Metallic", "Metalness"], "metallic")

    # ── Normal ────────────────────────────────────────────────────────────────
    normal_out = None
    if _channel_used(expanded, 'use_normal'):
        n_out = _build_channel(node_tree, expanded, 'normal', uv_map, start_x, ch_y['normal'], x_step)
        if n_out:
            normal_out = n_out

    # ── Emission ──────────────────────────────────────────────────────────────
    if _channel_used(expanded, 'use_emission'):
        e_out = _build_channel(node_tree, expanded, 'emission', uv_map, start_x, ch_y['emission'], x_step)
        if e_out:
            _link_to_bsdf(node_tree, e_out, bsdf,
                          ["Emission Color", "Emission", "emission"], "emission")
            # Use max emission strength weighted by opacity
            strengths = [(l.emission_strength * l.opacity) for l in expanded if l.use_emission]
            if strengths:
                val = node_tree.nodes.new("ShaderNodeValue")
                val.name = f"{TLM_PREFIX}emission_strength"
                val.outputs[0].default_value = max(strengths)
                val.location = (end_x, ch_y['emission'])
                _link_to_bsdf(node_tree, val.outputs[0], bsdf,
                              ["Emission Strength", "emission_strength"], "emission_strength")

    # ── Bump ──────────────────────────────────────────────────────────────────
    bump_out = None
    if _channel_used(expanded, 'use_bump'):
        bump_out_socket = _build_bump_channel(node_tree, expanded, uv_map, start_x, ch_y['bump'], x_step)
        if bump_out_socket:
            bump_out = bump_out_socket
            # If we have both Normal Map and Bump, chain them: Normal → Bump.Normal
            if normal_out:
                bump_node = bump_out_socket.node  # the Bump node
                node_tree.links.new(normal_out, bump_node.inputs["Normal"])

    # ── Connect final normal to BSDF ─────────────────────────────────────────
    # Bump takes priority (it already incorporates the Normal Map via its Normal input)
    final_normal = bump_out or normal_out
    if final_normal:
        _link_to_bsdf(node_tree, final_normal, bsdf, ["Normal", "normal"], "normal")

    # Position BSDF and Material Output to the right of all channels
    bsdf.location = (end_x + 300, 0)
    mat_out = next((n for n in node_tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)
    if mat_out:
        mat_out.location = (end_x + 600, 0)

    # Restore user-customized node positions if they existed before rebuild
    _restore_custom_links(node_tree, _saved_custom_links)
    _restore_node_positions(node_tree, _saved_positions)

    # Ensure Blender re-evaluates the node tree after rebuild
    node_tree.update_tag()
    material.update_tag()


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
            current = _apply_adjustment(node_tree, layer, current, x, y)
            continue

        elif layer.layer_type == "GROUP":
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
            tex = _new_img_tex(node_tree, layer.image, uv_map, x, y, layer=layer)
            layer_color_out = tex.outputs["Color"]
            layer_alpha_out = tex.outputs["Alpha"]

        elif layer.layer_type == "FILL":
            fn = _new_fill(node_tree, layer.fill_color, x, y)
            layer_color_out = fn.outputs["Color"]
            layer_alpha_out = None

        elif layer.layer_type == "PROCEDURAL":
            p_color, p_alpha = _build_procedural_node(node_tree, layer, uv_map, x, y)
            if p_color is None:
                continue
            layer_color_out = p_color
            layer_alpha_out = p_alpha

        else:
            continue

        if current is None:
            current = layer_color_out
            prev_alpha = layer_alpha_out
            continue

        mix_x = x + 280
        mix = _new_mix(node_tree, layer.blend_mode, layer.opacity, mix_x, y - 40)
        node_tree.links.new(current, _a_socket(mix))
        node_tree.links.new(layer_color_out, _b_socket(mix))
        _set_factor(node_tree, mix, layer, layer_alpha_out, prev_alpha, mix_x, y, i, uv_map)
        current = _result_socket(mix)
        prev_alpha = layer_alpha_out

    return current


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

    mapping = node_tree.nodes.new("ShaderNodeMapping")
    mapping.name = f"{TLM_PREFIX}pfac_map_{name_suffix}"
    mapping.location = (x - 300, y)
    # NOTE: Scale is applied at the texture node level, not the Mapping node,
    # to match _build_procedural_node and avoid doubling the scale.
    mapping.inputs["Location"].default_value = (
        layer.proc_offset_x, layer.proc_offset_y, layer.proc_offset_z
    )
    # Select coordinate space based on layer setting
    coord_type = getattr(layer, 'proc_coord_type', 'GENERATED')
    if coord_type == 'UV':
        uv_node = node_tree.nodes.new("ShaderNodeUVMap")
        uv_node.name = f"{TLM_PREFIX}pfac_uv_{name_suffix}"
        uv_node.uv_map = uv_map
        uv_node.location = (x - 500, y - 50)
        node_tree.links.new(uv_node.outputs["UV"], mapping.inputs["Vector"])
    elif coord_type == 'OBJECT':
        node_tree.links.new(tc.outputs["Object"], mapping.inputs["Vector"])
    else:  # GENERATED (default)
        node_tree.links.new(tc.outputs["Generated"], mapping.inputs["Vector"])

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
        if hasattr(tex, 'normalize'):
            tex.normalize = True
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs.get("Distance") or tex.outputs[0]

    elif pt == 'WAVE':
        tex = node_tree.nodes.new("ShaderNodeTexWave")
        tex.wave_type = layer.proc_wave_type
        try:
            tex.wave_profile = layer.proc_wave_profile
        except Exception:
            pass
        tex.inputs["Scale"].default_value        = layer.proc_scale
        tex.inputs["Distortion"].default_value   = layer.proc_distortion
        tex.inputs["Detail"].default_value       = layer.proc_detail
        tex.inputs["Detail Scale"].default_value = layer.proc_wave_detail_scale
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs["Fac"]

    elif pt == 'CHECKER':
        tex = node_tree.nodes.new("ShaderNodeTexChecker")
        tex.inputs["Scale"].default_value = layer.proc_checker_scale
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs.get("Fac") or tex.outputs[0]

    elif pt == 'GRADIENT':
        tex = node_tree.nodes.new("ShaderNodeTexGradient")
        tex.gradient_type = layer.proc_gradient_type
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs["Fac"]

    elif pt == 'MARBLE':
        # Wave + Noise for marble fac channel
        wave = node_tree.nodes.new("ShaderNodeTexWave")
        wave.wave_type = layer.proc_marble_wave_type
        wave.bands_direction = 'X'
        wave.name = f"{TLM_PREFIX}pfac_marble_wave_{name_suffix}"
        wave.location = (x - 100, y)
        wave.inputs["Scale"].default_value = layer.proc_scale
        wave.inputs["Detail"].default_value = layer.proc_detail
        wave.inputs["Distortion"].default_value = 0.0
        node_tree.links.new(vec_out, wave.inputs["Vector"])

        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.name = f"{TLM_PREFIX}pfac_marble_noise_{name_suffix}"
        noise.location = (x - 350, y - 150)
        noise.inputs["Scale"].default_value = layer.proc_scale * 2.0
        noise.inputs["Detail"].default_value = layer.proc_detail
        noise.inputs["Roughness"].default_value = layer.proc_roughness_proc
        node_tree.links.new(vec_out, noise.inputs["Vector"])

        mult = node_tree.nodes.new("ShaderNodeMath")
        mult.operation = 'MULTIPLY'
        mult.name = f"{TLM_PREFIX}pfac_marble_mult_{name_suffix}"
        mult.location = (x - 200, y - 150)
        node_tree.links.new(noise.outputs["Fac"], mult.inputs[0])
        mult.inputs[1].default_value = layer.proc_marble_distortion

        phase_input = wave.inputs.get("Phase Offset")
        if phase_input:
            node_tree.links.new(mult.outputs["Value"], phase_input)
        else:
            node_tree.links.new(mult.outputs["Value"], wave.inputs["Distortion"])

        return wave.outputs["Fac"]

    elif pt == 'CLOUDS':
        tex = node_tree.nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value      = layer.proc_scale
        tex.inputs["Detail"].default_value     = layer.proc_detail
        tex.inputs["Roughness"].default_value  = layer.proc_roughness_proc
        tex.inputs["Distortion"].default_value = 0.0
        node_tree.links.new(vec_out, tex.inputs["Vector"])
        fac_out = tex.outputs["Fac"]

    if tex is None or fac_out is None:
        return None

    tex.name = f"{TLM_PREFIX}pfac_tex_{name_suffix}"
    tex.location = (x - 100, y)
    return fac_out


def _build_bump_channel(node_tree, layers, uv_map, start_x, y_base, x_step):
    """
    Build a combined bump/normal output from all layers that have use_bump=True.
    For Proc layers: uses the Fac output (greyscale) → Bump node.
    For Paint layers: uses the R channel of the image → Bump node.
    Multiple bump layers are blended via Mix nodes before entering the final Bump node.
    Returns a Normal socket ready to connect to BSDF Normal input.
    """
    bump_inputs = []  # list of (fac_socket, strength, distance) tuples
    positions = _layer_positions(layers, start_x, y_base)

    for i, layer in enumerate(layers):
        if not getattr(layer, 'use_bump', False):
            continue

        x, y = positions[i]
        fac_out = None

        if layer.layer_type == "PROCEDURAL":
            # Reuse shared Fac helper — avoids duplicating all the texture branches
            fac_out = _build_proc_fac_node(node_tree, layer, f"bump_{i}", x, y, uv_map)

        elif layer.layer_type == "PAINT" and layer.image:
            # Use R channel of the paint image as height
            tex = node_tree.nodes.new("ShaderNodeTexImage")
            tex.name = f"{TLM_PREFIX}bump_img_{i}"
            tex.image = layer.image
            tex.location = (x, y)
            try:
                tex.image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
            uv = node_tree.nodes.new("ShaderNodeUVMap")
            uv.name = f"{TLM_PREFIX}bump_uv_{i}"
            uv.uv_map = uv_map
            uv.location = (x - 220, y)
            node_tree.links.new(uv.outputs["UV"], tex.inputs["Vector"])
            sep = node_tree.nodes.new("ShaderNodeSeparateColor")
            sep.name = f"{TLM_PREFIX}bump_sep_{i}"
            sep.location = (x + 220, y)
            node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
            fac_out = sep.outputs["Red"]

        if fac_out:
            bump_inputs.append((fac_out, layer.bump_strength, layer.bump_distance))

    if not bump_inputs:
        return None

    # Blend multiple bump inputs by adding their Fac values, then into one Bump node
    if len(bump_inputs) == 1:
        combined_fac = bump_inputs[0][0]
        strength     = bump_inputs[0][1]
        distance     = bump_inputs[0][2]
    else:
        # Mix multiple height inputs together
        combined_fac = bump_inputs[0][0]
        last_x = positions[-1][0] if positions else start_x
        for j in range(1, len(bump_inputs)):
            add = node_tree.nodes.new("ShaderNodeMath")
            add.operation = 'ADD'
            add.name = f"{TLM_PREFIX}bump_add_{j}"
            add.location = (last_x + 300 + j * 200, y_base)
            add.use_clamp = True
            node_tree.links.new(combined_fac, add.inputs[0])
            node_tree.links.new(bump_inputs[j][0], add.inputs[1])
            combined_fac = add.outputs["Value"]
        strength = sum(b[1] for b in bump_inputs) / len(bump_inputs)
        distance = sum(b[2] for b in bump_inputs) / len(bump_inputs)

    # Final Bump node → Normal output
    bump_node = node_tree.nodes.new("ShaderNodeBump")
    bump_node.name = f"{TLM_PREFIX}bump_final"
    bump_node.label = "TLM Bump"
    last_x = positions[-1][0] if positions else start_x
    bump_node.location = (last_x + 300 + len(bump_inputs) * 200, y_base)
    bump_node.inputs["Strength"].default_value = strength
    bump_node.inputs["Distance"].default_value = distance
    node_tree.links.new(combined_fac, bump_node.inputs["Height"])

    return bump_node.outputs["Normal"]



def _channel_used(layers, flag_attr):
    """Check if any layer in the list has a channel enabled."""
    return any(getattr(l, flag_attr, False) for l in layers)


def _find_bsdf(node_tree):
    for node in node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED' and not node.name.startswith(TLM_PREFIX):
            return node
    # Create one if missing
    bsdf = node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (400, 0)
    out = next((n for n in node_tree.nodes if n.type == 'OUTPUT_MATERIAL'
                and not n.name.startswith(TLM_PREFIX)), None)
    if out:
        node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return bsdf


# ── Flatten ───────────────────────────────────────────────────────────────────

def flatten_to_single_image(material, output_image_name, resolution=(1024, 1024)):
    rebuild_node_tree(material)
    if output_image_name in bpy.data.images:
        out_img = bpy.data.images[output_image_name]
        out_img.scale(*resolution)
    else:
        out_img = bpy.data.images.new(output_image_name, *resolution, alpha=True)

    node_tree = material.node_tree
    bake_node = node_tree.nodes.new("ShaderNodeTexImage")
    bake_node.name = f"{TLM_PREFIX}bake_target"
    bake_node.image = out_img
    bake_node.location = (200, 0)
    for n in node_tree.nodes:
        n.select = False
    bake_node.select = True
    node_tree.nodes.active = bake_node
    bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'}, save_mode='INTERNAL')
    node_tree.nodes.remove(bake_node)
    return out_img


def register():
    pass

def unregister():
    pass
