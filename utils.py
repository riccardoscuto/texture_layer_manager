"""
utils.py
Helper functions for Texture Layer Manager.
"""

import bpy
import numpy as np


def get_active_material(context):
    """Return the active material or None."""
    obj = context.active_object
    if obj and obj.active_material:
        return obj.active_material
    return None


def ensure_uv_map(obj, uv_map_name="UVMap"):
    """Ensure the object has the specified UV map, create it if missing."""
    mesh = obj.data
    if uv_map_name not in mesh.uv_layers:
        mesh.uv_layers.new(name=uv_map_name)
        return False  # was created
    return True  # already existed


def image_exists(name):
    return name in bpy.data.images


def get_or_create_image(name, width, height, alpha=True, float_buffer=False):
    """Get an existing image or create a new one."""
    if name in bpy.data.images:
        img = bpy.data.images[name]
        if img.size[0] != width or img.size[1] != height:
            img.scale(width, height)
        return img, False  # (image, was_created)

    img = bpy.data.images.new(name, width=width, height=height, alpha=alpha, float_buffer=float_buffer)
    return img, True


def clear_image(img, color=(0.0, 0.0, 0.0, 0.0)):
    """Fill an image with a solid color (RGBA).

    FIX: previously used a Python list multiplication which allocates a huge
    list in memory and is very slow on 2K/4K images.  numpy foreach_set is
    ~10–50× faster and uses far less peak memory.
    """
    r, g, b, a = color
    total = img.size[0] * img.size[1]
    px = np.empty(total * 4, dtype=np.float32)
    px[0::4] = r
    px[1::4] = g
    px[2::4] = b
    px[3::4] = a
    img.pixels.foreach_set(px)
    img.update()


def pack_all_tlm_images(mat):
    """Pack all layer images into the .blend file for portability."""
    tlm = mat.tlm
    for layer in tlm.layers:
        img = layer.image
        if img and not img.packed_file:
            img.pack()
        mask = layer.mask_image
        if mask and not mask.packed_file:
            mask.pack()
