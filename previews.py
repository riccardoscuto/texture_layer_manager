"""
previews.py
Thumbnail preview system for Texture Layer Manager.

Uses Blender's native image preview system (image.preview_ensure())
for paint layers, and small helper images for fill layer swatches.
"""

import bpy

# Cache fill-swatch image names so we don't recreate them every draw
_fill_cache = {}  # "(r,g,b,a)" -> image_name


def get_layer_icon_id(layer):
    """Return icon_id for a paint layer using Blender's native preview."""
    image = layer.image
    if image is None:
        return 0
    try:
        image.preview_ensure()
        iid = image.preview.icon_id
        if iid and iid > 0:
            return iid
    except Exception:
        pass
    return 0


def get_fill_icon_id(layer):
    """DEPRECATED — kept only for backwards compatibility with external tools.

    The TLM_UL_LayerList no longer calls this: FILL layers now use a native
    ``layout.prop(..., "fill_color")`` widget which renders a small color
    swatch without creating image datablocks. Creating ``.tlm_swatch_*``
    images polluted the ``bpy.data, "images"`` dropdowns used to pick
    textures (mask source, PBR channels, etc.).

    The function still works (creates a 4x4 image and returns its icon_id),
    but its results are no longer used internally. Will be removed in 1.0.

    Returns icon_id for a fill layer (solid color swatch).
    """
    r, g, b, a = layer.fill_color
    key = f"{r:.2f},{g:.2f},{b:.2f},{a:.2f}"

    cached_name = _fill_cache.get(key)
    if cached_name:
        img = bpy.data.images.get(cached_name)
        if img is not None:
            try:
                img.preview_ensure()
                iid = img.preview.icon_id
                if iid and iid > 0:
                    return iid
            except Exception:
                pass

    # Create a small swatch image
    name = f".tlm_swatch_{key}"
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(name, 4, 4, alpha=True)

    # Fill with the color (4x4 = 16 pixels, 64 floats)
    pixels = [r, g, b, a] * (4 * 4)
    img.pixels.foreach_set(pixels)
    img.update()

    _fill_cache[key] = name

    try:
        img.preview_ensure()
        iid = img.preview.icon_id
        if iid and iid > 0:
            return iid
    except Exception:
        pass
    return 0


def invalidate(image_name):
    """Mark a layer thumbnail as dirty (force preview regeneration)."""
    img = bpy.data.images.get(image_name)
    if img is not None and img.preview:
        img.preview.reload()


def invalidate_all():
    """Clear the entire thumbnail cache."""
    global _fill_cache
    _fill_cache = {}
    # Clean up swatch images
    for img in list(bpy.data.images):
        if img.name.startswith(".tlm_swatch_"):
            bpy.data.images.remove(img)


def cleanup_orphan_swatches():
    """Remove all .tlm_swatch_* images from bpy.data.images.

    Safe to call any time EXCEPT during register() / .blend load: bpy.data
    is in 'restricted' mode there and accessing bpy.data.images raises
    `_RestrictData` AttributeError. Use _deferred_swatch_cleanup() (timer)
    or schedule via load_post handler instead.

    These images are never linked into a node tree — they only existed as
    preview sources for the old FILL icon path. .blend files saved before
    the FILL widget migration may still contain them.
    """
    removed = 0
    try:
        images = list(bpy.data.images)
    except AttributeError:
        # bpy.data restricted (mid-register or mid-load) — caller should
        # defer via timer.
        return 0
    for img in images:
        if img.name.startswith(".tlm_swatch_"):
            try:
                bpy.data.images.remove(img)
                removed += 1
            except Exception:
                pass
    if removed:
        print(f"[TLM] Removed {removed} orphan .tlm_swatch_* image(s)")
    return removed


def _deferred_swatch_cleanup():
    """One-shot timer body — runs once Blender is past the restricted phase."""
    try:
        cleanup_orphan_swatches()
    except Exception:
        import traceback
        traceback.print_exc()
    return None  # returning None unregisters this timer


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    """Sweep .tlm_swatch_* images when a .blend file is loaded.

    Files saved before the FILL widget migration carry stale swatches; this
    handler keeps the dropdowns clean after every load. Decorated with
    @persistent so it survives 'New File' clearing the handler list.
    """
    try:
        cleanup_orphan_swatches()
    except Exception:
        pass


def register():
    global _fill_cache
    _fill_cache = {}
    # bpy.data is restricted during register() — schedule the sweep on a
    # timer so it runs once Blender is past the restricted phase.
    try:
        bpy.app.timers.register(_deferred_swatch_cleanup, first_interval=0.5)
    except Exception:
        pass
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    global _fill_cache
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    # Sweep once more on disable (best-effort — bpy.data may be restricted
    # if Blender is mid-shutdown).
    cleanup_orphan_swatches()
    _fill_cache = {}
