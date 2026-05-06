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


# ─── Bake safety ──────────────────────────────────────────────────────────────

def _bake_preflight(context):
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
    return True, ""


class _BakeGuard:
    """Context manager that makes a bake operation safe for the user.

    On entry:
      - Forces render engine to CYCLES (required by bpy.ops.object.bake).
      - Snapshots node-tree selection + active node.

    On exit:
      - Restores render engine.
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

    def __init__(self, context, node_tree):
        self.context = context
        self.node_tree = node_tree
        self._engine_backup = None
        self._active_node_backup = None
        self._node_selection_backup = {}
        self._orphans = []

    def __enter__(self):
        scene = self.context.scene
        self._engine_backup = scene.render.engine
        if self._engine_backup != 'CYCLES':
            try:
                scene.render.engine = 'CYCLES'
            except Exception:
                # Cycles unavailable — extremely rare in Blender 5.0, let caller fail naturally
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

        # Restore render engine
        try:
            self.context.scene.render.engine = self._engine_backup
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
        # Override Blender's default 'generated black opaque' image — set
        # the generated_color BEFORE we start writing pixels so any path
        # that consults it (preview, internal cache invalidation) sees
        # white-transparent first.
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
            img.generated_color = (1.0, 1.0, 1.0, 0.0)
        except Exception:
            pass
        # alpha_mode='NONE' tells Blender's tex node to ignore the image's
        # alpha as a colour-space-related straight/premultiplied flag.
        # The compositor still reads tex.outputs["Alpha"] for layer
        # coverage — that's a separate path. With STRAIGHT (default),
        # Blender 5.0 sometimes auto-multiplies RGB by Alpha when
        # sampling, which fights the routing logic for paint layers.
        try:
            img.alpha_mode = 'NONE'
        except Exception:
            pass

        # Now overwrite the pixel buffer explicitly. We do BOTH foreach_set
        # (fast) AND a slow-path fallback if it's unavailable, then call
        # update() + update_tag() — Blender 5.0 needs both for the buffer
        # to be visible to downstream readers (rebuild, tex node sampling).
        try:
            import numpy as np
            px = np.tile([1.0, 1.0, 1.0, 0.0], res * res).astype(np.float32)
            img.pixels.foreach_set(px)
        except Exception:
            img.pixels[:] = [1.0, 1.0, 1.0, 0.0] * (res * res)
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
