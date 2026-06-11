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

# Cache preset lists (legacy, user) to avoid os.listdir on every draw call
_preset_cache = ([], [])
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
    the compositor bottom (where the clip silently has no effect).

    Also draws the Height Blend controls — both are blend-boundary
    modifiers, so they live together below the PBR channels."""
    col.prop(active, "use_clipping_mask",
             text="Clipping Mask", icon='CLIPUV_DEHLT', toggle=True)
    if active.use_clipping_mask and _is_at_compositor_bottom(active, tlm):
        col.label(text="No effect — nothing to clip against below",
                  icon='ERROR')

    # ── Height Blend ──────────────────────────────────────────────────
    col.prop(active, "use_height_blend",
             text="Height Blend", icon='MOD_DISPLACE', toggle=True)
    if active.use_height_blend:
        hb = col.box().column(align=True)
        hb.prop(active, "height_blend_source", text="Height")
        if active.height_blend_source == 'IMAGE':
            hb.prop_search(active, "height_blend_image_name",
                           bpy.data, "images", text="Map")
        elif active.layer_type != 'PROCEDURAL':
            hb.label(text="Auto height needs a Procedural layer",
                     icon='INFO')
        hb.prop(active, "height_blend_contrast", text="Contrast", slider=True)
        hb.label(text="Opacity = height bias (0.5 = neutral)", icon='INFO')


class TLM_UL_LayerList(UIList):
    bl_idname = "TLM_UL_layer_list"

    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        layer = item
        if self.layout_type not in {'DEFAULT', 'COMPACT'}:
            layout.label(text="", icon='IMAGE_DATA')
            return

        row = layout.row(align=True)
        if getattr(data, "shader_editable", False):
            row.enabled = False

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
            # "Paint on this layer" only makes sense for PAINT layers — Fill,
            # Procedural and Reference don't have a paintable image canvas.
            # The operator itself would just report a warning, so hide the
            # icon entirely on those types to keep the row honest.
            if layer.layer_type == "PAINT":
                op = row.operator("tlm.set_active_paint_layer", text="",
                                  icon='BRUSH_DATA', emboss=False)
                op.layer_index = index
            clip_icon = 'CLIPUV_HLT' if layer.use_clipping_mask else 'CLIPUV_DEHLT'
            row.prop(layer, "use_clipping_mask", text="", icon=clip_icon, emboss=False)
            # Alpha uses Shader Math operations instead of artistic colour
            # blend modes.
            if getattr(layer, 'output_channel', 'BASE_COLOR') == 'ALPHA':
                row.prop(layer, "alpha_math_operation", text="")
            else:
                row.prop(layer, "blend_mode", text="")
            row.prop(layer, "opacity", text="", slider=True)
        elif layer.layer_type == "GROUP":
            row.prop(layer, "opacity", text="", slider=True)
        elif layer.layer_type == "ADJUSTMENT":
            # No blend mode (adjustment is destructive on the channel),
            # but opacity acts as the strength dial — surface it inline
            # so the user can dial without opening the layer settings.
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
    if tlm.shader_editable:
        badge.label(text="Editable", icon='NODE_MATERIAL')
    else:
        ac_icon = 'LINKED' if tlm.auto_composite else 'UNLINKED'
        badge.prop(tlm, "auto_composite", text="", icon=ac_icon, toggle=True, emboss=True)

    layout.separator(factor=0.5)
    if tlm.shader_editable:
        note = layout.box()
        note.label(text="Editable Shader mode", icon='NODE_MATERIAL')
        note.label(text="TLM rebuilds are disabled for this material.")
        row = note.row(align=True)
        row.operator("tlm.return_to_managed_shader", text="Return to TLM", icon='FILE_REFRESH')

    add_row = layout.row(align=True)
    add_row.scale_y = 1.1
    add_row.enabled = not tlm.shader_editable
    # Layer-type buttons in alphabetical order (Adj / Fill / Paint /
    # Proc / Ref) so they're predictable to find regardless of which
    # type the user reaches for first.
    add_row.operator("tlm.add_adjustment_layer",   text="Adj",   icon='MODIFIER')
    add_row.operator("tlm.add_fill_layer",         text="Fill",  icon='COLOR')
    add_row.operator("tlm.add_paint_layer",        text="Paint", icon='IMAGE_RGB_ALPHA')
    add_row.operator("tlm.add_procedural_layer",   text="Proc",  icon='TEXTURE')
    add_row.operator("tlm.add_reference_layer",    text="Ref",   icon='LINKED')
    add_row.operator("tlm.add_group",              text="",      icon='FILE_FOLDER')
    add_row.separator()
    add_row.operator("tlm.import_texture_as_layer", text="", icon='IMPORT')
    add_row.operator("tlm.layer_from_clipboard",    text="", icon='COPYDOWN')

    ops_row = layout.row(align=True)
    ops_row.enabled = not tlm.shader_editable
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
    layer_details = layout.column()
    layer_details.enabled = not tlm.shader_editable
    _draw_active_layer(layer_details, active, tlm, mat)


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
        if getattr(active, 'output_channel', 'BASE_COLOR') == 'ALPHA':
            br.prop(active, "alpha_math_operation", text="")
        else:
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
    # ── CORE ROW: blend + opacity + output channel + proc_type ────────
    # These four are the "what kind of layer is this" essentials — never
    # collapsible because hiding them would orphan the user.
    br = col.row(align=True)
    if getattr(active, 'output_channel', 'BASE_COLOR') == 'ALPHA':
        br.prop(active, "alpha_math_operation", text="")
    else:
        br.prop(active, "blend_mode", text="")
    br.prop(active, "opacity",    text="Opacity", slider=True)
    br.operator("tlm.keyframe_opacity", text="", icon='KEYFRAME_HLT',
                emboss=False).action = 'INSERT'
    oc = col.row(align=True)
    oc.prop(active, "output_channel", text="Output", icon='NODE_COMPOSITING')
    if active.output_channel == 'EMISSION':
        col.prop(active, "emission_strength", text="Emission Strength", slider=True)

    col.separator(factor=0.5)
    col.prop(active, "proc_type")

    # Read shared state once
    use_manual = getattr(active, 'proc_use_manual_stops', True)
    proc_t = active.proc_type
    pt = proc_t
    # GRADIENT/FRESNEL force a 0..1 ramp internally — manual stops + contrast
    # are hidden for these.
    manual_supported = proc_t not in ('GRADIENT', 'FRESNEL')

    # ── SECTION 1/4: PBR Channels (collapsible) ─────────────────────────
    # First section so the user sees routing options up top — most layers
    # need at least one channel toggle (use_bump / use_roughness / etc.)
    # and burying them at the bottom is what made the panel feel buried.
    col.separator(factor=0.4)
    _draw_pbr_channels(col, active, tlm)

    # ── SECTION 2/4: Pattern Params (per-proc-type knobs + Scale) ───────
    col.separator(factor=0.4)
    hdr_pat = col.row(align=True)
    hdr_pat.prop(active, "show_proc_pattern_section",
                 text=f"Pattern Params · {pt.replace('_', ' ').title()}",
                 icon='TRIA_DOWN' if active.show_proc_pattern_section else 'TRIA_RIGHT',
                 emboss=False)
    if not active.show_proc_pattern_section:
        # Pattern section folded — skip the per-type knob block entirely
        # and jump down to mapping / Color Ramp / Fresnel / mask sections.
        _draw_procedural_advanced_tail(col, active, tlm)
        return
    pbox = col.box().column(align=True)
    pbox.scale_y = 0.95
    # All per-proc-type knobs go inside `pbox` instead of `col` so they
    # live inside the collapsible box. The pt branches below mirror the
    # exact controls they showed pre-refactor.
    col_pat = pbox
    # Scale lives INSIDE Pattern Params — it's the most fundamental knob
    # of every procedural (other than GRADIENT which has no scale).
    if pt != 'GRADIENT':
        col_pat.prop(active, "proc_scale", slider=False)
        col_pat.separator(factor=0.3)
    if pt == 'NOISE':
        col_pat.prop(active, "proc_detail",         slider=True)
        col_pat.prop(active, "proc_roughness_proc", slider=True, text="Roughness")
        col_pat.prop(active, "proc_lacunarity",     slider=True)
        col_pat.prop(active, "proc_distortion",     slider=True)
    elif pt == 'VORONOI':
        col_pat.prop(active, "proc_voronoi_feature")
        col_pat.prop(active, "proc_voronoi_distance")
        col_pat.prop(active, "proc_randomness", slider=True)
        col_pat.prop(active, "proc_detail", slider=True)
        col_pat.prop(active, "proc_roughness_proc", slider=True, text="Roughness")
        col_pat.prop(active, "proc_lacunarity", slider=True)
        vr = col_pat.row(align=True)
        vr.prop(active, "proc_voronoi_random_color", text="Random Per Cell", toggle=True, icon='SEQ_CHROMA_SCOPE')
        if active.proc_voronoi_random_color:
            vr.prop(active, "proc_voronoi_random_seed", text="Seed")
    elif pt == 'WAVE':
        wr = col_pat.row(align=True)
        wr.prop(active, "proc_wave_type",    text="")
        wr.prop(active, "proc_wave_profile", text="")
        if active.proc_wave_type == 'RINGS':
            col_pat.prop(active, "proc_wave_rings_direction", text="Rings Direction")
        else:
            col_pat.prop(active, "proc_wave_bands_direction", text="Bands Direction")
        col_pat.prop(active, "proc_detail",            slider=True)
        col_pat.prop(active, "proc_wave_detail_scale", slider=True, text="Detail Scale")
        col_pat.prop(active, "proc_wave_detail_roughness", slider=True, text="Detail Roughness")
        col_pat.prop(active, "proc_distortion",        slider=True)
        col_pat.prop(active, "proc_wave_phase_offset", slider=True, text="Phase Offset")
    elif pt == 'GRADIENT':
        col_pat.prop(active, "proc_gradient_type")
    elif pt == 'MUSGRAVE':
        col_pat.prop(active, "proc_detail",         slider=True)
        col_pat.prop(active, "proc_roughness_proc", slider=True, text="Roughness")
        col_pat.prop(active, "proc_lacunarity",     slider=True)
    elif pt == 'MARBLE':
        col_pat.prop(active, "proc_marble_wave_type", text="Pattern")
        if active.proc_marble_wave_type == 'RINGS':
            col_pat.prop(active, "proc_marble_rings_direction", text="Rings Direction")
        else:
            col_pat.prop(active, "proc_marble_bands_direction", text="Bands Direction")
        col_pat.prop(active, "proc_marble_wave_profile", text="Profile")
        col_pat.prop(active, "proc_detail", slider=True)
        col_pat.prop(active, "proc_roughness_proc", slider=True, text="Roughness")
        col_pat.prop(active, "proc_distortion", slider=True, text="Wave Distortion")
        col_pat.prop(active, "proc_marble_distortion", slider=True, text="Turbulence")
    elif pt == 'BRICK':
        # Brick uses Color1/Color2 as the two brick variants and Color3
        # (when use_proc_color3 is on) as the mortar. The other Color UI
        # is shared above via the colour-pickers row, so here we only
        # surface the brick-specific layout knobs.
        offr = col_pat.row(align=True)
        offr.prop(active, "proc_brick_offset",      text="Offset",  slider=True)
        offr.prop(active, "proc_brick_offset_freq", text="Frequency")
        sqr = col_pat.row(align=True)
        sqr.prop(active, "proc_brick_squash",      text="Squash",  slider=True)
        sqr.prop(active, "proc_brick_squash_freq", text="Frequency")
        col_pat.separator(factor=0.3)
        mr = col_pat.row(align=True)
        mr.prop(active, "proc_brick_mortar_size",   text="Mortar Size",   slider=True)
        mr.prop(active, "proc_brick_mortar_smooth", text="Mortar Smooth", slider=True)
        bw = col_pat.row(align=True)
        bw.prop(active, "proc_brick_width", text="Brick Width")
        bw.prop(active, "proc_brick_row_height", text="Row Height")
        col_pat.prop(active, "proc_brick_bias", text="Bias", slider=True)
        if not active.use_proc_color3:
            col_pat.label(text="Tip: enable Color 3 above to set mortar colour",
                          icon='INFO')
    elif pt == 'MAGIC':
        col_pat.prop(active, "proc_magic_depth",      text="Depth", slider=True)
        col_pat.prop(active, "proc_magic_distortion", text="Distortion", slider=True)
    elif pt == 'WHITE_NOISE':
        col_pat.label(text="Pure per-pixel random — no extra params",
                      icon='INFO')
    elif pt == 'STRIPES':
        col_pat.prop(active, "proc_stripe_direction", text="Direction")
        col_pat.prop(active, "proc_stripe_width",     slider=True)
        col_pat.prop(active, "proc_stripe_sharpness", slider=True)
        col_pat.prop(active, "proc_distortion", text="Distortion", slider=True)
        col_pat.prop(active, "proc_detail", text="Detail", slider=True)
        col_pat.prop(active, "proc_wave_detail_scale", text="Detail Scale", slider=True)
        col_pat.prop(active, "proc_wave_detail_roughness", text="Detail Roughness", slider=True)
        col_pat.prop(active, "proc_wave_phase_offset", text="Phase Offset", slider=True)
    elif pt == 'HEX_GRID':
        col_pat.prop(active, "proc_hex_edge_width", text="Edge Width", slider=True)
        col_pat.prop(active, "proc_randomness",     text="Randomness", slider=True)
        col_pat.prop(active, "proc_detail",         text="Detail", slider=True)
        col_pat.label(text="Tip: Randomness 0 = perfect hexagons",
                      icon='INFO')
    elif pt == 'GABOR':
        # Anisotropic Gabor noise — directional streak generator.
        # Brushed metal: high Anisotropy (0.9-1.0), Frequency 3-5.
        col_pat.prop(active, "proc_gabor_anisotropy", text="Anisotropy", slider=True)
        col_pat.prop(active, "proc_gabor_orientation", text="Orientation")
        col_pat.prop(active, "proc_gabor_frequency", text="Frequency", slider=True)
        col_pat.label(text="Tip: Anisotropy 1.0 = parallel streaks (brushed metal)",
                      icon='INFO')
    elif pt == 'DOTS':
        # Packed circular dots in a jittered grid. proc_scale sets
        # density (higher = more dots), proc_randomness jitters cell
        # positions (1.0 = full natural look, 0.0 = perfect lattice).
        col_pat.prop(active, "proc_dots_radius",   text="Radius",   slider=True)
        col_pat.prop(active, "proc_dots_softness", text="Softness", slider=True)
        col_pat.prop(active, "proc_randomness",    text="Randomness", slider=True)
        col_pat.prop(active, "proc_detail",        text="Detail", slider=True)
        col_pat.label(text="Tip: Radius 0.30, Softness 0.05 = clean polkadots",
                      icon='INFO')
    elif pt == 'RIDGED':
        # Ridged fractal noise — razor-like crests, ideal for mountains,
        # rock veins, lightning, crackle. Detail / Lacunarity reuse the
        # shared noise sliders shown above.
        col_pat.prop(active, "proc_detail", text="Detail", slider=True)
        col_pat.prop(active, "proc_roughness_proc", text="Roughness", slider=True)
        col_pat.prop(active, "proc_lacunarity", text="Lacunarity", slider=True)
        col_pat.prop(active, "proc_distortion", text="Distortion", slider=True)
        col_pat.prop(active, "proc_ridged_offset", text="Offset", slider=True)
        col_pat.prop(active, "proc_ridged_gain",   text="Gain",   slider=True)
        col_pat.label(text="Tip: Gain 3-4 + Detail 8 = razor-sharp ridges",
                      icon='INFO')
    elif pt == 'CRACKS':
        # Voronoi distance-to-edge tuned for narrow organic veins.
        # Combine with proc_distortion (vector distortion above) for
        # the most natural-looking crack networks.
        col_pat.prop(active, "proc_cracks_width",     text="Width",     slider=True)
        col_pat.prop(active, "proc_cracks_sharpness", text="Sharpness", slider=True)
        col_pat.prop(active, "proc_randomness",       text="Randomness", slider=True)
        col_pat.prop(active, "proc_roughness_proc",   text="Roughness", slider=True)
        col_pat.label(text="Tip: add Vector Distortion above for organic cracks",
                      icon='INFO')
    elif pt == 'WOOD':
        # Concentric growth rings + organic distortion + fine grain.
        col_pat.prop(active, "proc_detail",          text="Ring Detail",      slider=True)
        col_pat.prop(active, "proc_wood_distortion", text="Ring Distortion",  slider=True)
        col_pat.prop(active, "proc_wood_grain",      text="Grain",            slider=True)
        col_pat.label(text="Tip: Color1 = early wood, Color2 = dark ring line",
                      icon='INFO')

    elif pt == 'SCRATCHES':
        col_pat.prop(active, "proc_scratches_angle", text="Angle")
        col_pat.prop(active, "proc_scratches_aniso", text="Anisotropy", slider=True)
        col_pat.prop(active, "proc_scratches_width", text="Width", slider=True)
        col_pat.prop(active, "proc_detail", text="Detail", slider=True)
        col_pat.label(text="Tip: Color1 = surface, Color2 = scratch streaks",
                      icon='INFO')

    elif pt == 'CAUSTICS':
        col_pat.prop(active, "proc_caustics_freq", text="Frequency", slider=True)
        col_pat.prop(active, "proc_randomness",    text="Randomness", slider=True)
        col_pat.label(text="Tip: add Vector Distortion above for liquid shimmer",
                      icon='INFO')
    elif pt == 'WEAVE':
        col_pat.prop(active, "proc_weave_width", text="Thread Width", slider=True)
        col_pat.label(text="Tip: Scale = thread count · Color1 = gap, Color2 = thread",
                      icon='INFO')

    elif pt == 'TILES':
        col_pat.prop(active, "proc_tiles_layout", text="Layout")
        col_pat.prop(active, "proc_tiles_aspect", text="Aspect", slider=True)
        col_pat.prop(active, "proc_tiles_mortar", text="Mortar", slider=True)
        col_pat.prop(active, "proc_tiles_random", text="Random Shade", slider=True)
        col_pat.label(text="Tip: use UV coords · Color1 = mortar, Color2 = tile",
                      icon='INFO')

    elif pt == 'SCATTER':
        col_pat.prop(active, "proc_scatter_density", text="Density", slider=True)
        col_pat.prop(active, "proc_scatter_size", text="Dot Size", slider=True)
        col_pat.prop(active, "proc_randomness", text="Randomness", slider=True)
        col_pat.label(text="Tip: Color1 = background, Color2 = scattered dots",
                      icon='INFO')

    elif pt == 'TRUCHET':
        col_pat.prop(active, "proc_truchet_width", text="Line Width", slider=True)
        col_pat.label(text="Tip: use UV coords · Color1 = background, Color2 = path",
                      icon='INFO')

    # End of pattern params (col_pat box). Tail handles mapping + Fresnel
    # + mask + pbr channels regardless of whether pattern section is open.
    _draw_procedural_advanced_tail(col, active, tlm)


# Procedurals whose texture node switches between discrete colours
# (Color1/Color2[/Mortar]) with NO ColorRamp -- show only the colour pickers.
# (MAGIC also has no ramp but generates its own colours, handled separately.)
_DIRECT_COLOR_PROCS = {'CHECKER', 'BRICK'}

# Procedurals that support the Triplanar toggle (fac-based organic
# patterns). Mirror of compositing.procedurals._TRIPLANAR_PROCS.
_TRIPLANAR_PROCS = {'NOISE', 'VORONOI', 'WAVE', 'MUSGRAVE', 'RIDGED',
                    'WHITE_NOISE', 'GABOR', 'DOTS', 'CRACKS',
                    'MARBLE', 'WOOD', 'SCRATCHES', 'CAUSTICS', 'WEAVE',
                    'TILES', 'SCATTER', 'TRUCHET'}


def _draw_procedural_advanced_tail(col, active, tlm):
    """Draw post-Pattern sections of a PROCEDURAL layer in the new order:
       Section 3/4 = Mapping (collapsible)
       Section 4/4 = Color Ramp (collapsible) — last because it's stylistic
                      tuning after the pattern + mapping are dialled in.
       Then: Fresnel Rim, Mask, Clipping, Group assignment.
    PBR Channels is drawn at the TOP of the panel by the caller — not here.
    """
    use_manual = getattr(active, 'proc_use_manual_stops', True)
    proc_t = active.proc_type
    manual_supported = proc_t not in ('GRADIENT', 'FRESNEL')

    # ── SECTION 3/4: Mapping ──────────────────────────────────────────
    col.separator(factor=0.4)
    hdr_map = col.row(align=True)
    hdr_map.prop(active, "show_proc_mapping_section",
                 text="Mapping",
                 icon='TRIA_DOWN' if active.show_proc_mapping_section else 'TRIA_RIGHT',
                 emboss=False)
    if active.show_proc_mapping_section:
        map_box = col.box().column(align=True)
        map_box.scale_y = 0.95
        map_box.prop(active, "proc_mapping_type", text="Mapping Type")
        loc_row = map_box.row(align=True)
        loc_row.label(text="Location", icon='OBJECT_ORIGIN')
        loc_row.prop(active, "proc_offset_x", text="X")
        loc_row.prop(active, "proc_offset_y", text="Y")
        loc_row.prop(active, "proc_offset_z", text="Z")
        rot_row = map_box.row(align=True)
        rot_row.label(text="Rotation", icon='DRIVER_ROTATIONAL_DIFFERENCE')
        rot_row.prop(active, "proc_rotation_x", text="X")
        rot_row.prop(active, "proc_rotation_y", text="Y")
        rot_row.prop(active, "proc_rotation_z", text="Z")
        scl_row = map_box.row(align=True)
        scl_row.label(text="Scale", icon='EMPTY_ARROWS')
        scl_row.prop(active, "proc_mapping_scale_x", text="X")
        scl_row.prop(active, "proc_mapping_scale_y", text="Y")
        scl_row.prop(active, "proc_mapping_scale_z", text="Z")

        map_box.separator(factor=0.4)
        map_box.prop(active, "proc_coord_preset", text="Preset")
        map_box.prop(active, "proc_coord_type", text="Coords")
        if active.proc_coord_type == 'OBJECT':
            map_box.prop(active, "proc_normalize_coords", text="Normalize Scale")
        # Triplanar: project the pattern from 3 axes + blend, so it doesn't
        # stretch on faces that don't face the mapping axis (Mapping still
        # applies). Off by default -- costs 3 texture evaluations.
        if active.proc_type in _TRIPLANAR_PROCS:
            map_box.prop(active, "proc_use_triplanar",
                         text="Triplanar (anti-stretch)", toggle=True)
        # View-driven UV parallax — the pattern slides with the camera
        # when this is > 0. Try 0.2–0.3 for holographic-foil parallax.
        map_box.prop(active, "proc_uv_view_shift", slider=True,
                     text="View Parallax")
        # Coordinate transform (polar/spherical/swirl/cylindrical)
        map_box.prop(active, "proc_coord_transform", text="Transform")
        if active.proc_coord_transform == 'SWIRL':
            map_box.prop(active, "proc_swirl_amount", slider=True, text="Swirl")
        if active.proc_coord_transform in {'POLAR', 'CYLINDRICAL'}:
            map_box.label(text="Tip: integer Scale = seamless wrap", icon='INFO')
        elif active.proc_coord_transform == 'SPHERICAL':
            map_box.label(text="Tip: pattern wraps X, poles compress", icon='INFO')
        # Smart warnings for Generated coordinates
        if active.proc_coord_type == 'GENERATED':
            import bpy as _bpy
            obj = _bpy.context.active_object
            if obj:
                s = obj.scale
                tol = 0.02
                if abs(s.x - 1.0) > tol or abs(s.y - 1.0) > tol or abs(s.z - 1.0) > tol:
                    map_box.label(text="Scale not applied; pattern may stretch", icon='ERROR')
                dims = obj.dimensions
                if dims.x > 0 and dims.y > 0 and dims.z > 0:
                    ratio = max(dims) / max(min(dims), 0.001)
                    if ratio > 1.3:
                        map_box.label(text="Anisotropic shape: try Object coords", icon='INFO')
        # Vector distortion lives inside Mapping (it warps the coords).
        map_box.prop(active, "proc_vector_distortion", slider=True, text="Vec Distort")

    # ── SECTION 4/4: Color Ramp ──────────────────────────────────────
    # Final section in the new order — stylistic colour tuning happens
    # after the pattern + mapping are dialled in. Internal order:
    #   Mode/Interp dropdowns + Manual Stops toggle → Color pickers (with
    #   Pos sliders when Manual Stops is on) → extras + Add Stop button
    #   → Contrast/Center (legacy auto-positions, disabled when Manual
    #   Stops is on which is the new default).
    col.separator(factor=0.4)
    # CHECKER / BRICK have no ColorRamp (discrete colours on the node), so the
    # box is just "Colors" and only the pickers are shown.
    is_direct_color = proc_t in _DIRECT_COLOR_PROCS
    n_extras = len(active.proc_extra_color_stops)
    badge_col = "" if is_direct_color else (f" (+{n_extras})" if n_extras else "")
    hdr_color = col.row(align=True)
    hdr_color.prop(active, "show_proc_color_section",
                   text=("Colors" if is_direct_color else f"Color Ramp{badge_col}"),
                   icon='TRIA_DOWN' if active.show_proc_color_section else 'TRIA_RIGHT',
                   emboss=False)
    if active.show_proc_color_section:
        cbox = col.box().column(align=True)
        cbox.scale_y = 0.95
        if is_direct_color:
            # Discrete node colours only -- no ColorRamp, so no positions / mode
            # / interp / stops / contrast. (BRICK adds a Mortar colour.)
            cr = cbox.row(align=True)
            cr.prop(active, "proc_color1", text="")
            cr.prop(active, "proc_color2", text="")
            if active.use_proc_color3:   # BRICK mortar colour
                c3r = cbox.row(align=True)
                c3r.prop(active, "use_proc_color3", text="",
                         icon='REMOVE', toggle=True)
                c3r.prop(active, "proc_color3", text="")
        else:
            # Mode / Interpolation dropdowns FIRST (only proc types using ColorRamp)
            uses_color_ramp = proc_t not in ('STRIPES', 'HEX_GRID')
            if uses_color_ramp:
                mi_row = cbox.row(align=True)
                mi_row.prop(active, "proc_color_ramp_mode", text="")
                mi_row.prop(active, "proc_color_ramp_interpolation", text="")
                if manual_supported:
                    ms_row = cbox.row(align=True)
                    ms_row.prop(active, "proc_use_manual_stops",
                                text="Manual Stops", toggle=True,
                                icon='IPO_LINEAR' if not use_manual else 'IPO_CONSTANT')
                cbox.separator(factor=0.3)
            # THEN colour pickers + per-stop positions
            cr = cbox.row(align=True)
            cr.prop(active, "proc_color1", text="")
            cr.prop(active, "proc_color2", text="")
            if use_manual and manual_supported:
                pos12 = cbox.row(align=True)
                pos12.prop(active, "proc_color1_position", text="Pos 1", slider=True)
                pos12.prop(active, "proc_color2_position", text="Pos 2", slider=True)
            # Legacy Color 3 row (only when the flag is True from old presets)
            legacy_c3 = active.use_proc_color3
            if legacy_c3:
                c3r = cbox.row(align=True)
                c3r.prop(active, "use_proc_color3", text="",
                         icon='REMOVE', toggle=True)
                c3r.prop(active, "proc_color3", text="")
                c3r.prop(active, "proc_color3_position", text="Pos", slider=True)
            # Extra color stops collection
            label_offset = 4 if legacy_c3 else 3
            for idx, stop in enumerate(active.proc_extra_color_stops):
                sr = cbox.row(align=True)
                sr.prop(stop, "color", text="")
                sr.prop(stop, "position", text=f"Pos {idx + label_offset}", slider=True)
                del_op = sr.operator("tlm.remove_proc_color_stop", text="", icon='X')
                del_op.index = idx
            # Add Color Stop button
            add_row = cbox.row(align=True)
            add_row.operator("tlm.add_proc_color_stop", text="Add Color Stop", icon='ADD')
            # Contrast + Ramp Center -- GRADIENT/MAGIC/FRESNEL don't use these;
            # CHECKER/BRICK handled above.
            if proc_t not in ('GRADIENT', 'MAGIC', 'FRESNEL'):
                cbox.separator(factor=0.3)
                cr_row = cbox.row(align=True)
                cr_row.enabled = not use_manual
                cr_row.prop(active, "proc_contrast", slider=True)
                cr_row.prop(active, "proc_ramp_center", slider=True, text="Center")

    # Mask + Surface Effects (Fresnel + Displacement + Volume) + Clipping +
    # Group assignment. Order chosen so the most-edited block (Mask) sits
    # closer to Pattern/Mapping/Color Ramp, with the advanced
    # Surface-Effects collapsible right after.
    col.separator(factor=0.4)
    _draw_mask_block(col, active)
    _draw_surface_effects(col, active, tlm)
    _draw_clipping_mask(col, active, tlm)
    col.separator(factor=0.4)
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
    if src == 'WIREFRAME':
        box.prop(active, "mask_wireframe_size", slider=True, text="Size")
        box.prop(active, "mask_wireframe_use_pixel_size", text="Use Pixel Size")
    if src == 'VORONOI':
        # Per-slot Voronoi mask params. Pair scale with a colour layer's
        # proc_scale for the cobblestone alignment trick.
        feat_prop  = 'mask_voronoi_feature_b'    if is_b else 'mask_voronoi_feature'
        scale_prop = 'mask_voronoi_scale_b'      if is_b else 'mask_voronoi_scale'
        rand_prop  = 'mask_voronoi_randomness_b' if is_b else 'mask_voronoi_randomness'
        edge_prop  = 'mask_voronoi_edge_width_b' if is_b else 'mask_voronoi_edge_width'
        box.prop(active, feat_prop, text="Feature")
        box.prop(active, scale_prop, slider=True, text="Scale")
        box.prop(active, rand_prop, slider=True, text="Randomness")
        box.prop(active, edge_prop, slider=True, text="Edge Width")
    # POINTINESS, EDGE_WEAR, CURVATURE_SMART: no per-slot parameter.
    # Shared smart-generator tuning shown once below in its own sub-box.
    box.prop(active, inv_prop, text=f"Invert {slot}")


def _draw_mask_block(col, active):
    """Shared mask UI used by Procedural / Paint / Fill / Reference layers.

    Includes: Mask A + optional Mask B + combine mode + Contrast +
    Smart Generator controls (when source is EDGE_WEAR/DIRT/CURVATURE_SMART) +
    Mask Refinement section (Levels + Softness + Blur).

    UX:
    - Collapsible header (show_mask_section) is ALWAYS shown so the layer
      panel keeps a consistent section structure regardless of whether
      a mask is configured. A small toggle on the right of the header
      enables/disables the mask without losing settings.
    - When the box is expanded with use_mask=False, the body shows the
      two add-paths ("Add Mask" creates a paintable image; "Bake Smart"
      runs the smart-mask bake operator).
    - When use_mask=True, the body shows the full mask configuration.
    """
    # Always-visible collapsible header so the section is discoverable
    # regardless of state. The mask icon doubles as the quick on/off toggle.
    if active.use_mask:
        head_text = f"Mask  ·  {active.mask_source.replace('_', ' ').title()}"
    else:
        head_text = "Mask"
    header = col.row(align=True)
    header.prop(
        active, "show_mask_section",
        text=head_text,
        icon='TRIA_DOWN' if active.show_mask_section else 'TRIA_RIGHT',
        emboss=False,
    )
    header.prop(active, "use_mask", text="", icon='MOD_MASK', toggle=True)

    if not active.show_mask_section:
        return

    mbox = col.box().column(align=True)

    # Mask is off — surface the two enabling paths INSIDE the collapsible
    # box (so the structure stays consistent with the on-state).
    if not active.use_mask:
        mbox.label(text="No mask configured", icon='INFO')
        mr = mbox.row(align=True)
        mr.operator("tlm.add_layer_mask", text="Add Mask", icon='ADD')
        mr.operator("tlm.add_smart_mask", text="Bake Smart Mask", icon='SHADERFX')
        return

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

    # ── Contrast (always visible — most common knob) ──
    mbox.prop(active, "mask_contrast", slider=True, text="Contrast")

    # ── Refinement (Levels / Softness / Blur) — collapsible ─────────────
    # Count active refinements so the collapsed header shows a badge.
    refine_count = (
        (1 if active.use_mask_levels else 0)
        + (1 if active.mask_softness > 1e-4 else 0)
        + (1 if active.mask_source == 'IMAGE' and active.mask_blur > 1e-4 else 0)
    )
    refine_badge = f" ({refine_count})" if refine_count else ""
    mbox.separator(factor=0.4)
    ref_hdr = mbox.row(align=True)
    ref_hdr.prop(active, "show_mask_refinement",
                 text=f"Refinement{refine_badge}",
                 icon='TRIA_DOWN' if active.show_mask_refinement else 'TRIA_RIGHT',
                 emboss=False)
    if active.show_mask_refinement:
        rbox = mbox.box().column(align=True)
        rbox.scale_y = 0.9
        # Softness — softens the mask transition without remapping
        rbox.prop(active, "mask_softness", slider=True, text="Softness")
        # Image-only: Blur — taps the UV input
        if active.mask_source == 'IMAGE':
            rbox.prop(active, "mask_blur", slider=True, text="Blur")
        # Levels — input/output remap + gamma
        rbox.separator(factor=0.3)
        rbox.prop(active, "use_mask_levels", text="Levels",
                  icon='IPO_LINEAR', toggle=True)
        if active.use_mask_levels:
            lbox = rbox.box().column(align=True)
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


def _draw_surface_effects(col, active, tlm):
    """Collapsible: per-layer surface modifiers (Fresnel + Displacement opt-in).

    Two features grouped under one header — both are PER-LAYER state:

      * Fresnel Rim          — per-layer view-angle modulator (use_fresnel_mask
                               + fresnel_ior + fresnel_strength)
      * Add to Displace      — per-layer flag opting this layer into the
                               material-level displacement stack. The shared
                               material-level settings (Method / Strength /
                               Midlevel) live in the Composite section to
                               avoid duplicating UI across multiple layers.

    Material-level Volume Absorption / Scatter was moved OUT of this
    collapsible (it lives only in Composite section) because per-layer
    UI duplication created the impression those are per-layer toggles
    when they're actually a single material datablock.

    Only meaningful on layers that contribute to surface shading
    (PAINT, FILL, PROCEDURAL, REFERENCE). ADJUSTMENT and GROUP skip it.
    """
    n_active = (
        (1 if active.use_fresnel_mask else 0)
        + (1 if getattr(active, 'use_displacement', False) else 0)
    )
    badge = f" ({n_active})" if n_active else ""

    col.separator(factor=0.4)
    hdr = col.row(align=True)
    hdr.prop(active, "show_surface_effects",
             text=f"Surface Effects{badge}",
             icon='TRIA_DOWN' if active.show_surface_effects else 'TRIA_RIGHT',
             emboss=False)
    if not active.show_surface_effects:
        return

    sbox = col.box().column(align=True)
    sbox.scale_y = 0.95

    # ── Fresnel Rim (per-layer) ─────────────────────────────────────
    sbox.label(text="Fresnel Rim", icon='LIGHT_HEMI')
    fr = sbox.row(align=True)
    fr.prop(active, "use_fresnel_mask", text="Enable", toggle=True)
    if active.use_fresnel_mask:
        fr.prop(active, "fresnel_ior",      text="IOR")
        fr.prop(active, "fresnel_strength", text="Str", slider=True)

    # ── Displacement (per-layer opt-in only — shared settings in Composite) ──
    sbox.separator(factor=0.5)
    sbox.label(text="Displacement", icon='MOD_SUBSURF')
    dr = sbox.row(align=True)
    dr.prop(active, "use_displacement",
            text="Add to Displace", toggle=True)
    if active.use_displacement:
        dr.prop(active, "displacement_scale",
                text="Layer Scale", slider=True)
        if not tlm.use_displacement:
            warn = sbox.row(align=True)
            warn.alert = True
            warn.label(text="Master Displacement OFF (see Composite section)",
                       icon='ERROR')
        else:
            sbox.label(text="Shared settings live in the Composite section",
                       icon='INFO')


def _draw_reference(col, active, tlm):
    """Reference layer UI — reuses another layer's pattern with its own blend/mask/channels."""
    br = col.row(align=True)
    # Alpha uses Shader Math operations instead of artistic colour blends.
    if getattr(active, 'output_channel', 'BASE_COLOR') == 'ALPHA':
        br.prop(active, "alpha_math_operation", text="")
    else:
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
    ref = None
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

    # ── Reference mode (Composed vs Raw Pattern) ─────────────────────
    # RAW_PATTERN only meaningful when the source is PROCEDURAL — show
    # the dropdown contextually, and surface this layer's own ColorRamp
    # controls so the user can remap the source's raw FAC to a
    # different colour pattern + channel.
    src_is_proc = ref is not None and ref.layer_type == "PROCEDURAL"
    if src_is_proc:
        col.separator(factor=0.4)
        col.prop(active, "reference_mode", text="Mode")
        if active.reference_mode == 'RAW_PATTERN':
            box = col.box().column(align=True)
            box.label(text="Own ColorRamp (applied to source FAC):",
                      icon='IPO_LINEAR')
            mi = box.row(align=True)
            mi.prop(active, "proc_color_ramp_mode", text="")
            mi.prop(active, "proc_color_ramp_interpolation", text="")
            ms = box.row(align=True)
            ms.prop(active, "proc_use_manual_stops", text="Manual Stops",
                    toggle=True,
                    icon='IPO_LINEAR' if not active.proc_use_manual_stops
                         else 'IPO_CONSTANT')
            box.separator(factor=0.3)
            cr = box.row(align=True)
            cr.prop(active, "proc_color1", text="")
            cr.prop(active, "proc_color2", text="")
            if active.proc_use_manual_stops:
                ps = box.row(align=True)
                ps.prop(active, "proc_color1_position",
                        text="Pos 1", slider=True)
                ps.prop(active, "proc_color2_position",
                        text="Pos 2", slider=True)
            # Extra stops
            for idx, stop in enumerate(active.proc_extra_color_stops):
                sr = box.row(align=True)
                sr.prop(stop, "color", text="")
                sr.prop(stop, "position",
                        text=f"Pos {idx + 3}", slider=True)
                del_op = sr.operator("tlm.remove_proc_color_stop",
                                      text="", icon='X')
                del_op.index = idx
            box.operator("tlm.add_proc_color_stop",
                         text="Add Color Stop", icon='ADD')

    col.separator(factor=0.6)
    _draw_mask_block(col, active)

    _draw_surface_effects(col, active, tlm)

    col.separator(factor=0.6)
    # Note: Normal + Bump channels on a REFERENCE layer use this layer's own
    # image/strength — they do NOT pick up from the referenced pattern.
    # That's an intentional limitation of the Mix(VECTOR) normal pipeline.
    if getattr(active, 'use_normal', False) or getattr(active, 'use_bump', False):
        col.label(text="Normal/Bump use THIS layer's images, not the reference's",
                  icon='INFO')
    _draw_pbr_channels(col, active, tlm)

    _draw_clipping_mask(col, active, tlm)

    col.separator(factor=0.5)
    _draw_group_assignment(col, active, tlm)


