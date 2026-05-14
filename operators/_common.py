"""
Shared helpers used across all operator submodules.
"""

import bpy
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty, EnumProperty, BoolProperty, FloatProperty, FloatVectorProperty
from .. import compositing, previews


def _get_material(context):
    """Return the active material, or None."""
    obj = context.active_object
    if obj and obj.active_material:
        return obj.active_material
    return None


def _is_shader_editable_material(mat):
    """True when this material has been handed over to manual Shader Editor edits."""
    return bool(mat and getattr(mat.tlm, "shader_editable", False))


def _can_edit_tlm_stack(context):
    """Return True when TLM layer-stack operators may mutate the material."""
    mat = _get_material(context)
    return mat is not None and not _is_shader_editable_material(mat)


# ─── Blend mode normalisation (legacy import compat) ─────────────────────────
#
# Older .tlm files (TLM ≤ v0.3) saved blend modes as UI-style title-case
# strings ("Screen", "Multiply", "Color Dodge"). The current schema is
# all-caps enum identifiers ("SCREEN", "MULTIPLY", "COLOR_DODGE").
#
# operators/presets.py had this mapping inline; operators/io.py didn't,
# so the same legacy .tlm imported via tlm.import_json silently fell
# back to "MIX" (Blender's EnumProperty refuses invalid enum values).
# Centralise here so both paths apply the same migration.
_LEGACY_BLEND_MODE_MAP = {
    "Normal": "MIX",          "MIX": "MIX",
    "Screen": "SCREEN",       "SCREEN": "SCREEN",
    "Multiply": "MULTIPLY",   "MULTIPLY": "MULTIPLY",
    "Overlay": "OVERLAY",     "OVERLAY": "OVERLAY",
    "Add": "ADD",             "ADD": "ADD",
    "Subtract": "SUBTRACT",   "SUBTRACT": "SUBTRACT",
    "Difference": "DIFFERENCE", "DIFFERENCE": "DIFFERENCE",
    "Divide": "DIVIDE",       "DIVIDE": "DIVIDE",
    "Darken": "DARKEN",       "DARKEN": "DARKEN",
    "Lighten": "LIGHTEN",     "LIGHTEN": "LIGHTEN",
    "Color Dodge": "COLOR_DODGE", "COLOR_DODGE": "COLOR_DODGE",
    "Color Burn": "COLOR_BURN",   "COLOR_BURN": "COLOR_BURN",
    "Soft Light": "SOFT_LIGHT",   "SOFT_LIGHT": "SOFT_LIGHT",
    "Linear Light": "LINEAR_LIGHT", "LINEAR_LIGHT": "LINEAR_LIGHT",
    "Exclusion": "EXCLUSION", "EXCLUSION": "EXCLUSION",
    "Hue": "HUE",             "HUE": "HUE",
    "Saturation": "SATURATION", "SATURATION": "SATURATION",
    "Color": "COLOR",         "COLOR": "COLOR",
    "Luminosity": "LUMINOSITY", "LUMINOSITY": "LUMINOSITY",
}


def _normalize_blend_mode(value, default="MIX"):
    """Coerce a serialised blend_mode value into the current enum domain.

    Accepts both the legacy UI-style title-case names and the current
    all-caps identifiers. Unknown values fall back to ``default``
    (which is also what Blender's EnumProperty would do silently — we
    just make the migration explicit and consistent across the two
    importers (io.py and presets.py)).
    """
    if not value:
        return default
    return _LEGACY_BLEND_MODE_MAP.get(value, default)


# ─── Bake safety ──────────────────────────────────────────────────────────────

def _bake_preflight(context, material=None):
    """Validate pre-conditions for any bake operation.

    Returns (ok: bool, error_message: str).
    Caller should report the error and cancel if not ok.
    """
    obj = context.active_object
    if not obj:
        return False, "No active object. Select a mesh first."
    if obj.type != 'MESH':
        return False, f"Active object '{obj.name}' is not a mesh."
    mesh = obj.data
    if not mesh.uv_layers or len(mesh.uv_layers) == 0:
        return False, f"Mesh '{obj.name}' has no UV map. Unwrap it first (U → Smart UV Project)."
    if len(mesh.polygons) == 0:
        return False, f"Mesh '{obj.name}' has no faces to bake onto."
    if not obj.active_material:
        return False, f"Object '{obj.name}' has no active material to bake."
    if material is not None and obj.active_material != material:
        return False, "Active material changed before bake. Select the material you want to bake."

    # Blender bakes every face on the object. If other material slots are used,
    # those slots also need an active bake image node, otherwise the operation
    # can fail or produce partial maps. Keep the add-on path explicit: bake one
    # TLM material at a time on geometry assigned to that active material.
    active_index = obj.active_material_index
    used_indices = {poly.material_index for poly in mesh.polygons}
    if active_index not in used_indices:
        return False, (
            f"Active material '{obj.active_material.name}' is not assigned to any face on "
            f"'{obj.name}'. Assign it to the mesh before baking."
        )
    other_indices = sorted(idx for idx in used_indices if idx != active_index)
    if other_indices:
        return False, (
            f"Object '{obj.name}' uses multiple material slots on its faces. "
            "Bake/export currently works on one active material at a time: isolate those faces "
            "or assign the active material to the whole bake object first."
        )
    return True, ""


