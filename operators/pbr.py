"""PBR channel image operators."""

import bpy
from bpy.types import Operator
from ._common import _get_material, compositing


CHANNEL_INFO = {
    'roughness':    ('use_roughness',    'roughness_image_name',    'Roughness'),
    'metallic':     ('use_metallic',     'metallic_image_name',     'Metallic'),
    'normal':       ('use_normal',       'normal_image_name',       'Normal'),
    'transmission': ('use_transmission', 'transmission_image_name', 'Transmission'),
    'emission':     ('use_emission',     'emission_image_name',     'Emission'),
    'alpha':        ('use_alpha',        'alpha_image_name',        'Alpha'),
}


class TLM_OT_AddChannelImage(Operator):
    """Create a new image for a PBR channel on the active layer."""
    bl_idname = "tlm.add_channel_image"
    bl_label = "Add Channel Image"
    bl_options = {'REGISTER', 'UNDO'}

    channel: bpy.props.StringProperty(default='roughness')

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and mat.tlm.active_layer is not None

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        layer = tlm.active_layer
        if not layer:
            return {'CANCELLED'}

        info = CHANNEL_INFO.get(self.channel)
        if not info:
            return {'CANCELLED'}
        flag_attr, img_attr, label = info

        res = int(tlm.resolution)
        img_name = f"{layer.name}_{label}"

        defaults = {
            'roughness': [0.5, 0.5, 0.5, 1.0],
            'metallic':  [0.0, 0.0, 0.0, 1.0],
            'normal':    [0.5, 0.5, 1.0, 1.0],
            'emission':     [0.0, 0.0, 0.0, 1.0],
            'transmission': [0.0, 0.0, 0.0, 1.0],
            # Alpha defaults to white = fully visible. The user can paint
            # the cutout shape afterwards. Black would also be defensible
            # but "everything visible until I paint it away" matches the
            # mental model of every other PBR channel.
            'alpha':        [1.0, 1.0, 1.0, 1.0],
        }
        fill = defaults.get(self.channel, [0.5, 0.5, 0.5, 1.0])

        img = bpy.data.images.new(img_name, width=res, height=res, alpha=True)
        import numpy as np
        fill_px = np.array(fill * (res * res), dtype=np.float32)
        img.pixels.foreach_set(fill_px)
        img.pack()

        if self.channel in ('roughness', 'metallic', 'normal',
                            'transmission', 'alpha'):
            try:
                img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass

        setattr(layer, img_attr, img.name)
        setattr(layer, flag_attr, True)

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'NODE_EDITOR':
                        area.tag_redraw()

        self.report({'INFO'}, f"Added {label} channel to '{layer.name}'")
        return {'FINISHED'}


class TLM_OT_RemoveChannelImage(Operator):
    """Remove the image from a PBR channel, reverting to the fill value."""
    bl_idname = "tlm.remove_channel_image"
    bl_label = "Remove Channel"
    bl_options = {'REGISTER', 'UNDO'}

    channel: bpy.props.StringProperty(default='roughness')

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and mat.tlm.active_layer is not None

    def execute(self, context):
        mat = _get_material(context)
        layer = mat.tlm.active_layer
        info = CHANNEL_INFO.get(self.channel)
        if not info:
            return {'CANCELLED'}
        flag_attr, img_attr, label = info
        setattr(layer, flag_attr, False)
        if mat.tlm.auto_composite:
            compositing.rebuild_node_tree(mat)
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'NODE_EDITOR':
                        area.tag_redraw()
        self.report({'INFO'}, f"Disabled {label} channel on '{layer.name}'")
        return {'FINISHED'}


classes = [
    TLM_OT_AddChannelImage,
    TLM_OT_RemoveChannelImage,
]
