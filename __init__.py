bl_info = {
    "name": "Texture Layer Manager",
    "author": "TLM Dev",
    "version": (0, 3, 23),
    "blender": (4, 0, 0),
    "location": "Properties > Material > Texture Layers",
    "description": "Non-destructive layer system for texture painting, similar to Photoshop/Substance",
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


def register():
    for mod in modules:
        mod.register()
    _subscribe_msgbus()


def unregister():
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    for mod in reversed(modules):
        mod.unregister()


if __name__ == "__main__":
    register()