def _draw_adjustment(col, active, tlm):
    # Target channel — adjustments now route to a specific PBR channel,
    # not just base color. Colour channels (Base Color) accept every
    # adj_type; scalar channels (Roughness / Metallic / Alpha) only get
    # a meaningful effect from BRIGHT_CONTRAST and LEVELS, while
    # HUE_SAT / COLOR_BALANCE pass through as no-ops. We surface a hint
    # so the user knows when their pick is silently ignored.
    oc = col.row(align=True)
    oc.prop(active, "output_channel", text="Target", icon='NODE_COMPOSITING')
    col.prop(active, "adj_type")

    target_is_scalar = active.output_channel in ('ROUGHNESS', 'METALLIC', 'ALPHA')
    if target_is_scalar and active.adj_type in ('HUE_SAT', 'COLOR_BALANCE', 'GRADIENT_MAP'):
        col.label(text=f"{active.adj_type.replace('_', '/')} has no effect on a scalar channel",
                  icon='INFO')

    br = col.row(align=True)
    br.prop(active, "opacity",   text="Opacity / Strength", slider=True)
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
    elif active.adj_type == 'GRADIENT_MAP':
        col.label(text="Gradient (dark → light):")
        rr = col.row(align=True)
        rr.prop(active, "proc_color1", text="")
        rr.prop(active, "proc_color2", text="")
        if active.use_proc_color3:
            rr.prop(active, "proc_color3", text="")
        col.prop(active, "use_proc_color3", text="3-Colour Ramp", toggle=True)
        if active.use_proc_color3:
            col.prop(active, "proc_color3_position", text="Mid Position", slider=True)
        cr = col.row(align=True)
        cr.prop(active, "proc_contrast", text="Contrast", slider=True)
        cr.prop(active, "proc_ramp_center", text="Center", slider=True)
    col.separator(factor=0.5)
    _draw_group_assignment(col, active, tlm)


