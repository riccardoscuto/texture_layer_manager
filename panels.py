"""
panels.py — UI for Texture Layer Manager.
Properties panel: Properties > Material > Texture Layers
N-panel:          3D Viewport > N-panel > TLM tab

UI v2 changes:
- Toolbar split into two rows (Add / Operations)
- Auto Composite toggle always visible above the list
- PBR channel badges shown inline in the layer list
- Active layer box: type badge + editable name in header
- Compact PBR channel grid with toggle buttons
- Settings panel reorganised into clear sub-sections
- Main panel open by default (removed DEFAULT_CLOSED)
- N-panel viewport open by default too
"""

import bpy
import os as _os
import time as _time
from bpy.types import Panel, UIList
from . import previews

# Cache user presets list to avoid os.listdir on every draw call
_preset_cache = []
_preset_cache_time = 0.0
_PRESET_CACHE_TTL = 2.0  # seconds

_COLOR_TAG_ICON = {
    'RED': 'COLLECTION_COLOR_01', 'ORANGE': 'COLLECTION_COLOR_02',
    'YELLOW': 'COLLECTION_COLOR_03', 'GREEN': 'COLLECTION_COLOR_04',
    'BLUE': 'COLLECTION_COLOR_05', 'PURPLE': 'COLLECTION_COLOR_06',
    'PINK': 'COLLECTION_COLOR_07',
}

_PBR_BADGE = {
    'use_roughness':    'RNDCURVE',
    'use_metallic':     'MATFLUID',
    'use_normal':       'NORMALS_FACE',
    'use_emission':     'LIGHT',
    'use_transmission': 'MATSPHERE',
    'use_alpha':        'IMAGE_ALPHA',
    'use_bump':         'MOD_DISPLACE',
}

# Output channel → icon shown next to the layer name in the UIList.
# Mirrors the routing decision so the user can scan the stack and see
# which layer drives which BSDF input at a glance.
_OUTPUT_BADGE = {
    'BASE_COLOR': 'COLOR',
    'ROUGHNESS':  'RNDCURVE',
    'METALLIC':   'MATFLUID',
    'ALPHA':      'IMAGE_ALPHA',
}


def _is_at_compositor_bottom(active, tlm):
    """True if ``active`` will be the first layer composited in its scope.

    The compositor processes ``reversed(tlm.layers)`` (see compositing.py
    _composite_layer_list and rebuild_node_tree). Within a scope — root
    (``group_name == ""``) or the same non-empty ``group_name`` — the layer
    with the highest tlm.layers index is the first to be composited. For
    that layer ``prev_alpha`` is always None, so Clipping Mask silently
    does nothing (see compositing.py _set_factor, clipping branch).

    We use this to surface a UI warning next to the Clipping Mask toggle.
    Invisible and ADJUSTMENT siblings are skipped because they don't
    produce a ``prev_alpha`` either.
    """
    if active is None or tlm is None:
        return False
    try:
        active_idx = list(tlm.layers).index(active)
    except ValueError:
        return False
    scope = active.group_name
    for i in range(active_idx + 1, len(tlm.layers)):
        other = tlm.layers[i]
        if not other.visible:
            continue
        if other.layer_type == "ADJUSTMENT":
            continue
        if other.group_name == scope:
            return False
    return True


def _draw_clipping_mask(col, active, tlm):
    """Draw the Clipping Mask toggle + a warning label if the layer is at
    the compositor bottom (where the clip silently has no effect)."""
    col.prop(active, "use_clipping_mask",
             text="Clipping Mask", icon='CLIPUV_DEHLT', toggle=True)
    if active.use_clipping_mask and _is_at_compositor_bottom(active, tlm):
        col.label(text="No effect — nothing to clip against below",
                  icon='ERROR')


class TLM_UL_LayerList(UIList):
    bl_idname = "TLM_UL_layer_list"

    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        layer = item
        if self.layout_type not in {'DEFAULT', 'COMPACT'}:
            layout.label(text="", icon='IMAGE_DATA')
            return

        row = layout.row(align=True)

        is_child = layer.group_name != ""
        if is_child:
            row.separator(factor=2.5)

        ct_icon = _COLOR_TAG_ICON.get(layer.color_tag)
        if ct_icon:
            row.label(text="", icon=ct_icon)

        if layer.layer_type == "GROUP":
            col_icon = 'TRIA_DOWN' if not layer.collapsed else 'TRIA_RIGHT'
            op = row.operator("tlm.toggle_group_collapse", text="", icon=col_icon, emboss=False)
            op.layer_index = index
            row.label(text="", icon='FILE_FOLDER')
        elif layer.layer_type == "FILL":
            # Native color widget — also serves as a quick-edit click target.
            # Avoids creating .tlm_swatch_* image datablocks that would pollute
            # the bpy.data.images dropdowns used to pick textures elsewhere.
            # scale_x compresses to roughly the same visual width as the paint
            # preview icon (16px). Blender enforces a minimum widget width so
            # values below ~0.3 don't shrink further.
            swatch = row.row(align=True)
            swatch.scale_x = 0.35
            swatch.prop(layer, "fill_color", text="")
        else:
            fallback = {
                'PAINT': 'IMAGE_RGB_ALPHA',
                'ADJUSTMENT': 'MODIFIER', 'PROCEDURAL': 'TEXTURE',
                'REFERENCE': 'LINKED',
            }.get(layer.layer_type, 'IMAGE_DATA')
            iid = 0
            try:
                if layer.layer_type == "PAINT":
                    iid = previews.get_layer_icon_id(layer)
            except Exception:
                iid = 0
            if iid and iid > 0:
                row.label(text="", icon_value=iid)
            else:
                row.label(text="", icon=fallback)

        vis_icon = 'HIDE_OFF' if layer.visible else 'HIDE_ON'
        op = row.operator("tlm.toggle_layer_visibility", text="", icon=vis_icon, emboss=False)
        op.layer_index = index

        solo_icon = 'OUTLINER_OB_LIGHT' if (data.solo_layer_index == index) else 'LIGHT'
        op = row.operator("tlm.solo_layer", text="", icon=solo_icon, emboss=False)
        op.layer_index = index

        if layer.layer_type != "GROUP":
            lock_icon = 'LOCKED' if layer.locked else 'UNLOCKED'
            row.prop(layer, "locked", text="", icon=lock_icon, emboss=False)

        row.prop(layer, "name", text="", emboss=False)

        if layer.layer_type not in ("GROUP", "ADJUSTMENT"):
            badge_row = row.row(align=True)
            badge_row.scale_x = 0.7
            for flag, badge_icon in _PBR_BADGE.items():
                if getattr(layer, flag, False):
                    badge_row.label(text="", icon=badge_icon)

        if layer.layer_type not in ("GROUP", "ADJUSTMENT"):
            op = row.operator("tlm.set_active_paint_layer", text="", icon='BRUSH_DATA', emboss=False)
            op.layer_index = index
            clip_icon = 'CLIPUV_HLT' if layer.use_clipping_mask else 'CLIPUV_DEHLT'
            row.prop(layer, "use_clipping_mask", text="", icon=clip_icon, emboss=False)
            row.prop(layer, "blend_mode", text="")
            row.prop(layer, "opacity", text="", slider=True)
        elif layer.layer_type == "GROUP":
            row.prop(layer, "opacity", text="", slider=True)

    def filter_items(self, context, data, propname):
        layers = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(layers)
        order = list(range(len(layers)))

        # Built-in name search (shown via the funnel icon)
        if self.filter_name:
            search = self.filter_name.lower()
            for i, layer in enumerate(layers):
                if search not in layer.name.lower():
                    flags[i] &= ~self.bitflag_filter_item

        collapsed = {l.name for l in layers if l.layer_type == "GROUP" and l.collapsed}
        for i, layer in enumerate(layers):
            if layer.group_name in collapsed:
                flags[i] = 0
        return flags, order


