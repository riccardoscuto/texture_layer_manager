"""
properties.py
Defines the data model for Texture Layer Manager.
Each layer stores its image reference, blend mode, opacity, etc.
All data lives as Blender PropertyGroups — saved with the .blend file.
"""

import bpy
from bpy.props import (
    StringProperty, FloatProperty, BoolProperty,
    EnumProperty, CollectionProperty, IntProperty,
)
from bpy.types import PropertyGroup
from . import compositing

# ── Rebuild debounce ──────────────────────────────────────────────────────────
# Prevents a full node-tree rebuild on every individual slider tick.
# Multiple rapid changes within 0.05 s are batched into a single rebuild.

_rebuild_pending = False


def _do_deferred_rebuild():
    """Timer callback — runs once after the debounce interval."""
    global _rebuild_pending
    if not _rebuild_pending:
        # An operator already performed the rebuild — skip redundant work.
        return None
    _rebuild_pending = False
    try:
        ctx = bpy.context
        obj = ctx.active_object if ctx else None
        if obj and obj.active_material:
            mat = obj.active_material
            if mat.tlm.auto_composite:
                compositing.rebuild_node_tree(mat)
                # Force shader editor redraw — timer callbacks don't
                # automatically trigger UI updates like operators do.
                for window in ctx.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type == 'NODE_EDITOR':
                            area.tag_redraw()
    except Exception:
        import traceback
        traceback.print_exc()
    return None  # returning None unregisters the timer


def cancel_pending_rebuild():
    """Cancel any pending deferred rebuild.

    Call this from operators that invoke compositing.rebuild_node_tree()
    directly, so the deferred timer doesn't fire a redundant second rebuild
    that clears and recreates all nodes (which can fail to trigger a UI
    redraw in the shader editor).
    """
    global _rebuild_pending
    _rebuild_pending = False


def _on_layer_update(self, context):
    """Called whenever any layer property changes. Triggers a debounced recomposite."""
    global _rebuild_pending
    # Guard: context may not have an active object (e.g. during file load)
    if not context or not context.active_object:
        return
    mat = context.active_object.active_material
    if not mat or not mat.tlm.auto_composite:
        return
    if not _rebuild_pending:
        _rebuild_pending = True
        bpy.app.timers.register(_do_deferred_rebuild, first_interval=0.05)


# ─── Blend mode enum ─────────────────────────────────────────────────────────

BLEND_MODES = [
    ("MIX",        "Normal",     "Alpha composite over layer below", 0),
    ("MULTIPLY",   "Multiply",   "Darken by multiplying values",      1),
    ("SCREEN",     "Screen",     "Lighten, inverse of multiply",      2),
    ("OVERLAY",    "Overlay",    "Contrast-enhancing blend",          3),
    ("ADD",        "Add",        "Additive / lighten",                4),
    ("SUBTRACT",   "Subtract",   "Subtract layer from below",         5),
    ("DIFFERENCE", "Difference", "Absolute difference",               6),
    ("DIVIDE",     "Divide",     "Divide layer below by this layer",  7),
    ("DARKEN",     "Darken",     "Keep darkest of both",              8),
    ("LIGHTEN",    "Lighten",    "Keep lightest of both",             9),
    ("COLOR_DODGE","Color Dodge","Brighten based on layer",           10),
    ("COLOR_BURN", "Color Burn", "Darken based on layer",            11),
    ("SOFT_LIGHT", "Soft Light", "Subtle contrast blend",             12),
    ("HARD_LIGHT", "Hard Light", "Strong contrast blend",             13),
    ("LINEAR_LIGHT","Linear Light","High-contrast dodge+burn",        14),
]

LAYER_TYPES = [
    ("PAINT",       "Paint",       "Regular paint layer with an image texture"),
    ("FILL",        "Fill",        "Solid color fill layer"),
    ("ADJUSTMENT",  "Adjustment",  "Modifier layer: Hue/Sat, Levels, etc."),
    ("GROUP",       "Group",       "Folder that contains other layers"),
    ("PROCEDURAL",  "Procedural",  "Shader-based procedural texture layer"),
]


# ─── Single Layer ─────────────────────────────────────────────────────────────