def _draw_paint_fill(col, active, tlm):
    br = col.row(align=True)
    if getattr(active, 'output_channel', 'BASE_COLOR') == 'ALPHA':
        br.prop(active, "alpha_math_operation", text="")
    else:
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
    # Primary Output = Emission: surface strength (and, for a Fill with no
    # image, the emission colour) right here. Previously the only way to
    # reach these was the secondary "Emission" toggle in PBR Channels.
    if active.output_channel == 'EMISSION':
        if active.layer_type == "FILL":
            col.prop(active, "emission_color", text="Emission Color")
        col.prop(active, "emission_strength", text="Emission Strength", slider=True)

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

    _draw_surface_effects(col, active, tlm)

    # ── Image Mapping (paint + PBR image layers) ────────────────────────────
    # Collapsible: Source / Interpolation / Projection / Extension plus
    # Location/Rotation/Scale (per-axis). Mirrors the layout of Blender's
    # native ShaderNodeTexImage panel so users moving between addons
    # already know where to look. Only meaningful for layers that
    # produce image textures (PAINT) or use PBR channel images (FILL /
    # Procedural with channel images).
    #
    # Box projection replaces the old custom Triplanar feature: it
    # samples along the three world axes and blends them, parametrised
    # by `paint_projection_blend` (= Blender's "Blend" socket).
    col.separator(factor=0.6)
    mr = col.row(align=True)
    mr.prop(active, "show_paint_mapping",
            text="Mapping",
            icon='TRIA_DOWN' if active.show_paint_mapping else 'TRIA_RIGHT',
            emboss=False)
    if active.show_paint_mapping:
        mbox = col.box().column(align=True)
        mbox.scale_y = 0.9
        # Sampling configuration: 4 dropdowns matching Blender's native
        # Image Texture node layout (Source / Interpolation / Projection /
        # Extension).
        mbox.prop(active, "paint_source",        text="Source")
        mbox.prop(active, "paint_interpolation", text="Interpolation")
        mbox.prop(active, "paint_projection",    text="Projection")
        if active.paint_projection == 'BOX':
            mbox.prop(active, "paint_projection_blend", text="Blend", slider=True)
        mbox.prop(active, "paint_extension",     text="Extension")
        mbox.separator(factor=0.4)
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

    _draw_clipping_mask(col, active, tlm)

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

    # Displacement now lives in the Surface Effects collapsible — it's a
    # geometric modifier (not a shading channel) and shares space with
    # Fresnel + Volume there. The Bump row above is the only normal-
    # modifying control that stays in PBR Channels, because Bump
    # perturbs the shading normal (per-channel data flow) while
    # Displacement moves vertices (per-material vertex flow).

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
                    # ── Selective emission ───────────────────────────
                    # Gates the procedural emission to a subset of regions
                    # (random cells / noise blobs / painted mask) instead
                    # of lighting up the entire procedural pattern.
                    pc.separator(factor=0.3)
                    pc.prop(layer, "emission_selector_type", text="Lit Regions")
                    sel_type = layer.emission_selector_type
                    if sel_type in ('RANDOM_CELLS', 'NOISE'):
                        sr = pc.row(align=True)
                        sr.prop(layer, "emission_selector_scale",
                                text="Scale" if sel_type == 'NOISE' else "Cells")
                        sr.prop(layer, "emission_selector_threshold",
                                text="Lit Frac", slider=True)
                        pc.prop(layer, "emission_selector_seed", text="Seed")
                    elif sel_type == 'IMAGE':
                        pc.prop_search(layer, "emission_selector_image_name",
                                       bpy.data, "images", text="Mask Image")
                if layer.blend_mode == "ADD":
                    pc.label(text="ADD + Emission: use only one", icon='ERROR')
            if ch_id == 'alpha' and enabled:
                pc.prop(layer, "alpha_math_operation", text="Operation")
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


