"""Compositing and flatten operators."""

import bpy
from bpy.types import Operator
from bpy.props import StringProperty
from ._common import _get_material, compositing


class TLM_OT_RebuildComposite(Operator):
    """Manually rebuild the shader node tree from the current layer stack."""
    bl_idname = "tlm.rebuild_composite"
    bl_label = "Rebuild Composite"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        mat = _get_material(context)
        compositing.rebuild_node_tree(mat)
        self.report({'INFO'}, "Composite rebuilt.")
        return {'FINISHED'}


class TLM_OT_FlattenLayers(Operator):
    """Bake all visible layers into a single texture (non-destructive: original layers kept)."""
    bl_idname = "tlm.flatten_layers"
    bl_label = "Flatten to Texture"
    bl_options = {'REGISTER', 'UNDO'}

    output_name: StringProperty(
        name="Output Image Name",
        default="TLM_Flattened",
    )

    @classmethod
    def poll(cls, context):
        return (_get_material(context) is not None
                and context.active_object is not None)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        res = int(tlm.resolution)

        try:
            img = compositing.flatten_to_single_image(mat, self.output_name, (res, res))
            self.report({'INFO'}, f"Flattened to '{img.name}' ({res}\u00d7{res})")
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        return {'FINISHED'}

    def draw(self, context):
        self.layout.prop(self, "output_name")


classes = [
    TLM_OT_RebuildComposite,
    TLM_OT_FlattenLayers,
]