class TLM_LayerItem(PropertyGroup):
    """Represents a single texture layer."""

    layer_type: EnumProperty(
        name="Type",
        items=LAYER_TYPES,
        default="PAINT",
        update=_on_layer_update,
    )

    visible: BoolProperty(
        name="Visible",
        default=True,
        update=_on_layer_update,
    )

    locked: BoolProperty(
        name="Locked",
        description="Prevent painting on this layer",
        default=False,
    )

    opacity: FloatProperty(
        name="Opacity",
        min=0.0, max=1.0,
        default=1.0,
        subtype='FACTOR',
        update=_on_layer_update,
    )

    blend_mode: EnumProperty(
        name="Blend Mode",
        items=BLEND_MODES,
        default="MIX",
        update=_on_layer_update,
    )

    # Reference to the Blender Image datablock (by name, the Blender way)
    image_name: StringProperty(
        name="Image",
        description="Name of the bpy.data.images image for this layer",
        default="",
    )

    # Fill layer: solid color
    fill_color: bpy.props.FloatVectorProperty(
        name="Fill Color",
        subtype='COLOR',
        min=0.0, max=1.0,
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        update=_on_layer_update,
    )

    # Optional mask
    use_mask: BoolProperty(
        name="Use Mask",
        default=False,
        update=_on_layer_update,
    )

    mask_image_name: StringProperty(
        name="Mask Image",
        default="",
        update=_on_layer_update,
    )

    # Clipping mask — clip this layer to the alpha of the layer directly below
    use_clipping_mask: BoolProperty(
        name="Clipping Mask",
        description="Clip this layer to the alpha of the layer below (like Photoshop)",
        default=False,
        update=_on_layer_update,
    )

    # Triplanar projection — no UV needed, projects from 3 axes
    use_triplanar: BoolProperty(
        name="Triplanar Projection",
        description="Project texture from 3 axes — works without UV unwrap",
        default=False,
        update=_on_layer_update,
    )
    triplanar_scale: FloatProperty(
        name="Scale", default=1.0, min=0.001, max=100.0,
        update=_on_layer_update,
    )
    triplanar_sharpness: FloatProperty(
        name="Blend Sharpness", default=2.0, min=0.1, max=20.0,
        description="Higher = harder transitions between axes",
        update=_on_layer_update,
    )

    # Internal: name of the node group we generate for this layer
    node_group_name: StringProperty(
        name="Internal Node Group",
        default="",
    )

    # ── PBR Channels ─────────────────────────────────────────────────────────
    # Each layer can independently paint/fill additional PBR channels.
    # All channels are opt-in: disabling them leaves the channel untouched.

    # Roughness
    use_roughness: BoolProperty(name="Roughness", default=False, update=_on_layer_update)
    roughness_image_name: StringProperty(name="Roughness Image", default="",
        update=_on_layer_update)
    roughness_fill: FloatProperty(
        name="Roughness", default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )

    # Metallic
    use_metallic: BoolProperty(name="Metallic", default=False, update=_on_layer_update)
    metallic_image_name: StringProperty(name="Metallic Image", default="",
        update=_on_layer_update)
    metallic_fill: FloatProperty(
        name="Metallic", default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )

    # Normal Map
    use_normal: BoolProperty(name="Normal", default=False, update=_on_layer_update)
    normal_image_name: StringProperty(name="Normal Image", default="",
        update=_on_layer_update)
    normal_strength: FloatProperty(
        name="Normal Strength", default=1.0, min=0.0, max=5.0,
        update=_on_layer_update,
    )

    # Emission
    use_emission: BoolProperty(name="Emission", default=False, update=_on_layer_update)
    emission_image_name: StringProperty(name="Emission Image", default="",
        update=_on_layer_update)
    emission_color: bpy.props.FloatVectorProperty(
        name="Emission Color", subtype='COLOR',
        min=0.0, max=1.0, size=4, default=(1.0, 1.0, 1.0, 1.0),
        update=_on_layer_update,
    )
    emission_strength: FloatProperty(
        name="Emission Strength", default=1.0, min=0.0, max=100.0,
        update=_on_layer_update,
    )

    # Bump — derived from the layer's own Fac signal (Proc) or image (Paint)
    use_bump: BoolProperty(
        name="Bump",
        description="Generate bump/surface detail from this layer's texture signal",
        default=False,
        update=_on_layer_update,
    )
    bump_strength: FloatProperty(
        name="Bump Strength", default=0.5, min=0.0, max=5.0,
        update=_on_layer_update,
    )
    bump_distance: FloatProperty(
        name="Bump Distance", default=0.05, min=0.001, max=1.0,
        update=_on_layer_update,
    )

    @property
    def roughness_image(self):
        return bpy.data.images.get(self.roughness_image_name)

    @property
    def metallic_image(self):
        return bpy.data.images.get(self.metallic_image_name)

    @property
    def normal_image(self):
        return bpy.data.images.get(self.normal_image_name)

    @property
    def emission_image(self):
        return bpy.data.images.get(self.emission_image_name)

    # ── Group / folder properties ─────────────────────────────────────────────

    # Name of the parent GROUP layer (empty string = top-level, no parent)
    group_name: StringProperty(
        name="Parent Group",
        description="Name of the group this layer belongs to (empty = root)",
        default="",
    )

    # Only meaningful when layer_type == 'GROUP'
    collapsed: BoolProperty(
        name="Collapsed",
        description="Hide child layers in the list",
        default=False,
    )

    # ── Adjustment layer properties ───────────────────────────────────────────

    adj_type: EnumProperty(
        name="Adjustment",
        items=[
            ('HUE_SAT',        "Hue/Saturation",    "Adjust hue, saturation and value"),
            ('BRIGHT_CONTRAST', "Brightness/Contrast","Adjust brightness and contrast"),
            ('LEVELS',         "Levels",             "Remap input/output tonal range"),
            ('COLOR_BALANCE',  "Color Balance",      "Lift / Gamma / Gain (cinematic grading)"),
        ],
        default='HUE_SAT',
        update=_on_layer_update,
    )

    # Hue/Saturation/Value
    adj_hue: FloatProperty(
        name="Hue", default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )
    adj_saturation: FloatProperty(
        name="Saturation", default=1.0, min=0.0, max=2.0,
        update=_on_layer_update,
    )
    adj_value: FloatProperty(
        name="Value", default=1.0, min=0.0, max=2.0,
        update=_on_layer_update,
    )

    # Brightness/Contrast
    adj_brightness: FloatProperty(
        name="Brightness", default=0.0, min=-1.0, max=1.0,
        update=_on_layer_update,
    )
    adj_contrast: FloatProperty(
        name="Contrast", default=0.0, min=-1.0, max=1.0,
        update=_on_layer_update,
    )

    # Levels
    adj_in_min: FloatProperty(
        name="Input Black", default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )
    adj_in_max: FloatProperty(
        name="Input White", default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )
    # FIX: renamed from adj_gamma (which was silently overwritten by the
    # Color Balance FloatVectorProperty below). Now uses a distinct name.
    adj_levels_gamma: FloatProperty(
        name="Gamma (Midtones)", default=1.0, min=0.1, max=10.0,
        update=_on_layer_update,
    )
    adj_out_min: FloatProperty(
        name="Output Black", default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )
    adj_out_max: FloatProperty(
        name="Output White", default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )

    # Color Balance (Lift / Gamma / Gain)
    adj_lift: bpy.props.FloatVectorProperty(
        name="Lift", subtype='COLOR', min=0.0, max=2.0, size=3,
        default=(1.0, 1.0, 1.0), update=_on_layer_update,
    )
    adj_gamma: bpy.props.FloatVectorProperty(
        name="Gamma", subtype='COLOR', min=0.0, max=2.0, size=3,
        default=(1.0, 1.0, 1.0), update=_on_layer_update,
    )
    adj_gain: bpy.props.FloatVectorProperty(
        name="Gain", subtype='COLOR', min=0.0, max=2.0, size=3,
        default=(1.0, 1.0, 1.0), update=_on_layer_update,
    )

    # ── Procedural layer properties ───────────────────────────────────────────

    proc_type: EnumProperty(
        name="Type",
        items=[
            ('NOISE',    "Noise",    "Perlin/FBM noise"),
            ('VORONOI',  "Voronoi",  "Cell/Worley noise"),
            ('WAVE',     "Wave",     "Sine wave bands or rings"),
            ('GRADIENT', "Gradient", "Linear, radial, quadratic or spherical gradient"),
            ('MUSGRAVE', "Musgrave", "Fractal noise (Multifractal, Ridged, etc.)"),
            ('CHECKER',  "Checker",  "Alternating checkerboard pattern"),
        ],
        default='NOISE',
        update=_on_layer_update,
    )

    # Shared: scale, mapping offset/rotation
    proc_scale: FloatProperty(
        name="Scale", default=5.0, min=0.001, max=1000.0,
        update=_on_layer_update,
    )
    proc_offset_x: FloatProperty(name="Offset X", default=0.0, update=_on_layer_update)
    proc_offset_y: FloatProperty(name="Offset Y", default=0.0, update=_on_layer_update)
    proc_offset_z: FloatProperty(name="Offset Z", default=0.0, update=_on_layer_update)

    # Colors (Color1 = dark/base, Color2 = bright/accent)
    proc_color1: bpy.props.FloatVectorProperty(
        name="Color 1", subtype='COLOR', min=0.0, max=1.0, size=4,
        default=(0.0, 0.0, 0.0, 1.0), update=_on_layer_update,
    )
    proc_color2: bpy.props.FloatVectorProperty(
        name="Color 2", subtype='COLOR', min=0.0, max=1.0, size=4,
        default=(1.0, 1.0, 1.0, 1.0), update=_on_layer_update,
    )

    # Noise / Musgrave
    proc_detail: FloatProperty(
        name="Detail", default=2.0, min=0.0, max=15.0,
        update=_on_layer_update,
    )
    proc_roughness_proc: FloatProperty(
        name="Roughness", default=0.5, min=0.0, max=1.0,
        update=_on_layer_update,
    )
    proc_distortion: FloatProperty(
        name="Distortion", default=0.0, min=-10.0, max=10.0,
        update=_on_layer_update,
    )
    proc_lacunarity: FloatProperty(
        name="Lacunarity", default=2.0, min=0.0, max=10.0,
        update=_on_layer_update,
    )

    # Voronoi
    proc_voronoi_feature: EnumProperty(
        name="Feature",
        items=[
            ('F1',           "F1",           "Distance to nearest point"),
            ('F2',           "F2",           "Distance to second nearest"),
            ('SMOOTH_F1',    "Smooth F1",    "Smooth minimum"),
            ('DISTANCE_TO_EDGE', "Edge",     "Distance to cell edge"),
            ('N_SPHERE_RADIUS', "Radius",    "N-sphere radius"),
        ],
        default='F1',
        update=_on_layer_update,
    )
    proc_voronoi_distance: EnumProperty(
        name="Distance",
        items=[
            ('EUCLIDEAN', "Euclidean", ""),
            ('MANHATTAN', "Manhattan", ""),
            ('CHEBYCHEV', "Chebychev", ""),
            ('MINKOWSKI', "Minkowski", ""),
        ],
        default='EUCLIDEAN',
        update=_on_layer_update,
    )
    proc_randomness: FloatProperty(
        name="Randomness", default=1.0, min=0.0, max=1.0,
        update=_on_layer_update,
    )

    # Wave
    proc_wave_type: EnumProperty(
        name="Wave Type",
        items=[
            ('BANDS', "Bands", "Parallel bands"),
            ('RINGS', "Rings", "Concentric rings"),
        ],
        default='BANDS',
        update=_on_layer_update,
    )
    proc_wave_profile: EnumProperty(
        name="Profile",
        items=[
            ('SIN',      "Sine",     ""),
            ('SAW',      "Sawtooth", ""),
            ('TRI',      "Triangle", ""),
        ],
        default='SIN',
        update=_on_layer_update,
    )
    proc_wave_detail_scale: FloatProperty(
        name="Detail Scale", default=1.0, min=0.0, max=10.0,
        update=_on_layer_update,
    )

    # Gradient
    proc_gradient_type: EnumProperty(
        name="Gradient Type",
        items=[
            ('LINEAR',     "Linear",     ""),
            ('QUADRATIC',  "Quadratic",  ""),
            ('EASING',     "Easing",     ""),
            ('DIAGONAL',   "Diagonal",   ""),
            ('SPHERICAL',  "Spherical",  ""),
            ('QUADRATIC_SPHERE', "Quad Sphere", ""),
            ('RADIAL',     "Radial",     ""),
        ],
        default='LINEAR',
        update=_on_layer_update,
    )

    # Checker
    proc_checker_scale: FloatProperty(
        name="Checker Scale", default=5.0, min=0.001, max=1000.0,
        update=_on_layer_update,
    )

    @property
    def image(self):
        """Convenience accessor for the Blender Image datablock."""
        return bpy.data.images.get(self.image_name)

    @property
    def mask_image(self):
        return bpy.data.images.get(self.mask_image_name)


