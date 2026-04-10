"""
Shared helpers used across all operator submodules.
"""

import bpy
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty, EnumProperty, BoolProperty, FloatProperty, FloatVectorProperty
from .. import compositing, previews


def _get_material(context):
    """Return the active material, or None."""
    obj = context.active_object
    if obj and obj.active_material:
        return obj.active_material
    return None


def _ensure_nodes(mat):
    """Make sure the material uses nodes."""
    if not mat.use_nodes:
        mat.use_nodes = True


def _add_layer_common(context, layer_type):
    """Shared logic for all add-layer operators."""
    mat = _get_material(context)
    if not mat:
        return None
    _ensure_nodes(mat)
    tlm = mat.tlm

    # Determine parent group from the currently active layer:
    # - if active is an EMPTY GROUP -> new layer goes INSIDE it (first child)
    # - if active is a GROUP with children -> new layer goes ABOVE the group (root level)
    # - if active is already inside a group -> new layer goes in the same group
    # - otherwise -> root level
    active = tlm.active_layer
    parent_group = ""
    if active:
        if active.layer_type == "GROUP":
            has_children = any(l.group_name == active.name for l in tlm.layers)
            if has_children:
                parent_group = ""  # above the group, at root level
            else:
                parent_group = active.name  # inside the empty group
        elif active.group_name:
            parent_group = active.group_name

    layer = tlm.layers.add()
    layer.layer_type = layer_type
    layer.opacity = 1.0
    layer.blend_mode = "MIX"
    layer.visible = True
    layer.group_name = parent_group  # assign to group if applicable

    if layer_type == "PAINT":
        layer.name = f"Layer {len(tlm.layers)}"
        res = int(tlm.resolution)
        img = bpy.data.images.new(layer.name, width=res, height=res, alpha=True, float_buffer=False)
        import numpy as np
        px = np.zeros(res * res * 4, dtype=np.float32)
        img.pixels.foreach_set(px)
        img.use_fake_user = True  # prevent GC when layer is hidden
        layer.image_name = img.name
        previews.invalidate(img.name)
    elif layer_type == "FILL":
        layer.name = f"Fill {len(tlm.layers)}"
    elif layer_type == "ADJUSTMENT":
        layer.name = "Hue/Sat"

    # Move new layer to the correct position (Photoshop convention).
    # layers.add() appends at end; move it to the right spot.
    new_idx = len(tlm.layers) - 1
    if len(tlm.layers) > 1:
        if parent_group and active and active.layer_type == "GROUP":
            # Adding inside a group: place BELOW the group header (index + 1)
            target = tlm.active_layer_index + 1
        else:
            # Adding above the active layer
            target = tlm.active_layer_index
    else:
        target = 0
    while new_idx > target:
        tlm.layers.move(new_idx, new_idx - 1)
        new_idx -= 1
    tlm.active_layer_index = new_idx

    if tlm.auto_composite:
        compositing.rebuild_node_tree(mat)

    return layer.name
