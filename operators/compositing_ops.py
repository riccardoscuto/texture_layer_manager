"""Compositing and flatten operators."""

import bpy
from bpy.types import Operator
from bpy.props import StringProperty
from ._common import _get_material, compositing


_EDITABLE_NODE_PROP = "tlm_editable_node"
_EDITABLE_FROM_PROP = "tlm_editable_from"


def _is_tlm_generated_node(node):
    return (
        node.name.startswith(compositing.TLM_PREFIX)
        or node.get("tlm_layer") is not None
        or node.get("tlm_role") is not None
        or node.get("tlm_frame_owner") is not None
    )


def _make_editable_node_name(name):
    if name.startswith(compositing.TLM_PREFIX):
        name = name[len(compositing.TLM_PREFIX):]
    name = name.replace("_", " ").strip()
    return name or "Editable Node"


def _detach_tlm_node(node):
    old_name = node.name
    for key in list(node.keys()):
        if key.startswith("tlm_"):
            try:
                del node[key]
            except Exception:
                pass
    node[_EDITABLE_NODE_PROP] = True
    node[_EDITABLE_FROM_PROP] = old_name
    node.name = _make_editable_node_name(old_name)
    if not getattr(node, "label", ""):
        node.label = node.name


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
        if mat.tlm.shader_editable:
            self.report(
                {'WARNING'},
                "Shader is editable. Use 'Return to TLM' before rebuilding.",
            )
            return {'CANCELLED'}
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


class TLM_OT_ConvertToEditableShader(Operator):
    """Convert the current TLM graph to a normal editable Blender shader."""
    bl_idname = "tlm.convert_to_editable_shader"
    bl_label = "Convert to Editable Shader"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        if tlm.shader_editable:
            self.report({'INFO'}, "Shader is already editable.")
            return {'FINISHED'}

        compositing.rebuild_node_tree(mat)
        node_tree = mat.node_tree
        if node_tree is None:
            self.report({'ERROR'}, "Material has no node tree.")
            return {'CANCELLED'}

        detached = 0
        for node in list(node_tree.nodes):
            if _is_tlm_generated_node(node):
                _detach_tlm_node(node)
                detached += 1

        tlm.auto_composite = False
        tlm.shader_editable = True
        node_tree.update_tag()
        mat.update_tag()

        self.report(
            {'INFO'},
            f"Converted {detached} TLM nodes to an editable Blender shader.",
        )
        return {'FINISHED'}


class TLM_OT_ReturnToManagedShader(Operator):
    """Discard converted editable TLM nodes and rebuild from the layer stack."""
    bl_idname = "tlm.return_to_managed_shader"
    bl_label = "Return to TLM"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        node_tree = mat.node_tree
        removed = 0
        if node_tree is not None:
            for node in list(node_tree.nodes):
                if node.get(_EDITABLE_NODE_PROP):
                    node_tree.nodes.remove(node)
                    removed += 1

        tlm.shader_editable = False
        tlm.auto_composite = True
        compositing.rebuild_node_tree(mat)

        self.report(
            {'INFO'},
            f"Returned to TLM-managed mode. Removed {removed} editable nodes.",
        )
        return {'FINISHED'}


classes = [
    TLM_OT_RebuildComposite,
    TLM_OT_FlattenLayers,
    TLM_OT_ConvertToEditableShader,
    TLM_OT_ReturnToManagedShader,
]