def _perf_metric(layout, label, value, icon='BLANK1'):
    row = layout.row(align=True)
    row.label(text=label, icon=icon)
    row.label(text=value)


def _draw_composite_section(layout, tlm):
    """Material-level composite controls, organised into logical groups:
       1. Build mode (Auto Composite / Editable)
       2. Surface (BSDF IOR + Alpha pipeline)
       3. Volume (Absorption + Scatter — only shown when active to keep panel short)
       4. Displacement (Master + Method/Strength/Midlevel/Adaptive)
       5. Actions (Rebuild / Flatten / Convert / Refresh thumbs)

    Volume + Displacement collapse into a single info row when their master
    toggle is off — keeps the section visually quiet when not in use.
    """
    comp = layout.column(align=True)
    if tlm.shader_editable:
        box = comp.box()
        box.label(text="Editable Shader mode", icon='NODE_MATERIAL')
        box.label(text="Layer changes will not rebuild the shader.")
        box.operator("tlm.return_to_managed_shader",
                     text="Return to TLM Managed", icon='FILE_REFRESH')
        return

    # ── 1. Build mode ──
    ac_icon = 'LINKED' if tlm.auto_composite else 'UNLINKED'
    comp.prop(tlm, "auto_composite", text="Auto Composite",
              icon=ac_icon, toggle=True)

    # ── 2. Surface ──
    # IOR + Alpha pipeline. The IOR slider drives BSDF.IOR, but the build
    # gates it on transmission being present in the stack: Principled's
    # IOR also raises opaque dielectric Fresnel, so applying it without
    # transmission would wash opaque materials toward white. The slider
    # stays visible so users can set it before adding the glass layer.
    comp.separator(factor=0.6)
    surf_box = comp.box().column(align=True)
    surf_box.label(text="Surface", icon='NODE_MATERIAL')
    ior_row = surf_box.row(align=True)
    ior_row.prop(tlm, "bsdf_ior", text="IOR", slider=True)
    # Hint label: when IOR is a non-default value but transmission isn't in
    # the active stack, the user's IOR setting is silent — make that clear.
    if abs(tlm.bsdf_ior - 1.45) > 1e-3:
        from .compositing.channels import stack_has_transmission
        if not stack_has_transmission(tlm.layers):
            hint = surf_box.row(align=True)
            hint.label(
                text=f"IOR {tlm.bsdf_ior:.2f} not applied — needs transmission in the stack",
                icon='INFO',
            )
    surf_box.separator(factor=0.3)
    # Alpha pipeline — labelled clearly so the user knows what it does.
    surf_box.label(text="Alpha:", icon='IMAGE_ALPHA')
    surf_box.prop(tlm, "use_base_color_alpha",
                  text="Use PNG Alpha Channel  (foliage / decals)",
                  icon='IMAGE_ALPHA', toggle=True)

    # Contextual hints for use_base_color_alpha. The toggle has very
    # different effects depending on what the user's PAINT layers actually
    # carry — so we surface the relevant guidance instead of relying on
    # the (long) tooltip text the user might not read.
    paint_layers_with_image = [
        l for l in tlm.layers
        if l.visible and l.layer_type == 'PAINT' and l.image is not None
    ]
    paint_pngs_with_alpha = [
        l for l in paint_layers_with_image
        if (l.image.depth in (32, 64, 128)  # 8/16/32-bit RGBA
            or l.image.alpha_mode != 'NONE')
        and l.image.source == 'FILE'
        and bool(l.image.filepath)
    ]
    if tlm.use_base_color_alpha:
        if not paint_layers_with_image:
            hint = surf_box.row(align=True)
            hint.alert = True
            hint.label(
                text="No PAINT layer — toggle has no effect",
                icon='ERROR',
            )
        elif not paint_pngs_with_alpha:
            # All paint layers use TLM-generated canvases (no real alpha
            # channel) — toggle silently does nothing.
            hint = surf_box.row(align=True)
            hint.alert = True
            hint.label(
                text="Painted canvases have alpha=1. Use Output:Alpha instead",
                icon='ERROR',
            )
    else:
        # Toggle OFF but a PNG with alpha exists — suggest enabling.
        if paint_pngs_with_alpha:
            hint = surf_box.row(align=True)
            hint.label(
                text=f"PNG with alpha detected ({paint_pngs_with_alpha[0].image.name})",
                icon='INFO',
            )

    surf_box.prop(tlm, "alpha_blend_method", text="Blend Mode")

    # ── 3. Volume — quiet when off, expands when active ──
    comp.separator(factor=0.6)
    vol_box = comp.box().column(align=True)
    vol_box.label(text="Volume", icon='OUTLINER_DATA_VOLUME')
    vol_box.prop(tlm, "use_volume_absorption", text="Absorption",
                 icon='OUTLINER_DATA_VOLUME', toggle=True)
    if tlm.use_volume_absorption:
        vol_box.prop(tlm, "volume_absorption_color", text="Color")
        vol_box.prop(tlm, "volume_absorption_density", text="Density", slider=True)
    vol_box.separator(factor=0.3)
    vol_box.prop(tlm, "use_volume_scatter", text="Scatter",
                 icon='OUTLINER_OB_VOLUME', toggle=True)
    if tlm.use_volume_scatter:
        vol_box.prop(tlm, "volume_scatter_color", text="Color")
        vol_box.prop(tlm, "volume_scatter_density", text="Density", slider=True)
        vol_box.prop(tlm, "volume_scatter_anisotropy", text="Anisotropy", slider=True)

    # ── 4. Displacement ──
    # Master toggle wires the layer stack's displacement contributions to
    # Material Output.Displacement. Per-layer use_displacement (labelled
    # "Add to Displace" in the Surface Effects section of the layer panel)
    # selects which layers feed into the height stack.
    comp.separator(factor=0.6)
    disp_box = comp.box().column(align=True)
    disp_box.label(text="Displacement", icon='MOD_SUBSURF')
    disp_box.prop(tlm, "use_displacement", text="Master",
                  icon='MOD_SUBSURF', toggle=True)
    if tlm.use_displacement:
        disp_box.prop(tlm, "displacement_method", text="Method")
        disp_box.prop(tlm, "displacement_strength", text="Strength", slider=True)
        disp_box.prop(tlm, "displacement_midlevel", text="Midlevel", slider=True)
        # Adaptive Subdiv is meaningless for BUMP method — hide it.
        if tlm.displacement_method != 'BUMP':
            disp_box.prop(tlm, "displacement_adaptive", text="Auto Adaptive Subdiv",
                          icon='MESH_GRID', toggle=True)
        # Count layers actually feeding the stack so the user knows whether
        # the master toggle has any effect.
        n_feeders = sum(1 for l in tlm.layers
                        if l.visible and getattr(l, 'use_displacement', False))
        if n_feeders == 0:
            warn = disp_box.row(align=True)
            warn.alert = True
            warn.label(
                text="No layer opted in — toggle 'Add to Displace' on a layer",
                icon='ERROR',
            )

    # ── 5. Actions ──
    comp.separator(factor=0.6)
    ops_row = comp.row(align=True)
    ops_row.operator("tlm.rebuild_composite", text="Rebuild", icon='FILE_REFRESH')
    ops_row.operator("tlm.flatten_layers",    text="Flatten", icon='IMAGE_ZDEPTH')
    comp.operator("tlm.convert_to_editable_shader",
                  text="Convert to Editable Shader", icon='NODE_MATERIAL')
    comp.operator("tlm.refresh_thumbnails",
                  text="Refresh Thumbnails", icon='FILE_REFRESH')


