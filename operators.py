"""
operators.py
All Blender Operators for Texture Layer Manager.
These are the actions triggered by buttons in the UI.
"""

import bpy
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty, EnumProperty, BoolProperty
from . import compositing, previews


def _get_material(context):
    """Return the active material, or None."""
    obj = context.active_object
    if obj and obj.active_material:
        return obj.active_material
    return None


def _ensure_nodes(mat):
    """Make sure the material uses nodes."""
    if not mat.use_nodes:
        mat.use_nodes = True


# ─── Add Layer (3 separate operators, no enum = no Blender 5.0 validation issues)

def _add_layer_common(context, layer_type):
    """Shared logic for all add-layer operators."""
    mat = _get_material(context)
    if not mat:
        return None
    _ensure_nodes(mat)
    tlm = mat.tlm

    # Determine parent group from the currently active layer:
    # - if active is an EMPTY GROUP → new layer goes INSIDE it (first child)
    # - if active is a GROUP with children → new layer goes ABOVE the group (root level)
    # - if active is already inside a group → new layer goes in the same group
    # - otherwise → root level
    active = tlm.active_layer
    parent_group = ""
    if active:
        if active.layer_type == "GROUP":
            has_children = any(l.group_name == active.name for l in tlm.layers)
            if has_children:
                parent_group = ""  # above the group, at root level
            else:
                parent_group = active.name  # inside the empty group
        elif active.group_name:
            parent_group = active.group_name

    layer = tlm.layers.add()
    layer.layer_type = layer_type
    layer.opacity = 1.0
    layer.blend_mode = "MIX"
    layer.visible = True
    layer.group_name = parent_group  # assign to group if applicable

    if layer_type == "PAINT":
        layer.name = f"Layer {len(tlm.layers)}"
        res = int(tlm.resolution)
        img = bpy.data.images.new(layer.name, width=res, height=res, alpha=True, float_buffer=False)
        import numpy as np
        px = np.zeros(res * res * 4, dtype=np.float32)
        img.pixels.foreach_set(px)
        img.use_fake_user = True  # prevent GC when layer is hidden
        layer.image_name = img.name
        previews.invalidate(img.name)
    elif layer_type == "FILL":
        layer.name = f"Fill {len(tlm.layers)}"
    elif layer_type == "ADJUSTMENT":
        layer.name = "Hue/Sat"

    # Move new layer to the correct position (Photoshop convention).
    # layers.add() appends at end; move it to the right spot.
    new_idx = len(tlm.layers) - 1
    if len(tlm.layers) > 1:
        if parent_group and active and active.layer_type == "GROUP":
            # Adding inside a group: place BELOW the group header (index + 1)
            target = tlm.active_layer_index + 1
        else:
            # Adding above the active layer
            target = tlm.active_layer_index
    else:
        target = 0
    while new_idx > target:
        tlm.layers.move(new_idx, new_idx - 1)
        new_idx -= 1
    tlm.active_layer_index = new_idx

    if tlm.auto_composite:
        compositing.rebuild_node_tree(mat)

    return layer.name


class TLM_OT_AddPaintLayer(Operator):
    """Add a new paint layer with a blank transparent image above the active layer."""
    bl_idname = "tlm.add_paint_layer"
    bl_label = "Add Paint Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        name = _add_layer_common(context, "PAINT")
        if name is None:
            self.report({'ERROR'}, "No active material")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Added paint layer '{name}'")
        return {'FINISHED'}


class TLM_OT_AddFillLayer(Operator):
    """Add a solid color fill layer above the active layer."""
    bl_idname = "tlm.add_fill_layer"
    bl_label = "Add Fill Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        name = _add_layer_common(context, "FILL")
        if name is None:
            self.report({'ERROR'}, "No active material")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Added fill layer '{name}'")
        return {'FINISHED'}


class TLM_OT_AddAdjustmentLayer(Operator):
    """Add a new adjustment layer (Hue/Sat, Levels, Brightness/Contrast)."""
    bl_idname = "tlm.add_adjustment_layer"
    bl_label = "Add Adjustment Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        name = _add_layer_common(context, "ADJUSTMENT")
        if name is None:
            self.report({'ERROR'}, "No active material")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Added adjustment layer '{name}'")
        return {'FINISHED'}


class TLM_OT_AddProceduralLayer(Operator):
    """Add a new procedural texture layer (Noise, Voronoi, Wave, Gradient, etc.)."""
    bl_idname = "tlm.add_procedural_layer"
    bl_label = "Add Procedural Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        mat = _get_material(context)
        _ensure_nodes(mat)
        tlm = mat.tlm

        active = tlm.active_layer
        parent_group = ""
        if active:
            if active.layer_type == "GROUP":
                has_children = any(l.group_name == active.name for l in tlm.layers)
                if has_children:
                    parent_group = ""  # above the group, at root level
                else:
                    parent_group = active.name  # inside the empty group
            elif active.group_name:
                parent_group = active.group_name

        layer = tlm.layers.add()
        layer.layer_type  = "PROCEDURAL"
        layer.name        = "Noise"
        layer.opacity     = 1.0
        layer.blend_mode  = "MIX"
        layer.visible     = True
        layer.group_name  = parent_group
        layer.proc_type   = 'NOISE'

        # Move new layer to the correct position (Photoshop convention)
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

        self.report({'INFO'}, f"Added procedural layer '{layer.name}'")
        return {'FINISHED'}


# ─── Group Operators ──────────────────────────────────────────────────────────

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
        layer.group_name = ""  # groups are always root-level

        # Move new layer above the active layer (Photoshop convention)
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
        # Can't move a GROUP into a group (no nesting)
        return active is not None and active.layer_type != "GROUP"

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        active = tlm.active_layer
        if active is None:
            return {'CANCELLED'}

        # Verify target group exists
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


# ─── Remove Layer ─────────────────────────────────────────────────────────────

class TLM_OT_RemoveLayer(Operator):
    """Remove the active layer (image datablock is kept in bpy.data)."""
    bl_idname = "tlm.remove_layer"
    bl_label = "Remove Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and len(mat.tlm.layers) > 0

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        idx = tlm.active_layer_index

        if idx < 0 or idx >= len(tlm.layers):
            self.report({'WARNING'}, "No layer selected")
            return {'CANCELLED'}

        layer = tlm.layers[idx]
        layer_name = layer.name

        # If removing a GROUP, clear group_name on orphaned children
        if layer.layer_type == "GROUP":
            for other in tlm.layers:
                if other.group_name == layer_name:
                    other.group_name = ""

        tlm.layers.remove(idx)

        # Clamp index
        tlm.active_layer_index = max(0, min(idx, len(tlm.layers) - 1))

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Removed layer '{layer_name}' (image kept in bpy.data)")
        return {'FINISHED'}


# ─── Move Layer ───────────────────────────────────────────────────────────────

class TLM_OT_MoveLayer(Operator):
    """Move the active layer up or down in the stack."""
    bl_idname = "tlm.move_layer"
    bl_label = "Move Layer"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        items=[("UP", "Up", ""), ("DOWN", "Down", "")],
        default="UP",
    )

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and len(mat.tlm.layers) > 1

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        idx = tlm.active_layer_index

        if self.direction == "UP" and idx > 0:
            tlm.layers.move(idx, idx - 1)
            tlm.active_layer_index = idx - 1
        elif self.direction == "DOWN" and idx < len(tlm.layers) - 1:
            tlm.layers.move(idx, idx + 1)
            tlm.active_layer_index = idx + 1
        else:
            return {'CANCELLED'}

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        return {'FINISHED'}


# ─── Duplicate Layer ──────────────────────────────────────────────────────────

class TLM_OT_DuplicateLayer(Operator):
    """Duplicate the active layer, including its image data."""
    bl_idname = "tlm.duplicate_layer"
    bl_label = "Duplicate Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and mat.tlm.active_layer is not None

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        src = tlm.active_layer
        if src is None:
            return {'CANCELLED'}

        # Add a new layer
        new_layer = tlm.layers.add()
        new_layer.name = src.name + " Copy"
        new_layer.layer_type = src.layer_type
        new_layer.opacity = src.opacity
        new_layer.blend_mode = src.blend_mode
        new_layer.visible = src.visible
        new_layer.fill_color = src.fill_color[:]
        new_layer.use_mask = src.use_mask
        new_layer.use_clipping_mask = src.use_clipping_mask
        new_layer.group_name = src.group_name
        new_layer.use_triplanar = src.use_triplanar
        new_layer.triplanar_scale = src.triplanar_scale
        new_layer.triplanar_sharpness = src.triplanar_sharpness

        # FIX: copy PBR channel settings that were missing in the original
        new_layer.use_roughness = src.use_roughness
        new_layer.roughness_fill = src.roughness_fill
        new_layer.roughness_image_name = src.roughness_image_name
        new_layer.use_metallic = src.use_metallic
        new_layer.metallic_fill = src.metallic_fill
        new_layer.metallic_image_name = src.metallic_image_name
        new_layer.use_normal = src.use_normal
        new_layer.normal_image_name = src.normal_image_name
        new_layer.normal_strength = src.normal_strength
        new_layer.use_emission = src.use_emission
        new_layer.emission_image_name = src.emission_image_name
        new_layer.emission_color = src.emission_color[:]
        new_layer.emission_strength = src.emission_strength
        new_layer.use_bump = src.use_bump
        new_layer.bump_strength = src.bump_strength
        new_layer.bump_distance = src.bump_distance

        # Deep-copy image pixels
        if src.image:
            orig = src.image
            dup = orig.copy()
            dup.name = new_layer.name
            dup.use_fake_user = True
            new_layer.image_name = dup.name
            previews.invalidate(dup.name)

        # Place duplicate just below source (higher index = below in UIList)
        new_idx = len(tlm.layers) - 1
        target = tlm.active_layer_index + 1
        while new_idx > target:
            tlm.layers.move(new_idx, new_idx - 1)
            new_idx -= 1
        tlm.active_layer_index = new_idx

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        return {'FINISHED'}


