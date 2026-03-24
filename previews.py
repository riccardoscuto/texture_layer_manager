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
    """Return icon_id for a fill layer (solid color swatch).

    Creates a tiny 4x4 image filled with the layer color and uses
    Blender's native preview system to display it.
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


def register():
    global _fill_cache
    _fill_cache = {}


def unregister():
    global _fill_cache
    # Clean up swatch images
    for img in list(bpy.data.images):
        if img.name.startswith(".tlm_swatch_"):
            bpy.data.images.remove(img)
    _fill_cache = {}
