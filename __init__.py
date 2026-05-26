bl_info = {
    "name": "Texture Layer Manager",
    "author": "TLM Dev",
    "version": (0, 5, 0),
    "blender": (4, 0, 0),
    "location": "Properties > Material > Texture Layers",
    "description": "Non-destructive layer-based texture authoring with PBR channels, smart masks, and procedurals",
    "category": "Material",
}

import bpy
from . import properties, operators, panels, compositing, previews

modules = [properties, previews, operators, panels, compositing]

# msgbus owner handle — needed to unsubscribe on unregister
_msgbus_owner = object()

# FIX: debounce the image-change handler so rapid pixel writes during painting
# (which fire the msgbus on every stroke sample) don't trigger a full
# invalidate_all + redraw on every event.  All changes within 0.15 s are
# collapsed into a single invalidation.
_invalidate_pending = False


def _do_deferred_invalidate():
    """Timer callback — fires once after the debounce interval."""
    global _invalidate_pending
    _invalidate_pending = False
    previews.invalidate_all()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'PROPERTIES':
                area.tag_redraw()
    return None  # returning None unregisters the timer


def _on_image_changed():
    """Called by msgbus when any bpy.data.images entry changes."""
    global _invalidate_pending
    if not _invalidate_pending:
        _invalidate_pending = True
        bpy.app.timers.register(_do_deferred_invalidate, first_interval=0.15)


def _subscribe_msgbus():
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.Image, "pixels"),
        owner=_msgbus_owner,
        args=(),
        notify=_on_image_changed,
        options={'PERSISTENT'},
    )


# ── Depsgraph handler: hot-update NDOTL/NDOTH sun direction ────────────────
# When the user moves or rotates a Sun light, refresh the baked sun vector
# in all TLM materials' NDOTL/NDOTH dot-product nodes. Avoids needing to
# rebuild the entire material on every sun pose change.
_last_sun_pose = None  # cache the last Sun matrix to detect changes


def _on_depsgraph_update_post(scene, depsgraph):
    """Detect Sun light pose changes and refresh NDOTL/NDOTH nodes."""
    global _last_sun_pose
    try:
        # Find the first Sun in the scene
        sun_obj = None
        for obj in scene.objects:
            if obj.type == 'LIGHT' and obj.data.type == 'SUN':
                sun_obj = obj
                break
        if sun_obj is None:
            _last_sun_pose = None
            return
        # Compute a cheap pose signature (matrix row 2 = the sun direction we care about)
        z_axis = sun_obj.matrix_world.col[2].to_3d().normalized()
        pose_signature = (round(z_axis.x, 4), round(z_axis.y, 4), round(z_axis.z, 4))
        if pose_signature == _last_sun_pose:
            return  # nothing changed at the precision we care about
        _last_sun_pose = pose_signature
        # Sun moved → walk all TLM materials and refresh their baked vectors
        from . import compositing
        n = compositing.hot_update_sun_direction()
        # No print here — too chatty on every depsgraph update
    except Exception:
        pass  # never crash the depsgraph from a hot-update handler


def register():
    # Print loaded version + module path so the user can verify in the
    # System Console that Blender actually picked up the latest code
    # (cached __pycache__ or duplicate installs sometimes load stale).
    print(f"[TLM] register: version {bl_info['version']} from {__file__}")
    for mod in modules:
        mod.register()
    _subscribe_msgbus()
    # Register depsgraph handler for NDOTL/NDOTH sun hot-update
    if _on_depsgraph_update_post not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update_post)


def unregister():
    # Cancel any pending invalidation timer
    global _invalidate_pending
    if _invalidate_pending and bpy.app.timers.is_registered(_do_deferred_invalidate):
        bpy.app.timers.unregister(_do_deferred_invalidate)
    _invalidate_pending = False

    bpy.msgbus.clear_by_owner(_msgbus_owner)
    # Remove depsgraph handler
    if _on_depsgraph_update_post in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update_post)
    for mod in reversed(modules):
        mod.unregister()


if __name__ == "__main__":
    register()
