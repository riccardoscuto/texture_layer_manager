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
# 250ms balances responsiveness with absorbing a slider drag — ~4 rebuilds
# per 1-second drag instead of one per 60Hz tick. Larger values feel laggy
# on hover-edit; smaller values stutter on slider drags with many layers.

_REBUILD_DEBOUNCE_S = 0.25
_suppress_layer_updates = False

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
            if mat and mat.tlm.auto_composite and not mat.tlm.shader_editable:
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


def cancel_pending_rebuild(material_name=None):
    """Cancel any pending deferred rebuild.

    Call this from operators that invoke compositing.rebuild_node_tree()
    directly, so the deferred timer doesn't fire a redundant second rebuild
    that clears and recreates all nodes (which can fail to trigger a UI
    redraw in the shader editor).

    When material_name is provided, only that material is removed from the
    pending set. This prevents an explicit rebuild for Material A from
    swallowing a queued rebuild for Material B in multi-material scenes.
    With no material_name, empties the whole pending set and unregisters
    the timer.
    """
    global _pending_materials
    if material_name is None:
        _pending_materials = set()
    else:
        _pending_materials.discard(material_name)
        if _pending_materials:
            return

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
    if _suppress_layer_updates:
        return
    try:
        if not context or not context.active_object:
            return
        mat = context.active_object.active_material
        if not mat or not mat.tlm.auto_composite or mat.tlm.shader_editable:
            return
        need_timer = not _pending_materials  # first material in this batch
        _pending_materials.add(mat.name)
        if need_timer:
            bpy.app.timers.register(_do_deferred_rebuild, first_interval=_REBUILD_DEBOUNCE_S)
    except ReferenceError:
        pass  # object or material was deleted mid-callback


def _on_mask_source_change(self, context):
    """Called when ``mask_source`` (or ``mask_source_b``) changes.

    Auto-enables the corresponding ``use_mask`` / ``use_mask_b`` toggle
    when the user picks anything other than the default ``IMAGE``. Without
    this, both the panel UI and programmatic preset construction can hit
    a silent no-op: the user picks "DIRT" from the dropdown but the mask
    is still gated off by ``use_mask=False``, so the layer applies
    everywhere and the source choice has no visible effect.

    The check on the previous value path-prefix ('mask_source' vs
    'mask_source_b') lets the same callback serve both slots — Blender's
    update mechanism passes the PropertyGroup instance (``self``) so we
    can inspect both fields directly.
    """
    # Slot A
    if getattr(self, 'mask_source', 'IMAGE') != 'IMAGE' and not self.use_mask:
        self.use_mask = True   # this also triggers _on_layer_update via the use_mask setter
        return                 # avoid double-rebuild
    # Slot B
    if getattr(self, 'mask_source_b', 'IMAGE') != 'IMAGE' and not self.use_mask_b:
        self.use_mask_b = True
        return
    # Normal source change (or change back to IMAGE) — just rebuild
    _on_layer_update(self, context)


def _on_name_change(self, context):
    """Called when a layer's `name` is edited.

    Two responsibilities beyond a regular rebuild:
    1. Repoint cross-references that used the OLD name — group children
       (``group_name``) and REFERENCE layers (``reference_layer_name``).
       Without this, renaming a group orphans every child and renaming
       a referenced layer breaks every reference into it.
    2. Trigger the standard debounced rebuild so node tags
       (``tlm_layer = layer.name``) catch up — the hot-update lookups
       use the current name, so leftover nodes tagged with the old
       name become unreachable until a rebuild runs.

    The previous name is shadowed in ``_name_prev`` (a hidden
    StringProperty); we read it before overwriting it with the new
    value.
    """
    if _suppress_layer_updates:
        return
    try:
        new_name = self.name
        old_name = self.get("_name_prev", "") or ""
        # Update mirror so the next rename has a fresh "old" to read.
        self["_name_prev"] = new_name

        if old_name and old_name != new_name and context and context.active_object:
            mat = context.active_object.active_material
            if mat and hasattr(mat, "tlm"):
                tlm = mat.tlm
                # Repoint group children so they stay nested in their group.
                for sib in tlm.layers:
                    if sib == self:
                        continue
                    if getattr(sib, "group_name", "") == old_name:
                        sib.group_name = new_name
                    if getattr(sib, "reference_layer_name", "") == old_name:
                        sib.reference_layer_name = new_name
        # Always rebuild — the rebuild re-tags all nodes with current
        # layer names, fixing any stale `tlm_layer` custom props that
        # would otherwise break the hot-update path.
        _on_layer_update(self, context)
    except ReferenceError:
        pass


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
        if _suppress_layer_updates:
            return
        try:
            if not context or not context.active_object:
                return
            mat = context.active_object.active_material
            if not mat or not mat.tlm.auto_composite or mat.tlm.shader_editable:
                return
            if not compositing.hot_update_property(mat, self, prop_name):
                _on_layer_update(self, context)
        except ReferenceError:
            pass  # object or material was deleted mid-callback
    return _cb


# ─── Blend mode enum ─────────────────────────────────────────────────────────

