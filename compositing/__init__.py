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

# Procedurals: per-proc-type shader builders + mapping/coord helpers.
# Must come BEFORE hot_update so hot handlers can resolve names like
# _set_input_default / _apply_mapping_settings via late imports.
from .procedurals import *  # noqa: F401, F403

# Channels: per-output-channel builders + factor wiring + linking.
from .channels import *  # noqa: F401, F403


# Hot-update handlers + dispatch + sun-direction depsgraph hook.
from .hot_update import *  # noqa: F401, F403





def register():
    pass

def unregister():
    pass
