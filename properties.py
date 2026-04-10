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

# Set of material names that need rebuilding — accumulates across rapid changes.
_pending_materials: set = set()


def _do_deferred_rebuild():
    """Timer callback — runs once after the debounce interval."""
    global _pending_materials
    if not _pending_materials:
        return None
    # Snapshot and clear immediately so new triggers during rebuild
    # register a fresh timer.
    mats_to_rebuild = _pending_materials
    _pending_materials = set()
    try:
        for mat_name in mats_to_rebuild:
            mat = bpy.data.materials.get(mat_name)
            if mat and mat.tlm.auto_composite:
                compositing.rebuild_node_tree(mat)
        # Force shader editor redraw — timer callbacks don't
        # automatically trigger UI updates like operators do.
        ctx = bpy.context
        if ctx:
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
    global _pending_materials
    _pending_materials = set()


def _on_layer_update(self, context):
    """Called whenever a STRUCTURAL property changes. Triggers a debounced full rebuild."""
    global _pending_materials
    try:
        if not context or not context.active_object:
            return
        mat = context.active_object.active_material
        if not mat or not mat.tlm.auto_composite:
            return
        need_timer = not _pending_materials  # first material in this batch
        _pending_materials.add(mat.name)
        if need_timer:
            bpy.app.timers.register(_do_deferred_rebuild, first_interval=0.05)
    except ReferenceError:
        pass  # object or material was deleted mid-callback


