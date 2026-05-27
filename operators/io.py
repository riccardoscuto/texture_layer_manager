"""JSON import/export and clipboard operators."""

import bpy
import json
import base64
import os
import struct
import zlib
import numpy as np
from bpy.types import Operator
from ._common import _get_material, _can_edit_tlm_stack, _ensure_nodes, compositing, _normalize_blend_mode
from .pbr import CHANNEL_INFO


# â”€â”€â”€ Export / Import JSON â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _image_to_png_b64(image):
    """
    Encode a bpy.data.images image as a base64 PNG string.
    Pure Python â€” no external deps beyond numpy (already required).
    """
    if image is None:
        return None
    w, h = image.size
    if w == 0 or h == 0:
        return None

    # Read pixels as float32 RGBA
    buf = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(buf)
    buf = buf.reshape((h, w, 4))

    # Flip vertically (Blender bottom-up â†’ PNG top-down)
    buf = buf[::-1, :, :]

    # Convert to uint8
    px = (np.clip(buf, 0, 1) * 255).astype(np.uint8)

    # Build minimal PNG in memory
    def png_chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    # PNG signature
    sig = b'\x89PNG\r\n\x1a\n'

    # IHDR â€” RGBA 8-bit (color type 6)
    ihdr_data = struct.pack('>II', w, h) + bytes([8, 6, 0, 0, 0])
    ihdr = png_chunk(b'IHDR', ihdr_data)

    # IDAT â€” prepend filter byte 0 to each scanline, then compress.
    # Using numpy to build the raw byte stream is significantly faster than
    # string concatenation in a Python loop on large images.
    filter_col = np.zeros((h, 1), dtype=np.uint8)
    rows_with_filter = np.concatenate([filter_col, px.reshape(h, w * 4)], axis=1)
    compressed = zlib.compress(rows_with_filter.tobytes(), 6)  # level 6: good balance
    idat = png_chunk(b'IDAT', compressed)

    # IEND
    iend = png_chunk(b'IEND', b'')

    png_bytes = sig + ihdr + idat + iend
    return base64.b64encode(png_bytes).decode('ascii')


def _png_b64_to_image(b64_str, name, expected_w, expected_h, replace_existing=True):
    """
    Decode a base64 PNG string back into a bpy.data.images image.
    Uses Blender's built-in image loading via a temp file.
    """
    import tempfile

    png_bytes = base64.b64decode(b64_str)

    # Write to temp file and let Blender load it
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(png_bytes)
        tmp_path = f.name

    try:
        # Remove existing image with same name only for explicit import/replace
        # flows. Presets may be merged into an existing scene and must not
        # delete unrelated paint canvases that happen to share a name.
        if replace_existing and name in bpy.data.images:
            bpy.data.images.remove(bpy.data.images[name])

        try:
            img = bpy.data.images.load(tmp_path, check_existing=False)
        except TypeError:
            img = bpy.data.images.load(tmp_path)
        img.name = name
        img.pack()  # embed in .blend so temp file can be deleted
    finally:
        os.unlink(tmp_path)

    return img