def _draw_performance_section(layout, tlm):
    perf = layout.column(align=True)
    perf.prop(tlm, "performance_debug",
              text="Performance Debug", icon='INFO', toggle=True)
    if not tlm.performance_debug:
        return

    _perf_metric(perf, "Rebuild", f"{tlm.perf_last_rebuild_ms:.2f} ms", 'NODETREE')
    hot_status = "OK" if tlm.perf_last_hot_update_ok else "Fallback"
    hot_prop = tlm.perf_last_hot_update_prop or "-"
    _perf_metric(
        perf,
        "Hot Update",
        f"{tlm.perf_last_hot_update_ms:.2f} ms  {hot_prop}  {hot_status}",
        'FILE_REFRESH',
    )
    _perf_metric(
        perf,
        "Bake",
        f"{tlm.perf_last_bake_ms:.2f} ms  {tlm.perf_last_bake_maps} maps",
        'RENDER_STILL',
    )
    smart_status = "OK" if tlm.perf_last_smart_mask_ok else "-"
    _perf_metric(
        perf,
        "Smart Mask",
        f"{tlm.perf_last_smart_mask_ms:.2f} ms  {smart_status}",
        'SHADERFX',
    )

    perf.separator(factor=0.5)
    _perf_metric(
        perf,
        "Graph",
        f"{tlm.perf_last_node_count} nodes  {tlm.perf_last_visible_layer_count}/{tlm.perf_last_layer_count} layers",
        'NODE_MATERIAL',
    )
    _perf_metric(
        perf,
        "Mesh",
        f"{tlm.perf_last_mesh_vertices} verts  {tlm.perf_last_mesh_faces} faces  {tlm.perf_last_mesh_objects} obj",
        'MESH_DATA',
    )