def draw_tlm_main(layout, context):
    obj = context.active_object
    if not obj or not obj.active_material:
        layout.label(text="No active material", icon='ERROR')
        return

    mat = obj.active_material
    tlm = mat.tlm
    n = len(tlm.layers)

    header = layout.row(align=True)
    header.label(text=mat.name, icon='MATERIAL')
    badge = header.row(align=True)
    badge.alignment = 'RIGHT'
    badge.label(text=f"{n} layer{'s' if n != 1 else ''}")
    ac_icon = 'LINKED' if tlm.auto_composite else 'UNLINKED'
    badge.prop(tlm, "auto_composite", text="", icon=ac_icon, toggle=True, emboss=True)

    layout.separator(factor=0.5)

    add_row = layout.row(align=True)
    add_row.scale_y = 1.1
    add_row.operator("tlm.add_paint_layer",       text="Paint", icon='IMAGE_RGB_ALPHA')
    add_row.operator("tlm.add_fill_layer",         text="Fill",  icon='COLOR')
    add_row.operator("tlm.add_adjustment_layer",   text="Adj",   icon='MODIFIER')
    add_row.operator("tlm.add_procedural_layer",   text="Proc",  icon='TEXTURE')
    add_row.operator("tlm.add_reference_layer",    text="Ref",   icon='LINKED')
    add_row.operator("tlm.add_group",              text="",      icon='FILE_FOLDER')
    add_row.separator()
    add_row.operator("tlm.import_texture_as_layer", text="", icon='IMPORT')
    add_row.operator("tlm.layer_from_clipboard",    text="", icon='COPYDOWN')

    ops_row = layout.row(align=True)
    ops_row.operator("tlm.remove_layer",    text="", icon='TRASH')
    ops_row.separator()
    ops_row.operator("tlm.move_layer_to_end", text="", icon='TRIA_UP_BAR').direction = "TOP"
    ops_row.operator("tlm.move_layer",      text="", icon='TRIA_UP').direction   = "UP"
    ops_row.operator("tlm.move_layer",      text="", icon='TRIA_DOWN').direction = "DOWN"
    ops_row.operator("tlm.move_layer_to_end", text="", icon='TRIA_DOWN_BAR').direction = "BOTTOM"
    ops_row.separator()
    ops_row.operator("tlm.duplicate_layer", text="", icon='DUPLICATE')

    layout.template_list(
        "TLM_UL_layer_list", "",
        mat.tlm, "layers",
        mat.tlm, "active_layer_index",
        rows=6,
    )

    active = tlm.active_layer
    if not active:
        return
    _draw_active_layer(layout, active, tlm, mat)


def _draw_active_layer(layout, active, tlm, mat):
    ltype_icon = {
        'PAINT': 'IMAGE_RGB_ALPHA', 'FILL': 'COLOR',
        'ADJUSTMENT': 'MODIFIER', 'GROUP': 'FILE_FOLDER',
        'PROCEDURAL': 'TEXTURE', 'REFERENCE': 'LINKED',
    }.get(active.layer_type, 'IMAGE_DATA')

    ltype_label = {
        'PAINT': "Paint Layer", 'FILL': "Fill Layer",
        'ADJUSTMENT': "Adjustment Layer", 'GROUP': "Group",
        'PROCEDURAL': "Procedural Layer", 'REFERENCE': "Reference Layer",
    }.get(active.layer_type, "Layer")

    box = layout.box()
    hrow = box.row(align=True)
    hrow.label(text=ltype_label, icon=ltype_icon)
    hrow.prop(active, "name", text="", emboss=True)
    # Solo button removed from here — lives in the UIList row, and after the
    # solo-also-selects fix the active layer == soloed layer when toggled
    # from the list. Showing it here too was redundant.
    hrow.prop(active, "color_tag", text="", icon_only=True)

    col = box.column(align=True)
    # Lock: disable the entire detail column when layer is locked.
    # Header row (name/solo/color_tag/lock toggle) stays editable so the
    # user can always unlock; this only greys out blend/opacity/channels/
    # mask/branching/procedural params.
    col.enabled = not active.locked

    if active.layer_type == "PROCEDURAL":
        _draw_procedural(col, active, tlm)
    elif active.layer_type == "REFERENCE":
        _draw_reference(col, active, tlm)
    elif active.layer_type == "GROUP":
        # Blend mode + opacity row — group treats its composited output as a
        # single layer, so these apply to the entire folder.
        br = col.row(align=True)
        if getattr(active, 'output_channel', 'BASE_COLOR') != 'ALPHA':
            br.prop(active, "blend_mode", text="")
        br.prop(active, "opacity", text="Opacity", slider=True)
        br.operator("tlm.keyframe_opacity", text="", icon='KEYFRAME_HLT',
                    emboss=False).action = 'INSERT'
        children = [l for l in tlm.layers if l.group_name == active.name]
        n_vis = sum(1 for l in children if l.visible)
        col.label(
            text=f"{len(children)} layer{'s' if len(children) != 1 else ''}  ({n_vis} visible)",
            icon='LAYER_ACTIVE'
        )
        # Mask section — masks the group's composited output so the mask
        # applies uniformly to every child (group mask: one mask shared by
        # the whole folder, applied after children compositing).
        col.separator(factor=0.4)
        _draw_mask_block(col, active)
    elif active.layer_type == "ADJUSTMENT":
        _draw_adjustment(col, active, tlm)
    else:
        _draw_paint_fill(col, active, tlm)


