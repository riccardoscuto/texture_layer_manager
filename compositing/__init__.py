"""
compositing.py
The core engine of Texture Layer Manager.

Builds a node tree per PBR channel (Base Color, Roughness, Metallic,
Normal Map, Emission) and connects each chain to the correct input on
the Principled BSDF. Channels are opt-in per layer — only channels that
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

    from .. import properties as _props
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


# ── Hot-update dispatch ──────────────────────────────────────────────────────

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
# tuple — without "alpha" the alpha mix's Factor stayed stale on every
# opacity change (the visible symptom: layers with use_alpha enabled
# appeared to "ignore" opacity until a full rebuild ran).


def _new_img_tex(node_tree, image, uv_map, x, y, colorspace="sRGB", layer=None, tag_role=None):
    """Create an Image Texture node, configured from the layer's mapping
    properties (interpolation / projection / source / extension /
    location / rotation / scale).

    If ``tag_role`` and ``layer`` are both provided, the tex node is
    tagged with (layer.name, tag_role) so it can be located later by
    the hot-update path (_hot_image_swap).

    Box projection (was a custom Triplanar implementation in an earlier
    version) is now Blender's native projection mode on the same node —
    cheaper (one tex node instead of three) and uniform across the rest
    of the engine.
    """
    # ── Paint-canvas pixel preservation (paranoid mode) ──────────────────
    # Two earlier fixes (eed83d6, c5454bd) plugged the known mechanisms by
    # which Blender 5.0's image cache wipes the buffer of a GENERATED
    # paint canvas when output_channel toggles (source flip to FILE,
    # colorspace_settings ping-pong). Reports of pixel loss persist on
    # some flows we haven't isolated yet — maybe `node.image = image`
    # itself triggers a re-decode under specific cache states.
    #
    # Snapshot pixels BEFORE any node attribute writes, then restore at
    # the end if the buffer was zeroed. Only for PAINT layers' main
    # canvas (GENERATED, no filepath) — the case that gets bitten.
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
    # Force the colorspace explicitly — keeps the image consistent with
    # how this layer uses it (sRGB for base color, Non-Color for data).
    #
    # SPECIAL CASE for PAINT layers' MAIN canvas: lock to sRGB regardless
    # of which channel the layer is currently routed to. Why:
    #   - The paint canvas is bpy.data.images.new() = source='GENERATED'
    #     with no filepath on disk.
    #   - When output_channel changes (e.g. Base Color → Roughness),
    #     this code wants the colorspace to flip (sRGB → Non-Color).
    #   - Blender 5.0 flipping colorspace_settings.name on a GENERATED
    #     image WITHOUT a backing file ZEROES the in-memory pixel
    #     buffer (it tries to re-decode from the missing file).
    #   - Symptom: paint pixels disappear every time the user toggles
    #     output_channel.
    # Accepting a small gamma-curve difference when a paint canvas is
    # routed to a scalar channel is much better than losing the user's
    # paint work. The user's paint already looked sRGB in the image
    # editor anyway — the visual mapping stays intuitive.
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
    # are mirrored straight from the layer property → node attribute
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
                    # for a TLM-created paint canvas — switching it
                    # to FILE would wipe the unsaved pixels).
                else:
                    # Non-FILE target (GENERATED / SEQUENCE / MOVIE) —
                    # user explicitly picked it via paint_source.
                    node.image.source = src
        except Exception:
            pass
        # Projection Blend is an INPUT socket on the node — wired only
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

    # Optional Mapping node — inserted only if Location/Rotation/Scale differ
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
    # cheap — read one pixel — and the restore only fires on a real
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
    override — normal and bump have their own math and are unaffected.

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
    the standard blend types (MIX/ADD/MULTIPLY/SUBTRACT/DIVIDE/etc.) — no
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
        # Pre-4.0 fallback — ShaderNodeMixRGB has the same blend_type enum,
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
    """Mix node for vector channels (Normal) — Vector type, always MIX blend."""
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
                # _layer_contributes_to (checked above) already gates this:
                # AUTO requires use_emission, output_channel routing bypasses it.
                # Build Fac mask from procedural pattern, then use emission_color
                # as the glow color. The Fac controls WHERE it glows, not what color.
                fac_out = _build_proc_fac_node(
                    node_tree, layer, f"emis_{i}", x, y, uv_map
                )
                if fac_out is None:
                    continue

                # Unified smooth emission mask: Invert → Power → SmoothStep
                # No more binary LESS_THAN vs soft Power split — one continuous pipeline.
                threshold = getattr(layer, 'proc_emission_threshold', 0.0)
                falloff   = getattr(layer, 'proc_emission_falloff', 0.08)
                contrast  = getattr(layer, 'proc_contrast', 0.5)
                exponent  = 1.0 + contrast * 8.0   # range 1.0 → 9.0

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

                # ── Selective emission ───────────────────────────────────
                # An optional second mask gates WHERE the procedural
                # emission is allowed to light up — multiplied in here so
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


def _sync_material_alpha_method(material, alpha_connected, tlm):
    """Set the material's blend method based on the alpha pipeline state.

    Even when TLM correctly wires something to BSDF.Alpha, Eevee with
    mat.blend_method='OPAQUE' (the default for new materials) silently
    ignores the input and renders the surface opaque. This was the
    single biggest UX surprise for cutout / decal / foliage workflows —
    "I set output_channel to Alpha but nothing happens".

    Behaviour:
      - tlm.alpha_blend_method == 'AUTO' (default):
          * alpha_connected → HASHED (good general default — supports
            smooth alpha, anti-aliased edges, no manual sorting)
          * else → OPAQUE (no perf overhead when alpha isn't used)
      - any other value: forced regardless of connection state. Users
        who explicitly want BLEND for a glass material keep that even
        when no alpha layer is present.

    The Blender 4.2+ API replaced ``mat.blend_method`` with
    ``mat.surface_render_method`` (only DITHERED / BLENDED — CLIP and
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

    # Legacy attribute (Blender ≤ 4.1) — still respected on 4.2+ as a
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


# ── Selective emission selector ──────────────────────────────────────────────

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


def _defer_rebuild(material):
    """Schedule a rebuild via timer when we can't do it right now."""
    from .. import properties
    properties._pending_materials.add(material.name)
    if len(properties._pending_materials) == 1:
        import bpy
        bpy.app.timers.register(properties._do_deferred_rebuild, first_interval=0.05)


def rebuild_node_tree(material):
    # Cancel any pending deferred rebuild — this explicit call supersedes it.
    from .. import properties
    properties.cancel_pending_rebuild(material.name)

    # Reset deterministic counter so node names match between rebuilds
    global _node_counter
    _node_counter = 0

    tlm = material.tlm
    perf_started = _time.perf_counter() if performance_enabled(material) else None
    if getattr(tlm, "shader_editable", False):
        print(f"[TLM] Skipping rebuild for '{material.name}' — shader is in Editable mode")
        return
    _ensure_unique_layer_names(material)

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
        # GROUP layers are always root-level — reject any nested-group state
        # that leaked in (e.g. from a hand-edited preset) so they still
        # composite correctly instead of disappearing as a phantom child.
        if layer.group_name and layer.layer_type != "GROUP":
            group_children.setdefault(layer.group_name, []).append(layer)

    # Build root layer list — Groups are composited as a unit (not expanded flat)
    # This preserves group alpha for Clipping Mask support.
    # GROUP layers always appear at root regardless of any stray group_name.
    def _is_root(l):
        if not l.visible:
            return False
        if l.layer_type == "GROUP":
            return True
        return not l.group_name
    root_layers = [l for l in all_layers if _is_root(l)]

    # Solo override — show only the solo'd layer.
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
                    # Invalid / missing reference name — fall back to the
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
        # Nothing to build — clear TLM nodes (no layers visible) but don't
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
                pass  # can't remove either — will be cleaned on next rebuild
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
            print("[TLM] WARNING: No Principled BSDF found — skipping rebuild")
            _restore_custom_links(node_tree, _saved_custom_links)
            _record_rebuild_performance(material, perf_started, node_tree, expanded)
            return
        # Restore BSDF→Surface link if the previous rebuild's alpha wrap
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

        # ── Channel y positions — spaced 400px apart ──
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

        # ── Displacement ─────────────────────────────────────────────────
        # When `mat.tlm.use_displacement=True` AND at least one layer has
        # `use_displacement=True`, build a cumulative height stack and wire
        # it to Material Output.Displacement via ShaderNodeDisplacement.
        # Tear down first so toggle-off leaves no orphans.
        if mat_out is not None:
            stale_disp = [n for n in node_tree.nodes
                          if n.bl_idname == 'ShaderNodeDisplacement'
                          and n.name.startswith(TLM_PREFIX)]
            for n in stale_disp:
                node_tree.nodes.remove(n)
            disp_in = mat_out.inputs.get("Displacement")
            if disp_in is not None and disp_in.is_linked:
                for l in list(node_tree.links):
                    if l.to_node is mat_out and l.to_socket is disp_in:
                        node_tree.links.remove(l)
            if (getattr(tlm, 'use_displacement', False)
                    and disp_in is not None
                    and _channel_used(expanded, 'use_displacement')):
                height_out = _build_displacement_channel(
                    node_tree, expanded, uv_map, start_x, ch_y.get('bump', shader_y),
                    x_step,
                )
                if height_out is not None:
                    disp_node = node_tree.nodes.new('ShaderNodeDisplacement')
                    disp_node.name = f"{TLM_PREFIX}displacement_{_next_id()}"
                    disp_node.label = "Displacement (TLM)"
                    disp_node.location = (shader_x, shader_y - 620)
                    disp_node.inputs["Scale"].default_value = getattr(
                        tlm, 'displacement_strength', 0.1
                    )
                    disp_node.inputs["Midlevel"].default_value = getattr(
                        tlm, 'displacement_midlevel', 0.5
                    )
                    node_tree.links.new(height_out, disp_node.inputs["Height"])
                    node_tree.links.new(disp_node.outputs["Displacement"], disp_in)

        # ── Base Color — built from root_layers to preserve GROUP alpha for clipping mask ─
        bc_out, bc_alpha = _build_base_color(node_tree, root_layers, group_children, uv_map, start_x, ch_y['base_color'], x_step)
        if bc_out:
            bc_out = _route_through_user_slot(
                node_tree, material, 'base_color', bc_out,
                end_x, ch_y['base_color'],
            )
            # Anime/toon mode: route base_color → BSDF.Emission Color instead
            # of Base Color. This bypasses Cycles' diffuse cosine attenuation
            # so cel-shading NDOTL bands appear as flat saturated colours,
            # not multiplied by cos(NdotL). Set Base Color = BLACK so the
            # diffuse component contributes 0.
            if getattr(material.tlm, 'use_emission_output', False):
                _link_channel_to_bsdf(
                    node_tree, bc_out, bsdf, ["Emission Color", "Emission"],
                    "base_color", route_x, ch_y['base_color'],
                )
                # Force Base Color → BLACK and Emission Strength = 1
                bc_socket = bsdf.inputs.get("Base Color")
                if bc_socket is not None:
                    bc_socket.default_value = (0.0, 0.0, 0.0, 1.0)
                es_socket = bsdf.inputs.get("Emission Strength")
                if es_socket is not None and not es_socket.is_linked:
                    es_socket.default_value = 1.0
                # Force matte BSDF — emission overrides anyway, but matte
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

        # ── Roughness ─────────────────────────────────────────────────────────────
        # Same architecture as Base Color: a per-layer Mix chain ending in
        # the BSDF input. The Math ADD passthrough that used to sit here was
        # cosmetic (added 0.0 to the value) and confused the debug — the
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

        # ── Metallic ──────────────────────────────────────────────────────────────
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

        # ── Normal ────────────────────────────────────────────────────────────────
        normal_out = None
        if _channel_used(expanded, 'use_normal'):
            normal_out = _build_normal_channel(
                node_tree, expanded, uv_map, start_x, ch_y['normal'], x_step
            )

        # ── Emission ──────────────────────────────────────────────────────────────
        if _channel_used(expanded, 'use_emission'):
            # If anime/toon emission_output mode is on, the base_color stack
            # already drives BSDF.Emission Color. Building the emission
            # channel here would just produce orphan node groups (built but
            # never connected to anything because we skip the link). Skip
            # the whole emission build entirely in that case.
            if getattr(material.tlm, 'use_emission_output', False):
                pass  # emission channel build skipped — base_color drives Emission
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
                # emission_output mode — base_color stack drives Emission at
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

        # ── Transmission ─────────────────────────────────────────────────────────
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

        # ── Alpha ────────────────────────────────────────────────────────────────
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
                # Wrap through Mix Shader + Transparent BSDF — see
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

        # ── Bump ──────────────────────────────────────────────────────────────────
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

        # ── Connect final normal to BSDF ─────────────────────────────────────────
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
            # Schedule a deferred rebuild via timer — it will run in a safe context.
            print(f"[TLM] Restricted context detected, deferring rebuild")
            from .. import properties as _props
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
    'BASE_COLOR': 'base_color',
    'ROUGHNESS':  'roughness',
    'METALLIC':   'metallic',
    'ALPHA':      'alpha',
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
    """Make sure BSDF.BSDF → MaterialOutput.Surface is connected.

    Called after _clear_tlm_nodes removes the alpha-wrap (Mix Shader +
    Transparent BSDF). Without this, removing the wrap would leave
    Material Output's Surface input dangling → solid black render.
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
      Principled BSDF.Alpha — the surface stays opaque even when the
      alpha is meant to be 0 (user-reported: object placed behind the
      cube is not visible through alpha=0 regions in Cycles, while
      Eevee correctly shows the cutout). The portable, engine-agnostic
      pattern is to wrap the surface output:

          Principled BSDF ──â”
                            â”œ── Mix Shader (factor = alpha) ── Output
          Transparent BSDF ─â”˜

      Mix Shader factor convention: 0 → input 1, 1 → input 2. So we
      put Transparent in slot 1 and Principled in slot 2, giving
      alpha = 0 → invisible, alpha = 1 → opaque. Works identically in
      both Eevee and Cycles regardless of blend_method or
      surface_render_method.

    Also keeps the alpha → BSDF.Alpha connection (for Eevee bake paths
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

    # Connect alpha to BSDF.Alpha too — preserves the previous Eevee
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

        # Drop the existing BSDF → Surface link only after the replacement
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


# ── Flatten ───────────────────────────────────────────────────────────────────

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

    # Baking requires Cycles — switch temporarily if needed
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

    # Create temp Diffuse BSDF → Material Output
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


# ── Layer frame grouping (visual organization in shader editor) ──────────────

# Discreet tints — enough to distinguish at a glance, subdued enough not to
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
    """True if the role identifies a chain Mix node — these flow between layers
    at the far-right of the channel and would stretch the frame across the
    whole canvas. We leave them outside the frames.

    Covers:
      - `mix`, `mix_{channel}`, `mix_scalar`, `mix_vector` (from `_new_mix*`)
      - `opacity_target_{channel}` (overwrites from `_set_factor` / clipping)

    Preserves inside-frame nodes like `vdist_mix`, `marble_mult`, `mask_*`,
    `proc_*`, `normal_*`, `adj_*`, `fill_*`, `ref_*` — they are clustered near
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
    positions — one per channel band. A single frame wrapping all of them
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
    the same color & label — the user still sees "these belong to Layer X"
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


# ── Tag validation (debug mode) ───────────────────────────────────────────────

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



# ── Sub-modules ────────────────────────────────────────────────────────
# Import order matters here: each sub-module's late `from . import ...`
# resolves names defined ABOVE in __init__.py PLUS names brought in by
# earlier sub-module imports. So masks must come BEFORE hot_update
# (hot_update's handlers use _apply_mask from masks).

# Masks: smart generators, slot resolution, refinement, blur groups.
from .masks import *  # noqa: F401, F403

# Hot-update handlers + dispatch + sun-direction depsgraph hook.
from .hot_update import *  # noqa: F401, F403




def register():
    pass

def unregister():
    pass
