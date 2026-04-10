"""Preset stack operators and built-in presets."""

import os
import json
import bpy
from bpy.types import Operator
from ._common import _get_material, _ensure_nodes, compositing


# Built-in presets shipped with the addon
BUILTIN_PRESETS = {
    "Metal Base": [
        # Fill base: dark steel, metallic, low roughness
        {"name": "Metal Base", "type": "FILL",
         "fill_color": [0.08, 0.08, 0.09, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.25,
         "use_metallic": True,  "metallic_fill":  1.0},
        # Proc: surface variation in roughness + subtle bump
        {"name": "Metal Surface", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 1.5, "proc_detail": 3.0, "proc_roughness_proc": 0.5,
         "proc_distortion": 0.2,
         "proc_color1": [0.06, 0.06, 0.07, 1.0],
         "proc_color2": [0.18, 0.18, 0.20, 1.0],
         "opacity": 0.4, "blend_mode": "Screen",
         "use_roughness": True, "roughness_fill": 0.45,
         "use_bump": True, "bump_strength": 0.3, "bump_distance": 0.02},
    ],
    "Rock Base": [
        # Fill base: dark volcanic rock
        {"name": "Rock Dark", "type": "FILL",
         "fill_color": [0.08, 0.06, 0.04, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.95,
         "use_metallic": True,  "metallic_fill":  0.0},
        # Proc: large-scale color variation with strong bump
        {"name": "Rock Variation", "type": "PROCEDURAL", "proc_type": "MUSGRAVE",
         "proc_scale": 0.5, "proc_detail": 6.0,
         "proc_roughness_proc": 0.6, "proc_lacunarity": 2.2,
         "proc_color1": [0.05, 0.04, 0.02, 1.0],
         "proc_color2": [0.42, 0.32, 0.20, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.9,
         "use_bump": True, "bump_strength": 1.2, "bump_distance": 0.08},
        # Proc: microdetail noise with fine bump
        {"name": "Rock Microdetail", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 2.5, "proc_detail": 8.0,
         "proc_roughness_proc": 0.7, "proc_distortion": 0.8,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.35, 0.28, 0.18, 1.0],
         "opacity": 0.5, "blend_mode": "Overlay",
         "use_bump": True, "bump_strength": 0.6, "bump_distance": 0.02},
    ],
    "Skin Base": [
        # Fill base: mid skin tone
        {"name": "Skin Base", "type": "FILL",
         "fill_color": [0.72, 0.48, 0.36, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.65,
         "use_metallic": True,  "metallic_fill":  0.0},
        # Darker undertone
        {"name": "Skin Undertone", "type": "FILL",
         "fill_color": [0.55, 0.30, 0.20, 1.0],
         "opacity": 0.4, "blend_mode": "Multiply"},
        # Proc: pore microdetail with subtle bump
        {"name": "Skin Pores", "type": "PROCEDURAL", "proc_type": "VORONOI",
         "proc_scale": 4.0, "proc_randomness": 0.8,
         "proc_color1": [0.60, 0.38, 0.28, 1.0],
         "proc_color2": [0.80, 0.58, 0.44, 1.0],
         "opacity": 0.15, "blend_mode": "Overlay",
         "use_roughness": True, "roughness_fill": 0.55,
         "use_bump": True, "bump_strength": 0.2, "bump_distance": 0.005},
    ],
    "Rusted Metal": [
        # Fill base: dark steel
        {"name": "Steel Base", "type": "FILL",
         "fill_color": [0.12, 0.11, 0.10, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.35,
         "use_metallic": True,  "metallic_fill":  0.9},
        # Rust patches: orange/brown noise
        {"name": "Rust Patches", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 0.7, "proc_detail": 8.0,
         "proc_roughness_proc": 0.6, "proc_distortion": 2.0,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.55, 0.18, 0.03, 1.0],
         "opacity": 0.85, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.9,
         "use_bump": True, "bump_strength": 0.8, "bump_distance": 0.04},
        # Surface corrosion: fine detail bump
        {"name": "Corrosion Detail", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 3.0, "proc_detail": 6.0,
         "proc_roughness_proc": 0.8, "proc_distortion": 1.0,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.30, 0.12, 0.04, 0.8],
         "opacity": 0.45, "blend_mode": "Multiply",
         "use_bump": True, "bump_strength": 0.4, "bump_distance": 0.015},
    ],
    "Wood Grain": [
        # Fill base: dark wood
        {"name": "Wood Dark", "type": "FILL",
         "fill_color": [0.25, 0.12, 0.04, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.75},
        # Wave: wood grain rings with bump
        {"name": "Wood Grain", "type": "PROCEDURAL", "proc_type": "WAVE",
         "proc_scale": 0.8, "proc_wave_type": "BANDS",
         "proc_distortion": 2.5, "proc_detail": 4.0,
         "proc_wave_detail_scale": 1.5,
         "proc_color1": [0.18, 0.08, 0.02, 1.0],
         "proc_color2": [0.55, 0.32, 0.12, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.65,
         "use_bump": True, "bump_strength": 0.5, "bump_distance": 0.03},
        # Fine grain noise
        {"name": "Wood Fiber", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 3.5, "proc_detail": 5.0, "proc_roughness_proc": 0.6,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.15, 0.08, 0.02, 0.6],
         "opacity": 0.35, "blend_mode": "Multiply",
         "use_bump": True, "bump_strength": 0.2, "bump_distance": 0.008},
    ],
}


class TLM_OT_ApplyPreset(Operator):
    """Apply a built-in or saved preset layer stack."""
    bl_idname = "tlm.apply_preset"
    bl_label = "Apply Preset"
    bl_options = {'REGISTER', 'UNDO'}

    preset_name: bpy.props.StringProperty(default="Metal Base")
    merge: bpy.props.BoolProperty(
        name="Merge with existing",
        description="Add preset layers on top of current stack",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def execute(self, context):
        mat = _get_material(context)
        _ensure_nodes(mat)
        tlm = mat.tlm

        preset_layers = BUILTIN_PRESETS.get(self.preset_name)
        if not preset_layers:
            # Try user presets directory
            preset_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "presets")
            preset_file = os.path.join(preset_dir, f"{self.preset_name}.tlm")
            if os.path.exists(preset_file):
                try:
                    with open(preset_file, 'r') as f:
                        data = json.load(f)
                    preset_layers = data.get("layers", [])
                except (ValueError, OSError) as e:
                    self.report({'ERROR'}, f"Invalid preset file: {e}")
                    return {'CANCELLED'}
            else:
                self.report({'ERROR'}, f"Preset '{self.preset_name}' not found")
                return {'CANCELLED'}

        if not self.merge:
            tlm.layers.clear()

        for ld in preset_layers:
            layer = tlm.layers.add()
            layer.name       = ld.get("name", "Layer")
            layer.layer_type = ld.get("type", "FILL")
            layer.opacity    = ld.get("opacity", 1.0)
            layer.visible    = ld.get("visible", True)
            layer.group_name = ld.get("group_name", "")
            layer.collapsed  = ld.get("collapsed", False)
            layer.use_clipping_mask = ld.get("use_clipping_mask", False)

            # blend_mode: map UI names to internal enum values
            bm_map = {
                "Normal": "MIX", "MIX": "MIX",
                "Screen": "SCREEN", "SCREEN": "SCREEN",
                "Multiply": "MULTIPLY", "MULTIPLY": "MULTIPLY",
                "Overlay": "OVERLAY", "OVERLAY": "OVERLAY",
                "Add": "ADD", "ADD": "ADD",
                "Subtract": "SUBTRACT", "SUBTRACT": "SUBTRACT",
                "Difference": "DIFFERENCE", "DIFFERENCE": "DIFFERENCE",
                "Darken": "DARKEN", "DARKEN": "DARKEN",
                "Lighten": "LIGHTEN", "LIGHTEN": "LIGHTEN",
                "Color Dodge": "COLOR_DODGE", "COLOR_DODGE": "COLOR_DODGE",
                "Color Burn": "COLOR_BURN", "COLOR_BURN": "COLOR_BURN",
                "Soft Light": "SOFT_LIGHT", "SOFT_LIGHT": "SOFT_LIGHT",
                "Linear Light": "LINEAR_LIGHT", "LINEAR_LIGHT": "LINEAR_LIGHT",
                "Exclusion": "EXCLUSION", "EXCLUSION": "EXCLUSION",
                "Hue": "HUE", "HUE": "HUE",
                "Saturation": "SATURATION", "SATURATION": "SATURATION",
                "Color": "COLOR", "COLOR": "COLOR",
                "Luminosity": "LUMINOSITY", "LUMINOSITY": "LUMINOSITY",
            }
            layer.blend_mode = bm_map.get(ld.get("blend_mode", "MIX"), "MIX")

            if layer.layer_type == "FILL":
                layer.fill_color = ld.get("fill_color", [1,1,1,1])
            elif layer.layer_type == "PROCEDURAL":
                layer.proc_type            = ld.get("proc_type", "NOISE")
                layer.proc_scale           = ld.get("proc_scale", 5.0)
                layer.proc_color1          = ld.get("proc_color1", [0,0,0,1])
                layer.proc_color2          = ld.get("proc_color2", [1,1,1,1])
                layer.proc_detail          = ld.get("proc_detail", 2.0)
                layer.proc_roughness_proc  = ld.get("proc_roughness_proc", 0.5)
                layer.proc_distortion      = ld.get("proc_distortion", 0.0)
                layer.proc_lacunarity      = ld.get("proc_lacunarity", 2.0)
                layer.proc_offset_x        = ld.get("proc_offset_x", 0.0)
                layer.proc_offset_y        = ld.get("proc_offset_y", 0.0)
                layer.proc_offset_z        = ld.get("proc_offset_z", 0.0)
                layer.proc_wave_type       = ld.get("proc_wave_type", "BANDS")
                layer.proc_wave_profile    = ld.get("proc_wave_profile", "SIN")
                layer.proc_wave_detail_scale = ld.get("proc_wave_detail_scale", 1.0)
                layer.proc_gradient_type   = ld.get("proc_gradient_type", "LINEAR")
                layer.proc_checker_scale   = ld.get("proc_checker_scale", 5.0)
                layer.proc_voronoi_feature  = ld.get("proc_voronoi_feature", "F1")
                layer.proc_voronoi_distance = ld.get("proc_voronoi_distance", "EUCLIDEAN")
                layer.proc_randomness       = ld.get("proc_randomness", 1.0)
                layer.proc_marble_distortion = ld.get("proc_marble_distortion", 5.0)
                layer.proc_marble_wave_type  = ld.get("proc_marble_wave_type", "BANDS")
                layer.proc_contrast         = ld.get("proc_contrast", 0.5)
                layer.proc_vector_distortion= ld.get("proc_vector_distortion", 0.0)
                layer.proc_coord_type       = ld.get("proc_coord_type", "GENERATED")
                layer.proc_emission_threshold = ld.get("proc_emission_threshold", 0.0)
                layer.use_proc_color3       = ld.get("use_proc_color3", False)
                if layer.use_proc_color3:
                    layer.proc_color3          = ld.get("proc_color3", [0.5, 0.5, 0.5, 1])
                    layer.proc_color3_position = ld.get("proc_color3_position", 0.5)
            elif layer.layer_type == "ADJUSTMENT":
                layer.adj_type             = ld.get("adj_type", "HUE_SAT")
                layer.adj_hue              = ld.get("adj_hue", 0.5)
                layer.adj_saturation       = ld.get("adj_saturation", 1.0)
                layer.adj_value            = ld.get("adj_value", 1.0)
                layer.adj_brightness       = ld.get("adj_brightness", 0.0)
                layer.adj_contrast         = ld.get("adj_contrast", 0.0)
                layer.adj_in_min           = ld.get("adj_in_min", 0.0)
                layer.adj_in_max           = ld.get("adj_in_max", 1.0)
                layer.adj_levels_gamma     = ld.get("adj_levels_gamma", 1.0)
                layer.adj_out_min          = ld.get("adj_out_min", 0.0)
                layer.adj_out_max          = ld.get("adj_out_max", 1.0)
                layer.adj_curve_contrast   = ld.get("adj_curve_contrast", 0.0)
                layer.adj_curve_brightness = ld.get("adj_curve_brightness", 0.0)
                layer.adj_curve_black_point = ld.get("adj_curve_black_point", 0.0)
                layer.adj_curve_white_point = ld.get("adj_curve_white_point", 1.0)
                layer.adj_lift  = ld.get("adj_lift", [1, 1, 1])
                layer.adj_gamma = ld.get("adj_gamma", [1, 1, 1])
                layer.adj_gain  = ld.get("adj_gain", [1, 1, 1])

            # Common properties for non-GROUP, non-ADJUSTMENT layers
            if layer.layer_type not in ("GROUP", "ADJUSTMENT"):
                layer.use_fresnel_mask  = ld.get("use_fresnel_mask", False)
                layer.fresnel_ior       = ld.get("fresnel_ior", 1.45)
                layer.fresnel_strength  = ld.get("fresnel_strength", 1.0)
                layer.use_mask          = ld.get("use_mask", False)
                layer.mask_image_name   = ld.get("mask_image_name", "")
                layer.use_triplanar     = ld.get("use_triplanar", False)
                layer.triplanar_scale   = ld.get("triplanar_scale", 1.0)
                layer.triplanar_sharpness = ld.get("triplanar_sharpness", 1.0)
                # PBR channels
                layer.use_roughness        = ld.get("use_roughness", False)
                layer.roughness_fill       = ld.get("roughness_fill", 0.5)
                layer.roughness_image_name = ld.get("roughness_image_name", "")
                layer.use_metallic         = ld.get("use_metallic", False)
                layer.metallic_fill        = ld.get("metallic_fill", 0.0)
                layer.metallic_image_name  = ld.get("metallic_image_name", "")
                layer.use_bump             = ld.get("use_bump", False)
                layer.bump_strength        = ld.get("bump_strength", 0.5)
                layer.bump_distance        = ld.get("bump_distance", 0.05)
                layer.use_normal           = ld.get("use_normal", False)
                layer.normal_image_name    = ld.get("normal_image_name", "")
                layer.use_emission         = ld.get("use_emission", False)
                layer.emission_image_name  = ld.get("emission_image_name", "")
                if layer.use_emission:
                    layer.emission_color    = ld.get("emission_color", [1,1,1,1])
                    layer.emission_strength = ld.get("emission_strength", 1.0)
                layer.use_transmission        = ld.get("use_transmission", False)
                layer.transmission_fill       = ld.get("transmission_fill", 0.0)
                layer.transmission_image_name = ld.get("transmission_image_name", "")

        tlm.active_layer_index = max(0, len(tlm.layers) - 1)
        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Applied preset '{self.preset_name}'")
        return {'FINISHED'}


class TLM_OT_SavePreset(Operator):
    """Save the current layer stack as a named preset."""
    bl_idname = "tlm.save_preset"
    bl_label = "Save as Preset"
    bl_options = {'REGISTER'}

    preset_name: bpy.props.StringProperty(name="Preset Name", default="My Preset")

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and len(mat.tlm.layers) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm

        preset_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "presets")
        os.makedirs(preset_dir, exist_ok=True)

        # Serialize layers (without image pixel data — presets are lightweight)
        layers_data = []
        for layer in tlm.layers:
            d = {
                "name": layer.name, "type": layer.layer_type,
                "opacity": round(layer.opacity, 4), "blend_mode": layer.blend_mode,
                "visible": layer.visible, "group_name": layer.group_name,
                "collapsed": layer.collapsed,
                "use_clipping_mask": layer.use_clipping_mask,
            }
            if layer.layer_type == "FILL":
                d["fill_color"] = list(layer.fill_color)
            elif layer.layer_type == "PROCEDURAL":
                d.update({
                    "proc_type": layer.proc_type, "proc_scale": layer.proc_scale,
                    "proc_color1": list(layer.proc_color1), "proc_color2": list(layer.proc_color2),
                    "proc_detail": layer.proc_detail, "proc_distortion": layer.proc_distortion,
                    "proc_roughness_proc": layer.proc_roughness_proc,
                    "proc_lacunarity": layer.proc_lacunarity,
                    "proc_offset_x": layer.proc_offset_x,
                    "proc_offset_y": layer.proc_offset_y,
                    "proc_offset_z": layer.proc_offset_z,
                    "proc_wave_type": layer.proc_wave_type,
                    "proc_wave_profile": layer.proc_wave_profile,
                    "proc_wave_detail_scale": layer.proc_wave_detail_scale,
                    "proc_gradient_type": layer.proc_gradient_type,
                    "proc_checker_scale": layer.proc_checker_scale,
                    "proc_voronoi_feature": layer.proc_voronoi_feature,
                    "proc_voronoi_distance": layer.proc_voronoi_distance,
                    "proc_randomness": layer.proc_randomness,
                    "proc_marble_distortion": layer.proc_marble_distortion,
                    "proc_marble_wave_type": layer.proc_marble_wave_type,
                    "proc_contrast": layer.proc_contrast,
                    "proc_vector_distortion": layer.proc_vector_distortion,
                    "proc_coord_type": layer.proc_coord_type,
                    "proc_emission_threshold": getattr(layer, 'proc_emission_threshold', 0.0),
                    "use_proc_color3": getattr(layer, 'use_proc_color3', False),
                })
                if getattr(layer, 'use_proc_color3', False):
                    d["proc_color3"] = list(layer.proc_color3)
                    d["proc_color3_position"] = layer.proc_color3_position
            elif layer.layer_type == "ADJUSTMENT":
                d.update({
                    "adj_type": layer.adj_type,
                    "adj_hue": layer.adj_hue,
                    "adj_saturation": layer.adj_saturation,
                    "adj_value": layer.adj_value,
                    "adj_brightness": layer.adj_brightness,
                    "adj_contrast": layer.adj_contrast,
                    "adj_in_min": layer.adj_in_min,
                    "adj_in_max": layer.adj_in_max,
                    "adj_levels_gamma": layer.adj_levels_gamma,
                    "adj_out_min": layer.adj_out_min,
                    "adj_out_max": layer.adj_out_max,
                    "adj_curve_contrast": layer.adj_curve_contrast,
                    "adj_curve_brightness": layer.adj_curve_brightness,
                    "adj_curve_black_point": layer.adj_curve_black_point,
                    "adj_curve_white_point": layer.adj_curve_white_point,
                    "adj_lift": list(layer.adj_lift),
                    "adj_gamma": list(layer.adj_gamma),
                    "adj_gain": list(layer.adj_gain),
                })
            # Common properties for non-GROUP, non-ADJUSTMENT layers
            if layer.layer_type not in ("GROUP", "ADJUSTMENT"):
                d["use_fresnel_mask"]  = getattr(layer, 'use_fresnel_mask', False)
                d["fresnel_ior"]       = getattr(layer, 'fresnel_ior', 1.45)
                d["fresnel_strength"]  = getattr(layer, 'fresnel_strength', 1.0)
                d["use_mask"]          = layer.use_mask
                d["mask_image_name"]   = layer.mask_image_name
                d["use_triplanar"]     = getattr(layer, 'use_triplanar', False)
                d["triplanar_scale"]   = getattr(layer, 'triplanar_scale', 1.0)
                d["triplanar_sharpness"] = getattr(layer, 'triplanar_sharpness', 1.0)
                # PBR channels
                d["use_roughness"]        = layer.use_roughness
                d["roughness_fill"]       = layer.roughness_fill
                d["roughness_image_name"] = getattr(layer, 'roughness_image_name', "")
                d["use_metallic"]         = layer.use_metallic
                d["metallic_fill"]        = layer.metallic_fill
                d["metallic_image_name"]  = getattr(layer, 'metallic_image_name', "")
                d["use_bump"]             = layer.use_bump
                d["bump_strength"]        = layer.bump_strength
                d["bump_distance"]        = layer.bump_distance
                d["use_normal"]           = getattr(layer, 'use_normal', False)
                d["normal_image_name"]    = getattr(layer, 'normal_image_name', "")
                d["use_emission"]         = getattr(layer, 'use_emission', False)
                d["emission_image_name"]  = getattr(layer, 'emission_image_name', "")
                if getattr(layer, 'use_emission', False):
                    d["emission_color"]    = list(layer.emission_color)
                    d["emission_strength"] = layer.emission_strength
                d["use_transmission"]        = getattr(layer, 'use_transmission', False)
                d["transmission_fill"]       = getattr(layer, 'transmission_fill', 0.0)
                d["transmission_image_name"] = getattr(layer, 'transmission_image_name', "")
            layers_data.append(d)

        data = {"preset_name": self.preset_name, "layers": layers_data}
        filepath = os.path.join(preset_dir, f"{self.preset_name}.tlm")
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            self.report({'ERROR'}, f"Failed to save preset: {e}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Saved preset '{self.preset_name}'")
        return {'FINISHED'}


class TLM_OT_DeletePreset(Operator):
    """Delete a user-saved preset file."""
    bl_idname = "tlm.delete_preset"
    bl_label = "Delete Preset"
    bl_options = {'REGISTER'}

    preset_name: bpy.props.StringProperty(default="")

    def execute(self, context):
        preset_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "presets")
        filepath = os.path.join(preset_dir, f"{self.preset_name}.tlm")
        if os.path.exists(filepath):
            os.remove(filepath)
            self.report({'INFO'}, f"Deleted preset '{self.preset_name}'")
        else:
            self.report({'WARNING'}, f"Preset file not found: {self.preset_name}")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)


classes = [
    TLM_OT_ApplyPreset,
    TLM_OT_SavePreset,
    TLM_OT_DeletePreset,
]