def _draw_procedural(col, active, tlm):
    # Blend mode + Opacity at top (same position as Fill/Paint)
    br = col.row(align=True)
    # Hide blend mode when the layer routes to Alpha — alpha is coverage,
    # not a colour; artistic blend modes (Overlay/Hard Light/etc.) make no
    # sense there. The compositor force-sets MIX for alpha regardless.
    if getattr(active, 'output_channel', 'BASE_COLOR') != 'ALPHA':
        br.prop(active, "blend_mode", text="")
    br.prop(active, "opacity",    text="Opacity", slider=True)
    br.operator("tlm.keyframe_opacity", text="", icon='KEYFRAME_HLT',
                emboss=False).action = 'INSERT'
    oc = col.row(align=True)
    oc.prop(active, "output_channel", text="Output", icon='NODE_COMPOSITING')

    col.separator(factor=0.5)
    col.prop(active, "proc_type")
    col.separator(factor=0.5)
    cr = col.row(align=True)
    cr.prop(active, "proc_color1", text="")
    cr.prop(active, "proc_color2", text="")
    c3r = col.row(align=True)
    c3r.prop(active, "use_proc_color3", text="", icon='ADD' if not active.use_proc_color3 else 'REMOVE', toggle=True)
    if active.use_proc_color3:
        c3r.prop(active, "proc_color3", text="")
        c3r.prop(active, "proc_color3_position", text="Pos", slider=True)
    else:
        c3r.label(text="Color 3")
    col.separator(factor=0.5)
    col.prop(active, "proc_scale", slider=False)
    col.separator(factor=0.5)

    pt = active.proc_type
    if pt == 'NOISE':
        col.prop(active, "proc_detail",         slider=True)
        col.prop(active, "proc_roughness_proc", slider=True, text="Roughness")
        col.prop(active, "proc_lacunarity",     slider=True)
        col.prop(active, "proc_distortion",     slider=True)
    elif pt == 'VORONOI':
        col.prop(active, "proc_voronoi_feature")
        col.prop(active, "proc_voronoi_distance")
        col.prop(active, "proc_randomness", slider=True)
        vr = col.row(align=True)
        vr.prop(active, "proc_voronoi_random_color", text="Random Per Cell", toggle=True, icon='SEQ_CHROMA_SCOPE')
        if active.proc_voronoi_random_color:
            vr.prop(active, "proc_voronoi_random_seed", text="Seed")
    elif pt == 'WAVE':
        wr = col.row(align=True)
        wr.prop(active, "proc_wave_type",    text="")
        wr.prop(active, "proc_wave_profile", text="")
        col.prop(active, "proc_detail",            slider=True)
        col.prop(active, "proc_wave_detail_scale", slider=True, text="Detail Scale")
        col.prop(active, "proc_distortion",        slider=True)
    elif pt == 'GRADIENT':
        col.prop(active, "proc_gradient_type")
    elif pt == 'MUSGRAVE':
        col.prop(active, "proc_detail",         slider=True)
        col.prop(active, "proc_roughness_proc", slider=True, text="Roughness")
        col.prop(active, "proc_lacunarity",     slider=True)
    elif pt == 'MARBLE':
        col.prop(active, "proc_marble_wave_type", text="Pattern")
        col.prop(active, "proc_detail", slider=True)
        col.prop(active, "proc_roughness_proc", slider=True, text="Roughness")
        col.prop(active, "proc_distortion", slider=True, text="Wave Distortion")
        col.prop(active, "proc_marble_distortion", slider=True, text="Turbulence")


    col.separator(factor=0.5)
    off_row = col.row(align=True)
    off_row.label(text="Offset:", icon='OBJECT_ORIGIN')
    off_row.prop(active, "proc_offset_x", text="X")
    off_row.prop(active, "proc_offset_y", text="Y")
    off_row.prop(active, "proc_offset_z", text="Z")

    col.prop(active, "proc_coord_preset", text="Preset")
    col.prop(active, "proc_coord_type", text="Coords")

    # Show normalize toggle only for Object coordinates
    if active.proc_coord_type == 'OBJECT':
        col.prop(active, "proc_normalize_coords", text="Normalize Scale")

    # ── Coordinate transform (polar / spherical / swirl / cylindrical) ──
    col.prop(active, "proc_coord_transform", text="Transform")
    if active.proc_coord_transform == 'SWIRL':
        col.prop(active, "proc_swirl_amount", slider=True, text="Swirl")
    if active.proc_coord_transform in {'POLAR', 'CYLINDRICAL'}:
        col.label(text="Tip: use integer Scale for seamless wrap", icon='INFO')
    elif active.proc_coord_transform == 'SPHERICAL':
        col.label(text="Tip: pattern wraps X, poles compress", icon='INFO')

    # Smart warnings for Generated coordinates
    if active.proc_coord_type == 'GENERATED':
        import bpy as _bpy
        obj = _bpy.context.active_object
        if obj:
            s = obj.scale
            tol = 0.02
            if abs(s.x - 1.0) > tol or abs(s.y - 1.0) > tol or abs(s.z - 1.0) > tol:
                col.label(text="Scale not applied \u2014 pattern may stretch", icon='ERROR')
            dims = obj.dimensions
            if dims.x > 0 and dims.y > 0 and dims.z > 0:
                ratio = max(dims) / max(min(dims), 0.001)
                if ratio > 1.3:
                    col.label(text="Anisotropic shape \u2014 try Object coords", icon='INFO')

    # Contrast controls the ColorRamp stop positions. CHECKER outputs Color
    # directly (no ColorRamp) so the slider would be a no-op; GRADIENT already
    # gives a clean linear ramp where contrast adds little value and confuses
    # users. Hide for both.
    if active.proc_type not in ('CHECKER', 'GRADIENT'):
        col.prop(active, "proc_contrast", slider=True)
    col.prop(active, "proc_vector_distortion", slider=True, text="Vec Distort")

    col.separator(factor=0.6)
    fr = col.row(align=True)
    fr.prop(active, "use_fresnel_mask", text="Fresnel", icon='LIGHT_HEMI', toggle=True)
    if active.use_fresnel_mask:
        fr.prop(active, "fresnel_ior", text="IOR")
        fr.prop(active, "fresnel_strength", text="Str", slider=True)

    col.separator(factor=0.6)
    _draw_mask_block(col, active)

    _draw_clipping_mask(col, active, tlm)

    col.separator(factor=0.6)
    _draw_pbr_channels(col, active, tlm)

    col.separator(factor=0.5)
    _draw_group_assignment(col, active, tlm)


