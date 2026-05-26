import bpy
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty, EnumProperty, BoolProperty

from ._common import _get_material, _can_edit_tlm_stack, _add_layer_common, compositing, previews


class TLM_OT_AddPaintLayer(Operator):
    """Add a new paint layer with a blank transparent image above the active layer."""
    bl_idname = "tlm.add_paint_layer"
    bl_label = "Add Paint Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _can_edit_tlm_stack(context)

    def execute(self, context):
        name = _add_layer_common(context, "PAINT")
        if name is None:
            self.report({'ERROR'}, "No active material")
            return {'CANCELLED'}

        # Convenience: take the user straight to a usable paint workflow.
        # Three side effects:
        #   1. Mark the new layer's image as the active paint canvas
        #      (selects the right tex node so brush strokes hit it).
        #   2. Switch the object to TEXTURE_PAINT mode.
        #   3. Switch the 3D viewport to MATERIAL preview shading
        #      (RENDERED doesn't update live during paint strokes —
        #       Cycles re-renders only after the stroke finishes,
        #       Eevee/MaterialPreview updates per-stroke).
        # Each step is wrapped in try/except — these are nice-to-have,
        # the paint layer was already added successfully and we don't
        # want to roll back on a context-restricted failure.
        obj = context.active_object
        was_in_paint = bool(obj and obj.mode == 'TEXTURE_PAINT')

        # If we're already in TEXTURE_PAINT, drop back to OBJECT first.
        # Selecting a new active tex node while still in paint mode
        # doesn't refresh the brush canvas — the user keeps painting on
        # the previous layer's image. The exit/re-enter cycle below
        # forces Blender to re-resolve the active image.
        if was_in_paint and obj and obj.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass

        mat = _get_material(context)
        if mat:
            new_idx = mat.tlm.active_layer_index
            try:
                bpy.ops.tlm.set_active_paint_layer(layer_index=new_idx)
            except Exception:
                pass

        if obj and obj.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
            except Exception:
                pass  # not always allowed (e.g. no UV map, or restricted ctx)

        # Find the 3D viewport the user is working in and switch shading.
        # We pick the first VIEW_3D area that's not in SOLID — if they're
        # already in MATERIAL or RENDERED we still nudge to MATERIAL since
        # painting is sluggish in RENDERED.
        screen = getattr(context, "screen", None)
        if screen:
            for area in screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        try:
                            if space.shading.type != 'MATERIAL':
                                space.shading.type = 'MATERIAL'
                        except Exception:
                            pass
                        break
                break

        self.report({'INFO'}, f"Added paint layer '{name}'")
        return {'FINISHED'}


class TLM_OT_AddFillLayer(Operator):
    """Add a solid color fill layer above the active layer."""
    bl_idname = "tlm.add_fill_layer"
    bl_label = "Add Fill Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _can_edit_tlm_stack(context)

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
        return _can_edit_tlm_stack(context)

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
        return _can_edit_tlm_stack(context)

    def execute(self, context):
        name = _add_layer_common(context, "PROCEDURAL")
        if name is None:
            self.report({'ERROR'}, "No active material")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Added procedural layer '{name}'")
        return {'FINISHED'}