class _BakeGuard:
    """Context manager that makes a bake operation safe for the user.

    On entry:
      - Forces render engine to CYCLES (required by bpy.ops.object.bake).
      - Switches the bake object to Object Mode and selects only that object.
      - Disables selected-to-active baking while the add-on bakes its images.
      - Snapshots node-tree selection + active node.

    On exit:
      - Restores render engine.
      - Restores bake settings, active object, object mode, and object selection.
      - Restores node selection + active node.
      - Removes every image still sitting in the `register_orphan()` queue,
        i.e. bake targets that were created but never `commit()`-ed.

    Per-bake semantics (supports multiple bakes in one guard):
        register_orphan(img)  # before the bake, in case it fails
        bpy.ops.object.bake(...)
        commit()              # bake succeeded — clears pending orphans

    If the bake fails (either exception or early return without commit), the
    __exit__ handler disposes of whatever is still queued.

    Usage:
        with _BakeGuard(context, node_tree) as guard:
            img = bpy.data.images.new(...)
            guard.register_orphan(img)
            bpy.ops.object.bake(type='EMIT', save_mode='INTERNAL')
            guard.commit()  # bake succeeded — keep image

            img2 = bpy.data.images.new(...)
            guard.register_orphan(img2)
            bpy.ops.object.bake(type='EMIT', save_mode='INTERNAL')
            # exception here → img2 gets cleaned on __exit__, img is kept
            guard.commit()
    """

    _BAKE_SETTING_PROPS = (
        "target",
        "save_mode",
        "use_selected_to_active",
        "use_clear",
    )

    def __init__(self, context, node_tree, obj=None):
        self.context = context
        self.node_tree = node_tree
        self.obj = obj or context.active_object
        self._engine_backup = None
        self._bake_settings_backup = {}
        self._active_object_backup = None
        self._selected_objects_backup = []
        self._object_mode_backup = None
        self._active_material_index_backup = None
        self._active_node_backup = None
        self._node_selection_backup = {}
        self._orphans = []

    def __enter__(self):
        scene = self.context.scene
        view_layer = self.context.view_layer
        obj = self.obj

        self._active_object_backup = view_layer.objects.active
        self._selected_objects_backup = list(self.context.selected_objects)
        if obj is not None and obj.name in bpy.data.objects:
            self._object_mode_backup = getattr(obj, "mode", None)
            self._active_material_index_backup = getattr(obj, "active_material_index", None)
            try:
                view_layer.objects.active = obj
            except Exception:
                pass
            try:
                if obj.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
            try:
                for ob in self.context.scene.objects:
                    ob.select_set(False)
                obj.select_set(True)
                view_layer.objects.active = obj
            except Exception:
                pass
        self._engine_backup = scene.render.engine
        if self._engine_backup != 'CYCLES':
            try:
                scene.render.engine = 'CYCLES'
            except Exception:
                # Cycles unavailable — extremely rare in Blender 5.0, let caller fail naturally
                pass

        bake_settings = getattr(scene.render, "bake", None)
        if bake_settings is not None:
            for prop in self._BAKE_SETTING_PROPS:
                if hasattr(bake_settings, prop):
                    try:
                        self._bake_settings_backup[prop] = getattr(bake_settings, prop)
                    except Exception:
                        pass
            try:
                bake_settings.use_selected_to_active = False
            except Exception:
                pass
            try:
                bake_settings.target = 'IMAGE_TEXTURES'
            except Exception:
                pass
            try:
                bake_settings.save_mode = 'INTERNAL'
            except Exception:
                pass
            try:
                bake_settings.use_clear = True
            except Exception:
                pass

        if self.node_tree:
            self._active_node_backup = self.node_tree.nodes.active
            self._node_selection_backup = {n: n.select for n in self.node_tree.nodes}
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Clean up any orphan images still queued (un-committed)
        for img in self._orphans:
            if img is None:
                continue
            try:
                if img.name in bpy.data.images:
                    bpy.data.images.remove(img)
            except Exception:
                pass
        self._orphans.clear()

        # Restore node selection + active
        if self.node_tree:
            for node, sel in self._node_selection_backup.items():
                try:
                    node.select = sel
                except Exception:
                    pass  # node may have been removed during bake
            try:
                self.node_tree.nodes.active = self._active_node_backup
            except Exception:
                pass

        # Restore bake settings
        bake_settings = getattr(self.context.scene.render, "bake", None)
        if bake_settings is not None:
            for prop, value in self._bake_settings_backup.items():
                try:
                    setattr(bake_settings, prop, value)
                except Exception:
                    pass

        # Restore render engine
        try:
            self.context.scene.render.engine = self._engine_backup
        except Exception:
            pass

        # Restore object selection / active material / active object / mode.
        obj = self.obj
        view_layer = self.context.view_layer
        if obj is not None and obj.name in bpy.data.objects:
            try:
                if getattr(obj, "mode", None) != 'OBJECT':
                    view_layer.objects.active = obj
                    bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
            if self._active_material_index_backup is not None:
                try:
                    obj.active_material_index = self._active_material_index_backup
                except Exception:
                    pass
        try:
            for ob in self.context.scene.objects:
                ob.select_set(False)
            for ob in self._selected_objects_backup:
                if ob is not None and ob.name in bpy.data.objects:
                    ob.select_set(True)
        except Exception:
            pass
        try:
            if (self._active_object_backup is not None
                    and self._active_object_backup.name in bpy.data.objects):
                view_layer.objects.active = self._active_object_backup
        except Exception:
            pass
        if (obj is not None and obj.name in bpy.data.objects
                and self._object_mode_backup
                and self._object_mode_backup != 'OBJECT'):
            try:
                view_layer.objects.active = obj
                obj.select_set(True)
                bpy.ops.object.mode_set(mode=self._object_mode_backup)
            except Exception:
                pass

        return False  # never suppress exceptions

    def register_orphan(self, img):
        """Queue an image for removal unless commit() is called after bake."""
        if img is not None:
            self._orphans.append(img)

    def commit(self):
        """Call after a bake succeeds — clears the pending-orphan queue so
        currently-registered images are kept."""
        self._orphans.clear()