_SMART_GEN_SOURCES = {'EDGE_WEAR', 'DIRT', 'CURVATURE_SMART'}
# Sources that need an AO distance slider (raw AO + DIRT, which uses AO internally)
_AO_DISTANCE_SOURCES = {'AO', 'DIRT'}


def _draw_mask_slot(box, active, slot):
    """Draw a single mask slot (A or B). slot is 'A' or 'B'.

    Note: the source enum itself is drawn by the caller (next to the Mask toggle
    for A, or at the top of the Mask B sub-box for B). This helper only draws
    source-dependent parameters + the Invert toggle.
    """
    is_b = (slot == 'B')
    src_prop  = 'mask_source_b'        if is_b else 'mask_source'
    img_prop  = 'mask_image_name_b'    if is_b else 'mask_image_name'
    ao_prop   = 'mask_ao_distance_b'   if is_b else 'mask_ao_distance'
    inv_prop  = 'mask_invert_b'        if is_b else 'mask_invert'
    label     = "Mask B"               if is_b else "Mask A"
    icon      = 'SELECT_EXTEND'        if is_b else 'SELECT_SET'

    box.label(text=label, icon=icon)
    src = getattr(active, src_prop)
    if src == 'IMAGE':
        if getattr(active, img_prop):
            box.prop_search(active, img_prop, bpy.data, "images",
                            text="", icon='IMAGE_DATA')
        else:
            if not is_b:  # only A has the "New Image" operator
                box.operator("tlm.add_layer_mask", text="New Image", icon='ADD')
            else:
                box.label(text="No image selected", icon='INFO')
    # AO distance slider — raw AO uses it directly; DIRT smart generator
    # uses it internally as the inverted AO source.
    if src in _AO_DISTANCE_SOURCES:
        box.prop(active, ao_prop, slider=True, text="AO Distance")
    # POINTINESS, EDGE_WEAR, CURVATURE_SMART: no per-slot parameter.
    # Shared smart-generator tuning shown once below in its own sub-box.
    box.prop(active, inv_prop, text=f"Invert {slot}")


def _draw_mask_block(col, active):
    """Shared mask UI used by Procedural / Paint / Fill / Reference layers.

    Includes: Mask A + optional Mask B + combine mode + Contrast +
    Smart Generator controls (when source is EDGE_WEAR/DIRT/CURVATURE_SMART) +
    Mask Refinement section (Levels + Softness + Blur).

    UX:
    - When use_mask=False: compact row with only the two add-paths
      ("Add Mask" creates a paintable image; "Bake Smart" runs the smart-mask
      bake operator). The toggle itself is implicit — both ops set use_mask=True.
    - When use_mask=True: collapsible header (show_mask_section) + details box.
      The mask source dropdown lives at the top of the details box.
    """
    if not active.use_mask:
        # Mask is off — show only the two ways to enable it.
        mr = col.row(align=True)
        mr.label(text="", icon='MOD_MASK')
        mr.operator("tlm.add_layer_mask", text="Add Mask", icon='ADD')
        mr.operator("tlm.add_smart_mask", text="Bake Smart Mask", icon='SHADERFX')
        return

    # Mask is on — collapsible header with quick-disable toggle.
    header = col.row(align=True)
    header.prop(
        active, "show_mask_section",
        text=f"Mask  ·  {active.mask_source.replace('_', ' ').title()}",
        icon='TRIA_DOWN' if active.show_mask_section else 'TRIA_RIGHT',
        emboss=False,
    )
    header.prop(active, "use_mask", text="", icon='MOD_MASK', toggle=True)

    if not active.show_mask_section:
        return

    mbox = col.box().column(align=True)

    # Source dropdown lives at the top of the details box.
    mbox.prop(active, "mask_source", text="Source")

    # ── Mask A ──
    _draw_mask_slot(mbox, active, 'A')

    # ── Smart generator params (visible only when A or B uses a smart source) ──
    uses_smart_a = active.mask_source in _SMART_GEN_SOURCES
    uses_smart_b = active.use_mask_b and active.mask_source_b in _SMART_GEN_SOURCES
    if uses_smart_a or uses_smart_b:
        sgbox = mbox.box().column(align=True)
        sgbox.scale_y = 0.9
        sgbox.label(text="Smart Generator", icon='SHADERFX')
        sgbox.prop(active, "mask_gen_intensity", slider=True, text="Intensity")
        sgbox.prop(active, "mask_gen_sharpness", slider=True, text="Sharpness")
        br = sgbox.row(align=True)
        br.prop(active, "mask_gen_breakup",       slider=True, text="Breakup")
        br.prop(active, "mask_gen_breakup_scale", slider=True, text="Scale")

    # ── Mask B ──
    mbox.separator(factor=0.5)
    mbox.prop(active, "use_mask_b", text="Add Secondary Mask (B)",
              icon='SELECT_EXTEND', toggle=True)
    if active.use_mask_b:
        bbox = mbox.box().column(align=True)
        bbox.prop(active, "mask_source_b", text="Source")
        _draw_mask_slot(bbox, active, 'B')
        mbox.prop(active, "mask_combine", text="Combine")

    # ── Contrast ──
    mbox.prop(active, "mask_contrast", slider=True, text="Contrast")

    # ── Image-only: Blur ──
    # Blur taps the UV input, so it only makes sense when the primary source is IMAGE.
    if active.mask_source == 'IMAGE':
        mbox.prop(active, "mask_blur", slider=True, text="Blur")

    # ── Mask Refinement section (Levels + Softness) ──
    mbox.separator(factor=0.5)
    rrow = mbox.row(align=True)
    rrow.prop(active, "use_mask_levels", text="Levels",
              icon='IPO_LINEAR', toggle=True)
    rrow.prop(active, "mask_softness", slider=True, text="Softness")
    if active.use_mask_levels:
        lbox = mbox.box().column(align=True)
        lbox.scale_y = 0.85
        lbox.label(text="Input:", icon='ARROW_LEFTRIGHT')
        ir = lbox.row(align=True)
        ir.prop(active, "mask_levels_in_min", text="Black", slider=True)
        ir.prop(active, "mask_levels_in_max", text="White", slider=True)
        lbox.prop(active, "mask_levels_gamma", text="Gamma", slider=True)
        lbox.separator(factor=0.3)
        lbox.label(text="Output:", icon='ARROW_LEFTRIGHT')
        o_r = lbox.row(align=True)
        o_r.prop(active, "mask_levels_out_min", text="Black", slider=True)
        o_r.prop(active, "mask_levels_out_max", text="White", slider=True)