# ─── Per-Material Settings ────────────────────────────────────────────────────

class TLM_MaterialProperties(PropertyGroup):
    """Attached to every bpy.types.Material as mat.tlm"""

    layers: CollectionProperty(
        name="Layers",
        type=TLM_LayerItem,
    )

    active_layer_index: IntProperty(
        name="Active Layer",
        default=0,
    )

    auto_composite: BoolProperty(
        name="Auto Composite",
        description="Rebuild node tree automatically when layers change",
        default=True,
    )

    # Resolution for new layers
    resolution: EnumProperty(
        name="New Layer Resolution",
        items=[
            ("512",  "512 × 512",   ""),
            ("1024", "1024 × 1024", ""),
            ("2048", "2048 × 2048", ""),
            ("4096", "4096 × 4096", ""),
        ],
        default="1024",
    )

    # UV map to use
    uv_map: StringProperty(
        name="UV Map",
        description="UV map used for all layers",
        default="UVMap",
    )

    @property
    def active_layer(self):
        if 0 <= self.active_layer_index < len(self.layers):
            return self.layers[self.active_layer_index]
        return None


# ─── Registration ─────────────────────────────────────────────────────────────

classes = [
    TLM_LayerItem,
    TLM_MaterialProperties,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Material.tlm = bpy.props.PointerProperty(type=TLM_MaterialProperties)


def unregister():
    del bpy.types.Material.tlm
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
