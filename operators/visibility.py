"""Layer visibility and activation operators."""

import bpy
from bpy.types import Operator
from bpy.props import IntProperty
from ._common import _get_material, compositing


class TLM_OT_SetActivePaintLayer(Operator):
    """Set this layer as the active image for Texture Paint mode."""
    bl_idname = "tlm.set_active_paint_layer"
    bl_label = "Paint on this Layer"
    bl_options = {'REGISTER', 'UNDO'}

    layer_index: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm

        if self.layer_index >= len(tlm.layers):
            return {'CANCELLED'}

        layer = tlm.layers[self.layer_index]
        if layer.locked:
            self.report({'WARNING'}, f"Layer '{layer.name}' is locked.")
            return {'CANCELLED'}

        if not layer.image:
            self.report({'WARNING'}, "This layer has no image yet.")
            return {'CANCELLED'}

        node_tree = mat.node_tree
        if node_tree:
            for node in node_tree.nodes:
                node.select = False
                if (node.type == 'TEX_IMAGE'
                        and node.image
                        and node.image.name == layer.image_name):
                    node.select = True
                    node_tree.nodes.active = node

        tlm.active_layer_index = self.layer_index
        self.report({'INFO'}, f"Painting on: {layer.name}")
        return {'FINISHED'}


class TLM_OT_ToggleLayerVisibility(Operator):
    """Show or hide this layer in the composite."""
    bl_idname = "tlm.toggle_layer_visibility"
    bl_label = "Toggle Visibility"
    bl_options = {'REGISTER', 'UNDO'}

    layer_index: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm

        if self.layer_index >= len(tlm.layers):
            return {'CANCELLED'}

        layer = tlm.layers[self.layer_index]
        layer.visible = not layer.visible

        for l in tlm.layers:
            if l.layer_type == "PAINT" and l.image:
                l.image.use_fake_user = True

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        return {'FINISHED'}


class TLM_OT_SoloLayer(Operator):
    """Isolate this layer — hide all others without changing their visibility state."""
    bl_idname = "tlm.solo_layer"
    bl_label = "Solo Layer"
    bl_options = {'REGISTER', 'UNDO'}

    layer_index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm

        if self.layer_index < 0 or self.layer_index >= len(tlm.layers):
            return {'CANCELLED'}

        if tlm.solo_layer_index == self.layer_index:
            tlm.solo_layer_index = -1
            self.report({'INFO'}, "Solo off")
        else:
            tlm.solo_layer_index = self.layer_index
            layer_name = tlm.layers[self.layer_index].name
            self.report({'INFO'}, f"Solo: '{layer_name}'")

        # Also make this the active layer so the property panel below the
        # layer list reflects the layer being soloed. Without this, soloing
        # a layer while a different one is selected leaves the property
        # editor showing stale data — confusing because the user looks at
        # the panel expecting it to describe the soloed layer.
        tlm.active_layer_index = self.layer_index

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        return {'FINISHED'}


classes = [
    TLM_OT_SetActivePaintLayer,
    TLM_OT_ToggleLayerVisibility,
    TLM_OT_SoloLayer,
]
