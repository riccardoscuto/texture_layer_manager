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
    'RED': 'SEQUENCE_COLOR_01', 'ORANGE': 'SEQUENCE_COLOR_02',
    'YELLOW': 'SEQUENCE_COLOR_03', 'GREEN': 'SEQUENCE_COLOR_04',
    'BLUE': 'SEQUENCE_COLOR_06', 'PURPLE': 'SEQUENCE_COLOR_07',
    'PINK': 'SEQUENCE_COLOR_08',
}

_PBR_BADGE = {
    'use_roughness':    'RNDCURVE',
    'use_metallic':     'MATFLUID',
    'use_normal':       'NORMALS_FACE',
    'use_emission':     'LIGHT',
    'use_transmission': 'MATSPHERE',
    'use_bump':         'MOD_DISPLACE',
}


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
        else:
            fallback = {
                'PAINT': 'IMAGE_RGB_ALPHA', 'FILL': 'COLOR',
                'ADJUSTMENT': 'MODIFIER', 'PROCEDURAL': 'TEXTURE',
            }.get(layer.layer_type, 'IMAGE_DATA')
            iid = 0
            try:
                if layer.layer_type == "PAINT":
                    iid = previews.get_layer_icon_id(layer)
                elif layer.layer_type == "FILL":
                    iid = previews.get_fill_icon_id(layer)
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
    add_row.operator("tlm.add_group",              text="",      icon='FILE_FOLDER')
    add_row.separator()
    add_row.operator("tlm.import_texture_as_layer", text="", icon='IMPORT')
    add_row.operator("tlm.layer_from_clipboard",    text="", icon='COPYDOWN')

    ops_row = layout.row(align=True)
    ops_row.operator("tlm.remove_layer",    text="", icon='TRASH')
    ops_row.separator()
    ops_row.operator("tlm.move_layer",      text="", icon='TRIA_UP').direction   = "UP"
    ops_row.operator("tlm.move_layer",      text="", icon='TRIA_DOWN').direction = "DOWN"
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
        'PROCEDURAL': 'TEXTURE',
    }.get(active.layer_type, 'IMAGE_DATA')

    ltype_label = {
        'PAINT': "Paint Layer", 'FILL': "Fill Layer",
        'ADJUSTMENT': "Adjustment Layer", 'GROUP': "Group",
        'PROCEDURAL': "Procedural Layer",
    }.get(active.layer_type, "Layer")

    box = layout.box()
    hrow = box.row(align=True)
    hrow.label(text=ltype_label, icon=ltype_icon)
    hrow.prop(active, "name", text="", emboss=True)
    solo_active = (tlm.solo_layer_index == tlm.active_layer_index)
    hrow.operator(
        "tlm.solo_layer", text="",
        icon='OUTLINER_OB_LIGHT' if solo_active else 'LIGHT',
        emboss=False,
    ).layer_index = tlm.active_layer_index
    hrow.prop(active, "color_tag", text="", icon_only=True)

    col = box.column(align=True)

    if active.layer_type == "PROCEDURAL":
        _draw_procedural(col, active, tlm)
    elif active.layer_type == "GROUP":
        col.prop(active, "opacity", slider=True)
        children = [l for l in tlm.layers if l.group_name == active.name]
        n_vis = sum(1 for l in children if l.visible)
        col.label(
            text=f"{len(children)} layer{'s' if len(children) != 1 else ''}  ({n_vis} visible)",
            icon='LAYER_ACTIVE'
        )
    elif active.layer_type == "ADJUSTMENT":
        _draw_adjustment(col, active, tlm)
    else:
        _draw_paint_fill(col, active, tlm)