# ─── Set Active for Painting ──────────────────────────────────────────────────

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

        # Set as the active image in the node tree so Texture Paint uses it
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


# ─── Toggle Visibility ────────────────────────────────────────────────────────

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

        # Protect paint images from GC when hidden — set fake_user on all images
        for l in tlm.layers:
            if l.layer_type == "PAINT" and l.image:
                l.image.use_fake_user = True

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        return {'FINISHED'}


# ─── Rebuild Composite ────────────────────────────────────────────────────────

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


# ─── Flatten to Single Texture ────────────────────────────────────────────────

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
            self.report({'INFO'}, f"Flattened to '{img.name}' ({res}×{res})")
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        return {'FINISHED'}

    def draw(self, context):
        self.layout.prop(self, "output_name")


# ─── Add Mask to Layer ────────────────────────────────────────────────────────

class TLM_OT_AddLayerMask(Operator):
    """Add a white (fully visible) mask to the active layer."""
    bl_idname = "tlm.add_layer_mask"
    bl_label = "Add Mask"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and mat.tlm.active_layer is not None

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        layer = tlm.active_layer

        if layer.use_mask and layer.mask_image:
            self.report({'WARNING'}, "Layer already has a mask.")
            return {'CANCELLED'}

        res = int(tlm.resolution)
        mask_img = bpy.data.images.new(
            name=f"{layer.name}_mask",
            width=res, height=res,
            alpha=False,
        )
        # Fill with white = fully visible (numpy: faster than Python list on 2K/4K)
        import numpy as np
        px = np.ones(res * res * 4, dtype=np.float32)
        mask_img.pixels.foreach_set(px)
        mask_img.use_fake_user = True

        layer.mask_image_name = mask_img.name
        layer.use_mask = True

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Mask added to '{layer.name}'")
        return {'FINISHED'}


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


# ─── Export / Import JSON ─────────────────────────────────────────────────────

import json
import base64
import os
import struct
import zlib
import numpy as np


def _image_to_png_b64(image):
    """
    Encode a bpy.data.images image as a base64 PNG string.
    Pure Python — no external deps beyond numpy (already required).
    """
    if image is None:
        return None
    w, h = image.size
    if w == 0 or h == 0:
        return None

    # Read pixels as float32 RGBA
    buf = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(buf)
    buf = buf.reshape((h, w, 4))

    # Flip vertically (Blender bottom-up → PNG top-down)
    buf = buf[::-1, :, :]

    # Convert to uint8
    px = (np.clip(buf, 0, 1) * 255).astype(np.uint8)

    # Build minimal PNG in memory
    def png_chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    # PNG signature
    sig = b'\x89PNG\r\n\x1a\n'

    # IHDR — RGBA 8-bit (color type 6)
    ihdr_data = struct.pack('>II', w, h) + bytes([8, 6, 0, 0, 0])
    ihdr = png_chunk(b'IHDR', ihdr_data)

    # IDAT — prepend filter byte 0 to each scanline, then compress.
    # Using numpy to build the raw byte stream is significantly faster than
    # string concatenation in a Python loop on large images.
    filter_col = np.zeros((h, 1), dtype=np.uint8)
    rows_with_filter = np.concatenate([filter_col, px.reshape(h, w * 4)], axis=1)
    compressed = zlib.compress(rows_with_filter.tobytes(), 6)  # level 6: good balance
    idat = png_chunk(b'IDAT', compressed)

    # IEND
    iend = png_chunk(b'IEND', b'')

    png_bytes = sig + ihdr + idat + iend
    return base64.b64encode(png_bytes).decode('ascii')


def _png_b64_to_image(b64_str, name, expected_w, expected_h):
    """
    Decode a base64 PNG string back into a bpy.data.images image.
    Uses Blender's built-in image loading via a temp file.
    """
    import tempfile

    png_bytes = base64.b64decode(b64_str)

    # Write to temp file and let Blender load it
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(png_bytes)
        tmp_path = f.name

    try:
        # Remove existing image with same name to avoid conflicts
        if name in bpy.data.images:
            bpy.data.images.remove(bpy.data.images[name])

        img = bpy.data.images.load(tmp_path)
        img.name = name
        img.pack()  # embed in .blend so temp file can be deleted
    finally:
        os.unlink(tmp_path)

    return img


def _layer_to_dict(layer):
    """Serialize a TLM_LayerItem to a plain dict."""
    d = {
        "name":              layer.name,
        "type":              layer.layer_type,
        "visible":           layer.visible,
        "locked":            layer.locked,
        "opacity":           round(layer.opacity, 4),
        "blend_mode":        layer.blend_mode,
        "group_name":        layer.group_name,
        "collapsed":         layer.collapsed,
        "use_clipping_mask": layer.use_clipping_mask,
    }

    if layer.layer_type == "PAINT":
        d["image_name"] = layer.image_name
        d["image_data"] = _image_to_png_b64(layer.image)

    elif layer.layer_type == "FILL":
        d["fill_color"] = list(layer.fill_color)

    elif layer.layer_type == "PROCEDURAL":
        d["proc_type"]             = layer.proc_type
        d["proc_scale"]            = round(layer.proc_scale, 4)
        d["proc_color1"]           = list(layer.proc_color1)
        d["proc_color2"]           = list(layer.proc_color2)
        d["proc_detail"]           = round(layer.proc_detail, 4)
        d["proc_roughness_proc"]   = round(layer.proc_roughness_proc, 4)
        d["proc_distortion"]       = round(layer.proc_distortion, 4)
        d["proc_lacunarity"]       = round(layer.proc_lacunarity, 4)
        d["proc_offset_x"]         = round(layer.proc_offset_x, 4)
        d["proc_offset_y"]         = round(layer.proc_offset_y, 4)
        d["proc_offset_z"]         = round(layer.proc_offset_z, 4)
        d["proc_voronoi_feature"]  = layer.proc_voronoi_feature
        d["proc_voronoi_distance"] = layer.proc_voronoi_distance
        d["proc_randomness"]       = round(layer.proc_randomness, 4)
        d["proc_wave_type"]        = layer.proc_wave_type
        d["proc_wave_profile"]     = layer.proc_wave_profile
        d["proc_wave_detail_scale"]= round(layer.proc_wave_detail_scale, 4)
        d["proc_gradient_type"]    = layer.proc_gradient_type
        d["proc_checker_scale"]    = round(layer.proc_checker_scale, 4)
        d["proc_contrast"]         = round(layer.proc_contrast, 4)
        d["proc_vector_distortion"]= round(layer.proc_vector_distortion, 4)
        d["proc_coord_type"]       = layer.proc_coord_type
        d["proc_marble_distortion"]= round(layer.proc_marble_distortion, 4)
        d["proc_marble_wave_type"] = layer.proc_marble_wave_type
        d["proc_emission_threshold"] = round(getattr(layer, 'proc_emission_threshold', 0.0), 4)
        d["use_proc_color3"]       = getattr(layer, 'use_proc_color3', False)
        if d["use_proc_color3"]:
            d["proc_color3"]          = list(layer.proc_color3)
            d["proc_color3_position"] = round(layer.proc_color3_position, 4)

    elif layer.layer_type == "ADJUSTMENT":
        d["adj_type"]        = layer.adj_type
        d["adj_hue"]         = round(layer.adj_hue, 4)
        d["adj_saturation"]  = round(layer.adj_saturation, 4)
        d["adj_value"]       = round(layer.adj_value, 4)
        d["adj_brightness"]  = round(layer.adj_brightness, 4)
        d["adj_contrast"]    = round(layer.adj_contrast, 4)
        d["adj_in_min"]      = round(layer.adj_in_min, 4)
        d["adj_in_max"]      = round(layer.adj_in_max, 4)
        d["adj_levels_gamma"] = round(layer.adj_levels_gamma, 4)
        d["adj_out_min"]     = round(layer.adj_out_min, 4)
        d["adj_out_max"]     = round(layer.adj_out_max, 4)
        d["adj_curve_contrast"]    = round(layer.adj_curve_contrast, 4)
        d["adj_curve_brightness"]  = round(layer.adj_curve_brightness, 4)
        d["adj_curve_black_point"] = round(layer.adj_curve_black_point, 4)
        d["adj_curve_white_point"] = round(layer.adj_curve_white_point, 4)
        d["adj_lift"]  = list(layer.adj_lift)
        d["adj_gamma"] = list(layer.adj_gamma)
        d["adj_gain"]  = list(layer.adj_gain)

    # Common properties for non-GROUP, non-ADJUSTMENT layers
    if layer.layer_type not in ("GROUP", "ADJUSTMENT"):
        d["use_fresnel_mask"]  = getattr(layer, 'use_fresnel_mask', False)
        d["fresnel_ior"]       = round(getattr(layer, 'fresnel_ior', 1.45), 4)
        d["fresnel_strength"]  = round(getattr(layer, 'fresnel_strength', 1.0), 4)
        d["use_mask"]          = layer.use_mask
        d["mask_image_name"]   = layer.mask_image_name
        d["use_triplanar"]     = getattr(layer, 'use_triplanar', False)
        d["triplanar_scale"]   = round(getattr(layer, 'triplanar_scale', 1.0), 4)
        d["triplanar_sharpness"] = round(getattr(layer, 'triplanar_sharpness', 1.0), 4)
        # PBR channels
        d["use_roughness"]     = layer.use_roughness
        d["roughness_fill"]    = round(layer.roughness_fill, 4)
        d["roughness_image_name"] = getattr(layer, 'roughness_image_name', "")
        d["use_metallic"]      = layer.use_metallic
        d["metallic_fill"]     = round(layer.metallic_fill, 4)
        d["metallic_image_name"]  = getattr(layer, 'metallic_image_name', "")
        d["use_bump"]          = layer.use_bump
        d["bump_strength"]     = round(layer.bump_strength, 4)
        d["bump_distance"]     = round(layer.bump_distance, 4)
        d["use_normal"]        = getattr(layer, 'use_normal', False)
        d["normal_image_name"]    = getattr(layer, 'normal_image_name', "")
        d["use_emission"]      = getattr(layer, 'use_emission', False)
        if layer.use_emission:
            d["emission_color"]    = list(layer.emission_color)
            d["emission_strength"] = round(layer.emission_strength, 4)
        d["emission_image_name"]  = getattr(layer, 'emission_image_name', "")
        d["use_transmission"]  = getattr(layer, 'use_transmission', False)
        d["transmission_fill"] = round(getattr(layer, 'transmission_fill', 0.0), 4)
        d["transmission_image_name"] = getattr(layer, 'transmission_image_name', "")

    return d


