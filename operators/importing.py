"""Texture and PBR set import operators."""

import os
import bpy
from bpy.types import Operator
from ._common import _get_material, _ensure_nodes, compositing, previews
from .pbr import CHANNEL_INFO


class TLM_OT_ImportTextureAsLayer(Operator):
    """Import an image file and add it as a new layer (or assign to a channel)."""
    bl_idname = "tlm.import_texture_as_layer"
    bl_label = "Import Texture as Layer"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.tga;*.tiff;*.tif;*.exr;*.hdr;*.bmp",
        options={'HIDDEN'}
    )

    channel: bpy.props.EnumProperty(
        name="Assign to Channel",
        items=[
            ('base_color', "Base Color", "Assign as the main color layer"),
            ('roughness',  "Roughness",  "Assign as roughness channel"),
            ('metallic',   "Metallic",   "Assign as metallic channel"),
            ('normal',     "Normal Map", "Assign as normal map channel"),
            ('emission',      "Emission",     "Assign as emission channel"),
            ('transmission',  "Transmission", "Assign as transmission channel"),
        ],
        default='base_color',
    )

    add_to_active: bpy.props.BoolProperty(
        name="Assign to active layer channel",
        description="Assign to a channel of the active layer instead of creating a new layer",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        mat = _get_material(context)
        _ensure_nodes(mat)
        tlm = mat.tlm

        filepath = bpy.path.abspath(self.filepath)
        if not filepath:
            self.report({'ERROR'}, "No file selected")
            return {'CANCELLED'}

        # Load image
        try:
            img = bpy.data.images.load(filepath)
            img.pack()
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load image: {e}")
            return {'CANCELLED'}

        # Mark non-color for technical channels
        if self.channel in ('roughness', 'metallic', 'normal'):
            try:
                img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass

        if self.add_to_active and tlm.active_layer:
            # Assign to channel of active layer
            layer = tlm.active_layer
            if self.channel == 'base_color':
                layer.image_name = img.name
            else:
                info = CHANNEL_INFO.get(self.channel)
                if info:
                    flag_attr, img_attr, _ = info
                    setattr(layer, img_attr, img.name)
                    setattr(layer, flag_attr, True)
        else:
            # Create a new PAINT layer with this image as base color
            layer = tlm.layers.add()
            layer.layer_type = "PAINT"
            layer.name = img.name.rsplit('.', 1)[0]  # strip extension
            layer.opacity = 1.0
            layer.blend_mode = "MIX"
            layer.visible = True
            layer.group_name = ""

            if self.channel == 'base_color':
                layer.image_name = img.name
            else:
                info = CHANNEL_INFO.get(self.channel)
                if info:
                    flag_attr, img_attr, _ = info
                    setattr(layer, img_attr, img.name)
                    setattr(layer, flag_attr, True)

            # Append at end (base convention)
            new_idx = len(tlm.layers) - 1
            tlm.active_layer_index = new_idx

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Imported '{img.name}' as layer")
        return {'FINISHED'}


# ─── PBR Texture Set Import ──────────────────────────────────────────────────

# Keyword-to-channel mappings for auto-detection
PBR_KEYWORDS = {
    'base_color':   {'basecolor', 'base_color', 'albedo', 'diffuse', 'diff', 'color', 'col'},
    'roughness':    {'roughness', 'rough', 'gloss'},
    'metallic':     {'metallic', 'metal', 'metalness'},
    'normal':       {'normal', 'norm', 'nrm', 'normalgl', 'normal_gl', 'normaldx', 'normal_dx'},
    'emission':     {'emission', 'emissive', 'emit', 'glow'},
    'transmission': {'transmission', 'opacity', 'alpha', 'transparency'},
}

PBR_SKIP_KEYWORDS = {'ao', 'ambient_occlusion', 'ambientocclusion', 'occlusion',
                      'displacement', 'disp', 'height'}

PBR_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.exr', '.bmp', '.tga'}

PBR_NON_COLOR_CHANNELS = {'roughness', 'metallic', 'normal', 'transmission'}


def _detect_pbr_channel(filename):
    """Return (channel_id, is_skipped) from a texture filename."""
    import re
    name_lower = filename.lower()
    stem = os.path.splitext(name_lower)[0]
    ext = os.path.splitext(name_lower)[1]

    if ext not in PBR_IMAGE_EXTENSIONS:
        return (None, False)

    # Check skip keywords first (ao, displacement, height)
    for kw in PBR_SKIP_KEYWORDS:
        if kw in stem:
            return (kw, True)

    # Check each channel — base_color first (dict is insertion-ordered)
    for channel_id, keywords in PBR_KEYWORDS.items():
        for kw in keywords:
            if kw in stem:
                return (channel_id, False)

    return (None, False)


class TLM_OT_ImportPBRSet(Operator):
    """Import a folder of PBR textures and auto-assign channels."""
    bl_idname = "tlm.import_pbr_set"
    bl_label = "Import PBR Texture Set"
    bl_options = {'REGISTER', 'UNDO'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.tga;*.tiff;*.tif;*.exr;*.bmp",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        mat = _get_material(context)
        if not mat:
            return {'CANCELLED'}
        _ensure_nodes(mat)
        tlm = mat.tlm

        directory = bpy.path.abspath(self.directory)
        if not directory or not os.path.isdir(directory):
            self.report({'ERROR'}, "Invalid directory")
            return {'CANCELLED'}

        # ── Phase 1: Scan & detect ────────────────────────────────────────
        detected = {}   # channel_id -> filepath
        skipped = []    # (filename, reason)
        unknown = []    # unrecognized image files

        for fname in sorted(os.listdir(directory)):
            fpath = os.path.join(directory, fname)
            if not os.path.isfile(fpath):
                continue

            ext = os.path.splitext(fname)[1].lower()
            if ext not in PBR_IMAGE_EXTENSIONS:
                continue

            channel_id, is_skipped = _detect_pbr_channel(fname)

            if is_skipped:
                skipped.append((fname, channel_id))
            elif channel_id is not None:
                if channel_id not in detected:
                    detected[channel_id] = fpath
            else:
                unknown.append(fname)

        if not detected:
            self.report({'ERROR'}, "No PBR textures detected in folder")
            return {'CANCELLED'}

        # ── Phase 2: Report detection ─────────────────────────────────────
        for ch_id, fpath in detected.items():
            self.report({'INFO'}, f"  {ch_id}: {os.path.basename(fpath)}")
        for fname, reason in skipped:
            self.report({'INFO'}, f"  Skipped ({reason}): {fname}")
        for fname in unknown:
            self.report({'INFO'}, f"  Unrecognized: {fname}")

        # ── Phase 3: Layer name from folder ───────────────────────────────
        name = os.path.basename(os.path.normpath(directory)) or "PBR Import"

        # ── Phase 4: Load images ──────────────────────────────────────────
        loaded = {}  # channel_id -> bpy.data.images
        for ch_id, fpath in detected.items():
            try:
                img = bpy.data.images.load(fpath)
                img.pack()
                img.use_fake_user = True
                if ch_id in PBR_NON_COLOR_CHANNELS:
                    try:
                        img.colorspace_settings.name = "Non-Color"
                    except Exception:
                        pass
                loaded[ch_id] = img
            except Exception as e:
                self.report({'WARNING'}, f"Failed to load {os.path.basename(fpath)}: {e}")

        if not loaded:
            self.report({'ERROR'}, "Failed to load any images")
            return {'CANCELLED'}

        # Gloss map warning
        if 'roughness' in loaded:
            r_name = loaded['roughness'].name.lower()
            if 'gloss' in r_name:
                self.report({'WARNING'},
                    "Gloss map loaded as Roughness without inversion — "
                    "you may need to invert manually")

        # ── Phase 5: Create layer ─────────────────────────────────────────
        layer = tlm.layers.add()
        layer.layer_type = "PAINT"
        layer.name = name
        layer.opacity = 1.0
        layer.blend_mode = "MIX"
        layer.visible = True

        # Assign base color
        if 'base_color' in loaded:
            layer.image_name = loaded['base_color'].name
        else:
            self.report({'WARNING'},
                "No base color map found — assign one manually if needed")

        # Assign PBR channels
        for ch_id, img in loaded.items():
            if ch_id == 'base_color':
                continue
            info = CHANNEL_INFO.get(ch_id)
            if info:
                flag_attr, img_attr, _label = info
                setattr(layer, img_attr, img.name)
                setattr(layer, flag_attr, True)

        # Set active
        tlm.active_layer_index = len(tlm.layers) - 1

        if layer.image_name:
            previews.invalidate(layer.image_name)

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        ch_count = len(loaded)
        self.report({'INFO'},
            f"Imported PBR set '{name}' with {ch_count} channel(s)")
        return {'FINISHED'}


classes = [
    TLM_OT_ImportTextureAsLayer,
    TLM_OT_ImportPBRSet,
]