def _layer_to_dict(layer):
    """Serialize a TLM_LayerItem to a plain dict."""
    d = {
        "name":              layer.name,
        "type":              layer.layer_type,
        "visible":           layer.visible,
        "locked":            layer.locked,
        "opacity":           round(layer.opacity, 4),
        "blend_mode":        layer.blend_mode,
        "group_name":        layer.group_name,
        "collapsed":         layer.collapsed,
        "use_clipping_mask": layer.use_clipping_mask,
        # Routing â€” which BSDF input this layer drives
        "output_channel":    getattr(layer, 'output_channel', 'BASE_COLOR'),
        # Branching â€” per-channel blend mode overrides
        "blend_mode_base_color":   getattr(layer, 'blend_mode_base_color',   'INHERIT'),
        "blend_mode_roughness":    getattr(layer, 'blend_mode_roughness',    'INHERIT'),
        "blend_mode_metallic":     getattr(layer, 'blend_mode_metallic',     'INHERIT'),
        "blend_mode_emission":     getattr(layer, 'blend_mode_emission',     'INHERIT'),
        "blend_mode_transmission": getattr(layer, 'blend_mode_transmission', 'INHERIT'),
        "blend_mode_alpha":        getattr(layer, 'blend_mode_alpha',        'INHERIT'),
        "alpha_math_operation":    getattr(layer, 'alpha_math_operation',    'MULTIPLY'),
    }

    if layer.layer_type == "PAINT":
        d["image_name"] = layer.image_name
        d["image_data"] = _image_to_png_b64(layer.image)

    elif layer.layer_type == "FILL":
        d["fill_color"] = list(layer.fill_color)

    elif layer.layer_type == "PROCEDURAL":
        d["proc_type"]             = layer.proc_type
        d["proc_scale"]            = round(layer.proc_scale, 4)
        d["proc_color1"]           = list(layer.proc_color1)
        d["proc_color2"]           = list(layer.proc_color2)
        d["proc_detail"]           = round(layer.proc_detail, 4)
        d["proc_roughness_proc"]   = round(layer.proc_roughness_proc, 4)
        d["proc_distortion"]       = round(layer.proc_distortion, 4)
        d["proc_magic_distortion"] = round(getattr(layer, 'proc_magic_distortion', 1.0), 4)
        d["proc_magic_depth"]      = getattr(layer, 'proc_magic_depth', 2)
        d["proc_stripe_direction"] = getattr(layer, 'proc_stripe_direction', 'Y')
        d["proc_stripe_width"]     = round(getattr(layer, 'proc_stripe_width', 0.5), 4)
        d["proc_stripe_sharpness"] = round(getattr(layer, 'proc_stripe_sharpness', 1.0), 4)
        d["proc_hex_edge_width"]   = round(getattr(layer, 'proc_hex_edge_width', 0.05), 4)
        d["proc_brick_offset"]     = round(getattr(layer, 'proc_brick_offset', 0.5), 4)
        d["proc_brick_offset_freq"]= getattr(layer, 'proc_brick_offset_freq', 2)
        d["proc_brick_squash"]     = round(getattr(layer, 'proc_brick_squash', 1.0), 4)
        d["proc_brick_squash_freq"]= getattr(layer, 'proc_brick_squash_freq', 2)
        d["proc_brick_mortar_size"]= round(getattr(layer, 'proc_brick_mortar_size', 0.02), 4)
        d["proc_brick_mortar_smooth"] = round(getattr(layer, 'proc_brick_mortar_smooth', 0.1), 4)
        d["proc_brick_bias"]       = round(getattr(layer, 'proc_brick_bias', 0.0), 4)
        d["proc_brick_width"]      = round(getattr(layer, 'proc_brick_width', 0.5), 4)
        d["proc_brick_row_height"] = round(getattr(layer, 'proc_brick_row_height', 0.25), 4)
        d["proc_dots_radius"]      = round(getattr(layer, 'proc_dots_radius', 0.35), 4)
        d["proc_dots_softness"]    = round(getattr(layer, 'proc_dots_softness', 0.15), 4)
        d["proc_cracks_width"]     = round(getattr(layer, 'proc_cracks_width', 0.05), 4)
        d["proc_cracks_sharpness"] = round(getattr(layer, 'proc_cracks_sharpness', 0.7), 4)
        d["proc_ridged_offset"]    = round(getattr(layer, 'proc_ridged_offset', 1.0), 4)
        d["proc_ridged_gain"]      = round(getattr(layer, 'proc_ridged_gain', 2.0), 4)
        d["proc_gabor_anisotropy"] = round(getattr(layer, 'proc_gabor_anisotropy', 1.0), 4)
        d["proc_gabor_orientation"]= round(getattr(layer, 'proc_gabor_orientation', 45.0), 4)
        d["proc_gabor_frequency"]  = round(getattr(layer, 'proc_gabor_frequency', 2.0), 4)
        d["proc_lacunarity"]       = round(layer.proc_lacunarity, 4)
        d["proc_offset_x"]         = round(layer.proc_offset_x, 4)
        d["proc_offset_y"]         = round(layer.proc_offset_y, 4)
        d["proc_offset_z"]         = round(layer.proc_offset_z, 4)
        d["proc_rotation_x"]       = round(getattr(layer, 'proc_rotation_x', 0.0), 4)
        d["proc_rotation_y"]       = round(getattr(layer, 'proc_rotation_y', 0.0), 4)
        d["proc_rotation_z"]       = round(getattr(layer, 'proc_rotation_z', 0.0), 4)
        d["proc_mapping_scale_x"]  = round(getattr(layer, 'proc_mapping_scale_x', 1.0), 4)
        d["proc_mapping_scale_y"]  = round(getattr(layer, 'proc_mapping_scale_y', 1.0), 4)
        d["proc_mapping_scale_z"]  = round(getattr(layer, 'proc_mapping_scale_z', 1.0), 4)
        d["proc_mapping_type"]     = getattr(layer, 'proc_mapping_type', 'POINT')
        d["proc_voronoi_feature"]  = layer.proc_voronoi_feature
        d["proc_voronoi_distance"] = layer.proc_voronoi_distance
        d["proc_randomness"]       = round(layer.proc_randomness, 4)
        d["proc_wave_type"]        = layer.proc_wave_type
        d["proc_wave_profile"]     = layer.proc_wave_profile
        d["proc_wave_bands_direction"] = getattr(layer, 'proc_wave_bands_direction', 'X')
        d["proc_wave_rings_direction"] = getattr(layer, 'proc_wave_rings_direction', 'X')
        d["proc_wave_detail_scale"]= round(layer.proc_wave_detail_scale, 4)
        d["proc_wave_detail_roughness"] = round(getattr(layer, 'proc_wave_detail_roughness', 0.5), 4)
        d["proc_wave_phase_offset"]= round(getattr(layer, 'proc_wave_phase_offset', 0.0), 4)
        d["proc_gradient_type"]    = layer.proc_gradient_type
        d["proc_contrast"]         = round(layer.proc_contrast, 4)
        d["proc_ramp_center"]      = round(getattr(layer, 'proc_ramp_center', 0.5), 4)
        d["proc_vector_distortion"]= round(layer.proc_vector_distortion, 4)
        d["proc_coord_type"]       = layer.proc_coord_type
        d["proc_marble_distortion"]= round(layer.proc_marble_distortion, 4)
        d["proc_marble_wave_type"] = layer.proc_marble_wave_type
        d["proc_marble_wave_profile"] = getattr(layer, 'proc_marble_wave_profile', 'SIN')
        d["proc_marble_bands_direction"] = getattr(layer, 'proc_marble_bands_direction', 'X')
        d["proc_marble_rings_direction"] = getattr(layer, 'proc_marble_rings_direction', 'X')
        d["proc_emission_threshold"] = round(getattr(layer, 'proc_emission_threshold', 0.0), 4)
        d["use_proc_color3"]       = getattr(layer, 'use_proc_color3', False)
        if d["use_proc_color3"]:
            d["proc_color3"]          = list(layer.proc_color3)
            d["proc_color3_position"] = round(layer.proc_color3_position, 4)
        # ColorRamp controls (manual stops + mode + interpolation)
        d["proc_use_manual_stops"]      = getattr(layer, 'proc_use_manual_stops', False)
        d["proc_color1_position"]       = round(getattr(layer, 'proc_color1_position', 0.0), 4)
        d["proc_color2_position"]       = round(getattr(layer, 'proc_color2_position', 1.0), 4)
        d["proc_color_ramp_mode"]       = getattr(layer, 'proc_color_ramp_mode', 'RGB')
        d["proc_color_ramp_interpolation"] = getattr(
            layer, 'proc_color_ramp_interpolation', 'LINEAR'
        )
        # Extra colour stops (collection of {color, position})
        d["proc_extra_color_stops"] = [
            {"color": list(s.color), "position": round(s.position, 4)}
            for s in getattr(layer, 'proc_extra_color_stops', [])
        ]
        # Feature A â€” Advanced coordinates (POLAR / SPHERICAL / SWIRL / CYLINDRICAL)
        d["proc_coord_transform"]  = getattr(layer, 'proc_coord_transform', 'NONE')
        d["proc_swirl_amount"]     = round(getattr(layer, 'proc_swirl_amount', 2.0), 4)
        # Feature B â€” Voronoi random per cell
        d["proc_voronoi_random_color"] = getattr(layer, 'proc_voronoi_random_color', False)
        d["proc_voronoi_random_seed"]  = round(getattr(layer, 'proc_voronoi_random_seed', 0.0), 4)

    elif layer.layer_type == "REFERENCE":
        # Reference layers reuse another layer's pattern â€” only the source name
        # is distinctive; everything else is in the common mask/PBR sections.
        d["reference_layer_name"] = getattr(layer, 'reference_layer_name', "")

    elif layer.layer_type == "ADJUSTMENT":
        d["adj_type"]        = layer.adj_type
        d["adj_hue"]         = round(layer.adj_hue, 4)
        d["adj_saturation"]  = round(layer.adj_saturation, 4)
        d["adj_value"]       = round(layer.adj_value, 4)
        d["adj_brightness"]  = round(layer.adj_brightness, 4)
        d["adj_contrast"]    = round(layer.adj_contrast, 4)
        d["adj_in_min"]      = round(layer.adj_in_min, 4)
        d["adj_in_max"]      = round(layer.adj_in_max, 4)
        d["adj_levels_gamma"] = round(layer.adj_levels_gamma, 4)
        d["adj_out_min"]     = round(layer.adj_out_min, 4)
        d["adj_out_max"]     = round(layer.adj_out_max, 4)
        d["adj_lift"]  = list(layer.adj_lift)
        d["adj_gamma"] = list(layer.adj_gamma)
        d["adj_gain"]  = list(layer.adj_gain)

    # Common properties for non-GROUP, non-ADJUSTMENT layers
    if layer.layer_type not in ("GROUP", "ADJUSTMENT"):
        d["use_fresnel_mask"]  = getattr(layer, 'use_fresnel_mask', False)
        d["fresnel_ior"]       = round(getattr(layer, 'fresnel_ior', 1.45), 4)
        d["fresnel_strength"]  = round(getattr(layer, 'fresnel_strength', 1.0), 4)
        d["use_mask"]          = layer.use_mask
        d["mask_image_name"]   = layer.mask_image_name
        # Feature C â€” Advanced combinable masks
        d["mask_source"]        = getattr(layer, 'mask_source', 'IMAGE')
        d["mask_invert"]        = getattr(layer, 'mask_invert', False)
        d["mask_ao_distance"]   = round(getattr(layer, 'mask_ao_distance', 0.5), 4)
        d["mask_wireframe_size"] = round(getattr(layer, 'mask_wireframe_size', 0.01), 4)
        d["mask_wireframe_use_pixel_size"] = getattr(layer, 'mask_wireframe_use_pixel_size', True)
        d["mask_voronoi_feature"]    = getattr(layer, 'mask_voronoi_feature', 'DISTANCE_TO_EDGE')
        d["mask_voronoi_scale"]      = round(getattr(layer, 'mask_voronoi_scale', 10.0), 4)
        d["mask_voronoi_randomness"] = round(getattr(layer, 'mask_voronoi_randomness', 1.0), 4)
        d["mask_voronoi_edge_width"] = round(getattr(layer, 'mask_voronoi_edge_width', 1.0), 4)
        d["use_mask_b"]         = getattr(layer, 'use_mask_b', False)
        d["mask_source_b"]      = getattr(layer, 'mask_source_b', 'POINTINESS')
        d["mask_image_name_b"]  = getattr(layer, 'mask_image_name_b', "")
        d["mask_invert_b"]      = getattr(layer, 'mask_invert_b', False)
        d["mask_ao_distance_b"] = round(getattr(layer, 'mask_ao_distance_b', 0.5), 4)
        d["mask_voronoi_feature_b"]    = getattr(layer, 'mask_voronoi_feature_b', 'DISTANCE_TO_EDGE')
        d["mask_voronoi_scale_b"]      = round(getattr(layer, 'mask_voronoi_scale_b', 10.0), 4)
        d["mask_voronoi_randomness_b"] = round(getattr(layer, 'mask_voronoi_randomness_b', 1.0), 4)
        d["mask_voronoi_edge_width_b"] = round(getattr(layer, 'mask_voronoi_edge_width_b', 1.0), 4)
        d["mask_combine"]       = getattr(layer, 'mask_combine', 'MULTIPLY')
        d["mask_contrast"]      = round(getattr(layer, 'mask_contrast', 0.5), 4)
        # Mask refinement â€” Levels (input range + gamma + output range)
        d["use_mask_levels"]     = getattr(layer, 'use_mask_levels', False)
        d["mask_levels_in_min"]  = round(getattr(layer, 'mask_levels_in_min', 0.0), 4)
        d["mask_levels_in_max"]  = round(getattr(layer, 'mask_levels_in_max', 1.0), 4)
        d["mask_levels_gamma"]   = round(getattr(layer, 'mask_levels_gamma', 1.0), 4)
        d["mask_levels_out_min"] = round(getattr(layer, 'mask_levels_out_min', 0.0), 4)
        d["mask_levels_out_max"] = round(getattr(layer, 'mask_levels_out_max', 1.0), 4)
        # Mask refinement â€” Softness + Blur
        d["mask_softness"]       = round(getattr(layer, 'mask_softness', 0.0), 4)
        d["mask_blur"]           = round(getattr(layer, 'mask_blur', 0.0), 4)
        # Smart-generator parameters (shared across EDGE_WEAR/DIRT/CURVATURE_SMART)
        d["mask_gen_intensity"]      = round(getattr(layer, 'mask_gen_intensity', 1.0), 4)
        d["mask_gen_breakup"]        = round(getattr(layer, 'mask_gen_breakup', 0.3), 4)
        d["mask_gen_breakup_scale"]  = round(getattr(layer, 'mask_gen_breakup_scale', 15.0), 4)
        d["mask_gen_sharpness"]      = round(getattr(layer, 'mask_gen_sharpness', 0.5), 4)
        # Image texture mapping config (Source / Interpolation /
        # Projection / Extension + Box blend). Triplanar used to live
        # here as a custom feature â€” now replaced by paint_projection
        # = 'BOX' which uses Blender's native triplanar.
        d["paint_interpolation"]    = getattr(layer, 'paint_interpolation', 'Linear')
        d["paint_projection"]       = getattr(layer, 'paint_projection', 'FLAT')
        d["paint_projection_blend"] = round(getattr(layer, 'paint_projection_blend', 0.3), 4)
        d["paint_source"]           = getattr(layer, 'paint_source', 'FILE')
        # PBR channels
        d["use_roughness"]     = layer.use_roughness
        d["roughness_fill"]    = round(layer.roughness_fill, 4)
        d["roughness_image_name"] = getattr(layer, 'roughness_image_name', "")
        d["use_metallic"]      = layer.use_metallic
        d["metallic_fill"]     = round(layer.metallic_fill, 4)
        d["metallic_image_name"]  = getattr(layer, 'metallic_image_name', "")
        d["use_bump"]          = layer.use_bump
        d["bump_strength"]     = round(layer.bump_strength, 4)
        d["bump_distance"]     = round(layer.bump_distance, 4)
        d["use_displacement"]  = getattr(layer, 'use_displacement', False)
        d["displacement_scale"] = round(getattr(layer, 'displacement_scale', 1.0), 4)
        d["use_normal"]        = getattr(layer, 'use_normal', False)
        d["normal_image_name"]    = getattr(layer, 'normal_image_name', "")
        d["normal_strength"]   = round(getattr(layer, 'normal_strength', 1.0), 4)
        d["normal_tile_scale"] = round(getattr(layer, 'normal_tile_scale', 1.0), 4)
        d["normal_rotation"]   = round(getattr(layer, 'normal_rotation', 0.0), 4)
        d["use_emission"]      = getattr(layer, 'use_emission', False)
        if layer.use_emission:
            d["emission_color"]    = list(layer.emission_color)
            d["emission_strength"] = round(layer.emission_strength, 4)
        d["emission_image_name"]  = getattr(layer, 'emission_image_name', "")
        # Selective emission (gates procedural emission to a subset of regions)
        d["emission_selector_type"]       = getattr(layer, 'emission_selector_type', 'NONE')
        d["emission_selector_scale"]      = round(getattr(layer, 'emission_selector_scale', 4.0), 4)
        d["emission_selector_threshold"]  = round(getattr(layer, 'emission_selector_threshold', 0.3), 4)
        d["emission_selector_seed"]       = round(getattr(layer, 'emission_selector_seed', 0.0), 4)
        d["emission_selector_image_name"] = getattr(layer, 'emission_selector_image_name', "")
        d["use_transmission"]  = getattr(layer, 'use_transmission', False)
        d["transmission_fill"] = round(getattr(layer, 'transmission_fill', 0.0), 4)
        d["transmission_image_name"] = getattr(layer, 'transmission_image_name', "")
        # Alpha is a routable channel AND can also be enabled via use_alpha
        # on top of any other routing target (cumulative). Both paths need
        # the same triplet of properties saved/restored, otherwise a layer
        # like 'output=BASE_COLOR + use_alpha=True' would lose its alpha
        # config on export/import.
        d["use_alpha"]         = getattr(layer, 'use_alpha', False)
        d["alpha_fill"]        = round(getattr(layer, 'alpha_fill', 1.0), 4)
        d["alpha_image_name"]  = getattr(layer, 'alpha_image_name', "")

    return d


