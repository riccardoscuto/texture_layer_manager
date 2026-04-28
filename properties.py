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
# Multiple rapid changes within the debounce window are batched into a single rebuild.
# 180ms is a balance: feels snappy to the eye, absorbs a slider drag
# (~60Hz ticks from Blender) into ~5 rebuilds per 1-second drag instead of ~20.

_REBUILD_DEBOUNCE_S = 0.18

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

    Empties the pending set AND unregisters the timer. The previous version
    only emptied the set — the timer still fired and was a no-op, but until
    it fired (~200ms) it could race with the explicit rebuild.
    """
    global _pending_materials
    _pending_materials = set()
    # bpy.app.timers identifies a registered timer by the function object,
    # so we can unregister _do_deferred_rebuild directly.
    try:
        if bpy.app.timers.is_registered(_do_deferred_rebuild):
            bpy.app.timers.unregister(_do_deferred_rebuild)
    except (RuntimeError, AttributeError):
        # Timers API can be unavailable during register/unregister of the
        # addon itself, or right after a .blend reload — fail silent.
        pass


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
            bpy.app.timers.register(_do_deferred_rebuild, first_interval=_REBUILD_DEBOUNCE_S)
    except ReferenceError:
        pass  # object or material was deleted mid-callback


def _on_preset_change(self, context):
    """Apply coordinate preset — sets coord_type, normalize, and distortion in one click."""
    preset = self.proc_coord_preset
    if preset == 'SPHERICAL':
        self.proc_coord_type = 'OBJECT'
        self.proc_normalize_coords = True
        self.proc_vector_distortion = 0.15
    elif preset == 'SURFACE':
        self.proc_coord_type = 'GENERATED'
        self.proc_normalize_coords = False
    elif preset == 'UV_DRIVEN':
        self.proc_coord_type = 'UV'
        self.proc_normalize_coords = False
    # 'CUSTOM' = no-op, user has full manual control
    if preset != 'CUSTOM':
        _on_layer_update(self, context)


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

# Per-channel blend override: same items as BLEND_MODES, plus 'INHERIT' at index 0.
# INHERIT means "use the layer's main blend_mode".
# Enables branching: one layer can MULTIPLY on base_color but OVERLAY on roughness.
BLEND_MODES_OVERRIDE = [
    ("INHERIT", "Inherit (Layer)", "Use the layer's main blend mode", 0),
] + [(idt, nm, ds, i + 1) for (idt, nm, ds, i) in BLEND_MODES]

LAYER_TYPES = [
    ("PAINT",       "Paint",       "Regular paint layer with an image texture",  0),
    ("FILL",        "Fill",        "Solid color fill layer",                     1),
    ("ADJUSTMENT",  "Adjustment",  "Modifier layer: Hue/Sat, Levels, etc.",     2),
    ("GROUP",       "Group",       "Folder that contains other layers",          3),
    ("PROCEDURAL",  "Procedural",  "Shader-based procedural texture layer",     4),
    ("REFERENCE",   "Reference",   "Reuse another layer's output with independent blend/mask/channels", 5),
]


# ─── Single Layer ─────────────────────────────────────────────────────────────

class TLM_LayerItem(PropertyGroup):
    """Represents a single texture layer."""

    # Override the implicit PropertyGroup `name` so renames trigger a rebuild.
    # The rebuild re-labels the layer's NodeFrame in the shader editor, keeping
    # the frame label in sync with the UI name. Debounced (180ms) like every
    # other structural update.
    name: StringProperty(
        name="Name",
        default="Layer",
        update=_on_layer_update,
    )

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
        description=(
            "Prevent edits: grays out all layer parameters (blend, opacity, "
            "channels, mask, procedural params, branching) and blocks remove / "
            "move / paint-mode activation. Duplicate is still allowed. "
            "Visibility and solo toggles remain usable"
        ),
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

    # ── Per-channel blend mode overrides (Branching) ─────────────────────
    # Default INHERIT means "use the main blend_mode above". Setting any
    # other value lets a single layer have DIFFERENT blending per channel.
    # Example: a Voronoi layer that MULTIPLY-darkens base_color grooves
    # while OVERLAY-blending roughness patches.
    # Structural update (full rebuild) because each change rewires a mix node.
    blend_mode_base_color: EnumProperty(
        name="BaseColor Blend",
        description="Override blend mode on base color channel only",
        items=BLEND_MODES_OVERRIDE, default="INHERIT",
        update=_on_layer_update,
    )
    blend_mode_roughness: EnumProperty(
        name="Roughness Blend",
        description="Override blend mode on roughness channel only",
        items=BLEND_MODES_OVERRIDE, default="INHERIT",
        update=_on_layer_update,
    )
    blend_mode_metallic: EnumProperty(
        name="Metallic Blend",
        description="Override blend mode on metallic channel only",
        items=BLEND_MODES_OVERRIDE, default="INHERIT",
        update=_on_layer_update,
    )
    blend_mode_emission: EnumProperty(
        name="Emission Blend",
        description="Override blend mode on emission channel only",
        items=BLEND_MODES_OVERRIDE, default="INHERIT",
        update=_on_layer_update,
    )
    blend_mode_transmission: EnumProperty(
        name="Transmission Blend",
        description="Override blend mode on transmission channel only",
        items=BLEND_MODES_OVERRIDE, default="INHERIT",
        update=_on_layer_update,
    )
    blend_mode_alpha: EnumProperty(
        name="Alpha Blend",
        description="Override blend mode on alpha channel only",
        items=BLEND_MODES_OVERRIDE, default="INHERIT",
        update=_on_layer_update,
    )

    # ── Output channel routing ──────────────────────────────────────────────
    # Quick-select: route this layer to ONE specific BSDF input, bypassing
    # the use_<channel> toggles. AUTO (default) = use the toggles as before.
    # Lets users drop a procedural Checker / Voronoi at any layer position
    # and aim it at Roughness / Metallic / Alpha without configuring 6 flags.
    output_channel: EnumProperty(
        name="Output Channel",
        description="Where this layer's output goes on the Principled BSDF",
        items=[
            ('AUTO',          "Auto",          "Use the per-channel use_X toggles below (default behavior)"),
            ('BASE_COLOR',    "Base Color",    "Send this layer ONLY to Base Color (ignore other use_X toggles)"),
            ('ROUGHNESS',     "Roughness",     "Send this layer ONLY to Roughness"),
            ('METALLIC',      "Metallic",      "Send this layer ONLY to Metallic"),
            ('ALPHA',         "Alpha",         "Send this layer ONLY to Alpha (surface opacity)"),
        ],
        default='AUTO',
        update=_on_layer_update,
    )

    # Reference to the Blender Image datablock (by name, the Blender way)
    image_name: StringProperty(
        name="Image",
        description="Name of the bpy.data.images image for this layer",
        default="",
        update=_make_hot_callback("image_name"),
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

    # ── Advanced combinable masks ────────────────────────────────────────
    # Two composable mask slots (A and B) plus a combine operation.
    # Each slot can pull from IMAGE, AO (ambient occlusion) or POINTINESS
    # (geometry-derived curvature/edge detection).  This unlocks physical
    # masking like "rust only on edges AND where dirt noise is heavy".
    use_mask: BoolProperty(
        name="Use Mask",
        description="Enable a paint mask to restrict where this layer is visible",
        default=False,
        update=_on_layer_update,
    )

    mask_source: EnumProperty(
        name="Mask A Source",
        description="Where the primary mask value comes from",
        items=[
            ('IMAGE',      "Image",      "Use a painted image as mask",                    0),
            ('AO',         "Ambient Occlusion", "Cavity mask from Ambient Occlusion — dark in recesses", 1),
            ('POINTINESS', "Pointiness", "Geometry curvature — bright on convex edges, dark in concavities", 2),
            # ── Smart generators (LIVE): physics-based masks with noise breakup, ──
            # ── evaluated every shader sample. For BAKED alternatives use the    ──
            # ── "Bake Smart Mask" button below.                                  ──
            ('EDGE_WEAR',       "Edge Wear (Live)",       "Pointiness convex edges + noise breakup + sharpness — simulates worn-out edges (real-time)", 3),
            ('DIRT',            "Dirt (Live)",            "Inverted AO × noise grunge — accumulates in cavities with organic variation (real-time)",   4),
            ('CURVATURE_SMART', "Curvature (Live)",       "Bipolar pointiness (both convex + concave) with threshold — highlights all edges (real-time)", 5),
        ],
        default='IMAGE',
        update=_on_layer_update,
    )

    mask_image_name: StringProperty(
        name="Mask Image",
        description="Image controlling where this layer is visible (white = visible)",
        default="",
        update=_on_layer_update,
    )

    mask_invert: BoolProperty(
        name="Invert Mask A",
        description="Invert the primary mask (white↔black)",
        default=False,
        update=_on_layer_update,
    )

    mask_ao_distance: FloatProperty(
        name="AO Distance A",
        description="Maximum distance for AO ray in mask A. Larger = broader cavities",
        default=0.5, min=0.01, max=10.0,
        update=_make_hot_callback("mask_ao_distance"),
    )

    # ── Secondary mask (combines with primary) ──
    use_mask_b: BoolProperty(
        name="Use Secondary Mask",
        description="Enable a second mask combined with the first (AND/OR/etc.)",
        default=False,
        update=_on_layer_update,
    )

    mask_source_b: EnumProperty(
        name="Mask B Source",
        description="Where the secondary mask value comes from",
        items=[
            ('IMAGE',      "Image",      "Use a painted image as mask",                    0),
            ('AO',         "Ambient Occlusion", "Cavity mask from Ambient Occlusion",                   1),
            ('POINTINESS', "Pointiness", "Geometry curvature — edges vs recesses",                     2),
            ('EDGE_WEAR',       "Edge Wear (Smart)",       "Pointiness convex + noise breakup",                                  3),
            ('DIRT',            "Dirt (Smart)",            "Inverted AO × noise grunge",                                         4),
            ('CURVATURE_SMART', "Curvature (Smart)",       "Bipolar pointiness — both convex + concave edges",                   5),
        ],
        default='POINTINESS',
        update=_on_layer_update,
    )

    mask_image_name_b: StringProperty(
        name="Mask B Image",
        description="Secondary image mask (only used when source is Image)",
        default="",
        update=_on_layer_update,
    )

    mask_invert_b: BoolProperty(
        name="Invert Mask B",
        description="Invert the secondary mask",
        default=False,
        update=_on_layer_update,
    )

    mask_ao_distance_b: FloatProperty(
        name="AO Distance B",
        description="Maximum distance for AO ray in mask B",
        default=0.5, min=0.01, max=10.0,
        update=_make_hot_callback("mask_ao_distance_b"),
    )

    mask_combine: EnumProperty(
        name="Combine",
        description="How to combine mask A with mask B",
        items=[
            ('MULTIPLY',  "AND (Multiply)", "Both masks must be bright → mask AND",         0),
            ('MINIMUM',   "AND (Strict)",   "Take the darker of A and B → strict AND",      1),
            ('MAXIMUM',   "OR (Lighten)",   "Take the brighter of A and B → mask OR",       2),
            ('ADD',       "Add",            "Sum both masks (clamped) — brightens result",  3),
            ('SUBTRACT',  "Subtract",       "A minus B — removes B regions from A",         4),
            ('SCREEN',    "Screen",         "1-(1-A)(1-B) — softer OR, less clipping",      5),
            ('DIFFERENCE',"Difference",     "|A-B| — mask XOR where they disagree",         6),
        ],
        default='MULTIPLY',
        update=_on_layer_update,
    )

    mask_contrast: FloatProperty(
        name="Mask Contrast",
        description="Contrast applied after combining masks. 0.5 = no change, <0.5 softer, >0.5 harder",
        default=0.5, min=0.0, max=1.0,
        update=_make_hot_callback("mask_contrast"),
    )

    # ── Mask refinement: Levels + Softness ───────────────────────────────
    # Levels: remap input range, apply gamma, remap output range
    use_mask_levels: BoolProperty(
        name="Mask Levels",
        description="Remap the combined mask: input range, gamma, output range",
        default=False,
        update=_on_layer_update,
    )
    mask_levels_in_min: FloatProperty(
        name="In Min",
        description="Black point: input values at/below this become 0",
        default=0.0, min=0.0, max=1.0,
        update=_make_hot_callback("mask_levels_in_min"),
    )
    mask_levels_in_max: FloatProperty(
        name="In Max",
        description="White point: input values at/above this become 1",
        default=1.0, min=0.0, max=1.0,
        update=_make_hot_callback("mask_levels_in_max"),
    )
    mask_levels_gamma: FloatProperty(
        name="Gamma",
        description="Midpoint bias — <1 brightens midtones, >1 darkens them",
        default=1.0, min=0.05, max=10.0,
        update=_make_hot_callback("mask_levels_gamma"),
    )
    mask_levels_out_min: FloatProperty(
        name="Out Min",
        description="Output floor — mask will never be darker than this",
        default=0.0, min=0.0, max=1.0,
        update=_make_hot_callback("mask_levels_out_min"),
    )
    mask_levels_out_max: FloatProperty(
        name="Out Max",
        description="Output ceiling — mask will never be brighter than this",
        default=1.0, min=0.0, max=1.0,
        update=_make_hot_callback("mask_levels_out_max"),
    )

    # Softness: widen/shrink the transition zone around 0.5 via smoothstep
    mask_softness: FloatProperty(
        name="Mask Softness",
        description="Smooth transition around the midpoint. 0 = sharp, 1 = very soft",
        default=0.0, min=0.0, max=1.0,
        update=_make_hot_callback("mask_softness"),
    )

    # Blur: pseudo-gaussian via multi-tap average of the mask input (UV-based sources only)
    # 0 = no blur, higher values = larger tap radius (implemented as neighbor averaging)
    mask_blur: FloatProperty(
        name="Mask Blur",
        description="Pseudo-blur of IMAGE-source masks. 0 = off, higher values = larger radius (has runtime cost)",
        default=0.0, min=0.0, max=0.1,
        update=_on_layer_update,
    )

    # ── Smart generator parameters (EDGE_WEAR / DIRT / CURVATURE_SMART) ──
    # These are shared across the generator mask sources below.
    mask_gen_intensity: FloatProperty(
        name="Intensity",
        description="Overall strength of the smart generator",
        default=1.0, min=0.0, max=2.0,
        update=_on_layer_update,
    )
    mask_gen_breakup: FloatProperty(
        name="Breakup",
        description="Organic noise variation applied to the generator — 0 = clean, 1 = very broken",
        default=0.3, min=0.0, max=1.0,
        update=_on_layer_update,
    )
    mask_gen_breakup_scale: FloatProperty(
        name="Breakup Scale",
        description="Scale of the breakup noise",
        default=15.0, min=0.1, max=200.0,
        update=_on_layer_update,
    )
    mask_gen_sharpness: FloatProperty(
        name="Sharpness",
        description="Edge/threshold sharpness of the generator",
        default=0.5, min=0.0, max=1.0,
        update=_on_layer_update,
    )

    # Clipping mask — clip this layer to the alpha of the layer directly below
    use_clipping_mask: BoolProperty(
        name="Clipping Mask",
        description="Show this layer only where the layer directly below has alpha (alpha-clipped to the layer underneath)",
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

    # ── Image Texture mapping (paint + PBR image layers) ────────────────────
    # Applied to ShaderNodeTexImage's Vector input via a ShaderNodeMapping
    # node. Default values (loc=0, rot=0, scale=1) trigger no Mapping node,
    # keeping the node graph minimal for layers that don't need transforms.
    paint_extension: EnumProperty(
        name="Extension",
        description="How the image is sampled outside its [0,1] UV range",
        items=[
            ('CLIP',   "Clip",   "Clamp to image edge — no repetition (decals, badges)"),
            ('REPEAT', "Repeat", "Tile the image (seamless textures)"),
            ('EXTEND', "Extend", "Stretch the edge pixels outward"),
            ('MIRROR', "Mirror", "Mirror at the boundary (no visible seam)"),
        ],
        default='CLIP',
        update=_on_layer_update,
    )

    # NOTE: paint_location_* are in UV space — 1.0 means "shift by exactly
    # one image width". soft_min/soft_max bound slider drag to [-2, +2] so
    # the user can't accidentally fling the image off the visible UV range
    # (which would make the layer "disappear" with extension=CLIP). Manual
    # numeric input still goes beyond.
    paint_location_x: FloatProperty(
        name="Location X", default=0.0,
        soft_min=-2.0, soft_max=2.0, step=1, precision=3,
        description="UV-space shift along X. 1.0 = one full image width",
        update=_make_hot_callback("paint_location_x"),
    )
    paint_location_y: FloatProperty(
        name="Location Y", default=0.0,
        soft_min=-2.0, soft_max=2.0, step=1, precision=3,
        description="UV-space shift along Y. 1.0 = one full image height",
        update=_make_hot_callback("paint_location_y"),
    )
    paint_location_z: FloatProperty(
        name="Location Z", default=0.0,
        soft_min=-2.0, soft_max=2.0, step=1, precision=3,
        description="Z shift — used only with 3D textures or rotated UVs",
        update=_make_hot_callback("paint_location_z"),
    )
    paint_rotation_x: FloatProperty(
        name="Rotation X", default=0.0, subtype='ANGLE',
        soft_min=-6.2832, soft_max=6.2832,  # ±2π
        update=_make_hot_callback("paint_rotation_x"),
    )
    paint_rotation_y: FloatProperty(
        name="Rotation Y", default=0.0, subtype='ANGLE',
        soft_min=-6.2832, soft_max=6.2832,
        update=_make_hot_callback("paint_rotation_y"),
    )
    paint_rotation_z: FloatProperty(
        name="Rotation Z", default=0.0, subtype='ANGLE',
        soft_min=-6.2832, soft_max=6.2832,
        update=_make_hot_callback("paint_rotation_z"),
    )
    paint_scale_x: FloatProperty(
        name="Scale X", default=1.0, soft_min=0.01, soft_max=20.0,
        step=10, precision=3,
        update=_make_hot_callback("paint_scale_x"),
    )
    paint_scale_y: FloatProperty(
        name="Scale Y", default=1.0, soft_min=0.01, soft_max=20.0,
        step=10, precision=3,
        update=_make_hot_callback("paint_scale_y"),
    )
    paint_scale_z: FloatProperty(
        name="Scale Z", default=1.0, soft_min=0.01, soft_max=20.0,
        step=10, precision=3,
        update=_make_hot_callback("paint_scale_z"),
    )

    # ── Reference Layer: reuses another layer's pattern output ──────────────
    # When layer_type == 'REFERENCE', this layer doesn't generate its own
    # pattern — it fetches the color/alpha outputs of the referenced layer
    # and blends them with this layer's OWN blend_mode, opacity, mask, and
    # per-channel overrides. Enables "one Voronoi, many behaviors" workflows.
    reference_layer_name: StringProperty(
        name="Reference Layer",
        description="Name of the layer whose pattern this reference reuses",
        default="",
        update=_on_layer_update,
    )

    # ── PBR Channels ─────────────────────────────────────────────────────────
    # Each layer can independently paint/fill additional PBR channels.
    # All channels are opt-in: disabling them leaves the channel untouched.

    # Roughness
    use_roughness: BoolProperty(name="Roughness", description="Enable roughness channel for this layer",
        default=False, update=_on_layer_update)
    roughness_image_name: StringProperty(name="Roughness Image",
        description="Image texture for the roughness channel", default="",
        update=_make_hot_callback("roughness_image_name"))
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
        update=_make_hot_callback("metallic_image_name"))
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
        update=_make_hot_callback("normal_image_name"))
    normal_strength: FloatProperty(
        name="Normal Strength", description="Strength of the normal map effect",
        default=1.0, min=0.0, max=5.0,
        update=_make_hot_callback("normal_strength"),
    )
    normal_tile_scale: FloatProperty(
        name="Normal Tile",
        description="Tiling scale for the normal map. Higher = more repetitions",
        default=1.0, min=0.1, max=20.0,
        update=_make_hot_callback("normal_tile_scale"),
    )
    normal_rotation: FloatProperty(
        name="Normal Rotation",
        description="Rotation of the normal map in degrees",
        default=0.0, min=-360.0, max=360.0,
        update=_make_hot_callback("normal_rotation"),
    )

    # Emission
    use_emission: BoolProperty(name="Emission", description="Enable emission (glow) channel for this layer",
        default=False, update=_on_layer_update)
    emission_image_name: StringProperty(name="Emission Image",
        description="Image texture for the emission channel", default="",
        update=_make_hot_callback("emission_image_name"))
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
        update=_make_hot_callback("transmission_image_name"))
    transmission_fill: FloatProperty(
        name="Transmission", description="Constant transmission value (0 = opaque, 1 = fully transparent/glass)",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("transmission_fill"),
    )

    # Alpha (BSDF Alpha input — controls overall surface opacity)
    use_alpha: BoolProperty(name="Alpha",
        description="Enable alpha channel for this layer (drives BSDF Alpha for surface opacity / cutout)",
        default=False, update=_on_layer_update)
    alpha_image_name: StringProperty(name="Alpha Image",
        description="Image texture for the alpha channel (R channel of the image is used)",
        default="",
        update=_make_hot_callback("alpha_image_name"))
    alpha_fill: FloatProperty(
        name="Alpha", description="Constant alpha value (0 = fully transparent, 1 = fully opaque)",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("alpha_fill"),
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
    # UI state — collapsible Branching (per-channel blend overrides) section
    show_blend_overrides: BoolProperty(
        name="Show Branching",
        description="Expand per-channel blend mode overrides",
        default=False,
    )
    # UI state — collapsible Mask section (only shown when use_mask=True)
    show_mask_section: BoolProperty(
        name="Show Mask Details",
        description="Expand the mask configuration (sources, refinement, etc.)",
        default=True,
    )
    # UI state — collapsible Image Mapping section
    show_paint_mapping: BoolProperty(
        name="Show Image Mapping",
        description="Expand Extension + Location/Rotation/Scale for image textures",
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

    @property
    def alpha_image(self):
        return bpy.data.images.get(self.alpha_image_name)

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

    # ── Voronoi random-per-cell (jawbreaker, greeble, mosaic) ──
    # When enabled, each Voronoi cell gets a random value derived from the
    # cell's Position output through a WhiteNoise texture.  The result feeds
    # the same ColorRamp (color1→color2→optional color3), so each cell picks
    # a discrete color from the ramp instead of the smooth distance gradient.
    proc_voronoi_random_color: BoolProperty(
        name="Random Per Cell",
        description="Each Voronoi cell receives a random value (drives Color and Fac outputs). "
                    "Use with 2- or 3-color ramp for jawbreaker / mosaic / greeble patterns",
        default=False,
        update=_on_layer_update,
    )

    proc_voronoi_random_seed: FloatProperty(
        name="Random Seed",
        description="Shift the per-cell randomization. Change this to get a different random layout "
                    "for otherwise identical Voronoi settings",
        default=0.0, min=0.0, max=100.0,
        update=_on_layer_update,
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
            ('GENERATED', "Generated", "Normalized to object bounding box (0-1). Can distort on non-uniform objects", 0),
            ('OBJECT',    "Object",    "Object-space coordinates. Consistent 3D patterns, works best with applied scale", 1),
            ('UV',        "UV",        "UV map coordinates. Follows UV unwrap, may show seams",                        2),
        ],
        default='OBJECT',
        update=_on_layer_update,
    )

    proc_coord_preset: EnumProperty(
        name="Coord Preset",
        description="Quick coordinate setup for common use cases",
        items=[
            ('CUSTOM',    "Custom",    "Manual coordinate settings",                          0),
            ('SPHERICAL', "Spherical", "Object coords + scale normalization + distortion",    1),
            ('SURFACE',   "Surface",   "Generated coords, no normalization",                  2),
            ('UV_DRIVEN', "UV",        "UV map coordinates",                                  3),
        ],
        default='CUSTOM',
        update=_on_preset_change,
    )

    proc_normalize_coords: BoolProperty(
        name="Normalize Object Coords",
        description="Compensate object dimensions so patterns look consistent "
                    "regardless of object size or proportions. Only applies to Object coords",
        default=False,
        update=_on_layer_update,
    )

    # ── Coordinate transform (polar / spherical / swirl / cylindrical) ──
    # Converts the Cartesian coords into a transformed space BEFORE the
    # Mapping node so that texture patterns wrap circularly, spherically,
    # or spiral-twist.  This unlocks planet / lollipop / ring / cylinder
    # patterns that are impossible with raw Noise/Voronoi/Wave.
    proc_coord_transform: EnumProperty(
        name="Coord Transform",
        description="Spatial transformation applied to coordinates before texturing. "
                    "NONE = raw Cartesian. POLAR = circular (XY → angle,radius). "
                    "SPHERICAL = planet-like (XYZ → phi,theta). "
                    "SWIRL = spiral twist. CYLINDRICAL = cylinder wrap (XY → angle, Z)",
        items=[
            ('NONE',        "None",        "No transform — raw XYZ",                             0),
            ('POLAR',       "Polar",       "XY → angle/radius — circular / radial patterns",      1),
            ('SPHERICAL',   "Spherical",   "XYZ → phi/theta — planet / spherical patterns",       2),
            ('SWIRL',       "Swirl",       "Rotate XY around Z by radius — spiral patterns",      3),
            ('CYLINDRICAL', "Cylindrical", "XY → angle, Z vertical — cylinder wrap patterns",     4),
        ],
        default='NONE',
        update=_on_layer_update,
    )

    proc_swirl_amount: FloatProperty(
        name="Swirl Amount",
        description="Twist strength (radians per unit radius) for Swirl transform. "
                    "Positive = clockwise, negative = counter-clockwise",
        default=2.0, min=-20.0, max=20.0,
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

    # Emission mask — smooth threshold with controllable falloff
    proc_emission_threshold: FloatProperty(
        name="Emission Threshold",
        description="Where the emission 'turns on'. Higher = wider glowing area. "
                    "Works together with Falloff for smooth edges",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_layer_update,
    )
    proc_emission_falloff: FloatProperty(
        name="Emission Falloff",
        description="Softness of the emission edge transition. "
                    "Low = sharp edge, High = gradual fade",
        default=0.08, min=0.001, max=1.0, subtype='FACTOR',
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
    # Defensive: if a previous version of TLM was loaded and never properly
    # unregistered (e.g. user did "Reload Scripts" without disabling first),
    # the same class identifiers are still in the Blender registry. Drop them
    # before registering the new modules so re-install/upgrade doesn't error.
    for cls in classes:
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass  # not registered — fine
    for cls in classes:
        bpy.utils.register_class(cls)
    if hasattr(bpy.types.Material, 'tlm'):
        try:
            del bpy.types.Material.tlm
        except (AttributeError, RuntimeError):
            pass
    bpy.types.Material.tlm = bpy.props.PointerProperty(type=TLM_MaterialProperties)


def unregister():
    # Cancel any pending rebuild timer
    if bpy.app.timers.is_registered(_do_deferred_rebuild):
        bpy.app.timers.unregister(_do_deferred_rebuild)
    global _pending_materials
    _pending_materials = set()

    if hasattr(bpy.types.Material, 'tlm'):
        try:
            del bpy.types.Material.tlm
        except (AttributeError, RuntimeError):
            pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
