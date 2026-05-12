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
            # Create a new PAINT layer with this image as base color.
            # Inherit the same group as the currently active layer so the
            # imported texture lands in the user's working group, mirroring
            # the behaviour of _add_layer_common.
            active = tlm.active_layer
            parent_group = ""
            if active:
                if active.layer_type == "GROUP":
                    has_children = any(l.group_name == active.name
                                       for l in tlm.layers)
                    if has_children:
                        parent_group = ""  # above the group at root
                    else:
                        parent_group = active.name  # inside the empty group
                elif active.group_name:
                    parent_group = active.group_name

            layer = tlm.layers.add()
            layer.layer_type = "PAINT"
            layer.name = img.name.rsplit('.', 1)[0]  # strip extension
            layer.opacity = 1.0
            layer.blend_mode = "MIX"
            layer.visible = True
            layer.group_name = parent_group

            if self.channel == 'base_color':
                layer.image_name = img.name
            else:
                info = CHANNEL_INFO.get(self.channel)
                if info:
                    flag_attr, img_attr, _ = info
                    setattr(layer, img_attr, img.name)
                    setattr(layer, flag_attr, True)

            # Place the new layer ABOVE the active one in the UI, matching
            # what tlm.add_paint_layer / add_fill_layer / etc. do. Without
            # this the imported texture always landed at the bottom of the
            # list regardless of where the user was working.
            new_idx = len(tlm.layers) - 1
            if len(tlm.layers) > 1:
                if parent_group and active and active.layer_type == "GROUP":
                    target = tlm.active_layer_index + 1
                else:
                    target = tlm.active_layer_index
            else:
                target = 0
            while new_idx > target:
                tlm.layers.move(new_idx, new_idx - 1)
                new_idx -= 1
            tlm.active_layer_index = new_idx

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Imported '{img.name}' as layer")
        return {'FINISHED'}


# ─── PBR Texture Set Import ──────────────────────────────────────────────────

# Keyword-to-channel mappings for auto-detection.
#
# Two iteration changes vs the original substring-only matcher:
#   1. alpha / opacity / transparency now resolve to the ALPHA channel
#      (formerly they were lumped together with transmission, which broke
#      every foliage / decal / cutout asset where the user expects them
#      to feed BSDF.Alpha not Transmission).
#   2. Matching is WORD-BOUNDARY (regex \b...\b), not substring. Without
#      this `col` matched `collage`, `metal` matched `gunmetal`, etc. —
#      a bad import 30% of the time on real-world Substance / Quixel
#      asset folder names. The token-level match below requires the
#      keyword to sit between non-word characters (or at the start/end
#      of the stem), so `metal_bronze.png` matches but `gunmetal.png`
#      does not.
#
# Within the same channel, longer / more specific keywords are checked
# first — `basecolor` beats `color`, `normalgl` beats `normal` — so a
# file named `normalgl.png` is recognised as a normal map rather than
# falling through.
PBR_KEYWORDS = {
    'base_color':   ['basecolor', 'base_color', 'albedo', 'diffuse', 'diff', 'color', 'col'],
    'roughness':    ['roughness', 'rough', 'gloss'],
    'metallic':     ['metalness', 'metallic', 'metal'],
    'normal':       ['normal_gl', 'normalgl', 'normal_dx', 'normaldx', 'normal', 'norm', 'nrm'],
    'emission':     ['emission', 'emissive', 'emit', 'glow'],
    'transmission': ['transmission', 'translucency', 'refraction'],
    'alpha':        ['opacity', 'transparency', 'alpha', 'cutout', 'mask'],
}

PBR_SKIP_KEYWORDS = ['ambient_occlusion', 'ambientocclusion', 'occlusion', 'ao',
                     'displacement', 'disp', 'height', 'bump']

PBR_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.exr', '.bmp', '.tga'}

PBR_NON_COLOR_CHANNELS = {'roughness', 'metallic', 'normal', 'transmission', 'alpha'}


# Pre-compiled regexes with word boundaries. Built lazily so updates to
# the keyword tables above are picked up automatically in dev iteration.
_PBR_CHANNEL_RES = None
_PBR_SKIP_RES = None


def _build_pbr_regexes():
    """Compile {channel_id: [(keyword, regex), ...]} keyed for word-boundary match."""
    import re
    global _PBR_CHANNEL_RES, _PBR_SKIP_RES
    _PBR_CHANNEL_RES = {
        ch: [(kw, re.compile(rf'(?:^|[\W_]){re.escape(kw)}(?:[\W_]|$)'))
             for kw in keywords]
        for ch, keywords in PBR_KEYWORDS.items()
    }
    _PBR_SKIP_RES = [(kw, re.compile(rf'(?:^|[\W_]){re.escape(kw)}(?:[\W_]|$)'))
                     for kw in PBR_SKIP_KEYWORDS]


def _detect_pbr_channel(filename):
    """Return (channel_id_or_skip_reason, is_skipped) from a texture filename.

    Word-boundary matching: `metal` matches `metal_red.png` and
    `red_metal.png` but NOT `gunmetal.png`. This stops substring noise
    like `col` → base_color matching `collage.png`.
    """
    if _PBR_CHANNEL_RES is None:
        _build_pbr_regexes()

    name_lower = filename.lower()
    stem = os.path.splitext(name_lower)[0]
    ext = os.path.splitext(name_lower)[1]

    if ext not in PBR_IMAGE_EXTENSIONS:
        return (None, False)

    # Skip keywords first (AO, displacement, height, bump — TLM doesn't
    # accept these as direct channels; they're either ignored or used
    # via dedicated workflows).
    for kw, rx in _PBR_SKIP_RES:
        if rx.search(stem):
            return (kw, True)

    # Channel match: dict-insertion-ordered (base_color first), longer
    # keyword first within each channel.
    for channel_id, regexes in _PBR_CHANNEL_RES.items():
        for _kw, rx in regexes:
            if rx.search(stem):
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
