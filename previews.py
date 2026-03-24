"""
previews.py
Thumbnail preview system for Texture Layer Manager.

Blender 5.0: image_pixels expects w*h int32 (packed RGBA).
We use image_pixels_float (w*h*4 floats, 0.0-1.0) for simplicity.
"""

import bpy
import bpy.utils.previews
import numpy as np

_preview_collection = None
_cache = {}  # image_name -> (pixel_hash, icon_id)

# Blender 5.0 enforces 64x64; older versions support 128x128
THUMB_SIZE = 64 if bpy.app.version >= (5, 0, 0) else 128


def _get_collection():
    global _preview_collection
    if _preview_collection is None:
        _preview_collection = bpy.utils.previews.new()
    return _preview_collection


def _cheap_hash(image):
    """Sample ~16 pixels for fast change detection.

    FIX: the original accessed image.pixels as a Python sequence (slow — Blender
    loads all pixels before allowing indexed access).  foreach_get into a small
    pre-allocated buffer samples only what we need in one C-level call.
    """
    if image is None or image.size[0] == 0 or image.size[1] == 0:
        return 0
    w, h = image.size
    total = w * h
    if total == 0:
        return 0

    # Sample up to 16 evenly-spaced pixels; each pixel is 4 floats
    n_samples = min(16, total)
    step = max(1, total // n_samples)
    indices = range(0, total, step)

    # Read only the required pixels via foreach_get on a full buffer
    # (Blender doesn't expose random-access foreach_get, so we read all
    #  and index — still faster because it avoids Python-level iteration)
    buf = np.empty(total * 4, dtype=np.float32)
    image.pixels.foreach_get(buf)

    sample = []
    for i in indices:
        base = i * 4
        sample.append(round(float(buf[base]),     2))
        sample.append(round(float(buf[base + 1]), 2))
        sample.append(round(float(buf[base + 2]), 2))
    return hash(tuple(sample))


def _generate_thumbnail(image):
    """Downsample image to THUMB_SIZE and write into the preview collection."""
    pcoll = _get_collection()
    key = image.name

    w, h = image.size
    if w == 0 or h == 0:
        return 0

    buf = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(buf)
    buf = buf.reshape((h, w, 4))
    buf = buf[::-1, :, :]  # flip vertically (Blender is bottom-up)

    th = THUMB_SIZE
    y_idx = np.linspace(0, h - 1, th).astype(int)
    x_idx = np.linspace(0, w - 1, th).astype(int)
    thumb = buf[np.ix_(y_idx, x_idx)]

    flat = np.clip(thumb, 0.0, 1.0).flatten().tolist()

    if key in pcoll:
        del pcoll[key]

    preview = pcoll.new(key)
    preview.image_size = (th, th)
    preview.image_pixels_float = flat

    return int(preview.icon_id) & 0x7FFFFFFF


def get_layer_icon_id(layer):
    """Return icon_id for a paint layer, regenerating if dirty."""
    image = layer.image
    if image is None:
        return 0

    key = image.name
    current_hash = _cheap_hash(image)
    cached = _cache.get(key)
    if cached and cached[0] == current_hash:
        return cached[1]

    try:
        icon_id = _generate_thumbnail(image)
        _cache[key] = (current_hash, icon_id)
        return icon_id
    except Exception:
        return 0


def get_fill_icon_id(layer):
    """Return icon_id for a fill layer (solid color swatch)."""
    pcoll = _get_collection()
    r, g, b, a = layer.fill_color
    key = f"__fill_{r:.3f}_{g:.3f}_{b:.3f}_{a:.3f}"

    if key in pcoll:
        return int(pcoll[key].icon_id) & 0x7FFFFFFF

    th = THUMB_SIZE
    flat = [r, g, b, a] * (th * th)

    preview = pcoll.new(key)
    preview.image_size = (th, th)
    preview.image_pixels_float = flat

    return int(preview.icon_id) & 0x7FFFFFFF


def invalidate(image_name):
    """Mark a layer thumbnail as dirty."""
    _cache.pop(image_name, None)
    pcoll = _get_collection()
    if image_name in pcoll:
        del pcoll[image_name]


def invalidate_all():
    """Clear the entire thumbnail cache."""
    global _cache
    _cache = {}
    _get_collection().clear()


def register():
    global _preview_collection, _cache
    _preview_collection = bpy.utils.previews.new()
    _cache = {}


def unregister():
    global _preview_collection, _cache
    if _preview_collection is not None:
        bpy.utils.previews.remove(_preview_collection)
        _preview_collection = None
    _cache = {}
