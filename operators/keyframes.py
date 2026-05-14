"""Keyframe operators."""

import bpy
from bpy.types import Operator
from bpy.props import EnumProperty
from ._common import _get_material, _can_edit_tlm_stack


class TLM_OT_KeyframeOpacity(Operator):
    """Insert or delete a keyframe on the active layer's opacity."""
    bl_idname = "tlm.keyframe_opacity"
    bl_label = "Keyframe Opacity"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(
        items=[
            ('INSERT', "Insert", "Insert keyframe at current frame"),
            ('DELETE', "Delete", "Delete keyframe at current frame"),
        ],
        default='INSERT',
    )

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return _can_edit_tlm_stack(context) and mat.tlm.active_layer is not None

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        idx = tlm.active_layer_index
        frame = context.scene.frame_current
        data_path = f'tlm.layers[{idx}].opacity'

        try:
            if self.action == 'INSERT':
                mat.keyframe_insert(data_path=data_path, frame=frame)
                self.report({'INFO'}, f"Keyframe inserted at frame {frame}")
            else:
                mat.keyframe_delete(data_path=data_path, frame=frame)
                self.report({'INFO'}, f"Keyframe deleted at frame {frame}")
        except Exception as e:
            self.report({'WARNING'}, f"Keyframe failed: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


classes = [
    TLM_OT_KeyframeOpacity,
]