def _dict_to_layer(d, tlm):
    """Deserialize a dict into a new TLM_LayerItem appended to tlm.layers."""
    layer = tlm.layers.add()
    layer.name       = d.get("name", "Layer")
    layer.layer_type = d.get("type", "PAINT")
    layer.visible    = d.get("visible", True)
    layer.locked     = d.get("locked", False)
    layer.opacity    = d.get("opacity", 1.0)
    layer.blend_mode = d.get("blend_mode", "MIX")
    layer.group_name        = d.get("group_name", "")
    layer.collapsed         = d.get("collapsed", False)
    layer.use_clipping_mask = d.get("use_clipping_mask", False)

    if layer.layer_type == "PAINT":
        img_name = d.get("image_name", layer.name)
        img_data = d.get("image_data")
        if img_data:
            img = _png_b64_to_image(img_data, img_name, 0, 0)
            layer.image_name = img.name
        else:
            # No pixel data — create blank image
            res = int(tlm.resolution)
            img = bpy.data.images.new(img_name, width=res, height=res, alpha=True)
            img.pixels[:] = [0.0] * (res * res * 4)
            layer.image_name = img.name

    elif layer.layer_type == "FILL":
        fc = d.get("fill_color", [1, 1, 1, 1])
        layer.fill_color = fc

    elif layer.layer_type == "PROCEDURAL":
        layer.proc_type             = d.get("proc_type", "NOISE")
        layer.proc_scale            = d.get("proc_scale", 5.0)
        layer.proc_color1           = d.get("proc_color1", [0,0,0,1])
        layer.proc_color2           = d.get("proc_color2", [1,1,1,1])
        layer.proc_detail           = d.get("proc_detail", 2.0)
        layer.proc_roughness_proc   = d.get("proc_roughness_proc", 0.5)
        layer.proc_distortion       = d.get("proc_distortion", 0.0)
        layer.proc_lacunarity       = d.get("proc_lacunarity", 2.0)
        layer.proc_offset_x         = d.get("proc_offset_x", 0.0)
        layer.proc_offset_y         = d.get("proc_offset_y", 0.0)
        layer.proc_offset_z         = d.get("proc_offset_z", 0.0)
        layer.proc_voronoi_feature  = d.get("proc_voronoi_feature", "F1")
        layer.proc_voronoi_distance = d.get("proc_voronoi_distance", "EUCLIDEAN")
        layer.proc_randomness       = d.get("proc_randomness", 1.0)
        layer.proc_wave_type        = d.get("proc_wave_type", "BANDS")
        layer.proc_wave_profile     = d.get("proc_wave_profile", "SIN")
        layer.proc_wave_detail_scale= d.get("proc_wave_detail_scale", 1.0)
        layer.proc_gradient_type    = d.get("proc_gradient_type", "LINEAR")
        layer.proc_checker_scale    = d.get("proc_checker_scale", 5.0)
        layer.proc_contrast         = d.get("proc_contrast", 0.5)
        layer.proc_vector_distortion= d.get("proc_vector_distortion", 0.0)
        layer.proc_coord_type       = d.get("proc_coord_type", "GENERATED")
        layer.proc_marble_distortion= d.get("proc_marble_distortion", 5.0)
        layer.proc_marble_wave_type = d.get("proc_marble_wave_type", "BANDS")
        layer.proc_emission_threshold = d.get("proc_emission_threshold", 0.0)
        layer.use_proc_color3       = d.get("use_proc_color3", False)
        if layer.use_proc_color3:
            layer.proc_color3          = d.get("proc_color3", [0.5, 0.5, 0.5, 1])
            layer.proc_color3_position = d.get("proc_color3_position", 0.5)

    elif layer.layer_type == "ADJUSTMENT":
        layer.adj_type       = d.get("adj_type", "HUE_SAT")
        layer.adj_hue        = d.get("adj_hue", 0.5)
        layer.adj_saturation = d.get("adj_saturation", 1.0)
        layer.adj_value      = d.get("adj_value", 1.0)
        layer.adj_brightness = d.get("adj_brightness", 0.0)
        layer.adj_contrast   = d.get("adj_contrast", 0.0)
        layer.adj_in_min     = d.get("adj_in_min", 0.0)
        layer.adj_in_max     = d.get("adj_in_max", 1.0)
        # Support old key "adj_gamma" for backwards compat with files saved before this fix
        layer.adj_levels_gamma = d.get("adj_levels_gamma", d.get("adj_gamma", 1.0))
        layer.adj_out_min    = d.get("adj_out_min", 0.0)
        layer.adj_out_max    = d.get("adj_out_max", 1.0)
        layer.adj_curve_contrast    = d.get("adj_curve_contrast", 0.0)
        layer.adj_curve_brightness  = d.get("adj_curve_brightness", 0.0)
        layer.adj_curve_black_point = d.get("adj_curve_black_point", 0.0)
        layer.adj_curve_white_point = d.get("adj_curve_white_point", 1.0)
        layer.adj_lift  = d.get("adj_lift", [1, 1, 1])
        layer.adj_gamma = d.get("adj_gamma", [1, 1, 1])
        layer.adj_gain  = d.get("adj_gain", [1, 1, 1])

    # Common properties for non-GROUP, non-ADJUSTMENT layers
    if layer.layer_type not in ("GROUP", "ADJUSTMENT"):
        layer.use_fresnel_mask  = d.get("use_fresnel_mask", False)
        layer.fresnel_ior       = d.get("fresnel_ior", 1.45)
        layer.fresnel_strength  = d.get("fresnel_strength", 1.0)
        layer.use_mask          = d.get("use_mask", False)
        layer.mask_image_name   = d.get("mask_image_name", "")
        layer.use_triplanar     = d.get("use_triplanar", False)
        layer.triplanar_scale   = d.get("triplanar_scale", 1.0)
        layer.triplanar_sharpness = d.get("triplanar_sharpness", 1.0)
        # PBR channels
        layer.use_roughness        = d.get("use_roughness", False)
        layer.roughness_fill       = d.get("roughness_fill", 0.5)
        layer.roughness_image_name = d.get("roughness_image_name", "")
        layer.use_metallic         = d.get("use_metallic", False)
        layer.metallic_fill        = d.get("metallic_fill", 0.0)
        layer.metallic_image_name  = d.get("metallic_image_name", "")
        layer.use_bump             = d.get("use_bump", False)
        layer.bump_strength        = d.get("bump_strength", 0.5)
        layer.bump_distance        = d.get("bump_distance", 0.05)
        layer.use_normal           = d.get("use_normal", False)
        layer.normal_image_name    = d.get("normal_image_name", "")
        layer.use_emission         = d.get("use_emission", False)
        layer.emission_image_name  = d.get("emission_image_name", "")
        if layer.use_emission:
            layer.emission_color    = d.get("emission_color", [1,1,1,1])
            layer.emission_strength = d.get("emission_strength", 1.0)
        layer.use_transmission        = d.get("use_transmission", False)
        layer.transmission_fill       = d.get("transmission_fill", 0.0)
        layer.transmission_image_name = d.get("transmission_image_name", "")

    return layer