def _draw_reference(col, active, tlm):
    """Reference layer UI — reuses another layer's pattern with its own blend/mask/channels."""
    br = col.row(align=True)
    # Hide blend mode when the layer routes to Alpha — alpha is coverage,
    # not a colour; artistic blend modes (Overlay/Hard Light/etc.) make no
    # sense there. The compositor force-sets MIX for alpha regardless.
    if getattr(active, 'output_channel', 'BASE_COLOR') != 'ALPHA':
        br.prop(active, "blend_mode", text="")
    br.prop(active, "opacity",    text="Opacity", slider=True)
    br.operator("tlm.keyframe_opacity", text="", icon='KEYFRAME_HLT',
                emboss=False).action = 'INSERT'

    col.separator(factor=0.6)
    col.label(text="Reference:", icon='LINKED')
    col.prop_search(active, "reference_layer_name",
                    tlm, "layers", text="", icon='LAYER_ACTIVE')

    # Validation hints — user-facing feedback that matches the compositing
    # guard rails so they can't accidentally build an invalid graph.
    ref_name = active.reference_layer_name
    if not ref_name:
        col.label(text="Pick a source layer above", icon='INFO')
    elif ref_name == active.name:
        col.label(text="Cannot reference itself", icon='ERROR')
    else:
        ref = next((l for l in tlm.layers if l.name == ref_name), None)
        if ref is None:
            col.label(text="Referenced layer not found", icon='ERROR')
        elif ref.layer_type == "REFERENCE":
            col.label(text="Cannot reference another Reference", icon='ERROR')
        elif ref.layer_type in {"ADJUSTMENT", "GROUP"}:
            col.label(text="Source must be Paint, Fill or Procedural",
                      icon='ERROR')
        else:
            col.label(text=f"→ {ref.layer_type.title()} pattern reused",
                      icon='CHECKMARK')

    col.separator(factor=0.6)
    _draw_mask_block(col, active)

    _draw_clipping_mask(col, active, tlm)

    col.separator(factor=0.6)
    fr = col.row(align=True)
    fr.prop(active, "use_fresnel_mask", text="Fresnel", icon='LIGHT_HEMI', toggle=True)
    if active.use_fresnel_mask:
        fr.prop(active, "fresnel_ior", text="IOR")
        fr.prop(active, "fresnel_strength", text="Str", slider=True)

    col.separator(factor=0.6)
    # Note: Normal + Bump channels on a REFERENCE layer use this layer's own
    # image/strength — they do NOT pick up from the referenced pattern.
    # That's an intentional limitation of the Mix(VECTOR) normal pipeline.
    if getattr(active, 'use_normal', False) or getattr(active, 'use_bump', False):
        col.label(text="Normal/Bump use THIS layer's images, not the reference's",
                  icon='INFO')
    _draw_pbr_channels(col, active, tlm)

    col.separator(factor=0.5)
    _draw_group_assignment(col, active, tlm)


def _draw_adjustment(col, active, tlm):
    col.prop(active, "adj_type")
    col.separator(factor=0.5)
    if active.adj_type == 'HUE_SAT':
        col.prop(active, "adj_hue",        slider=True)
        col.prop(active, "adj_saturation", slider=True)
        col.prop(active, "adj_value",      slider=True)
    elif active.adj_type == 'BRIGHT_CONTRAST':
        col.prop(active, "adj_brightness", slider=True)
        col.prop(active, "adj_contrast",   slider=True)
    elif active.adj_type == 'LEVELS':
        col.label(text="Input:", icon='ARROW_LEFTRIGHT')
        ir = col.row(align=True)
        ir.prop(active, "adj_in_min", text="Black", slider=True)
        ir.prop(active, "adj_in_max", text="White", slider=True)
        col.prop(active, "adj_levels_gamma", text="Gamma", slider=True)
        col.separator(factor=0.4)
        col.label(text="Output:", icon='ARROW_LEFTRIGHT')
        or_ = col.row(align=True)
        or_.prop(active, "adj_out_min", text="Black", slider=True)
        or_.prop(active, "adj_out_max", text="White", slider=True)
    elif active.adj_type == 'COLOR_BALANCE':
        g = col.column(align=True)
        g.scale_y = 0.85
        g.label(text="Lift (Shadows):")
        g.prop(active, "adj_lift",  text="")
        g.label(text="Gamma (Midtones):")
        g.prop(active, "adj_gamma", text="")
        g.label(text="Gain (Highlights):")
        g.prop(active, "adj_gain",  text="")
    elif active.adj_type == 'CURVES':
        col.prop(active, "adj_curve_contrast",   slider=True)
        col.prop(active, "adj_curve_brightness", slider=True)
        col.separator(factor=0.3)
        col.label(text="Tone Clipping:", icon='IPO_LINEAR')
        cr = col.row(align=True)
        cr.prop(active, "adj_curve_black_point", text="Black", slider=True)
        cr.prop(active, "adj_curve_white_point", text="White", slider=True)
    col.separator(factor=0.5)
    _draw_group_assignment(col, active, tlm)