def _draw_procedural(col, active, tlm):
    # Blend mode + Opacity at top (same position as Fill/Paint)
    br = col.row(align=True)
    br.prop(active, "blend_mode", text="")
    br.prop(active, "opacity",    text="Opacity", slider=True)

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
        col.prop(active, "proc_distortion",     slider=True)
    elif pt == 'VORONOI':
        col.prop(active, "proc_voronoi_feature")
        col.prop(active, "proc_voronoi_distance")
        col.prop(active, "proc_randomness", slider=True)
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
    elif pt == 'CHECKER':
        col.prop(active, "proc_checker_scale")
    elif pt == 'MARBLE':
        col.prop(active, "proc_marble_wave_type", text="Pattern")
        col.prop(active, "proc_detail", slider=True)
        col.prop(active, "proc_roughness_proc", slider=True, text="Roughness")
        col.prop(active, "proc_distortion", slider=True, text="Wave Distortion")
        col.prop(active, "proc_marble_distortion", slider=True, text="Turbulence")
    elif pt == 'CLOUDS':
        col.prop(active, "proc_detail",         slider=True)
        col.prop(active, "proc_roughness_proc", slider=True, text="Roughness")
        col.prop(active, "proc_lacunarity",     slider=True)
        col.prop(active, "proc_distortion",     slider=True)


    col.separator(factor=0.5)
    off_row = col.row(align=True)
    off_row.label(text="Offset:", icon='OBJECT_ORIGIN')
    off_row.prop(active, "proc_offset_x", text="X")
    off_row.prop(active, "proc_offset_y", text="Y")
    off_row.prop(active, "proc_offset_z", text="Z")

    col.prop(active, "proc_coord_type", text="Coords")
    col.prop(active, "proc_contrast", slider=True)
    col.prop(active, "proc_vector_distortion", slider=True, text="Vec Distort")

    col.separator(factor=0.6)
    fr = col.row(align=True)
    fr.prop(active, "use_fresnel_mask", text="Fresnel", icon='LIGHT_HEMI', toggle=True)
    if active.use_fresnel_mask:
        fr.prop(active, "fresnel_ior", text="IOR")
        fr.prop(active, "fresnel_strength", text="Str", slider=True)

    col.separator(factor=0.6)
    mr = col.row(align=True)
    mr.prop(active, "use_mask", text="Mask", icon='MOD_MASK', toggle=True)
    if active.use_mask and active.mask_image_name:
        mr.prop_search(active, "mask_image_name", bpy.data, "images",
                       text="", icon='IMAGE_DATA')
    elif active.use_mask:
        mr.operator("tlm.add_layer_mask", text="New",   icon='ADD')
    else:
        mr.operator("tlm.add_layer_mask", text="Add",   icon='ADD')
    mr.operator("tlm.add_smart_mask",     text="Smart", icon='SHADERFX')
    col.prop(active, "use_clipping_mask",
             text="Clipping Mask", icon='CLIPUV_DEHLT', toggle=True)

    col.separator(factor=0.6)
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
    br.prop(active, "blend_mode", text="")
    br.prop(active, "opacity",    text="", slider=True)

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
    mr = col.row(align=True)
    mr.prop(active, "use_mask", text="Mask", icon='MOD_MASK', toggle=True)
    if active.use_mask and active.mask_image_name:
        mr.prop_search(active, "mask_image_name", bpy.data, "images",
                       text="", icon='IMAGE_DATA')
    elif active.use_mask:
        mr.operator("tlm.add_layer_mask", text="New",   icon='ADD')
    else:
        mr.operator("tlm.add_layer_mask", text="Add",   icon='ADD')
    mr.operator("tlm.add_smart_mask",     text="Smart", icon='SHADERFX')
    col.prop(active, "use_clipping_mask",
             text="Clipping Mask", icon='CLIPUV_DEHLT', toggle=True)

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

    col.separator(factor=0.6)
    _draw_pbr_channels(col, active, tlm)

    col.separator(factor=0.5)
    _draw_group_assignment(col, active, tlm)