def _ensure_nodes(mat):
    """Make sure the material uses nodes."""
    if not mat.use_nodes:
        mat.use_nodes = True


def _add_layer_common(context, layer_type):
    """Shared logic for all add-layer operators."""
    mat = _get_material(context)
    if not mat:
        return None
    if _is_shader_editable_material(mat):
        return None
    _ensure_nodes(mat)
    tlm = mat.tlm

    # Determine parent group from the currently active layer:
    # - if active is an EMPTY GROUP -> new layer goes INSIDE it (first child)
    # - if active is a GROUP with children -> new layer goes ABOVE the group (root level)
    # - if active is already inside a group -> new layer goes in the same group
    # - otherwise -> root level
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
        layer.name = f"Paint {len(tlm.layers)}"
        res = int(tlm.resolution)
        # Initialise unpainted pixels as BLACK-transparent (0,0,0,0).
        #
        # We used to init as WHITE-transparent (1,1,1,0) here, on the theory
        # that "if alpha ever leaks, white is benign". But the compositor
        # has since been hardened to always honour layer_alpha (commit
        # 0ce37dc + the first-layer modulator path), so RGB at alpha=0
        # never reaches the BSDF — the init colour only matters at the
        # brush AA edge, where the texture sampler interpolates between
        # the unpainted RGB and the painted RGB.
        #
        # With WHITE init and a DARK brush, that edge interpolates
        # through grey, which mixes with the layer below into a visible
        # darker fringe. With BLACK init and a DARK brush the edge
        # stays near-black throughout the gradient, so the fringe
        # collapses into a smooth alpha fade. The reverse case
        # (light brush, dark below) gets a mild light fringe instead,
        # which is the lesser of the two evils for typical paint
        # workflows (most artists paint dark masks/dirt over a brighter
        # base, not the opposite).
        try:
            img_gen = bpy.data.images.new(layer.name,
                                           width=res, height=res,
                                           alpha=True, float_buffer=False)
        except TypeError:
            img_gen = bpy.data.images.new(layer.name,
                                           width=res, height=res,
                                           alpha=True)
        img = img_gen
        try:
            img.generated_color = (0.0, 0.0, 0.0, 0.0)
        except Exception:
            pass
        # Keep the canvas alpha readable by ShaderNodeTexImage.Alpha. Using
        # alpha_mode='NONE' makes Cycles ignore coverage in some paint-over-fill
        # paths, so transparent black pixels leak into the Base Color mix.
        try:
            img.alpha_mode = 'STRAIGHT'
        except Exception:
            pass

        # Now overwrite the pixel buffer explicitly. We do BOTH foreach_set
        # (fast) AND a slow-path fallback if it's unavailable, then call
        # update() + update_tag() — Blender 5.0 needs both for the buffer
        # to be visible to downstream readers (rebuild, tex node sampling).
        try:
            import numpy as np
            px = np.zeros(res * res * 4, dtype=np.float32)
            img.pixels.foreach_set(px)
        except Exception:
            img.pixels[:] = [0.0, 0.0, 0.0, 0.0] * (res * res)
        try:
            img.update()
        except Exception:
            pass
        try:
            img.update_tag()
        except Exception:
            pass
        img.use_fake_user = True  # prevent GC when layer is hidden
        layer.image_name = img.name
        previews.invalidate(img.name)
    elif layer_type == "FILL":
        layer.name = f"Fill {len(tlm.layers)}"
    elif layer_type == "ADJUSTMENT":
        layer.name = "Hue/Sat"
    elif layer_type == "PROCEDURAL":
        # proc_type defaults to 'NOISE' via the PropertyGroup definition.
        layer.name = "Noise"
    elif layer_type == "REFERENCE":
        layer.name = f"Reference {len(tlm.layers)}"

    # Move new layer to the correct position: new layers appear ABOVE the
    # active one in the UI (= composited LATER, i.e. on top). layers.add()
    # appends at end; move it to the right spot.
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
