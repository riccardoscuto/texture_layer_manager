"""
TLM Hero Preset - Rocky Chunky Pile (v2 - cobblestone)
=======================================================

Showcase for *real geometric* Displacement, redesigned to match the
"Stone + Dirt" cobblestone reference (two-shader Mix Shader pattern
replicated via TLM layer routing).

Architecture mirror of the reference node graph:
  * Reference uses two PrincipledBSDFs (Stone + Dirt) mixed by a
    Voronoi DTE mask, both feeding the same Voronoi-driven Displacement.
  * We get the same look from a single PrincipledBSDF using TLM's
    cumulative layer routing:
      - "Stone" branch  = base FILL + per-cell random Voronoi tint
      - "Dirt"  branch  = Voronoi DTE overlay (MULTIPLY) darkening cracks
      - "Moss"  branch  = subtle green tint in some recesses
      - "Disp"  branch  = the SAME Voronoi F1 cells push out as pebbles
      - "Bump"  branch  = Voronoi DTE crack network + high-freq grain

Key reference tricks replicated:
  * Voronoi F1 with `proc_voronoi_random_color` gives each pebble its
    own colour pulled from the ColorRamp - same effect the reference
    achieves with its Stone Color 1/2/3 inputs.
  * Voronoi DTE at the SAME scale as F1 makes the dirt valleys land
    exactly between pebble crowns - no spatial drift.
  * Per-layer displacement_scale + tlm.displacement_strength control
    pebble height; midlevel=0.5 keeps the dirt sitting at mesh level
    while pebbles push out.

Layer stack (bottom -> top):
  01. Stone Base Fill        - medium stone color anchor
  02. Stone Color Random     - per-cell Voronoi color variation
  03. Dirt Crack Overlay     - dark crevices via Voronoi DTE
  04. Moss Tint              - subtle olive in deep recesses
  05. MACRO Displacement     - same Voronoi F1 = silhouette pebbles
  06. MESO Displacement      - Noise for organic pebble shape
  07. Stone Bump Cracks      - Voronoi DTE crack detail (shading only)
  08. Stone Bump Grain       - high-freq Noise pits
  09. Roughness Cracks       - rougher in crack valleys
  10. Roughness Stone Variation - per-pebble roughness scatter

Material-level:
  tlm.use_displacement       = True   (master switch)
  tlm.displacement_strength  = 0.18   (Cycles Displacement node Scale)
  tlm.displacement_midlevel  = 0.5
  tlm.displacement_adaptive  = True

Cycles required for visible silhouette break (EEVEE will still render
the bump shading but won't move vertices).
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


MATERIAL_NAME = "TLM_Hero_Rocky_Chunky_Pile"
RESOLUTION = "1024"
USE_SELECTED_MESH = True
PREVIEW_OBJECT_NAME = "TLM_Hero_Rocky_Chunky_Pile_Icosphere"


# --- Palette ---------------------------------------------------------------
# Dark-basalt cobblestone palette - matches the reference render which
# is dominated by near-black stones, warm brown earth wedged between
# them, and sparse olive moss accents. Stones use very low roughness
# (0.22) + high IOR (1.65) for crisp white specular highlights on tops.
STONE_BASE     = (0.030, 0.027, 0.024, 1.0)   # near-black anchor
STONE_VAR_A    = (0.060, 0.050, 0.040, 1.0)   # slightly brighter dark
STONE_VAR_B    = (0.020, 0.017, 0.014, 1.0)   # darkest near-pure-black
STONE_VAR_C    = (0.045, 0.030, 0.018, 1.0)   # warm brown stone variant
DIRT_COLOR     = (0.20, 0.115, 0.055, 1.0)    # warm earth between stones
MOSS_COLOR     = (0.13, 0.21, 0.05, 1.0)      # olive moss accent
BLACK          = (0.0, 0.0, 0.0, 1.0)
WHITE          = (1.0, 1.0, 1.0, 1.0)


# Voronoi cell scale shared across colour + displacement layers.
# Same scale = the colour cells line up with the displacement pebbles.
COBBLE_SCALE = 10.0


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
                 contrast=0.5, center=0.5, distance="EUCLIDEAN",
                 random_color=False, random_seed=0.0):
    layer.proc_voronoi_feature = feature
    layer.proc_voronoi_distance = distance
    layer.proc_scale = scale
    layer.proc_randomness = randomness
    layer.proc_contrast = contrast
    layer.proc_ramp_center = center
    layer.proc_voronoi_random_color = random_color
    layer.proc_voronoi_random_seed = random_seed


def build_rocky_chunky_pile():
    obj = _active_mesh_or_preview_icosphere()
    mat = _material_on_object(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    tlm.shader_editable = False
    _clear_layers(mat)

    # ── 01. Stone base fill --------------------------------------------
    # Near-black anchor + LOW roughness (glossy stone) so the specular
    # highlights on stone tops read crisp like the reference render.
    base = _add_fill(mat, "01 Stone Base Fill", STONE_BASE)
    base.use_roughness = True
    base.roughness_fill = 0.22       # low for crisp white highlights
    base.use_metallic = True
    base.metallic_fill = 0.0

    # ── 02. Stone color random (per-cell Voronoi) ---------------------
    # KEY trick: random_color=True makes each Voronoi cell pick a discrete
    # colour from the 3-stop ramp. Combined with the same cell layout used
    # by MACRO Displacement (layer 05), each pebble that pushes out gets
    # its own colour - identical to the reference's Stone Color 1/2/3.
    stone_col = _add_proc(mat, "02 Stone Color Random", "VORONOI",
                          output="BASE_COLOR", blend="MIX", opacity=0.95)
    _set_voronoi(stone_col, feature="F1", scale=COBBLE_SCALE,
                 randomness=1.0, contrast=0.0, center=0.5,
                 random_color=True, random_seed=12.0)
    stone_col.proc_color1 = STONE_VAR_A
    stone_col.proc_color2 = STONE_VAR_B
    stone_col.proc_use_color3 = True
    stone_col.proc_color3 = STONE_VAR_C

    # ── 03. Dirt crack overlay (DIRT mask) ---------------------------
    # Solid warm brown gated by TLM's built-in DIRT mask (AO-based cavity
    # detection × noise grunge). With displaced geometry, AO cavities land
    # naturally in the gaps between pebbles. mask_ao_distance=0.06 keeps
    # the coverage tight so dirt doesn't bleed onto stone faces.
    dirt = _add_proc(mat, "03 Dirt Crack Overlay", "VORONOI",
                     output="BASE_COLOR", blend="MIX", opacity=0.85)
    _set_voronoi(dirt, feature="DISTANCE_TO_EDGE", scale=COBBLE_SCALE,
                 randomness=1.0, contrast=0.0, center=0.5)
    dirt.proc_color1 = DIRT_COLOR
    dirt.proc_color2 = DIRT_COLOR    # uniform brown — the MASK gates it
    dirt.use_mask = True
    dirt.mask_source = 'DIRT'
    dirt.mask_ao_distance = 0.06     # tight crack-only coverage

    # ── 04. Moss tint (DIRT mask, sparse) ----------------------------
    # Same DIRT mask but lower opacity → only the deepest cavities
    # get the olive moss accent.
    moss = _add_proc(mat, "04 Moss Tint", "VORONOI",
                     output="BASE_COLOR", blend="OVERLAY", opacity=0.60)
    _set_voronoi(moss, feature="DISTANCE_TO_EDGE",
                 scale=COBBLE_SCALE * 0.7, randomness=1.0,
                 contrast=0.0, center=0.5, random_seed=73.0)
    moss.proc_color1 = MOSS_COLOR
    moss.proc_color2 = MOSS_COLOR
    moss.use_mask = True
    moss.mask_source = 'DIRT'
    moss.mask_ao_distance = 0.06

    # ── 05. MACRO Displacement (Voronoi F1) --------------------------
    # SAME scale as layer 02 ensures each colour cell IS a physical
    # pebble. contrast=0.30 + ramp_center=0.55 sharpens the cliff edges
    # between stones so they read as distinct 3D pebbles (not soft mounds).
    disp_macro = _add_proc(mat, "05 MACRO Displacement", "VORONOI",
                           output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_voronoi(disp_macro, feature="F1", scale=COBBLE_SCALE,
                 randomness=1.0, contrast=0.30, center=0.55)
    disp_macro.proc_color1 = WHITE   # cell centre → peak
    disp_macro.proc_color2 = BLACK   # cell edge → valley
    disp_macro.use_displacement = True
    disp_macro.displacement_scale = 1.0

    # ── 06. MESO Displacement (Noise lump) ---------------------------
    # Light noise modulation on top of MACRO so pebbles don't all look
    # like identical Voronoi crystals. Kept low (0.25) so the cliff
    # edges between stones stay sharp.
    disp_meso = _add_proc(mat, "06 MESO Displacement", "NOISE",
                          output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_noise(disp_meso, scale=18.0, detail=4.0, roughness=0.55,
               lacunarity=2.0, distortion=0.15,
               contrast=0.25, center=0.50)
    disp_meso.proc_color1 = BLACK
    disp_meso.proc_color2 = WHITE
    disp_meso.use_displacement = True
    disp_meso.displacement_scale = 0.25

    # ── 07. Stone bump cracks (Voronoi DTE) --------------------------
    # Shading-only crack relief on top of the displaced silhouette.
    # Strength kept moderate (0.45) so highlights survive on stone tops.
    bump_cracks = _add_proc(mat, "07 Stone Bump Cracks", "VORONOI",
                            output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_voronoi(bump_cracks, feature="DISTANCE_TO_EDGE",
                 scale=COBBLE_SCALE, randomness=1.0,
                 contrast=0.95, center=0.12)
    bump_cracks.proc_color1 = BLACK
    bump_cracks.proc_color2 = WHITE
    bump_cracks.use_bump = True
    bump_cracks.bump_strength = 0.45
    bump_cracks.bump_distance = 0.040

    # ── 08. Stone bump grain (high-freq Noise) -----------------------
    # Very subtle micro-grain so close-ups don't read as polished glass.
    bump_grain = _add_proc(mat, "08 Stone Bump Grain", "NOISE",
                           output="BASE_COLOR", blend="MIX", opacity=0.0)
    _set_noise(bump_grain, scale=90.0, detail=8.0, roughness=0.55,
               lacunarity=2.0, distortion=0.0,
               contrast=0.45, center=0.50)
    bump_grain.proc_color1 = BLACK
    bump_grain.proc_color2 = WHITE
    bump_grain.use_bump = True
    bump_grain.bump_strength = 0.10
    bump_grain.bump_distance = 0.004

    # ── 09. Roughness cracks ----------------------------------------
    # Dust-filled cracks are matter than glossy stone faces.
    rough_cracks = _add_proc(mat, "09 Roughness Cracks", "VORONOI",
                             output="ROUGHNESS", blend="MIX", opacity=0.95)
    _set_voronoi(rough_cracks, feature="DISTANCE_TO_EDGE",
                 scale=COBBLE_SCALE, randomness=1.0,
                 contrast=0.95, center=0.10)
    rough_cracks.proc_color1 = (0.85, 0.85, 0.85, 1.0)  # cracks → rougher
    rough_cracks.proc_color2 = (0.22, 0.22, 0.22, 1.0)  # stone face matches base

    # ── 10. Roughness per-pebble variation -------------------------
    # Per-cell random roughness in a tight low range so highlights vary
    # slightly stone-to-stone but no pebble becomes fully matte.
    rough_var = _add_proc(mat, "10 Roughness Stone Variation", "VORONOI",
                          output="ROUGHNESS", blend="OVERLAY", opacity=0.45)
    _set_voronoi(rough_var, feature="F1", scale=COBBLE_SCALE,
                 randomness=1.0, contrast=0.0, center=0.5,
                 random_color=True, random_seed=12.0)
    rough_var.proc_color1 = (0.15, 0.15, 0.15, 1.0)
    rough_var.proc_color2 = (0.32, 0.32, 0.32, 1.0)

    # ── Material-level: IOR + displacement master --------------------
    # IOR 1.65 (above 1.45 default) for stronger Fresnel — the white
    # specular highlights the reference shows on stone tops need
    # punchier specular reflection than the default IOR gives.
    tlm.bsdf_ior = 1.65
    tlm.use_displacement = True
    tlm.displacement_strength = 0.42
    tlm.displacement_midlevel = 0.5
    tlm.displacement_adaptive = True

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print("")
    print("[TLM] Hero Rocky Chunky Pile v2 (cobblestone) built.")
    print(f"Object:   {obj.name}")
    print(f"Material: {mat.name}")
    print(f"Cobble scale: {COBBLE_SCALE}  (raise = smaller more pebbles)")
    print("Render in CYCLES (rendered viewport) to see silhouette break.")
    print("Tuning:")
    print("  - For tighter pebbles: raise COBBLE_SCALE to 12-14 + bump strength.")
    print("  - For mossier look: raise layer 04 'Moss Tint' opacity to 0.45-0.55.")
    print("  - For deeper dirt: raise layer 03 'Dirt Crack Overlay' opacity to 0.95.")
    print("  - For more dramatic silhouette: tlm.displacement_strength 0.25-0.30.")


if __name__ == "__main__":
    build_rocky_chunky_pile()