class TLM_OT_AddReferenceLayer(Operator):
    """Add a Reference layer that reuses the pattern of another layer.

    The reference layer fetches the color/alpha output of the chosen source layer,
    then applies its OWN blend mode, opacity, mask, and per-channel overrides.
    Enables 'one pattern, many behaviors' workflows (e.g. same Voronoi drives
    both base color and roughness with different blends)."""
    bl_idname = "tlm.add_reference_layer"
    bl_label = "Add Reference Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        # Need at least one non-REFERENCE layer to reference
        if mat is None or mat.tlm.shader_editable:
            return False
        return any(l.layer_type in {"PAINT", "FILL", "PROCEDURAL"} for l in mat.tlm.layers)

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm

        # Try to auto-select a sensible default reference target: the active layer
        # if it's a valid pattern layer, else the first non-REFERENCE layer.
        default_ref = ""
        active = tlm.active_layer
        if active and active.layer_type in {"PAINT", "FILL", "PROCEDURAL"}:
            default_ref = active.name
        else:
            for l in tlm.layers:
                if l.layer_type in {"PAINT", "FILL", "PROCEDURAL"}:
                    default_ref = l.name
                    break

        name = _add_layer_common(context, "REFERENCE")
        if name is None:
            self.report({'ERROR'}, "No active material")
            return {'CANCELLED'}

        # Set the reference after the layer exists
        new_layer = tlm.layers[tlm.active_layer_index]
        new_layer.reference_layer_name = default_ref

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Added reference layer '{name}' → '{default_ref or '<unset>'}'")
        return {'FINISHED'}


class TLM_OT_AddProcColorStop(Operator):
    """Add a new colour stop to the active procedural layer's ColorRamp.

    The new stop is inserted at the midpoint between the existing
    Color1 and Color2 positions, so the user sees an immediate change
    without having to set position manually. Stops can later be dragged
    via the position slider — Blender's ColorRamp sorts them internally.
    """
    bl_idname = "tlm.add_proc_color_stop"
    bl_label = "Add Color Stop"
    bl_description = "Add an extra ColorRamp stop to the active procedural layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        if mat is None or mat.tlm.shader_editable:
            return False
        active = mat.tlm.active_layer
        return active is not None and active.layer_type == 'PROCEDURAL'

    def execute(self, context):
        mat = _get_material(context)
        layer = mat.tlm.active_layer
        if layer is None or layer.layer_type != 'PROCEDURAL':
            self.report({'ERROR'}, "Active layer is not a procedural layer")
            return {'CANCELLED'}

        # Insert at the midpoint of the current Color1 / Color2 positions
        # so the user sees the stop fall between them. Default colour is
        # the average of color1 and color2 — visually a "smooth midstop".
        if getattr(layer, 'proc_use_manual_stops', False):
            p1 = layer.proc_color1_position
            p2 = layer.proc_color2_position
        else:
            from .. import compositing as _comp
            contrast = layer.proc_contrast
            center = layer.proc_ramp_center
            p1, p2 = _comp._ramp_stops(contrast, center)
        new_pos = max(0.001, min(0.999, (p1 + p2) * 0.5))
        c1 = layer.proc_color1
        c2 = layer.proc_color2
        avg_color = (
            (c1[0] + c2[0]) * 0.5,
            (c1[1] + c2[1]) * 0.5,
            (c1[2] + c2[2]) * 0.5,
            1.0,
        )

        stop = layer.proc_extra_color_stops.add()
        stop.position = new_pos
        stop.color = avg_color
        layer.proc_active_color_stop_index = len(layer.proc_extra_color_stops) - 1

        # Element count changed → need full rebuild (hot_proc_color returns
        # False on element-count mismatch, but doing it directly is cleaner).
        if mat.tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Added color stop at {new_pos:.2f}")
        return {'FINISHED'}


class TLM_OT_RemoveProcColorStop(Operator):
    """Remove the active extra color stop from the active procedural layer."""
    bl_idname = "tlm.remove_proc_color_stop"
    bl_label = "Remove Color Stop"
    bl_description = "Remove the extra ColorRamp stop at the given index"
    bl_options = {'REGISTER', 'UNDO'}

    # When invoked from the per-row X button the panel sets this index
    # directly. When invoked with the default -1 we fall back to the
    # active_color_stop_index property on the layer.
    index: IntProperty(
        name="Stop Index",
        description="Index of the stop to remove (-1 = use the layer's active index)",
        default=-1,
    )

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        if mat is None or mat.tlm.shader_editable:
            return False
        active = mat.tlm.active_layer
        if active is None or active.layer_type != 'PROCEDURAL':
            return False
        return len(active.proc_extra_color_stops) > 0

    def execute(self, context):
        mat = _get_material(context)
        layer = mat.tlm.active_layer
        if layer is None or layer.layer_type != 'PROCEDURAL':
            return {'CANCELLED'}

        idx = self.index if self.index >= 0 else layer.proc_active_color_stop_index
        if idx < 0 or idx >= len(layer.proc_extra_color_stops):
            self.report({'WARNING'}, "Invalid stop index")
            return {'CANCELLED'}

        layer.proc_extra_color_stops.remove(idx)
        # Clamp the active index to a valid value after removal
        new_count = len(layer.proc_extra_color_stops)
        if new_count > 0:
            layer.proc_active_color_stop_index = min(idx, new_count - 1)
        else:
            layer.proc_active_color_stop_index = 0

        if mat.tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        return {'FINISHED'}


