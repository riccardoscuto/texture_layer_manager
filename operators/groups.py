"""Group management operators."""

import bpy
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty
from ._common import _get_material, _ensure_nodes, compositing


class TLM_OT_AddGroup(Operator):
    """Add a new empty group (folder) above the active layer."""
    bl_idname = "tlm.add_group"
    bl_label = "Add Group"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        mat = _get_material(context)
        _ensure_nodes(mat)
        tlm = mat.tlm

        layer = tlm.layers.add()
        layer.layer_type = "GROUP"
        layer.name = f"Group {len(tlm.layers)}"
        layer.visible = True
        layer.collapsed = False
        layer.group_name = ""

        new_idx = len(tlm.layers) - 1
        target = tlm.active_layer_index if len(tlm.layers) > 1 else 0
        while new_idx > target:
            tlm.layers.move(new_idx, new_idx - 1)
            new_idx -= 1
        tlm.active_layer_index = new_idx

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Added group '{layer.name}'")
        return {'FINISHED'}


class TLM_OT_MoveToGroup(Operator):
    """Move the active layer into a group."""
    bl_idname = "tlm.move_to_group"
    bl_label = "Move to Group"
    bl_options = {'REGISTER', 'UNDO'}

    group_name: StringProperty(name="Group Name", default="")

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        if not mat:
            return False
        active = mat.tlm.active_layer
        return active is not None and active.layer_type != "GROUP"

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        active = tlm.active_layer
        if active is None:
            return {'CANCELLED'}

        target = next((l for l in tlm.layers if l.name == self.group_name and l.layer_type == "GROUP"), None)
        if target is None:
            self.report({'WARNING'}, f"Group '{self.group_name}' not found")
            return {'CANCELLED'}

        active.group_name = self.group_name

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Moved '{active.name}' into '{self.group_name}'")
        return {'FINISHED'}


class TLM_OT_RemoveFromGroup(Operator):
    """Remove the active layer from its group (move to root)."""
    bl_idname = "tlm.remove_from_group"
    bl_label = "Remove from Group"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        if not mat:
            return False
        active = mat.tlm.active_layer
        return active is not None and active.group_name != ""

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        active = tlm.active_layer
        if active is None:
            return {'CANCELLED'}

        old_group = active.group_name
        active.group_name = ""

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Removed '{active.name}' from group '{old_group}'")
        return {'FINISHED'}


class TLM_OT_ToggleGroupCollapse(Operator):
    """Expand or collapse a group folder."""
    bl_idname = "tlm.toggle_group_collapse"
    bl_label = "Toggle Group"

    layer_index: IntProperty(default=0)

    def execute(self, context):
        mat = _get_material(context)
        if not mat:
            return {'CANCELLED'}
        tlm = mat.tlm
        if 0 <= self.layer_index < len(tlm.layers):
            layer = tlm.layers[self.layer_index]
            if layer.layer_type == "GROUP":
                layer.collapsed = not layer.collapsed
        return {'FINISHED'}


classes = [
    TLM_OT_AddGroup,
    TLM_OT_MoveToGroup,
    TLM_OT_RemoveFromGroup,
    TLM_OT_ToggleGroupCollapse,
]
