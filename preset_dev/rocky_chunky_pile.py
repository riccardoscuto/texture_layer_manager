"""
TLM Hero Preset - Rocky Chunky Pile
====================================

Showcase for *real geometric* Displacement (TLM v0.5.0 feature).

Unlike every other rock-ish TLM hero (burnt_sand_rock, cracked_lava_crust)
this one is built around the new ``mat.tlm.use_displacement`` + per-layer
``use_displacement`` pipeline: the silhouette of the mesh actually breaks
into chunky shapes. Bump-only rocks always read as a flat ball with
"painted" relief; Cycles adaptive subdivision + displacement turns the
sphere into a believable pile of rubble.

Architecture (bottom -> top):
  01. Rock Base Fill           - dark grey-brown anchor + base roughness
  02. Tonal Macro Noise        - large iron-oxide tint variation
  03. Color Voronoi Cracks     - dark crack lines into base color
  04. MACRO Displacement       - Voronoi F1 boulder chunks (silhouette)
  05. MESO Displacement        - Noise lumps on top of macro chunks
  06. Macro Bump (Voronoi DTE) - fake-relief crack network (cheap detail)
  07. Micro Bump (Noise hi-f)  - surface micropits
  08. Roughness Cracks         - darker (rougher) in cracks
  09. Roughness Grain          - fine variation

Material-level:
  tlm.use_displacement       = True   (master switch)
  tlm.displacement_strength  = 0.12   (Cycles Displacement node Scale)
  tlm.displacement_midlevel  = 0.5
  tlm.displacement_adaptive  = True   (auto-add Subsurf + adaptive)

Why two displacement layers (MACRO + MESO):
  - MACRO is low-frequency Voronoi: it makes the BIG chunks that break
    the silhouette. Without it the rock reads as smooth-with-relief.
  - MESO is medium-frequency Noise added on top of MACRO: it gives the
    chunks themselves organic shape instead of crystalline Voronoi cells.
  - The two heights ADD (cumulative chain, same as Bump). The combined
    height feeds into a single ShaderNodeDisplacement.

The viewport will *not* show vector displacement in EEVEE; you need to
switch to Cycles (rendered viewport) to see the silhouette break. The
script does NOT switch the engine for you - it just configures the
material so it's ready to render in Cycles.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


MATERIAL_NAME = "TLM_Hero_Rocky_Chunky_Pile"
RESOLUTION = "1024"
USE_SELECTED_MESH = True
PREVIEW_OBJECT_NAME = "TLM_Hero_Rocky_Chunky_Pile_Icosphere"


# --- Palette ---------------------------------------------------------------
# Dark, cool, slightly warm-shifted granite-ish rock.
ROCK_BASE  = (0.150, 0.130, 0.115, 1.0)   # base fill
ROCK_LIGHT = (0.320, 0.280, 0.235, 1.0)   # raised tonal patches
ROCK_TINT  = (0.380, 0.260, 0.180, 1.0)   # warm iron-oxide tint
ROCK_CRACK = (0.040, 0.035, 0.030, 1.0)   # deep crack interior
BLACK      = (0.0, 0.0, 0.0, 1.0)
WHITE      = (1.0, 1.0, 1.0, 1.0)


def _active_mesh_or_preview_icosphere():
    obj = bpy.context.active_object
    if USE_SELECTED_MESH and obj and obj.type == "MESH":
        return obj

    existing = bpy.data.objects.get(PREVIEW_OBJECT_NAME)
    if existing and existing.type == "MESH":
        bpy.ops.object.select_all(action="DESELECT")
        existing.select_set(True)
        bpy.context.view_layer.objects.active = existing
        return existing

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=1.0)
    obj = bpy.context.active_object
    obj.name = PREVIEW_OBJECT_NAME
    try:
        bpy.ops.object.shade_smooth()
    except Exception:
        try:
            for poly in obj.data.polygons:
                poly.use_smooth = True
        except Exception:
            pass
    return obj


def _material_on_object(obj, name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj.active_material_index = 0
    bpy.context.view_layer.objects.active = obj
    return mat


def _clear_layers(mat):
    tlm = mat.tlm
    while len(tlm.layers):
        tlm.layers.remove(len(tlm.layers) - 1)
    tlm.active_layer_index = 0


def _add_fill(mat, name, color, output="BASE_COLOR", blend="MIX", opacity=1.0):
    _add_layer_common(bpy.context, "FILL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.fill_color = color
    layer.output_channel = output
    layer.blend_mode = blend
    layer.opacity = opacity
    return layer


def _add_proc(mat, name, proc_type="NOISE", output="BASE_COLOR",
              blend="MIX", opacity=1.0):
    _add_layer_common(bpy.context, "PROCEDURAL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.layer_type = "PROCEDURAL"
    layer.proc_type = proc_type
    layer.output_channel = output
    layer.blend_mode = blend
    layer.opacity = opacity
    layer.proc_coord_type = "OBJECT"
    layer.proc_mapping_type = "POINT"
    return layer


def _set_noise(layer, scale, detail, roughness, lacunarity=2.0,
               distortion=0.0, contrast=0.5, center=0.5):
    layer.proc_scale = scale
    layer.proc_detail = detail
    layer.proc_roughness_proc = roughness
    layer.proc_lacunarity = lacunarity
    layer.proc_distortion = distortion
    layer.proc_contrast = contrast
    layer.proc_ramp_center = center


def _set_voronoi(layer, feature, scale, randomness=1.0,
                 contrast=0.5, center=0.5, distance="EUCLIDEAN"):
    layer.proc_voronoi_feature = feature
    layer.proc_voronoi_distance = distance
    layer.proc_scale = scale
    layer.proc_randomness = randomness
    layer.proc_contrast = contrast
    layer.proc_ramp_center = center


def build_rocky_chunky_pile():
    obj = _active_mesh_or_preview_icosphere()
    mat = _material_on_object(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    tlm.shader_editable = False
    _clear_layers(mat)

    # ── 01. Base fill ----------------------------------------------------
    # Sets the base color + anchors roughness (no metallic, dielectric rock).
    base = _add_fill(mat, "01 Rock Base Fill", ROCK_BASE)
    base.use_roughness = True
    base.roughness_fill = 0.88
    base.use_metallic = True
    base.metallic_fill = 0.0

    # ── 02. Tonal macro variation ----------------------------------------
    # Big lazy iron-oxide tint blobs so the rock isn't monotone.
    tint = _add_proc(mat, "02 Tonal Macro Noise", "NOISE",
                     output="BASE_COLOR", blend="OVERLAY", opacity=0.45)
    _set_noise(tint, scale=4.0, detail=2.0, roughness=0.50,
               lacunarity=2.0, distortion=0.20,
               contrast=0.25, center=0.55)
    tint.proc_color1 = ROCK_BASE
    tint.proc_color2 = ROCK_TINT

    # ── 03. Color cracks (Voronoi DTE) -----------------------------------
    # Distance-to-edge gives narrow dark cracks between cells.
    cracks_col = _add_proc(mat, "03 Color Voronoi Cracks", "VORONOI",
                           output="BASE_COLOR", blend="MULTIPLY", opacity=0.55)
    _set_voronoi(cracks_col, feature="DISTANCE_TO_EDGE", scale=8.0,
                 randomness=1.0, contrast=0.85, center=0.18)
    # MULTIPLY: white = no effect, dark = crack ink
    cracks_col.proc_color1 = ROCK_CRACK
    cracks_col.proc_color2 = WHITE

    # ── 04. MACRO Displacement (Voronoi F1) ------------------------------
    # The silhouette breaker. Voronoi F1 = distance-to-cell-center, which
    # makes nice rounded chunks (cell centers = peaks, cell edges = valleys).
    # Low scale = few large chunks = obvious silhouette breaks.
    disp_macro = _add_proc(mat, "04 MACRO Displacement", "VORONOI",
                           output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_voronoi(disp_macro, feature="F1", scale=3.5, randomness=1.0,
                 contrast=0.0, center=0.5)
    # Height ramp: dark in valleys, bright on peaks.
    disp_macro.proc_color1 = WHITE   # F1=0 (cell center) → peak
    disp_macro.proc_color2 = BLACK   # F1 high (cell edge) → valley
    disp_macro.use_displacement = True
    disp_macro.displacement_scale = 1.0

    # ── 05. MESO Displacement (Noise) ------------------------------------
    # Adds organic lumps on top of macro chunks so they don't look like
    # crystalline Voronoi cells.
    disp_meso = _add_proc(mat, "05 MESO Displacement", "NOISE",
                          output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_noise(disp_meso, scale=12.0, detail=4.0, roughness=0.55,
               lacunarity=2.0, distortion=0.10,
               contrast=0.30, center=0.50)
    disp_meso.proc_color1 = BLACK
    disp_meso.proc_color2 = WHITE
    disp_meso.use_displacement = True
    disp_meso.displacement_scale = 0.50

    # ── 06. Macro Bump (Voronoi DTE) -------------------------------------
    # Reuses crack texture as bump. Bump is cheap and adds shading-only
    # detail on top of the displaced silhouette - perfect for fine cracks.
    bump_cracks = _add_proc(mat, "06 Macro Bump Cracks", "VORONOI",
                            output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_voronoi(bump_cracks, feature="DISTANCE_TO_EDGE", scale=8.0,
                 randomness=1.0, contrast=0.85, center=0.18)
    bump_cracks.proc_color1 = BLACK
    bump_cracks.proc_color2 = WHITE
    bump_cracks.use_bump = True
    bump_cracks.bump_strength = 0.55
    bump_cracks.bump_distance = 0.030

    # ── 07. Micro Bump (Noise high-frequency) ----------------------------
    # Surface micropits / grain. Keeps the rock from looking polished
    # when the camera gets close.
    bump_micro = _add_proc(mat, "07 Micro Bump", "NOISE",
                           output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_noise(bump_micro, scale=80.0, detail=8.0, roughness=0.55,
               lacunarity=2.0, distortion=0.0,
               contrast=0.40, center=0.50)
    bump_micro.proc_color1 = BLACK
    bump_micro.proc_color2 = WHITE
    bump_micro.use_bump = True
    bump_micro.bump_strength = 0.25
    bump_micro.bump_distance = 0.005

    # ── 08. Roughness from cracks ----------------------------------------
    # Cracks are slightly rougher than the polished face.
    rough_cracks = _add_proc(mat, "08 Roughness Cracks", "VORONOI",
                             output="ROUGHNESS", blend="MIX", opacity=0.55)
    _set_voronoi(rough_cracks, feature="DISTANCE_TO_EDGE", scale=8.0,
                 randomness=1.0, contrast=0.85, center=0.18)
    # Output 0..1, ROUGHNESS expects 0=glossy, 1=matte.
    rough_cracks.proc_color1 = (1.0, 1.0, 1.0, 1.0)   # cracks → rough
    rough_cracks.proc_color2 = (0.78, 0.78, 0.78, 1.0)  # body slightly less

    # ── 09. Roughness grain ----------------------------------------------
    rough_grain = _add_proc(mat, "09 Roughness Grain", "NOISE",
                            output="ROUGHNESS", blend="ADD", opacity=0.08)
    _set_noise(rough_grain, scale=40.0, detail=8.0, roughness=0.50,
               lacunarity=2.0, distortion=0.0,
               contrast=0.20, center=0.50)
    rough_grain.proc_color1 = BLACK
    rough_grain.proc_color2 = WHITE

    # ── Material-level displacement master switch -----------------------
    tlm.use_displacement = True
    tlm.displacement_strength = 0.12
    tlm.displacement_midlevel = 0.5
    tlm.displacement_adaptive = True

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print("")
    print("[TLM] Hero Rocky Chunky Pile material built.")
    print(f"Object:   {obj.name}")
    print(f"Material: {mat.name}")
    print("Render in CYCLES (rendered viewport) to see silhouette break.")
    print("Tuning:")
    print("  - Lower tlm.displacement_strength if chunks are too aggressive.")
    print("  - Raise layer 04 'MACRO Displacement' scale for more, smaller chunks.")
    print("  - Lower layer 04 displacement_scale if peaks read too tall.")


if __name__ == "__main__":
    build_rocky_chunky_pile()
