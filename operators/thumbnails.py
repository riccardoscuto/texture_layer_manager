"""Thumbnail refresh operator."""

import bpy
from bpy.types import Operator
from ._common import _get_material, previews


class TLM_OT_RefreshThumbnails(Operator):
    """Force regeneration of all layer thumbnails."""
    bl_idname = "tlm.refresh_thumbnails"
    bl_label = "Refresh Thumbnails"

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        previews.invalidate_all()
        for area in context.screen.areas:
            if area.type == 'PROPERTIES':
                area.tag_redraw()
        self.report({'INFO'}, "Thumbnails refreshed.")
        return {'FINISHED'}


classes = [
    TLM_OT_RefreshThumbnails,
]