def _draw_bake_section(layout, tlm):
    """Export the composited material to flat PNG textures on disk.

    Distinct from per-layer "PBR Channels" (which is the layer's contribution
    routing). These operators take the FINAL composited shader, render each
    channel to an image, and save them as PNG — what you'd ship to Unity /
    Unreal / Godot / glTF when the addon won't be installed at render time.
    """
    bake = layout.column(align=True)
    bake.label(text="Export composited material → PNG textures",
               icon='INFO')
    bake.operator("tlm.bake_pbr", text="Bake to PBR Textures…", icon='EXPORT')
    op = bake.operator("tlm.bake_pbr", text="Custom Channels…",
                       icon='NODE_COMPOSITING')
    op.preset = 'CUSTOM'


def _draw_presets_section(layout, tlm):
    from .operators import BUILTIN_PRESETS
    from .operators.presets import _legacy_preset_dir, _user_preset_dir

    # In-code built-in presets (none shipped by default) — 2-col grid.
    if BUILTIN_PRESETS:
        grid = layout.column(align=True)
        grid.scale_y = 0.95
        prow = None
        for i, pname in enumerate(BUILTIN_PRESETS):
            if i % 2 == 0:
                prow = grid.row(align=True)
            op = prow.operator("tlm.apply_preset", text=pname, icon='MATERIAL')
            op.preset_name = pname

    # Cache both folders (legacy = bundled, user = saved) to avoid an
    # os.listdir on every redraw.
    global _preset_cache, _preset_cache_time
    now = _time.monotonic()
    if now - _preset_cache_time > _PRESET_CACHE_TTL:
        _preset_cache_time = now

        def _list(d):
            try:
                return sorted(f[:-4] for f in _os.listdir(d) if f.endswith(".tlm"))
            except OSError:
                return []
        _preset_cache = (_list(_legacy_preset_dir()), _list(_user_preset_dir()))
    legacy_presets, user_presets = _preset_cache

    # ── Legacy presets: the curated set bundled with the addon (apply only) ──
    layout.separator(factor=0.5)
    layout.label(text="Legacy Presets:", icon='PRESET')
    if legacy_presets:
        lgrid = layout.column(align=True)
        lgrid.scale_y = 0.95
        for pname in legacy_presets:
            op = lgrid.operator("tlm.apply_preset", text=pname, icon='MATERIAL')
            op.preset_name = pname
    else:
        r = layout.row()
        r.enabled = False
        r.label(text="None bundled")

    # ── Saved presets: user-created, stored outside the addon (full control) ──
    layout.separator(factor=0.5)
    layout.label(text="Saved Presets:", icon='FILE_FOLDER')
    if user_presets:
        ugrid = layout.column(align=True)
        ugrid.scale_y = 0.95
        for pname in user_presets:
            urow = ugrid.row(align=True)
            op = urow.operator("tlm.apply_preset", text=pname, icon='PRESET')
            op.preset_name = pname
            rop = urow.operator("tlm.rename_preset", text="", icon='GREASEPENCIL')
            rop.preset_name = pname
            dop = urow.operator("tlm.delete_preset", text="", icon='TRASH')
            dop.preset_name = pname
    else:
        r = layout.row()
        r.enabled = False
        r.label(text="No saved presets yet")
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
TLM_PT_PropsPerformance = _make_section_panel(
    "TLM_PT_props_performance", "Performance",   'INFO',
    "TLM_PT_main_panel", _draw_performance_section,
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
TLM_PT_ViewPerformance = _make_section_panel(
    "TLM_PT_view_performance", "Performance",   'INFO',
    "TLM_PT_viewport_panel", _draw_performance_section,
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
    # Sub-panel order = display order in the Properties tab.
    # Most-used at the top, least-used at the bottom.
    # Composite = material-level settings (IOR, Volume, Displacement, Actions).
    # Presets = preset library.
    # Bake & Export = export to PBR textures.
    # Layer Stack I/O = JSON/TLM file exchange.
    # Performance = debug metrics, only useful when troubleshooting.
    # Canvas = default resolution + UV map for NEW layers (set once per project).
    TLM_PT_PropsComposite,
    TLM_PT_PropsPresets,
    TLM_PT_PropsBake,
    TLM_PT_PropsIO,
    TLM_PT_PropsPerformance,
    TLM_PT_PropsCanvas,
    TLM_PT_ViewportPanel,
    TLM_PT_ViewComposite,
    TLM_PT_ViewPresets,
    TLM_PT_ViewBake,
    TLM_PT_ViewIO,
    TLM_PT_ViewPerformance,
    TLM_PT_ViewCanvas,
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