def _draw_paint_fill(col, active, tlm):
    br = col.row(align=True)
    if getattr(active, 'output_channel', 'BASE_COLOR') != 'ALPHA':
        br.prop(active, "blend_mode", text="")
    br.prop(active, "opacity",    text="", slider=True)
    br.operator("tlm.keyframe_opacity", text="", icon='KEYFRAME_HLT',
                emboss=False).action = 'INSERT'
    # Output channel routing — quick-select target BSDF input.
    # AUTO (default) preserves the legacy behavior driven by use_<channel>
    # toggles. Any other value bypasses them and sends this layer to a
    # single channel (Base Color / Roughness / Metallic / Alpha).
    oc = col.row(align=True)
    oc.prop(active, "output_channel", text="Output", icon='NODE_COMPOSITING')

    if active.layer_type == "FILL":
        col.separator(factor=0.5)
        col.prop(active, "fill_color", text="Color")

    if active.layer_type == "PAINT" and active.image_name:
        col.separator(factor=0.3)
        ir = col.row(align=True)
        ir.label(text=active.image_name, icon='IMAGE_RGB_ALPHA')
        if active.image:
            w, h = active.image.size
            ir.label(text=f"{w}×{h}")

    col.separator(factor=0.6)
    _draw_mask_block(col, active)

    _draw_clipping_mask(col, active, tlm)

    col.separator(factor=0.6)
    fr = col.row(align=True)
    fr.prop(active, "use_fresnel_mask", text="Fresnel", icon='LIGHT_HEMI', toggle=True)
    if active.use_fresnel_mask:
        fr.prop(active, "fresnel_ior", text="IOR")
        fr.prop(active, "fresnel_strength", text="Str", slider=True)

    col.separator(factor=0.6)
    tr = col.row(align=True)
    tr.prop(active, "use_triplanar", text="Triplanar", icon='ORIENTATION_GLOBAL', toggle=True)
    if active.use_triplanar:
        ts = col.column(align=True)
        ts.scale_y = 0.9
        tsr = ts.row(align=True)
        tsr.prop(active, "triplanar_scale",     text="Scale")
        tsr.prop(active, "triplanar_sharpness", text="Sharp", slider=True)

    # ── Image Mapping (paint + PBR image layers) ────────────────────────────
    # Collapsible: Extension + Location/Rotation/Scale (per-axis), only
    # relevant for layers that produce image textures (PAINT) or use PBR
    # channel images (FILL / Procedural with channel images).
    col.separator(factor=0.6)
    mr = col.row(align=True)
    mr.prop(active, "show_paint_mapping",
            text="Image Mapping",
            icon='TRIA_DOWN' if active.show_paint_mapping else 'TRIA_RIGHT',
            emboss=False)
    if active.show_paint_mapping:
        mbox = col.box().column(align=True)
        mbox.scale_y = 0.9
        mbox.prop(active, "paint_extension", text="Extension")
        mbox.label(text="Location:")
        lr = mbox.row(align=True)
        lr.prop(active, "paint_location_x", text="X")
        lr.prop(active, "paint_location_y", text="Y")
        lr.prop(active, "paint_location_z", text="Z")
        mbox.label(text="Rotation:")
        rr = mbox.row(align=True)
        rr.prop(active, "paint_rotation_x", text="X")
        rr.prop(active, "paint_rotation_y", text="Y")
        rr.prop(active, "paint_rotation_z", text="Z")
        mbox.label(text="Scale:")
        sr = mbox.row(align=True)
        sr.prop(active, "paint_scale_x", text="X")
        sr.prop(active, "paint_scale_y", text="Y")
        sr.prop(active, "paint_scale_z", text="Z")

    col.separator(factor=0.6)
    _draw_pbr_channels(col, active, tlm)

    col.separator(factor=0.5)
    _draw_group_assignment(col, active, tlm)


