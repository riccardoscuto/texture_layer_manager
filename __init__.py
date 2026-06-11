bl_info = {
    "name": "Texture Layer Manager",
    "author": "Eihort",
    "version": (0, 5, 11),
    "blender": (5, 0, 0),
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


# ── Frame-change handler: drive ANIMATED layer props into the node tree ────
# Blender's animation system writes property values directly and BYPASSES the
# Python `update=` callbacks TLM relies on to hot-sync the node graph. So a
# keyframed `tlm.layers[i].opacity` (the Keyframe button) animated the property
# but never moved the compositing Mix factor — the material looked frozen and
# the button "did nothing". On each frame change we re-apply the matching
# hot-updater for the animated layer props so animation shows live AND in
# rendered output.
import re as _re
_TLM_LAYER_FCURVE_RE = _re.compile(r"tlm\.layers\[(\d+)\]\.([A-Za-z_0-9]+)")


def _iter_action_fcurves(action):
    """Yield an Action's F-curves across legacy and 4.4+ slotted layouts."""
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        for fc in legacy:
            yield fc
        return
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            bags = getattr(strip, "channelbags", None)
            if bags is not None:
                for bag in bags:
                    for fc in getattr(bag, "fcurves", []):
                        yield fc
            else:
                for slot in getattr(action, "slots", []):
                    try:
                        bag = strip.channelbag(slot)
                    except Exception:
                        bag = None
                    if bag is not None:
                        for fc in getattr(bag, "fcurves", []):
                            yield fc


@bpy.app.handlers.persistent
def _on_frame_change_post(scene, depsgraph=None):
    """Re-sync animated TLM layer properties into the node tree each frame."""
    try:
        from .compositing import hot_update as _hu
        dispatch = _hu._HOT_DISPATCH
        opac = dispatch.get("opacity")
    except Exception:
        return
    for mat in bpy.data.materials:
      try:
        if not getattr(mat, "use_nodes", False):
            continue
        tlm = getattr(mat, "tlm", None)
        if tlm is None or len(tlm.layers) == 0:
            continue
        ad = mat.animation_data
        if ad is None or ad.action is None:
            continue
        nt = mat.node_tree
        if nt is None:
            continue
        # (1) Cheap unconditional opacity re-sync — guarantees the Keyframe
        #     button works even if the Action F-curve API can't be walked.
        if opac is not None:
            for layer in tlm.layers:
                try:
                    opac(nt, layer, "opacity")
                except Exception:
                    pass
        # (2) F-curve-targeted dispatch for any OTHER animated hot property
        #     (proc params, colours, emission strength, adjustments, …).
        try:
            for fc in _iter_action_fcurves(ad.action):
                m = _TLM_LAYER_FCURVE_RE.match(fc.data_path or "")
                if not m:
                    continue
                prop = m.group(2)
                if prop == "opacity":
                    continue  # handled above
                li = int(m.group(1))
                if li >= len(tlm.layers):
                    continue
                fn = dispatch.get(prop)
                if fn is not None:
                    try:
                        fn(nt, tlm.layers[li], prop)
                    except Exception:
                        pass
        except Exception:
            pass
      except Exception:
        continue  # one bad material must never break playback


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
    # Register frame-change handler so animated/keyframed layer props (opacity
    # etc.) actually drive the node tree during playback and rendering.
    if _on_frame_change_post not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_on_frame_change_post)


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
    # Remove frame-change handler
    if _on_frame_change_post in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame_change_post)
    for mod in reversed(modules):
        mod.unregister()


if __name__ == "__main__":
    register()
