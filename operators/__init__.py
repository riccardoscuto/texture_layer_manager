"""
operators — All Blender Operators for Texture Layer Manager.
Split into submodules by functional area for maintainability.
"""

import bpy

from .layers import classes as _layers
from .groups import classes as _groups
from .visibility import classes as _visibility
from .compositing_ops import classes as _compositing_ops
from .masks import classes as _masks
from .io import classes as _io
from .pbr import classes as _pbr
from .importing import classes as _importing
from .bake import classes as _bake
from .presets import classes as _presets
from .channel_pack import classes as _channel_pack
from .keyframes import classes as _keyframes
from .thumbnails import classes as _thumbnails

# Re-export commonly used items for backwards compatibility
from .pbr import CHANNEL_INFO
from .io import _layer_to_dict, _dict_to_layer
from .presets import BUILTIN_PRESETS

classes = (
    _layers
    + _groups
    + _visibility
    + _compositing_ops
    + _masks
    + _io
    + _pbr
    + _importing
    + _bake
    + _presets
    + _channel_pack
    + _keyframes
    + _thumbnails
)


def register():
    # Defensive: drop stale registrations before re-registering. See
    # properties.register() for the rationale.
    for cls in classes:
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
