import bpy
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty, EnumProperty, BoolProperty

from ._common import _get_material, _ensure_nodes, _add_layer_common, compositing, previews


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
                    parent_group = ""
                else:
                    parent_group = active.name
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

        if layer.layer_type == "GROUP":
            for other in tlm.layers:
                if other.group_name == layer_name:
                    other.group_name = ""

        tlm.layers.remove(idx)
        tlm.active_layer_index = max(0, min(idx, len(tlm.layers) - 1))

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Removed layer '{layer_name}' (image kept in bpy.data)")
        return {'FINISHED'}


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

        if src.image:
            orig = src.image
            dup = orig.copy()
            dup.name = new_layer.name
            dup.use_fake_user = True
            new_layer.image_name = dup.name
            previews.invalidate(dup.name)

        new_idx = len(tlm.layers) - 1
        target = tlm.active_layer_index + 1
        while new_idx > target:
            tlm.layers.move(new_idx, new_idx - 1)
            new_idx -= 1
        tlm.active_layer_index = new_idx

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        return {'FINISHED'}


class TLM_OT_MoveLayerToEnd(Operator):
    """Move the active layer to the top or bottom of the stack."""
    bl_idname = "tlm.move_layer_to_end"
    bl_label = "Move Layer to End"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        items=[("TOP", "Top", ""), ("BOTTOM", "Bottom", "")],
        default="TOP",
    )

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and len(mat.tlm.layers) > 1

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        idx = tlm.active_layer_index

        if self.direction == "TOP":
            while idx > 0:
                tlm.layers.move(idx, idx - 1)
                idx -= 1
            tlm.active_layer_index = 0
        else:
            last = len(tlm.layers) - 1
            while idx < last:
                tlm.layers.move(idx, idx + 1)
                idx += 1
            tlm.active_layer_index = last

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        return {'FINISHED'}


classes = [
    TLM_OT_AddPaintLayer,
    TLM_OT_AddFillLayer,
    TLM_OT_AddAdjustmentLayer,
    TLM_OT_AddProceduralLayer,
    TLM_OT_RemoveLayer,
    TLM_OT_MoveLayer,
    TLM_OT_DuplicateLayer,
    TLM_OT_MoveLayerToEnd,
]