def _draw_pbr_channels(col, layer, tlm):
    col.label(text="PBR Channels:", icon='NODE_MATERIAL')
    pbox = col.box()
    pc = pbox.column(align=True)
    pc.scale_y = 0.9

    bumpr = pc.row(align=True)
    bumpr.prop(layer, "use_bump", text="Bump", icon='MOD_DISPLACE', toggle=True)
    if layer.use_bump:
        bumpr.prop(layer, "bump_strength", text="Str", slider=True)
        bumpr.prop(layer, "bump_distance", text="Dist", slider=True)

    channels = [
        ('roughness', 'use_roughness', 'roughness_image_name', 'roughness_fill',
         None,            "Roughness", 'RNDCURVE'),
        ('metallic',  'use_metallic',  'metallic_image_name',  'metallic_fill',
         None,            "Metallic",  'MATFLUID'),
        ('normal',    'use_normal',    'normal_image_name',    None,
         None,            "Normal",    'NORMALS_FACE'),
        ('emission',  'use_emission',  'emission_image_name',  None,
         'emission_color',"Emission",  'LIGHT'),
        ('transmission','use_transmission','transmission_image_name','transmission_fill',
         None,            "Transmission",'MATSPHERE'),
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
            if ch_id == 'emission' and enabled:
                pc.prop(layer, "emission_strength", slider=True)
                if layer.layer_type == "PROCEDURAL":
                    pc.prop(layer, "proc_emission_threshold", slider=True, text="Threshold")
                if layer.blend_mode == "ADD":
                    pc.label(text="ADD + Emission: use only one", icon='ERROR')
        else:
            op = ch_row.operator("tlm.add_channel_image", text="", icon='ADD', emboss=False)
            op.channel = ch_id


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


def draw_tlm_settings(layout, context):
    obj = context.active_object
    if not obj or not obj.active_material:
        return
    mat = obj.active_material
    tlm = mat.tlm

    layout.label(text="Canvas", icon='IMAGE_DATA')
    canvas = layout.column(align=True)
    canvas.prop(tlm, "resolution")
    canvas.prop(tlm, "uv_map")

    layout.separator(factor=0.8)
    layout.label(text="Composite", icon='NODE_MATERIAL')
    comp = layout.column(align=True)
    ac_icon = 'LINKED' if tlm.auto_composite else 'UNLINKED'
    comp.prop(tlm, "auto_composite", text="Auto Composite", icon=ac_icon, toggle=True)
    ops_row = comp.row(align=True)
    ops_row.operator("tlm.rebuild_composite", text="Rebuild",   icon='FILE_REFRESH')
    ops_row.operator("tlm.flatten_layers",    text="Flatten",   icon='IMAGE_ZDEPTH')
    comp.operator("tlm.refresh_thumbnails",   text="Refresh Thumbnails", icon='FILE_REFRESH')

    layout.separator(factor=0.8)
    layout.label(text="Bake & Export", icon='RENDER_STILL')
    layout.operator("tlm.bake_pbr", text="Bake PBR Maps…", icon='EXPORT')

    layout.separator(factor=0.8)
    layout.label(text="Presets", icon='PRESET_NEW')
    from .operators import BUILTIN_PRESETS
    grid = layout.column(align=True)
    grid.scale_y = 0.95
    prow = None
    for i, pname in enumerate(BUILTIN_PRESETS):
        if i % 2 == 0:
            prow = grid.row(align=True)
        op = prow.operator("tlm.apply_preset", text=pname, icon='MATERIAL')
        op.preset_name = pname
    layout.separator(factor=0.3)
    layout.operator("tlm.save_preset", text="Save Current as Preset…", icon='FILE_TICK')

    # ── User-saved presets (cached to avoid os.listdir every draw) ──────
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
    if user_presets:
        layout.separator(factor=0.3)
        layout.label(text="Saved Presets:", icon='FILE_FOLDER')
        ugrid = layout.column(align=True)
        ugrid.scale_y = 0.95
        for pname in user_presets:
            urow = ugrid.row(align=True)
            op = urow.operator("tlm.apply_preset", text=pname, icon='PRESET')
            op.preset_name = pname
            dop = urow.operator("tlm.delete_preset", text="", icon='TRASH')
            dop.preset_name = pname

    layout.separator(factor=0.8)
    layout.label(text="Layer Stack I/O", icon='FILE_FOLDER')
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


class TLM_PT_SettingsPanel(Panel):
    bl_label       = "TLM Settings"
    bl_idname      = "TLM_PT_settings_panel"
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = "material"
    bl_parent_id   = "TLM_PT_main_panel"
    bl_options     = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return (context.active_object is not None
                and context.active_object.active_material is not None)

    def draw(self, context):
        draw_tlm_settings(self.layout, context)


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


class TLM_PT_ViewportSettingsPanel(Panel):
    bl_label       = "TLM Settings"
    bl_idname      = "TLM_PT_viewport_settings_panel"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'TLM'
    bl_parent_id   = 'TLM_PT_viewport_panel'
    bl_options     = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return (context.active_object is not None
                and context.active_object.active_material is not None)

    def draw(self, context):
        draw_tlm_settings(self.layout, context)


classes = [
    TLM_UL_LayerList,
    TLM_PT_MainPanel,
    TLM_PT_SettingsPanel,
    TLM_PT_ViewportPanel,
    TLM_PT_ViewportSettingsPanel,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