class TLM_OT_RemoveLayer(Operator):
    """Remove the active layer (image datablock is kept in bpy.data)."""
    bl_idname = "tlm.remove_layer"
    bl_label = "Remove Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and not mat.tlm.shader_editable and len(mat.tlm.layers) > 0

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        idx = tlm.active_layer_index

        if idx < 0 or idx >= len(tlm.layers):
            self.report({'WARNING'}, "No layer selected")
            return {'CANCELLED'}

        layer = tlm.layers[idx]
        layer_name = layer.name

        if layer.locked:
            self.report({'WARNING'}, f"Layer '{layer_name}' is locked. Unlock it to remove.")
            return {'CANCELLED'}

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
        return mat is not None and not mat.tlm.shader_editable and len(mat.tlm.layers) > 1

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        idx = tlm.active_layer_index

        if 0 <= idx < len(tlm.layers) and tlm.layers[idx].locked:
            self.report(
                {'WARNING'},
                f"Layer '{tlm.layers[idx].name}' is locked. Unlock it to move.",
            )
            return {'CANCELLED'}

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
        return mat is not None and not mat.tlm.shader_editable and mat.tlm.active_layer is not None

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        src = tlm.active_layer
        if src is None:
            return {'CANCELLED'}

        new_layer = tlm.layers.add()

        # Copy ALL registered properties automatically.
        # This prevents missed properties when new ones are added.
        skip = {'name', 'image_name'}  # handled separately below
        for prop in src.bl_rna.properties:
            if prop.identifier in ('rna_type',) or prop.identifier in skip:
                continue
            if prop.is_readonly:
                continue
            try:
                val = getattr(src, prop.identifier)
                # FloatVectorProperty / color arrays need slice copy
                if hasattr(val, '__len__') and not isinstance(val, str):
                    setattr(new_layer, prop.identifier, val[:])
                else:
                    setattr(new_layer, prop.identifier, val)
            except (AttributeError, TypeError):
                pass

        new_layer.name = compositing.unique_layer_name(
            tlm.layers, src.name + " Copy", current=new_layer
        )

        if src.image:
            orig = src.image
            dup = orig.copy()
            dup.name = new_layer.name
            dup.use_fake_user = True
            new_layer.image_name = dup.name
            previews.invalidate(dup.name)

        new_idx = len(tlm.layers) - 1
        target = tlm.active_layer_index
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
        return mat is not None and not mat.tlm.shader_editable and len(mat.tlm.layers) > 1

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        idx = tlm.active_layer_index

        if 0 <= idx < len(tlm.layers) and tlm.layers[idx].locked:
            self.report(
                {'WARNING'},
                f"Layer '{tlm.layers[idx].name}' is locked. Unlock it to move.",
            )
            return {'CANCELLED'}

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
    TLM_OT_AddReferenceLayer,
    TLM_OT_AddProcColorStop,
    TLM_OT_RemoveProcColorStop,
    TLM_OT_RemoveLayer,
    TLM_OT_MoveLayer,
    TLM_OT_DuplicateLayer,
    TLM_OT_MoveLayerToEnd,
]