def _draw_pbr_channels(col, layer, tlm):
    # Collapsible header with active channel count badge.
    # Counts every additional channel toggled on (these stack ON TOP of
    # the main output_channel target).
    active_count = sum(1 for f in ('use_roughness', 'use_metallic',
                                    'use_normal', 'use_emission',
                                    'use_transmission', 'use_alpha',
                                    'use_bump')
                       if getattr(layer, f))
    badge = f" ({active_count})" if active_count else ""
    row = col.row(align=True)
    row.prop(layer, "show_pbr_channels",
             text=f"PBR Channels{badge}",
             icon='TRIA_DOWN' if layer.show_pbr_channels else 'TRIA_RIGHT',
             emboss=False)
    if not layer.show_pbr_channels:
        return

    pbox = col.box()
    pc = pbox.column(align=True)
    pc.scale_y = 0.9

    bumpr = pc.row(align=True)
    bumpr.prop(layer, "use_bump", text="Bump", icon='MOD_DISPLACE', toggle=True)
    if layer.use_bump:
        bumpr.prop(layer, "bump_strength", text="Str", slider=True)
        bumpr.prop(layer, "bump_distance", text="Dist", slider=True)

    # PBR Channels list — toggles here are ADDITIONAL channels beyond the
    # main "Output" target chosen at the top of the panel. Cumulative
    # semantics: a layer routed to ROUGHNESS with use_metallic=True drives
    # both. base_color is omitted because it has no toggle (only reachable
    # as a routing target).
    channels = [
        ('roughness',    'use_roughness',    'roughness_image_name',    'roughness_fill',
         None,             "Roughness",    'RNDCURVE'),
        ('metallic',     'use_metallic',     'metallic_image_name',     'metallic_fill',
         None,             "Metallic",     'MATFLUID'),
        ('normal',       'use_normal',       'normal_image_name',       None,
         None,             "Normal",       'NORMALS_FACE'),
        ('emission',     'use_emission',     'emission_image_name',     None,
         'emission_color', "Emission",     'LIGHT'),
        ('transmission', 'use_transmission', 'transmission_image_name', 'transmission_fill',
         None,             "Transmission", 'MATSPHERE'),
        ('alpha',        'use_alpha',        'alpha_image_name',        'alpha_fill',
         None,             "Alpha",        'IMAGE_ALPHA'),
    ]

    for ch_id, flag, img_attr, fill_attr, color_attr, label, icon in channels:
        pc.separator(factor=0.3)
        ch_row = pc.row(align=True)
        enabled = getattr(layer, flag)
        ch_row.prop(layer, flag, text=label, icon=icon, toggle=True)
        if enabled:
            img_name = getattr(layer, img_attr)
            has_img  = bool(img_name and bpy.data.images.get(img_name))
            if has_img:
                # prop_search lets the user swap the image without remove+re-add
                ch_row.prop_search(layer, img_attr, bpy.data, "images", text="")
                op = ch_row.operator("tlm.remove_channel_image", text="", icon='X', emboss=False)
                op.channel = ch_id
            else:
                if fill_attr:
                    ch_row.prop(layer, fill_attr, text="", slider=True)
                elif color_attr:
                    ch_row.prop(layer, color_attr, text="")
                op = ch_row.operator("tlm.add_channel_image", text="New", icon='ADD')
                op.channel = ch_id
            imp = ch_row.operator("tlm.import_texture_as_layer", text="", icon='FILEBROWSER', emboss=False)
            imp.channel   = ch_id
            imp.add_to_active = True
            if ch_id == 'normal' and enabled:
                pc.prop(layer, "normal_strength", slider=True)
                nr = pc.row(align=True)
                nr.prop(layer, "normal_tile_scale", text="Tile")
                nr.prop(layer, "normal_rotation", text="Rot")
            if ch_id == 'emission' and enabled:
                pc.prop(layer, "emission_strength", slider=True)
                if layer.layer_type == "PROCEDURAL":
                    pc.prop(layer, "proc_emission_threshold", slider=True, text="Threshold")
                    pc.prop(layer, "proc_emission_falloff", slider=True, text="Falloff")
                if layer.blend_mode == "ADD":
                    pc.label(text="ADD + Emission: use only one", icon='ERROR')
        else:
            op = ch_row.operator("tlm.add_channel_image", text="", icon='ADD', emboss=False)
            op.channel = ch_id

    # ── Branching: per-channel blend mode overrides ─────────────────────────
    # Shows a collapsible section with 5 dropdowns (one per overridable channel).
    # INHERIT = use the main blend_mode. Any other value = branching override.
    # Only meaningful when at least one channel is enabled.
    override_channels = [
        ('base_color',  "Base Color"),
        ('roughness',   "Roughness"),
        ('metallic',    "Metallic"),
        ('emission',    "Emission"),
        ('transmission',"Transmission"),
    ]
    # Count active overrides to show a badge
    active_overrides = sum(
        1 for ch_id, _ in override_channels
        if getattr(layer, f"blend_mode_{ch_id}", "INHERIT") != "INHERIT"
    )
    badge_ov = f" ({active_overrides})" if active_overrides else ""
    pc.separator(factor=0.6)
    orow = pc.row(align=True)
    orow.prop(layer, "show_blend_overrides",
              text=f"Branching{badge_ov}",
              icon='TRIA_DOWN' if layer.show_blend_overrides else 'TRIA_RIGHT',
              emboss=False)
    if layer.show_blend_overrides:
        obox = pc.box()
        oc = obox.column(align=True)
        oc.scale_y = 0.85
        oc.label(text="Per-channel blend override:", icon='NODE_COMPOSITING')
        for ch_id, ch_label in override_channels:
            # Only show override row when the channel is enabled (or always for base_color)
            if ch_id == 'base_color' or getattr(layer, f"use_{ch_id}", False):
                oc.prop(layer, f"blend_mode_{ch_id}", text=ch_label)


def _draw_group_assignment(col, active, tlm):
    groups = [l for l in tlm.layers if l.layer_type == "GROUP"]
    if not groups:
        return
    col.separator(factor=0.3)
    col.label(text="Group:", icon='FILE_FOLDER')
    if active.group_name:
        gr = col.row(align=True)
        gr.label(text=active.group_name, icon='LAYER_ACTIVE')
        gr.operator("tlm.remove_from_group", text="", icon='X', emboss=False)
    else:
        gc = col.column(align=True)
        gc.scale_y = 0.85
        for g in groups:
            op = gc.operator("tlm.move_to_group", text=f"→ {g.name}", icon='FILE_FOLDER')
            op.group_name = g.name


# Each section is a small standalone draw function — used by both the
# Properties-tab and Viewport-sidebar variants of the per-section panels.
# No wrapper "TLM Settings" panel anymore: each section is a direct
# sibling sub-panel of the Texture Layers main panel, collapsed by
# default. The user opens the one they need without opening a wrapper
# first.

def _draw_canvas_section(layout, tlm):
    canvas = layout.column(align=True)
    canvas.prop(tlm, "resolution")
    canvas.prop(tlm, "uv_map")


def _draw_composite_section(layout, tlm):
    comp = layout.column(align=True)
    ac_icon = 'LINKED' if tlm.auto_composite else 'UNLINKED'
    comp.prop(tlm, "auto_composite", text="Auto Composite",
              icon=ac_icon, toggle=True)
    comp.prop(tlm, "use_base_color_alpha",
              text="Use Paint Alpha", icon='IMAGE_ALPHA', toggle=True)
    ops_row = comp.row(align=True)
    ops_row.operator("tlm.rebuild_composite", text="Rebuild", icon='FILE_REFRESH')
    ops_row.operator("tlm.flatten_layers",    text="Flatten", icon='IMAGE_ZDEPTH')
    comp.operator("tlm.refresh_thumbnails",
                  text="Refresh Thumbnails", icon='FILE_REFRESH')


def _draw_bake_section(layout, tlm):
    bake = layout.column(align=True)
    bake.operator("tlm.bake_pbr", text="Bake PBR Maps…", icon='EXPORT')
    op = bake.operator("tlm.bake_pbr", text="PBR Channels…",
                       icon='NODE_COMPOSITING')
    op.preset = 'CUSTOM'


def _draw_presets_section(layout, tlm):
    from .operators import BUILTIN_PRESETS
    grid = layout.column(align=True)
    grid.scale_y = 0.95
    prow = None
    for i, pname in enumerate(BUILTIN_PRESETS):
        if i % 2 == 0:
            prow = grid.row(align=True)
        op = prow.operator("tlm.apply_preset", text=pname, icon='MATERIAL')
        op.preset_name = pname

    # User-saved presets (cached to avoid os.listdir every draw)
    global _preset_cache, _preset_cache_time
    preset_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "presets")
    now = _time.monotonic()
    if now - _preset_cache_time > _PRESET_CACHE_TTL:
        _preset_cache_time = now
        if _os.path.isdir(preset_dir):
            _preset_cache = sorted(
                f[:-4] for f in _os.listdir(preset_dir) if f.endswith(".tlm")
            )
        else:
            _preset_cache = []
    user_presets = _preset_cache
    layout.separator(factor=0.5)
    layout.label(text="Saved Presets:", icon='FILE_FOLDER')
    if user_presets:
        ugrid = layout.column(align=True)
        ugrid.scale_y = 0.95
        for pname in user_presets:
            urow = ugrid.row(align=True)
            op = urow.operator("tlm.apply_preset", text=pname, icon='PRESET')
            op.preset_name = pname
            dop = urow.operator("tlm.delete_preset", text="", icon='TRASH')
            dop.preset_name = pname
    layout.operator("tlm.save_preset",
                    text="Save Current as Preset…", icon='FILE_TICK')


