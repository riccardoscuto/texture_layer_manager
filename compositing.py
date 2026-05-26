"""
compositing.py
The core engine of Texture Layer Manager.

Builds a node tree per PBR channel (Base Color, Roughness, Metallic,
Normal Map, Emission) and connects each chain to the correct input on
the Principled BSDF. Channels are opt-in per layer â€” only channels that
have at least one layer using them are built.
"""

import bpy
import time as _time

TLM_PREFIX = "TLM_"

# Prefix for shared NodeGroup datablocks (e.g. mask blur kernels).
# Kept distinct from TLM_PREFIX so `_clear_tlm_nodes` (which operates on
# material-local nodes) doesn't accidentally target NodeGroup datablocks,
# which are cleaned via `_cleanup_tlm_mask_blur_groups` instead.
TLM_GROUP_PREFIX = "TLM_maskblur_"

# Material-local, user-editable slot nodes. Intentionally does not start with
# TLM_PREFIX: _clear_tlm_nodes removes generated nodes on every rebuild, while
# these pass-through groups must survive and keep the user's internal edits.
TLM_USER_SLOT_PREFIX = "TLMUser_"
TLM_USER_SLOT_PROP = "tlm_user_slot"

_USER_SLOT_DEFS = {
    'base_color':   ("Base Color", "NodeSocketColor",  "Color"),
    'roughness':    ("Roughness",  "NodeSocketFloat",  "Value"),
    'metallic':     ("Metallic",   "NodeSocketFloat",  "Value"),
    'emission':     ("Emission",   "NodeSocketColor",  "Color"),
    'transmission': ("Transmission", "NodeSocketFloat", "Value"),
    'alpha':        ("Alpha",      "NodeSocketFloat",  "Value"),
    'normal':       ("Normal",     "NodeSocketVector", "Vector"),
}

# Deterministic node counter â€” resets each rebuild so names are stable
_node_counter = 0


def _next_id():
    """Return a sequential int that's stable across rebuilds."""
    global _node_counter
    _node_counter += 1
    return _node_counter

# â”€â”€ Blend mode mapping â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

BLEND_TO_MIX_MODE = {
    "MIX": "MIX", "MULTIPLY": "MULTIPLY", "SCREEN": "SCREEN",
    "OVERLAY": "OVERLAY", "ADD": "ADD", "SUBTRACT": "SUBTRACT",
    "DIFFERENCE": "DIFFERENCE", "DIVIDE": "DIVIDE", "DARKEN": "DARKEN",
    "LIGHTEN": "LIGHTEN", "COLOR_DODGE": "DODGE", "COLOR_BURN": "BURN",
    "SOFT_LIGHT": "SOFT_LIGHT",
    # HARD_LIGHT removed from the user-facing BLEND_MODES enum due to
    # cross-version inconsistency. Older presets that still carry the
    # identifier fall through .get(..., "MIX") to a safe default.
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
    ("transmission","use_transmission","transmission_image_name","Transmission Weight",False, True),
    ("alpha",      "use_alpha",     "alpha_image_name",    "Alpha",        False, True),
]


_USE_NEW_MIX = bpy.app.version >= (4, 0, 0)