def _make_hot_callback(prop_name):
    """Create a callback that attempts hot update, falling back to full rebuild."""
    def _cb(self, context):
        try:
            if not context or not context.active_object:
                return
            mat = context.active_object.active_material
            if not mat or not mat.tlm.auto_composite:
                return
            if not compositing.hot_update_property(mat, self, prop_name):
                _on_layer_update(self, context)
        except ReferenceError:
            pass  # object or material was deleted mid-callback
    return _cb


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
    ("PAINT",       "Paint",       "Regular paint layer with an image texture",  0),
    ("FILL",        "Fill",        "Solid color fill layer",                     1),
    ("ADJUSTMENT",  "Adjustment",  "Modifier layer: Hue/Sat, Levels, etc.",     2),
    ("GROUP",       "Group",       "Folder that contains other layers",          3),
    ("PROCEDURAL",  "Procedural",  "Shader-based procedural texture layer",     4),
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

    color_tag: EnumProperty(
        name="Color Tag",
        description="Color label for visual organization",
        items=[
            ('NONE',   "None",   "", 0),
            ('RED',    "Red",    "", 1),
            ('ORANGE', "Orange", "", 2),
            ('YELLOW', "Yellow", "", 3),
            ('GREEN',  "Green",  "", 4),
            ('BLUE',   "Blue",   "", 5),
            ('PURPLE', "Purple", "", 6),
            ('PINK',   "Pink",   "", 7),
        ],
        default='NONE',
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
        update=_make_hot_callback("opacity"),
    )

    blend_mode: EnumProperty(
        name="Blend Mode",
        description="How this layer blends with layers below",
        items=BLEND_MODES,
        default="MIX",
        update=_make_hot_callback("blend_mode"),
    )

    # Reference to the Blender Image datablock (by name, the Blender way)
    image_name: StringProperty(
        name="Image",
        description="Name of the bpy.data.images image for this layer",
        default="",
        update=_on_layer_update,
    )

    # Fill layer: solid color
    fill_color: bpy.props.FloatVectorProperty(
        name="Fill Color",
        description="Solid fill color for this layer",
        subtype='COLOR',
        min=0.0, max=1.0,
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        update=_make_hot_callback("fill_color"),
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
        update=_make_hot_callback("roughness_fill"),
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
        update=_make_hot_callback("metallic_fill"),
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
        update=_make_hot_callback("emission_color"),
    )
    emission_strength: FloatProperty(
        name="Emission Strength", description="Intensity multiplier for the emission effect",
        default=1.0, min=0.0, max=100.0,
        update=_make_hot_callback("emission_strength"),
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
        update=_make_hot_callback("transmission_fill"),
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
        update=_make_hot_callback("bump_strength"),
    )
    bump_distance: FloatProperty(
        name="Bump Distance", description="Scale of the bump displacement",
        default=0.05, min=0.001, max=1.0,
        update=_make_hot_callback("bump_distance"),
    )

    # UI state — collapsible PBR section
    show_pbr_channels: BoolProperty(
        name="Show PBR Channels",
        default=False,
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
            ('HUE_SAT',        "Hue/Saturation",    "Adjust hue, saturation and value",             0),
            ('BRIGHT_CONTRAST', "Brightness/Contrast","Adjust brightness and contrast",              1),
            ('LEVELS',         "Levels",             "Remap input/output tonal range",               2),
            ('COLOR_BALANCE',  "Color Balance",      "Lift / Gamma / Gain (cinematic grading)",      3),
            ('CURVES',         "Curves",             "Parametric RGB curve (contrast, brightness, tone clipping)", 4),
        ],
        default='HUE_SAT',
        update=_on_layer_update,
    )

    # Hue/Saturation/Value
    adj_hue: FloatProperty(
        name="Hue", description="Rotate hue — 0.5 is no change",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("adj_hue"),
    )
    adj_saturation: FloatProperty(
        name="Saturation", description="Saturation multiplier — 1.0 is no change, 0 is greyscale",
        default=1.0, min=0.0, max=2.0,
        update=_make_hot_callback("adj_saturation"),
    )
    adj_value: FloatProperty(
        name="Value", description="Value/brightness multiplier — 1.0 is no change",
        default=1.0, min=0.0, max=2.0,
        update=_make_hot_callback("adj_value"),
    )

    # Brightness/Contrast
    adj_brightness: FloatProperty(
        name="Brightness", description="Shift brightness up or down",
        default=0.0, min=-1.0, max=1.0,
        update=_make_hot_callback("adj_brightness"),
    )
    adj_contrast: FloatProperty(
        name="Contrast", description="Increase or decrease contrast",
        default=0.0, min=-1.0, max=1.0,
        update=_make_hot_callback("adj_contrast"),
    )

    # Levels
    adj_in_min: FloatProperty(
        name="Input Black", description="Black point of the input tonal range",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("adj_in_min"),
    )
    adj_in_max: FloatProperty(
        name="Input White", description="White point of the input tonal range",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("adj_in_max"),
    )
    adj_levels_gamma: FloatProperty(
        name="Gamma (Midtones)", description="Midtone gamma correction",
        default=1.0, min=0.1, max=10.0,
        update=_make_hot_callback("adj_levels_gamma"),
    )
    adj_out_min: FloatProperty(
        name="Output Black", description="Black point of the output tonal range",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("adj_out_min"),
    )
    adj_out_max: FloatProperty(
        name="Output White", description="White point of the output tonal range",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("adj_out_max"),
    )

    # Color Balance (Lift / Gamma / Gain)
    adj_lift: bpy.props.FloatVectorProperty(
        name="Lift", description="Shadows color correction",
        subtype='COLOR', min=0.0, max=2.0, size=3,
        default=(1.0, 1.0, 1.0), update=_make_hot_callback("adj_lift"),
    )
    adj_gamma: bpy.props.FloatVectorProperty(
        name="Gamma", description="Midtones color correction",
        subtype='COLOR', min=0.0, max=2.0, size=3,
        default=(1.0, 1.0, 1.0), update=_make_hot_callback("adj_gamma"),
    )
    adj_gain: bpy.props.FloatVectorProperty(
        name="Gain", description="Highlights color correction",
        subtype='COLOR', min=0.0, max=2.0, size=3,
        default=(1.0, 1.0, 1.0), update=_make_hot_callback("adj_gain"),
    )

    # Curves (parametric)
    adj_curve_contrast: FloatProperty(
        name="Contrast",
        description="S-curve contrast: positive increases contrast, negative decreases",
        default=0.0, min=-1.0, max=1.0,
        update=_make_hot_callback("adj_curve_contrast"),
    )
    adj_curve_brightness: FloatProperty(
        name="Brightness",
        description="Shift midpoint of curve up or down",
        default=0.0, min=-1.0, max=1.0,
        update=_make_hot_callback("adj_curve_brightness"),
    )
    adj_curve_black_point: FloatProperty(
        name="Black Point",
        description="Raise shadows — crush blacks by lifting the low end",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("adj_curve_black_point"),
    )
    adj_curve_white_point: FloatProperty(
        name="White Point",
        description="Lower highlights — clip whites by pulling down the high end",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("adj_curve_white_point"),
    )

    # ── Procedural layer properties ───────────────────────────────────────────

    proc_type: EnumProperty(
        name="Type",
        items=[
            ('NOISE',    "Noise",    "Perlin/FBM noise",                                    0),
            ('VORONOI',  "Voronoi",  "Cell/Worley noise",                                   1),
            ('WAVE',     "Wave",     "Sine wave bands or rings",                             2),
            ('GRADIENT', "Gradient", "Linear, radial, quadratic or spherical gradient",      3),
            ('MUSGRAVE', "Musgrave", "Fractal noise (Multifractal, Ridged, etc.)",           4),
            ('CHECKER',  "Checker",  "Alternating checkerboard pattern",                     5),
            ('MARBLE',   "Marble",   "Wave bands distorted by noise — marble/veined stone", 6),
            ('CLOUDS',   "Clouds",   "Soft billowy noise — clouds, smoke, organic shapes",  7),
        ],
        default='NOISE',
        update=_on_layer_update,
    )

    # Shared: scale, mapping offset/rotation
    proc_scale: FloatProperty(
        name="Scale", description="Overall scale of the procedural texture",
        default=5.0, min=0.001, max=1000.0,
        update=_make_hot_callback("proc_scale"),
    )
    proc_offset_x: FloatProperty(name="Offset X", description="Offset texture origin along X",
        default=0.0, update=_make_hot_callback("proc_offset_x"))
    proc_offset_y: FloatProperty(name="Offset Y", description="Offset texture origin along Y",
        default=0.0, update=_make_hot_callback("proc_offset_y"))
    proc_offset_z: FloatProperty(name="Offset Z", description="Offset texture origin along Z",
        default=0.0, update=_make_hot_callback("proc_offset_z"))

    # Colors (Color1 = dark/base, Color2 = bright/accent)
    proc_color1: bpy.props.FloatVectorProperty(
        name="Color 1", description="Dark/base color of the procedural gradient",
        subtype='COLOR', min=0.0, max=1.0, size=4,
        default=(0.0, 0.0, 0.0, 1.0), update=_make_hot_callback("proc_color1"),
    )
    proc_color2: bpy.props.FloatVectorProperty(
        name="Color 2", description="Bright/accent color of the procedural gradient",
        subtype='COLOR', min=0.0, max=1.0, size=4,
        default=(1.0, 1.0, 1.0, 1.0), update=_make_hot_callback("proc_color2"),
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
        default=(0.5, 0.5, 0.5, 1.0), update=_make_hot_callback("proc_color3"),
    )
    proc_color3_position: FloatProperty(
        name="Color 3 Pos",
        description="Position of the third color stop (0 = at Color1, 1 = at Color2)",
        default=0.5, min=0.01, max=0.99, subtype='FACTOR',
        update=_make_hot_callback("proc_color3_position"),
    )

    # Noise / Musgrave
    proc_detail: FloatProperty(
        name="Detail", description="Number of noise octaves — more detail means finer grain",
        default=2.0, min=0.0, max=15.0,
        update=_make_hot_callback("proc_detail"),
    )
    proc_roughness_proc: FloatProperty(
        name="Roughness", description="Blending roughness between noise octaves",
        default=0.5, min=0.0, max=1.0,
        update=_make_hot_callback("proc_roughness_proc"),
    )
    proc_distortion: FloatProperty(
        name="Distortion", description="Amount of distortion applied to the texture",
        default=0.0, min=-10.0, max=10.0,
        update=_make_hot_callback("proc_distortion"),
    )
    proc_lacunarity: FloatProperty(
        name="Lacunarity", description="Gap between successive noise octaves",
        default=2.0, min=0.0, max=10.0,
        update=_make_hot_callback("proc_lacunarity"),
    )

    # Voronoi
    proc_voronoi_feature: EnumProperty(
        name="Feature",
        items=[
            ('F1',           "F1",           "Distance to nearest point",    0),
            ('F2',           "F2",           "Distance to second nearest",   1),
            ('SMOOTH_F1',    "Smooth F1",    "Smooth minimum",               2),
            ('DISTANCE_TO_EDGE', "Edge",     "Distance to cell edge",        3),
            ('N_SPHERE_RADIUS', "Radius",    "N-sphere radius",              4),
        ],
        default='F1',
        update=_on_layer_update,
    )
    proc_voronoi_distance: EnumProperty(
        name="Distance",
        items=[
            ('EUCLIDEAN', "Euclidean", "Standard straight-line distance",    0),
            ('MANHATTAN', "Manhattan", "Grid-based taxi-cab distance",       1),
            ('CHEBYCHEV', "Chebychev", "Maximum of axis distances",         2),
            ('MINKOWSKI', "Minkowski", "Generalized distance metric",       3),
        ],
        default='EUCLIDEAN',
        update=_on_layer_update,
    )
    proc_randomness: FloatProperty(
        name="Randomness", description="Randomness of Voronoi cell positions",
        default=1.0, min=0.0, max=1.0,
        update=_make_hot_callback("proc_randomness"),
    )

    # Wave
    proc_wave_type: EnumProperty(
        name="Wave Type",
        items=[
            ('BANDS', "Bands", "Parallel bands",     0),
            ('RINGS', "Rings", "Concentric rings",    1),
        ],
        default='BANDS',
        update=_on_layer_update,
    )
    proc_wave_profile: EnumProperty(
        name="Profile",
        items=[
            ('SIN',      "Sine",     "Smooth sine wave",         0),
            ('SAW',      "Sawtooth", "Sharp sawtooth ramp",      1),
            ('TRI',      "Triangle", "Triangular zigzag wave",   2),
        ],
        default='SIN',
        update=_on_layer_update,
    )
    proc_wave_detail_scale: FloatProperty(
        name="Detail Scale", description="Scale of the detail noise overlaid on the wave",
        default=1.0, min=0.0, max=10.0,
        update=_make_hot_callback("proc_wave_detail_scale"),
    )

    # Gradient
    proc_gradient_type: EnumProperty(
        name="Gradient Type",
        items=[
            ('LINEAR',     "Linear",     "Straight linear gradient",             0),
            ('QUADRATIC',  "Quadratic",  "Quadratic falloff gradient",           1),
            ('EASING',     "Easing",     "Smooth ease-in/ease-out",             2),
            ('DIAGONAL',   "Diagonal",   "Diagonal corner-to-corner",           3),
            ('SPHERICAL',  "Spherical",  "Spherical radial falloff",            4),
            ('QUADRATIC_SPHERE', "Quad Sphere", "Quadratic spherical falloff",  5),
            ('RADIAL',     "Radial",     "Angular radial sweep",                6),
        ],
        default='LINEAR',
        update=_on_layer_update,
    )

    # Checker
    proc_checker_scale: FloatProperty(
        name="Checker Scale", description="Size of the checker squares",
        default=5.0, min=0.001, max=1000.0,
        update=_make_hot_callback("proc_checker_scale"),
    )

    # Marble
    proc_marble_distortion: FloatProperty(
        name="Turbulence",
        description="Amount of noise distortion applied to the wave bands",
        default=2.0, min=0.0, max=20.0,
        update=_make_hot_callback("proc_marble_distortion"),
    )
    proc_marble_wave_type: EnumProperty(
        name="Pattern",
        description="Marble band pattern",
        items=[
            ('BANDS', "Bands", "Parallel marble veins",      0),
            ('RINGS', "Rings", "Concentric marble rings",     1),
        ],
        default='BANDS',
        update=_on_layer_update,
    )

    proc_coord_type: EnumProperty(
        name="Coordinates",
        description="Texture coordinate space for procedural patterns",
        items=[
            ('GENERATED', "Generated", "Normalized to object bounding box (0-1). Consistent across different objects", 0),
            ('OBJECT',    "Object",    "World-space object coordinates. Pattern changes with object size/position",    1),
            ('UV',        "UV",        "UV map coordinates. Follows UV unwrap, may show seams",                        2),
        ],
        default='GENERATED',
        update=_on_layer_update,
    )

    proc_contrast: FloatProperty(
        name="Contrast",
        description="Controls how sharp the transition between Color1 and Color2 is. "
                    "Low = soft gradient, High = hard edge",
        default=0.5, min=0.0, max=1.0,
        update=_make_hot_callback("proc_contrast"),
    )

    # Vector coordinate distortion — inject Noise into texture coordinates
    # for organic, non-geometric patterns (e.g. warped Voronoi cracks)
    proc_vector_distortion: FloatProperty(
        name="Vector Distortion",
        description="Distort texture coordinates with Noise for organic patterns. "
                    "0 = no distortion, higher = more warped",
        default=0.0, min=0.0, max=2.0,
        update=_make_hot_callback("proc_vector_distortion"),
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
        update=_make_hot_callback("fresnel_ior"),
    )
    fresnel_strength: FloatProperty(
        name="Fresnel Strength",
        description="How strongly the Fresnel mask affects this layer. "
                    "1.0 = full Fresnel, 0.0 = no effect",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("fresnel_strength"),
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

    # Solo layer — isolate one layer without modifying visibility states
    solo_layer_index: IntProperty(
        name="Solo Layer",
        description="Index of the solo'd layer (-1 = off)",
        default=-1,
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
    # Cancel any pending rebuild timer
    if bpy.app.timers.is_registered(_do_deferred_rebuild):
        bpy.app.timers.unregister(_do_deferred_rebuild)
    global _pending_materials
    _pending_materials = set()

    del bpy.types.Material.tlm
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