def _dict_to_layer(d, tlm):
    """Deserialize a dict into a new TLM_LayerItem appended to tlm.layers."""
    layer = tlm.layers.add()
    layer.name       = d.get("name", "Layer")
    layer.layer_type = d.get("type", "PAINT")
    layer.visible    = d.get("visible", True)
    layer.locked     = d.get("locked", False)
    layer.opacity    = d.get("opacity", 1.0)
    # Use the shared blend-mode normaliser so legacy .tlm files saved
    # in TLM â‰¤ 0.3 (title-case names like "Screen") don't silently
    # collapse to MIX. operators/presets.py also goes through this.
    layer.blend_mode = _normalize_blend_mode(d.get("blend_mode"))
    # GROUP layers are always root-level â€” discard any stray parent to
    # block nested-group states from arriving via external files.
    _raw_group = d.get("group_name", "")
    layer.group_name = "" if layer.layer_type == "GROUP" else _raw_group
    layer.collapsed         = d.get("collapsed", False)
    layer.use_clipping_mask = d.get("use_clipping_mask", False)
    # Routing â€” default BASE_COLOR keeps pre-routing presets working.
    # Legacy 'AUTO' (older builds) falls through to BASE_COLOR via the
    # alias in _layer_contributes_to.
    _out_ch = d.get("output_channel", "BASE_COLOR")
    if _out_ch == "AUTO":
        _out_ch = "BASE_COLOR"
    try:
        layer.output_channel = _out_ch
    except (TypeError, ValueError):
        layer.output_channel = "BASE_COLOR"
    # Branching â€” per-channel blend mode overrides (INHERIT default = backward-compat)
    layer.blend_mode_base_color   = d.get("blend_mode_base_color",   "INHERIT")
    layer.blend_mode_roughness    = d.get("blend_mode_roughness",    "INHERIT")
    layer.blend_mode_metallic     = d.get("blend_mode_metallic",     "INHERIT")
    layer.blend_mode_emission     = d.get("blend_mode_emission",     "INHERIT")
    layer.blend_mode_transmission = d.get("blend_mode_transmission", "INHERIT")
    layer.blend_mode_alpha        = d.get("blend_mode_alpha",        "INHERIT")
    try:
        layer.alpha_math_operation = d.get("alpha_math_operation", "MULTIPLY")
    except (TypeError, ValueError):
        layer.alpha_math_operation = "MULTIPLY"

    if layer.layer_type == "PAINT":
        img_name = d.get("image_name", layer.name)
        img_data = d.get("image_data")
        if img_data:
            img = _png_b64_to_image(img_data, img_name, 0, 0)
            layer.image_name = img.name
        else:
            # No pixel data â€” create blank image
            res = int(tlm.resolution)
            img = bpy.data.images.new(img_name, width=res, height=res, alpha=True)
            img.pixels[:] = [0.0] * (res * res * 4)
            layer.image_name = img.name

    elif layer.layer_type == "FILL":
        fc = d.get("fill_color", [1, 1, 1, 1])
        layer.fill_color = fc

    elif layer.layer_type == "PROCEDURAL":
        # Back-compat migrations for removed proc_types/props:
        # â€¢ CLOUDS was merged into NOISE (identical underlying node).
        # â€¢ proc_checker_scale was merged into the shared proc_scale.
        _proc_type_raw = d.get("proc_type", "NOISE")
        if _proc_type_raw == "CLOUDS":
            _proc_type_raw = "NOISE"
        layer.proc_type             = _proc_type_raw
        # If the preset had a distinct checker scale, honor it on CHECKER layers.
        if _proc_type_raw == "CHECKER" and "proc_checker_scale" in d:
            layer.proc_scale        = d["proc_checker_scale"]
        else:
            layer.proc_scale        = d.get("proc_scale", 5.0)
        layer.proc_color1           = d.get("proc_color1", [0,0,0,1])
        layer.proc_color2           = d.get("proc_color2", [1,1,1,1])
        layer.proc_detail           = d.get("proc_detail", 2.0)
        layer.proc_roughness_proc   = d.get("proc_roughness_proc", 0.5)
        layer.proc_distortion       = d.get("proc_distortion", 0.0)
        layer.proc_magic_distortion = d.get("proc_magic_distortion", 1.0)
        layer.proc_magic_depth      = d.get("proc_magic_depth", 2)
        layer.proc_stripe_direction = d.get("proc_stripe_direction", "Y")
        layer.proc_stripe_width     = d.get("proc_stripe_width", 0.5)
        layer.proc_stripe_sharpness = d.get("proc_stripe_sharpness", 1.0)
        layer.proc_hex_edge_width   = d.get("proc_hex_edge_width", 0.05)
        layer.proc_brick_offset     = d.get("proc_brick_offset", 0.5)
        layer.proc_brick_offset_freq= d.get("proc_brick_offset_freq", 2)
        layer.proc_brick_squash     = d.get("proc_brick_squash", 1.0)
        layer.proc_brick_squash_freq= d.get("proc_brick_squash_freq", 2)
        layer.proc_brick_mortar_size= d.get("proc_brick_mortar_size", 0.02)
        layer.proc_brick_mortar_smooth = d.get("proc_brick_mortar_smooth", 0.1)
        layer.proc_brick_bias       = d.get("proc_brick_bias", 0.0)
        layer.proc_brick_width      = d.get("proc_brick_width", 0.5)
        layer.proc_brick_row_height = d.get("proc_brick_row_height", 0.25)
        layer.proc_dots_radius      = d.get("proc_dots_radius", 0.35)
        layer.proc_dots_softness    = d.get("proc_dots_softness", 0.15)
        layer.proc_cracks_width     = d.get("proc_cracks_width", 0.05)
        layer.proc_cracks_sharpness = d.get("proc_cracks_sharpness", 0.7)
        layer.proc_ridged_offset    = d.get("proc_ridged_offset", 1.0)
        layer.proc_ridged_gain      = d.get("proc_ridged_gain", 2.0)
        layer.proc_gabor_anisotropy = d.get("proc_gabor_anisotropy", 1.0)
        layer.proc_gabor_orientation= d.get("proc_gabor_orientation", 45.0)
        layer.proc_gabor_frequency  = d.get("proc_gabor_frequency", 2.0)
        layer.proc_lacunarity       = d.get("proc_lacunarity", 2.0)
        layer.proc_offset_x         = d.get("proc_offset_x", 0.0)
        layer.proc_offset_y         = d.get("proc_offset_y", 0.0)
        layer.proc_offset_z         = d.get("proc_offset_z", 0.0)
        layer.proc_rotation_x       = d.get("proc_rotation_x", 0.0)
        layer.proc_rotation_y       = d.get("proc_rotation_y", 0.0)
        layer.proc_rotation_z       = d.get("proc_rotation_z", 0.0)
        layer.proc_mapping_scale_x  = d.get("proc_mapping_scale_x", 1.0)
        layer.proc_mapping_scale_y  = d.get("proc_mapping_scale_y", 1.0)
        layer.proc_mapping_scale_z  = d.get("proc_mapping_scale_z", 1.0)
        layer.proc_mapping_type     = d.get("proc_mapping_type", "POINT")
        layer.proc_voronoi_feature  = d.get("proc_voronoi_feature", "F1")
        layer.proc_voronoi_distance = d.get("proc_voronoi_distance", "EUCLIDEAN")
        layer.proc_randomness       = d.get("proc_randomness", 1.0)
        layer.proc_wave_type        = d.get("proc_wave_type", "BANDS")
        layer.proc_wave_profile     = d.get("proc_wave_profile", "SIN")
        layer.proc_wave_bands_direction = d.get("proc_wave_bands_direction", "X")
        layer.proc_wave_rings_direction = d.get("proc_wave_rings_direction", "X")
        layer.proc_wave_detail_scale= d.get("proc_wave_detail_scale", 1.0)
        layer.proc_wave_detail_roughness = d.get("proc_wave_detail_roughness", 0.5)
        layer.proc_wave_phase_offset= d.get("proc_wave_phase_offset", 0.0)
        layer.proc_gradient_type    = d.get("proc_gradient_type", "LINEAR")
        layer.proc_contrast         = d.get("proc_contrast", 0.5)
        layer.proc_ramp_center      = d.get("proc_ramp_center", 0.5)
        layer.proc_vector_distortion= d.get("proc_vector_distortion", 0.0)
        layer.proc_coord_type       = d.get("proc_coord_type", "GENERATED")
        layer.proc_marble_distortion= d.get("proc_marble_distortion", 5.0)
        layer.proc_marble_wave_type = d.get("proc_marble_wave_type", "BANDS")
        layer.proc_marble_wave_profile = d.get("proc_marble_wave_profile", "SIN")
        layer.proc_marble_bands_direction = d.get("proc_marble_bands_direction", "X")
        layer.proc_marble_rings_direction = d.get("proc_marble_rings_direction", "X")
        layer.proc_emission_threshold = d.get("proc_emission_threshold", 0.0)
        layer.use_proc_color3       = d.get("use_proc_color3", False)
        if layer.use_proc_color3:
            layer.proc_color3          = d.get("proc_color3", [0.5, 0.5, 0.5, 1])
            layer.proc_color3_position = d.get("proc_color3_position", 0.5)
        # ColorRamp controls (manual stops + mode + interpolation)
        layer.proc_use_manual_stops      = d.get("proc_use_manual_stops", False)
        layer.proc_color1_position       = d.get("proc_color1_position", 0.0)
        layer.proc_color2_position       = d.get("proc_color2_position", 1.0)
        layer.proc_color_ramp_mode       = d.get("proc_color_ramp_mode", "RGB")
        layer.proc_color_ramp_interpolation = d.get(
            "proc_color_ramp_interpolation", "LINEAR"
        )
        # Extra colour stops — clear then re-populate
        layer.proc_extra_color_stops.clear()
        for s in d.get("proc_extra_color_stops", []):
            item = layer.proc_extra_color_stops.add()
            item.color = s.get("color", [0.5, 0.5, 0.5, 1.0])
            item.position = s.get("position", 0.5)
        # Legacy migration: pre-collection presets stored a 3rd stop on
        # use_proc_color3 + proc_color3 + proc_color3_position. When such
        # a preset comes in AND the new collection is still empty, fold
        # Color 3 into the collection so the UI shows a unified list.
        if layer.use_proc_color3 and not layer.proc_extra_color_stops:
            item = layer.proc_extra_color_stops.add()
            item.color = list(layer.proc_color3)
            item.position = layer.proc_color3_position
            layer.use_proc_color3 = False
        # Feature A â€” Advanced coordinates
        layer.proc_coord_transform  = d.get("proc_coord_transform", "NONE")
        layer.proc_swirl_amount     = d.get("proc_swirl_amount", 2.0)
        # Feature B â€” Voronoi random per cell
        layer.proc_voronoi_random_color = d.get("proc_voronoi_random_color", False)
        layer.proc_voronoi_random_seed  = d.get("proc_voronoi_random_seed", 0.0)

    elif layer.layer_type == "REFERENCE":
        layer.reference_layer_name = d.get("reference_layer_name", "")

    elif layer.layer_type == "ADJUSTMENT":
        # CURVES was removed in favour of LEVELS+BRIGHT_CONTRAST â€” remap
        # legacy presets so they still load without an enum error.
        adj_t = d.get("adj_type", "HUE_SAT")
        if adj_t == "CURVES":
            adj_t = "BRIGHT_CONTRAST"
        layer.adj_type       = adj_t
        layer.adj_hue        = d.get("adj_hue", 0.5)
        layer.adj_saturation = d.get("adj_saturation", 1.0)
        layer.adj_value      = d.get("adj_value", 1.0)
        layer.adj_brightness = d.get("adj_brightness", 0.0)
        layer.adj_contrast   = d.get("adj_contrast", 0.0)
        layer.adj_in_min     = d.get("adj_in_min", 0.0)
        layer.adj_in_max     = d.get("adj_in_max", 1.0)
        # Support old key "adj_gamma" for backwards compat with files saved before this fix
        layer.adj_levels_gamma = d.get("adj_levels_gamma", d.get("adj_gamma", 1.0))
        layer.adj_out_min    = d.get("adj_out_min", 0.0)
        layer.adj_out_max    = d.get("adj_out_max", 1.0)
        layer.adj_lift  = d.get("adj_lift", [1, 1, 1])
        layer.adj_gamma = d.get("adj_gamma", [1, 1, 1])
        layer.adj_gain  = d.get("adj_gain", [1, 1, 1])

    # Common properties for non-GROUP, non-ADJUSTMENT layers
    if layer.layer_type not in ("GROUP", "ADJUSTMENT"):
        layer.use_fresnel_mask  = d.get("use_fresnel_mask", False)
        layer.fresnel_ior       = d.get("fresnel_ior", 1.45)
        layer.fresnel_strength  = d.get("fresnel_strength", 1.0)
        layer.use_mask          = d.get("use_mask", False)
        layer.mask_image_name   = d.get("mask_image_name", "")
        # Feature C â€” Advanced combinable masks
        layer.mask_source        = d.get("mask_source", "IMAGE")
        layer.mask_invert        = d.get("mask_invert", False)
        layer.mask_ao_distance   = d.get("mask_ao_distance", 0.5)
        layer.mask_wireframe_size = d.get("mask_wireframe_size", 0.01)
        layer.mask_wireframe_use_pixel_size = d.get("mask_wireframe_use_pixel_size", True)
        try:
            layer.mask_voronoi_feature    = d.get("mask_voronoi_feature", 'DISTANCE_TO_EDGE')
        except (TypeError, ValueError):
            pass
        layer.mask_voronoi_scale      = d.get("mask_voronoi_scale", 10.0)
        layer.mask_voronoi_randomness = d.get("mask_voronoi_randomness", 1.0)
        layer.mask_voronoi_edge_width = d.get("mask_voronoi_edge_width", 1.0)
        layer.use_mask_b         = d.get("use_mask_b", False)
        layer.mask_source_b      = d.get("mask_source_b", "POINTINESS")
        layer.mask_image_name_b  = d.get("mask_image_name_b", "")
        layer.mask_invert_b      = d.get("mask_invert_b", False)
        layer.mask_ao_distance_b = d.get("mask_ao_distance_b", 0.5)
        try:
            layer.mask_voronoi_feature_b    = d.get("mask_voronoi_feature_b", 'DISTANCE_TO_EDGE')
        except (TypeError, ValueError):
            pass
        layer.mask_voronoi_scale_b      = d.get("mask_voronoi_scale_b", 10.0)
        layer.mask_voronoi_randomness_b = d.get("mask_voronoi_randomness_b", 1.0)
        layer.mask_voronoi_edge_width_b = d.get("mask_voronoi_edge_width_b", 1.0)
        layer.mask_combine       = d.get("mask_combine", "MULTIPLY")
        layer.mask_contrast      = d.get("mask_contrast", 0.5)
        # Mask refinement â€” Levels
        layer.use_mask_levels     = d.get("use_mask_levels", False)
        layer.mask_levels_in_min  = d.get("mask_levels_in_min", 0.0)
        layer.mask_levels_in_max  = d.get("mask_levels_in_max", 1.0)
        layer.mask_levels_gamma   = d.get("mask_levels_gamma", 1.0)
        layer.mask_levels_out_min = d.get("mask_levels_out_min", 0.0)
        layer.mask_levels_out_max = d.get("mask_levels_out_max", 1.0)
        # Mask refinement â€” Softness + Blur
        layer.mask_softness       = d.get("mask_softness", 0.0)
        layer.mask_blur           = d.get("mask_blur", 0.0)
        # Smart generator parameters
        layer.mask_gen_intensity     = d.get("mask_gen_intensity", 1.0)
        layer.mask_gen_breakup       = d.get("mask_gen_breakup", 0.3)
        layer.mask_gen_breakup_scale = d.get("mask_gen_breakup_scale", 15.0)
        layer.mask_gen_sharpness     = d.get("mask_gen_sharpness", 0.5)
        # Image texture mapping config â€” Triplanar removed in favour
        # of paint_projection='BOX'. Legacy files that still carry
        # use_triplanar=True are auto-migrated below.
        layer.paint_interpolation    = d.get("paint_interpolation", "Linear")
        legacy_triplanar = d.get("use_triplanar", False)
        layer.paint_projection       = d.get(
            "paint_projection", "BOX" if legacy_triplanar else "FLAT"
        )
        layer.paint_projection_blend = d.get("paint_projection_blend", 0.3)
        layer.paint_source           = d.get("paint_source", "FILE")
        # PBR channels
        layer.use_roughness        = d.get("use_roughness", False)
        layer.roughness_fill       = d.get("roughness_fill", 0.5)
        layer.roughness_image_name = d.get("roughness_image_name", "")
        layer.use_metallic         = d.get("use_metallic", False)
        layer.metallic_fill        = d.get("metallic_fill", 0.0)
        layer.metallic_image_name  = d.get("metallic_image_name", "")
        layer.use_bump             = d.get("use_bump", False)
        layer.bump_strength        = d.get("bump_strength", 0.5)
        layer.bump_distance        = d.get("bump_distance", 0.05)
        layer.use_displacement     = d.get("use_displacement", False)
        layer.displacement_scale   = d.get("displacement_scale", 1.0)
        layer.use_normal           = d.get("use_normal", False)
        layer.normal_image_name    = d.get("normal_image_name", "")
        layer.normal_strength      = d.get("normal_strength", 1.0)
        layer.normal_tile_scale    = d.get("normal_tile_scale", 1.0)
        layer.normal_rotation      = d.get("normal_rotation", 0.0)
        layer.use_emission         = d.get("use_emission", False)
        layer.emission_image_name  = d.get("emission_image_name", "")
        if layer.use_emission:
            layer.emission_color    = d.get("emission_color", [1,1,1,1])
            layer.emission_strength = d.get("emission_strength", 1.0)
        # Selective emission
        layer.emission_selector_type       = d.get("emission_selector_type", "NONE")
        layer.emission_selector_scale      = d.get("emission_selector_scale", 4.0)
        layer.emission_selector_threshold  = d.get("emission_selector_threshold", 0.3)
        layer.emission_selector_seed       = d.get("emission_selector_seed", 0.0)
        layer.emission_selector_image_name = d.get("emission_selector_image_name", "")
        layer.use_transmission        = d.get("use_transmission", False)
        layer.transmission_fill       = d.get("transmission_fill", 0.0)
        layer.transmission_image_name = d.get("transmission_image_name", "")
        layer.use_alpha               = d.get("use_alpha", False)
        layer.alpha_fill              = d.get("alpha_fill", 1.0)
        layer.alpha_image_name        = d.get("alpha_image_name", "")

    return layer