# â”€â”€ Node helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    those are managed by TLM itself â€” restoring them would overwrite the
    newly built chain connections (e.g. Fillâ†’BSDF overwriting Mixâ†’BSDF).
    """
    # Node types that TLM manages connections to â€” never restore these
    _MANAGED_TYPES = {'BSDF_PRINCIPLED', 'OUTPUT_MATERIAL'}

    saved = []
    for link in node_tree.links:
        from_tlm = link.from_node.name.startswith(TLM_PREFIX)
        to_tlm   = link.to_node.name.startswith(TLM_PREFIX)

        if from_tlm and not to_tlm:
            # TLM output â†’ custom input â€” skip if target is BSDF/MatOutput
            if link.to_node.type in _MANAGED_TYPES:
                continue
            saved.append((
                link.to_node.name,   link.to_socket.name,   False,
                link.from_node.name, link.from_socket.name,
            ))
        elif to_tlm and not from_tlm:
            # Custom output â†’ TLM input â€” skip if source is BSDF/MatOutput
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
                # custom output â†’ TLM input
                node_tree.links.new(
                    cust_node.outputs[cust_sock],
                    tlm_node.inputs[tlm_sock],
                )
            else:
                # TLM output â†’ custom input
                node_tree.links.new(
                    tlm_node.outputs[tlm_sock],
                    cust_node.inputs[cust_sock],
                )
        except (KeyError, IndexError):
            # Socket no longer exists â€” skip silently
            pass


def _clear_tlm_nodes(node_tree):
    """Remove all TLM-managed nodes from the node tree.

    Identifies TLM nodes by either:
    1. Name starting with TLM_PREFIX (the primary mechanism), or
    2. Custom property `tlm_layer` or `tlm_role` set (defensive: covers any
       node that was tagged via _tag() but didn't get the prefix in its name).

    Without the second check, math nodes inside helper pipelines (mask
    combine, smart generator, coordinate transforms, ...) that lack a name
    assignment leak across rebuilds, leaving orphan "Multiply" / "Math"
    nodes in the shader editor.
    """
    to_remove = []
    for n in node_tree.nodes:
        if n.name.startswith(TLM_PREFIX):
            to_remove.append(n)
        elif n.get("tlm_layer") is not None or n.get("tlm_role") is not None:
            to_remove.append(n)
    for n in to_remove:
        node_tree.nodes.remove(n)


def _make_user_slot_group(group_name, label, socket_type, socket_label):
    """Create a pass-through node group that users can safely edit."""
    tree = bpy.data.node_groups.new(group_name, "ShaderNodeTree")
    tree.interface.new_socket(name="Input", in_out='INPUT', socket_type=socket_type)
    tree.interface.new_socket(name="Output", in_out='OUTPUT', socket_type=socket_type)

    gi = tree.nodes.new("NodeGroupInput")
    gi.location = (-300, 0)
    go = tree.nodes.new("NodeGroupOutput")
    go.location = (300, 0)
    tree.name = group_name
    tree["tlm_user_slot_label"] = label
    tree["tlm_user_slot_socket"] = socket_label
    _ensure_user_slot_group_contents(tree, socket_type)
    return tree


def _ensure_user_slot_group_contents(tree, socket_type):
    """Populate a fresh Custom Slot with one neutral, user-editable node.

    The slot still behaves as a pass-through, but it no longer opens as an
    empty Input -> Output wire. Artists immediately get a Math / Color /
    Vector node they can tweak or replace.
    """
    if tree is None:
        return
    if any(n.type not in {'GROUP_INPUT', 'GROUP_OUTPUT'} for n in tree.nodes):
        return

    gi = next((n for n in tree.nodes if n.type == 'GROUP_INPUT'), None)
    go = next((n for n in tree.nodes if n.type == 'GROUP_OUTPUT'), None)
    if gi is None or go is None:
        return

    for link in list(tree.links):
        try:
            tree.links.remove(link)
        except Exception:
            pass

    try:
        if socket_type == "NodeSocketFloat":
            edit = tree.nodes.new("ShaderNodeMath")
            edit.operation = 'MULTIPLY'
            edit.use_clamp = True
            edit.inputs[1].default_value = 1.0
            edit.label = "User Math"
            edit.location = (0, 0)
            tree.links.new(gi.outputs["Input"], edit.inputs[0])
            tree.links.new(edit.outputs["Value"], go.inputs["Output"])
        elif socket_type == "NodeSocketVector":
            edit = tree.nodes.new("ShaderNodeVectorMath")
            edit.operation = 'ADD'
            edit.inputs[1].default_value = (0.0, 0.0, 0.0)
            edit.label = "User Vector Math"
            edit.location = (0, 0)
            tree.links.new(gi.outputs["Input"], edit.inputs[0])
            tree.links.new(edit.outputs["Vector"], go.inputs["Output"])
        else:
            edit = tree.nodes.new("ShaderNodeHueSaturation")
            edit.label = "User Color Adjust"
            edit.location = (0, 0)
            if edit.inputs.get("Fac"):
                edit.inputs["Fac"].default_value = 1.0
            if edit.inputs.get("Hue"):
                edit.inputs["Hue"].default_value = 0.5
            if edit.inputs.get("Saturation"):
                edit.inputs["Saturation"].default_value = 1.0
            if edit.inputs.get("Value"):
                edit.inputs["Value"].default_value = 1.0
            tree.links.new(gi.outputs["Input"], edit.inputs["Color"])
            tree.links.new(edit.outputs["Color"], go.inputs["Output"])
    except Exception:
        try:
            tree.links.new(gi.outputs["Input"], go.inputs["Output"])
        except Exception:
            pass


def _find_user_slot_node(node_tree, channel_id):
    for node in node_tree.nodes:
        if node.get(TLM_USER_SLOT_PROP) == channel_id:
            return node
    legacy_name = f"{TLM_USER_SLOT_PREFIX}{channel_id}"
    return node_tree.nodes.get(legacy_name)


def _get_or_create_user_slot_node(node_tree, material, channel_id, x, y):
    slot_def = _USER_SLOT_DEFS.get(channel_id)
    if slot_def is None:
        return None
    label, socket_type, socket_label = slot_def

    node = _find_user_slot_node(node_tree, channel_id)
    if node is not None and node.type == 'GROUP':
        if node.node_tree is not None:
            node[TLM_USER_SLOT_PROP] = channel_id
            _ensure_user_slot_group_contents(node.node_tree, socket_type)
            return node

    group_name = f"{TLM_USER_SLOT_PREFIX}{material.name}_{channel_id}"
    group = bpy.data.node_groups.get(group_name)
    if group is None:
        group = _make_user_slot_group(group_name, label, socket_type, socket_label)
    else:
        _ensure_user_slot_group_contents(group, socket_type)

    node = node_tree.nodes.new("ShaderNodeGroup")
    node.name = f"{TLM_USER_SLOT_PREFIX}{channel_id}"
    node.label = f"TLM Custom {label}"
    node.node_tree = group
    node.location = (x, y)
    node[TLM_USER_SLOT_PROP] = channel_id
    return node


def _route_through_user_slot(node_tree, material, channel_id, source_socket, x, y):
    """Route a channel through a persistent user-editable pass-through group."""
    if source_socket is None:
        return None
    tlm = getattr(material, "tlm", None)
    if tlm is None or not getattr(tlm, "use_custom_slots", False):
        return source_socket

    slot = _get_or_create_user_slot_node(node_tree, material, channel_id, x, y)
    if slot is None:
        return source_socket
    in_socket = slot.inputs.get("Input")
    out_socket = slot.outputs.get("Output")
    if in_socket is None or out_socket is None:
        return source_socket
    try:
        node_tree.links.new(source_socket, in_socket)
    except Exception:
        return source_socket
    return out_socket


def _get_or_build_mask_blur_group(img):
    """Get or create the shared 5-tap cross blur NodeGroup for a given image.

    One group per image is created (image is baked into the group's
    ShaderNodeTexImage nodes and can't be parameterized via socket). Groups
    are reused across all layers/materials that sample the same image, and
    orphans are swept by `_cleanup_tlm_mask_blur_groups` after each rebuild.

    Group interface:
        Inputs:  UV (Vector), Blur (Float)
        Outputs: Value (Float 0..1)
    """
    group_name = f"{TLM_GROUP_PREFIX}{img.name}"
    existing = bpy.data.node_groups.get(group_name)
    if existing is not None:
        return existing

    tree = bpy.data.node_groups.new(group_name, 'ShaderNodeTree')
    tree.use_fake_user = True  # survive save/load even when temporarily unreferenced

    # Blender 4.0+ interface API (required for 5.0)
    tree.interface.new_socket(name="UV",    in_out='INPUT',  socket_type='NodeSocketVector')
    tree.interface.new_socket(name="Blur",  in_out='INPUT',  socket_type='NodeSocketFloat')
    tree.interface.new_socket(name="Value", in_out='OUTPUT', socket_type='NodeSocketFloat')

    gi = tree.nodes.new("NodeGroupInput")
    gi.location = (-1000, 0)
    go = tree.nodes.new("NodeGroupOutput")
    go.location = (900, 0)

    # -blur = blur Ã— -1 (shared by west + south taps)
    neg = tree.nodes.new("ShaderNodeMath")
    neg.operation = 'MULTIPLY'
    neg.inputs[1].default_value = -1.0
    neg.location = (-800, -200)
    tree.links.new(gi.outputs["Blur"], neg.inputs[0])

    # 5-tap cross kernel: (x_offset_socket_or_None, y_offset_socket_or_None, weight)
    # None = zero offset; weights sum to 1.0 (center=0.40, cardinals=0.15 each).
    taps = [
        (None,                None,                0.40),  # center
        (gi.outputs["Blur"],  None,                0.15),  # east
        (neg.outputs[0],      None,                0.15),  # west
        (None,                gi.outputs["Blur"],  0.15),  # north
        (None,                neg.outputs[0],      0.15),  # south
    ]

    weighted = []
    for i, (x_sock, y_sock, w) in enumerate(taps):
        row_y = 300 - i * 180

        combine = tree.nodes.new("ShaderNodeCombineXYZ")
        combine.location = (-600, row_y)
        if x_sock is not None:
            tree.links.new(x_sock, combine.inputs["X"])
        if y_sock is not None:
            tree.links.new(y_sock, combine.inputs["Y"])

        mp = tree.nodes.new("ShaderNodeMapping")
        mp.location = (-420, row_y)
        tree.links.new(gi.outputs["UV"], mp.inputs["Vector"])
        tree.links.new(combine.outputs["Vector"], mp.inputs["Location"])

        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        if img.colorspace_settings.name != "Non-Color":
            img.colorspace_settings.name = "Non-Color"
        tex.location = (-220, row_y)
        tree.links.new(mp.outputs["Vector"], tex.inputs["Vector"])

        sep = tree.nodes.new("ShaderNodeSeparateColor")
        sep.location = (0, row_y)
        tree.links.new(tex.outputs["Color"], sep.inputs["Color"])

        ms = tree.nodes.new("ShaderNodeMath")
        ms.operation = 'MULTIPLY'
        ms.inputs[1].default_value = w
        ms.location = (180, row_y)
        tree.links.new(sep.outputs["Red"], ms.inputs[0])
        weighted.append(ms.outputs[0])

    # Sum 5 weighted taps via a small Add-tree
    a1 = tree.nodes.new("ShaderNodeMath")
    a1.operation = 'ADD'
    a1.location = (400, 180)
    tree.links.new(weighted[0], a1.inputs[0])
    tree.links.new(weighted[1], a1.inputs[1])

    a2 = tree.nodes.new("ShaderNodeMath")
    a2.operation = 'ADD'
    a2.location = (400, -180)
    tree.links.new(weighted[2], a2.inputs[0])
    tree.links.new(weighted[3], a2.inputs[1])

    a3 = tree.nodes.new("ShaderNodeMath")
    a3.operation = 'ADD'
    a3.location = (580, 0)
    tree.links.new(a1.outputs[0], a3.inputs[0])
    tree.links.new(a2.outputs[0], a3.inputs[1])

    afinal = tree.nodes.new("ShaderNodeMath")
    afinal.operation = 'ADD'
    afinal.use_clamp = True
    afinal.location = (760, 0)
    tree.links.new(a3.outputs[0], afinal.inputs[0])
    tree.links.new(weighted[4], afinal.inputs[1])

    tree.links.new(afinal.outputs[0], go.inputs["Value"])
    return tree


def _cleanup_tlm_mask_blur_groups():
    """Remove TLM mask blur NodeGroups not referenced by any material.

    Called at the end of rebuild_node_tree. Without this, every image
    rename/bake would leak an orphan NodeGroup in bpy.data.node_groups.
    """
    used = set()
    for mat in bpy.data.materials:
        nt = mat.node_tree
        if nt is None:
            continue
        for n in nt.nodes:
            if n.type == 'GROUP' and n.node_tree is not None \
                    and n.node_tree.name.startswith(TLM_GROUP_PREFIX):
                used.add(n.node_tree.name)

    for ng in list(bpy.data.node_groups):
        if ng.name.startswith(TLM_GROUP_PREFIX) and ng.name not in used:
            bpy.data.node_groups.remove(ng)


# â”€â”€ Hot-update tagging infrastructure â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def unique_layer_name(layers, base, current=None):
    """Return a Blender-style unique layer name within a TLM layer collection."""
    base = (base or "Layer").strip() or "Layer"
    existing = {l.name for l in layers if l != current}
    if base not in existing:
        return base
    idx = 2
    while True:
        candidate = f"{base} {idx}"
        if candidate not in existing:
            return candidate
        idx += 1


def _ensure_unique_layer_names(material):
    """Deduplicate layer names so node tags/frame grouping stay unambiguous."""
    tlm = getattr(material, "tlm", None)
    if tlm is None:
        return
    seen = set()
    if len({l.name for l in tlm.layers}) == len(tlm.layers):
        for layer in tlm.layers:
            layer["_name_prev"] = layer.name
        return

    from . import properties as _props
    old_suppress = getattr(_props, "_suppress_layer_updates", False)
    _props._suppress_layer_updates = True
    try:
        for layer in tlm.layers:
            name = (layer.name or "Layer").strip() or "Layer"
            if name in seen:
                name = unique_layer_name(tlm.layers, name, current=layer)
                layer.name = name
            layer["_name_prev"] = name
            seen.add(name)
    finally:
        _props._suppress_layer_updates = old_suppress


def _tag(node, layer_name, role, **extra):
    """Stamp a node with layer identity and role for hot-update lookup."""
    node["tlm_layer"] = layer_name
    node["tlm_role"] = role
    for k, v in extra.items():
        node[f"tlm_{k}"] = v


def _find_tagged(node_tree, layer_name, role):
    """Find first node matching layer + role."""
    for n in node_tree.nodes:
        if n.get("tlm_layer") == layer_name and n.get("tlm_role") == role:
            return n
    return None


def _find_all_tagged(node_tree, layer_name, role):
    """Find ALL nodes matching layer + role."""
    return [n for n in node_tree.nodes
            if n.get("tlm_layer") == layer_name and n.get("tlm_role") == role]


def _retag_frame_owner(node_tree, pre_names, frame_owner):
    """Add `tlm_frame_owner` to TLM nodes created since the `pre_names` snapshot.

    Used for Reference layer pattern copies: the nodes keep their original
    `tlm_layer` tag (= source layer's name) so hot updates on the source
    cascade through the copies, but visual frame grouping should use the
    REFERENCE layer's name so each reference gets its own NodeFrame in the
    shader editor.

    `pre_names` is a set of node names captured before the copy was built.
    Any new TLM-prefixed node is tagged with `tlm_frame_owner = frame_owner`.
    """
    for n in node_tree.nodes:
        if n.name in pre_names:
            continue
        if not n.name.startswith(TLM_PREFIX):
            continue
        n["tlm_frame_owner"] = frame_owner


def _material_from_node_tree(node_tree):
    """Find the material that owns a given node tree."""
    for mat in bpy.data.materials:
        if mat.use_nodes and mat.node_tree == node_tree:
            return mat
    return None


# â”€â”€ Hot-update dispatch â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Mapping of proc property names to Blender shader node input names
_PROC_INPUT_MAP = {
    "proc_scale": "Scale",
    "proc_detail": "Detail",
    "proc_roughness_proc": "Roughness",
    "proc_distortion": "Distortion",
    "proc_magic_distortion": "Distortion",  # Magic-only override
    "proc_lacunarity": "Lacunarity",
    "proc_randomness": "Randomness",
    "proc_wave_detail_scale": "Detail Scale",
    "proc_wave_detail_roughness": "Detail Roughness",
    "proc_wave_phase_offset": "Phase Offset",
}


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
# tuple â€” without "alpha" the alpha mix's Factor stayed stale on every
# opacity change (the visible symptom: layers with use_alpha enabled
# appeared to "ignore" opacity until a full rebuild ran).


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

    SAW wave fac runs 0â†’1 each period; the threshold below which fac is
    "background" is (1 - width), so the bright stripe spans `width` of
    the period (width=0.5 â†’ equal stripes, width=1 â†’ solid bright).

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


def _clamped_ramp_position(value):
    return min(max(float(value), 0.001), 0.999)


def hot_update_sun_direction():
    """Walk all TLM materials, find NDOTL/NDOTH dot-product nodes, and
    refresh their baked sun direction vector. Called by the depsgraph
    handler whenever the scene's Sun light moves or rotates.

    This avoids a full material rebuild on Sun changes â€” much cheaper.
    """
    sun_dir = _find_first_sun_direction()
    updated_materials = 0

    for mat in bpy.data.materials:
        if not mat.use_nodes or not mat.node_tree:
            continue
        tree_updated = False
        for n in mat.node_tree.nodes:
            # NDOTL: TLM_mask_ndotl_dot_*  â€” second input is the baked sun vector
            if 'mask_ndotl_dot' in n.name and n.type == 'VECTOR_MATH':
                try:
                    n.inputs[1].default_value = sun_dir
                    tree_updated = True
                except Exception:
                    pass
            # NDOTH: TLM_mask_ndoth_addlv_*  â€” input 1 holds the sun dir
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
                # has positive dot â€” hence we negate Blender's -Z.
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

    - ``contrast`` 0..1 â†’ narrows the transition band (high contrast = sharp).
      contrast=0.0 â†’ band spans the full 0..1 range (soft gradient).
      contrast=1.0 â†’ band collapses to ~0.02 around ``center`` (razor sharp).
    - ``center`` 0..1 â†’ where along Fac the band sits.
      center=0.5 â†’ symmetric (legacy behaviour).
      center=0.05 â†’ band near Fac=0 (e.g. thin Color1 outline at low-Fac region).
      center=0.95 â†’ band near Fac=1 (thin highlight in high-Fac region).

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

    # â”€â”€ Mix-topology path (STRIPES / HEX_GRID) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # These procs produce a binary fac so they bypass ColorRamp and
    # store their colours on dedicated Mix nodes. proc_color3_position
    # here means "core fraction" (0..1) of the inner stripe/edge.
    mix_main = _find_tagged(node_tree, layer.name, "proc_cmix_main")
    if not mix_main:
        return False  # nothing to update â€” fall back to rebuild

    has_color3 = getattr(layer, 'use_proc_color3', False)
    mix_inner = _find_tagged(node_tree, layer.name, "proc_cmix_inner")
    # Topology mismatch â€” let _on_layer_update do a full rebuild.
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
    (see _build_bump_channel refactor). If the tagged node is missing â€” either
    the rebuild hasn't run yet or the layer has no bump â€” fall through to a
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
    """Update per-layer Paint Mapping node (Location/Rotation/Scale Ã— XYZ).

    The Mapping node exists ONLY when at least one of the 9 values differs
    from default (loc=0, rot=0, scale=1). Crossing the threshold changes
    topology â†’ fallback rebuild.
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
        return False  # topology change â€” fallback rebuild
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
    """Update mask contrast Power exponent. Topology changes when crossing 0.5 â†’ rebuild."""
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
    """Update mask softness SMOOTHSTEP range. Topology changes when crossing 0 â†’ rebuild."""
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
        return False  # whole stack is gated â€” let rebuild handle on/off
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
    â†’ fall back to rebuild so the other branch is created / torn down.
    """
    new_blur = getattr(layer, 'mask_blur', 0.0)
    should_have_group = new_blur > 1e-4

    # Check both slots â€” either can have an IMAGE source using the blur group.
    nodes_a = _find_all_tagged(node_tree, layer.name, "mask_blur_group_a")
    nodes_b = _find_all_tagged(node_tree, layer.name, "mask_blur_group_b")
    all_groups = nodes_a + nodes_b

    # Determine which slots currently use an IMAGE source (the only source
    # that ever spawns a blur group). If use_mask itself is off, the whole
    # _apply_mask pipeline is skipped â†’ no groups on either slot.
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
        return False  # topology change â†’ rebuild

    if not all_groups:
        return True  # nothing to update, nothing expected â†’ no-op OK

    for g in all_groups:
        try:
            g.inputs["Blur"].default_value = new_blur
        except (KeyError, AttributeError):
            return False  # Group instance in a bad state â€” fall back to rebuild
    return True


# â”€â”€ Image-swap hot path â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Maps image-name properties â†’ (tag role to find the tex node, expected colorspace).
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
    - the new image-name is empty (clearing â†’ topology change, fill node needed)
    - the image datablock isn't found
    - no tagged tex node exists (first-time assignment after build â†’ structural)
    - any matched node isn't TEX_IMAGE (unexpected node type â†’ structural)
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
        return True  # re-entrant call from depsgraph â€” suppress
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


def _new_img_tex(node_tree, image, uv_map, x, y, colorspace="sRGB", layer=None, tag_role=None):
    """Create an Image Texture node, configured from the layer's mapping
    properties (interpolation / projection / source / extension /
    location / rotation / scale).

    If ``tag_role`` and ``layer`` are both provided, the tex node is
    tagged with (layer.name, tag_role) so it can be located later by
    the hot-update path (_hot_image_swap).

    Box projection (was a custom Triplanar implementation in an earlier
    version) is now Blender's native projection mode on the same node â€”
    cheaper (one tex node instead of three) and uniform across the rest
    of the engine.
    """
    # â”€â”€ Paint-canvas pixel preservation (paranoid mode) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Two earlier fixes (eed83d6, c5454bd) plugged the known mechanisms by
    # which Blender 5.0's image cache wipes the buffer of a GENERATED
    # paint canvas when output_channel toggles (source flip to FILE,
    # colorspace_settings ping-pong). Reports of pixel loss persist on
    # some flows we haven't isolated yet â€” maybe `node.image = image`
    # itself triggers a re-decode under specific cache states.
    #
    # Snapshot pixels BEFORE any node attribute writes, then restore at
    # the end if the buffer was zeroed. Only for PAINT layers' main
    # canvas (GENERATED, no filepath) â€” the case that gets bitten.
    # Other images (FILE-sourced, mask, etc.) skip the snapshot to keep
    # rebuild cheap.
    _is_paint_canvas = (
        layer is not None
        and getattr(layer, 'layer_type', '') == "PAINT"
        and image is not None
        and image.name == getattr(layer, 'image_name', '')
        and image.source == 'GENERATED'
        and not image.filepath
    )
    _saved_pixels = None
    if _is_paint_canvas:
        try:
            import numpy as np
            _w, _h = image.size
            _n = _w * _h * 4
            if _n > 0:
                _saved_pixels = np.empty(_n, dtype=np.float32)
                image.pixels.foreach_get(_saved_pixels)
                # Guard against snapshotting an already-zeroed buffer:
                # if a previous rebuild already wiped it, restoring zeros
                # would be a no-op and we'd never recover. Detect via the
                # "has any non-zero pixel" probe; treat all-zero as
                # "nothing worth saving".
                if not _saved_pixels.any():
                    _saved_pixels = None
        except Exception:
            _saved_pixels = None

    node = node_tree.nodes.new("ShaderNodeTexImage")
    node.name = f"{TLM_PREFIX}img_{image.name}_{_next_id()}"
    node.image = image
    node.location = (x, y)
    # Force the colorspace explicitly â€” keeps the image consistent with
    # how this layer uses it (sRGB for base color, Non-Color for data).
    #
    # SPECIAL CASE for PAINT layers' MAIN canvas: lock to sRGB regardless
    # of which channel the layer is currently routed to. Why:
    #   - The paint canvas is bpy.data.images.new() = source='GENERATED'
    #     with no filepath on disk.
    #   - When output_channel changes (e.g. Base Color â†’ Roughness),
    #     this code wants the colorspace to flip (sRGB â†’ Non-Color).
    #   - Blender 5.0 flipping colorspace_settings.name on a GENERATED
    #     image WITHOUT a backing file ZEROES the in-memory pixel
    #     buffer (it tries to re-decode from the missing file).
    #   - Symptom: paint pixels disappear every time the user toggles
    #     output_channel.
    # Accepting a small gamma-curve difference when a paint canvas is
    # routed to a scalar channel is much better than losing the user's
    # paint work. The user's paint already looked sRGB in the image
    # editor anyway â€” the visual mapping stays intuitive.
    # Other images (FILL's PBR slot, mask images, etc.) keep the
    # standard behaviour because they're either FILE-sourced or only
    # ever used in one role.
    is_paint_main_image = (
        layer is not None
        and getattr(layer, 'layer_type', '') == "PAINT"
        and image is not None
        and image.name == getattr(layer, 'image_name', '')
    )
    target_cs = "sRGB" if is_paint_main_image else colorspace
    try:
        if node.image.colorspace_settings.name != target_cs:
            node.image.colorspace_settings.name = target_cs
    except Exception:
        pass
    if is_paint_main_image:
        # Paint layers use the Image Texture Alpha output as coverage for
        # the layer mix. alpha_mode='NONE' makes Cycles treat transparent
        # paint pixels like opaque black RGB in some paths, so existing
        # generated canvases are repaired here on every rebuild.
        try:
            if node.image.alpha_mode == 'NONE':
                node.image.alpha_mode = 'STRAIGHT'
        except Exception:
            pass

    # Apply per-layer Image Texture node configuration. All of these
    # are mirrored straight from the layer property â†’ node attribute
    # because Blender's ShaderNodeTexImage uses the same enum values
    # (FLAT/BOX/SPHERE/TUBE for projection, CLIP/REPEAT/EXTEND/MIRROR
    # for extension, Linear/Cubic/Closest/Smart for interpolation,
    # FILE/GENERATED/SEQUENCE/MOVIE for source).
    if layer is not None:
        try:
            node.extension = getattr(layer, 'paint_extension', 'CLIP')
        except Exception:
            pass
        try:
            node.interpolation = getattr(layer, 'paint_interpolation', 'Linear')
        except Exception:
            pass
        try:
            node.projection = getattr(layer, 'paint_projection', 'FLAT')
        except Exception:
            pass
        # Source lives on the image datablock (image.source), not on the
        # node. Only assign when actually different to avoid re-triggering
        # the image reload path during a rebuild.
        #
        # CRITICAL: blindly setting source='FILE' on a GENERATED image
        # (a freshly-painted canvas with no filepath on disk) causes
        # Blender to try to load the missing file and ZERO OUT the
        # in-memory pixel buffer. Symptom: paint pixels disappear the
        # moment output_channel toggles. Guard against the destructive
        # direction by only writing FILE when the image really has a
        # filepath; user-explicit choices for non-FILE sources still
        # pass through.
        try:
            src = getattr(layer, 'paint_source', 'FILE')
            if node.image and node.image.source != src:
                if src == 'FILE':
                    if node.image.filepath:
                        node.image.source = src
                    # else: leave source untouched (likely GENERATED
                    # for a TLM-created paint canvas â€” switching it
                    # to FILE would wipe the unsaved pixels).
                else:
                    # Non-FILE target (GENERATED / SEQUENCE / MOVIE) â€”
                    # user explicitly picked it via paint_source.
                    node.image.source = src
        except Exception:
            pass
        # Projection Blend is an INPUT socket on the node â€” wired only
        # for Box projection (the only mode that uses it).
        if getattr(layer, 'paint_projection', 'FLAT') == 'BOX':
            try:
                node.projection_blend = getattr(layer, 'paint_projection_blend', 0.3)
            except Exception:
                pass

    uv = node_tree.nodes.new("ShaderNodeUVMap")
    uv.name = f"{TLM_PREFIX}uv_{_next_id()}"
    uv.uv_map = uv_map
    uv.location = (x - 220, y)

    # Optional Mapping node â€” inserted only if Location/Rotation/Scale differ
    # from defaults, so simple paint layers stay graph-light.
    vec_out = uv.outputs["UV"]
    if layer is not None:
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
        nontrivial = (
            any(abs(v) > 1e-6 for v in loc)
            or any(abs(v) > 1e-6 for v in rot)
            or any(abs(v - 1.0) > 1e-6 for v in scl)
        )
        if nontrivial:
            mapping = node_tree.nodes.new("ShaderNodeMapping")
            mapping.name = f"{TLM_PREFIX}paint_map_{_next_id()}"
            mapping.location = (x - 60, y)
            mapping.inputs["Location"].default_value = loc
            mapping.inputs["Rotation"].default_value = rot
            mapping.inputs["Scale"].default_value    = scl
            _tag(mapping, layer.name, "paint_mapping")
            node_tree.links.new(uv.outputs["UV"], mapping.inputs["Vector"])
            vec_out = mapping.outputs["Vector"]

    node_tree.links.new(vec_out, node.inputs["Vector"])
    if tag_role and layer is not None:
        _tag(node, layer.name, tag_role)

    # Paint-canvas pixel preservation (paranoid restore). If the buffer
    # was zeroed by any of the attribute writes above (Blender 5.0's
    # image cache occasionally invalidates GENERATED images during
    # node/image/colorspace assignment), put it back. The probe is
    # cheap â€” read one pixel â€” and the restore only fires on a real
    # wipe, so steady-state rebuilds aren't penalised.
    if _saved_pixels is not None:
        try:
            import numpy as np
            _probe = np.empty(4, dtype=np.float32)
            image.pixels.foreach_get(_probe)
            # If the first pixel matches what we saved, assume buffer
            # survived intact. If it's all zero AND we know we saved
            # non-zero content, restore.
            if not _probe.any() and _saved_pixels.any():
                image.pixels.foreach_set(_saved_pixels)
                try:
                    image.update()
                except Exception:
                    pass
                try:
                    image.update_tag()
                except Exception:
                    pass
        except Exception:
            pass

    return node


def _effective_blend_mode(layer, channel_id):
    """Return the effective blend mode for `channel_id` on `layer`.

    Branching: each layer can override its main blend_mode per PBR channel.
    Only base_color / roughness / metallic / emission / transmission support
    override â€” normal and bump have their own math and are unaffected.

    Alpha is handled by _new_alpha_math_composite instead of this blend
    path. Returning MIX here is only a defensive fallback for legacy paths.

    INHERIT (or any channel without an override property) falls back to
    `layer.blend_mode`.
    """
    if channel_id == 'alpha':
        return 'MIX'
    if channel_id in ('base_color', 'roughness', 'metallic', 'emission', 'transmission'):
        override = getattr(layer, f"blend_mode_{channel_id}", "INHERIT")
        if override and override != "INHERIT":
            return override
    return layer.blend_mode


def _new_mix(node_tree, blend_mode, opacity, x, y, layer_name="", channel=""):
    if _USE_NEW_MIX:
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
    if layer_name:
        _tag(node, layer_name, f"mix_{channel}" if channel else "mix")
    return node


def _new_mix_scalar(node_tree, blend_mode, opacity, x, y, layer_name="", channel=""):
    """Mix node for scalar channels (Roughness/Metallic/Alpha/Transmission).

    Float-typed mix that honors the layer's blend_mode just like the color
    Mix does for Base Color. ShaderNodeMix with data_type='FLOAT' supports
    the standard blend types (MIX/ADD/MULTIPLY/SUBTRACT/DIVIDE/etc.) â€” no
    reason to hard-code MIX and lose per-channel blend overrides.
    Earlier versions used blend_type='MIX' only, which made the
    branching feature (blend_mode_roughness etc.) silently ineffective on
    scalar channels.
    """
    if _USE_NEW_MIX:
        node = node_tree.nodes.new("ShaderNodeMix")
        node.data_type = 'RGBA'
        try:
            node.blend_type = BLEND_TO_MIX_MODE.get(blend_mode, "MIX")
        except (TypeError, AttributeError):
            node.blend_type = 'MIX'  # safety: fall back if Blender rejects
        node.inputs["Factor"].default_value = opacity
    else:
        # Pre-4.0 fallback â€” ShaderNodeMixRGB has the same blend_type enum,
        # output goes through SeparateColor downstream to extract the float.
        node = node_tree.nodes.new("ShaderNodeMixRGB")
        try:
            node.blend_type = BLEND_TO_MIX_MODE.get(blend_mode, "MIX")
        except (TypeError, AttributeError):
            node.blend_type = 'MIX'
        node.inputs["Fac"].default_value = opacity
    node.name = f"{TLM_PREFIX}mix_scalar_{_next_id()}"
    node.location = (x, y)
    if layer_name:
        _tag(node, layer_name, f"mix_{channel}" if channel else "mix_scalar")
    to_scalar = node_tree.nodes.new("ShaderNodeRGBToBW")
    to_scalar.name = f"{TLM_PREFIX}mix_scalar_bw_{_next_id()}"
    to_scalar.location = (x + 190, y)
    if layer_name:
        _tag(to_scalar, layer_name,
             f"mix_scalar_result_{channel}" if channel else "mix_scalar_result")
    node_tree.links.new(_result_socket(node), to_scalar.inputs["Color"])
    node["tlm_scalar_result_node"] = to_scalar.name
    return node


def _new_alpha_math_composite(node_tree, layer, current, layer_out, layer_alpha,
                              prev_alpha, x, y, i, uv_map="UVMap"):
    """Composite an Alpha-channel layer with a ShaderNodeMath operation.

    Alpha is scalar opacity, so artistic color blend modes are a poor fit.
    We first calculate Math(current_alpha, layer_alpha), then mix from the
    previous value to that result using the normal opacity/mask/fresnel factor.
    """
    if current is None:
        # Channel default base for alpha (1.0 = fully opaque). Tag with
        # empty layer_name so _hot_scalar_fill doesn't pick this up when
        # the user drags alpha_fill — that hot path targets val_alpha
        # tagged with the layer's own name.
        current = _new_value(node_tree, 1.0, x - 100, y - 20,
                             layer_name="",
                             channel="alpha").outputs["Value"]

    op = getattr(layer, "alpha_math_operation", "MULTIPLY") or "MULTIPLY"
    math = node_tree.nodes.new("ShaderNodeMath")
    try:
        math.operation = op
    except (TypeError, ValueError):
        math.operation = 'MULTIPLY'
    math.use_clamp = True
    math.name = f"{TLM_PREFIX}alpha_math_{_next_id()}"
    math.label = f"Alpha {math.operation.replace('_', ' ').title()}"
    math.location = (x + 140, y - 40)
    _tag(math, layer.name, "alpha_math")

    _UNARY_ALPHA_MATH = {
        'SQRT', 'INVERSE_SQRT', 'ABSOLUTE', 'EXPONENT', 'SIGN',
        'ROUND', 'FLOOR', 'CEIL', 'TRUNC', 'FRACT',
        'SINE', 'COSINE', 'TANGENT', 'ARCSINE', 'ARCCOSINE',
        'ARCTANGENT', 'SINH', 'COSH', 'TANH', 'RADIANS', 'DEGREES',
    }
    try:
        if math.operation in _UNARY_ALPHA_MATH:
            node_tree.links.new(layer_out, math.inputs[0])
        else:
            node_tree.links.new(current, math.inputs[0])
            node_tree.links.new(layer_out, math.inputs[1])
            if len(math.inputs) > 2:
                math.inputs[2].default_value = 0.0
    except Exception:
        return current

    mix_x = x + 320
    mix = _new_mix_scalar(node_tree, 'MIX', layer.opacity, mix_x, y - 40,
                          layer_name=layer.name, channel="alpha")
    node_tree.links.new(current, _a_socket_scalar(mix))
    node_tree.links.new(math.outputs["Value"], _b_socket_scalar(mix))
    _set_factor(node_tree, mix, layer, layer_alpha, prev_alpha,
                mix_x, y, i, uv_map, channel="alpha")
    return _result_socket_scalar(mix)


def _new_mix_vector(node_tree, opacity, x, y, layer_name="", channel=""):
    """Mix node for vector channels (Normal) â€” Vector type, always MIX blend."""
    node = node_tree.nodes.new("ShaderNodeMix")
    node.data_type = 'VECTOR'
    node.blend_type = 'MIX'  # always MIX for normal vectors
    node.inputs["Factor"].default_value = opacity
    node.name = f"{TLM_PREFIX}mix_vector_{_next_id()}"
    node.location = (x, y)
    if layer_name:
        _tag(node, layer_name, f"mix_{channel}" if channel else "mix_vector")
    return node


def _enabled_socket(sockets, name):
    """Find the first *enabled* socket with the given name.

    ShaderNodeMix has multiple sockets named 'A', 'B', 'Result' â€” one per
    data-type (Float, Vector, Color).  Only the one matching the active
    data_type is enabled.  Falling back to sockets[name] would silently
    return the hidden Float variant, corrupting the entire chain.
    """
    for s in sockets:
        if s.name == name and s.enabled:
            return s
    # Fallback â€” should not happen but avoids crash
    return sockets[name]


def _factor_socket(node):
    if _USE_NEW_MIX:
        return node.inputs["Factor"]
    return node.inputs["Fac"]


def _a_socket(node):
    if _USE_NEW_MIX:
        return _enabled_socket(node.inputs, "A")
    return node.inputs["Color1"]


def _b_socket(node):
    if _USE_NEW_MIX:
        return _enabled_socket(node.inputs, "B")
    return node.inputs["Color2"]


def _result_socket(node):
    if _USE_NEW_MIX:
        return _enabled_socket(node.outputs, "Result")
    return node.outputs["Color"]


def _a_socket_scalar(node):
    if _USE_NEW_MIX:
        return _enabled_socket(node.inputs, "A")
    return node.inputs["Color1"]


def _b_socket_scalar(node):
    if _USE_NEW_MIX:
        return _enabled_socket(node.inputs, "B")
    return node.inputs["Color2"]


def _result_socket_scalar(node):
    result_node_name = node.get("tlm_scalar_result_node")
    if result_node_name:
        result_node = node.id_data.nodes.get(result_node_name)
        if result_node:
            return result_node.outputs.get("Val") or result_node.outputs[0]
    if _USE_NEW_MIX:
        return _enabled_socket(node.outputs, "Result")
    return node.outputs["Color"]


def _new_fill(node_tree, color, x, y, layer_name="", channel=""):
    node = node_tree.nodes.new("ShaderNodeRGB")
    node.name = f"{TLM_PREFIX}fill_{_next_id()}"
    node.outputs[0].default_value = color
    node.location = (x, y)
    if layer_name:
        role = f"fill_{channel}" if channel else "fill"
        _tag(node, layer_name, role)
    return node


def _new_value(node_tree, value, x, y, layer_name="", channel=""):
    """Single float Value node for scalar fill."""
    node = node_tree.nodes.new("ShaderNodeValue")
    node.name = f"{TLM_PREFIX}val_{_next_id()}"
    node.outputs[0].default_value = value
    node.location = (x, y)
    if layer_name:
        _tag(node, layer_name, f"val_{channel}")
    return node


def _build_smart_generator(node_tree, layer, gen_type, ao_distance, x, y, name_tag=""):
    """Build a physics-based smart mask generator output socket (0-1).

    EDGE_WEAR: Pointiness convex edges + noise breakup + sharpness â€” simulates worn edges
    DIRT: Inverted AO Ã— grunge noise â€” accumulates in cavities
    CURVATURE_SMART: Bipolar pointiness (|p-0.5|*2) â€” both convex + concave edges

    Shared layer properties: mask_gen_intensity, mask_gen_breakup,
    mask_gen_breakup_scale, mask_gen_sharpness. These are shared by slot A and B.
    """
    intensity = getattr(layer, 'mask_gen_intensity', 1.0)
    breakup   = getattr(layer, 'mask_gen_breakup', 0.3)
    bsc       = getattr(layer, 'mask_gen_breakup_scale', 15.0)
    sharpness = getattr(layer, 'mask_gen_sharpness', 0.5)

    x_base = x - 640
    y_base = y - 100

    # â”€â”€ 1. Base source â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if gen_type == 'DIRT':
        src = node_tree.nodes.new("ShaderNodeAmbientOcclusion")
        src.samples = 16
        src.inputs["Distance"].default_value = ao_distance
        src.location = (x_base, y_base)
        src.name = f"{TLM_PREFIX}gen_ao_{name_tag}_{_next_id()}"
        _tag(src, layer.name, f"gen_ao_{name_tag}")
        # Invert AO â€” dirt accumulates in cavities which AO marks dark
        inv = node_tree.nodes.new("ShaderNodeMath")
        inv.operation = 'SUBTRACT'
        inv.name = f"{TLM_PREFIX}gen_ao_inv_{name_tag}_{_next_id()}"
        inv.inputs[0].default_value = 1.0
        inv.use_clamp = True
        inv.location = (x_base + 200, y_base)
        # Blender 5.0: ShaderNodeAmbientOcclusion outputs are "Color" and "AO"
        # (the old "Fac" output was removed). Use "AO" for the scalar occlusion factor.
        node_tree.links.new(src.outputs["AO"], inv.inputs[1])
        base = inv.outputs["Value"]
    else:
        geo = node_tree.nodes.new("ShaderNodeNewGeometry")
        geo.location = (x_base, y_base)
        geo.name = f"{TLM_PREFIX}gen_geo_{name_tag}_{_next_id()}"
        _tag(geo, layer.name, f"gen_geo_{name_tag}")
        p = geo.outputs["Pointiness"]
        if gen_type == 'EDGE_WEAR':
            # Convex edges only: MapRange 0.5..0.7 â†’ 0..1 (narrow band, clamped)
            mr = node_tree.nodes.new("ShaderNodeMapRange")
            mr.clamp = True
            mr.inputs["From Min"].default_value = 0.5
            mr.inputs["From Max"].default_value = 0.7
            mr.inputs["To Min"].default_value = 0.0
            mr.inputs["To Max"].default_value = 1.0
            mr.location = (x_base + 200, y_base)
            mr.name = f"{TLM_PREFIX}gen_mr_{name_tag}_{_next_id()}"
            node_tree.links.new(p, mr.inputs["Value"])
            base = mr.outputs["Result"]
        else:  # CURVATURE_SMART: bipolar edges
            sub = node_tree.nodes.new("ShaderNodeMath")
            sub.operation = 'SUBTRACT'
            sub.name = f"{TLM_PREFIX}gen_curv_sub_{name_tag}_{_next_id()}"
            sub.location = (x_base + 200, y_base)
            node_tree.links.new(p, sub.inputs[0])
            sub.inputs[1].default_value = 0.5
            ab = node_tree.nodes.new("ShaderNodeMath")
            ab.operation = 'ABSOLUTE'
            ab.name = f"{TLM_PREFIX}gen_curv_abs_{name_tag}_{_next_id()}"
            ab.location = (x_base + 320, y_base)
            node_tree.links.new(sub.outputs[0], ab.inputs[0])
            m2 = node_tree.nodes.new("ShaderNodeMath")
            m2.operation = 'MULTIPLY'
            m2.name = f"{TLM_PREFIX}gen_curv_mul_{name_tag}_{_next_id()}"
            m2.use_clamp = True
            m2.location = (x_base + 440, y_base)
            node_tree.links.new(ab.outputs[0], m2.inputs[0])
            m2.inputs[1].default_value = 2.0
            base = m2.outputs[0]

    # â”€â”€ 2. Noise breakup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if breakup > 1e-4:
        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.noise_dimensions = '3D'
        noise.inputs["Scale"].default_value = bsc
        noise.inputs["Detail"].default_value = 2.0
        noise.inputs["Roughness"].default_value = 0.5
        noise.location = (x_base + 440, y_base - 160)
        noise.name = f"{TLM_PREFIX}gen_noise_{name_tag}_{_next_id()}"
        _tag(noise, layer.name, f"gen_noise_{name_tag}")

        # modulation = 1 - breakup + breakup*noise  (noise âˆˆ 0..1)
        # Use MULTIPLY_ADD:  noise * breakup + (1 - breakup)
        mod = node_tree.nodes.new("ShaderNodeMath")
        mod.operation = 'MULTIPLY_ADD'
        mod.name = f"{TLM_PREFIX}gen_breakup_madd_{name_tag}_{_next_id()}"
        mod.location = (x_base + 620, y_base - 160)
        node_tree.links.new(noise.outputs["Fac"], mod.inputs[0])
        mod.inputs[1].default_value = breakup
        mod.inputs[2].default_value = 1.0 - breakup

        mm = node_tree.nodes.new("ShaderNodeMath")
        mm.operation = 'MULTIPLY'
        mm.name = f"{TLM_PREFIX}gen_breakup_mul_{name_tag}_{_next_id()}"
        mm.use_clamp = True
        mm.location = (x_base + 740, y_base)
        node_tree.links.new(base, mm.inputs[0])
        node_tree.links.new(mod.outputs[0], mm.inputs[1])
        base = mm.outputs[0]

    # â”€â”€ 3. Sharpness via POWER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if abs(sharpness - 0.5) > 1e-4:
        if sharpness < 0.5:
            exp = 0.3 + (sharpness / 0.5) * 0.7     # 0.0 â†’ 0.3 (very soft)
        else:
            exp = 1.0 + ((sharpness - 0.5) / 0.5) * 4.0  # 1.0 â†’ 5.0 (very sharp)
        pwr = node_tree.nodes.new("ShaderNodeMath")
        pwr.operation = 'POWER'
        pwr.use_clamp = True
        pwr.location = (x_base + 860, y_base)
        pwr.name = f"{TLM_PREFIX}gen_sharp_{name_tag}_{_next_id()}"
        node_tree.links.new(base, pwr.inputs[0])
        pwr.inputs[1].default_value = exp
        base = pwr.outputs[0]

    # â”€â”€ 4. Intensity multiplier â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if abs(intensity - 1.0) > 1e-4:
        im = node_tree.nodes.new("ShaderNodeMath")
        im.operation = 'MULTIPLY'
        im.use_clamp = True
        im.location = (x_base + 980, y_base)
        im.name = f"{TLM_PREFIX}gen_intensity_{name_tag}_{_next_id()}"
        node_tree.links.new(base, im.inputs[0])
        im.inputs[1].default_value = intensity
        base = im.outputs[0]

    return base


def _build_blurred_image_mask(node_tree, img, uv_map, blur, x, y, name_tag="",
                              layer_name=""):
    """Pseudo-blur via 5-tap cross, encapsulated in a reusable NodeGroup.

    The actual kernel (5 Image Texture samples + Mapping offsets + weighted
    sum) lives inside the shared `TLM_maskblur_<image>` NodeGroup. In the
    material's node tree we place only two nodes: a UV Map and a single
    Group instance â€” instead of the ~20 nodes the inline version used.

    Runtime cost is unchanged (still 5 texture samples per pixel); this is
    a pure visual/architectural cleanup.

    The Group instance is tagged with role ``mask_blur_group_<slot>`` so the
    ``_hot_mask_blur`` handler can update the Blur input without rebuilding.
    """
    uv = node_tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = uv_map
    uv.name = f"{TLM_PREFIX}mask_blur_uv_{name_tag}_{_next_id()}"
    uv.location = (x - 380, y - 100)

    group = node_tree.nodes.new("ShaderNodeGroup")
    group.node_tree = _get_or_build_mask_blur_group(img)
    group.name = f"{TLM_PREFIX}mask_blur_group_{name_tag}_{_next_id()}"
    group.label = f"Mask Blur ({img.name})"
    group.location = (x - 180, y - 100)
    group.inputs["Blur"].default_value = blur
    if layer_name:
        _tag(group, layer_name, f"mask_blur_group_{name_tag}")
    node_tree.links.new(uv.outputs["UV"], group.inputs["UV"])

    return group.outputs["Value"]



def _build_mask_slot(node_tree, layer, slot, uv_map, x, y, name_tag=""):
    """Build a single mask value socket (0-1) for slot 'a' or 'b'.

    Returns the output socket of the final node in the slot's mini-chain,
    or None if the mask source is invalid (e.g. IMAGE with no image assigned).
    The slot reads the appropriate properties:
        slot='a' â†’ mask_source, mask_image_name, mask_invert, mask_ao_distance
        slot='b' â†’ mask_source_b, mask_image_name_b, mask_invert_b, mask_ao_distance_b
    Supports sources: IMAGE (with optional blur), AO, POINTINESS,
    EDGE_WEAR, DIRT, CURVATURE_SMART.
    """
    if slot == 'a':
        source      = getattr(layer, 'mask_source', 'IMAGE')
        img_name    = getattr(layer, 'mask_image_name', "")
        invert      = getattr(layer, 'mask_invert', False)
        ao_distance = getattr(layer, 'mask_ao_distance', 0.5)
    else:
        source      = getattr(layer, 'mask_source_b', 'IMAGE')
        img_name    = getattr(layer, 'mask_image_name_b', "")
        invert      = getattr(layer, 'mask_invert_b', False)
        ao_distance = getattr(layer, 'mask_ao_distance_b', 0.5)

    val = None
    if source == 'IMAGE':
        img = bpy.data.images.get(img_name) if img_name else None
        if img is None:
            return None
        blur = getattr(layer, 'mask_blur', 0.0)
        if blur > 1e-4:
            val = _build_blurred_image_mask(node_tree, img, uv_map, blur, x, y, name_tag,
                                            layer_name=layer.name)
        else:
            tex = _new_img_tex(node_tree, img, uv_map, x - 440, y - 100, "Non-Color")
            sep = node_tree.nodes.new("ShaderNodeSeparateColor")
            sep.name = f"{TLM_PREFIX}mask_sep_{name_tag}_{_next_id()}"
            sep.location = (x - 280, y - 100)
            node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
            val = sep.outputs["Red"]
    elif source == 'AO':
        ao = node_tree.nodes.new("ShaderNodeAmbientOcclusion")
        ao.name = f"{TLM_PREFIX}mask_ao_{name_tag}_{_next_id()}"
        ao.location = (x - 300, y - 100)
        ao.samples = 16
        ao.inputs["Distance"].default_value = ao_distance
        _tag(ao, layer.name, f"mask_ao_{name_tag}")
        # Blender 5.0: ShaderNodeAmbientOcclusion outputs are "Color" and "AO"
        # (the old "Fac" output was removed). Use "AO" for the scalar occlusion factor.
        val = ao.outputs["AO"]
    elif source == 'POINTINESS':
        geo = node_tree.nodes.new("ShaderNodeNewGeometry")
        geo.name = f"{TLM_PREFIX}mask_geo_{name_tag}_{_next_id()}"
        geo.location = (x - 300, y - 100)
        _tag(geo, layer.name, f"mask_geo_{name_tag}")
        val = geo.outputs["Pointiness"]
    elif source == 'WIREFRAME':
        wire = node_tree.nodes.new("ShaderNodeWireframe")
        wire.name = f"{TLM_PREFIX}mask_wireframe_{name_tag}_{_next_id()}"
        wire.location = (x - 300, y - 100)
        wire.inputs["Size"].default_value = getattr(layer, 'mask_wireframe_size', 0.01)
        if hasattr(wire, "use_pixel_size"):
            wire.use_pixel_size = getattr(layer, 'mask_wireframe_use_pixel_size', True)
        _tag(wire, layer.name, f"mask_wireframe_{name_tag}")
        val = wire.outputs["Fac"]
    elif source == 'FRESNEL':
        # View-angle gradient: 0 facing the camera, 1 at grazing silhouette.
        # Same IOR semantics as use_fresnel_mask but here Fresnel is the
        # SOURCE (drives a procedural's Fac for iridescent gradients via the
        # downstream ColorRamp) rather than a per-layer modifier multiplied
        # into the factor. This unlocks oil-slick / bubble / hologram /
        # mother-of-pearl materials that need a continuous Fresnel â†’ colour
        # gradient instead of a single-colour rim halo.
        fr = node_tree.nodes.new("ShaderNodeFresnel")
        fr.name = f"{TLM_PREFIX}mask_fresnel_{name_tag}_{_next_id()}"
        fr.location = (x - 300, y - 100)
        fr.inputs["IOR"].default_value = getattr(layer, 'mask_fresnel_ior', 1.45)
        _tag(fr, layer.name, f"mask_fresnel_{name_tag}")
        val = fr.outputs["Fac"]
    elif source == 'NDOTL':
        # Light angle: Normal Â· Sun direction â†’ [0,1]. Pair with
        # proc_contrast=1.0 ColorRamp on a downstream procedural for binary
        # anime/toon cel-shading. The sun direction is baked from the first
        # Sun light in the scene at build time (changes to the sun require
        # rebuilding the material via the rebuild button).
        sun_dir = _find_first_sun_direction()
        # Build the geometry â†’ normal â†’ dot-product chain
        geo = node_tree.nodes.new("ShaderNodeNewGeometry")
        geo.name = f"{TLM_PREFIX}mask_ndotl_geo_{name_tag}_{_next_id()}"
        geo.location = (x - 480, y - 100)
        _tag(geo, layer.name, f"mask_ndotl_geo_{name_tag}")

        # Vector Math DOT_PRODUCT(normal, sun_dir)
        dot = node_tree.nodes.new("ShaderNodeVectorMath")
        dot.operation = 'DOT_PRODUCT'
        dot.name = f"{TLM_PREFIX}mask_ndotl_dot_{name_tag}_{_next_id()}"
        dot.location = (x - 320, y - 100)
        _tag(dot, layer.name, f"mask_ndotl_dot_{name_tag}")
        node_tree.links.new(geo.outputs["Normal"], dot.inputs[0])
        # inputs[1] is a Vector socket â€” set its default_value
        dot.inputs[1].default_value = sun_dir

        # Map [-1, 1] â†’ [0, 1] so the value works as a standard mask
        mr = node_tree.nodes.new("ShaderNodeMapRange")
        mr.clamp = True
        mr.inputs["From Min"].default_value = -1.0
        mr.inputs["From Max"].default_value = 1.0
        mr.inputs["To Min"].default_value = 0.0
        mr.inputs["To Max"].default_value = 1.0
        mr.name = f"{TLM_PREFIX}mask_ndotl_mr_{name_tag}_{_next_id()}"
        mr.location = (x - 180, y - 100)
        # Vector Math DOT_PRODUCT outputs a scalar via the "Value" socket
        node_tree.links.new(dot.outputs["Value"], mr.inputs["Value"])
        val = mr.outputs["Result"]
    elif source == 'NDOTH':
        # Half-Vector specular: Normal Â· normalize(SunDir + ViewDir).
        # Peaks where the surface points exactly between the sun and the
        # camera = the classic Phong specular highlight position. With a
        # narrow ColorRamp (proc_contrast=1.0) this gives a small "anime
        # toon highlight" shape on convex peaks aligned with the light.
        sun_dir = _find_first_sun_direction()

        geo = node_tree.nodes.new("ShaderNodeNewGeometry")
        geo.name = f"{TLM_PREFIX}mask_ndoth_geo_{name_tag}_{_next_id()}"
        geo.location = (x - 580, y - 100)
        _tag(geo, layer.name, f"mask_ndoth_geo_{name_tag}")

        # H = normalize(L + V). L is the baked sun direction (= direction
        # TO the light). V is Geometry.Incoming = direction from surface
        # TO the viewer. Both unit vectors â†’ sum then normalize.
        add_lv = node_tree.nodes.new("ShaderNodeVectorMath")
        add_lv.operation = 'ADD'
        add_lv.name = f"{TLM_PREFIX}mask_ndoth_addlv_{name_tag}_{_next_id()}"
        add_lv.location = (x - 460, y - 100)
        node_tree.links.new(geo.outputs["Incoming"], add_lv.inputs[0])
        add_lv.inputs[1].default_value = sun_dir

        norm_h = node_tree.nodes.new("ShaderNodeVectorMath")
        norm_h.operation = 'NORMALIZE'
        norm_h.name = f"{TLM_PREFIX}mask_ndoth_normh_{name_tag}_{_next_id()}"
        norm_h.location = (x - 380, y - 100)
        node_tree.links.new(add_lv.outputs["Vector"], norm_h.inputs[0])

        # NdotH = Normal Â· H
        dot = node_tree.nodes.new("ShaderNodeVectorMath")
        dot.operation = 'DOT_PRODUCT'
        dot.name = f"{TLM_PREFIX}mask_ndoth_dot_{name_tag}_{_next_id()}"
        dot.location = (x - 240, y - 100)
        _tag(dot, layer.name, f"mask_ndoth_dot_{name_tag}")
        node_tree.links.new(geo.outputs["Normal"], dot.inputs[0])
        node_tree.links.new(norm_h.outputs["Vector"], dot.inputs[1])

        # Map [-1, 1] â†’ [0, 1] (Half-Lambert style for the highlight too)
        mr = node_tree.nodes.new("ShaderNodeMapRange")
        mr.clamp = True
        mr.inputs["From Min"].default_value = -1.0
        mr.inputs["From Max"].default_value = 1.0
        mr.inputs["To Min"].default_value = 0.0
        mr.inputs["To Max"].default_value = 1.0
        mr.name = f"{TLM_PREFIX}mask_ndoth_mr_{name_tag}_{_next_id()}"
        mr.location = (x - 100, y - 100)
        node_tree.links.new(dot.outputs["Value"], mr.inputs["Value"])
        val = mr.outputs["Result"]
    elif source in ('EDGE_WEAR', 'DIRT', 'CURVATURE_SMART'):
        val = _build_smart_generator(node_tree, layer, source, ao_distance, x, y, name_tag)

    if val is None:
        return None

    if invert:
        inv = node_tree.nodes.new("ShaderNodeMath")
        inv.operation = 'SUBTRACT'
        inv.use_clamp = True
        inv.name = f"{TLM_PREFIX}mask_inv_{name_tag}_{_next_id()}"
        inv.location = (x - 180, y - 100)
        inv.inputs[0].default_value = 1.0
        node_tree.links.new(val, inv.inputs[1])
        val = inv.outputs["Value"]

    return val


def _apply_mask(node_tree, mix_node, layer, uv_map, x, y, layer_alpha=None):
    """Build the advanced mask pipeline and wire it into mix_node's factor.

    Pipeline: A [combine B] â†’ contrast â†’ multiply(opacity) â†’ factor socket

    Returns the final Multiply Math node (caller sets inputs[1] = opacity)
    or None if the primary mask source is invalid.
    """
    a = _build_mask_slot(node_tree, layer, 'a', uv_map, x, y - 0, name_tag="a")
    if a is None:
        return None

    combined = a
    if getattr(layer, 'use_mask_b', False):
        b = _build_mask_slot(node_tree, layer, 'b', uv_map, x, y - 80, name_tag="b")
        if b is not None:
            mode = getattr(layer, 'mask_combine', 'MULTIPLY')
            if mode == 'SCREEN':
                # screen = 1 - (1-a)*(1-b) â€” smoother OR
                ia = node_tree.nodes.new("ShaderNodeMath")
                ia.operation = 'SUBTRACT'
                ia.name = f"{TLM_PREFIX}mask_screen_ia_{_next_id()}"
                ia.inputs[0].default_value = 1.0
                ia.location = (x - 120, y - 60)
                node_tree.links.new(a, ia.inputs[1])

                ib = node_tree.nodes.new("ShaderNodeMath")
                ib.operation = 'SUBTRACT'
                ib.name = f"{TLM_PREFIX}mask_screen_ib_{_next_id()}"
                ib.inputs[0].default_value = 1.0
                ib.location = (x - 120, y - 100)
                node_tree.links.new(b, ib.inputs[1])

                mul = node_tree.nodes.new("ShaderNodeMath")
                mul.operation = 'MULTIPLY'
                mul.name = f"{TLM_PREFIX}mask_screen_mul_{_next_id()}"
                mul.location = (x - 60, y - 80)
                node_tree.links.new(ia.outputs[0], mul.inputs[0])
                node_tree.links.new(ib.outputs[0], mul.inputs[1])

                scr = node_tree.nodes.new("ShaderNodeMath")
                scr.operation = 'SUBTRACT'
                scr.use_clamp = True
                scr.name = f"{TLM_PREFIX}mask_screen_{_next_id()}"
                scr.location = (x, y - 80)
                scr.inputs[0].default_value = 1.0
                node_tree.links.new(mul.outputs[0], scr.inputs[1])
                combined = scr.outputs[0]
            else:
                op_map = {
                    'MULTIPLY':  'MULTIPLY',
                    'MINIMUM':   'MINIMUM',
                    'MAXIMUM':   'MAXIMUM',
                    'ADD':       'ADD',
                    'SUBTRACT':  'SUBTRACT',
                    'DIFFERENCE':'ABSOLUTE',  # Built via Subtract â†’ Absolute below
                }
                if mode == 'DIFFERENCE':
                    sub = node_tree.nodes.new("ShaderNodeMath")
                    sub.operation = 'SUBTRACT'
                    sub.name = f"{TLM_PREFIX}mask_diff_sub_{_next_id()}"
                    sub.location = (x - 80, y - 80)
                    node_tree.links.new(a, sub.inputs[0])
                    node_tree.links.new(b, sub.inputs[1])
                    ab = node_tree.nodes.new("ShaderNodeMath")
                    ab.operation = 'ABSOLUTE'
                    ab.use_clamp = True
                    ab.name = f"{TLM_PREFIX}mask_comb_{_next_id()}"
                    ab.location = (x - 20, y - 80)
                    node_tree.links.new(sub.outputs[0], ab.inputs[0])
                    combined = ab.outputs[0]
                else:
                    op = node_tree.nodes.new("ShaderNodeMath")
                    op.operation = op_map.get(mode, 'MULTIPLY')
                    op.use_clamp = True
                    op.name = f"{TLM_PREFIX}mask_comb_{_next_id()}"
                    op.location = (x - 80, y - 80)
                    node_tree.links.new(a, op.inputs[0])
                    node_tree.links.new(b, op.inputs[1])
                    combined = op.outputs[0]

    # Contrast via POWER: 0.5 = no change; <0.5 softer (exp<1); >0.5 harder (exp>1)
    contrast = getattr(layer, 'mask_contrast', 0.5)
    if abs(contrast - 0.5) > 1e-4:
        if contrast < 0.5:
            exp = 0.25 + (contrast / 0.5) * 0.75     # 0 â†’ 0.25, 0.5 â†’ 1.0
        else:
            exp = 1.0 + ((contrast - 0.5) / 0.5) * 3.0  # 0.5 â†’ 1.0, 1.0 â†’ 4.0
        pwr = node_tree.nodes.new("ShaderNodeMath")
        pwr.operation = 'POWER'
        pwr.use_clamp = True
        pwr.name = f"{TLM_PREFIX}mask_contrast_{_next_id()}"
        pwr.location = (x + 60, y - 80)
        node_tree.links.new(combined, pwr.inputs[0])
        pwr.inputs[1].default_value = exp
        _tag(pwr, layer.name, "mask_contrast")
        combined = pwr.outputs[0]

    # â”€â”€ Levels: in range â†’ gamma â†’ out range (industry-standard tonal remap) â”€
    if getattr(layer, 'use_mask_levels', False):
        in_min  = getattr(layer, 'mask_levels_in_min', 0.0)
        in_max  = getattr(layer, 'mask_levels_in_max', 1.0)
        gamma   = getattr(layer, 'mask_levels_gamma', 1.0)
        out_min = getattr(layer, 'mask_levels_out_min', 0.0)
        out_max = getattr(layer, 'mask_levels_out_max', 1.0)

        if in_max > in_min + 1e-4 and (in_min > 1e-4 or in_max < 1 - 1e-4):
            lv_in = node_tree.nodes.new("ShaderNodeMapRange")
            lv_in.clamp = True
            lv_in.inputs["From Min"].default_value = in_min
            lv_in.inputs["From Max"].default_value = in_max
            lv_in.inputs["To Min"].default_value = 0.0
            lv_in.inputs["To Max"].default_value = 1.0
            lv_in.location = (x + 180, y - 80)
            lv_in.name = f"{TLM_PREFIX}mask_lv_in_{_next_id()}"
            _tag(lv_in, layer.name, "mask_lv_in")
            node_tree.links.new(combined, lv_in.inputs["Value"])
            combined = lv_in.outputs["Result"]

        if abs(gamma - 1.0) > 1e-4:
            lv_g = node_tree.nodes.new("ShaderNodeMath")
            lv_g.operation = 'POWER'
            lv_g.use_clamp = True
            lv_g.location = (x + 260, y - 80)
            lv_g.name = f"{TLM_PREFIX}mask_lv_gamma_{_next_id()}"
            _tag(lv_g, layer.name, "mask_lv_gamma")
            node_tree.links.new(combined, lv_g.inputs[0])
            # Convention: gamma<1 brightens midtones, so exp = 1/gamma
            lv_g.inputs[1].default_value = 1.0 / gamma
            combined = lv_g.outputs[0]

        if out_min > 1e-4 or out_max < 1 - 1e-4:
            lv_out = node_tree.nodes.new("ShaderNodeMapRange")
            lv_out.clamp = True
            lv_out.inputs["From Min"].default_value = 0.0
            lv_out.inputs["From Max"].default_value = 1.0
            lv_out.inputs["To Min"].default_value = out_min
            lv_out.inputs["To Max"].default_value = out_max
            lv_out.location = (x + 340, y - 80)
            lv_out.name = f"{TLM_PREFIX}mask_lv_out_{_next_id()}"
            _tag(lv_out, layer.name, "mask_lv_out")
            node_tree.links.new(combined, lv_out.inputs["Value"])
            combined = lv_out.outputs["Result"]

    # â”€â”€ Softness: smoothstep S-curve around 0.5 â€” widens transition zone â”€â”€
    softness = getattr(layer, 'mask_softness', 0.0)
    if softness > 1e-4:
        half = softness * 0.5
        sm = node_tree.nodes.new("ShaderNodeMapRange")
        sm.clamp = True
        sm.interpolation_type = 'SMOOTHSTEP'
        sm.inputs["From Min"].default_value = max(0.0, 0.5 - half)
        sm.inputs["From Max"].default_value = min(1.0, 0.5 + half)
        sm.inputs["To Min"].default_value = 0.0
        sm.inputs["To Max"].default_value = 1.0
        sm.location = (x + 420, y - 80)
        sm.name = f"{TLM_PREFIX}mask_soft_{_next_id()}"
        _tag(sm, layer.name, "mask_soft")
        node_tree.links.new(combined, sm.inputs["Value"])
        combined = sm.outputs["Result"]

    # Final multiplier â€” caller sets inputs[1] to opacity
    mult = node_tree.nodes.new("ShaderNodeMath")
    mult.operation = 'MULTIPLY'
    mult.name = f"{TLM_PREFIX}mask_mult_{_next_id()}"
    mult.location = (x + 520, y - 80)
    node_tree.links.new(combined, mult.inputs[0])

    # When the layer also has a paint alpha (PAINT image alpha or PROC fac
    # for emission), combine it INTO the factor: factor = mask Ã— opacity Ã—
    # alpha. Previously the mask path overwrote the alpha contribution, so
    # a transparent paint stroke under a mask still painted as if opaque.
    if layer_alpha is not None:
        am = node_tree.nodes.new("ShaderNodeMath")
        am.operation = 'MULTIPLY'
        am.use_clamp = True
        am.name = f"{TLM_PREFIX}mask_alpha_mult_{_next_id()}"
        am.location = (x + 640, y - 80)
        node_tree.links.new(mult.outputs["Value"], am.inputs[0])
        node_tree.links.new(layer_alpha, am.inputs[1])
        node_tree.links.new(am.outputs["Value"], _factor_socket(mix_node))
    else:
        node_tree.links.new(mult.outputs["Value"], _factor_socket(mix_node))
    return mult


# â”€â”€ Layer width helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    """Legacy helper â€” returns just x positions (used by end_x calculation)."""
    return [pos[0] for pos in _layer_positions(layers, x0, 0)]


# â”€â”€ Per-channel composite builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _build_channel(node_tree, layers, channel_id, uv_map, x0, y_base, x_step):
    """
    Build a compositing chain for one PBR channel.
    Returns the final output socket, or None if no layer contributes.

    channel_id: 'base_color' | 'roughness' | 'metallic' | 'emission' | 'transmission'
    NOTE: 'normal' is handled by _build_normal_channel() instead.
    """
    is_scalar = channel_id in ('roughness', 'metallic', 'transmission', 'alpha')
    is_emission = channel_id == 'emission'

    # Map channel_id â†’ attribute names on TLM_LayerItem
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

        # â”€â”€ Reference layer: reuse another layer's pattern output â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Pattern comes from the referenced layer, blending from this layer.
        # Respects this layer's channel flags, blend_mode, opacity, mask,
        # fill values, and branching overrides â€” only the raw PATTERN
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
            # newly-created nodes with `tlm_frame_owner = layer.name` â€” this
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
                    # _new_img_tex doesn't tag â€” stamp the tex node so it's
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

        # â”€â”€ Determine layer color/value output â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if layer.layer_type == "PAINT":
            out_ch = getattr(layer, 'output_channel', 'AUTO')
            _routed_handled = False

            # Routed PAINT: the main paint image (layer.image) becomes the
            # source for whatever target channel was selected, bypassing the
            # per-channel image_name properties (which the user typically
            # doesn't fill in for routed layers).
            # - target=Alpha â†’ use image's native alpha output (PNG cutout)
            # - target=Roughness/Metallic/Transmission â†’ SeparateColor.R
            # - target=Base Color â†’ Color output
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
                    # everywhere â†’ the paint's R value (typically 0 on
                    # an unpainted canvas) drives the entire surface.
                    # Symptom: paintâ†’Roughness made the whole cube
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
                # Use the painted image as emission source â€” this is the key workflow:
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
                # in the UI for routed layers â€” the only thing the user sees
                # is the color swatch, so we honor that.
                # Black â†’ 0, white â†’ 1, grey 0.5 â†’ 0.5; colors weighted by
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
                # _layer_contributes_to (checked above) already gates this:
                # AUTO requires use_emission, output_channel routing bypasses it.
                # Build Fac mask from procedural pattern, then use emission_color
                # as the glow color. The Fac controls WHERE it glows, not what color.
                fac_out = _build_proc_fac_node(
                    node_tree, layer, f"emis_{i}", x, y, uv_map
                )
                if fac_out is None:
                    continue

                # Unified smooth emission mask: Invert â†’ Power â†’ SmoothStep
                # No more binary LESS_THAN vs soft Power split â€” one continuous pipeline.
                threshold = getattr(layer, 'proc_emission_threshold', 0.0)
                falloff   = getattr(layer, 'proc_emission_falloff', 0.08)
                contrast  = getattr(layer, 'proc_contrast', 0.5)
                exponent  = 1.0 + contrast * 8.0   # range 1.0 â†’ 9.0

                # Step 1: edge = 1 - fac  (invert so mask=1 at cell edges)
                invert = node_tree.nodes.new("ShaderNodeMath")
                invert.operation = 'SUBTRACT'
                invert.name = f"{TLM_PREFIX}emis_inv_{i}"
                invert.location = (x + 80, y - 120)
                invert.inputs[0].default_value = 1.0
                node_tree.links.new(fac_out, invert.inputs[1])
                invert.use_clamp = True

                # Step 2: shaped = edge ^ exponent  (contrast sharpening)
                power = node_tree.nodes.new("ShaderNodeMath")
                power.operation = 'POWER'
                power.name = f"{TLM_PREFIX}emis_pow_{i}"
                power.location = (x + 240, y - 120)
                node_tree.links.new(invert.outputs["Value"], power.inputs[0])
                power.inputs[1].default_value = exponent
                power.use_clamp = True

                # Step 3: mask = smoothstep(threshold, threshold + falloff, shaped)
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

                # â”€â”€ Selective emission â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                # An optional second mask gates WHERE the procedural
                # emission is allowed to light up â€” multiplied in here so
                # the smoothstep above still shapes each lit region's
                # falloff. Disabled by default (selector_type=NONE).
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
            elif is_scalar:
                # Drive roughness/metallic/transmission/alpha from the
                # same procedural colour graph used by Base Color, then
                # convert that colour to a scalar. This keeps ColorRamp,
                # Color 3, Contrast and custom procedural colour topology
                # consistent across every routed output.
                #
                # When this channel is the layer's PRIMARY routing target
                # (output_channel matches), the user expects the procedural
                # pattern to drive the BSDF input directly â€” no multiplier.
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

                _routing_target = {
                    'BASE_COLOR': 'base_color', 'ROUGHNESS': 'roughness',
                    'METALLIC': 'metallic', 'ALPHA': 'alpha',
                }.get(getattr(layer, 'output_channel', 'BASE_COLOR'), 'base_color')

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

        # â”€â”€ Mix with current â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if channel_id == 'alpha':
            current = _new_alpha_math_composite(
                node_tree, layer, current, layer_out, layer_alpha, prev_alpha,
                x, y, i, uv_map
            )
            continue

        if current is None:
            # Emission always mixes against black so opacity=0 â†’ zero emission,
            # even without explicit modulators. For every other channel, only
            # create a mix when a modulator (mask / fresnel / opacity<1) would
            # otherwise be silently dropped. (Bugs #3, #4, #5 consolidation.)
            # Color channels also need a mix whenever a layer_alpha is present
            # â€” otherwise a PAINT layer that's mostly transparent renders its
            # raw RGB on the alpha=0 pixels (e.g. black for an empty canvas).
            # A scalar paint with image alpha needs the modulator mix too,
            # so unpainted pixels (alpha=0) pass through the channel's
            # default value instead of being overwritten with the paint's
            # R=0 â†’ uniform smoothness / zero metallic / etc. on the
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
            # Same factor pipeline as the color path (opacity Ã— alpha Ã— mask
            # Ã— fresnel Ã— clipping). Without this call, scalar channels
            # ignored mask/fresnel/clipping and used opacity straight as
            # the Factor â€” feature parity break vs Base Color.
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
        # factor: factor = mask Ã— opacity Ã— alpha. The mask alone is not
        # enough â€” a transparent paint stroke must remain transparent
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
        # Factor = opacity Ã— layer_alpha (whenever alpha exists).
        # Previous code only folded alpha into the factor when the blend
        # mode was MIX, on the (wrong) assumption that "non-MIX modes
        # ignore alpha". The alpha is COVERAGE, not a colour input â€” it
        # tells the Mix where the layer "exists". Without it, a paint
        # layer in DIVIDE/SCREEN/OVERLAY/etc. shows through transparent
        # pixels because Factor=opacity=1.0 ignores empty paint.
        # Now wired uniformly:
        #   layer_alpha present â†’ MULTIPLY(alpha, opacity) â†’ Factor
        #   no alpha            â†’ default_value = opacity
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

    # â”€â”€ Fresnel mask: multiply the current factor by a Fresnel output â”€â”€â”€â”€â”€
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
            # Factor is driven by a link â€” reroute through Fresnel multiply
            src = existing_link.from_socket
            node_tree.links.remove(existing_link)
            node_tree.links.new(src, fmult.inputs[0])
        else:
            # Factor is a constant â€” use its value
            fmult.inputs[0].default_value = factor_sock.default_value
        node_tree.links.new(fresnel_fac, fmult.inputs[1])
        node_tree.links.new(fmult.outputs["Value"], factor_sock)


# â”€â”€ Coordinate normalization helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    # we pick the first one found â€” usually the active object during editing)
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


# â”€â”€ Coordinate transform helper (polar / spherical / swirl / cylindrical) â”€â”€â”€â”€

def _inject_coord_transform(node_tree, layer, coord_out, x, y, name_tag=""):
    """Apply a polar/spherical/swirl/cylindrical coordinate transformation.

    Converts the input Cartesian vector into a transformed space that creates
    circular, spherical, or spiral texture patterns.  The transform is applied
    BEFORE the Mapping node so that Location/Scale in Mapping act on the
    transformed coordinates (intuitive angular/radial control).

    NONE         â†’ passthrough (returns coord_out unchanged)
    POLAR        â†’ (atan2(y,x)/2Ï€ + 0.5, sqrt(xÂ²+yÂ²), z)
    SPHERICAL    â†’ (atan2(y,x)/2Ï€ + 0.5, acos(z/r)/Ï€, 0)
    SWIRL        â†’ (x,y) rotated around Z by amount*radius, z preserved
    CYLINDRICAL  â†’ (atan2(y,x)/2Ï€ + 0.5, z, sqrt(xÂ²+yÂ²))

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

    # Helper: build atan2(y, x) â†’ Math node, returns output socket
    def _atan2(yy, xx, ny=30):
        n = node_tree.nodes.new("ShaderNodeMath")
        n.operation = 'ARCTAN2'
        n.name = f"{TLM_PREFIX}ctx_atan_{name_tag}_{_next_id()}"
        n.location = (x - 360, y - ny)
        node_tree.links.new(yy, n.inputs[0])
        node_tree.links.new(xx, n.inputs[1])
        return n.outputs[0]

    # Helper: normalize angle atan_out â†’ atan/(2Ï€) + 0.5, returns socket
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
        # Rotate (x, y) around Z by (amount * radius) â€” spiral twist.
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
        # Z = radius_xy          (distance from axis â€” useful as depth)
        angle = _norm_angle(_atan2(y_sock, x_sock))
        radius = _radius_xy()
        node_tree.links.new(angle, comb.inputs["X"])
        node_tree.links.new(z_sock, comb.inputs["Y"])
        node_tree.links.new(radius, comb.inputs["Z"])

    return comb.outputs["Vector"]


# â”€â”€ Vector distortion helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _inject_vector_distortion(node_tree, layer, mapping_out, x, y, name_tag=""):
    """Optionally inject Noise-based vector coordinate distortion.

    Technique: Mapping â†’ [Noise Texture â†’ Mix(Linear Light, low Factor)] â†’ Texture
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


# â”€â”€ Material-level alpha blend method â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Map TLM's alpha_blend_method enum to:
#   - mat.blend_method (Blender â‰¤ 4.1, legacy enum)
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


def _sync_material_alpha_method(material, alpha_connected, tlm):
    """Set the material's blend method based on the alpha pipeline state.

    Even when TLM correctly wires something to BSDF.Alpha, Eevee with
    mat.blend_method='OPAQUE' (the default for new materials) silently
    ignores the input and renders the surface opaque. This was the
    single biggest UX surprise for cutout / decal / foliage workflows â€”
    "I set output_channel to Alpha but nothing happens".

    Behaviour:
      - tlm.alpha_blend_method == 'AUTO' (default):
          * alpha_connected â†’ HASHED (good general default â€” supports
            smooth alpha, anti-aliased edges, no manual sorting)
          * else â†’ OPAQUE (no perf overhead when alpha isn't used)
      - any other value: forced regardless of connection state. Users
        who explicitly want BLEND for a glass material keep that even
        when no alpha layer is present.

    The Blender 4.2+ API replaced ``mat.blend_method`` with
    ``mat.surface_render_method`` (only DITHERED / BLENDED â€” CLIP and
    HASHED collapsed). We write to whichever attribute exists so the
    same TLM addon works on 4.0 / 4.1 / 4.2 / 5.0 without branching at
    install time.
    """
    requested = getattr(tlm, 'alpha_blend_method', 'AUTO')
    if requested == 'AUTO':
        resolved = 'HASHED' if alpha_connected else 'OPAQUE'
    else:
        resolved = requested

    # In Blender 5.0, forcing surface_render_method='DITHERED' while TLM has
    # no alpha route can make Cycles show a black material. For AUTO/no-alpha,
    # do not touch the new API. On legacy Blender versions that only expose
    # blend_method, still reset to OPAQUE so Eevee leaves transparency mode.
    if not alpha_connected and requested == 'AUTO':
        if not hasattr(material, 'surface_render_method') and hasattr(material, 'blend_method'):
            try:
                material.blend_method = 'OPAQUE'
            except (TypeError, AttributeError):
                pass
        return

    # Legacy attribute (Blender â‰¤ 4.1) â€” still respected on 4.2+ as a
    # deprecated alias. Safe to write only when we have a non-default
    # reason to (alpha actually wired, or user explicitly overrode).
    if hasattr(material, 'blend_method'):
        try:
            material.blend_method = _LEGACY_BLEND_METHOD.get(resolved, 'OPAQUE')
        except (TypeError, AttributeError):
            pass

    # New attribute (Blender 4.2+). Only set BLENDED for explicit BLEND
    # mode; otherwise DITHERED is the right transparency style for
    # cutout / hashed and is safe when alpha is genuinely driven.
    if alpha_connected and hasattr(material, 'surface_render_method'):
        try:
            material.surface_render_method = _NEW_RENDER_METHOD.get(resolved, 'DITHERED')
        except (TypeError, AttributeError):
            pass


# â”€â”€ Selective emission selector â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _build_emission_selector(node_tree, layer, uv_map, x, y, name_tag=""):
    """Build a 0..1 mask that gates where a procedural emission can glow.

    Returns a Value socket suitable for multiplying into the emission
    mask, or None when the selector is disabled (or invalid). The
    selector is applied AFTER the proc fac â†’ invert â†’ power â†’ smoothstep
    pipeline, so the original threshold/falloff still shape each glow's
    falloff â€” the selector only decides which regions are allowed to
    light up at all.

    Three modes:
      - RANDOM_CELLS: Voronoi(F1) â†’ WhiteNoise hash on cell position â†’
        threshold. Produces a per-cell on/off mask. The de-facto
        sci-fi-panel mode: "30% of the cells glow".
      - NOISE: Perlin noise â†’ smoothstep threshold. Produces organic
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
        # User asked for nothing lit â€” short-circuit with a 0 constant
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
            return None  # no image assigned â†’ treat as disabled
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
    # rest of the layer (UVMap â†’ optional seed offset â†’ procedural).
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
        # only cells whose hash > 0.7 light up â†’ roughly 30% lit.
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


# â”€â”€ Voronoi random-per-cell helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _voronoi_fac(node_tree, layer, tex_node, x, y, name_tag=""):
    """Return the Fac socket for a Voronoi node.

    Default: tex_node.outputs["Distance"] â€” smooth cell-distance gradient.
    Random-per-cell: tex_node.outputs["Position"] â†’ WhiteNoise â†’ Value,
    giving each cell a discrete random scalar that feeds the ColorRamp.
    """
    if not getattr(layer, 'proc_voronoi_random_color', False):
        return tex_node.outputs.get("Distance") or tex_node.outputs[0]

    position_out = tex_node.outputs.get("Position")
    if position_out is None:
        # Position not available â€” fall back gracefully
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


# â”€â”€ Fresnel mask helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _build_fresnel_mask(node_tree, layer, x, y, name_tag=""):
    """Build a Fresnel node that outputs a 0-1 mask based on viewing angle.

    Returns the Fac output socket, or None if Fresnel is not enabled.
    Edge-on faces â†’ 1.0 (visible), face-on â†’ 0.0 (hidden).
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
    # user dragged the strength slider down â€” it returned True (silently
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


# â”€â”€ Procedural node builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _build_procedural_node(node_tree, layer, uv_map, x, y):
    """
    Build the shader nodes for a PROCEDURAL layer.
    Returns (color_out, alpha_out) sockets.
    The output is always a color: proc_color1 â†’ proc_color2 mapped via the
    texture's Fac output through a ColorRamp for maximum control.
    """
    # â”€â”€ Texture coordinate + mapping â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

    # â”€â”€ Vector distortion (organic coordinate warping) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    vec_out = _inject_vector_distortion(
        node_tree, layer, mapping.outputs["Vector"], x, y, name_tag="proc"
    )

    # â”€â”€ Texture node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
        # ShaderNodeTexBrick â€” color1/color2 are brick variants, color3
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
        # ShaderNodeTexMagic â€” kaleidoscopic colored swirl. depth
        # controls fractal iterations; distortion warps the swirls.
        # Note: Magic uses its own dedicated proc_magic_distortion (not
        # the shared proc_distortion) because the canonical "swirl" look
        # needs distortion â‰ˆ 1.0, while the shared default is 0.0 which
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
        # ShaderNodeTexWhiteNoise â€” pure per-pixel random. Uses the
        # standard Value output (scalar) as fac so the ColorRamp
        # downstream maps it via Color1 â†’ Color2.
        tex_node = node_tree.nodes.new("ShaderNodeTexWhiteNoise")
        tex_node.noise_dimensions = '3D'
        fac_out = tex_node.outputs["Value"]

    elif pt == 'FRESNEL':
        # View-angle gradient procedural â€” the layer's fac comes from a
        # Fresnel node (0 facing the camera, 1 at grazing silhouette). The
        # standard ColorRamp downstream maps that gradient via Color1â†’Color2
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
        # Inside dot (distance < radius) â†’ 1.0; outside (> radius) â†’ 0.0.
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
        # crests â€” ideal for mountain ridges, rock veins, lightning,
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
        fold.inputs[2].default_value = -1.0  # -1 â†’ range -1..1
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
        inv.inputs[0].default_value = 1.0   # 1 - |â€¦|
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
        # sharpness=1.0 â†’ minimum smoothstep band (razor edge).
        # sharpness=0.0 â†’ full-width gradient (soft fissure).
        band = max(0.003, (1.0 - sharpness) * width * 0.8)

        mr = node_tree.nodes.new("ShaderNodeMapRange")
        try:
            mr.interpolation_type = 'SMOOTHSTEP'
        except Exception:
            pass
        mr.clamp = True
        # Inside crack (distance < width) â†’ 1.0; outside â†’ 0.0.
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
        # ShaderNodeTexGabor (Blender 4.3+) â€” anisotropic Gabor noise.
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
            # 2D Gabor â€” orientation is a single angle in radians.
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
        # SAW gives a clean 0â†’1 ramp per period; Map Range with a
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
            # Inner core stripe â€” narrower than the main stripe by
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
    # nodes do â€” link the procedural coord chain into the Vector socket
    # except for FRESNEL which is angle-based, not spatial.
    if "Vector" in tex_node.inputs:
        node_tree.links.new(vec_out, tex_node.inputs["Vector"])

    # â”€â”€ ColorRamp: map Fac â†’ Color1..Color2 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Delegated to _build_proc_color_ramp so all proc_types share the
    # same ramp construction (Manual Stops, color mode, interpolation,
    # extra stops, legacy Color 3). See helper near top of file.
    return _build_proc_color_ramp(node_tree, layer, x, y, fac_out), None

def _wrap_adjustment_opacity(node_tree, layer, original_output, adjusted_output, x, y,
                             channel_id="base_color"):
    """Wrap a colour-channel adjustment chain with an opacity-driven Mix.

    Mix(A=original, B=adjusted, factor=opacity) makes layer.opacity behave
    uniformly as "strength" across every adjustment type:
      - opacity = 0 â†’ bypass (output equals input)
      - opacity = 1 â†’ full effect
      - 0 < opacity < 1 â†’ linear blend
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
# default to 'base_color' for backward compatibility â€” older .blends /
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
# accept the math-on-a-float adjustment types â€” HUE_SAT and
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
    unchanged â€” their HSV / RGB math has no scalar interpretation. The
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
        # Fac stays at full strength here â€” the wrapping Mix below
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


# â”€â”€ Group compositing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _composite_layer_list(node_tree, layers, uv_map, start_x, y_base, x_step):
    """Composite a flat list for the base color channel only (used for groups)."""
    return _build_channel(node_tree, layers, 'base_color', uv_map, start_x, y_base, x_step)


# â”€â”€ Main rebuild â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _link_to_bsdf(node_tree, output_socket, bsdf, input_names, channel_label):
    """Try to link output_socket to one of the BSDF input_names. Logs on failure."""
    for name in input_names:
        if name in bsdf.inputs:
            node_tree.links.new(output_socket, bsdf.inputs[name])
            print(f"[TLM] Connected {channel_label} â†’ BSDF.{name}")
            return
    print(f"[TLM] ERROR: could not connect {channel_label} â€” "
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


def _defer_rebuild(material):
    """Schedule a rebuild via timer when we can't do it right now."""
    from . import properties
    properties._pending_materials.add(material.name)
    if len(properties._pending_materials) == 1:
        import bpy
        bpy.app.timers.register(properties._do_deferred_rebuild, first_interval=0.05)


def rebuild_node_tree(material):
    # Cancel any pending deferred rebuild â€” this explicit call supersedes it.
    from . import properties
    properties.cancel_pending_rebuild(material.name)

    # Reset deterministic counter so node names match between rebuilds
    global _node_counter
    _node_counter = 0

    tlm = material.tlm
    perf_started = _time.perf_counter() if performance_enabled(material) else None
    if getattr(tlm, "shader_editable", False):
        print(f"[TLM] Skipping rebuild for '{material.name}' â€” shader is in Editable mode")
        return
    _ensure_unique_layer_names(material)

    node_tree = material.node_tree

    if node_tree is None:
        material.use_nodes = True
        node_tree = material.node_tree

    # Protect all referenced images BEFORE clearing nodes â€” prevents Blender GC
    # from collecting images that become temporarily unreferenced during rebuild
    import bpy as _bpy
    for layer in tlm.layers:
        if layer.layer_type == "PAINT" and layer.image_name:
            img = _bpy.data.images.get(layer.image_name)
            if img:
                img.use_fake_user = True
                # Pack any dirty PAINT image (user painted but didn't save)
                # so the pixels survive the upcoming colorspace / image
                # reassign on the new tex node. Without this, Blender 5.0
                # was discarding fresh paint when output_channel changed
                # because the rebuild looked like an image reload.
                if getattr(img, 'is_dirty', False):
                    try:
                        img.pack()
                    except Exception:
                        pass  # already packed or packing not supported
        # Also protect PBR channel images (roughness, metallic, normal, emission)
        for attr in ('roughness_image_name', 'metallic_image_name',
                     'normal_image_name', 'emission_image_name',
                     'transmission_image_name', 'alpha_image_name',
                     'mask_image_name', 'mask_image_name_b'):
            iname = getattr(layer, attr, "")
            if iname:
                pimg = _bpy.data.images.get(iname)
                if pimg:
                    pimg.use_fake_user = True

    all_layers = list(reversed(tlm.layers))
    group_children = {}
    for layer in all_layers:
        # GROUP layers are always root-level â€” reject any nested-group state
        # that leaked in (e.g. from a hand-edited preset) so they still
        # composite correctly instead of disappearing as a phantom child.
        if layer.group_name and layer.layer_type != "GROUP":
            group_children.setdefault(layer.group_name, []).append(layer)

    # Build root layer list â€” Groups are composited as a unit (not expanded flat)
    # This preserves group alpha for Clipping Mask support.
    # GROUP layers always appear at root regardless of any stray group_name.
    def _is_root(l):
        if not l.visible:
            return False
        if l.layer_type == "GROUP":
            return True
        return not l.group_name
    root_layers = [l for l in all_layers if _is_root(l)]

    # Solo override â€” show only the solo'd layer.
    # Use tlm.layers (not all_layers which is reversed) since solo_layer_index
    # comes from the UIList which indexes into tlm.layers directly.
    #
    # GROUP / ADJUSTMENT / REFERENCE need special handling because they
    # don't make sense in isolation:
    #  - GROUP: keep its children so the group composites as a unit.
    #  - ADJUSTMENT: it modifies the layers BELOW it; soloing it alone
    #    leaves nothing for the adjustment to act on (visible symptom:
    #    the BSDF gets no Base Color input). Include the entire prefix
    #    of layers up to and including the adjustment so the user sees
    #    "the stack with this adjustment applied".
    #  - REFERENCE: pulls its pattern from another layer by name. If the
    #    referenced layer isn't in the solo set the lookup fails and
    #    nothing renders. Include the source layer too.
    solo_idx = tlm.solo_layer_index
    if 0 <= solo_idx < len(tlm.layers):
        solo_layer = tlm.layers[solo_idx]
        if solo_layer.visible:
            if solo_layer.layer_type == "ADJUSTMENT":
                prefix = list(tlm.layers[:solo_idx + 1])
                root_layers = [l for l in prefix if _is_root(l)]
                # Trim group_children to only the groups present in the prefix.
                kept = {l.name for l in prefix if l.layer_type == "GROUP"}
                group_children = {k: v for k, v in group_children.items()
                                  if k in kept}
            elif solo_layer.layer_type == "REFERENCE":
                ref_name = getattr(solo_layer, 'reference_layer_name', '')
                ref_layer = next(
                    (l for l in tlm.layers
                     if l.name == ref_name and l != solo_layer),
                    None,
                )
                if ref_layer is not None:
                    # Stack order: source first so it becomes `current`,
                    # then the REFERENCE composes on top with its own
                    # opacity / mask / blend.
                    root_layers = [ref_layer, solo_layer]
                    # If the source is a group, keep its children too.
                    if ref_layer.layer_type == "GROUP":
                        preserved = group_children.get(ref_layer.name, [])
                        group_children = ({ref_layer.name: preserved}
                                          if preserved else {})
                    else:
                        group_children = {}
                else:
                    # Invalid / missing reference name â€” fall back to the
                    # reference itself; the build path will skip it but
                    # we avoid an empty tree.
                    root_layers = [solo_layer]
                    group_children = {}
            elif solo_layer.layer_type == "GROUP":
                root_layers = [solo_layer]
                preserved = group_children.get(solo_layer.name, [])
                group_children = ({solo_layer.name: preserved}
                                  if preserved else {})
            else:
                root_layers = [solo_layer]
                group_children = {}
        else:
            root_layers = []


    if not root_layers:
        # Nothing to build â€” clear TLM nodes (no layers visible) but don't
        # leave a half-built tree. Sweep the shared TLM_maskblur_*
        # NodeGroups too: without this, deleting/hiding all layers
        # accumulated orphan groups in bpy.data.node_groups across
        # save/reopen cycles (the cleanup at the end of the full rebuild
        # path was being skipped).
        _clear_tlm_nodes(node_tree)
        _cleanup_tlm_mask_blur_groups()
        # If the previous rebuild used the alpha wrap, _clear_tlm_nodes()
        # just removed the Mix Shader that fed Material Output.Surface. Put
        # the plain BSDF back when the Surface socket is now dangling.
        bsdf = _find_bsdf(node_tree)
        if bsdf:
            _ensure_bsdf_to_output(node_tree, bsdf, only_if_surface_empty=True)
        # Alpha can never be wired without layers. On legacy Blender this
        # resets blend_method; on Blender 4.2+/5.0 it intentionally leaves
        # surface_render_method alone to avoid the Cycles black-surface bug.
        _sync_material_alpha_method(material, False, tlm)
        _record_rebuild_performance(material, perf_started, node_tree, root_layers)
        return

    # Guard: verify we can write to nodes before destroying anything.
    # In depsgraph_update_pre handlers Blender blocks .name writes.
    _probe = None
    try:
        _probe = node_tree.nodes.new("ShaderNodeValue")
        _probe.name = f"{TLM_PREFIX}ctx_probe_"  # TLM_ prefix so _clear_tlm_nodes can clean up
        node_tree.nodes.remove(_probe)
    except AttributeError:
        # We're in a restricted context. Clean up orphan probe if created.
        if _probe is not None:
            try:
                node_tree.nodes.remove(_probe)
            except Exception:
                pass  # can't remove either â€” will be cleaned on next rebuild
        _defer_rebuild(material)
        return

    # Generated TLM nodes are laid out deterministically every rebuild. Restoring
    # old name-based positions after layer insertions/removals can collapse
    # different generated nodes onto stale coordinates.
    _saved_custom_links = _save_custom_links(node_tree)
    _clear_tlm_nodes(node_tree)

    expanded = []  # initialized here so _validate_tags can reference it after try/except
    try:
        uv_map  = tlm.uv_map or "UVMap"
        start_x = -1200
        x_step  = 300  # default for _build_base_color / _build_bump_channel

        # For PBR channels we still need a fully expanded list
        expanded = []
        for layer in root_layers:
            if layer.layer_type == "GROUP":
                children = [
                    l for l in group_children.get(layer.name, [])
                    if l.visible
                ]
                expanded.extend(children)
            else:
                expanded.append(layer)

        bsdf = _find_bsdf(node_tree)
        if not bsdf:
            print("[TLM] WARNING: No Principled BSDF found â€” skipping rebuild")
            _restore_custom_links(node_tree, _saved_custom_links)
            _record_rebuild_performance(material, perf_started, node_tree, expanded)
            return
        # Restore BSDFâ†’Surface link if the previous rebuild's alpha wrap
        # (Mix Shader + Transparent) was just removed by _clear_tlm_nodes.
        # If alpha is still needed, the wrap helper re-routes through
        # Mix Shader later in this same rebuild.
        _ensure_bsdf_to_output(node_tree, bsdf)

        # Calculate where the chain ends so BSDF + passthrough nodes go to the right
        # With grid layout, find the maximum x + width across ALL rows
        def _max_end_x(layers, x0):
            if not layers:
                return x0
            positions = _layer_positions(layers, x0, 0)
            return max(px + _layer_width(layers[j])
                       for j, (px, _py) in enumerate(positions))

        end_x = max(_max_end_x(root_layers, start_x),
                    _max_end_x(expanded, start_x)) + 100

        # â”€â”€ Channel y positions â€” spaced 400px apart â”€â”€
        ch_y = {
            channel: -idx * _CHANNEL_Y_GAP
            for idx, channel in enumerate(_CHANNEL_LAYOUT_ORDER)
        }

        route_x = end_x + _CHANNEL_ROUTE_OFFSET
        active_channels = ['base_color']
        if _channel_used(expanded, 'use_roughness'):
            active_channels.append('roughness')
        if _channel_used(expanded, 'use_metallic'):
            active_channels.append('metallic')
        if _channel_used(expanded, 'use_normal') or _channel_used(expanded, 'use_bump'):
            active_channels.append('normal')
        if _channel_used(expanded, 'use_emission'):
            active_channels.append('emission')
        if _channel_used(expanded, 'use_transmission'):
            active_channels.append('transmission')
        if (_channel_used(expanded, 'use_alpha')
                or getattr(tlm, 'use_base_color_alpha', False)):
            active_channels.append('alpha')
        lane_values = [ch_y[ch] for ch in active_channels]
        shader_y = (max(lane_values) + min(lane_values)) * 0.5
        shader_x = end_x + 540
        output_x = end_x + 900

        bsdf.location = (shader_x, shader_y)
        mat_out = next((n for n in node_tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)
        if mat_out:
            mat_out.location = (output_x, shader_y)

        # Apply material-level BSDF IOR (default 1.45 glass; ice = 1.31,
        # water = 1.33, diamond = 2.42). Only set when the IOR socket
        # exists and isn't user-linked — preserve any manual wiring.
        try:
            ior_in = bsdf.inputs.get("IOR")
            if ior_in is not None and not ior_in.is_linked:
                ior_in.default_value = getattr(tlm, 'bsdf_ior', 1.45)
        except (AttributeError, KeyError):
            pass

        # ── Volume shaders (Absorption + Scatter) ─────────────────────────
        # Three valid configurations:
        #   - only Absorption ON → wire absorption.Volume → Output.Volume
        #   - only Scatter ON    → wire scatter.Volume → Output.Volume
        #   - BOTH ON            → Add Shader(abs.Volume, scat.Volume) → Output.Volume
        # Always tear down first so toggle-off cleanly leaves no orphans.
        if mat_out is not None:
            stale = [n for n in node_tree.nodes
                     if n.bl_idname in ('ShaderNodeVolumeAbsorption',
                                        'ShaderNodeVolumeScatter')
                     and n.name.startswith(TLM_PREFIX)]
            # Also tear down any TLM-managed Add Shader used for volume combine
            stale_add = [n for n in node_tree.nodes
                         if n.bl_idname == 'ShaderNodeAddShader'
                         and n.name.startswith(f"{TLM_PREFIX}volume_combine")]
            for n in stale + stale_add:
                node_tree.nodes.remove(n)

            vol_in = mat_out.inputs.get("Volume")
            if vol_in is not None and vol_in.is_linked:
                # Clear any existing link on Output.Volume — TLM owns this socket
                for l in list(node_tree.links):
                    if l.to_node is mat_out and l.to_socket is vol_in:
                        node_tree.links.remove(l)

            use_abs = getattr(tlm, 'use_volume_absorption', False)
            use_sct = getattr(tlm, 'use_volume_scatter', False)

            abs_node = None
            sct_node = None
            if use_abs and vol_in is not None:
                abs_node = node_tree.nodes.new('ShaderNodeVolumeAbsorption')
                abs_node.name = f"{TLM_PREFIX}volume_abs_{_next_id()}"
                abs_node.label = "Volume Absorption (TLM)"
                abs_node.location = (shader_x, shader_y - 300)
                abs_node.inputs["Color"].default_value = getattr(
                    tlm, 'volume_absorption_color', (0.55, 0.75, 0.95, 1.0)
                )
                abs_node.inputs["Density"].default_value = getattr(
                    tlm, 'volume_absorption_density', 1.0
                )
            if use_sct and vol_in is not None:
                sct_node = node_tree.nodes.new('ShaderNodeVolumeScatter')
                sct_node.name = f"{TLM_PREFIX}volume_sct_{_next_id()}"
                sct_node.label = "Volume Scatter (TLM)"
                sct_node.location = (shader_x, shader_y - 460)
                sct_node.inputs["Color"].default_value = getattr(
                    tlm, 'volume_scatter_color', (0.92, 0.96, 1.0, 1.0)
                )
                sct_node.inputs["Density"].default_value = getattr(
                    tlm, 'volume_scatter_density', 0.5
                )
                ani_in = sct_node.inputs.get("Anisotropy")
                if ani_in is not None:
                    ani_in.default_value = getattr(
                        tlm, 'volume_scatter_anisotropy', 0.0
                    )

            # Wire to Output.Volume based on which shaders are on
            if abs_node and sct_node:
                add = node_tree.nodes.new('ShaderNodeAddShader')
                add.name = f"{TLM_PREFIX}volume_combine_{_next_id()}"
                add.label = "Volume Combine (TLM)"
                add.location = (shader_x + 220, shader_y - 380)
                node_tree.links.new(abs_node.outputs["Volume"], add.inputs[0])
                node_tree.links.new(sct_node.outputs["Volume"], add.inputs[1])
                node_tree.links.new(add.outputs[0], vol_in)
            elif abs_node:
                node_tree.links.new(abs_node.outputs["Volume"], vol_in)
            elif sct_node:
                node_tree.links.new(sct_node.outputs["Volume"], vol_in)

        # â”€â”€ Base Color â€” built from root_layers to preserve GROUP alpha for clipping mask â”€
        bc_out, bc_alpha = _build_base_color(node_tree, root_layers, group_children, uv_map, start_x, ch_y['base_color'], x_step)
        if bc_out:
            bc_out = _route_through_user_slot(
                node_tree, material, 'base_color', bc_out,
                end_x, ch_y['base_color'],
            )
            # Anime/toon mode: route base_color â†’ BSDF.Emission Color instead
            # of Base Color. This bypasses Cycles' diffuse cosine attenuation
            # so cel-shading NDOTL bands appear as flat saturated colours,
            # not multiplied by cos(NdotL). Set Base Color = BLACK so the
            # diffuse component contributes 0.
            if getattr(material.tlm, 'use_emission_output', False):
                _link_channel_to_bsdf(
                    node_tree, bc_out, bsdf, ["Emission Color", "Emission"],
                    "base_color", route_x, ch_y['base_color'],
                )
                # Force Base Color â†’ BLACK and Emission Strength = 1
                bc_socket = bsdf.inputs.get("Base Color")
                if bc_socket is not None:
                    bc_socket.default_value = (0.0, 0.0, 0.0, 1.0)
                es_socket = bsdf.inputs.get("Emission Strength")
                if es_socket is not None and not es_socket.is_linked:
                    es_socket.default_value = 1.0
                # Force matte BSDF â€” emission overrides anyway, but matte
                # ensures no extra spec leak
                rs_socket = bsdf.inputs.get("Roughness")
                if rs_socket is not None and not rs_socket.is_linked:
                    rs_socket.default_value = 1.0
                ms_socket = bsdf.inputs.get("Metallic")
                if ms_socket is not None and not ms_socket.is_linked:
                    ms_socket.default_value = 0.0
            else:
                _link_channel_to_bsdf(
                    node_tree, bc_out, bsdf, ["Base Color", "base_color"],
                    "base_color", route_x, ch_y['base_color'],
                )

        # â”€â”€ Roughness â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Same architecture as Base Color: a per-layer Mix chain ending in
        # the BSDF input. The Math ADD passthrough that used to sit here was
        # cosmetic (added 0.0 to the value) and confused the debug â€” the
        # graph for Roughness now mirrors Base Color, just with Float-typed
        # mixes instead of Color.
        if _channel_used(expanded, 'use_roughness'):
            r_out = _build_channel(node_tree, expanded, 'roughness', uv_map, start_x, ch_y['roughness'], x_step)
            if r_out:
                r_out = _route_through_user_slot(
                    node_tree, material, 'roughness', r_out,
                    end_x, ch_y['roughness'],
                )
                _link_channel_to_bsdf(
                    node_tree, r_out, bsdf,
                    ["Roughness", "Specular Roughness"], "roughness",
                    route_x, ch_y['roughness'],
                )

        # â”€â”€ Metallic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if _channel_used(expanded, 'use_metallic'):
            m_out = _build_channel(node_tree, expanded, 'metallic', uv_map, start_x, ch_y['metallic'], x_step)
            if m_out:
                m_out = _route_through_user_slot(
                    node_tree, material, 'metallic', m_out,
                    end_x, ch_y['metallic'],
                )
                _link_channel_to_bsdf(
                    node_tree, m_out, bsdf,
                    ["Metallic", "Metalness"], "metallic",
                    route_x, ch_y['metallic'],
                )

        # â”€â”€ Normal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        normal_out = None
        if _channel_used(expanded, 'use_normal'):
            normal_out = _build_normal_channel(
                node_tree, expanded, uv_map, start_x, ch_y['normal'], x_step
            )

        # â”€â”€ Emission â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if _channel_used(expanded, 'use_emission'):
            # If anime/toon emission_output mode is on, the base_color stack
            # already drives BSDF.Emission Color. Building the emission
            # channel here would just produce orphan node groups (built but
            # never connected to anything because we skip the link). Skip
            # the whole emission build entirely in that case.
            if getattr(material.tlm, 'use_emission_output', False):
                pass  # emission channel build skipped â€” base_color drives Emission
            else:
                e_out = _build_channel(node_tree, expanded, 'emission', uv_map, start_x, ch_y['emission'], x_step)
                if e_out:
                    e_out = _route_through_user_slot(
                        node_tree, material, 'emission', e_out,
                        end_x, ch_y['emission'],
                    )
                    _link_channel_to_bsdf(
                        node_tree, e_out, bsdf,
                        ["Emission Color", "Emission", "emission"], "emission",
                        route_x, ch_y['emission'],
                    )
                # Use max emission strength weighted by opacity (skipped in
                # emission_output mode â€” base_color stack drives Emission at
                # strength 1.0 fixed for true flat cel-shading)
                if not getattr(material.tlm, 'use_emission_output', False):
                    strengths = [(l.emission_strength * l.opacity) for l in expanded if l.use_emission]
                    if strengths:
                        val = node_tree.nodes.new("ShaderNodeValue")
                        val.name = f"{TLM_PREFIX}emission_strength"
                        val.outputs[0].default_value = max(strengths)
                        val.location = (end_x, ch_y['emission'])
                        _tag(val, "__material__", "emission_strength")
                        _link_channel_to_bsdf(
                            node_tree, val.outputs[0], bsdf,
                            ["Emission Strength", "emission_strength"],
                            "emission_strength", route_x, ch_y['emission'] - 140,
                        )

        # â”€â”€ Transmission â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if _channel_used(expanded, 'use_transmission'):
            t_out = _build_channel(node_tree, expanded, 'transmission', uv_map, start_x, ch_y['transmission'], x_step)
            if t_out:
                passthrough = node_tree.nodes.new("ShaderNodeMath")
                passthrough.operation = 'ADD'
                passthrough.name = f"{TLM_PREFIX}trans_pass"
                passthrough.inputs[1].default_value = 0.0
                passthrough.use_clamp = True
                passthrough.location = (end_x, ch_y['transmission'])
                node_tree.links.new(t_out, passthrough.inputs[0])
                t_slot_out = _route_through_user_slot(
                    node_tree, material, 'transmission', passthrough.outputs["Value"],
                    end_x + 180, ch_y['transmission'],
                )
                _link_channel_to_bsdf(
                    node_tree, t_slot_out, bsdf,
                    ["Transmission Weight", "Transmission", "transmission"],
                    "transmission", route_x, ch_y['transmission'],
                )

        # â”€â”€ Alpha â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Drives the BSDF Alpha input (surface opacity / cutout). Distinct from
        # transmission (which is volumetric). Useful for foliage cards, decals,
        # masks projected on a surface, etc.
        alpha_explicitly_routed = _channel_used(expanded, 'use_alpha')
        alpha_was_connected = False
        if alpha_explicitly_routed:
            a_out = _build_channel(node_tree, expanded, 'alpha', uv_map, start_x, ch_y.get('alpha', 0), x_step)
            if a_out:
                a_out = _route_through_user_slot(
                    node_tree, material, 'alpha', a_out,
                    end_x, ch_y['alpha'],
                )
                # Wrap through Mix Shader + Transparent BSDF â€” see
                # _wire_alpha_via_transparent_bsdf for the rationale.
                # Cycles in Blender 5.0 doesn't honour Principled BSDF.Alpha
                # alone; the wrap fixes engine-portable transparency.
                a_out = _add_channel_reroute(
                    node_tree, a_out, "alpha", route_x, ch_y['alpha']
                )
                if _wire_alpha_via_transparent_bsdf(node_tree, a_out, bsdf):
                    alpha_was_connected = True
                else:
                    # Fallback if no Material Output present
                    _link_to_bsdf(node_tree, a_out, bsdf,
                                  ["Alpha", "alpha"], "alpha")
                    alpha_was_connected = True
        elif bc_alpha is not None and getattr(tlm, 'use_base_color_alpha', False):
            # Material-level opt-in: when the user wants the base color's
            # native alpha (typically the PAINT image alpha) to drive
            # surface transparency / bake. OFF by default because an empty
            # PAINT layer (alpha=0 everywhere) would otherwise unintentionally
            # hide the whole cube the moment it's added on top of a Fill.
            bc_alpha = _route_through_user_slot(
                node_tree, material, 'alpha', bc_alpha,
                end_x, ch_y['alpha'],
            )
            bc_alpha = _add_channel_reroute(
                node_tree, bc_alpha, "alpha_auto", route_x, ch_y['alpha']
            )
            if _wire_alpha_via_transparent_bsdf(node_tree, bc_alpha, bsdf):
                alpha_was_connected = True
            else:
                _link_to_bsdf(node_tree, bc_alpha, bsdf,
                              ["Alpha", "alpha"], "alpha-auto")
                alpha_was_connected = True

        # Sync the material's Eevee blend method so the BSDF.Alpha input
        # is actually visible (default 'OPAQUE' silently ignores it).
        _sync_material_alpha_method(material, alpha_was_connected, tlm)

        # â”€â”€ Bump â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Pass incoming_normal=normal_out so each per-layer Bump perturbs the
        # already-blended Normal Map instead of flat shading. _build_bump_channel
        # handles per-layer Strength/Distance + mask/opacity/fresnel via vector
        # mix (same architecture as _build_normal_channel).
        bump_out = None
        if _channel_used(expanded, 'use_bump'):
            bump_out = _build_bump_channel(
                node_tree, expanded, uv_map, start_x, ch_y['bump'], x_step,
                incoming_normal=normal_out,
            )

        # â”€â”€ Connect final normal to BSDF â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Bump takes priority: it already incorporates the Normal Map via the
        # first Bump node's Normal input, and all per-layer blending happens
        # inside _build_bump_channel.
        final_normal = bump_out or normal_out
        if final_normal:
            final_normal = _route_through_user_slot(
                node_tree, material, 'normal', final_normal,
                end_x, ch_y['normal'],
            )
            _link_channel_to_bsdf(
                node_tree, final_normal, bsdf, ["Normal", "normal"],
                "normal", route_x, ch_y['normal'],
            )

        # Position BSDF and Material Output to the right of all channels
        bsdf.location = (shader_x, shader_y)
        mat_out = next((n for n in node_tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)
        if mat_out:
            mat_out.location = (output_x, shader_y)

    except AttributeError as e:
        if "Writing to ID classes in this context is not allowed" in str(e):
            # Blender restricted context (e.g. depsgraph handler from another addon).
            # Schedule a deferred rebuild via timer â€” it will run in a safe context.
            print(f"[TLM] Restricted context detected, deferring rebuild")
            from . import properties as _props
            if not _props._pending_materials:
                _props._pending_materials.add(material.name)
                bpy.app.timers.register(_props._do_deferred_rebuild, first_interval=0.05)
            else:
                _props._pending_materials.add(material.name)
            return
        import traceback
        print("[TLM] ERROR during node tree rebuild:")
        traceback.print_exc()
    except Exception:
        import traceback
        print("[TLM] ERROR during node tree rebuild:")
        traceback.print_exc()

    # Always restore custom links, even if build partially failed.
    _restore_custom_links(node_tree, _saved_custom_links)

    # Group each layer's nodes in a colored NodeFrame for visual organization
    # in the shader editor. Generated positions are already final here.
    _assign_layer_frames(node_tree, expanded)

    # Ensure Blender re-evaluates the node tree after rebuild
    node_tree.update_tag()
    material.update_tag()

    _validate_tags(node_tree, expanded)

    # Sweep orphan mask-blur NodeGroups (e.g. left behind when an image was
    # renamed/removed or a layer with blur was deleted).
    _cleanup_tlm_mask_blur_groups()

    _record_rebuild_performance(material, perf_started, node_tree, expanded)


def _build_base_color(node_tree, root_layers, group_children, uv_map, start_x, y_base, x_step):
    """
    Build the base color chain from root_layers, treating each GROUP as a
    composited unit. This preserves proper alpha for Clipping Mask support â€”
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
            # channels â€” applying them here would pollute Base Color.
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
            # kept in sync with it â€” the generic _build_channel handles Reference for
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
                continue  # target not found or cyclic â€” silently skip

            # Snapshot node names before the copy is built so we can retag the
            # newly-created nodes with `tlm_frame_owner = layer.name` â€” this
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
                    # _new_img_tex doesn't tag â€” stamp the tex node so it's
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
            #   - use_mask: ALWAYS meaningful â€” a mask constrains where the
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
                # Neutral white background â€” visible as "empty" wherever the
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
    # present â€” gives the natural "PAINT image with transparency = cube
    # transparent" workflow without requiring a dedicated Output=Alpha layer.
    return current, prev_alpha


def _build_proc_fac_node(node_tree, layer, name_suffix, x, y, uv_map="UVMap"):
    """
    Build TexCoord â†’ Mapping â†’ Texture nodes for a PROCEDURAL layer and
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

    # â”€â”€ Vector distortion (organic coordinate warping) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
        # mask people actually want â€” and worse, when fed through the
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
        # Mirror of FRESNEL path in _build_procedural_node â€” angle-based
        # fac for scalar channels (roughness, metallic, bump) so iridescent-
        # style angle-dependent values can also drive non-colour channels.
        tex = node_tree.nodes.new("ShaderNodeFresnel")
        tex.name = f"{TLM_PREFIX}pfac_fresnel_{name_suffix}"
        tex.location = (x - 100, y)
        tex.inputs["IOR"].default_value = getattr(layer, 'proc_fresnel_ior', 1.45)
        _tag(tex, layer.name, "proc_tex")  # so _hot_proc_fresnel_ior can find it
        fac_out = tex.outputs["Fac"]

    elif pt == 'DOTS':
        # Mirror of DOTS path in _build_procedural_node â€” scalar fac
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
        # Mirror of RIDGED path â€” 5-stage math chain that produces
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
        # Mirror of CRACKS path â€” Voronoi DISTANCE_TO_EDGE thresholded.
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
        # Mirror of the GABOR path in _build_procedural_node â€” provides
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
        # Mirror of the STRIPES path in _build_procedural_node â€” Wave
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


# â”€â”€ Normal map channel builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    modulator but no incoming normal exists yet â€” mixing against the shading
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

        # â”€â”€ UV + optional Mapping (tiling/rotation) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

        # â”€â”€ Image Texture (Non-Color) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        tex = node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        if img.colorspace_settings.name != "Non-Color":
            img.colorspace_settings.name = "Non-Color"
        tex.name = f"{TLM_PREFIX}normal_tex_{_next_id()}"
        tex.location = (x + 100, y)
        _tag(tex, layer.name, "normal_tex")
        node_tree.links.new(vec_out, tex.inputs["Vector"])

        # â”€â”€ Per-layer NormalMap node with individual Strength â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        nm = node_tree.nodes.new("ShaderNodeNormalMap")
        nm.name = f"{TLM_PREFIX}normalmap_{_next_id()}"
        nm.location = (x + 300, y)
        nm.inputs["Strength"].default_value = getattr(layer, 'normal_strength', 1.0)
        _tag(nm, layer.name, "normal_map_node")
        node_tree.links.new(tex.outputs["Color"], nm.inputs["Color"])

        layer_normal = nm.outputs["Normal"]

        # â”€â”€ Blend with previous (vector space, not color!) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # First layer: if the layer has a modulator (mask / opacity<1 / fresnel)
        # we must still mix against a baseline shading normal â€” otherwise the
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
    _set_factor support â€” so per-layer opacity, masks, clipping and fresnel all
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
            # Reuse shared Fac helper â€” avoids duplicating all texture branches.
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

        # â”€â”€ Per-layer Bump node with individual Strength + Distance â”€â”€â”€â”€â”€â”€
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

        # â”€â”€ Blend with previous (vector space) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # First bump layer AND no incoming normal â†’ no baseline to mix
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
    'BASE_COLOR': 'base_color',
    'ROUGHNESS':  'roughness',
    'METALLIC':   'metallic',
    'ALPHA':      'alpha',
}

# Per-channel "use_<channel>" property name. Used by both routable
# (roughness/metallic/alpha) and non-routable (normal/emission/transmission/
# bump) channels â€” see _layer_contributes_to for the OR-logic.
_CHANNEL_USE_FLAG = {
    'roughness':    'use_roughness',
    'metallic':     'use_metallic',
    'alpha':        'use_alpha',
    'normal':       'use_normal',
    'emission':     'use_emission',
    'transmission': 'use_transmission',
    'bump':         'use_bump',
}


def _layer_contributes_to(layer, channel_id):
    """Resolve whether `layer` contributes to `channel_id`.

    Cumulative semantics (OR-logic):
    - output_channel = main routing target. Always contributes to that
      one channel (BASE_COLOR / ROUGHNESS / METALLIC / ALPHA).
    - use_<channel> toggles = additional channels. A layer routed to
      ROUGHNESS with use_metallic=True drives BOTH roughness AND metallic.
      A layer routed to BASE_COLOR with use_normal=True drives base color
      AND normal map.

    base_color has no use_base_color flag â€” it's reachable only as a
    routing target (the most common case anyway).

    Legacy 'AUTO' (older .blend files) maps to 'BASE_COLOR'.
    """
    out_ch = getattr(layer, 'output_channel', 'BASE_COLOR')
    if out_ch == 'AUTO':
        out_ch = 'BASE_COLOR'

    # Main routing target â€” always contributes here.
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
        'use_bump': 'bump',
    }
    channel_id = flag_to_channel.get(flag_attr)
    if channel_id is None:
        # Unknown flag â€” fall back to legacy direct attribute check.
        return any(getattr(l, flag_attr, False) for l in layers)
    return any(_layer_contributes_to(l, channel_id) for l in layers)


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


def _ensure_bsdf_to_output(node_tree, bsdf, only_if_surface_empty=False):
    """Make sure BSDF.BSDF â†’ MaterialOutput.Surface is connected.

    Called after _clear_tlm_nodes removes the alpha-wrap (Mix Shader +
    Transparent BSDF). Without this, removing the wrap would leave
    Material Output's Surface input dangling â†’ solid black render.
    Idempotent: no-op if already connected to the BSDF directly. When
    only_if_surface_empty is true, custom non-TLM Surface links are left alone.
    """
    mat_out = next((n for n in node_tree.nodes
                    if n.type == 'OUTPUT_MATERIAL'
                    and not n.name.startswith(TLM_PREFIX)), None)
    if mat_out is None:
        return False
    surface_input = mat_out.inputs.get("Surface")
    if surface_input is None:
        return False
    # Skip if already connected to this BSDF
    for link in surface_input.links:
        if link.from_socket.node == bsdf:
            return True
    if only_if_surface_empty and surface_input.links:
        return False
    try:
        node_tree.links.new(bsdf.outputs["BSDF"], surface_input)
        return True
    except Exception:
        return False


def _wire_alpha_via_transparent_bsdf(node_tree, alpha_out, bsdf):
    """Route alpha through Mix Shader + Transparent BSDF for engine portability.

    Why this exists:
      Blender 5.0 Cycles doesn't reliably honour a value driven into
      Principled BSDF.Alpha â€” the surface stays opaque even when the
      alpha is meant to be 0 (user-reported: object placed behind the
      cube is not visible through alpha=0 regions in Cycles, while
      Eevee correctly shows the cutout). The portable, engine-agnostic
      pattern is to wrap the surface output:

          Principled BSDF â”€â”€â”
                            â”œâ”€â”€ Mix Shader (factor = alpha) â”€â”€ Output
          Transparent BSDF â”€â”˜

      Mix Shader factor convention: 0 â†’ input 1, 1 â†’ input 2. So we
      put Transparent in slot 1 and Principled in slot 2, giving
      alpha = 0 â†’ invisible, alpha = 1 â†’ opaque. Works identically in
      both Eevee and Cycles regardless of blend_method or
      surface_render_method.

    Also keeps the alpha â†’ BSDF.Alpha connection (for Eevee bake paths
    and for any external tool that reads BSDF.Alpha directly).
    """
    mat_out = next((n for n in node_tree.nodes
                    if n.type == 'OUTPUT_MATERIAL'
                    and not n.name.startswith(TLM_PREFIX)), None)
    if mat_out is None:
        return False
    surface_input = mat_out.inputs.get("Surface")
    if surface_input is None:
        return False

    # Connect alpha to BSDF.Alpha too â€” preserves the previous Eevee
    # path and the bake operator's "read BSDF.Alpha to get alpha
    # output" assumption.
    if "Alpha" in bsdf.inputs:
        try:
            node_tree.links.new(alpha_out, bsdf.inputs["Alpha"])
        except Exception:
            pass

    trans = None
    mix = None
    try:
        trans = node_tree.nodes.new("ShaderNodeBsdfTransparent")
        trans.name = f"{TLM_PREFIX}alpha_transparent"
        trans.location = (bsdf.location.x + 220, bsdf.location.y - 200)

        mix = node_tree.nodes.new("ShaderNodeMixShader")
        mix.name = f"{TLM_PREFIX}alpha_mix_shader"
        mix.location = (bsdf.location.x + 440, bsdf.location.y)

        node_tree.links.new(trans.outputs["BSDF"], mix.inputs[1])
        node_tree.links.new(bsdf.outputs["BSDF"], mix.inputs[2])
        node_tree.links.new(alpha_out, mix.inputs[0])  # Fac

        # Drop the existing BSDF â†’ Surface link only after the replacement
        # shader is internally complete. If the final Surface link fails, the
        # except block restores the plain BSDF route.
        for link in list(surface_input.links):
            if link.from_socket.node == bsdf:
                node_tree.links.remove(link)
        node_tree.links.new(mix.outputs["Shader"], surface_input)
    except Exception:
        _ensure_bsdf_to_output(node_tree, bsdf, only_if_surface_empty=True)
        for node in (mix, trans):
            if node is not None:
                try:
                    node_tree.nodes.remove(node)
                except Exception:
                    pass
        return False
    return True


# â”€â”€ Flatten â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def flatten_to_single_image(material, output_image_name, resolution=(1024, 1024)):
    """Bake the composited Base Color chain to an image via Diffuse BSDF.

    Routes the TLM chain through a temporary Diffuse BSDF so the bake
    captures raw colour without Principled BSDF influence (metallic would
    zero-out diffuse, fresnel would alter colors).  A plain Diffuse BSDF
    has none of those complications and pass_filter={'COLOR'} strips lighting.
    Temporarily switches to Cycles if needed (baking requires Cycles).
    """
    scene = bpy.context.scene
    orig_engine = scene.render.engine
    orig_samples = scene.cycles.samples

    # Baking requires Cycles â€” switch temporarily if needed
    if orig_engine != 'CYCLES':
        scene.render.engine = 'CYCLES'

    # Use minimal samples for speed
    scene.cycles.samples = 1

    rebuild_node_tree(material)
    node_tree = material.node_tree

    if output_image_name in bpy.data.images:
        out_img = bpy.data.images[output_image_name]
        out_img.scale(*resolution)
    else:
        out_img = bpy.data.images.new(output_image_name, *resolution, alpha=True)

    # Find BSDF and Material Output
    bsdf = _find_bsdf(node_tree)
    mat_output = next((n for n in node_tree.nodes
                       if n.type == 'OUTPUT_MATERIAL'
                       and not n.name.startswith(TLM_PREFIX)), None)
    if not bsdf or not mat_output:
        if orig_engine != 'CYCLES':
            scene.render.engine = orig_engine
        scene.cycles.samples = orig_samples
        raise RuntimeError("No Principled BSDF or Material Output found")

    # Save original Surface connection
    surface_input = mat_output.inputs.get("Surface")
    orig_links = [(lnk.from_socket, lnk.to_socket)
                  for lnk in surface_input.links] if surface_input else []

    # Find what feeds the BSDF Base Color (= our composited chain)
    bc_socket = bsdf.inputs.get("Base Color")
    source_socket = None
    if bc_socket and bc_socket.links:
        source_socket = bc_socket.links[0].from_socket

    # Create temp Diffuse BSDF â†’ Material Output
    diffuse = node_tree.nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.name = f"{TLM_PREFIX}flatten_diffuse"
    diffuse.location = (600, 200)

    if source_socket:
        node_tree.links.new(source_socket, diffuse.inputs["Color"])
    else:
        diffuse.inputs["Color"].default_value = bc_socket.default_value

    node_tree.links.new(diffuse.outputs["BSDF"], surface_input)

    # Bake target
    bake_node = node_tree.nodes.new("ShaderNodeTexImage")
    bake_node.name = f"{TLM_PREFIX}bake_target"
    bake_node.image = out_img
    bake_node.location = (800, 0)
    for n in node_tree.nodes:
        n.select = False
    bake_node.select = True
    node_tree.nodes.active = bake_node

    try:
        bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'},
                            save_mode='INTERNAL')
    finally:
        node_tree.nodes.remove(bake_node)
        node_tree.nodes.remove(diffuse)
        for from_sock, to_sock in orig_links:
            node_tree.links.new(from_sock, to_sock)
        scene.cycles.samples = orig_samples
        if orig_engine != 'CYCLES':
            scene.render.engine = orig_engine

    return out_img


# â”€â”€ Layer frame grouping (visual organization in shader editor) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Discreet tints â€” enough to distinguish at a glance, subdued enough not to
# drown out the colors of the nodes themselves.
_LAYER_FRAME_COLORS = {
    'PAINT':       (0.25, 0.35, 0.55),   # blue
    'FILL':        (0.45, 0.45, 0.45),   # neutral gray
    'PROCEDURAL':  (0.40, 0.25, 0.50),   # purple
    'ADJUSTMENT':  (0.55, 0.55, 0.25),   # yellow
    'REFERENCE':   (0.25, 0.50, 0.35),   # green
    'GROUP':       (0.50, 0.25, 0.25),   # dark red
}
_LAYER_FRAME_FALLBACK = (0.35, 0.35, 0.35)


def _is_chain_role(role):
    """True if the role identifies a chain Mix node â€” these flow between layers
    at the far-right of the channel and would stretch the frame across the
    whole canvas. We leave them outside the frames.

    Covers:
      - `mix`, `mix_{channel}`, `mix_scalar`, `mix_vector` (from `_new_mix*`)
      - `opacity_target_{channel}` (overwrites from `_set_factor` / clipping)

    Preserves inside-frame nodes like `vdist_mix`, `marble_mult`, `mask_*`,
    `proc_*`, `normal_*`, `adj_*`, `fill_*`, `ref_*` â€” they are clustered near
    the layer's source and don't stretch the frame.
    """
    if not role:
        return False
    return (
        role == "mix"
        or role.startswith("mix_")
        or role.startswith("opacity_target_")
    )


# Y distance that separates channel bands in the generated layout. Two tagged
# nodes of the same layer sitting farther apart than this are considered to
# belong to different channel bands and get their own frame. Matches the
# typical channel Y spacing in _build_channel (~300-400 units).
_FRAME_Y_GAP = 250.0
_FRAME_X_GAP = 700.0
_FRAME_X_SPAN = 620.0


def _assign_layer_frames(node_tree, layers):
    """Wrap each layer's SOURCE nodes in colored NodeFrames, split by channel band.

    Multi-channel layers (FILL with img_* per channel, ADJUSTMENT with adj_*
    replicated per channel, etc.) have source nodes placed at DIFFERENT Y
    positions â€” one per channel band. A single frame wrapping all of them
    would span the entire vertical layout with empty middle bands, which is
    exactly what we want to avoid.

    Algorithm per layer:
      1. Collect all candidate nodes (skip chain-mixes, material-level, already
         parented, Frame nodes).
      2. Sort by Y descending.
      3. Split into clusters wherever consecutive Ys differ by more than
         `_FRAME_Y_GAP`.
      4. Wrap each cluster in its own NodeFrame.

    Each resulting frame is:
      - Labeled with `layer.name` (auto-syncs on rename via rebuild)
      - Colored by `layer.layer_type` for quick visual identification
      - Tight around its Y band (no empty middle space)

    A layer that touches 3 channels with distant Y gets 3 small frames, all
    the same color & label â€” the user still sees "these belong to Layer X"
    at a glance, but the canvas stays readable.
    """
    type_by_name = {layer.name: layer.layer_type for layer in layers}

    # 1. Collect candidate nodes per layer.
    # Prefer `tlm_frame_owner` (set on Reference pattern copies so they group
    # under the reference's own frame instead of the source's) and fall back
    # to `tlm_layer` for normal nodes.
    per_layer = {}
    for node in list(node_tree.nodes):
        ln = node.get("tlm_frame_owner") or node.get("tlm_layer")
        if not ln or ln == "__material__":
            continue
        if node.type == 'FRAME':
            continue  # never nest frames in frames
        if node.parent is not None:
            continue  # don't stomp on user-made groupings
        if _is_chain_role(node.get("tlm_role")):
            continue  # chain mixes flow visibly outside the frame
        per_layer.setdefault(ln, []).append(node)

    # 2-4. Cluster by Y band, create one frame per cluster.
    for ln, nodes in per_layer.items():
        if not nodes:
            continue
        color = _LAYER_FRAME_COLORS.get(
            type_by_name.get(ln), _LAYER_FRAME_FALLBACK
        )
        # Sort top-to-bottom on canvas (Blender Y increases upward).
        nodes.sort(key=lambda n: -n.location.y)
        cluster = [nodes[0]]
        for n in nodes[1:]:
            # Gap is measured from cluster's LOWEST node to this one.
            # (cluster is sorted top-down, so cluster[-1] is the lowest so far.)
            if cluster[-1].location.y - n.location.y > _FRAME_Y_GAP:
                _wrap_frame_cluster_by_x(node_tree, cluster, ln, color)
                cluster = [n]
            else:
                cluster.append(n)
        _wrap_frame_cluster_by_x(node_tree, cluster, ln, color)


def _wrap_frame_cluster_by_x(node_tree, nodes, layer_name, color):
    """Split a Y band into smaller frames when nodes sit far apart in X."""
    if not nodes:
        return
    nodes = sorted(nodes, key=lambda n: n.location.x)
    cluster = [nodes[0]]
    for n in nodes[1:]:
        if (n.location.x - cluster[-1].location.x > _FRAME_X_GAP
                or n.location.x - cluster[0].location.x > _FRAME_X_SPAN):
            _wrap_frame_cluster(node_tree, cluster, layer_name, color)
            cluster = [n]
        else:
            cluster.append(n)
    _wrap_frame_cluster(node_tree, cluster, layer_name, color)


def _wrap_frame_cluster(node_tree, nodes, layer_name, color):
    """Create one NodeFrame and parent every node in this cluster to it."""
    if not nodes:
        return
    margin_x = 40.0
    margin_y = 50.0
    min_x = min(n.location.x for n in nodes)
    max_y = max(n.location.y for n in nodes)
    max_x = max(n.location.x + max(getattr(n, "width", 140.0), 140.0)
                for n in nodes)
    min_y = min(n.location.y - max(getattr(n, "height", 100.0), 100.0)
                for n in nodes)
    frame_x = min_x - margin_x
    frame_y = max_y + margin_y

    frame = node_tree.nodes.new("NodeFrame")
    frame.name = f"{TLM_PREFIX}frame_{_next_id()}"
    frame.label = layer_name
    frame.label_size = 20
    frame.location = (frame_x, frame_y)
    try:
        frame.width = max(220.0, max_x - min_x + margin_x * 2.0)
        frame.height = max(160.0, max_y - min_y + margin_y * 2.0)
    except Exception:
        pass
    frame.use_custom_color = True
    frame.color = color
    _tag(frame, layer_name, "layer_frame")
    for n in nodes:
        abs_x = n.location.x
        abs_y = n.location.y
        n.parent = frame
        n.location = (abs_x - frame_x, abs_y - frame_y)


# â”€â”€ Tag validation (debug mode) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_DEBUG_TAGS = False  # flip to True during development


def _validate_tags(node_tree, layers):
    """Post-rebuild check: warn about expected tags that are missing."""
    if not _DEBUG_TAGS:
        return
    visible = [l for l in layers if l.visible and l.layer_type != "ADJUSTMENT"]
    for i, layer in enumerate(visible):
        if i == 0:
            continue  # first visible layer has no mix node
        if not _find_tagged(node_tree, layer.name, "mix_base_color"):
            print(f"[TLM TAG WARNING] Missing ({layer.name}, mix_base_color)")
    # Material-level tags (only warn if channels are actually in use)
    has_emission = any(l.use_emission and l.visible for l in layers)
    if has_emission and not _find_tagged(node_tree, "__material__", "emission_strength"):
        print("[TLM TAG WARNING] Missing (__material__, emission_strength)")
    # Bump is now per-layer (one bump_node per use_bump layer) rather than a
    # single bump_final node. Warn if any use_bump layer is missing its tag.
    for layer in layers:
        if layer.visible and getattr(layer, 'use_bump', False):
            if not _find_tagged(node_tree, layer.name, "bump_node"):
                print(f"[TLM TAG WARNING] Missing ({layer.name}, bump_node)")


def register():
    pass

def unregister():
    pass
