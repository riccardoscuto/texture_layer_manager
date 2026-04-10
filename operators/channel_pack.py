"""Channel packing operator."""

import bpy
import numpy as np
from bpy.types import Operator
from bpy.props import StringProperty, EnumProperty, BoolProperty
from ._common import _get_material


# ─── Channel Packing ─────────────────────────────────────────────────────────

_CH_SOURCE = [
    ('R', "Red", "Red channel", 0),
    ('G', "Green", "Green channel", 1),
    ('B', "Blue", "Blue channel", 2),
    ('A', "Alpha", "Alpha channel", 3),
    ('LUM', "Luminance", "Greyscale luminance", 4),
]


class TLM_OT_ChannelPack(Operator):
    """Pack separate images into RGBA channels of a single output texture."""
    bl_idname = "tlm.channel_pack"
    bl_label = "Channel Pack"
    bl_options = {'REGISTER', 'UNDO'}

    output_name: StringProperty(name="Output Name", default="Packed")

    r_image: StringProperty(name="R Image", default="")
    g_image: StringProperty(name="G Image", default="")
    b_image: StringProperty(name="B Image", default="")
    a_image: StringProperty(name="A Image", default="")

    r_source: EnumProperty(name="R From", items=_CH_SOURCE, default='R')
    g_source: EnumProperty(name="G From", items=_CH_SOURCE, default='G')
    b_source: EnumProperty(name="B From", items=_CH_SOURCE, default='B')
    a_source: EnumProperty(name="A From", items=_CH_SOURCE, default='A')

    invert_r: BoolProperty(name="Invert", default=False)
    invert_g: BoolProperty(name="Invert", default=False)
    invert_b: BoolProperty(name="Invert", default=False)
    invert_a: BoolProperty(name="Invert", default=False)

    resolution: EnumProperty(
        name="Resolution",
        items=[
            ("512", "512", "", 0), ("1024", "1024", "", 1),
            ("2048", "2048", "", 2), ("4096", "4096", "", 3),
        ],
        default="1024",
    )

    @classmethod
    def poll(cls, context):
        return len(bpy.data.images) > 0

    def invoke(self, context, event):
        mat = _get_material(context)
        if mat:
            self.resolution = mat.tlm.resolution
        return context.window_manager.invoke_props_dialog(self, width=400)

    def _read_channel(self, img_name, source_ch, res):
        """Read a single channel from an image, resampled to output res."""
        img = bpy.data.images.get(img_name)
        if not img:
            return None
        w, h = img.size
        if w == 0 or h == 0:
            return None
        px = np.zeros(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape((h, w, 4))

        ch_map = {'R': 0, 'G': 1, 'B': 2, 'A': 3}
        if source_ch == 'LUM':
            ch_data = (px[:, :, 0] * 0.2126
                       + px[:, :, 1] * 0.7152
                       + px[:, :, 2] * 0.0722)
        else:
            ch_data = px[:, :, ch_map[source_ch]]

        # Resize if dimensions differ (nearest-neighbor)
        if ch_data.shape[0] != res or ch_data.shape[1] != res:
            from_h, from_w = ch_data.shape
            y_idx = (np.arange(res) * from_h / res).astype(int)
            x_idx = (np.arange(res) * from_w / res).astype(int)
            ch_data = ch_data[np.ix_(y_idx, x_idx)]

        return ch_data

    def execute(self, context):
        res = int(self.resolution)
        out_px = np.zeros((res, res, 4), dtype=np.float32)
        out_px[:, :, 3] = 1.0  # default alpha

        channels = [
            (self.r_image, self.r_source, self.invert_r, 0),
            (self.g_image, self.g_source, self.invert_g, 1),
            (self.b_image, self.b_source, self.invert_b, 2),
            (self.a_image, self.a_source, self.invert_a, 3),
        ]

        filled = 0
        for img_name, source_ch, invert, out_ch in channels:
            if not img_name:
                continue
            ch_data = self._read_channel(img_name, source_ch, res)
            if ch_data is None:
                self.report({'WARNING'},
                            f"Image '{img_name}' not found, skipping")
                continue
            if invert:
                ch_data = 1.0 - ch_data
            out_px[:, :, out_ch] = ch_data
            filled += 1

        if filled == 0:
            self.report({'ERROR'}, "No valid source images")
            return {'CANCELLED'}

        # Create output image
        name = self.output_name
        if name in bpy.data.images:
            bpy.data.images.remove(bpy.data.images[name])
        out_img = bpy.data.images.new(name, width=res, height=res, alpha=True)
        try:
            out_img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass

        out_img.pixels.foreach_set(out_px.ravel())
        out_img.update()
        out_img.pack()

        self.report({'INFO'},
                    f"Packed {filled} channels into '{name}' ({res}x{res})")
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "output_name")
        layout.prop(self, "resolution")
        layout.separator()

        for label, img_attr, src_attr, inv_attr in [
            ("Red",   "r_image", "r_source", "invert_r"),
            ("Green", "g_image", "g_source", "invert_g"),
            ("Blue",  "b_image", "b_source", "invert_b"),
            ("Alpha", "a_image", "a_source", "invert_a"),
        ]:
            box = layout.box()
            row = box.row(align=True)
            row.label(text=f"{label}:")
            row.prop_search(self, img_attr, bpy.data, "images", text="")
            row2 = box.row(align=True)
            row2.prop(self, src_attr, text="Channel")
            row2.prop(self, inv_attr, toggle=True)


classes = [
    TLM_OT_ChannelPack,
]
