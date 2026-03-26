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
    ("EXCLUSION",  "Exclusion",  "Inversion-like difference blend",   15),
    ("HUE",        "Hue",        "Apply hue from this layer",         16),
    ("SATURATION", "Saturation", "Apply saturation from this layer",  17),
    ("COLOR",      "Color",      "Apply hue and saturation, keep luminosity", 18),
    ("LUMINOSITY", "Luminosity", "Apply luminosity, keep hue and saturation", 19),
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
        description="Toggle layer visibility in the composite",
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
        description="Layer opacity — 0 is fully transparent, 1 is fully opaque",
        min=0.0, max=1.0,
        default=1.0,
        subtype='FACTOR',
        update=_on_layer_update,
    )

    blend_mode: EnumProperty(
        name="Blend Mode",
        description="How this layer blends with layers below",
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
        description="Solid fill color for this layer",
        subtype='COLOR',
        min=0.0, max=1.0,
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        update=_on_layer_update,
    )

    # Optional mask
    use_mask: BoolProperty(
        name="Use Mask",
        description="Enable a paint mask to restrict where this layer is visible",
        default=False,
        update=_on_layer_update,
    )

    mask_image_name: StringProperty(
        name="Mask Image",
        description="Image controlling where this layer is visible (white = visible)",
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
        name="Scale", description="Scale of the triplanar projection",
        default=1.0, min=0.001, max=100.0,
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
    use_roughness: BoolProperty(name="Roughness", description="Enable roughness channel for this layer",
        default=False, update=_on_layer_update)
    roughness_image_name: StringProperty(name="Roughness Image",
        description="Image texture for the roughness channel", default="",
        update=_on_layer_update)
    roughness_fill: FloatProperty(
        name="Roughness", description="Constant roughness value (0 = smooth, 1 = rough)",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )

    # Metallic
    use_metallic: BoolProperty(name="Metallic", description="Enable metallic channel for this layer",
        default=False, update=_on_layer_update)
    metallic_image_name: StringProperty(name="Metallic Image",
        description="Image texture for the metallic channel", default="",
        update=_on_layer_update)
    metallic_fill: FloatProperty(
        name="Metallic", description="Constant metallic value (0 = dielectric, 1 = metal)",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )

    # Normal Map
    use_normal: BoolProperty(name="Normal", description="Enable normal map channel for this layer",
        default=False, update=_on_layer_update)
    normal_image_name: StringProperty(name="Normal Image",
        description="Normal map image for this layer", default="",
        update=_on_layer_update)
    normal_strength: FloatProperty(
        name="Normal Strength", description="Strength of the normal map effect",
        default=1.0, min=0.0, max=5.0,
        update=_on_layer_update,
    )

    # Emission
    use_emission: BoolProperty(name="Emission", description="Enable emission (glow) channel for this layer",
        default=False, update=_on_layer_update)
    emission_image_name: StringProperty(name="Emission Image",
        description="Image texture for the emission channel", default="",
        update=_on_layer_update)
    emission_color: bpy.props.FloatVectorProperty(
        name="Emission Color", description="Emission color when no image is assigned",
        subtype='COLOR',
        min=0.0, max=1.0, size=4, default=(1.0, 1.0, 1.0, 1.0),
        update=_on_layer_update,
    )
    emission_strength: FloatProperty(
        name="Emission Strength", description="Intensity multiplier for the emission effect",
        default=1.0, min=0.0, max=100.0,
        update=_on_layer_update,
    )

    # Transmission (Glass/Transparency)
    use_transmission: BoolProperty(name="Transmission",
        description="Enable transmission channel for this layer (glass/transparency effect)",
        default=False, update=_on_layer_update)
    transmission_image_name: StringProperty(name="Transmission Image",
        description="Image texture for the transmission channel", default="",
        update=_on_layer_update)
    transmission_fill: FloatProperty(
        name="Transmission", description="Constant transmission value (0 = opaque, 1 = fully transparent/glass)",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
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
        name="Bump Strength", description="How strongly the bump displaces the surface",
        default=0.5, min=0.0, max=5.0,
        update=_on_layer_update,
    )
    bump_distance: FloatProperty(
        name="Bump Distance", description="Scale of the bump displacement",
        default=0.05, min=0.001, max=1.0,
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

    @property
    def transmission_image(self):
        return bpy.data.images.get(self.transmission_image_name)

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
            ('CURVES',         "Curves",             "Parametric RGB curve (contrast, brightness, tone clipping)"),
        ],
        default='HUE_SAT',
        update=_on_layer_update,
    )

    # Hue/Saturation/Value
    adj_hue: FloatProperty(
        name="Hue", description="Rotate hue — 0.5 is no change",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )
    adj_saturation: FloatProperty(
        name="Saturation", description="Saturation multiplier — 1.0 is no change, 0 is greyscale",
        default=1.0, min=0.0, max=2.0,
        update=_on_layer_update,
    )
    adj_value: FloatProperty(
        name="Value", description="Value/brightness multiplier — 1.0 is no change",
        default=1.0, min=0.0, max=2.0,
        update=_on_layer_update,
    )

    # Brightness/Contrast
    adj_brightness: FloatProperty(
        name="Brightness", description="Shift brightness up or down",
        default=0.0, min=-1.0, max=1.0,
        update=_on_layer_update,
    )
    adj_contrast: FloatProperty(
        name="Contrast", description="Increase or decrease contrast",
        default=0.0, min=-1.0, max=1.0,
        update=_on_layer_update,
    )

    # Levels
    adj_in_min: FloatProperty(
        name="Input Black", description="Black point of the input tonal range",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )
    adj_in_max: FloatProperty(
        name="Input White", description="White point of the input tonal range",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )
    adj_levels_gamma: FloatProperty(
        name="Gamma (Midtones)", description="Midtone gamma correction",
        default=1.0, min=0.1, max=10.0,
        update=_on_layer_update,
    )
    adj_out_min: FloatProperty(
        name="Output Black", description="Black point of the output tonal range",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )
    adj_out_max: FloatProperty(
        name="Output White", description="White point of the output tonal range",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )

    # Color Balance (Lift / Gamma / Gain)
    adj_lift: bpy.props.FloatVectorProperty(
        name="Lift", description="Shadows color correction",
        subtype='COLOR', min=0.0, max=2.0, size=3,
        default=(1.0, 1.0, 1.0), update=_on_layer_update,
    )
    adj_gamma: bpy.props.FloatVectorProperty(
        name="Gamma", description="Midtones color correction",
        subtype='COLOR', min=0.0, max=2.0, size=3,
        default=(1.0, 1.0, 1.0), update=_on_layer_update,
    )
    adj_gain: bpy.props.FloatVectorProperty(
        name="Gain", description="Highlights color correction",
        subtype='COLOR', min=0.0, max=2.0, size=3,
        default=(1.0, 1.0, 1.0), update=_on_layer_update,
    )

    # Curves (parametric)
    adj_curve_contrast: FloatProperty(
        name="Contrast",
        description="S-curve contrast: positive increases contrast, negative decreases",
        default=0.0, min=-1.0, max=1.0,
        update=_on_layer_update,
    )
    adj_curve_brightness: FloatProperty(
        name="Brightness",
        description="Shift midpoint of curve up or down",
        default=0.0, min=-1.0, max=1.0,
        update=_on_layer_update,
    )
    adj_curve_black_point: FloatProperty(
        name="Black Point",
        description="Raise shadows — crush blacks by lifting the low end",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )
    adj_curve_white_point: FloatProperty(
        name="White Point",
        description="Lower highlights — clip whites by pulling down the high end",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
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
            ('MARBLE',   "Marble",   "Wave bands distorted by noise — marble/veined stone"),
            ('CLOUDS',   "Clouds",   "Soft billowy noise — clouds, smoke, organic shapes"),
        ],
        default='NOISE',
        update=_on_layer_update,
    )

    # Shared: scale, mapping offset/rotation
    proc_scale: FloatProperty(
        name="Scale", description="Overall scale of the procedural texture",
        default=5.0, min=0.001, max=1000.0,
        update=_on_layer_update,
    )
    proc_offset_x: FloatProperty(name="Offset X", description="Offset texture origin along X",
        default=0.0, update=_on_layer_update)
    proc_offset_y: FloatProperty(name="Offset Y", description="Offset texture origin along Y",
        default=0.0, update=_on_layer_update)
    proc_offset_z: FloatProperty(name="Offset Z", description="Offset texture origin along Z",
        default=0.0, update=_on_layer_update)

    # Colors (Color1 = dark/base, Color2 = bright/accent)
    proc_color1: bpy.props.FloatVectorProperty(
        name="Color 1", description="Dark/base color of the procedural gradient",
        subtype='COLOR', min=0.0, max=1.0, size=4,
        default=(0.0, 0.0, 0.0, 1.0), update=_on_layer_update,
    )
    proc_color2: bpy.props.FloatVectorProperty(
        name="Color 2", description="Bright/accent color of the procedural gradient",
        subtype='COLOR', min=0.0, max=1.0, size=4,
        default=(1.0, 1.0, 1.0, 1.0), update=_on_layer_update,
    )

    # Optional third color stop
    use_proc_color3: BoolProperty(
        name="Use Color 3",
        description="Enable a third color stop in the procedural gradient",
        default=False, update=_on_layer_update,
    )
    proc_color3: bpy.props.FloatVectorProperty(
        name="Color 3", description="Middle color of the procedural gradient (between Color1 and Color2)",
        subtype='COLOR', min=0.0, max=1.0, size=4,
        default=(0.5, 0.5, 0.5, 1.0), update=_on_layer_update,
    )
    proc_color3_position: FloatProperty(
        name="Color 3 Pos",
        description="Position of the third color stop (0 = at Color1, 1 = at Color2)",
        default=0.5, min=0.01, max=0.99, subtype='FACTOR',
        update=_on_layer_update,
    )

    # Noise / Musgrave
    proc_detail: FloatProperty(
        name="Detail", description="Number of noise octaves — more detail means finer grain",
        default=2.0, min=0.0, max=15.0,
        update=_on_layer_update,
    )
    proc_roughness_proc: FloatProperty(
        name="Roughness", description="Blending roughness between noise octaves",
        default=0.5, min=0.0, max=1.0,
        update=_on_layer_update,
    )
    proc_distortion: FloatProperty(
        name="Distortion", description="Amount of distortion applied to the texture",
        default=0.0, min=-10.0, max=10.0,
        update=_on_layer_update,
    )
    proc_lacunarity: FloatProperty(
        name="Lacunarity", description="Gap between successive noise octaves",
        default=2.0, min=0.0, max=10.0,
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
            ('EUCLIDEAN', "Euclidean", "Standard straight-line distance"),
            ('MANHATTAN', "Manhattan", "Grid-based taxi-cab distance"),
            ('CHEBYCHEV', "Chebychev", "Maximum of axis distances"),
            ('MINKOWSKI', "Minkowski", "Generalized distance metric"),
        ],
        default='EUCLIDEAN',
        update=_on_layer_update,
    )
    proc_randomness: FloatProperty(
        name="Randomness", description="Randomness of Voronoi cell positions",
        default=1.0, min=0.0, max=1.0,
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
            ('SIN',      "Sine",     "Smooth sine wave"),
            ('SAW',      "Sawtooth", "Sharp sawtooth ramp"),
            ('TRI',      "Triangle", "Triangular zigzag wave"),
        ],
        default='SIN',
        update=_on_layer_update,
    )
    proc_wave_detail_scale: FloatProperty(
        name="Detail Scale", description="Scale of the detail noise overlaid on the wave",
        default=1.0, min=0.0, max=10.0,
        update=_on_layer_update,
    )

    # Gradient
    proc_gradient_type: EnumProperty(
        name="Gradient Type",
        items=[
            ('LINEAR',     "Linear",     "Straight linear gradient"),
            ('QUADRATIC',  "Quadratic",  "Quadratic falloff gradient"),
            ('EASING',     "Easing",     "Smooth ease-in/ease-out"),
            ('DIAGONAL',   "Diagonal",   "Diagonal corner-to-corner"),
            ('SPHERICAL',  "Spherical",  "Spherical radial falloff"),
            ('QUADRATIC_SPHERE', "Quad Sphere", "Quadratic spherical falloff"),
            ('RADIAL',     "Radial",     "Angular radial sweep"),
        ],
        default='LINEAR',
        update=_on_layer_update,
    )

    # Checker
    proc_checker_scale: FloatProperty(
        name="Checker Scale", description="Size of the checker squares",
        default=5.0, min=0.001, max=1000.0,
        update=_on_layer_update,
    )

    # Marble
    proc_marble_distortion: FloatProperty(
        name="Turbulence",
        description="Amount of noise distortion applied to the wave bands",
        default=2.0, min=0.0, max=20.0,
        update=_on_layer_update,
    )
    proc_marble_wave_type: EnumProperty(
        name="Pattern",
        description="Marble band pattern",
        items=[
            ('BANDS', "Bands", "Parallel marble veins"),
            ('RINGS', "Rings", "Concentric marble rings"),
        ],
        default='BANDS',
        update=_on_layer_update,
    )

    proc_coord_type: EnumProperty(
        name="Coordinates",
        description="Texture coordinate space for procedural patterns",
        items=[
            ('GENERATED', "Generated", "Normalized to object bounding box (0-1). Consistent across different objects"),
            ('OBJECT',    "Object",    "World-space object coordinates. Pattern changes with object size/position"),
            ('UV',        "UV",        "UV map coordinates. Follows UV unwrap, may show seams"),
        ],
        default='GENERATED',
        update=_on_layer_update,
    )

    proc_contrast: FloatProperty(
        name="Contrast",
        description="Controls how sharp the transition between Color1 and Color2 is. "
                    "Low = soft gradient, High = hard edge",
        default=0.5, min=0.0, max=1.0,
        update=_on_layer_update,
    )

    # Vector coordinate distortion — inject Noise into texture coordinates
    # for organic, non-geometric patterns (e.g. warped Voronoi cracks)
    proc_vector_distortion: FloatProperty(
        name="Vector Distortion",
        description="Distort texture coordinates with Noise for organic patterns. "
                    "0 = no distortion, higher = more warped",
        default=0.0, min=0.0, max=2.0,
        update=_on_layer_update,
    )

    # Less Than threshold for emission mask — binary crack detection
    proc_emission_threshold: FloatProperty(
        name="Emission Threshold",
        description="Distance threshold for emission mask. "
                    "Values below this distance are lit. 0 = use Power sharpening instead",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )

    # Fresnel mask — edge glow based on viewing angle
    use_fresnel_mask: BoolProperty(
        name="Fresnel Mask",
        description="Apply a Fresnel (viewing angle) mask to this layer. "
                    "Creates edge glow / rim lighting effects",
        default=False,
        update=_on_layer_update,
    )
    fresnel_ior: FloatProperty(
        name="Fresnel IOR",
        description="Index of refraction for the Fresnel effect. "
                    "Lower = wider edge effect, Higher = narrower edge",
        default=1.45, min=1.0, max=5.0,
        update=_on_layer_update,
    )
    fresnel_strength: FloatProperty(
        name="Fresnel Strength",
        description="How strongly the Fresnel mask affects this layer. "
                    "1.0 = full Fresnel, 0.0 = no effect",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
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
            ("512",  "512 × 512",   "Low resolution, fast performance"),
            ("1024", "1024 × 1024", "Standard resolution for most use cases"),
            ("2048", "2048 × 2048", "High resolution for detailed textures"),
            ("4096", "4096 × 4096", "Ultra-high resolution, may be slow"),
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
