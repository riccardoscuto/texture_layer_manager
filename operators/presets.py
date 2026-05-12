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

    # ── Reference material recreations ────────────────────────────────────────
    # Ten materials from a common PBR reference grid: candy, rock, planet, etc.
    # All use only TLM procedural layers — no external textures needed.
    # Blend mode rules used throughout:
    #   Screen  → color1=black(passthrough), color2=bright → adds highlights
    #   Multiply → color1=white(passthrough), color2=dark → adds shadows/dirt
    #   MIX     → full pattern replacement (color1 at fac=0, color2 at fac=1)
    #   Overlay → contrast enhancement (darks darker, lights lighter)

    "Blue Marble": [
        # Polished deep-blue stone base
        {"name": "Marble Base", "type": "FILL",
         "fill_color": [0.04, 0.08, 0.32, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.12,
         "use_metallic": True, "metallic_fill": 0.0},
        # Large-scale depth variation: dark to lighter blue
        {"name": "Marble Depth", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 2.0, "proc_detail": 3.0,
         "proc_roughness_proc": 0.5, "proc_distortion": 1.0,
         "proc_color1": [0.02, 0.04, 0.18, 1.0],
         "proc_color2": [0.08, 0.14, 0.48, 1.0],
         "opacity": 0.60, "blend_mode": "MIX"},
        # White veins — Screen: black areas pass through (keep blue), bright=white veins
        {"name": "Marble White Veins", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 1.5, "proc_detail": 14.0,
         "proc_roughness_proc": 0.8, "proc_distortion": 2.5,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.88, 0.88, 0.90, 1.0],
         "opacity": 0.80, "blend_mode": "Screen"},
        # Secondary grey-blue veins (finer, lighter)
        {"name": "Marble Grey Veins", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 3.5, "proc_detail": 8.0,
         "proc_roughness_proc": 0.6, "proc_distortion": 1.5,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.52, 0.58, 0.72, 1.0],
         "opacity": 0.35, "blend_mode": "Screen"},
    ],

    "Mars Rock": [
        # Iron-oxide dust base
        {"name": "Mars Base", "type": "FILL",
         "fill_color": [0.48, 0.12, 0.04, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.90,
         "use_metallic": True, "metallic_fill": 0.0},
        # Large-scale color variation: cooler red to warm orange-red
        {"name": "Mars Variation", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 3.5, "proc_detail": 6.0,
         "proc_roughness_proc": 0.6, "proc_distortion": 0.8,
         "proc_color1": [0.32, 0.08, 0.02, 1.0],
         "proc_color2": [0.65, 0.22, 0.08, 1.0],
         "opacity": 0.80, "blend_mode": "Overlay",
         "use_roughness": True, "roughness_fill": 0.88},
        # Rock crevices — Multiply: white=passthrough, dark shadow at noise peaks
        {"name": "Mars Crevice", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 7.0, "proc_detail": 4.0,
         "proc_roughness_proc": 0.5, "proc_distortion": 1.2,
         "proc_color1": [1.0, 1.0, 1.0, 1.0],
         "proc_color2": [0.18, 0.04, 0.01, 1.0],
         "opacity": 0.55, "blend_mode": "Multiply",
         "use_bump": True, "bump_strength": 0.9, "bump_distance": 0.06},
        # Fine dust — Screen: adds lighter dust highlights on surfaces
        {"name": "Mars Dust", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 14.0, "proc_detail": 2.0,
         "proc_roughness_proc": 0.4, "proc_distortion": 0.3,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.68, 0.38, 0.18, 1.0],
         "opacity": 0.30, "blend_mode": "Screen"},
    ],

    "Saturn Planet": [
        # Warm tan atmospheric base
        {"name": "Saturn Base", "type": "FILL",
         "fill_color": [0.62, 0.48, 0.22, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.92,
         "use_metallic": True, "metallic_fill": 0.0},
        # Light bands — Screen adds bright band highlights
        # Note: BANDS direction is X in TLM. Rotate object 90° for horizontal bands.
        {"name": "Saturn Light Bands", "type": "PROCEDURAL", "proc_type": "WAVE",
         "proc_wave_type": "BANDS", "proc_scale": 6.0,
         "proc_distortion": 0.8, "proc_detail": 4.0, "proc_wave_detail_scale": 1.5,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.82, 0.70, 0.38, 1.0],
         "opacity": 0.65, "blend_mode": "Screen"},
        # Dark bands — Multiply darkens periodic zones
        {"name": "Saturn Dark Bands", "type": "PROCEDURAL", "proc_type": "WAVE",
         "proc_wave_type": "BANDS", "proc_scale": 11.0,
         "proc_distortion": 1.2, "proc_detail": 3.0, "proc_wave_detail_scale": 2.0,
         "proc_color1": [1.0, 1.0, 1.0, 1.0],
         "proc_color2": [0.28, 0.18, 0.06, 1.0],
         "opacity": 0.55, "blend_mode": "Multiply"},
        # Atmospheric micro-turbulence overlay
        {"name": "Saturn Turbulence", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 8.0, "proc_detail": 10.0,
         "proc_roughness_proc": 0.6, "proc_distortion": 1.5,
         "proc_color1": [0.52, 0.38, 0.15, 1.0],
         "proc_color2": [0.78, 0.62, 0.32, 1.0],
         "opacity": 0.30, "blend_mode": "Overlay"},
    ],

    "Jawbreaker Candy": [
        # Very glossy white candy base
        {"name": "Jawbreaker Base", "type": "FILL",
         "fill_color": [0.92, 0.92, 0.92, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.08,
         "use_metallic": True, "metallic_fill": 0.0},
        # 3-color Voronoi: red at cell centers, blue mid-distance, yellow at edges
        # Creates a gradient spectrum across each Voronoi cell
        {"name": "Jawbreaker Colors", "type": "PROCEDURAL", "proc_type": "VORONOI",
         "proc_scale": 3.5, "proc_voronoi_feature": "F1", "proc_randomness": 0.9,
         "proc_contrast": 0.0,
         "proc_color1": [0.88, 0.08, 0.08, 1.0],
         "use_proc_color3": True,
         "proc_color3": [0.05, 0.18, 0.90, 1.0],
         "proc_color3_position": 0.45,
         "proc_color2": [0.92, 0.78, 0.05, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.08},
        # Glaze highlights — Screen adds white glint at Voronoi cell boundaries
        {"name": "Jawbreaker Glaze", "type": "PROCEDURAL", "proc_type": "VORONOI",
         "proc_scale": 8.0, "proc_voronoi_feature": "F1", "proc_randomness": 0.6,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.95, 0.95, 0.95, 1.0],
         "opacity": 0.22, "blend_mode": "Screen"},
    ],

    "Quartz Rock": [
        # Semi-translucent grey-white mineral base
        {"name": "Quartz Base", "type": "FILL",
         "fill_color": [0.78, 0.76, 0.72, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.72,
         "use_metallic": True, "metallic_fill": 0.0,
         "use_transmission": True, "transmission_fill": 0.08},
        # Internal mineral inclusions — Multiply: white=passthrough, grey=darker veins
        {"name": "Quartz Inclusions", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 5.5, "proc_detail": 10.0,
         "proc_roughness_proc": 0.7, "proc_distortion": 0.8,
         "proc_color1": [1.0, 1.0, 1.0, 1.0],
         "proc_color2": [0.35, 0.33, 0.30, 1.0],
         "opacity": 0.55, "blend_mode": "Multiply"},
        # Crystal facet glints — Screen adds bright spots at Voronoi cell centers
        {"name": "Quartz Glints", "type": "PROCEDURAL", "proc_type": "VORONOI",
         "proc_scale": 12.0, "proc_randomness": 0.7, "proc_voronoi_feature": "F1",
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.92, 0.90, 0.88, 1.0],
         "opacity": 0.28, "blend_mode": "Screen",
         "use_roughness": True, "roughness_fill": 0.15},
    ],

    "Snowy Mountain": [
        # Dark grey rock base
        {"name": "Mountain Rock", "type": "FILL",
         "fill_color": [0.20, 0.18, 0.16, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.88,
         "use_metallic": True, "metallic_fill": 0.0},
        # Rock surface texture + bump
        {"name": "Mountain Rock Texture", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 5.0, "proc_detail": 8.0,
         "proc_roughness_proc": 0.7, "proc_distortion": 0.5,
         "proc_color1": [0.12, 0.10, 0.08, 1.0],
         "proc_color2": [0.38, 0.34, 0.28, 1.0],
         "opacity": 0.85, "blend_mode": "Overlay",
         "use_roughness": True, "roughness_fill": 0.85,
         "use_bump": True, "bump_strength": 1.0, "bump_distance": 0.06},
        # Snow coverage — MIX: rock-color areas = no snow, white = snow patches
        # Increase proc_scale for denser snow, decrease for larger snow fields
        {"name": "Snow Coverage", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 1.8, "proc_detail": 3.0,
         "proc_roughness_proc": 0.4, "proc_distortion": 0.2,
         "proc_color1": [0.20, 0.18, 0.16, 1.0],
         "proc_color2": [0.88, 0.90, 0.92, 1.0],
         "opacity": 0.90, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.82},
    ],

    "SciFi Greeble": [
        # Dark metallic hull base
        {"name": "Greeble Hull", "type": "FILL",
         "fill_color": [0.28, 0.28, 0.30, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.50,
         "use_metallic": True, "metallic_fill": 1.0},
        # Panel variation — Voronoi F1 creates panel-like cell regions + bump seams
        {"name": "Greeble Panels", "type": "PROCEDURAL", "proc_type": "VORONOI",
         "proc_scale": 2.5, "proc_voronoi_feature": "F1", "proc_randomness": 0.5,
         "proc_color1": [0.20, 0.20, 0.22, 1.0],
         "proc_color2": [0.38, 0.38, 0.42, 1.0],
         "opacity": 0.60, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.30,
         "use_bump": True, "bump_strength": 0.7, "bump_distance": 0.025},
        # Grime and wear — Multiply darkens random surface areas
        {"name": "Greeble Grime", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 3.0, "proc_detail": 5.0,
         "proc_roughness_proc": 0.8, "proc_distortion": 1.0,
         "proc_color1": [1.0, 1.0, 1.0, 1.0],
         "proc_color2": [0.10, 0.10, 0.12, 1.0],
         "opacity": 0.45, "blend_mode": "Multiply",
         "use_roughness": True, "roughness_fill": 0.75},
        # Tech-light accents — Voronoi emission dots at cell centers
        {"name": "Greeble Lights", "type": "PROCEDURAL", "proc_type": "VORONOI",
         "proc_scale": 8.0, "proc_voronoi_feature": "F1", "proc_randomness": 0.3,
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.08, 0.90, 0.50, 1.0],
         "opacity": 0.12, "blend_mode": "Screen",
         "use_emission": True,
         "emission_color": [0.08, 0.90, 0.50, 1.0], "emission_strength": 3.0},
    ],

    "Candy Corn": [
        # Waxy yellow base — the main body color
        {"name": "Candy Yellow", "type": "FILL",
         "fill_color": [0.95, 0.72, 0.08, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.28,
         "use_metallic": True, "metallic_fill": 0.0},
        # Orange band — Wave BANDS create periodic stripes along X axis
        # Note: use OBJECT coords + rotate mesh 90° to align bands with candy corn height
        {"name": "Candy Orange Band", "type": "PROCEDURAL", "proc_type": "WAVE",
         "proc_wave_type": "BANDS", "proc_scale": 2.2,
         "proc_distortion": 0.0, "proc_detail": 0.0, "proc_wave_detail_scale": 0.0,
         "proc_contrast": 0.85,
         "proc_color1": [0.90, 0.38, 0.04, 1.0],
         "proc_color2": [0.95, 0.72, 0.08, 1.0],
         "opacity": 1.0, "blend_mode": "MIX"},
        # White tip — finer wave creates the narrow white section at the point
        {"name": "Candy White Tip", "type": "PROCEDURAL", "proc_type": "WAVE",
         "proc_wave_type": "BANDS", "proc_scale": 4.8,
         "proc_distortion": 0.0, "proc_detail": 0.0, "proc_wave_detail_scale": 0.0,
         "proc_contrast": 0.88,
         "proc_color1": [0.93, 0.93, 0.92, 1.0],
         "proc_color2": [0.90, 0.38, 0.04, 1.0],
         "opacity": 1.0, "blend_mode": "MIX"},
    ],

    "White Bricks": [
        # Mortar/grout base
        {"name": "Brick Mortar", "type": "FILL",
         "fill_color": [0.68, 0.66, 0.63, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.92,
         "use_metallic": True, "metallic_fill": 0.0},
        # Brick faces — Checker alternates between brick-white and mortar-grey
        # Note: Checker is square (1:1). Real bricks are 2:1 offset — use UV scale for ratio.
        {"name": "Brick Faces", "type": "PROCEDURAL", "proc_type": "CHECKER",
         "proc_scale": 8.0,
         "proc_color1": [0.85, 0.84, 0.82, 1.0],
         "proc_color2": [0.68, 0.66, 0.63, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.85,
         "use_bump": True, "bump_strength": 0.5, "bump_distance": 0.015},
        # Micro surface variation on brick faces
        {"name": "Brick Surface Detail", "type": "PROCEDURAL", "proc_type": "NOISE",
         "proc_scale": 15.0, "proc_detail": 3.0, "proc_roughness_proc": 0.5,
         "proc_color1": [0.65, 0.63, 0.60, 1.0],
         "proc_color2": [0.82, 0.81, 0.78, 1.0],
         "opacity": 0.25, "blend_mode": "Overlay"},
    ],

    "Lolly Pop": [
        # Very glossy white candy base
        {"name": "Lolly Base", "type": "FILL",
         "fill_color": [0.95, 0.95, 0.95, 1.0],
         "opacity": 1.0, "blend_mode": "MIX",
         "use_roughness": True, "roughness_fill": 0.05,
         "use_metallic": True, "metallic_fill": 0.0},
        # Main swirl: Marble (Wave + Noise turbulence) creates the swirled pattern
        # Red-to-blue spiral arm. High marble_distortion = more turbulence/swirling.
        {"name": "Lolly Red-Blue Swirl", "type": "PROCEDURAL", "proc_type": "MARBLE",
         "proc_scale": 4.0, "proc_detail": 3.0,
         "proc_roughness_proc": 0.5, "proc_distortion": 2.0,
         "proc_marble_distortion": 8.0, "proc_marble_wave_type": "BANDS",
         "proc_color1": [0.92, 0.10, 0.10, 1.0],
         "proc_color2": [0.08, 0.18, 0.92, 1.0],
         "opacity": 1.0, "blend_mode": "MIX"},
        # Green swirl arm — Screen: dark areas pass through, bright=adds green
        {"name": "Lolly Green Swirl", "type": "PROCEDURAL", "proc_type": "MARBLE",
         "proc_scale": 3.0, "proc_detail": 2.0,
         "proc_roughness_proc": 0.3, "proc_distortion": 1.8,
         "proc_marble_distortion": 6.0, "proc_marble_wave_type": "BANDS",
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.10, 0.88, 0.22, 1.0],
         "proc_offset_x": 0.3, "proc_offset_y": 0.5,
         "opacity": 0.55, "blend_mode": "Screen"},
        # Yellow accent swirl
        {"name": "Lolly Yellow Swirl", "type": "PROCEDURAL", "proc_type": "MARBLE",
         "proc_scale": 5.0, "proc_detail": 2.0,
         "proc_roughness_proc": 0.4, "proc_distortion": 1.5,
         "proc_marble_distortion": 5.0, "proc_marble_wave_type": "BANDS",
         "proc_color1": [0.0, 0.0, 0.0, 0.0],
         "proc_color2": [0.96, 0.85, 0.10, 1.0],
         "proc_offset_z": 0.4,
         "opacity": 0.45, "blend_mode": "Screen"},
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
                    if not isinstance(data, dict):
                        self.report({'ERROR'},
                                    f"Invalid preset file: top-level must be an object")
                        return {'CANCELLED'}
                    preset_layers = data.get("layers", [])
                except (ValueError, OSError, TypeError) as e:
                    self.report({'ERROR'}, f"Invalid preset file: {e}")
                    return {'CANCELLED'}
            else:
                self.report({'ERROR'}, f"Preset '{self.preset_name}' not found")
                return {'CANCELLED'}

        # Schema validation: corrupted/hand-edited presets shouldn't crash Blender.
        if not isinstance(preset_layers, list):
            self.report({'ERROR'}, "Invalid preset format: 'layers' must be an array")
            return {'CANCELLED'}

        if not self.merge:
            tlm.layers.clear()

        # Per-layer apply isolated in a closure so a single malformed entry
        # can be rolled back without aborting the whole preset import.
        def _apply_layer_dict(ld):
            layer = tlm.layers.add()
            layer.name       = ld.get("name", "Layer")
            layer.layer_type = ld.get("type", "FILL")
            layer.opacity    = ld.get("opacity", 1.0)
            layer.visible    = ld.get("visible", True)
            # GROUP layers are always root-level — discard any stray parent
            # so a hand-edited preset can't produce a nested-group state.
            _raw_group = ld.get("group_name", "")
            layer.group_name = "" if layer.layer_type == "GROUP" else _raw_group
            layer.collapsed  = ld.get("collapsed", False)
            layer.use_clipping_mask = ld.get("use_clipping_mask", False)
            # Routing — restore output_channel; legacy 'AUTO' maps to 'BASE_COLOR'
            _out_ch = ld.get("output_channel", "BASE_COLOR")
            if _out_ch == "AUTO":
                _out_ch = "BASE_COLOR"
            try:
                layer.output_channel = _out_ch
            except (TypeError, ValueError):
                layer.output_channel = "BASE_COLOR"
            # Branching — per-channel blend mode overrides
            layer.blend_mode_base_color   = ld.get("blend_mode_base_color",   "INHERIT")
            layer.blend_mode_roughness    = ld.get("blend_mode_roughness",    "INHERIT")
            layer.blend_mode_metallic     = ld.get("blend_mode_metallic",     "INHERIT")
            layer.blend_mode_emission     = ld.get("blend_mode_emission",     "INHERIT")
            layer.blend_mode_transmission = ld.get("blend_mode_transmission", "INHERIT")
            layer.blend_mode_alpha        = ld.get("blend_mode_alpha",        "INHERIT")

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
                # Back-compat: CLOUDS was merged into NOISE; proc_checker_scale
                # was merged into proc_scale. Migrate on load.
                _pt_raw = ld.get("proc_type", "NOISE")
                if _pt_raw == "CLOUDS":
                    _pt_raw = "NOISE"
                layer.proc_type            = _pt_raw
                if _pt_raw == "CHECKER" and "proc_checker_scale" in ld:
                    layer.proc_scale       = ld["proc_checker_scale"]
                else:
                    layer.proc_scale       = ld.get("proc_scale", 5.0)
                layer.proc_color1          = ld.get("proc_color1", [0,0,0,1])
                layer.proc_color2          = ld.get("proc_color2", [1,1,1,1])
                layer.proc_detail          = ld.get("proc_detail", 2.0)
                layer.proc_roughness_proc  = ld.get("proc_roughness_proc", 0.5)
                layer.proc_distortion      = ld.get("proc_distortion", 0.0)
                layer.proc_magic_distortion = ld.get("proc_magic_distortion", 1.0)
                layer.proc_magic_depth     = ld.get("proc_magic_depth", 2)
                layer.proc_stripe_direction = ld.get("proc_stripe_direction", "Y")
                layer.proc_stripe_width    = ld.get("proc_stripe_width", 0.5)
                layer.proc_stripe_sharpness = ld.get("proc_stripe_sharpness", 1.0)
                layer.proc_hex_edge_width  = ld.get("proc_hex_edge_width", 0.05)
                layer.proc_lacunarity      = ld.get("proc_lacunarity", 2.0)
                layer.proc_offset_x        = ld.get("proc_offset_x", 0.0)
                layer.proc_offset_y        = ld.get("proc_offset_y", 0.0)
                layer.proc_offset_z        = ld.get("proc_offset_z", 0.0)
                layer.proc_wave_type       = ld.get("proc_wave_type", "BANDS")
                layer.proc_wave_profile    = ld.get("proc_wave_profile", "SIN")
                layer.proc_wave_detail_scale = ld.get("proc_wave_detail_scale", 1.0)
                layer.proc_gradient_type   = ld.get("proc_gradient_type", "LINEAR")
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
                # Feature A — Advanced coordinates
                layer.proc_coord_transform  = ld.get("proc_coord_transform", "NONE")
                layer.proc_swirl_amount     = ld.get("proc_swirl_amount", 2.0)
                # Feature B — Voronoi random per cell
                layer.proc_voronoi_random_color = ld.get("proc_voronoi_random_color", False)
                layer.proc_voronoi_random_seed  = ld.get("proc_voronoi_random_seed", 0.0)
            elif layer.layer_type == "REFERENCE":
                layer.reference_layer_name = ld.get("reference_layer_name", "")
            elif layer.layer_type == "ADJUSTMENT":
                # CURVES was removed in favour of LEVELS+BRIGHT_CONTRAST —
                # remap legacy presets so they still load without error.
                adj_t = ld.get("adj_type", "HUE_SAT")
                if adj_t == "CURVES":
                    adj_t = "BRIGHT_CONTRAST"
                layer.adj_type             = adj_t
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
                # Feature C — Advanced combinable masks
                layer.mask_source        = ld.get("mask_source", "IMAGE")
                layer.mask_invert        = ld.get("mask_invert", False)
                layer.mask_ao_distance   = ld.get("mask_ao_distance", 0.5)
                layer.use_mask_b         = ld.get("use_mask_b", False)
                layer.mask_source_b      = ld.get("mask_source_b", "POINTINESS")
                layer.mask_image_name_b  = ld.get("mask_image_name_b", "")
                layer.mask_invert_b      = ld.get("mask_invert_b", False)
                layer.mask_ao_distance_b = ld.get("mask_ao_distance_b", 0.5)
                layer.mask_combine       = ld.get("mask_combine", "MULTIPLY")
                layer.mask_contrast      = ld.get("mask_contrast", 0.5)
                # Mask refinement — Levels
                layer.use_mask_levels     = ld.get("use_mask_levels", False)
                layer.mask_levels_in_min  = ld.get("mask_levels_in_min", 0.0)
                layer.mask_levels_in_max  = ld.get("mask_levels_in_max", 1.0)
                layer.mask_levels_gamma   = ld.get("mask_levels_gamma", 1.0)
                layer.mask_levels_out_min = ld.get("mask_levels_out_min", 0.0)
                layer.mask_levels_out_max = ld.get("mask_levels_out_max", 1.0)
                # Mask refinement — Softness + Blur
                layer.mask_softness       = ld.get("mask_softness", 0.0)
                layer.mask_blur           = ld.get("mask_blur", 0.0)
                # Smart generator parameters
                layer.mask_gen_intensity     = ld.get("mask_gen_intensity", 1.0)
                layer.mask_gen_breakup       = ld.get("mask_gen_breakup", 0.3)
                layer.mask_gen_breakup_scale = ld.get("mask_gen_breakup_scale", 15.0)
                layer.mask_gen_sharpness     = ld.get("mask_gen_sharpness", 0.5)
                # Image texture mapping config — Triplanar removed in
                # favour of paint_projection='BOX'. Legacy presets carry
                # use_triplanar, auto-migrate them.
                layer.paint_interpolation    = ld.get("paint_interpolation", "Linear")
                legacy_triplanar = ld.get("use_triplanar", False)
                layer.paint_projection       = ld.get(
                    "paint_projection", "BOX" if legacy_triplanar else "FLAT"
                )
                layer.paint_projection_blend = ld.get("paint_projection_blend", 0.3)
                layer.paint_source           = ld.get("paint_source", "FILE")
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
                layer.normal_strength      = ld.get("normal_strength", 1.0)
                layer.normal_tile_scale    = ld.get("normal_tile_scale", 1.0)
                layer.normal_rotation      = ld.get("normal_rotation", 0.0)
                layer.use_emission         = ld.get("use_emission", False)
                layer.emission_image_name  = ld.get("emission_image_name", "")
                if layer.use_emission:
                    layer.emission_color    = ld.get("emission_color", [1,1,1,1])
                    layer.emission_strength = ld.get("emission_strength", 1.0)
                # Selective emission
                layer.emission_selector_type       = ld.get("emission_selector_type", "NONE")
                layer.emission_selector_scale      = ld.get("emission_selector_scale", 4.0)
                layer.emission_selector_threshold  = ld.get("emission_selector_threshold", 0.3)
                layer.emission_selector_seed       = ld.get("emission_selector_seed", 0.0)
                layer.emission_selector_image_name = ld.get("emission_selector_image_name", "")
                layer.use_transmission        = ld.get("use_transmission", False)
                layer.transmission_fill       = ld.get("transmission_fill", 0.0)
                layer.transmission_image_name = ld.get("transmission_image_name", "")
                layer.use_alpha               = ld.get("use_alpha", False)
                layer.alpha_fill              = ld.get("alpha_fill", 1.0)
                layer.alpha_image_name        = ld.get("alpha_image_name", "")

        skipped = 0
        for ld in preset_layers:
            if not isinstance(ld, dict):
                skipped += 1
                continue
            count_before = len(tlm.layers)
            try:
                _apply_layer_dict(ld)
            except Exception as e:
                # Roll back partial layer if the failure happened mid-way
                while len(tlm.layers) > count_before:
                    tlm.layers.remove(len(tlm.layers) - 1)
                skipped += 1
                print(f"[TLM] Skipped malformed preset layer: {e}")

        tlm.active_layer_index = max(0, len(tlm.layers) - 1)
        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        if skipped:
            self.report({'WARNING'},
                        f"Applied preset '{self.preset_name}' "
                        f"(skipped {skipped} malformed layers)")
        else:
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
            # Routing — which BSDF input the layer drives
            d["output_channel"]          = getattr(layer, 'output_channel',          'BASE_COLOR')
            # Branching — per-channel blend mode overrides
            d["blend_mode_base_color"]   = getattr(layer, 'blend_mode_base_color',   'INHERIT')
            d["blend_mode_roughness"]    = getattr(layer, 'blend_mode_roughness',    'INHERIT')
            d["blend_mode_metallic"]     = getattr(layer, 'blend_mode_metallic',     'INHERIT')
            d["blend_mode_emission"]     = getattr(layer, 'blend_mode_emission',     'INHERIT')
            d["blend_mode_transmission"] = getattr(layer, 'blend_mode_transmission', 'INHERIT')
            d["blend_mode_alpha"]        = getattr(layer, 'blend_mode_alpha',        'INHERIT')

            if layer.layer_type == "FILL":
                d["fill_color"] = list(layer.fill_color)
            elif layer.layer_type == "REFERENCE":
                d["reference_layer_name"] = getattr(layer, 'reference_layer_name', "")
            elif layer.layer_type == "PROCEDURAL":
                d.update({
                    "proc_type": layer.proc_type, "proc_scale": layer.proc_scale,
                    "proc_color1": list(layer.proc_color1), "proc_color2": list(layer.proc_color2),
                    "proc_detail": layer.proc_detail, "proc_distortion": layer.proc_distortion,
                    "proc_magic_distortion": getattr(layer, 'proc_magic_distortion', 1.0),
                    "proc_magic_depth": getattr(layer, 'proc_magic_depth', 2),
                    "proc_stripe_direction": getattr(layer, 'proc_stripe_direction', 'Y'),
                    "proc_stripe_width": getattr(layer, 'proc_stripe_width', 0.5),
                    "proc_stripe_sharpness": getattr(layer, 'proc_stripe_sharpness', 1.0),
                    "proc_hex_edge_width": getattr(layer, 'proc_hex_edge_width', 0.05),
                    "proc_roughness_proc": layer.proc_roughness_proc,
                    "proc_lacunarity": layer.proc_lacunarity,
                    "proc_offset_x": layer.proc_offset_x,
                    "proc_offset_y": layer.proc_offset_y,
                    "proc_offset_z": layer.proc_offset_z,
                    "proc_wave_type": layer.proc_wave_type,
                    "proc_wave_profile": layer.proc_wave_profile,
                    "proc_wave_detail_scale": layer.proc_wave_detail_scale,
                    "proc_gradient_type": layer.proc_gradient_type,
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
                    # Feature A — Advanced coordinate transforms
                    "proc_coord_transform": getattr(layer, 'proc_coord_transform', 'NONE'),
                    "proc_swirl_amount":    getattr(layer, 'proc_swirl_amount',    2.0),
                    # Feature B — Voronoi random per cell
                    "proc_voronoi_random_color": getattr(layer, 'proc_voronoi_random_color', False),
                    "proc_voronoi_random_seed":  getattr(layer, 'proc_voronoi_random_seed',  0.0),
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
                # Feature C — Advanced combinable masks
                d["mask_source"]        = getattr(layer, 'mask_source', 'IMAGE')
                d["mask_invert"]        = getattr(layer, 'mask_invert', False)
                d["mask_ao_distance"]   = getattr(layer, 'mask_ao_distance', 0.5)
                d["use_mask_b"]         = getattr(layer, 'use_mask_b', False)
                d["mask_source_b"]      = getattr(layer, 'mask_source_b', 'POINTINESS')
                d["mask_image_name_b"]  = getattr(layer, 'mask_image_name_b', "")
                d["mask_invert_b"]      = getattr(layer, 'mask_invert_b', False)
                d["mask_ao_distance_b"] = getattr(layer, 'mask_ao_distance_b', 0.5)
                d["mask_combine"]       = getattr(layer, 'mask_combine', 'MULTIPLY')
                d["mask_contrast"]      = getattr(layer, 'mask_contrast', 0.5)
                # Mask refinement — Levels
                d["use_mask_levels"]     = getattr(layer, 'use_mask_levels', False)
                d["mask_levels_in_min"]  = getattr(layer, 'mask_levels_in_min', 0.0)
                d["mask_levels_in_max"]  = getattr(layer, 'mask_levels_in_max', 1.0)
                d["mask_levels_gamma"]   = getattr(layer, 'mask_levels_gamma', 1.0)
                d["mask_levels_out_min"] = getattr(layer, 'mask_levels_out_min', 0.0)
                d["mask_levels_out_max"] = getattr(layer, 'mask_levels_out_max', 1.0)
                # Mask refinement — Softness + Blur
                d["mask_softness"]       = getattr(layer, 'mask_softness', 0.0)
                d["mask_blur"]           = getattr(layer, 'mask_blur', 0.0)
                # Smart generator parameters
                d["mask_gen_intensity"]     = getattr(layer, 'mask_gen_intensity', 1.0)
                d["mask_gen_breakup"]       = getattr(layer, 'mask_gen_breakup', 0.3)
                d["mask_gen_breakup_scale"] = getattr(layer, 'mask_gen_breakup_scale', 15.0)
                d["mask_gen_sharpness"]     = getattr(layer, 'mask_gen_sharpness', 0.5)
                # Image texture mapping config
                d["paint_interpolation"]    = getattr(layer, 'paint_interpolation', 'Linear')
                d["paint_projection"]       = getattr(layer, 'paint_projection', 'FLAT')
                d["paint_projection_blend"] = getattr(layer, 'paint_projection_blend', 0.3)
                d["paint_source"]           = getattr(layer, 'paint_source', 'FILE')
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
                d["normal_strength"]      = getattr(layer, 'normal_strength', 1.0)
                d["normal_tile_scale"]    = getattr(layer, 'normal_tile_scale', 1.0)
                d["normal_rotation"]      = getattr(layer, 'normal_rotation', 0.0)
                d["use_emission"]         = getattr(layer, 'use_emission', False)
                d["emission_image_name"]  = getattr(layer, 'emission_image_name', "")
                if getattr(layer, 'use_emission', False):
                    d["emission_color"]    = list(layer.emission_color)
                    d["emission_strength"] = layer.emission_strength
                # Selective emission
                d["emission_selector_type"]       = getattr(layer, 'emission_selector_type', 'NONE')
                d["emission_selector_scale"]      = getattr(layer, 'emission_selector_scale', 4.0)
                d["emission_selector_threshold"]  = getattr(layer, 'emission_selector_threshold', 0.3)
                d["emission_selector_seed"]       = getattr(layer, 'emission_selector_seed', 0.0)
                d["emission_selector_image_name"] = getattr(layer, 'emission_selector_image_name', "")
                d["use_transmission"]        = getattr(layer, 'use_transmission', False)
                d["transmission_fill"]       = getattr(layer, 'transmission_fill', 0.0)
                d["transmission_image_name"] = getattr(layer, 'transmission_image_name', "")
                d["use_alpha"]               = getattr(layer, 'use_alpha', False)
                d["alpha_fill"]              = getattr(layer, 'alpha_fill', 1.0)
                d["alpha_image_name"]        = getattr(layer, 'alpha_image_name', "")
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