BLEND_MODES = [
    # UI label "Mix" (not "Normal") — matches the Blender ShaderNodeMix
    # node's blend_type label so users can scan node graph and addon UI
    # without translating between names.
    ("MIX",        "Mix",        "Standard alpha/mix composite over layer below", 0),
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
    # HARD_LIGHT removed — it produces inconsistent results across
    # Blender versions (different formulas in 3.x vs 4.x), and the
    # use cases are covered by Overlay + opacity. Re-enable here if
    # asked, but the index numbering is no longer contiguous.
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

ALPHA_MATH_OPERATIONS = [
    ("ADD", "Add", "Current alpha plus this layer alpha", 0),
    ("SUBTRACT", "Subtract", "Current alpha minus this layer alpha", 1),
    ("MULTIPLY", "Multiply", "Current alpha multiplied by this layer alpha", 2),
    ("DIVIDE", "Divide", "Current alpha divided by this layer alpha", 3),
    ("MULTIPLY_ADD", "Multiply Add", "A * B + C", 4),
    ("POWER", "Power", "Current alpha raised by this layer alpha", 5),
    ("LOGARITHM", "Logarithm", "Logarithm math operation", 6),
    ("SQRT", "Square Root", "Square root math operation", 7),
    ("INVERSE_SQRT", "Inverse Square Root", "Inverse square root math operation", 8),
    ("ABSOLUTE", "Absolute", "Absolute value math operation", 9),
    ("EXPONENT", "Exponent", "Exponent math operation", 10),
    ("MINIMUM", "Minimum", "Keep the lower alpha value", 11),
    ("MAXIMUM", "Maximum", "Keep the higher alpha value", 12),
    ("LESS_THAN", "Less Than", "Comparison math operation", 13),
    ("GREATER_THAN", "Greater Than", "Comparison math operation", 14),
    ("SIGN", "Sign", "Sign math operation", 15),
    ("COMPARE", "Compare", "Compare math operation", 16),
    ("SMOOTH_MIN", "Smooth Minimum", "Soft minimum between alpha values", 17),
    ("SMOOTH_MAX", "Smooth Maximum", "Soft maximum between alpha values", 18),
    ("ROUND", "Round", "Round alpha value", 19),
    ("FLOOR", "Floor", "Floor alpha value", 20),
    ("CEIL", "Ceil", "Ceil alpha value", 21),
    ("TRUNC", "Truncate", "Truncate alpha value", 22),
    ("FRACT", "Fraction", "Fraction math operation", 23),
    ("MODULO", "Truncated Modulo", "Modulo math operation", 24),
    ("FLOORED_MODULO", "Floored Modulo", "Floored modulo math operation", 25),
    ("WRAP", "Wrap", "Wrap math operation", 26),
    ("SNAP", "Snap", "Snap math operation", 27),
    ("PINGPONG", "Ping-Pong", "Ping-pong math operation", 28),
    ("SINE", "Sine", "Sine of this layer alpha", 29),
    ("COSINE", "Cosine", "Cosine of this layer alpha", 30),
    ("TANGENT", "Tangent", "Tangent of this layer alpha", 31),
    ("ARCSINE", "Arcsine", "Arcsine of this layer alpha", 32),
    ("ARCCOSINE", "Arccosine", "Arccosine of this layer alpha", 33),
    ("ARCTANGENT", "Arctangent", "Arctangent of this layer alpha", 34),
    ("ARCTAN2", "Arctan2", "Arctan2 math operation", 35),
    ("SINH", "Hyperbolic Sine", "Hyperbolic sine of this layer alpha", 36),
    ("COSH", "Hyperbolic Cosine", "Hyperbolic cosine of this layer alpha", 37),
    ("TANH", "Hyperbolic Tangent", "Hyperbolic tangent of this layer alpha", 38),
    ("RADIANS", "To Radians", "Convert alpha value to radians", 39),
    ("DEGREES", "To Degrees", "Convert alpha value to degrees", 40),
]

LAYER_TYPES = [
    ("PAINT",       "Paint",       "Regular paint layer with an image texture",  0),
    ("FILL",        "Fill",        "Solid color fill layer",                     1),
    ("ADJUSTMENT",  "Adjustment",  "Modifier layer: Hue/Sat, Levels, etc.",     2),
    ("GROUP",       "Group",       "Folder that contains other layers",          3),
    ("PROCEDURAL",  "Procedural",  "Shader-based procedural texture layer",     4),
    ("REFERENCE",   "Reference",   "Reuse another layer's output with independent blend/mask/channels", 5),
]


# ─── Extra color stops on a procedural ColorRamp ──────────────────────────
# In addition to the always-present Color1/Color2 (+ optional Color3),
# a PROCEDURAL layer can carry N extra colour stops here. Each entry
# is one ColorRamp element with its own colour + position. The build
# path in _build_procedural_node iterates this collection and appends
# each stop to the ColorRamp. Hot updates re-thread the elements
# without rebuilding the whole graph.

class TLM_ProcColorStop(PropertyGroup):
    """A single extra ColorRamp stop (color + position) on a PROCEDURAL layer."""

    color: bpy.props.FloatVectorProperty(
        name="Color",
        description="Colour applied at this stop position",
        subtype='COLOR', min=0.0, max=1.0, size=4,
        default=(0.5, 0.5, 0.5, 1.0),
        update=lambda self, ctx: _on_proc_color_stop_change(self, ctx),
    )
    position: FloatProperty(
        name="Position",
        description="Position of this stop along the ColorRamp (0..1). "
                    "Blender sorts stops internally — order doesn't matter.",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=lambda self, ctx: _on_proc_color_stop_change(self, ctx),
    )


def _on_volume_absorption_change(material_props, context):
    """Structural toggle — add or remove the Volume Absorption shader.

    `use_volume_absorption` flipping requires a topology change (new
    nodes appear / disappear), so we route this through a full rebuild.
    The actual building lives in compositing.rebuild_node_tree which
    reads `mat.tlm.use_volume_absorption` and emits the Volume shader
    if true.
    """
    mat = _resolve_owning_material(material_props, context)
    if mat is None:
        return
    try:
        from . import compositing
    except ImportError:
        import importlib
        compositing = importlib.import_module(__package__ + ".compositing")
    if getattr(mat.tlm, 'auto_composite', True):
        compositing.rebuild_node_tree(mat)


def _on_layer_use_displacement_change(layer, context):
    """Per-layer ``use_displacement`` toggle handler.

    Two responsibilities:

    1. **Auto-enable the material-level master** (`tlm.use_displacement`)
       when the per-layer toggle flips False → True. Without this, users
       hit a confusing UX gotcha: clicking "Add to Displace" on a layer
       does nothing if they haven't separately enabled the master in the
       Composite section.

    2. **Trigger the standard deferred rebuild** via ``_on_layer_update``
       so the displacement node graph gets re-wired.

    Flipping the master OFF here when ALL layers are off would be
    symmetric — but a power user might want the master on with no
    layers (e.g. about to add a layer next), so leave that case alone.
    """
    if layer.use_displacement:
        try:
            mat = layer.id_data
            if (mat is not None and isinstance(mat, bpy.types.Material)
                    and not mat.tlm.use_displacement):
                mat.tlm.use_displacement = True
        except (AttributeError, ReferenceError):
            pass
    _on_layer_update(layer, context)


def _on_displacement_change(material_props, context):
    """Structural — full rebuild so the Displacement node and Material
    Output wiring is created or torn down. Also triggers the adaptive
    subdivision auto-setup (cycles experimental + mesh subsurf adaptive)
    when `displacement_adaptive` is True.
    """
    mat = _resolve_owning_material(material_props, context)
    if mat is None:
        return
    try:
        from . import compositing
    except ImportError:
        import importlib
        compositing = importlib.import_module(__package__ + ".compositing")
    # Auto-setup Cycles + mesh adaptive subdivision when displacement is on
    if material_props.use_displacement and material_props.displacement_adaptive:
        _ensure_displacement_setup(mat)
    if getattr(mat.tlm, 'auto_composite', True):
        compositing.rebuild_node_tree(mat)


def _on_displacement_param_change(material_props, context):
    """Hot-update for strength + midlevel — pokes the ShaderNodeDisplacement
    inputs directly. If the node doesn't exist (displacement off), no-op."""
    mat = _resolve_owning_material(material_props, context)
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return
    nt = mat.node_tree
    disp = next((n for n in nt.nodes if n.bl_idname == 'ShaderNodeDisplacement'), None)
    if disp is None:
        return
    try:
        s = disp.inputs.get("Scale")
        if s is not None and not s.is_linked:
            s.default_value = material_props.displacement_strength
        m = disp.inputs.get("Midlevel")
        if m is not None and not m.is_linked:
            m.default_value = material_props.displacement_midlevel
    except (AttributeError, KeyError):
        pass


def _ensure_displacement_setup(mat):
    """Configure Cycles + every mesh using this material for TRUE
    geometric displacement (silhouette break, not just bump):

      1. ``mat.displacement_method = 'DISPLACEMENT'``
         In Blender 5.1+ this lives directly on the material (used to be
         ``mat.cycles.displacement_method`` pre-3.x). Defaults to ``BUMP``
         which means the Displacement output is collapsed back to a bump
         normal — invisible on the silhouette. Forcing ``DISPLACEMENT``
         actually moves vertices.
         Older Blender exposes it via ``mat.cycles.displacement_method``;
         we try both with hasattr guards.

      2. On older Blender (pre-5.1) also ``scene.cycles.feature_set =
         'EXPERIMENTAL'`` — required for adaptive subd back then. Removed
         in 5.1.

      3. Each MESH using the material gets a SUBSURF modifier with the
         modifier-level ``use_adaptive_subdivision = True`` (Blender 5.1+
         exposes the flag here, not on ``obj.cycles`` like pre-5.x).

    Skips gracefully when the renderer isn't Cycles or the API is missing.
    """
    scene = bpy.context.scene
    if scene.render.engine != 'CYCLES':
        return

    # ── 1. Material displacement method ──
    # Read user-chosen method from tlm.displacement_method (enum:
    # BUMP / DISPLACEMENT / BOTH). Defaults to DISPLACEMENT for the
    # geometric silhouette break that this feature was designed for.
    desired = getattr(mat.tlm, 'displacement_method', 'DISPLACEMENT')
    # New-style API (Blender 4.x+ / 5.x): mat.displacement_method
    if hasattr(mat, 'displacement_method'):
        try:
            mat.displacement_method = desired
        except (AttributeError, TypeError, RuntimeError):
            pass
    # Old-style API (pre-4.x): mat.cycles.displacement_method
    elif hasattr(mat, 'cycles') and hasattr(mat.cycles, 'displacement_method'):
        try:
            mat.cycles.displacement_method = desired
        except (AttributeError, TypeError, RuntimeError):
            pass

    # ── 2. feature_set EXPERIMENTAL (only on old Blender) ──
    cycles = getattr(scene, 'cycles', None)
    if cycles is not None and hasattr(cycles, 'feature_set'):
        try:
            cycles.feature_set = 'EXPERIMENTAL'
        except (AttributeError, TypeError, RuntimeError):
            pass

    # ── 3. Mesh-level adaptive subdivision ──
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if not any(slot.material is mat for slot in obj.material_slots):
            continue
        # Find or create a SUBSURF modifier
        sub = next((m for m in obj.modifiers if m.type == 'SUBSURF'), None)
        if sub is None:
            try:
                sub = obj.modifiers.new(name="TLM_Adaptive_Subdiv", type='SUBSURF')
                sub.levels = 1
                sub.render_levels = 1
            except (RuntimeError, AttributeError):
                continue
        # Modifier-level adaptive flag (5.1+ canonical location)
        if hasattr(sub, 'use_adaptive_subdivision'):
            try:
                sub.use_adaptive_subdivision = True
            except (AttributeError, TypeError, RuntimeError):
                pass
        # Object-level fallback (pre-5.x)
        if hasattr(obj.cycles, 'use_adaptive_subdivision'):
            try:
                obj.cycles.use_adaptive_subdivision = True
            except (AttributeError, TypeError, RuntimeError):
                pass


def _on_volume_scatter_change(material_props, context):
    """Structural toggle for use_volume_scatter — triggers full rebuild
    so the Volume Scatter shader appears/disappears and the combine
    topology (alone, or Add Shader'd with Absorption) is rebuilt."""
    mat = _resolve_owning_material(material_props, context)
    if mat is None:
        return
    try:
        from . import compositing
    except ImportError:
        import importlib
        compositing = importlib.import_module(__package__ + ".compositing")
    if getattr(mat.tlm, 'auto_composite', True):
        compositing.rebuild_node_tree(mat)


def _on_volume_scatter_param_change(material_props, context):
    """Hot-update Volume Scatter shader's Color, Density, or Anisotropy
    without rebuilding. No-op if scatter is currently disabled.
    """
    mat = _resolve_owning_material(material_props, context)
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return
    nt = mat.node_tree
    sct = next((n for n in nt.nodes if n.bl_idname == 'ShaderNodeVolumeScatter'), None)
    if sct is None:
        return
    try:
        col = sct.inputs.get("Color")
        if col is not None and not col.is_linked:
            col.default_value = material_props.volume_scatter_color
        den = sct.inputs.get("Density")
        if den is not None and not den.is_linked:
            den.default_value = material_props.volume_scatter_density
        ani = sct.inputs.get("Anisotropy")
        if ani is not None and not ani.is_linked:
            ani.default_value = material_props.volume_scatter_anisotropy
    except (AttributeError, KeyError):
        pass


def _on_volume_absorption_param_change(material_props, context):
    """Hot-update the existing Volume Absorption node's Color or Density
    without rebuilding. If the node isn't present (e.g. user toggled
    use_volume_absorption off), this is a no-op.
    """
    mat = _resolve_owning_material(material_props, context)
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return
    nt = mat.node_tree
    vol = next((n for n in nt.nodes if n.bl_idname == 'ShaderNodeVolumeAbsorption'), None)
    if vol is None:
        return
    try:
        col = vol.inputs.get("Color")
        if col is not None and not col.is_linked:
            col.default_value = material_props.volume_absorption_color
        den = vol.inputs.get("Density")
        if den is not None and not den.is_linked:
            den.default_value = material_props.volume_absorption_density
    except (AttributeError, KeyError):
        pass


def _resolve_owning_material(material_props, context):
    """Find the Material that owns the given TLM_MaterialProperties.

    Uses `material_props.id_data` which Blender guarantees to be the ID
    datablock the PropertyGroup is attached to. This is far more reliable
    than the `is` comparison (which fails because Blender's bpy_struct
    wrappers don't preserve identity across accesses — `mat.tlm is mat.tlm`
    can return False even when they're the same underlying data).
    """
    try:
        owner = material_props.id_data
        if owner is not None and isinstance(owner, bpy.types.Material):
            return owner
    except (AttributeError, ReferenceError):
        pass
    # Fallback: scan by name match between context object's material and
    # the property values. Last-ditch in case id_data isn't available.
    obj = getattr(context, 'object', None)
    if obj and obj.active_material is not None:
        return obj.active_material
    return None


def _on_bsdf_ior_change(material_props, context):
    """Hot-update the BSDF.IOR input without rebuilding the whole tree."""
    mat = _resolve_owning_material(material_props, context)
    if not mat or not mat.use_nodes or not mat.node_tree:
        return
    bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        return
    ior_in = bsdf.inputs.get("IOR")
    if ior_in is None or ior_in.is_linked:
        return
    ior_in.default_value = material_props.bsdf_ior


def _on_proc_color_stop_change(stop, context):
    """A TLM_ProcColorStop lives inside TLM_LayerItem.proc_extra_color_stops
    which lives on a Material. Find owner via id_data (the Material), then
    walk its layers to find which one owns this stop (by as_pointer match).
    """
    try:
        from . import compositing
    except ImportError:
        import importlib
        compositing = importlib.import_module(__package__ + ".compositing")

    # The stop's id_data IS the Material that owns it via the layer chain.
    # This works because PropertyGroups inside CollectionProperty inside
    # PropertyGroup attached to a Material still report the Material as
    # id_data.
    try:
        mat = stop.id_data
    except (AttributeError, ReferenceError):
        mat = None
    if mat is None or not isinstance(mat, bpy.types.Material) or not hasattr(mat, 'tlm'):
        # Fallback: try the active object's material
        obj = getattr(context, 'object', None)
        mat = obj.active_material if (obj and hasattr(obj, 'active_material')) else None
        if not mat:
            return

    target_ptr = stop.as_pointer()
    for layer in mat.tlm.layers:
        if getattr(layer, 'layer_type', '') != 'PROCEDURAL':
            continue
        for s in getattr(layer, 'proc_extra_color_stops', []):
            if s.as_pointer() == target_ptr:
                nt = mat.node_tree
                if nt is None:
                    return
                ok = compositing._hot_proc_color(nt, layer, "proc_extra_color_stops")
                if not ok:
                    compositing.rebuild_node_tree(mat)
                return


# ─── Single Layer ─────────────────────────────────────────────────────────────

class TLM_LayerItem(PropertyGroup):
    """Represents a single texture layer."""

    # Override the implicit PropertyGroup `name` so renames trigger:
    #   1. A debounced full rebuild (so `tlm_layer` tags on existing
    #      nodes are refreshed — without this, the hot-update path
    #      can no longer find any of this layer's nodes by the new
    #      name and silently goes stale until the user nudges any
    #      other property to force a manual rebuild).
    #   2. Cross-reference repointing so group children and REFERENCE
    #      layers that pointed at the OLD name are re-pointed at the
    #      NEW name.
    # See _on_name_change for the full logic.
    name: StringProperty(
        name="Name",
        default="Layer",
        update=_on_name_change,
    )

    # NOTE: the previous name is shadowed in self["_name_prev"] (an ID
    # custom property, set/read via the dict-style API). No
    # bpy.props annotation is needed — IDPropertyGroup supports
    # arbitrary keys directly. See _on_name_change for the read/write.

    layer_type: EnumProperty(
        name="Type",
        description="Layer kind: Paint (image canvas), Fill (flat colour), "
                    "Procedural (generated pattern), Adjustment (remap below), "
                    "Group (folder), Reference (reuse another layer)",
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
    alpha_math_operation: EnumProperty(
        name="Alpha Math",
        description="Math operation used when this layer contributes to the Alpha channel",
        items=ALPHA_MATH_OPERATIONS,
        default="MULTIPLY",
        update=_on_layer_update,
    )

    # ── Output channel routing ──────────────────────────────────────────────
    # Each layer is pinned to ONE BSDF input among the 4 routable channels.
    # The non-routable channels (Normal / Emission / Transmission / Bump) are
    # multi-channel-friendly and continue to use their use_<channel> toggles
    # — they can coexist with any output_channel value.
    # Removed the legacy 'AUTO' entry: it was confusing for new users (output
    # said "Auto" while the layer was actually routed to base color via an
    # implicit toggle path). .blend files saved with output_channel='AUTO'
    # silently fall back to 'BASE_COLOR' on load (see _layer_contributes_to).
    output_channel: EnumProperty(
        name="Output Channel",
        description="Which Principled BSDF input this layer's PRIMARY output drives. "
                    "Additional channels can be enabled via the use_* toggles in "
                    "PBR Channels — but the dropdown sets where the layer's "
                    "procedural pattern / fill colour goes first",
        items=[
            ('BASE_COLOR',   "Base Color",   "Send this layer to Base Color (default)"),
            ('ROUGHNESS',    "Roughness",    "Send this layer to Roughness — pattern drives surface shininess"),
            ('METALLIC',     "Metallic",     "Send this layer to Metallic — pattern drives metallic mask"),
            ('EMISSION',     "Emission",     "Send this layer to Emission Color — pattern drives glow colour (use Emission Strength for intensity)"),
            ('TRANSMISSION', "Transmission", "Send this layer to Transmission Weight — pattern drives glass/clear-coat amount"),
            ('ALPHA',        "Alpha",        "Send this layer to Alpha (surface opacity / cutout)"),
        ],
        default='BASE_COLOR',
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
            ('WIREFRAME',  "Wireframe",  "Real mesh-edge mask from Blender's Wireframe shader node. Follows the actual topology/triangulation of the mesh instead of a fake crack/grid pattern.", 30),
            # ── Smart generators (LIVE): physics-based masks with noise breakup, ──
            # ── evaluated every shader sample. For BAKED alternatives use the    ──
            # ── "Bake Smart Mask" button below.                                  ──
            ('EDGE_WEAR',       "Edge Wear (Live)",       "Pointiness convex edges + noise breakup + sharpness — simulates worn-out edges (real-time)", 3),
            ('DIRT',            "Dirt (Live)",            "Inverted AO × noise grunge — accumulates in cavities with organic variation (real-time)",   4),
            ('CURVATURE_SMART', "Curvature (Live)",       "Bipolar pointiness (both convex + concave) with threshold — highlights all edges (real-time)", 5),
            # ── View-angle source ──
            ('FRESNEL',         "Fresnel",                "Viewing-angle gradient — 0 facing camera, 1 at grazing silhouette. Pair with a procedural's ColorRamp (color1/color2/contrast) to drive iridescent / oil-slick / bubble / hologram materials.", 6),
            # ── Light-angle source — for anime cel-shading and NdotL effects ──
            ('NDOTL',           "Light Angle (NdotL)",    "Normal × Sun direction (world space), remapped to [0,1]. 1 = surface fully lit, 0 = surface in shadow. Pair with proc_contrast=1.0 ColorRamp for HARD binary cel-shading (anime/toon look). Uses the first Sun light in the scene; rebuild material after moving the Sun.", 7),
            ('NDOTH',           "Half-Vector (NdotH)",    "Normal × Half-Vector between Sun and View. Peaks at the classic Phong specular highlight position (between sun and camera). Use for stylized toon specular highlights (anime sparkle), with proc_contrast=1.0 ColorRamp for a hard-edged shaped highlight.", 8),
            # ── Procedural-driven mask ──
            ('VORONOI',         "Voronoi",                "Use a Voronoi pattern as the mask. Pick F1 (cell distance, peaks at cell centres) or DISTANCE_TO_EDGE (peaks at cell centres, 0 at edges = ideal for crack/joint masks). Pair with mask_invert to flip. Use the SAME mask_voronoi_scale as a layer's proc_scale to align the mask cells with the layer's pattern (e.g. cobblestone: stone colour Voronoi and dirt mask Voronoi share scale so dirt lands exactly between stones).", 9),
        ],
        default='IMAGE',
        update=_on_mask_source_change,
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
    # ── Voronoi mask (when mask_source = 'VORONOI') ──
    mask_voronoi_feature: EnumProperty(
        name="Voronoi Feature A",
        description="Which Voronoi output drives the mask",
        items=[
            ('F1', "F1 (Distance to Cell)",
             "Distance to nearest cell centre. 0 at centre, increases outward — peaks at cell edges. Use mask_invert for centre-peaks."),
            ('DISTANCE_TO_EDGE', "Distance to Edge",
             "Distance to the nearest cell edge. 0 at edges (cracks), peaks at cell centres. Ideal for cobblestone joints — invert for crack-only mask."),
        ],
        default='DISTANCE_TO_EDGE',
        update=_on_layer_update,
    )
    mask_voronoi_scale: FloatProperty(
        name="Voronoi Scale A",
        description="Cell density of the Voronoi mask. Match the scale of a layer's proc_scale to align mask cells with the layer pattern (cobblestone trick)",
        default=10.0, min=0.1, max=200.0,
        update=_make_hot_callback("mask_voronoi_scale"),
    )
    mask_voronoi_randomness: FloatProperty(
        name="Voronoi Randomness A",
        description="Cell-centre jitter. 0 = grid, 1 = fully scattered",
        default=1.0, min=0.0, max=1.0,
        update=_make_hot_callback("mask_voronoi_randomness"),
    )
    mask_voronoi_edge_width: FloatProperty(
        name="Voronoi Edge Width A",
        description="How far the edge zone of the DTE mask extends INTO the cell (with mask_invert). "
                    "1.0 = full smooth gradient edge→centre. Lower values (~0.3) make the mask hit "
                    "saturation closer to the edge, producing thinner crack-only bands. Higher values "
                    "(>1.0) would extend beyond cell boundaries (clamped). For cobblestone dirt "
                    "filling broad areas between stones, use 1.0; for thin crack-only ink, use 0.3.",
        default=1.0, min=0.05, max=2.0,
        update=_make_hot_callback("mask_voronoi_edge_width"),
    )
    mask_wireframe_size: FloatProperty(
        name="Wireframe Size",
        description="Thickness of the real mesh-edge mask when mask source = WIREFRAME",
        default=0.01, min=0.0001, max=1.0,
        update=_make_hot_callback("mask_wireframe_size"),
    )
    mask_wireframe_use_pixel_size: BoolProperty(
        name="Use Pixel Size",
        description="Match Blender's Wireframe node screen-space thickness behavior",
        default=True,
        update=_make_hot_callback("mask_wireframe_use_pixel_size"),
    )

    # ── Anime / toon tint strength ──────────────────────────────────────
    # Multiplies the layer's output color by the first Sun light's color,
    # preserving the base value (only hue+saturation transfer). Implements
    # the "Tint Strength" parameter from anime shaders (e.g. Genshin) where
    # a warm sun tints the lit half warm and a cool sun tints it cool.
    use_light_tint: BoolProperty(
        name="Tint by Light Color",
        description="Modulate this layer's colour by the first Sun light's "
                    "colour (hue + saturation only, value preserved). "
                    "Anime/toon convention for warm-light-warm-tone shading",
        default=False,
        update=_on_layer_update,
    )
    light_tint_strength: FloatProperty(
        name="Tint Strength",
        description="How strongly the light colour tints this layer. "
                    "0 = no tint, 1 = full hue/sat replacement",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("light_tint_strength"),
    )

    mask_fresnel_ior: FloatProperty(
        name="Fresnel IOR (Mask)",
        description="Index of refraction when mask_source = FRESNEL. "
                    "Low (1.05-1.20) = wide rim covering most viewing angles; "
                    "high (2.0-5.0) = narrow rim only at grazing silhouette. "
                    "Glass-like ≈ 1.45, water ≈ 1.33, diamond ≈ 2.42",
        default=1.45, min=1.0, max=5.0,
        update=_make_hot_callback("mask_fresnel_ior"),
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
            ('WIREFRAME',  "Wireframe",  "Real mesh-edge mask from Blender's Wireframe shader node", 30),
            ('EDGE_WEAR',       "Edge Wear (Smart)",       "Pointiness convex + noise breakup",                                  3),
            ('DIRT',            "Dirt (Smart)",            "Inverted AO × noise grunge",                                         4),
            ('CURVATURE_SMART', "Curvature (Smart)",       "Bipolar pointiness — both convex + concave edges",                   5),
            ('FRESNEL',         "Fresnel",                 "Viewing-angle gradient — 0 facing, 1 grazing",                       6),
            ('NDOTL',           "Light Angle (NdotL)",     "Normal · Sun direction in [0,1] — for anime/toon shading",            7),
            ('NDOTH',           "Half-Vector (NdotH)",     "Normal · Half-Vector (sun+view) — for toon specular highlights",       8),
            ('VORONOI',         "Voronoi",                 "Voronoi pattern mask (see Mask A description for details)",         9),
        ],
        default='POINTINESS',
        update=_on_mask_source_change,
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
    mask_voronoi_feature_b: EnumProperty(
        name="Voronoi Feature B",
        description="Which Voronoi output drives the secondary mask",
        items=[
            ('F1', "F1 (Distance to Cell)", "0 at centre, peaks at edges"),
            ('DISTANCE_TO_EDGE', "Distance to Edge", "0 at edges, peaks at centres"),
        ],
        default='DISTANCE_TO_EDGE',
        update=_on_layer_update,
    )
    mask_voronoi_scale_b: FloatProperty(
        name="Voronoi Scale B",
        description="Cell density of the secondary Voronoi mask",
        default=10.0, min=0.1, max=200.0,
        update=_make_hot_callback("mask_voronoi_scale_b"),
    )
    mask_voronoi_randomness_b: FloatProperty(
        name="Voronoi Randomness B",
        description="Cell-centre jitter on the secondary Voronoi mask",
        default=1.0, min=0.0, max=1.0,
        update=_make_hot_callback("mask_voronoi_randomness_b"),
    )
    mask_voronoi_edge_width_b: FloatProperty(
        name="Voronoi Edge Width B",
        description="Edge zone width for the secondary Voronoi mask (see slot A description)",
        default=1.0, min=0.05, max=2.0,
        update=_make_hot_callback("mask_voronoi_edge_width_b"),
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
        update=_make_hot_callback("mask_blur"),
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

    # NOTE: Triplanar used to be a custom feature (use_triplanar /
    # triplanar_scale / triplanar_sharpness). It has been removed in
    # favour of Blender's native 'BOX' projection on the Image Texture
    # node, exposed via paint_projection below. Box projection is
    # cheaper (one tex node instead of three), supported in both
    # Cycles and Eevee, and uses Object/Generated coords automatically.

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

    # Texture filter — how the sampler picks/blends pixels at sub-texel
    # locations. 'Linear' is the sane default; 'Closest' gives a pixel-art
    # look; 'Cubic' is smoother for normal maps & smooth-shaded surfaces;
    # 'Smart' is Cycles-only and adaptive (falls back to Linear in Eevee).
    paint_interpolation: EnumProperty(
        name="Interpolation",
        description="Pixel sampling filter used when the image is magnified or minified",
        items=[
            ('Linear',  "Linear",  "Standard bilinear filtering — smooth default"),
            ('Cubic',   "Cubic",   "Smoother filtering (good for normal maps and gradients)"),
            ('Closest', "Closest", "Nearest-neighbour — no blending (pixel-art / 1:1 stamps)"),
            ('Smart',   "Smart",   "Cycles only — adaptive between Cubic and Linear"),
        ],
        default='Linear',
        update=_on_layer_update,
    )

    # Projection — how the UV / vector input is interpreted to sample
    # the image. 'Flat' is the typical UV-mapped case; 'Box' is
    # triplanar built into Blender (replaces our custom triplanar);
    # 'Sphere' and 'Tube' are for HDR / panoramic images.
    paint_projection: EnumProperty(
        name="Projection",
        description="How the image is projected onto the surface",
        items=[
            ('FLAT',   "Flat",   "Standard UV mapping (default)"),
            ('BOX',    "Box",    "Triplanar — sample along the three world axes and blend"),
            ('SPHERE', "Sphere", "Equirectangular wrap (HDR / 360°)"),
            ('TUBE',   "Tube",   "Cylindrical wrap (labels around bottles, etc.)"),
        ],
        default='FLAT',
        update=_on_layer_update,
    )

    # Box projection blend distance — only meaningful when projection
    # is 'BOX'. Width in UV units of the blend zone between adjacent
    # world-axis projections. 0 = hard seam, 1 = fully blended.
    paint_projection_blend: FloatProperty(
        name="Projection Blend",
        description="Blend width between projections (only used for Box projection)",
        default=0.3, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("paint_projection_blend"),
    )

    # Image source — what kind of image data is sampled. 'Single Image'
    # is the standard still texture. 'Generated' lets the image's
    # generated_color show through. 'Sequence' and 'Movie' are for
    # animated textures (frame range + offset + duration come from
    # the image datablock itself, this just toggles the mode).
    paint_source: EnumProperty(
        name="Source",
        description="Image source type — what kind of pixel data is sampled",
        items=[
            ('FILE',           "Single Image", "Still image from a file"),
            ('GENERATED',      "Generated",    "Procedurally generated (uses the image's generated_color)"),
            ('SEQUENCE',       "Image Sequence","Numbered frames driven by the scene timeline"),
            ('MOVIE',          "Movie",        "Video file decoded per frame"),
        ],
        default='FILE',
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
        description="Rotate the UV/image around the X axis (radians)",
        update=_make_hot_callback("paint_rotation_x"),
    )
    paint_rotation_y: FloatProperty(
        name="Rotation Y", default=0.0, subtype='ANGLE',
        soft_min=-6.2832, soft_max=6.2832,
        description="Rotate the UV/image around the Y axis (radians)",
        update=_make_hot_callback("paint_rotation_y"),
    )
    paint_rotation_z: FloatProperty(
        name="Rotation Z", default=0.0, subtype='ANGLE',
        soft_min=-6.2832, soft_max=6.2832,
        description="Rotate the UV/image around the Z axis (radians) — main rotation for 2D paint",
        update=_make_hot_callback("paint_rotation_z"),
    )
    paint_scale_x: FloatProperty(
        name="Scale X", default=1.0, soft_min=0.01, soft_max=20.0,
        step=10, precision=3,
        description="UV scale along X. >1 zooms out (tiling); <1 zooms in",
        update=_make_hot_callback("paint_scale_x"),
    )
    paint_scale_y: FloatProperty(
        name="Scale Y", default=1.0, soft_min=0.01, soft_max=20.0,
        step=10, precision=3,
        description="UV scale along Y. >1 zooms out (tiling); <1 zooms in",
        update=_make_hot_callback("paint_scale_y"),
    )
    paint_scale_z: FloatProperty(
        name="Scale Z", default=1.0, soft_min=0.01, soft_max=20.0,
        step=10, precision=3,
        description="Z scale — only meaningful with Box/Sphere/Tube projections",
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

    # ── True geometric displacement ──
    # Layers with use_displacement=True contribute a HEIGHT signal that
    # gets summed across the stack and wired to Material Output.Displacement.
    # Unlike Bump (which only perturbs the shading normal — silhouette
    # stays smooth), real Displacement moves the actual mesh vertices via
    # Cycles' adaptive subdivision feature. Required for chunky materials
    # like rocky terrain, brick walls, sci-fi panels with deep grooves —
    # anywhere the SILHOUETTE needs to break, not just the shading.
    use_displacement: BoolProperty(
        name="Displacement",
        description="Contribute this layer's texture to the material's "
                    "displacement height. Cumulative — multiple layers' "
                    "heights sum together exactly like Bump. Activating "
                    "this auto-enables the material-level Displacement "
                    "master (in Composite) if it was off.",
        default=False,
        update=_on_layer_use_displacement_change,
    )
    displacement_scale: FloatProperty(
        name="Displacement Scale",
        description="Per-layer height contribution before the material-level "
                    "displacement_strength multiplier. Positive pushes outward, "
                    "negative pushes inward. 1.0 = full layer height, "
                    "0.5 = half, 0.0 = no contribution.",
        default=1.0, min=-5.0, max=5.0,
        update=_make_hot_callback("displacement_scale"),
    )

    # UI state — collapsible PBR section
    show_pbr_channels: BoolProperty(
        name="Show PBR Channels",
        description="Expand the PBR Channels section: Bump + per-channel "
                    "(Roughness / Metallic / Normal / Emission / Transmission / "
                    "Alpha) toggles and their image/fill controls",
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
    # UI state — collapsible sub-sections of a PROCEDURAL layer's panel.
    # Defaults chosen so the most frequently tweaked controls are open
    # (Color + Pattern) while the verbose Mapping block is folded.
    show_proc_color_section: BoolProperty(
        name="Show Color Ramp",
        description="Expand the Color Ramp section: Color 1/2, extra stops, "
                    "Mode + Interpolation, Manual Stops toggle, Contrast/Center",
        default=True,
    )
    show_proc_pattern_section: BoolProperty(
        name="Show Pattern Params",
        description="Expand per-procedural pattern parameters "
                    "(Detail / Roughness / Feature / Wave Profile / etc)",
        default=True,
    )
    show_proc_mapping_section: BoolProperty(
        name="Show Mapping",
        description="Expand the Mapping block: Mapping Type, Location/Rotation/Scale, "
                    "Coordinate preset/type, Transform, Vector Distortion",
        default=False,
    )
    # UI state — collapsible Mask Refinement section (Levels + Softness + Blur).
    # Collapsed by default since refinement is advanced — most users set up the
    # mask source and don't need to remap Levels.
    show_mask_refinement: BoolProperty(
        name="Show Mask Refinement",
        description="Expand Levels (Input/Output/Gamma) + Softness + Blur controls",
        default=False,
    )
    # UI state — collapsible "Surface Effects" section.
    # Groups Fresnel Rim (per-layer), Displacement (per-layer toggle + shared
    # material-level shortcut) and Volume (material-level shortcut) into one
    # collapsible so the top-level layer panel stays readable. Collapsed by
    # default since these are advanced — most layers don't use them.
    show_surface_effects: BoolProperty(
        name="Show Surface Effects",
        description="Expand Fresnel Rim + Displacement + Volume controls",
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
        description="Which remap to apply on the layers below: Hue/Saturation, "
                    "Brightness/Contrast, Levels (in/out remap + gamma), or "
                    "Color Balance (Lift/Gamma/Gain cinematic grading)",
        items=[
            ('HUE_SAT',        "Hue/Saturation",    "Adjust hue, saturation and value",             0),
            ('BRIGHT_CONTRAST', "Brightness/Contrast","Adjust brightness and contrast",              1),
            ('LEVELS',         "Levels",             "Remap input/output tonal range",               2),
            ('COLOR_BALANCE',  "Color Balance",      "Lift / Gamma / Gain (cinematic grading)",      3),
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

    # ── Procedural layer properties ───────────────────────────────────────────

    proc_type: EnumProperty(
        name="Type",
        description="Procedural pattern generator. Each type has its own "
                    "Pattern Params (Detail / Roughness / Feature / etc) and "
                    "is composited via a shared ColorRamp for colour output",
        # Alphabetical order by display label so the dropdown is
        # scannable. Numeric identifiers are kept stable across versions
        # (changing them would re-shuffle existing presets/.tlm files).
        items=[
            ('BRICK',       "Brick",       "Brick / tile pattern with offset, mortar, color variation", 7),
            ('CHECKER',     "Checker",     "Alternating checkerboard pattern",                     5),
            ('CRACKS',      "Cracks",      "Organic crack / vein network from Voronoi distance-to-edge — marble veins, cracked ceramic, ice, lava fractures", 15),
            ('DOTS',        "Dots",        "Packed circular dots in a jittered grid — paint splatter, polkadots, freckles, perforations", 13),
            ('FRESNEL',     "Fresnel Gradient", "View-angle gradient: fac=0 facing camera, fac=1 at grazing silhouette. IOR controlled by proc_fresnel_ior. Pair Color1/Color2 with contrast+ramp_center for iridescent / oil-slick / bubble / hologram materials — the ColorRamp maps the angular sweep to a smooth or banded multi-colour rainbow.", 16),
            ('GABOR',       "Gabor",       "Anisotropic Gabor noise — directional streaks for brushed metal, fibers, woven fabric, scratches", 12),
            ('GRADIENT',    "Gradient",    "Linear, radial, quadratic or spherical gradient",      3),
            ('HEX_GRID',    "Hex Grid",    "Honeycomb / cell grid using Voronoi distance-to-edge", 11),
            ('MAGIC',       "Magic",       "Kaleidoscopic colored swirl pattern",                  8),
            ('MARBLE',      "Marble",      "Wave bands distorted by noise — marble/veined stone", 6),
            ('MUSGRAVE',    "Musgrave",    "Fractal noise (Multifractal, Ridged, etc.)",           4),
            ('NOISE',       "Noise",       "Perlin/FBM noise",                                    0),
            ('RIDGED',      "Ridged",      "Sharp inverted-ridge fractal — mountain crests, rock veins, lightning, crackle", 14),
            ('STRIPES',     "Stripes",     "Hard-edged stripes (X, Y or diagonal) with adjustable width and sharpness", 10),
            ('VORONOI',     "Voronoi",     "Cell/Worley noise",                                   1),
            ('WAVE',        "Wave",        "Sine wave bands or rings",                             2),
            ('WHITE_NOISE', "White Noise", "Per-pixel random — fine grain, dust, dithering",       9),
        ],
        default='NOISE',
        update=_on_layer_update,
    )

    # ── Brick-specific parameters ───────────────────────────────────────
    # Stock ShaderNodeTexBrick exposes mortar size/smooth/bias and
    # squash/squash_frequency plus offset/offset_frequency. Mortar color
    # uses a third color (we re-use proc_color3 for it when active).
    proc_brick_offset: FloatProperty(
        name="Offset",
        description="Row offset (0.5 = standard brick stagger)",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_brick_offset"),
    )
    proc_brick_offset_freq: IntProperty(
        name="Offset Frequency",
        description="How many rows before the offset pattern repeats",
        default=2, min=1, max=99,
        update=_make_hot_callback("proc_brick_offset_freq"),
    )
    proc_brick_squash: FloatProperty(
        name="Squash",
        description="Brick squash factor (1.0 = no squash)",
        default=1.0, min=0.0, max=99.0,
        update=_make_hot_callback("proc_brick_squash"),
    )
    proc_brick_squash_freq: IntProperty(
        name="Squash Frequency",
        description="How many rows between squash pulses",
        default=2, min=1, max=99,
        update=_make_hot_callback("proc_brick_squash_freq"),
    )
    proc_brick_mortar_size: FloatProperty(
        name="Mortar Size",
        description="Width of the mortar lines between bricks",
        default=0.02, min=0.0, max=0.125,
        update=_make_hot_callback("proc_brick_mortar_size"),
    )
    proc_brick_mortar_smooth: FloatProperty(
        name="Mortar Smooth",
        description="Edge softness of the mortar lines (0 = sharp, 1 = blurry)",
        default=0.1, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_brick_mortar_smooth"),
    )
    proc_brick_bias: FloatProperty(
        name="Bias",
        description="Color bias between brick1 and brick2 (-1..1)",
        default=0.0, min=-1.0, max=1.0,
        update=_make_hot_callback("proc_brick_bias"),
    )
    proc_brick_width: FloatProperty(
        name="Brick Width",
        description="Width of each brick cell in the Brick texture",
        default=0.5, min=0.001, max=100.0,
        update=_make_hot_callback("proc_brick_width"),
    )
    proc_brick_row_height: FloatProperty(
        name="Row Height",
        description="Height of each brick row in the Brick texture",
        default=0.25, min=0.001, max=100.0,
        update=_make_hot_callback("proc_brick_row_height"),
    )

    # ── Magic-specific parameters ───────────────────────────────────────
    proc_magic_depth: IntProperty(
        name="Depth",
        description="Number of iterations — higher = more swirly detail",
        default=2, min=0, max=10,
        update=_make_hot_callback("proc_magic_depth"),
    )
    # Dedicated distortion (proc_distortion is shared and defaults to 0.0,
    # which makes Magic produce flat vertical bands instead of the
    # expected coloured swirls). 1.0 gives the canonical "rainbow swirl"
    # users expect.
    proc_magic_distortion: FloatProperty(
        name="Distortion",
        description="Warp strength of the swirls — 0 = vertical bands, "
                    "1 = canonical swirl, higher = more chaotic",
        default=1.0, min=0.0, max=10.0,
        update=_make_hot_callback("proc_magic_distortion"),
    )

    # ── Gabor-specific parameters ───────────────────────────────────────
    # ShaderNodeTexGabor (Blender 4.3+) generates anisotropic Gabor noise:
    # directional streaks ideal for brushed metal, fiber weaves, hairline
    # scratches and other surfaces with a clear orientation. When the
    # node is unavailable (older Blender), the build falls back to a
    # tuned Wave-bands texture as approximation.
    proc_gabor_anisotropy: FloatProperty(
        name="Anisotropy",
        description="0 = isotropic noise (no direction); "
                    "1 = strongly directional parallel streaks",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_gabor_anisotropy"),
    )
    proc_gabor_orientation: FloatProperty(
        name="Orientation",
        description="Streak direction in degrees (0 = along X axis, "
                    "90 = along Y axis)",
        default=45.0, min=-360.0, max=360.0,
        update=_make_hot_callback("proc_gabor_orientation"),
    )
    # ── Fresnel-gradient-specific parameter ─────────────────────────────
    # When proc_type='FRESNEL', the procedural's fac comes from a Fresnel
    # node whose IOR is controlled here. Same semantics as the mask Fresnel
    # IOR — low (1.05-1.20) = wide angular sweep, high (2-5) = narrow rim.
    proc_fresnel_ior: FloatProperty(
        name="Fresnel IOR (Proc)",
        description="Index of refraction for the Fresnel Gradient procedural. "
                    "Low (1.05-1.20) = wide sweep across most viewing angles; "
                    "high (2.0-5.0) = pattern concentrated at grazing edges. "
                    "Glass ≈ 1.45, water ≈ 1.33, diamond ≈ 2.42",
        default=1.45, min=1.0, max=5.0,
        update=_make_hot_callback("proc_fresnel_ior"),
    )

    proc_gabor_frequency: FloatProperty(
        name="Frequency",
        description="Spatial frequency of the streak pattern — higher = "
                    "thinner / more closely packed streaks. Range up to 500 "
                    "is useful for very fine hairline brushed metal grooves.",
        default=2.0, min=0.1, max=500.0,
        update=_make_hot_callback("proc_gabor_frequency"),
    )

    # ── Dots-specific parameters ────────────────────────────────────────
    # Packed circles in a jittered grid: Voronoi F1 distance, thresholded
    # by Map Range, scaled by proc_scale. Useful for paint splatter,
    # polka dots, freckles and perforations.
    proc_dots_radius: FloatProperty(
        name="Dot Radius",
        description="Dot size as a fraction of the cell — 0 = no dots, "
                    "1 = dots fill the entire cell",
        default=0.35, min=0.01, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_dots_radius"),
    )
    proc_dots_softness: FloatProperty(
        name="Dot Softness",
        description="Edge falloff — 0 = hard circles, 1 = soft halos",
        default=0.15, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_dots_softness"),
    )

    # ── Ridged-specific parameters ──────────────────────────────────────
    # Classic ridged fractal noise: 1 - |2*noise - 1|, then sharpened.
    # Produces clean crests / valleys ideal for mountain ridges, rock
    # veins, lightning patterns and crackle textures.
    proc_ridged_offset: FloatProperty(
        name="Ridge Offset",
        description="Pre-fold offset — shifts where the ridge crest sits "
                    "(1.0 = symmetric ridges, lower = thicker bases)",
        default=1.0, min=0.0, max=2.0,
        update=_make_hot_callback("proc_ridged_offset"),
    )
    proc_ridged_gain: FloatProperty(
        name="Ridge Gain",
        description="Sharpness of the ridges — higher = thinner, more "
                    "razor-like crests",
        default=2.0, min=0.5, max=6.0,
        update=_make_hot_callback("proc_ridged_gain"),
    )

    # ── Cracks-specific parameters ──────────────────────────────────────
    # Voronoi distance-to-edge thresholded to thin organic crack lines.
    # Similar mechanism to HEX_GRID but with irregular cells and tuned
    # for narrow line geometry. proc_randomness controls cell jitter.
    proc_cracks_width: FloatProperty(
        name="Crack Width",
        description="Thickness of the crack lines (fraction of cell edge "
                    "distance)",
        default=0.05, min=0.005, max=0.3, subtype='FACTOR',
        update=_make_hot_callback("proc_cracks_width"),
    )
    proc_cracks_sharpness: FloatProperty(
        name="Crack Sharpness",
        description="Edge falloff — 0 = soft fissures, 1 = razor-sharp cracks",
        default=0.7, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_cracks_sharpness"),
    )

    # ── Stripes-specific parameters ─────────────────────────────────────
    # Stripes are built from a Wave (BANDS, SAW profile) thresholded
    # through a Map Range smoothstep — that lets the user dial both the
    # width of the lit stripe and the sharpness of its edge.
    proc_stripe_direction: EnumProperty(
        name="Direction",
        description="Axis along which stripes repeat",
        items=[
            ('X',        "X",        "Horizontal stripes (perpendicular to X)", 0),
            ('Y',        "Y",        "Vertical stripes (perpendicular to Y)",   1),
            ('DIAGONAL', "Diagonal", "Diagonal stripes",                        2),
            ('Z',        "Z",        "Depth-oriented stripes (perpendicular to Z)", 3),
        ],
        default='Y',
        update=_on_layer_update,  # structural — direction changes the wave node
    )
    proc_stripe_width: FloatProperty(
        name="Width",
        description="Fraction of each stripe period that is the bright stripe — "
                    "0 = no stripes, 0.5 = equal stripes, 1 = solid bright",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_stripe_width"),
    )
    proc_stripe_sharpness: FloatProperty(
        name="Sharpness",
        description="Edge hardness of the stripe — 0 = soft fade, 1 = perfectly sharp",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_stripe_sharpness"),
    )

    # ── Hex Gridâ€“specific parameters ────────────────────────────────────
    # Hex grid uses Voronoi(feature=DISTANCE_TO_EDGE) thresholded — true
    # regular hexagons need a custom UV transform, but the Voronoi
    # approximation gives a good honeycomb look with proc_randomness=0.
    proc_hex_edge_width: FloatProperty(
        name="Edge Width",
        description="Width of the hex grid lines — 0 = no lines, 0.5 = thick lines",
        default=0.05, min=0.0, max=0.5, subtype='FACTOR',
        update=_make_hot_callback("proc_hex_edge_width"),
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
    proc_rotation_x: FloatProperty(
        name="Rotation X",
        description="Rotate procedural coordinates around X before texture evaluation",
        subtype='ANGLE',
        default=0.0,
        update=_make_hot_callback("proc_rotation_x"),
    )
    proc_rotation_y: FloatProperty(
        name="Rotation Y",
        description="Rotate procedural coordinates around Y before texture evaluation",
        subtype='ANGLE',
        default=0.0,
        update=_make_hot_callback("proc_rotation_y"),
    )
    proc_rotation_z: FloatProperty(
        name="Rotation Z",
        description="Rotate procedural coordinates around Z before texture evaluation",
        subtype='ANGLE',
        default=0.0,
        update=_make_hot_callback("proc_rotation_z"),
    )
    proc_mapping_scale_x: FloatProperty(
        name="Scale X",
        description="Scale procedural coordinates along X before texture evaluation",
        default=1.0,
        update=_make_hot_callback("proc_mapping_scale_x"),
    )
    proc_mapping_scale_y: FloatProperty(
        name="Scale Y",
        description="Scale procedural coordinates along Y before texture evaluation",
        default=1.0,
        update=_make_hot_callback("proc_mapping_scale_y"),
    )
    proc_mapping_scale_z: FloatProperty(
        name="Scale Z",
        description="Scale procedural coordinates along Z before texture evaluation",
        default=1.0,
        update=_make_hot_callback("proc_mapping_scale_z"),
    )
    proc_mapping_type: EnumProperty(
        name="Mapping Type",
        description="Mapping vector type, matching the Blender Mapping node",
        items=[
            ('POINT',   "Point",   "Transform coordinates as points", 0),
            ('TEXTURE', "Texture", "Transform coordinates in texture mode", 1),
            ('VECTOR',  "Vector",  "Transform directions as vectors", 2),
            ('NORMAL',  "Normal",  "Transform normals", 3),
        ],
        default='POINT',
        update=_make_hot_callback("proc_mapping_type"),
    )

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
        description=(
            "Position of the third colour stop. "
            "Most procedurals: 0 = at Color1, 1 = at Color2. "
            "Stripes / Hex Grid: fraction of the stripe / edge that is "
            "the inner core colour (0 = no core, 1 = core fills the stripe)"
        ),
        default=0.5, min=0.01, max=0.99, subtype='FACTOR',
        update=_make_hot_callback("proc_color3_position"),
    )

    # ── Per-stop position controls (when proc_use_manual_stops=True) ──
    # In default mode the ColorRamp stop positions are computed from
    # proc_contrast + proc_ramp_center via _ramp_stops(). Enabling
    # proc_use_manual_stops switches to direct per-stop positions, so
    # you can drag each colour stop independently — same affordance as
    # editing a raw ColorRamp node, exposed through the TLM panel.
    proc_use_manual_stops: BoolProperty(
        name="Manual Stops",
        description="Drag each colour stop position directly (overrides Contrast + Ramp Center). "
                    "On (default): use Color1 Pos / Color2 Pos / extras Pos sliders for direct stop placement. "
                    "Off: positions are derived from Contrast + Ramp Center (legacy artist-friendly model).",
        default=True, update=_on_layer_update,
    )
    proc_color1_position: FloatProperty(
        name="Color 1 Pos",
        description="Position of the first colour stop on the ColorRamp (0..1). "
                    "Only active when Manual Stops is enabled. Default at 0.0 (start of the ramp).",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_color1_position"),
    )
    proc_color2_position: FloatProperty(
        name="Color 2 Pos",
        description="Position of the second colour stop on the ColorRamp (0..1). "
                    "Only active when Manual Stops is enabled. Default at 1.0 (end of the ramp).",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_color2_position"),
    )

    # ── ColorRamp mode + interpolation ──
    # These map 1:1 onto ShaderNodeValToRGB.color_ramp.color_mode and
    # .interpolation. HSV/HSL give hue-aware gradients (e.g. a smooth
    # red → green is "yellow" through HSV but "muddy brown" through RGB).
    # B_SPLINE / CARDINAL produce curved interpolation through the stops;
    # CONSTANT gives hard steps (good for stencil patterns).
    proc_color_ramp_mode: EnumProperty(
        name="Color Mode",
        description="Colour-space the ColorRamp interpolates in. "
                    "RGB blends linearly per channel (default). "
                    "HSV / HSL blend through hue space — rainbow gradients stay vivid.",
        items=[
            ('RGB', "RGB", "Linear blend per channel — default, neutral", 0),
            ('HSV', "HSV", "Hue/Saturation/Value blend — vivid through hue space", 1),
            ('HSL', "HSL", "Hue/Saturation/Lightness blend — alternative hue space", 2),
        ],
        default='RGB',
        update=_make_hot_callback("proc_color_ramp_mode"),
    )
    proc_color_ramp_interpolation: EnumProperty(
        name="Interpolation",
        description="How the ColorRamp interpolates between stops",
        items=[
            ('LINEAR',   "Linear",   "Straight linear blend between adjacent stops", 0),
            ('EASE',     "Ease",     "Smooth S-curve blend between adjacent stops", 1),
            ('B_SPLINE', "B-Spline", "Curved blend running through all stops (B-spline)", 2),
            ('CARDINAL', "Cardinal", "Curved blend running through all stops (Cardinal spline)", 3),
            ('CONSTANT', "Constant", "Hard steps — no interpolation between stops", 4),
        ],
        default='LINEAR',
        update=_make_hot_callback("proc_color_ramp_interpolation"),
    )

    # ── Extra ColorRamp stops (beyond Color1 / Color2 / Color3) ──
    # A user-managed list of additional stops layered on top of the
    # legacy 2-or-3-color model. Each stop is a TLM_ProcColorStop with
    # its own colour + position. The build path appends each to the
    # ColorRamp after Color1/Color2(/Color3). UIList in the panel with
    # tlm.add_proc_color_stop / tlm.remove_proc_color_stop operators.
    proc_extra_color_stops: bpy.props.CollectionProperty(type=TLM_ProcColorStop)
    proc_active_color_stop_index: IntProperty(
        name="Active Stop",
        description="Index of the currently selected extra color stop in the list",
        default=0, min=0,
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
        description="Which Voronoi metric drives the output: F1=distance to "
                    "nearest cell centre (filled cells), F2=second nearest, "
                    "Edge=distance to cell edge (for cracks/joints), "
                    "Radius=N-sphere radius",
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
        description="Distance metric for cell shapes: Euclidean (round), "
                    "Manhattan (axis-aligned), Chebychev (square cells), "
                    "Minkowski (parametric)",
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
        description="Wave layout: Bands = parallel stripes (use for wood "
                    "planks / striated patterns), Rings = concentric rings "
                    "(use for marble / wood end-grain / ripples)",
        items=[
            ('BANDS', "Bands", "Parallel bands",     0),
            ('RINGS', "Rings", "Concentric rings",    1),
        ],
        default='BANDS',
        update=_on_layer_update,
    )
    proc_wave_profile: EnumProperty(
        name="Profile",
        description="Cross-section of one wave cycle: Sine = smooth, "
                    "Sawtooth = sharp ramp, Triangle = symmetric zigzag",
        items=[
            ('SIN',      "Sine",     "Smooth sine wave",         0),
            ('SAW',      "Sawtooth", "Sharp sawtooth ramp",      1),
            ('TRI',      "Triangle", "Triangular zigzag wave",   2),
        ],
        default='SIN',
        update=_on_layer_update,
    )
    proc_wave_bands_direction: EnumProperty(
        name="Bands Direction",
        description="Axis along which the bands repeat",
        items=[
            ('X',        "X",        "Bands along the X axis", 0),
            ('Y',        "Y",        "Bands along the Y axis", 1),
            ('Z',        "Z",        "Bands along the Z axis", 2),
            ('DIAGONAL', "Diagonal", "Diagonal bands",         3),
        ],
        default='X',
        update=_on_layer_update,
    )
    proc_wave_rings_direction: EnumProperty(
        name="Rings Direction",
        description="Axis perpendicular to the ring plane (Spherical = "
                    "concentric 3D shells)",
        items=[
            ('X',         "X",         "Rings around the X axis", 0),
            ('Y',         "Y",         "Rings around the Y axis", 1),
            ('Z',         "Z",         "Rings around the Z axis", 2),
            ('SPHERICAL', "Spherical", "Spherical rings",         3),
        ],
        default='X',
        update=_on_layer_update,
    )
    proc_wave_detail_scale: FloatProperty(
        name="Detail Scale", description="Scale of the detail noise overlaid on the wave",
        default=1.0, min=0.0, max=10.0,
        update=_make_hot_callback("proc_wave_detail_scale"),
    )
    proc_wave_detail_roughness: FloatProperty(
        name="Detail Roughness",
        description="Roughness of the wave detail noise",
        default=0.5, min=0.0, max=1.0,
        update=_make_hot_callback("proc_wave_detail_roughness"),
    )
    proc_wave_phase_offset: FloatProperty(
        name="Phase Offset",
        description="Shift the wave phase without moving the mapping coordinates",
        default=0.0, min=-100.0, max=100.0,
        update=_make_hot_callback("proc_wave_phase_offset"),
    )

    # Gradient
    proc_gradient_type: EnumProperty(
        name="Gradient Type",
        description="Gradient shape: Linear/Quadratic/Easing/Diagonal go "
                    "across the surface, Spherical/Radial radiate from a "
                    "centre — pair with Color Ramp for masks or gradients",
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
    proc_marble_wave_profile: EnumProperty(
        name="Profile",
        description="Wave profile used by the marble bands",
        items=[
            ('SIN', "Sine", "Smooth sine wave",       0),
            ('SAW', "Saw",  "Sharp sawtooth ramp",    1),
            ('TRI', "Tri",  "Triangular zigzag wave", 2),
        ],
        default='SIN',
        update=_on_layer_update,
    )
    proc_marble_bands_direction: EnumProperty(
        name="Bands Direction",
        description="Direction of marble bands",
        items=[
            ('X',        "X",        "Bands along the X axis", 0),
            ('Y',        "Y",        "Bands along the Y axis", 1),
            ('Z',        "Z",        "Bands along the Z axis", 2),
            ('DIAGONAL', "Diagonal", "Diagonal bands",         3),
        ],
        default='X',
        update=_on_layer_update,
    )
    proc_marble_rings_direction: EnumProperty(
        name="Rings Direction",
        description="Direction of marble rings",
        items=[
            ('X',         "X",         "Rings around the X axis", 0),
            ('Y',         "Y",         "Rings around the Y axis", 1),
            ('Z',         "Z",         "Rings around the Z axis", 2),
            ('SPHERICAL', "Spherical", "Spherical rings",         3),
        ],
        default='X',
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
    proc_ramp_center: FloatProperty(
        name="Ramp Center",
        description="Where the Color1→Color2 transition band sits along the procedural Fac "
                    "(0=low end, 0.5=middle/symmetric, 1=high end). Combined with Contrast, "
                    "lets you make asymmetric thin outlines (band near 0 with high contrast = "
                    "thin Color1 line at low Fac), or thin highlights (band near 1)",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("proc_ramp_center"),
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

    # ── Selective emission ────────────────────────────────────────────────────
    # The procedural emission pipeline (proc fac → invert → power →
    # smoothstep → emission_color) lights up EVERY pixel where the
    # procedural's fac says so. For sci-fi panel use cases the artist
    # usually wants only SOME regions to glow (e.g. a random subset of
    # cells, or a hand-painted area), not the entire pattern.
    #
    # The selector is a secondary 0..1 mask multiplied into the emission
    # mask AFTER the smoothstep but BEFORE the emission_color mix, so
    # the original procedural threshold/falloff still shape the glow,
    # while the selector decides WHERE the glow is allowed at all.
    emission_selector_type: EnumProperty(
        name="Selective Emission",
        description="Gate the procedural emission to a subset of regions. "
                    "None = the whole procedural pattern glows (current "
                    "behaviour); other modes restrict the glow to a subset",
        items=[
            ('NONE',         "None",         "Uniform — every fac-positive pixel glows"),
            ('RANDOM_CELLS', "Random Cells", "Voronoi-cell based: only some cells glow, "
                                              "controlled by selector_threshold (=fraction lit). "
                                              "Best for circuit-board / panel-grid effects"),
            ('NOISE',        "Noise",        "Soft organic blobs — glows where a Perlin noise "
                                              "is above selector_threshold"),
            ('IMAGE',        "Image",        "Use a painted black/white image as the selector. "
                                              "White = lit, Black = unlit"),
        ],
        default='NONE',
        update=_on_layer_update,
    )
    emission_selector_scale: FloatProperty(
        name="Selector Scale",
        description="Scale of the selector pattern (cells per unit for "
                    "RANDOM_CELLS, noise frequency for NOISE)",
        default=4.0, min=0.1, max=200.0,
        update=_make_hot_callback("emission_selector_scale"),
    )
    emission_selector_threshold: FloatProperty(
        name="Selector Threshold",
        description="Fraction of the surface allowed to emit. "
                    "0 = nothing lit, 0.5 = half, 1 = everywhere lit "
                    "(equivalent to selector type = None)",
        default=0.3, min=0.0, max=1.0, subtype='FACTOR',
        update=_make_hot_callback("emission_selector_threshold"),
    )
    emission_selector_seed: FloatProperty(
        name="Selector Seed",
        description="Randomization seed — change to get a different "
                    "subset of glowing regions without altering anything else",
        default=0.0,
        update=_make_hot_callback("emission_selector_seed"),
    )
    emission_selector_image_name: StringProperty(
        name="Selector Image",
        description="Image datablock used as the selective-emission mask "
                    "(only when emission_selector_type = IMAGE). White = lit",
        default="",
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

    shader_editable: BoolProperty(
        name="Editable Shader",
        description=(
            "The current node tree has been converted to a normal Blender "
            "shader. TLM layer rebuilds are disabled until you return to "
            "TLM-managed mode"
        ),
        default=False,
    )

    use_emission_output: BoolProperty(
        name="Emission Output (Anime/Toon Mode)",
        description=(
            "Route the TLM Base Color stack to BSDF.Emission Color "
            "instead of BSDF.Base Color, with Base Color forced to BLACK. "
            "This BYPASSES Cycles' natural NdotL diffuse shading — necessary "
            "for true cel-shading where shadow band colours must NOT be "
            "multiplied by cos(NdotL). Use for anime/toon-style materials "
            "driven by NDOTL mask source bands"
        ),
        default=False,
    )

    # ── Material-level BSDF IOR (Index Of Refraction) ──
    # Drives BSDF.IOR on rebuild. Per-material because IOR is a physical
    # property of the medium, not per-layer. Common values: 1.00 air,
    # 1.31 ice, 1.33 water, 1.45 glass (BSDF default), 1.52 crown glass,
    # 1.77 sapphire, 2.42 diamond. Without this property, refraction-based
    # presets (ice, water, gems) had to set bsdf.inputs["IOR"] manually
    # after rebuild — fragile because the next rebuild reset it.
    bsdf_ior: bpy.props.FloatProperty(
        name="IOR",
        description="Index of Refraction. Affects refraction angle in transmission and "
                    "the strength of Fresnel reflections on Base Color. "
                    "Common values: 1.31 ice, 1.33 water, 1.45 glass (default), "
                    "1.52 crown glass, 2.42 diamond. 1.00 = no refraction (air).",
        default=1.45, min=1.00, max=3.50,
        subtype='UNSIGNED',
        update=lambda self, ctx: _on_bsdf_ior_change(self, ctx),
    )

    # ── Volume Absorption ──
    # Wires a Volume Absorption shader to Material Output.Volume. As light
    # rays travel through the mesh interior they get tinted+attenuated by
    # this color × density (Beer–Lambert absorption). Without this, true
    # ice/water/jade/gem materials look "thin" — the tint comes only from
    # surface base_color, not from the path through the volume.
    # Density of 1.0 = light loses 63% intensity per unit (Blender unit).
    # For a 2m diameter sphere, density 0.5–2.0 reads as "tinted glass".
    use_volume_absorption: BoolProperty(
        name="Volume Absorption",
        description="Wire a Volume Absorption shader to Material Output.Volume. "
                    "Gives refractive materials (ice, water, gems) the natural "
                    "'darker inside' look that comes from light getting absorbed "
                    "as it travels through the volume.",
        default=False,
        update=lambda self, ctx: _on_volume_absorption_change(self, ctx),
    )
    volume_absorption_color: bpy.props.FloatVectorProperty(
        name="Volume Color",
        description="Tint of the volume absorption. Light becomes more this colour "
                    "as it travels through the mesh interior. For ice: light cyan. "
                    "For amber: warm orange. For jade: green.",
        subtype='COLOR', min=0.0, max=1.0, size=4,
        default=(0.55, 0.75, 0.95, 1.0),
        update=lambda self, ctx: _on_volume_absorption_param_change(self, ctx),
    )
    volume_absorption_density: bpy.props.FloatProperty(
        name="Density",
        description="How quickly light is absorbed per unit distance. "
                    "Higher = darker interior. 0.0 = no absorption, 1.0 = moderate, "
                    "5.0 = strongly tinted (dark gem). Ice: 0.5-1.5. "
                    "Water: 0.1-0.5. Coloured gem: 2.0-8.0.",
        default=1.0, min=0.0, max=100.0,
        subtype='UNSIGNED',
        update=lambda self, ctx: _on_volume_absorption_param_change(self, ctx),
    )

    # ── Volume Scatter ──
    # Where Volume Absorption tints+attenuates light along its path,
    # Volume Scatter actually scatters light SIDEWAYS within the volume,
    # producing the "milky/cloudy" depth effect. Combined with Absorption
    # this gives realistic ice (subtle haze + blue tint), jade (green
    # tint + heavy scatter), wax (warm tint + medium scatter), milk
    # (white + high scatter), smoke (grey + strong forward scatter).
    use_volume_scatter: BoolProperty(
        name="Volume Scatter",
        description="Wire a Volume Scatter shader to Material Output.Volume "
                    "(combined via Add Shader if Volume Absorption is also on). "
                    "Scatter makes light bounce inside the volume, giving the "
                    "milky/cloudy interior look that absorption alone can't.",
        default=False,
        update=lambda self, ctx: _on_volume_scatter_change(self, ctx),
    )
    volume_scatter_color: bpy.props.FloatVectorProperty(
        name="Scatter Color",
        description="Colour the scattering tints. Often kept close to white "
                    "for ice/water; tinted for jade (green), wax (warm).",
        subtype='COLOR', min=0.0, max=1.0, size=4,
        default=(0.92, 0.96, 1.00, 1.0),
        update=lambda self, ctx: _on_volume_scatter_param_change(self, ctx),
    )
    volume_scatter_density: bpy.props.FloatProperty(
        name="Scatter Density",
        description="How much the volume scatters light. Higher = more milky. "
                    "Ice (subtle haze): 0.1-0.5. Wax: 0.5-1.5. Jade: 1.0-3.0. "
                    "Milk (opaque): 3.0+. 0 = no scatter (clear).",
        default=0.5, min=0.0, max=100.0,
        subtype='UNSIGNED',
        update=lambda self, ctx: _on_volume_scatter_param_change(self, ctx),
    )
    volume_scatter_anisotropy: bpy.props.FloatProperty(
        name="Anisotropy",
        description="Direction bias of the scatter. 0 = isotropic (uniform "
                    "scatter, default for ice/jade). Positive = forward scatter "
                    "(0.3-0.6 for smoke/fog/clouds). Negative = back scatter "
                    "(rare; gives 'rim glow' from behind).",
        default=0.0, min=-1.0, max=1.0,
        subtype='FACTOR',
        update=lambda self, ctx: _on_volume_scatter_param_change(self, ctx),
    )

    # ── True geometric Displacement (material-level master switch) ──
    # Wires a ShaderNodeDisplacement to Material Output.Displacement when
    # ON. Each PROCEDURAL/PAINT layer with its own `use_displacement=True`
    # contributes a height summed into the displacement node's Height
    # input. Requires Cycles + Adaptive Subdivision on the mesh for actual
    # vertex movement (else falls back to bump-style normal perturbation).
    # When `displacement_adaptive` is True, the rebuild auto-configures
    # cycles.feature_set='EXPERIMENTAL' + Subsurf modifier with adaptive
    # subdivision on every mesh using this material.
    use_displacement: BoolProperty(
        name="Displacement",
        description="Wire the layer stack's displacement contributions to "
                    "Material Output.Displacement. Unlike Bump (shading-only), "
                    "this moves real geometry (silhouette breaks). Needs "
                    "Cycles + Adaptive Subdivision for visible effect.",
        default=False,
        update=lambda self, ctx: _on_displacement_change(self, ctx),
    )
    displacement_strength: FloatProperty(
        name="Displacement Strength",
        description="Master multiplier on the displacement height. 0 = flat. "
                    "0.05-0.20 = typical (small details). 0.5+ = dramatic chunky "
                    "displacement (rocky pile, brick wall). Combined with "
                    "per-layer displacement_scale.",
        default=0.1, min=0.0, max=5.0,
        update=lambda self, ctx: _on_displacement_param_change(self, ctx),
    )
    displacement_midlevel: FloatProperty(
        name="Midlevel",
        description="Height value treated as 'neutral' (no displacement). "
                    "0.5 (default) means values around 0.5 stay at the original "
                    "surface; >0.5 pushes out; <0.5 pushes in.",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        update=lambda self, ctx: _on_displacement_param_change(self, ctx),
    )
    displacement_adaptive: BoolProperty(
        name="Adaptive Subdivision",
        description="Auto-enable Cycles experimental + add a Subsurf modifier "
                    "with adaptive subdivision on meshes using this material. "
                    "Required for the displacement to actually move geometry "
                    "(otherwise Cycles falls back to bump-like shading). Off "
                    "if you've already set this up manually.",
        default=True,
        update=lambda self, ctx: _on_displacement_change(self, ctx),
    )
    displacement_method: EnumProperty(
        name="Displacement Method",
        description="How Cycles converts the Displacement output to surface deformation.\n"
                    "• Bump Only: shading normal only (no silhouette break, no subdiv needed). "
                    "Same as Bump but uses the unified Displacement chain.\n"
                    "• Displacement Only (default): real vertex movement — silhouette breaks. "
                    "Requires Cycles + Adaptive Subdivision.\n"
                    "• Both: large vertices move (Displacement) and fine details survive as "
                    "Bump on top. Best for chunky materials with micro-grain.",
        items=[
            ('BUMP',         "Bump Only",         "Shading normal only (default Blender behaviour). No silhouette break, no subdiv required.", 0),
            ('DISPLACEMENT', "Displacement Only", "Real vertex movement, silhouette breaks. Needs Adaptive Subdivision.",                       1),
            ('BOTH',         "Both",              "Vertex displacement + bump in one — combine big shapes with micro detail.",                  2),
        ],
        default='DISPLACEMENT',
        update=lambda self, ctx: _on_displacement_change(self, ctx),
    )

    # use_custom_slots: BoolProperty(
    #     name="Custom Slots",
    #     description=(
    #         "Insert stable pass-through node groups between TLM channels and "
    #         "the shader so custom Shader Editor nodes can survive rebuilds"
    #     ),
    #     default=False,
    #     update=_on_layer_update,
    # )

    # When ON, the alpha output of the base color chain (typically the
    # alpha of a PAINT layer's image) is wired to BSDF.Alpha. Lets a
    # PAINT layer with a transparent PNG render as transparent and bake
    # to a real RGBA PNG without setting up a dedicated Output=Alpha layer.
    # Default OFF — without this, an empty PAINT layer (alpha=0 everywhere)
    # would unintentionally hide the cube.
    use_base_color_alpha: BoolProperty(
        name="Use PNG Alpha Channel",
        description=(
            "Wire the ALPHA CHANNEL of a PAINT layer's image to BSDF.Alpha. "
            "Use this when a PAINT layer carries a PNG with real transparency "
            "(foliage textures, decals, stickers) — the PNG's transparent "
            "pixels become transparent on the material in one layer.\n\n"
            "Different from a layer set to 'Output: Alpha':\n"
            "  • This reads the IMAGE's alpha channel (transparent PNG pixels)\n"
            "  • Output:Alpha reads the layer's RGB/value (paint a B&W mask)\n\n"
            "Has NO effect on TLM-generated paint canvases (their alpha is "
            "always 1.0) or on solid PNGs without an alpha channel"
        ),
        default=False,
        update=_on_layer_update,
    )

    # Eevee/Material Preview transparency mode. Even when TLM wires
    # BSDF.Alpha correctly, Eevee with mat.blend_method='OPAQUE' (the
    # default) silently ignores the input and renders the surface
    # opaque — a confusing UX where the user sees no effect from
    # output_channel=ALPHA layers.
    #
    # AUTO: TLM decides based on what's connected. When any layer
    # contributes to alpha (or use_base_color_alpha is on), use the
    # picked-up 'HASHED' equivalent (=DITHERED on 4.2+) which is the
    # best general default; otherwise OPAQUE for performance.
    # Other modes let the artist override (e.g. force BLEND for glass).
    #
    # Blender 4.2 split the legacy `blend_method` enum into a new
    # `surface_render_method` with just DITHERED / BLENDED. We map our
    # legacy values onto the new API at rebuild time and set both for
    # cross-version compatibility.
    alpha_blend_method: EnumProperty(
        name="Alpha Blend Method",
        description=(
            "How Eevee handles the BSDF.Alpha input. 'Auto' picks Hashed "
            "when any alpha layer is present, Opaque otherwise. Override "
            "manually for glass (Blend) / mask-cutout (Clip) workflows"
        ),
        items=[
            ('AUTO',   "Auto",
             "Hashed when alpha is wired, Opaque otherwise (recommended)"),
            ('OPAQUE', "Opaque",
             "Ignore alpha — no transparency"),
            ('CLIP',   "Clip",
             "Binary cutout — alpha < 0.5 is fully transparent (foliage masks)"),
            ('HASHED', "Hashed",
             "Stochastic dithering — supports smooth alpha, anti-aliased "
             "edges (general-purpose default for decals / cutout)"),
            ('BLEND',  "Blend",
             "True alpha blending — needed for glass, ghosts, smoke. "
             "Costs sorting and may have artefacts on overlapping faces"),
        ],
        default='AUTO',
        update=_on_layer_update,
    )

    # Resolution for new layers
    resolution: EnumProperty(
        name="New Layer Resolution",
        description="Default image size used when adding a new Paint layer "
                    "or generating a Smart Mask. Existing layers are NOT "
                    "resized — change this BEFORE adding the layer",
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

    performance_debug: BoolProperty(
        name="Performance Debug",
        description="Show lightweight timing data for rebuilds and heavy operations",
        default=False,
    )

    perf_last_rebuild_ms: FloatProperty(
        name="Last Rebuild ms",
        default=0.0,
        min=0.0,
        precision=2,
    )
    perf_last_hot_update_ms: FloatProperty(
        name="Last Hot Update ms",
        default=0.0,
        min=0.0,
        precision=2,
    )
    perf_last_hot_update_prop: StringProperty(
        name="Last Hot Update Prop",
        default="",
    )
    perf_last_hot_update_ok: BoolProperty(
        name="Last Hot Update OK",
        default=False,
    )
    perf_last_bake_ms: FloatProperty(
        name="Last Bake ms",
        default=0.0,
        min=0.0,
        precision=2,
    )
    perf_last_bake_maps: IntProperty(
        name="Last Bake Maps",
        default=0,
        min=0,
    )
    perf_last_smart_mask_ms: FloatProperty(
        name="Last Smart Mask ms",
        default=0.0,
        min=0.0,
        precision=2,
    )
    perf_last_smart_mask_ok: BoolProperty(
        name="Last Smart Mask OK",
        default=False,
    )
    perf_last_node_count: IntProperty(
        name="Node Count",
        default=0,
        min=0,
    )
    perf_last_layer_count: IntProperty(
        name="Layer Count",
        default=0,
        min=0,
    )
    perf_last_visible_layer_count: IntProperty(
        name="Visible Layer Count",
        default=0,
        min=0,
    )
    perf_last_mesh_vertices: IntProperty(
        name="Mesh Vertices",
        default=0,
        min=0,
    )
    perf_last_mesh_faces: IntProperty(
        name="Mesh Faces",
        default=0,
        min=0,
    )
    perf_last_mesh_objects: IntProperty(
        name="Mesh Objects",
        default=0,
        min=0,
    )

    @property
    def active_layer(self):
        if 0 <= self.active_layer_index < len(self.layers):
            return self.layers[self.active_layer_index]
        return None


# ─── Registration ─────────────────────────────────────────────────────────────

classes = [
    TLM_ProcColorStop,   # MUST register before TLM_LayerItem (it's the type= for the CollectionProperty)
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