class TLM_OT_ExportJSON(Operator):
    """Export the current layer stack to a .tlm JSON file."""
    bl_idname = "tlm.export_json"
    bl_label = "Export Layer Stack"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH', default="layer_stack.tlm")
    filter_glob: bpy.props.StringProperty(default="*.tlm", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def invoke(self, context, event):
        mat = _get_material(context)
        self.filepath = f"{mat.name}_layers.tlm"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm

        data = {
            "tlm_version": "0.4.0",
            "material":    mat.name,
            "resolution":  tlm.resolution,
            "uv_map":      tlm.uv_map,
            # Material-level physical / channel settings — without these
            # an exported .tlm round-trip loses IOR + Volume Abs/Scatter
            # + emission output toggle, breaking ice / gem / anime presets.
            "material_props": {
                "bsdf_ior":                  getattr(tlm, 'bsdf_ior', 1.45),
                "use_volume_absorption":     getattr(tlm, 'use_volume_absorption', False),
                "volume_absorption_color":   list(getattr(tlm, 'volume_absorption_color',
                                                          (0.55, 0.75, 0.95, 1.0))),
                "volume_absorption_density": getattr(tlm, 'volume_absorption_density', 1.0),
                "use_volume_scatter":        getattr(tlm, 'use_volume_scatter', False),
                "volume_scatter_color":      list(getattr(tlm, 'volume_scatter_color',
                                                          (0.92, 0.96, 1.0, 1.0))),
                "volume_scatter_density":    getattr(tlm, 'volume_scatter_density', 0.5),
                "volume_scatter_anisotropy": getattr(tlm, 'volume_scatter_anisotropy', 0.0),
                "use_emission_output":       getattr(tlm, 'use_emission_output', False),
                "use_base_color_alpha":      getattr(tlm, 'use_base_color_alpha', False),
                "alpha_blend_method":        getattr(tlm, 'alpha_blend_method', 'AUTO'),
                # Real geometric Displacement — material-level master switch
                # + Cycles Displacement node Scale/Midlevel. Without these,
                # a rocky/brick preset round-trip loses the chunky silhouette.
                "use_displacement":          getattr(tlm, 'use_displacement', False),
                "displacement_strength":     getattr(tlm, 'displacement_strength', 0.1),
                "displacement_midlevel":     getattr(tlm, 'displacement_midlevel', 0.5),
                "displacement_adaptive":     getattr(tlm, 'displacement_adaptive', True),
            },
            "layers":      [_layer_to_dict(l) for l in tlm.layers],
        }

        filepath = bpy.path.abspath(self.filepath)
        if not filepath.endswith('.tlm'):
            filepath += '.tlm'

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.report({'INFO'}, f"Exported {len(tlm.layers)} layers to {os.path.basename(filepath)}")
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


class TLM_OT_ImportJSON(Operator):
    """Import a .tlm JSON file and rebuild the layer stack."""
    bl_idname = "tlm.import_json"
    bl_label = "Import Layer Stack"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.tlm", options={'HIDDEN'})

    merge: bpy.props.BoolProperty(
        name="Merge with existing",
        description="Add imported layers on top of the current stack (unchecked = replace)",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return _can_edit_tlm_stack(context)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        mat = _get_material(context)
        _ensure_nodes(mat)
        tlm = mat.tlm

        filepath = bpy.path.abspath(self.filepath)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, f"Import failed: {e}")
            return {'CANCELLED'}

        # Schema validation: top-level must be a dict, "layers" must be a list.
        # A malformed file (corrupted, hand-edited, future version) shouldn't
        # crash Blender â€” fail soft with a user-visible error.
        if not isinstance(data, dict):
            self.report({'ERROR'}, "Invalid .tlm file: top-level must be an object")
            return {'CANCELLED'}
        layers_data = data.get("layers", [])
        if not isinstance(layers_data, list):
            self.report({'ERROR'}, "Invalid .tlm file: 'layers' must be an array")
            return {'CANCELLED'}

        if not self.merge:
            # Clear existing layers
            tlm.layers.clear()

        # Apply material-level properties if present. Old .tlm files (no
        # material_props block) fall through with defaults — fully
        # backward-compatible.
        mp = data.get("material_props", {})
        if isinstance(mp, dict) and mp:
            for prop_name, default in (
                ('bsdf_ior',                       1.45),
                ('use_volume_absorption',          False),
                ('volume_absorption_color',        None),
                ('volume_absorption_density',      1.0),
                ('use_volume_scatter',             False),
                ('volume_scatter_color',           None),
                ('volume_scatter_density',         0.5),
                ('volume_scatter_anisotropy',      0.0),
                ('use_emission_output',            False),
                ('use_base_color_alpha',           False),
                ('alpha_blend_method',             'AUTO'),
                ('use_displacement',               False),
                ('displacement_strength',          0.1),
                ('displacement_midlevel',          0.5),
                ('displacement_adaptive',          True),
            ):
                if prop_name in mp:
                    try:
                        setattr(tlm, prop_name, mp[prop_name])
                    except (TypeError, ValueError):
                        pass  # unknown enum value or invalid type — keep default

        imported = 0
        skipped = 0
        for ld in layers_data:
            if not isinstance(ld, dict):
                skipped += 1
                continue
            try:
                _dict_to_layer(ld, tlm)
                imported += 1
            except Exception as e:
                # Drop the half-built layer if _dict_to_layer added one before failing
                if len(tlm.layers) > imported and not self.merge:
                    tlm.layers.remove(len(tlm.layers) - 1)
                skipped += 1
                print(f"[TLM] Skipped malformed layer in {os.path.basename(filepath)}: {e}")

        # Restore settings if not merging
        if not self.merge:
            tlm.resolution = data.get("resolution", tlm.resolution)
            tlm.uv_map     = data.get("uv_map", tlm.uv_map)

        tlm.active_layer_index = max(0, len(tlm.layers) - 1)

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        if skipped:
            self.report({'WARNING'},
                        f"Imported {imported} layers, skipped {skipped} malformed "
                        f"from {os.path.basename(filepath)}")
        else:
            self.report({'INFO'},
                        f"Imported {imported} layers from {os.path.basename(filepath)}")
        return {'FINISHED'}


# â”€â”€â”€ Layer da Clipboard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TLM_OT_LayerFromClipboard(Operator):
    """Create a new layer from an image in the clipboard."""
    bl_idname = "tlm.layer_from_clipboard"
    bl_label = "Layer from Clipboard"
    bl_options = {'REGISTER', 'UNDO'}

    channel: bpy.props.EnumProperty(
        name="Channel",
        items=[
            ('base_color',    "Base Color",    ""),
            ('roughness',     "Roughness",     ""),
            ('metallic',      "Metallic",      ""),
            ('normal',        "Normal Map",    ""),
            ('emission',      "Emission",      ""),
            ('transmission',  "Transmission",  ""),
        ],
        default='base_color',
    )

    @classmethod
    def poll(cls, context):
        return _can_edit_tlm_stack(context)

    def execute(self, context):
        mat = _get_material(context)
        _ensure_nodes(mat)
        tlm = mat.tlm

        # Try to get image from clipboard via bpy.ops.image.new + paste.
        # `temp_img` and `committed` are tracked outside the try so the
        # finally block can clean up if an exception fires after the
        # blank image was created but before it gets renamed into a
        # real Clipboard_N layer image. Without this, repeated failed
        # paste attempts left TLM_Clipboard_Temp / .001 / .002 â€¦
        # accumulating in bpy.data.images.
        temp_img = None
        committed = False
        try:
            bpy.ops.image.new(name="TLM_Clipboard_Temp", width=1024, height=1024)
            temp_img = bpy.data.images.get("TLM_Clipboard_Temp")

            if hasattr(bpy.ops.image, 'clipboard_paste'):
                # Set the temp image as active in the image editor
                img_area = None
                for area in context.screen.areas:
                    if area.type == 'IMAGE_EDITOR':
                        img_area = area
                        break
                if img_area is None:
                    self.report({'ERROR'}, "Open an Image Editor area first")
                    return {'CANCELLED'}
                img_area.spaces.active.image = temp_img
                bpy.ops.image.clipboard_paste(area=img_area)
                pasted_img = img_area.spaces.active.image
            else:
                # Fallback: just use the blank image and inform user
                pasted_img = temp_img
                self.report({'WARNING'}, "Clipboard paste richiede Blender 4.x+. Creato layer vuoto.")

            pasted_img.name = f"Clipboard_{len(tlm.layers)+1}"
            committed = True  # temp_img has been adopted into the new name

            # Create layer
            layer = tlm.layers.add()
            layer.layer_type = "PAINT"
            layer.name = pasted_img.name
            layer.opacity = 1.0
            layer.blend_mode = "MIX"
            layer.visible = True

            if self.channel == 'base_color':
                layer.image_name = pasted_img.name
            else:
                info = CHANNEL_INFO.get(self.channel)
                if info:
                    flag_attr, img_attr, _ = info
                    setattr(layer, img_attr, pasted_img.name)
                    setattr(layer, flag_attr, True)

            # Append at end (base convention)
            new_idx = len(tlm.layers) - 1
            tlm.active_layer_index = new_idx

            if tlm.auto_composite:
                compositing.rebuild_node_tree(mat)

            self.report({'INFO'}, f"Layer creato da clipboard: '{pasted_img.name}'")
            return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"Clipboard non disponibile: {e}")
            return {'CANCELLED'}
        finally:
            # Drop the temp image if it was never adopted into a layer
            # (paste failed, image-editor missing, etc.). Safe to call
            # even when temp_img is None.
            if temp_img is not None and not committed:
                try:
                    bpy.data.images.remove(temp_img)
                except Exception:
                    pass


classes = [
    TLM_OT_ExportJSON,
    TLM_OT_ImportJSON,
    TLM_OT_LayerFromClipboard,
]