class TLM_OT_ExportJSON(Operator):
    """Export the current layer stack to a .tlm JSON file."""
    bl_idname = "tlm.export_json"
    bl_label = "Export Layer Stack"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH', default="layer_stack.tlm")
    filter_glob: bpy.props.StringProperty(default="*.tlm", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def invoke(self, context, event):
        mat = _get_material(context)
        self.filepath = f"{mat.name}_layers.tlm"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm

        data = {
            "tlm_version": "0.3.16",
            "material":    mat.name,
            "resolution":  tlm.resolution,
            "uv_map":      tlm.uv_map,
            "layers":      [_layer_to_dict(l) for l in tlm.layers],
        }

        filepath = bpy.path.abspath(self.filepath)
        if not filepath.endswith('.tlm'):
            filepath += '.tlm'

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.report({'INFO'}, f"Exported {len(tlm.layers)} layers to {os.path.basename(filepath)}")
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


class TLM_OT_ImportJSON(Operator):
    """Import a .tlm JSON file and rebuild the layer stack."""
    bl_idname = "tlm.import_json"
    bl_label = "Import Layer Stack"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.tlm", options={'HIDDEN'})

    merge: bpy.props.BoolProperty(
        name="Merge with existing",
        description="Add imported layers on top of the current stack (unchecked = replace)",
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
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, f"Import failed: {e}")
            return {'CANCELLED'}

        if not self.merge:
            # Clear existing layers
            tlm.layers.clear()

        layers_data = data.get("layers", [])
        for ld in layers_data:
            _dict_to_layer(ld, tlm)

        # Restore settings if not merging
        if not self.merge:
            tlm.resolution = data.get("resolution", tlm.resolution)
            tlm.uv_map     = data.get("uv_map", tlm.uv_map)

        tlm.active_layer_index = max(0, len(tlm.layers) - 1)

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Imported {len(layers_data)} layers from {os.path.basename(filepath)}")
        return {'FINISHED'}


# ─── PBR Channel Operators ────────────────────────────────────────────────────

# Map channel id → (flag_attr, image_name_attr, label)
CHANNEL_INFO = {
    'roughness':    ('use_roughness',    'roughness_image_name',    'Roughness'),
    'metallic':     ('use_metallic',     'metallic_image_name',     'Metallic'),
    'normal':       ('use_normal',       'normal_image_name',       'Normal'),
    'transmission': ('use_transmission', 'transmission_image_name', 'Transmission'),
    'emission':  ('use_emission',  'emission_image_name',  'Emission'),
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

        # Default pixel values per channel
        defaults = {
            'roughness': [0.5, 0.5, 0.5, 1.0],
            'metallic':  [0.0, 0.0, 0.0, 1.0],
            'normal':    [0.5, 0.5, 1.0, 1.0],  # flat normal map color
            'emission':     [0.0, 0.0, 0.0, 1.0],
            'transmission': [0.0, 0.0, 0.0, 1.0],
        }
        fill = defaults.get(self.channel, [0.5, 0.5, 0.5, 1.0])

        img = bpy.data.images.new(img_name, width=res, height=res, alpha=True)
        import numpy as np
        fill_px = np.array(fill * (res * res), dtype=np.float32)
        img.pixels.foreach_set(fill_px)
        img.pack()

        # Mark non-color for technical maps
        if self.channel in ('roughness', 'metallic', 'normal', 'transmission'):
            try:
                img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass

        setattr(layer, img_attr, img.name)
        setattr(layer, flag_attr, True)

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)
            # Force shader editor redraw — operator context doesn't always
            # propagate to NODE_EDITOR areas after a full node tree rebuild.
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


# ─── Import Texture as Layer ──────────────────────────────────────────────────

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


# ─── Bake PBR Export ──────────────────────────────────────────────────────────

class TLM_OT_BakePBR(Operator):
    """Bake all active PBR channels to image files ready for Unity/Unreal/GLTF."""
    bl_idname = "tlm.bake_pbr"
    bl_label = "Bake & Export PBR"
    bl_options = {'REGISTER'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    preset: bpy.props.EnumProperty(
        name="Preset",
        items=[
            ('UNREAL',  "Unreal Engine", "Albedo, Normal, ORM (R=Occlusion G=Roughness B=Metallic)"),
            ('UNITY',   "Unity HDRP",    "Albedo, Normal, Mask (R=Metallic A=Smoothness)"),
            ('GLTF',    "glTF",          "BaseColor, Normal, MetallicRoughness"),
            ('CUSTOM',  "Custom",        "Bake each channel separately"),
        ],
        default='UNREAL',
    )

    resolution: bpy.props.EnumProperty(
        name="Resolution",
        items=[
            ("512",  "512",  ""),
            ("1024", "1024", ""),
            ("2048", "2048", ""),
            ("4096", "4096", ""),
        ],
        default="1024",
    )

    file_format: bpy.props.EnumProperty(
        name="Format",
        items=[
            ('PNG',  "PNG",  ""),
            ('JPEG', "JPEG", ""),
            ('TIFF', "TIFF", ""),
            ('OPEN_EXR', "EXR", ""),
        ],
        default='PNG',
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

        import os
        res = int(self.resolution)
        ext = {'PNG': 'png', 'JPEG': 'jpg', 'TIFF': 'tif', 'OPEN_EXR': 'exr'}[self.file_format]
        base = mat.name
        out_dir = bpy.path.abspath(self.directory)
        os.makedirs(out_dir, exist_ok=True)

        # Rebuild node tree to ensure it's current
        compositing.rebuild_node_tree(mat)
        node_tree = mat.node_tree

        baked = []

        def _bake_channel(suffix, bsdf_input, colorspace="sRGB"):
            """Bake a single PBR channel via temporary Emission shader (no lighting)."""
            bsdf = next((n for n in node_tree.nodes
                         if n.type == 'BSDF_PRINCIPLED'
                         and not n.name.startswith('TLM_')), None)
            if not bsdf:
                return None

            socket = bsdf.inputs.get(bsdf_input)
            if not socket or not socket.links:
                return None

            # Get the node/socket feeding the BSDF input
            source_link = socket.links[0]
            source_socket = source_link.from_socket

            # Find Material Output
            mat_output = next((n for n in node_tree.nodes
                               if n.type == 'OUTPUT_MATERIAL'), None)
            if not mat_output:
                return None

            # Save original connection to Material Output Surface
            orig_surface_links = []
            surface_input = mat_output.inputs.get("Surface")
            if surface_input and surface_input.links:
                for lnk in surface_input.links:
                    orig_surface_links.append(lnk.from_socket)

            # Create temp Emission shader
            emit_node = node_tree.nodes.new("ShaderNodeEmission")
            emit_node.name = "TLM_bake_emit"
            emit_node.location = (400, 200)

            # For scalar channels (Roughness, Metallic), we need to convert
            # the value to color. Check if source is a color or value.
            # If the BSDF input is a scalar type, route through a converter.
            is_normal = (bsdf_input == "Normal")

            if is_normal:
                # Normal maps: bake the color data from the Normal Map node's input
                # Find the Normal Map node
                normal_node = source_socket.node
                if normal_node.type == 'NORMAL_MAP':
                    color_input = normal_node.inputs.get("Color")
                    if color_input and color_input.links:
                        source_socket = color_input.links[0].from_socket
                    else:
                        # No color input to normal map, skip
                        node_tree.nodes.remove(emit_node)
                        return None

                node_tree.links.new(source_socket, emit_node.inputs["Color"])
            else:
                node_tree.links.new(source_socket, emit_node.inputs["Color"])

            # Connect Emission → Material Output
            node_tree.links.new(emit_node.outputs["Emission"], surface_input)

            # Create bake target image
            img_name = f"{base}_{suffix}"
            if img_name in bpy.data.images:
                bpy.data.images.remove(bpy.data.images[img_name])
            img = bpy.data.images.new(img_name, width=res, height=res, alpha=True)
            if colorspace == "Non-Color":
                try:
                    img.colorspace_settings.name = "Non-Color"
                except Exception:
                    pass

            bake_node = node_tree.nodes.new("ShaderNodeTexImage")
            bake_node.name = "TLM_bake_tmp"
            bake_node.image = img
            bake_node.location = (600, 0)
            for n in node_tree.nodes:
                n.select = False
            bake_node.select = True
            node_tree.nodes.active = bake_node

            try:
                bpy.ops.object.bake(type='EMIT', save_mode='INTERNAL')
                filepath = os.path.join(out_dir, f"{img_name}.{ext}")
                img.filepath_raw = filepath
                img.file_format = self.file_format
                img.save()
                baked.append(f"{suffix} → {img_name}.{ext}")
            except Exception as e:
                self.report({'WARNING'}, f"Bake failed for {suffix}: {e}")
            finally:
                # Restore original connections
                node_tree.nodes.remove(bake_node)
                node_tree.nodes.remove(emit_node)
                # Re-link original shader to Material Output
                for orig_sock in orig_surface_links:
                    node_tree.links.new(orig_sock, surface_input)

            return img

        def _pack_orm(roughness_img, metallic_img):
            """Pack into ORM: R=AO(white), G=Roughness, B=Metallic."""
            import numpy as np
            img_name = f"{base}_ORM"
            if img_name in bpy.data.images:
                bpy.data.images.remove(bpy.data.images[img_name])
            orm = bpy.data.images.new(img_name, width=res, height=res, alpha=False)
            try:
                orm.colorspace_settings.name = "Non-Color"
            except Exception:
                pass

            px = np.ones(res * res * 4, dtype=np.float32)  # all white (AO=1)

            if roughness_img:
                r_px = np.zeros(res * res * 4, dtype=np.float32)
                roughness_img.pixels.foreach_get(r_px)
                px[1::4] = r_px[0::4]  # G = Roughness red channel
            else:
                px[1::4] = 0.5  # default roughness

            if metallic_img:
                m_px = np.zeros(res * res * 4, dtype=np.float32)
                metallic_img.pixels.foreach_get(m_px)
                px[2::4] = m_px[0::4]  # B = Metallic red channel
            else:
                px[2::4] = 0.0  # default metallic

            px[3::4] = 1.0  # Alpha = 1
            orm.pixels.foreach_set(px)
            orm.update()

            filepath = os.path.join(out_dir, f"{img_name}.{ext}")
            orm.filepath_raw = filepath
            orm.file_format = self.file_format
            orm.save()
            baked.append(f"ORM → {img_name}.{ext}")

            # Clean up temp separate images
            if roughness_img and roughness_img.name != img_name:
                bpy.data.images.remove(roughness_img)
            if metallic_img and metallic_img.name != img_name:
                bpy.data.images.remove(metallic_img)
            return orm

        def _pack_unity_mask(metallic_img, roughness_img):
            """Pack Unity Mask: R=Metallic, G=0, B=0, A=Smoothness (1-Roughness)."""
            import numpy as np
            img_name = f"{base}_Mask"
            if img_name in bpy.data.images:
                bpy.data.images.remove(bpy.data.images[img_name])
            mask = bpy.data.images.new(img_name, width=res, height=res, alpha=True)
            try:
                mask.colorspace_settings.name = "Non-Color"
            except Exception:
                pass

            px = np.zeros(res * res * 4, dtype=np.float32)

            if metallic_img:
                m_px = np.zeros(res * res * 4, dtype=np.float32)
                metallic_img.pixels.foreach_get(m_px)
                px[0::4] = m_px[0::4]  # R = Metallic
            # G, B = 0

            if roughness_img:
                r_px = np.zeros(res * res * 4, dtype=np.float32)
                roughness_img.pixels.foreach_get(r_px)
                px[3::4] = 1.0 - r_px[0::4]  # A = Smoothness (1 - Roughness)
            else:
                px[3::4] = 0.5  # default smoothness

            mask.pixels.foreach_set(px)
            mask.update()

            filepath = os.path.join(out_dir, f"{img_name}.{ext}")
            mask.filepath_raw = filepath
            mask.file_format = self.file_format
            mask.save()
            baked.append(f"Mask → {img_name}.{ext}")

            if roughness_img:
                bpy.data.images.remove(roughness_img)
            if metallic_img:
                bpy.data.images.remove(metallic_img)
            return mask

        def _pack_gltf_mr(metallic_img, roughness_img):
            """Pack glTF MetallicRoughness: R=0, G=Roughness, B=Metallic, A=1."""
            import numpy as np
            img_name = f"{base}_MetallicRoughness"
            if img_name in bpy.data.images:
                bpy.data.images.remove(bpy.data.images[img_name])
            mr = bpy.data.images.new(img_name, width=res, height=res, alpha=False)
            try:
                mr.colorspace_settings.name = "Non-Color"
            except Exception:
                pass

            px = np.zeros(res * res * 4, dtype=np.float32)

            if roughness_img:
                r_px = np.zeros(res * res * 4, dtype=np.float32)
                roughness_img.pixels.foreach_get(r_px)
                px[1::4] = r_px[0::4]  # G = Roughness
            else:
                px[1::4] = 0.5

            if metallic_img:
                m_px = np.zeros(res * res * 4, dtype=np.float32)
                metallic_img.pixels.foreach_get(m_px)
                px[2::4] = m_px[0::4]  # B = Metallic
            # R = 0 (unused in glTF spec)

            px[3::4] = 1.0
            mr.pixels.foreach_set(px)
            mr.update()

            filepath = os.path.join(out_dir, f"{img_name}.{ext}")
            mr.filepath_raw = filepath
            mr.file_format = self.file_format
            mr.save()
            baked.append(f"MetallicRoughness → {img_name}.{ext}")

            if roughness_img:
                bpy.data.images.remove(roughness_img)
            if metallic_img:
                bpy.data.images.remove(metallic_img)
            return mr

        if self.preset == 'UNREAL':
            _bake_channel("Albedo",     "Base Color",  "sRGB")
            _bake_channel("Normal",     "Normal",      "Non-Color")
            rough_img = _bake_channel("_tmp_Roughness", "Roughness", "Non-Color")
            metal_img = _bake_channel("_tmp_Metallic",  "Metallic",  "Non-Color")
            _pack_orm(rough_img, metal_img)

        elif self.preset == 'UNITY':
            _bake_channel("Albedo",     "Base Color",  "sRGB")
            _bake_channel("Normal",     "Normal",      "Non-Color")
            metal_img = _bake_channel("_tmp_Metallic",  "Metallic",  "Non-Color")
            rough_img = _bake_channel("_tmp_Roughness", "Roughness", "Non-Color")
            _pack_unity_mask(metal_img, rough_img)

        elif self.preset == 'GLTF':
            _bake_channel("BaseColor",  "Base Color",  "sRGB")
            _bake_channel("Normal",     "Normal",      "Non-Color")
            metal_img = _bake_channel("_tmp_Metallic",  "Metallic",  "Non-Color")
            rough_img = _bake_channel("_tmp_Roughness", "Roughness", "Non-Color")
            _pack_gltf_mr(metal_img, rough_img)

        elif self.preset == 'CUSTOM':
            _bake_channel("BaseColor", "Base Color",  "sRGB")
            _bake_channel("Roughness", "Roughness",   "Non-Color")
            _bake_channel("Metallic",  "Metallic",    "Non-Color")
            _bake_channel("Normal",    "Normal",      "Non-Color")
            _bake_channel("Emission",  "Emission Color", "sRGB")

        if baked:
            self.report({'INFO'}, f"Baked {len(baked)} maps to {out_dir}")
        else:
            self.report({'WARNING'}, "Nothing to bake — no PBR channels connected")

        return {'FINISHED'}


# ─── Smart Masks ──────────────────────────────────────────────────────────────

class TLM_OT_AddSmartMask(Operator):
    """Add a geometry-based smart mask (AO or Curvature) to the active layer."""
    bl_idname = "tlm.add_smart_mask"
    bl_label = "Add Smart Mask"
    bl_options = {'REGISTER', 'UNDO'}

    mask_type: bpy.props.EnumProperty(
        name="Type",
        items=[
            ('AO',        "Ambient Occlusion", "Darken crevices and cavities"),
            ('CURVATURE', "Curvature",          "Highlight edges and ridges"),
            ('FACING',    "Facing Angle",       "Based on angle to camera/world up"),
            ('HEIGHT',    "Height",             "Based on world Z position"),
        ],
        default='AO',
    )

    # AO params
    ao_distance: bpy.props.FloatProperty(name="Distance", default=1.0, min=0.001, max=100.0)
    ao_samples:  bpy.props.IntProperty(name="Samples",   default=16,  min=1,     max=128)

    # Curvature params
    curv_ridge:  bpy.props.FloatProperty(name="Ridge",   default=1.0, min=0.0, max=2.0)
    curv_valley: bpy.props.FloatProperty(name="Valley",  default=1.0, min=0.0, max=2.0)

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and mat.tlm.active_layer is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "mask_type")
        layout.separator()
        if self.mask_type == 'AO':
            layout.prop(self, "ao_distance")
            layout.prop(self, "ao_samples")
        elif self.mask_type == 'CURVATURE':
            layout.prop(self, "curv_ridge")
            layout.prop(self, "curv_valley")

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        layer = tlm.active_layer
        if not layer:
            return {'CANCELLED'}

        node_tree = mat.node_tree
        if not node_tree:
            mat.use_nodes = True
            node_tree = mat.node_tree

        # Build smart mask node group directly in the material
        mask_node = self._build_smart_mask_nodes(node_tree)
        if mask_node is None:
            self.report({'ERROR'}, "Failed to build smart mask nodes")
            return {'CANCELLED'}

        # Bake the smart mask to an image so it works as a standard mask
        res = int(tlm.resolution)
        img_name = f"{layer.name}_SmartMask_{self.mask_type}"
        if img_name in bpy.data.images:
            bpy.data.images.remove(bpy.data.images[img_name])
        img = bpy.data.images.new(img_name, width=res, height=res, alpha=False)
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass

        # Add temporary bake target
        bake_node = node_tree.nodes.new("ShaderNodeTexImage")
        bake_node.name = "TLM_smartmask_bake"
        bake_node.image = img
        bake_node.location = (800, -200)
        for n in node_tree.nodes:
            n.select = False
        bake_node.select = True
        node_tree.nodes.active = bake_node

        try:
            if self.mask_type == 'AO':
                bpy.ops.object.bake(type='AO', save_mode='INTERNAL')
            else:
                bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'}, save_mode='INTERNAL')
            img.pack()
            layer.use_mask = True
            layer.mask_image_name = img.name
            self.report({'INFO'}, f"Smart mask '{self.mask_type}' applied to '{layer.name}'")
        except Exception as e:
            self.report({'WARNING'}, f"Bake failed: {e}. Nodes added but mask not baked.")
        finally:
            node_tree.nodes.remove(bake_node)
            # Remove temp smart mask nodes
            for n in [n for n in node_tree.nodes if n.name.startswith("TLM_sm_")]:
                node_tree.nodes.remove(n)

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        return {'FINISHED'}

    def _build_smart_mask_nodes(self, node_tree):
        """Build procedural smart mask nodes (not baked yet)."""
        mt = self.mask_type

        if mt == 'AO':
            ao = node_tree.nodes.new("ShaderNodeAmbientOcclusion")
            ao.name = "TLM_sm_ao"
            ao.samples = self.ao_samples
            ao.inputs["Distance"].default_value = self.ao_distance
            ao.location = (400, -200)
            emit = node_tree.nodes.new("ShaderNodeEmission")
            emit.name = "TLM_sm_emit"
            emit.location = (600, -200)
            node_tree.links.new(ao.outputs["AO"], emit.inputs["Color"])
            out = node_tree.nodes.new("ShaderNodeOutputMaterial")
            out.name = "TLM_sm_out"
            out.location = (800, -200)
            out.target = 'ALL'
            node_tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
            return ao

        elif mt == 'CURVATURE':
            bevel = node_tree.nodes.new("ShaderNodeBevel")
            bevel.name = "TLM_sm_bevel"
            bevel.samples = 4
            bevel.inputs["Radius"].default_value = 0.05
            bevel.location = (400, -200)
            geo = node_tree.nodes.new("ShaderNodeNewGeometry")
            geo.name = "TLM_sm_geo"
            geo.location = (200, -200)
            dot = node_tree.nodes.new("ShaderNodeVectorMath")
            dot.operation = 'DOT_PRODUCT'
            dot.name = "TLM_sm_dot"
            dot.location = (600, -200)
            node_tree.links.new(bevel.outputs["Normal"], dot.inputs[0])
            node_tree.links.new(geo.outputs["Normal"], dot.inputs[1])
            emit = node_tree.nodes.new("ShaderNodeEmission")
            emit.name = "TLM_sm_emit"
            emit.location = (800, -200)
            node_tree.links.new(dot.outputs["Value"], emit.inputs["Color"])
            out = node_tree.nodes.new("ShaderNodeOutputMaterial")
            out.name = "TLM_sm_out"
            out.location = (1000, -200)
            out.target = 'ALL'
            node_tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
            return bevel

        elif mt == 'FACING':
            geo = node_tree.nodes.new("ShaderNodeNewGeometry")
            geo.name = "TLM_sm_geo"
            geo.location = (400, -200)
            emit = node_tree.nodes.new("ShaderNodeEmission")
            emit.name = "TLM_sm_emit"
            emit.location = (600, -200)
            node_tree.links.new(geo.outputs["Backfacing"], emit.inputs["Color"])
            out = node_tree.nodes.new("ShaderNodeOutputMaterial")
            out.name = "TLM_sm_out"
            out.location = (800, -200)
            out.target = 'ALL'
            node_tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
            return geo

        elif mt == 'HEIGHT':
            geo = node_tree.nodes.new("ShaderNodeNewGeometry")
            geo.name = "TLM_sm_geo"
            geo.location = (200, -200)
            sep = node_tree.nodes.new("ShaderNodeSeparateXYZ")
            sep.name = "TLM_sm_sep"
            sep.location = (400, -200)
            node_tree.links.new(geo.outputs["Position"], sep.inputs["Vector"])
            emit = node_tree.nodes.new("ShaderNodeEmission")
            emit.name = "TLM_sm_emit"
            emit.location = (600, -200)
            node_tree.links.new(sep.outputs["Z"], emit.inputs["Color"])
            out = node_tree.nodes.new("ShaderNodeOutputMaterial")
            out.name = "TLM_sm_out"
            out.location = (800, -200)
            out.target = 'ALL'
            node_tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
            return geo

        return None


# ─── Preset Stack ─────────────────────────────────────────────────────────────

import json as _json
import os as _os

# Built-in presets shipped with the addon
BUILTIN_PRESETS = {
    "Metal Base": [
        # Fill base: dark steel, metallic, low roughness
        {"name": "Metal Base", "type": "FILL",
         "fill_color": [0.08, 0.08, 0.09, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.25,
         "use_metallic": True,  "metallic_fill":  1.0},
        # Proc: surface variation in roughness + subtle bump
        {"name": "Metal Surface", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 1.5, "proc_detail": 3.0, "proc_roughness_proc": 0.5,
         "proc_distortion": 0.2,
         "proc_color1": [0.06, 0.06, 0.07, 1.0],
         "proc_color2": [0.18, 0.18, 0.20, 1.0],
         "opacity": 0.4, "blend_mode": "Screen",
         "use_roughness": True, "roughness_fill": 0.45,
         "use_bump": True, "bump_strength": 0.3, "bump_distance": 0.02},
    ],
    "Rock Base": [
        # Fill base: dark volcanic rock
        {"name": "Rock Dark", "type": "FILL",
         "fill_color": [0.08, 0.06, 0.04, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.95,
         "use_metallic": True,  "metallic_fill":  0.0},
        # Proc: large-scale color variation with strong bump
        {"name": "Rock Variation", "type": "PROCEDURAL", "proc_type": "MUSGRAVE",
         "proc_scale": 0.5, "proc_detail": 6.0,
         "proc_roughness_proc": 0.6, "proc_lacunarity": 2.2,
         "proc_color1": [0.05, 0.04, 0.02, 1.0],
         "proc_color2": [0.42, 0.32, 0.20, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.9,
         "use_bump": True, "bump_strength": 1.2, "bump_distance": 0.08},
        # Proc: microdetail noise with fine bump
        {"name": "Rock Microdetail", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 2.5, "proc_detail": 8.0,
         "proc_roughness_proc": 0.7, "proc_distortion": 0.8,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.35, 0.28, 0.18, 1.0],
         "opacity": 0.5, "blend_mode": "Overlay",
         "use_bump": True, "bump_strength": 0.6, "bump_distance": 0.02},
    ],
    "Skin Base": [
        # Fill base: mid skin tone
        {"name": "Skin Base", "type": "FILL",
         "fill_color": [0.72, 0.48, 0.36, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.65,
         "use_metallic": True,  "metallic_fill":  0.0},
        # Darker undertone
        {"name": "Skin Undertone", "type": "FILL",
         "fill_color": [0.55, 0.30, 0.20, 1.0],
         "opacity": 0.4, "blend_mode": "Multiply"},
        # Proc: pore microdetail with subtle bump
        {"name": "Skin Pores", "type": "PROCEDURAL", "proc_type": "VORONOI",
         "proc_scale": 4.0, "proc_randomness": 0.8,
         "proc_color1": [0.60, 0.38, 0.28, 1.0],
         "proc_color2": [0.80, 0.58, 0.44, 1.0],
         "opacity": 0.15, "blend_mode": "Overlay",
         "use_roughness": True, "roughness_fill": 0.55,
         "use_bump": True, "bump_strength": 0.2, "bump_distance": 0.005},
    ],
    "Rusted Metal": [
        # Fill base: dark steel
        {"name": "Steel Base", "type": "FILL",
         "fill_color": [0.12, 0.11, 0.10, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.35,
         "use_metallic": True,  "metallic_fill":  0.9},
        # Rust patches: orange/brown noise
        {"name": "Rust Patches", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 0.7, "proc_detail": 8.0,
         "proc_roughness_proc": 0.6, "proc_distortion": 2.0,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.55, 0.18, 0.03, 1.0],
         "opacity": 0.85, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.9,
         "use_bump": True, "bump_strength": 0.8, "bump_distance": 0.04},
        # Surface corrosion: fine detail bump
        {"name": "Corrosion Detail", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 3.0, "proc_detail": 6.0,
         "proc_roughness_proc": 0.8, "proc_distortion": 1.0,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.30, 0.12, 0.04, 0.8],
         "opacity": 0.45, "blend_mode": "Multiply",
         "use_bump": True, "bump_strength": 0.4, "bump_distance": 0.015},
    ],
    "Wood Grain": [
        # Fill base: dark wood
        {"name": "Wood Dark", "type": "FILL",
         "fill_color": [0.25, 0.12, 0.04, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.75},
        # Wave: wood grain rings with bump
        {"name": "Wood Grain", "type": "PROCEDURAL", "proc_type": "WAVE",
         "proc_scale": 0.8, "proc_wave_type": "BANDS",
         "proc_distortion": 2.5, "proc_detail": 4.0,
         "proc_wave_detail_scale": 1.5,
         "proc_color1": [0.18, 0.08, 0.02, 1.0],
         "proc_color2": [0.55, 0.32, 0.12, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.65,
         "use_bump": True, "bump_strength": 0.5, "bump_distance": 0.03},
        # Fine grain noise
        {"name": "Wood Fiber", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 3.5, "proc_detail": 5.0, "proc_roughness_proc": 0.6,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.15, 0.08, 0.02, 0.6],
         "opacity": 0.35, "blend_mode": "Multiply",
         "use_bump": True, "bump_strength": 0.2, "bump_distance": 0.008},
    ],
}


class TLM_OT_ApplyPreset(Operator):
    """Apply a built-in or saved preset layer stack."""
    bl_idname = "tlm.apply_preset"
    bl_label = "Apply Preset"
    bl_options = {'REGISTER', 'UNDO'}

    preset_name: bpy.props.StringProperty(default="Metal Base")
    merge: bpy.props.BoolProperty(
        name="Merge with existing",
        description="Add preset layers on top of current stack",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        mat = _get_material(context)
        _ensure_nodes(mat)
        tlm = mat.tlm

        preset_layers = BUILTIN_PRESETS.get(self.preset_name)
        if not preset_layers:
            # Try user presets directory
            preset_dir = _os.path.join(_os.path.dirname(__file__), "presets")
            preset_file = _os.path.join(preset_dir, f"{self.preset_name}.tlm")
            if _os.path.exists(preset_file):
                try:
                    with open(preset_file, 'r') as f:
                        data = _json.load(f)
                    preset_layers = data.get("layers", [])
                except (ValueError, OSError) as e:
                    self.report({'ERROR'}, f"Invalid preset file: {e}")
                    return {'CANCELLED'}
            else:
                self.report({'ERROR'}, f"Preset '{self.preset_name}' not found")
                return {'CANCELLED'}

        if not self.merge:
            tlm.layers.clear()

        from . import operators as _ops
        for ld in preset_layers:
            layer = tlm.layers.add()
            layer.name       = ld.get("name", "Layer")
            layer.layer_type = ld.get("type", "FILL")
            layer.opacity    = ld.get("opacity", 1.0)
            layer.visible    = ld.get("visible", True)
            layer.group_name = ld.get("group_name", "")
            layer.collapsed  = ld.get("collapsed", False)
            layer.use_clipping_mask = ld.get("use_clipping_mask", False)

            # blend_mode: map UI names to internal enum values
            bm_map = {
                "Normal": "MIX", "MIX": "MIX",
                "Screen": "SCREEN", "SCREEN": "SCREEN",
                "Multiply": "MULTIPLY", "MULTIPLY": "MULTIPLY",
                "Overlay": "OVERLAY", "OVERLAY": "OVERLAY",
                "Add": "ADD", "ADD": "ADD",
                "Subtract": "SUBTRACT", "SUBTRACT": "SUBTRACT",
                "Difference": "DIFFERENCE", "DIFFERENCE": "DIFFERENCE",
                "Darken": "DARKEN", "DARKEN": "DARKEN",
                "Lighten": "LIGHTEN", "LIGHTEN": "LIGHTEN",
                "Color Dodge": "COLOR_DODGE", "COLOR_DODGE": "COLOR_DODGE",
                "Color Burn": "COLOR_BURN", "COLOR_BURN": "COLOR_BURN",
                "Soft Light": "SOFT_LIGHT", "SOFT_LIGHT": "SOFT_LIGHT",
                "Linear Light": "LINEAR_LIGHT", "LINEAR_LIGHT": "LINEAR_LIGHT",
                "Exclusion": "EXCLUSION", "EXCLUSION": "EXCLUSION",
                "Hue": "HUE", "HUE": "HUE",
                "Saturation": "SATURATION", "SATURATION": "SATURATION",
                "Color": "COLOR", "COLOR": "COLOR",
                "Luminosity": "LUMINOSITY", "LUMINOSITY": "LUMINOSITY",
            }
            layer.blend_mode = bm_map.get(ld.get("blend_mode", "MIX"), "MIX")

            if layer.layer_type == "FILL":
                layer.fill_color = ld.get("fill_color", [1,1,1,1])
            elif layer.layer_type == "PROCEDURAL":
                layer.proc_type            = ld.get("proc_type", "NOISE")
                layer.proc_scale           = ld.get("proc_scale", 5.0)
                layer.proc_color1          = ld.get("proc_color1", [0,0,0,1])
                layer.proc_color2          = ld.get("proc_color2", [1,1,1,1])
                layer.proc_detail          = ld.get("proc_detail", 2.0)
                layer.proc_roughness_proc  = ld.get("proc_roughness_proc", 0.5)
                layer.proc_distortion      = ld.get("proc_distortion", 0.0)
                layer.proc_lacunarity      = ld.get("proc_lacunarity", 2.0)
                layer.proc_offset_x        = ld.get("proc_offset_x", 0.0)
                layer.proc_offset_y        = ld.get("proc_offset_y", 0.0)
                layer.proc_offset_z        = ld.get("proc_offset_z", 0.0)
                layer.proc_wave_type       = ld.get("proc_wave_type", "BANDS")
                layer.proc_wave_profile    = ld.get("proc_wave_profile", "SIN")
                layer.proc_wave_detail_scale = ld.get("proc_wave_detail_scale", 1.0)
                layer.proc_gradient_type   = ld.get("proc_gradient_type", "LINEAR")
                layer.proc_checker_scale   = ld.get("proc_checker_scale", 5.0)
                layer.proc_voronoi_feature  = ld.get("proc_voronoi_feature", "F1")
                layer.proc_voronoi_distance = ld.get("proc_voronoi_distance", "EUCLIDEAN")
                layer.proc_randomness       = ld.get("proc_randomness", 1.0)
                layer.proc_marble_distortion = ld.get("proc_marble_distortion", 5.0)
                layer.proc_marble_wave_type  = ld.get("proc_marble_wave_type", "BANDS")
                layer.proc_contrast         = ld.get("proc_contrast", 0.5)
                layer.proc_vector_distortion= ld.get("proc_vector_distortion", 0.0)
                layer.proc_coord_type       = ld.get("proc_coord_type", "GENERATED")
                layer.proc_emission_threshold = ld.get("proc_emission_threshold", 0.0)
                layer.use_proc_color3       = ld.get("use_proc_color3", False)
                if layer.use_proc_color3:
                    layer.proc_color3          = ld.get("proc_color3", [0.5, 0.5, 0.5, 1])
                    layer.proc_color3_position = ld.get("proc_color3_position", 0.5)
            elif layer.layer_type == "ADJUSTMENT":
                layer.adj_type             = ld.get("adj_type", "HUE_SAT")
                layer.adj_hue              = ld.get("adj_hue", 0.5)
                layer.adj_saturation       = ld.get("adj_saturation", 1.0)
                layer.adj_value            = ld.get("adj_value", 1.0)
                layer.adj_brightness       = ld.get("adj_brightness", 0.0)
                layer.adj_contrast         = ld.get("adj_contrast", 0.0)
                layer.adj_in_min           = ld.get("adj_in_min", 0.0)
                layer.adj_in_max           = ld.get("adj_in_max", 1.0)
                layer.adj_levels_gamma     = ld.get("adj_levels_gamma", 1.0)
                layer.adj_out_min          = ld.get("adj_out_min", 0.0)
                layer.adj_out_max          = ld.get("adj_out_max", 1.0)
                layer.adj_curve_contrast   = ld.get("adj_curve_contrast", 0.0)
                layer.adj_curve_brightness = ld.get("adj_curve_brightness", 0.0)
                layer.adj_curve_black_point = ld.get("adj_curve_black_point", 0.0)
                layer.adj_curve_white_point = ld.get("adj_curve_white_point", 1.0)
                layer.adj_lift  = ld.get("adj_lift", [1, 1, 1])
                layer.adj_gamma = ld.get("adj_gamma", [1, 1, 1])
                layer.adj_gain  = ld.get("adj_gain", [1, 1, 1])

            # Common properties for non-GROUP, non-ADJUSTMENT layers
            if layer.layer_type not in ("GROUP", "ADJUSTMENT"):
                layer.use_fresnel_mask  = ld.get("use_fresnel_mask", False)
                layer.fresnel_ior       = ld.get("fresnel_ior", 1.45)
                layer.fresnel_strength  = ld.get("fresnel_strength", 1.0)
                layer.use_mask          = ld.get("use_mask", False)
                layer.mask_image_name   = ld.get("mask_image_name", "")
                layer.use_triplanar     = ld.get("use_triplanar", False)
                layer.triplanar_scale   = ld.get("triplanar_scale", 1.0)
                layer.triplanar_sharpness = ld.get("triplanar_sharpness", 1.0)
                # PBR channels
                layer.use_roughness        = ld.get("use_roughness", False)
                layer.roughness_fill       = ld.get("roughness_fill", 0.5)
                layer.roughness_image_name = ld.get("roughness_image_name", "")
                layer.use_metallic         = ld.get("use_metallic", False)
                layer.metallic_fill        = ld.get("metallic_fill", 0.0)
                layer.metallic_image_name  = ld.get("metallic_image_name", "")
                layer.use_bump             = ld.get("use_bump", False)
                layer.bump_strength        = ld.get("bump_strength", 0.5)
                layer.bump_distance        = ld.get("bump_distance", 0.05)
                layer.use_normal           = ld.get("use_normal", False)
                layer.normal_image_name    = ld.get("normal_image_name", "")
                layer.use_emission         = ld.get("use_emission", False)
                layer.emission_image_name  = ld.get("emission_image_name", "")
                if layer.use_emission:
                    layer.emission_color    = ld.get("emission_color", [1,1,1,1])
                    layer.emission_strength = ld.get("emission_strength", 1.0)
                layer.use_transmission        = ld.get("use_transmission", False)
                layer.transmission_fill       = ld.get("transmission_fill", 0.0)
                layer.transmission_image_name = ld.get("transmission_image_name", "")

        tlm.active_layer_index = max(0, len(tlm.layers) - 1)
        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Applied preset '{self.preset_name}'")
        return {'FINISHED'}


class TLM_OT_SavePreset(Operator):
    """Save the current layer stack as a named preset."""
    bl_idname = "tlm.save_preset"
    bl_label = "Save as Preset"
    bl_options = {'REGISTER'}

    preset_name: bpy.props.StringProperty(name="Preset Name", default="My Preset")

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and len(mat.tlm.layers) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm

        preset_dir = _os.path.join(_os.path.dirname(__file__), "presets")
        _os.makedirs(preset_dir, exist_ok=True)

        # Serialize layers (without image pixel data — presets are lightweight)
        layers_data = []
        for layer in tlm.layers:
            d = {
                "name": layer.name, "type": layer.layer_type,
                "opacity": round(layer.opacity, 4), "blend_mode": layer.blend_mode,
                "visible": layer.visible, "group_name": layer.group_name,
                "collapsed": layer.collapsed,
                "use_clipping_mask": layer.use_clipping_mask,
            }
            if layer.layer_type == "FILL":
                d["fill_color"] = list(layer.fill_color)
            elif layer.layer_type == "PROCEDURAL":
                d.update({
                    "proc_type": layer.proc_type, "proc_scale": layer.proc_scale,
                    "proc_color1": list(layer.proc_color1), "proc_color2": list(layer.proc_color2),
                    "proc_detail": layer.proc_detail, "proc_distortion": layer.proc_distortion,
                    "proc_roughness_proc": layer.proc_roughness_proc,
                    "proc_lacunarity": layer.proc_lacunarity,
                    "proc_offset_x": layer.proc_offset_x,
                    "proc_offset_y": layer.proc_offset_y,
                    "proc_offset_z": layer.proc_offset_z,
                    "proc_wave_type": layer.proc_wave_type,
                    "proc_wave_profile": layer.proc_wave_profile,
                    "proc_wave_detail_scale": layer.proc_wave_detail_scale,
                    "proc_gradient_type": layer.proc_gradient_type,
                    "proc_checker_scale": layer.proc_checker_scale,
                    "proc_voronoi_feature": layer.proc_voronoi_feature,
                    "proc_voronoi_distance": layer.proc_voronoi_distance,
                    "proc_randomness": layer.proc_randomness,
                    "proc_marble_distortion": layer.proc_marble_distortion,
                    "proc_marble_wave_type": layer.proc_marble_wave_type,
                    "proc_contrast": layer.proc_contrast,
                    "proc_vector_distortion": layer.proc_vector_distortion,
                    "proc_coord_type": layer.proc_coord_type,
                    "proc_emission_threshold": getattr(layer, 'proc_emission_threshold', 0.0),
                    "use_proc_color3": getattr(layer, 'use_proc_color3', False),
                })
                if getattr(layer, 'use_proc_color3', False):
                    d["proc_color3"] = list(layer.proc_color3)
                    d["proc_color3_position"] = layer.proc_color3_position
            elif layer.layer_type == "ADJUSTMENT":
                d.update({
                    "adj_type": layer.adj_type,
                    "adj_hue": layer.adj_hue,
                    "adj_saturation": layer.adj_saturation,
                    "adj_value": layer.adj_value,
                    "adj_brightness": layer.adj_brightness,
                    "adj_contrast": layer.adj_contrast,
                    "adj_in_min": layer.adj_in_min,
                    "adj_in_max": layer.adj_in_max,
                    "adj_levels_gamma": layer.adj_levels_gamma,
                    "adj_out_min": layer.adj_out_min,
                    "adj_out_max": layer.adj_out_max,
                    "adj_curve_contrast": layer.adj_curve_contrast,
                    "adj_curve_brightness": layer.adj_curve_brightness,
                    "adj_curve_black_point": layer.adj_curve_black_point,
                    "adj_curve_white_point": layer.adj_curve_white_point,
                    "adj_lift": list(layer.adj_lift),
                    "adj_gamma": list(layer.adj_gamma),
                    "adj_gain": list(layer.adj_gain),
                })
            # Common properties for non-GROUP, non-ADJUSTMENT layers
            if layer.layer_type not in ("GROUP", "ADJUSTMENT"):
                d["use_fresnel_mask"]  = getattr(layer, 'use_fresnel_mask', False)
                d["fresnel_ior"]       = getattr(layer, 'fresnel_ior', 1.45)
                d["fresnel_strength"]  = getattr(layer, 'fresnel_strength', 1.0)
                d["use_mask"]          = layer.use_mask
                d["mask_image_name"]   = layer.mask_image_name
                d["use_triplanar"]     = getattr(layer, 'use_triplanar', False)
                d["triplanar_scale"]   = getattr(layer, 'triplanar_scale', 1.0)
                d["triplanar_sharpness"] = getattr(layer, 'triplanar_sharpness', 1.0)
                # PBR channels
                d["use_roughness"]        = layer.use_roughness
                d["roughness_fill"]       = layer.roughness_fill
                d["roughness_image_name"] = getattr(layer, 'roughness_image_name', "")
                d["use_metallic"]         = layer.use_metallic
                d["metallic_fill"]        = layer.metallic_fill
                d["metallic_image_name"]  = getattr(layer, 'metallic_image_name', "")
                d["use_bump"]             = layer.use_bump
                d["bump_strength"]        = layer.bump_strength
                d["bump_distance"]        = layer.bump_distance
                d["use_normal"]           = getattr(layer, 'use_normal', False)
                d["normal_image_name"]    = getattr(layer, 'normal_image_name', "")
                d["use_emission"]         = getattr(layer, 'use_emission', False)
                d["emission_image_name"]  = getattr(layer, 'emission_image_name', "")
                if getattr(layer, 'use_emission', False):
                    d["emission_color"]    = list(layer.emission_color)
                    d["emission_strength"] = layer.emission_strength
                d["use_transmission"]        = getattr(layer, 'use_transmission', False)
                d["transmission_fill"]       = getattr(layer, 'transmission_fill', 0.0)
                d["transmission_image_name"] = getattr(layer, 'transmission_image_name', "")
            layers_data.append(d)

        data = {"preset_name": self.preset_name, "layers": layers_data}
        filepath = _os.path.join(preset_dir, f"{self.preset_name}.tlm")
        try:
            with open(filepath, 'w') as f:
                _json.dump(data, f, indent=2)
        except OSError as e:
            self.report({'ERROR'}, f"Failed to save preset: {e}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Saved preset '{self.preset_name}'")
        return {'FINISHED'}


class TLM_OT_DeletePreset(Operator):
    """Delete a user-saved preset file."""
    bl_idname = "tlm.delete_preset"
    bl_label = "Delete Preset"
    bl_options = {'REGISTER'}

    preset_name: bpy.props.StringProperty(default="")

    def execute(self, context):
        preset_dir = _os.path.join(_os.path.dirname(__file__), "presets")
        filepath = _os.path.join(preset_dir, f"{self.preset_name}.tlm")
        if _os.path.exists(filepath):
            _os.remove(filepath)
            self.report({'INFO'}, f"Deleted preset '{self.preset_name}'")
        else:
            self.report({'WARNING'}, f"Preset file not found: {self.preset_name}")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)


# ─── Layer da Clipboard ───────────────────────────────────────────────────────

class TLM_OT_LayerFromClipboard(Operator):
    """Create a new layer from an image in the clipboard."""
    bl_idname = "tlm.layer_from_clipboard"
    bl_label = "Layer from Clipboard"
    bl_options = {'REGISTER', 'UNDO'}

    channel: bpy.props.EnumProperty(
        name="Channel",
        items=[
            ('base_color',    "Base Color",    ""),
            ('roughness',     "Roughness",     ""),
            ('metallic',      "Metallic",      ""),
            ('normal',        "Normal Map",    ""),
            ('emission',      "Emission",      ""),
            ('transmission',  "Transmission",  ""),
        ],
        default='base_color',
    )

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        mat = _get_material(context)
        _ensure_nodes(mat)
        tlm = mat.tlm

        # Try to get image from clipboard via bpy.ops.image.new + paste
        # Blender 4.x+ supports bpy.ops.image.clipboard_paste
        try:
            # Create a temp image and paste clipboard into it
            bpy.ops.image.new(name="TLM_Clipboard_Temp", width=1024, height=1024)
            temp_img = bpy.data.images.get("TLM_Clipboard_Temp")

            # Try clipboard paste (Blender 4.x)
            if hasattr(bpy.ops.image, 'clipboard_paste'):
                # Set the temp image as active in the image editor
                img_area = None
                for area in context.screen.areas:
                    if area.type == 'IMAGE_EDITOR':
                        img_area = area
                        break
                if img_area is None:
                    if temp_img:
                        bpy.data.images.remove(temp_img)
                    self.report({'ERROR'}, "Open an Image Editor area first")
                    return {'CANCELLED'}
                img_area.spaces.active.image = temp_img
                bpy.ops.image.clipboard_paste(area=img_area)
                pasted_img = img_area.spaces.active.image
            else:
                # Fallback: just use the blank image and inform user
                pasted_img = temp_img
                self.report({'WARNING'}, "Clipboard paste richiede Blender 4.x+. Creato layer vuoto.")

            pasted_img.name = f"Clipboard_{len(tlm.layers)+1}"

            # Create layer
            layer = tlm.layers.add()
            layer.layer_type = "PAINT"
            layer.name = pasted_img.name
            layer.opacity = 1.0
            layer.blend_mode = "MIX"
            layer.visible = True

            if self.channel == 'base_color':
                layer.image_name = pasted_img.name
            else:
                # CHANNEL_INFO is defined in this same module — no import needed
                info = CHANNEL_INFO.get(self.channel)
                if info:
                    flag_attr, img_attr, _ = info
                    setattr(layer, img_attr, pasted_img.name)
                    setattr(layer, flag_attr, True)

            # Append at end (base convention)
            new_idx = len(tlm.layers) - 1
            tlm.active_layer_index = new_idx

            if tlm.auto_composite:
                compositing.rebuild_node_tree(mat)

            self.report({'INFO'}, f"Layer creato da clipboard: '{pasted_img.name}'")
            return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"Clipboard non disponibile: {e}")
            return {'CANCELLED'}


# ─── Symmetry Paint Helper ────────────────────────────────────────────────────
# NOTE: Removed — Blender 5.0 texture paint symmetry not reliably controllable
# via Python API. Users should use Blender's built-in N → Tool → Symmetry panel.


# ─── Registration ─────────────────────────────────────────────────────────────

classes = [
    TLM_OT_AddPaintLayer,
    TLM_OT_AddFillLayer,
    TLM_OT_AddAdjustmentLayer,
    TLM_OT_AddProceduralLayer,
    TLM_OT_AddGroup,
    TLM_OT_MoveToGroup,
    TLM_OT_RemoveFromGroup,
    TLM_OT_ToggleGroupCollapse,
    TLM_OT_RemoveLayer,
    TLM_OT_MoveLayer,
    TLM_OT_DuplicateLayer,
    TLM_OT_SetActivePaintLayer,
    TLM_OT_ToggleLayerVisibility,
    TLM_OT_RebuildComposite,
    TLM_OT_FlattenLayers,
    TLM_OT_AddLayerMask,
    TLM_OT_RefreshThumbnails,
    TLM_OT_ExportJSON,
    TLM_OT_ImportJSON,
    TLM_OT_AddChannelImage,
    TLM_OT_RemoveChannelImage,
    TLM_OT_ImportTextureAsLayer,
    TLM_OT_BakePBR,
    TLM_OT_AddSmartMask,
    TLM_OT_ApplyPreset,
    TLM_OT_SavePreset,
    TLM_OT_DeletePreset,
    TLM_OT_LayerFromClipboard,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
