"""
TLM Showcase Preset — Galaxy Marble
=====================================

100% procedural — NO PAINT layers — black marble with swirling cosmic
veins in cyan/violet/gold. Showcases THREE under-used TLM features:

  1. **3-color ColorRamp** via `use_proc_color3 = True` + `proc_color3` +
     `proc_color3_position` — the procedural ramp now has three stops
     instead of the default two, so a single WAVE pattern can paint
     cyan → violet → gold veins from one Fac signal.
  2. **WAVE with heavy distortion** — `proc_distortion = 8.0` folds the
     sinusoidal bands into organic "nebula swirl" topology that looks
     hand-painted but is fully procedural.
  3. **Polished marble PBR** — roughness 0.18 with non-uniform variation
     (slightly rougher in the vein cores, mirror-glossy on the base),
     plus high metallic on the gold veins for a tasteful sparkle.

Visual anatomy:
  - Deep black marble base (charcoal, not pure black — keeps the
    Fresnel response visible)
  - Big organic vein structure from WAVE+distortion in cyan→violet→gold
  - Smaller crackled vein noise OVERLAY for "shattered nebula" detail
  - Tight cell variation via faint Voronoi for marble grain
  - Moderate gloss roughness (0.18) — polished surface, sharp reflections
  - Subtle microbump for the "stone feel" — too smooth would look plastic

Layer stack (7 layers):
  01. Marble Base FILL                — deep charcoal base
  02. Galaxy Veins WAVE (3-color)     — cyan/violet/gold cosmic veins
  03. Vein Detail NOISE OVERLAY       — small-scale nebula texture
  04. Marble Cells VORONOI MULTIPLY   — subtle stone grain
  05. Polish Roughness NOISE          → ROUGHNESS — polished with slight variation
  06. Vein Metal MAGIC                → METALLIC — gold/cyan veins get a sparkle
  07. Surface Microbump NOISE         → BUMP — barely-there stone texture
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Galaxy_Marble"
RESOLUTION = "1024"
TARGET_MESH = "TLM_GalaxyMarble"
SUBDIV_LEVEL = 3

# ── Marble base ──
BASE_COLOR             = (0.018, 0.020, 0.030, 1.0)     # near-black charcoal-violet
BASE_ROUGHNESS         = 0.18                            # polished
BASE_METALLIC          = 0.0

# ── Galaxy veins (WAVE+distortion, 3-color ramp) ──
VEIN_SCALE             = 0.9                             # LARGER vein patches → more "cosmic" less "tiger"
VEIN_DISTORTION        = 12.0                            # very heavy folds → maximum swirl
VEIN_DETAIL            = 6.0                             # moderate wave detail
VEIN_DETAIL_SCALE      = 1.0
VEIN_DETAIL_ROUGHNESS  = 0.55
VEIN_PHASE             = 0.5
VEIN_DIRECTION         = "DIAGONAL"                      # diagonal bands → not too symmetric

# Color1 (Fac=0) = DARK so low-Fac areas blend into the marble base via SCREEN.
# Color3 (mid) = rich violet for the transition zone.
# Color2 (Fac=1) = warm gold for the bright vein highlights.
# Result under SCREEN blend: dark stays dark, mid areas become violet,
# bright areas become gold → "black marble with violet-gold cosmic veins".
VEIN_CYAN              = (0.000, 0.000, 0.010, 1.0)      # near-black at low Fac
VEIN_VIOLET            = (0.380, 0.090, 0.560, 1.0)      # rich violet (mid Fac)
VEIN_GOLD              = (0.880, 0.580, 0.150, 1.0)      # warm gold (high Fac) — slightly dimmer
VEIN_COLOR3_POSITION   = 0.45                            # violet just before mid → wider violet band
VEIN_RAMP_CENTER       = 0.62                            # shift ramp toward high — more dark area
VEIN_CONTRAST          = 0.55                            # mid contrast
VEIN_OPACITY           = 0.75                            # 75% — dark base visible
VEIN_BLEND             = "SCREEN"                        # adds light to the dark base; dark regions stay dark

# ── Vein detail noise (small-scale nebula speckle) ──
# Lower opacity keeps it as subtle texture rather than a brightness boost.
DETAIL_SCALE           = 6.5
DETAIL_OPACITY         = 0.08
DETAIL_BLEND           = "OVERLAY"
DETAIL_COLOR_LO        = (0.10, 0.08, 0.18, 1.0)
DETAIL_COLOR_HI        = (0.55, 0.40, 0.80, 1.0)         # violet-leaning highlight

# ── Marble cells (subtle stone grain) ──
CELL_SCALE             = 5.5
CELL_RANDOMNESS        = 0.95
CELL_CONTRAST          = 0.20
CELL_OPACITY           = 0.20
CELL_BLEND             = "MULTIPLY"
CELL_DARK              = (0.85, 0.85, 0.90, 1.0)
CELL_LIGHT             = (1.00, 1.00, 1.00, 1.0)

# ── Polish roughness ──
POLISH_ROUGH_LO        = 0.10                            # mirror-clear at the cleanest
POLISH_ROUGH_HI        = 0.30                            # slightly rough in vein cores
POLISH_NOISE_SCALE     = 3.0

# ── Vein metallic (gold sparkle) ──
METAL_DEPTH            = 3                               # MAGIC turbulence depth
METAL_DISTORTION       = 2.5
METAL_SCALE            = 2.5
METAL_LO               = 0.0                             # non-metallic base
METAL_HI               = 0.85                            # near-fully metallic at hot spots

# ── Surface microbump ──
BUMP_SCALE             = 60.0
BUMP_STRENGTH          = 0.10                            # very subtle — marble is smooth
BUMP_DISTANCE          = 0.0010


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_mesh():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        return obj
    print(f"[TLM] Creating '{TARGET_MESH}' (subdivided ico sphere)…")
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_ico_sphere_add(radius=1.0, subdivisions=4, location=(0, 0, 1.0))
    obj = bpy.context.active_object
    obj.name = TARGET_MESH
    _subdivide(obj)
    return obj


def _subdivide(obj):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new(name="TLM_Subdiv", type='SUBSURF')
    mod.levels = SUBDIV_LEVEL
    mod.render_levels = SUBDIV_LEVEL
    try:
        with bpy.context.temp_override(active_object=obj, selected_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    try:
        with bpy.context.temp_override(active_object=obj, selected_objects=[obj]):
            bpy.ops.object.shade_smooth()
    except Exception:
        for p in obj.data.polygons:
            p.use_smooth = True


def _get_or_create_material(obj, name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    while obj.data.materials:
        obj.data.materials.pop(index=0)
    obj.data.materials.append(mat)
    obj.active_material_index = 0
    return mat


def _clear_layers(mat):
    tlm = mat.tlm
    while len(tlm.layers) > 0:
        tlm.layers.remove(len(tlm.layers) - 1)
    tlm.active_layer_index = 0


def _add_fill(mat, name, color, opacity=1.0, blend_mode="MIX",
              output_channel="BASE_COLOR"):
    _add_layer_common(bpy.context, "FILL")
    l = mat.tlm.layers[mat.tlm.active_layer_index]
    l.name = name
    l.fill_color = color
    l.opacity = opacity
    l.blend_mode = blend_mode
    l.output_channel = output_channel
    return l


def _add_proc(mat, name, proc_type, opacity=1.0, blend_mode="MIX",
              output_channel="BASE_COLOR"):
    _add_layer_common(bpy.context, "PROCEDURAL")
    l = mat.tlm.layers[mat.tlm.active_layer_index]
    l.name = name
    l.proc_type = proc_type
    l.opacity = opacity
    l.blend_mode = blend_mode
    l.output_channel = output_channel
    l.proc_coord_type = "OBJECT"
    return l


def _ensure_cycles():
    scene = bpy.context.scene
    if scene.render.engine != 'CYCLES':
        scene.render.engine = 'CYCLES'
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for sp in area.spaces:
                if sp.type == 'VIEW_3D' and sp.shading.type == 'SOLID':
                    sp.shading.type = 'MATERIAL'


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_galaxy_marble():
    _ensure_cycles()
    obj = _find_or_create_mesh()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Galaxy Marble v0.1 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Marble Base ───
    l_base = _add_fill(mat, "01 Marble Base", BASE_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = BASE_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = BASE_ROUGHNESS

    # ─── 02. Galaxy Veins (MARBLE = WAVE + NOISE distortion, 3-color ramp) ───
    # MARBLE produces organic swirly veins naturally (instead of WAVE's
    # straight bands). The wave underneath is distorted by noise turbulence,
    # making the veins look hand-painted. The 3-color ramp then maps the
    # Fac signal to a smooth dark→violet→gold gradient across the swirls.
    l_vein = _add_proc(mat, "02 Galaxy Veins", "MARBLE",
                       opacity=VEIN_OPACITY,
                       blend_mode=VEIN_BLEND,
                       output_channel="BASE_COLOR")
    l_vein.proc_scale = VEIN_SCALE
    l_vein.proc_marble_wave_type = "BANDS"
    l_vein.proc_marble_wave_profile = "SIN"
    l_vein.proc_marble_bands_direction = VEIN_DIRECTION
    l_vein.proc_detail = VEIN_DETAIL
    l_vein.proc_distortion = VEIN_DISTORTION
    l_vein.proc_marble_distortion = VEIN_DISTORTION

    # 3-color ColorRamp via proc_color3
    l_vein.proc_color1 = VEIN_CYAN                # Fac=0 → cyan
    l_vein.proc_color2 = VEIN_GOLD                # Fac=1 → gold
    l_vein.use_proc_color3 = True
    l_vein.proc_color3 = VEIN_VIOLET              # mid-ramp violet
    l_vein.proc_color3_position = VEIN_COLOR3_POSITION
    l_vein.proc_contrast = VEIN_CONTRAST
    l_vein.proc_ramp_center = VEIN_RAMP_CENTER    # shift ramp toward high Fac → more dark base visible

    # ─── 03. Vein Detail Noise (OVERLAY for nebula speckle) ───
    l_det = _add_proc(mat, "03 Vein Detail", "NOISE",
                      opacity=DETAIL_OPACITY,
                      blend_mode=DETAIL_BLEND,
                      output_channel="BASE_COLOR")
    l_det.proc_scale = DETAIL_SCALE
    l_det.proc_detail = 8.0
    l_det.proc_roughness_proc = 0.55
    l_det.proc_color1 = DETAIL_COLOR_LO
    l_det.proc_color2 = DETAIL_COLOR_HI
    l_det.proc_contrast = 0.30

    # ─── 04. Marble Cells (MULTIPLY for subtle grain) ───
    l_cells = _add_proc(mat, "04 Marble Cells", "VORONOI",
                        opacity=CELL_OPACITY,
                        blend_mode=CELL_BLEND,
                        output_channel="BASE_COLOR")
    l_cells.proc_voronoi_feature = "F1"
    l_cells.proc_voronoi_distance = "EUCLIDEAN"
    l_cells.proc_scale = CELL_SCALE
    l_cells.proc_randomness = CELL_RANDOMNESS
    l_cells.proc_color1 = CELL_LIGHT
    l_cells.proc_color2 = CELL_DARK
    l_cells.proc_contrast = CELL_CONTRAST

    # ─── 05. Polish Roughness (primary → ROUGHNESS) ───
    l_rough = _add_proc(mat, "05 Polish Roughness", "NOISE",
                        opacity=1.0,
                        blend_mode="MIX",
                        output_channel="ROUGHNESS")
    l_rough.proc_scale = POLISH_NOISE_SCALE
    l_rough.proc_detail = 5.0
    l_rough.proc_roughness_proc = 0.55
    l_rough.proc_color1 = (POLISH_ROUGH_LO, POLISH_ROUGH_LO, POLISH_ROUGH_LO, 1.0)
    l_rough.proc_color2 = (POLISH_ROUGH_HI, POLISH_ROUGH_HI, POLISH_ROUGH_HI, 1.0)
    l_rough.proc_contrast = 0.25

    # ─── 06. Vein Metallic (MAGIC for gold sparkle on veins) ───
    l_metal = _add_proc(mat, "06 Vein Metallic", "MAGIC",
                        opacity=1.0,
                        blend_mode="MIX",
                        output_channel="METALLIC")
    l_metal.proc_scale = METAL_SCALE
    l_metal.proc_magic_depth = METAL_DEPTH
    l_metal.proc_magic_distortion = METAL_DISTORTION
    l_metal.proc_color1 = (METAL_LO, METAL_LO, METAL_LO, 1.0)
    l_metal.proc_color2 = (METAL_HI, METAL_HI, METAL_HI, 1.0)
    l_metal.proc_contrast = 0.45

    # ─── 07. Surface Microbump ───
    # IMPORTANT: opacity=0.0 prevents the BLACK proc colors from contributing
    # to the base_color chain (MIX with opacity=1 + BLACK = full black
    # replace). The bump contribution flows through `use_bump=True` and is
    # independent of opacity for the bump channel.
    l_bump = _add_proc(mat, "07 Surface Microbump", "NOISE",
                       opacity=0.0,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_bump.proc_scale = BUMP_SCALE
    l_bump.proc_detail = 4.0
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_contrast = 0.45
    l_bump.use_bump = True
    l_bump.bump_strength = BUMP_STRENGTH
    l_bump.bump_distance = BUMP_DISTANCE

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Galaxy Marble built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_galaxy_marble()