def _draw_io_section(layout, tlm):
    io_row = layout.row(align=True)
    io_row.operator("tlm.export_json", text="Export .tlm", icon='EXPORT')
    io_row.operator("tlm.import_json", text="Import .tlm", icon='IMPORT')
    layout.operator("tlm.import_pbr_set", text="Import PBR Set...", icon='TEXTURE')


class TLM_PT_MainPanel(Panel):
    bl_label       = "Texture Layers"
    bl_idname      = "TLM_PT_main_panel"
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = "material"

    @classmethod
    def poll(cls, context):
        return (context.active_object is not None
                and context.active_object.active_material is not None)

    def draw(self, context):
        draw_tlm_main(self.layout, context)


# Each macro-section gets its own collapsible Panel attached as a child
# of the Texture Layers main panel. Five Panels for the Properties tab,
# five mirror Panels for the Viewport sidebar — they share the same
# _draw_*_section helper so behavior stays identical between contexts.
# All default to CLOSED so the panel doesn't grow visually before the
# user clicks into a section.

def _make_section_panel(idname, label, icon, parent_id, draw_fn,
                        space, region, category=None):
    """Factory: build a Panel subclass for a single TLM section."""

    class _Section(Panel):
        bl_label       = label
        bl_idname      = idname
        bl_space_type  = space
        bl_region_type = region
        bl_parent_id   = parent_id
        bl_options     = {'DEFAULT_CLOSED'}
        if category is not None:
            bl_category = category

        @classmethod
        def poll(cls, context):
            return (context.active_object is not None
                    and context.active_object.active_material is not None)

        def draw_header(self, context):
            self.layout.label(text="", icon=icon)

        def draw(self, context):
            mat = context.active_object.active_material
            tlm = mat.tlm
            draw_fn(self.layout, tlm)

    _Section.__name__ = idname.replace('TLM_PT_', 'TLM_PT_')
    return _Section


# ── Properties → Material → child sections ──────────────────────────────
TLM_PT_PropsCanvas    = _make_section_panel(
    "TLM_PT_props_canvas",    "Canvas",          'IMAGE_DATA',
    "TLM_PT_main_panel", _draw_canvas_section,
    space='PROPERTIES', region='WINDOW',
)
TLM_PT_PropsComposite = _make_section_panel(
    "TLM_PT_props_composite", "Composite",       'NODE_MATERIAL',
    "TLM_PT_main_panel", _draw_composite_section,
    space='PROPERTIES', region='WINDOW',
)
TLM_PT_PropsBake      = _make_section_panel(
    "TLM_PT_props_bake",      "Bake & Export",   'RENDER_STILL',
    "TLM_PT_main_panel", _draw_bake_section,
    space='PROPERTIES', region='WINDOW',
)
TLM_PT_PropsPresets   = _make_section_panel(
    "TLM_PT_props_presets",   "Presets",         'PRESET_NEW',
    "TLM_PT_main_panel", _draw_presets_section,
    space='PROPERTIES', region='WINDOW',
)
TLM_PT_PropsIO        = _make_section_panel(
    "TLM_PT_props_io",        "Layer Stack I/O", 'FILE_FOLDER',
    "TLM_PT_main_panel", _draw_io_section,
    space='PROPERTIES', region='WINDOW',
)


class TLM_PT_ViewportPanel(Panel):
    bl_label       = "Texture Layers"
    bl_idname      = "TLM_PT_viewport_panel"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'TLM'

    @classmethod
    def poll(cls, context):
        return (context.active_object is not None
                and context.active_object.active_material is not None)

    def draw(self, context):
        draw_tlm_main(self.layout, context)


# ── Viewport sidebar → TLM tab → child sections ─────────────────────────
TLM_PT_ViewCanvas    = _make_section_panel(
    "TLM_PT_view_canvas",    "Canvas",          'IMAGE_DATA',
    "TLM_PT_viewport_panel", _draw_canvas_section,
    space='VIEW_3D', region='UI', category='TLM',
)
TLM_PT_ViewComposite = _make_section_panel(
    "TLM_PT_view_composite", "Composite",       'NODE_MATERIAL',
    "TLM_PT_viewport_panel", _draw_composite_section,
    space='VIEW_3D', region='UI', category='TLM',
)
TLM_PT_ViewBake      = _make_section_panel(
    "TLM_PT_view_bake",      "Bake & Export",   'RENDER_STILL',
    "TLM_PT_viewport_panel", _draw_bake_section,
    space='VIEW_3D', region='UI', category='TLM',
)
TLM_PT_ViewPresets   = _make_section_panel(
    "TLM_PT_view_presets",   "Presets",         'PRESET_NEW',
    "TLM_PT_viewport_panel", _draw_presets_section,
    space='VIEW_3D', region='UI', category='TLM',
)
TLM_PT_ViewIO        = _make_section_panel(
    "TLM_PT_view_io",        "Layer Stack I/O", 'FILE_FOLDER',
    "TLM_PT_viewport_panel", _draw_io_section,
    space='VIEW_3D', region='UI', category='TLM',
)


classes = [
    TLM_UL_LayerList,
    TLM_PT_MainPanel,
    TLM_PT_PropsCanvas,
    TLM_PT_PropsComposite,
    TLM_PT_PropsBake,
    TLM_PT_PropsPresets,
    TLM_PT_PropsIO,
    TLM_PT_ViewportPanel,
    TLM_PT_ViewCanvas,
    TLM_PT_ViewComposite,
    TLM_PT_ViewBake,
    TLM_PT_ViewPresets,
    TLM_PT_ViewIO,
]


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
