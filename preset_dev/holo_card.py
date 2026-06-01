"""
TLM Showcase Preset — Holographic Trading Card (IMPORTED IMAGE + holo foil)
===========================================================================

An imported image (the card art) overlaid with a holographic rainbow-foil
sheen — the look of a Pokémon/sports "holo" card catching the light. The
STAR is the user's imported image; the procedural layers add the foil.

This is the bread-and-butter TLM pitch: drop in an image, then layer
procedural effects on top to turn it into a finished, sellable material.

★ FOIL PATTERN MENU ★
Five interchangeable foil patterns ship as separate layers. All but the
first are HIDDEN by default (eye toggle off) — the user just clicks the
eye on whichever they like:

    Foil A · Diagonal       ← ON by default (classic linear holo)
    Foil B · Counter-Diag   ← enable WITH A → cross-hatch / diamond foil
    Foil C · Vertical       ← tight vertical "lenticular" lines
    Foil D · Rings (CD)      ← concentric "compact-disc / cosmos" holo
    Foil E · Spiral          ← swirled rings → spiral-vortex foil

Every pattern shares:
  • the SAME 7-stop spectral rainbow ramp,
  • proc_uv_view_shift (camera-space-normal parallax) so the bands SLIDE
    as the card is reoriented — a real, view-dependent foil, not a print.
On top sits a FRESNEL hue-shift layer (the whole card's tint marches
through the spectrum with the viewing angle) + a microbump that breaks
the Fresnel into scintillating specks.

Layer stack (9):
  01. Card Art PAINT (image)         — BASE_COLOR, glossy laminate
  02. Foil A · Diagonal  ADD  (ON)   — view-shift rainbow stripes
  03. Foil B · Counter-Diag  (hidden)
  04. Foil C · Vertical      (hidden)
  05. Foil D · Rings (CD)     (hidden)
  06. Foil E · Radial Sweep   (hidden)
  07. Foil Hue Shift FRESNEL OVERLAY — view-dependent hue march
  08. Holo Sparkle NOISE SCREEN      — fine glitter
  09. Foil Microbump → BUMP          — scintillation
"""

import bpy
from math import radians

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Holo_Card"
RESOLUTION = "1024"
TARGET_MESH = "TLM_HoloCard"
CARD_IMAGE = "led_screen_demo_01312025"     # imported art — swap freely
CARD_LOC = (0.0, 0.0, 1.2)
CARD_SCALE = (1.05, 1.42, 1.05)             # 5:7 card aspect (X=width, Y=height post-90°X-rot)
CARD_ROT_DEG = (90.0, 0.0, 28.0)            # face camera + 28° yaw

# ── Card art (L01) ──
CARD_ROUGHNESS    = 0.09        # glossy lamination
CARD_METALLIC     = 0.0

# ── Foil pattern variants (shared params) ──
FOIL_OPACITY      = 0.22        # per-pattern; ADD blend (stack a couple → brighter)
FOIL_VIEW_SHIFT   = 0.50        # camera-space-normal parallax → bands slide on rotate
# (name, kind, visible-by-default)
FOIL_VARIANTS = [
    ("02 Foil A - Diagonal",     'DIAG',     True),
    ("03 Foil B - Counter-Diag", 'DIAG_REV', False),
    ("04 Foil C - Vertical",     'VERT',     False),
    ("05 Foil D - Rings (CD)",   'RINGS',    False),
    ("06 Foil E - Spiral",       'SPIRAL',   False),
]

# ── Fresnel hue shift (global view-dependent tint) ──
FOIL_FRES_IOR        = 1.45
FOIL_FRES_OPACITY    = 0.32
FOIL_FRES_BLEND      = "OVERLAY"   # shifts hue, doesn't blow out values
FOIL_FRES_CONTRAST   = 0.50
FOIL_FRES_RAMP_CENT  = 0.50

# bright spectral rainbow (shared by every foil layer)
HOLO_RED          = (1.00, 0.10, 0.20, 1.0)   # 0.00 (color1)
HOLO_ORANGE       = (1.00, 0.55, 0.05, 1.0)   # 0.17
HOLO_YELLOW       = (0.95, 0.95, 0.10, 1.0)   # 0.34 (color3)
HOLO_GREEN        = (0.10, 0.95, 0.30, 1.0)   # 0.50
HOLO_CYAN         = (0.05, 0.85, 1.00, 1.0)   # 0.66
HOLO_BLUE         = (0.20, 0.30, 1.00, 1.0)   # 0.83
HOLO_VIOLET       = (0.75, 0.15, 1.00, 1.0)   # 1.00 (color2)

# ── Sparkle (NOISE SCREEN) ──
SPARK_SCALE       = 60.0
SPARK_OPACITY     = 0.06
SPARK_CONTRAST    = 0.92


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_card():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is None or obj.type != 'MESH':
        for o in bpy.context.scene.objects:
            o.select_set(False)
        bpy.ops.mesh.primitive_plane_add(size=2.0, location=CARD_LOC)
        obj = bpy.context.active_object
        obj.name = TARGET_MESH
    obj.location = CARD_LOC
    obj.scale = CARD_SCALE
    obj.rotation_euler = tuple(radians(a) for a in CARD_ROT_DEG)
    return obj


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


def _reset_material_flags(tlm):
    for attr, val in (("use_emission_output", False),
                      ("use_volume_absorption", False),
                      ("use_volume_scatter", False),
                      ("use_displacement", False),
                      ("bsdf_ior", 1.45)):
        if hasattr(tlm, attr):
            try: setattr(tlm, attr, val)
            except Exception: pass


def _add_paint(mat, name, image_name, output_channel="BASE_COLOR"):
    _add_layer_common(bpy.context, "PAINT")
    l = mat.tlm.layers[mat.tlm.active_layer_index]
    l.name = name
    l.image_name = image_name
    l.output_channel = output_channel
    l.paint_interpolation = 'Linear'
    l.paint_extension = 'EXTEND'
    return l


def _add_proc(mat, name, proc_type, opacity=1.0, blend_mode="MIX",
              output_channel="BASE_COLOR", coord="OBJECT"):
    _add_layer_common(bpy.context, "PROCEDURAL")
    l = mat.tlm.layers[mat.tlm.active_layer_index]
    l.name = name
    l.proc_type = proc_type
    l.opacity = opacity
    l.blend_mode = blend_mode
    l.output_channel = output_channel
    l.proc_coord_type = coord
    return l


def _add_stop(layer, color, position):
    s = layer.proc_extra_color_stops.add()
    s.color = color
    s.position = position
    return s


def _apply_holo_ramp(l):
    """7-stop spectral rainbow + view-driven parallax — shared by all foils."""
    l.proc_use_manual_stops = True
    l.proc_color1 = HOLO_RED
    l.proc_color2 = HOLO_VIOLET
    l.use_proc_color3 = True
    l.proc_color3 = HOLO_YELLOW
    l.proc_color3_position = 0.34
    _add_stop(l, HOLO_ORANGE, 0.17)
    _add_stop(l, HOLO_GREEN,  0.50)
    _add_stop(l, HOLO_CYAN,   0.66)
    _add_stop(l, HOLO_BLUE,   0.83)
    l.proc_uv_view_shift = FOIL_VIEW_SHIFT


def _add_foil_variant(mat, name, kind, visible):
    """One interchangeable foil pattern. Hidden ones are skipped by the
    compositor (visible=False) until the user toggles the eye on."""
    l = _add_proc(mat, name, "WAVE", opacity=FOIL_OPACITY,
                  blend_mode="ADD", output_channel="BASE_COLOR", coord="UV")
    l.proc_wave_profile = "SAW"          # each cycle = one full spectrum sweep
    if kind == 'DIAG':
        l.proc_wave_type = "BANDS"
        l.proc_wave_bands_direction = "DIAGONAL"
        l.proc_scale = 3.5
    elif kind == 'DIAG_REV':
        l.proc_wave_type = "BANDS"
        l.proc_wave_bands_direction = "DIAGONAL"
        l.proc_scale = 3.5
        l.proc_mapping_scale_x = -1.0    # mirror X → the OTHER diagonal
    elif kind == 'VERT':
        l.proc_wave_type = "BANDS"
        l.proc_wave_bands_direction = "X"
        l.proc_scale = 6.0
    elif kind == 'RINGS':
        l.proc_wave_type = "RINGS"
        l.proc_wave_rings_direction = "Z"
        l.proc_scale = 5.0
    elif kind == 'SPIRAL':
        # straight BANDS twisted by a SWIRL coord transform → spiral arms.
        # (SWIRL rotates XY by amount*radius; it only bends a pattern that
        #  has ANGULAR variation — bands, not radially-symmetric rings.)
        l.proc_wave_type = "BANDS"
        l.proc_wave_bands_direction = "X"
        l.proc_scale = 4.0
        l.proc_coord_transform = "SWIRL"
        l.proc_swirl_amount = 12.0
    _apply_holo_ramp(l)
    l.visible = visible
    return l


def _ensure_cycles():
    scene = bpy.context.scene
    if scene.render.engine != 'CYCLES':
        scene.render.engine = 'CYCLES'


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_holo_card():
    _ensure_cycles()
    obj = _find_or_create_card()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Holographic Card on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _reset_material_flags(tlm)
    _clear_layers(mat)

    # ─── 01. Card Art (imported image) ───
    l_art = _add_paint(mat, "01 Card Art", CARD_IMAGE, output_channel="BASE_COLOR")
    l_art.use_roughness = True
    l_art.roughness_fill = CARD_ROUGHNESS
    l_art.use_metallic = True
    l_art.metallic_fill = CARD_METALLIC

    # ─── 02-06. Foil pattern menu (one visible, rest toggled off) ───
    for name, kind, vis in FOIL_VARIANTS:
        _add_foil_variant(mat, name, kind, vis)

    # ─── 07. Foil Hue Shift (FRESNEL → view-dependent global hue march) ───
    # SAME spectral ramp, Fac from a Fresnel node. Tilt the card → the
    # whole foil's hue marches through the spectrum. Works on top of
    # whichever pattern(s) are visible.
    l_hue = _add_proc(mat, "07 Foil Hue Shift", "FRESNEL",
                      opacity=FOIL_FRES_OPACITY,
                      blend_mode=FOIL_FRES_BLEND,
                      output_channel="BASE_COLOR")
    l_hue.proc_fresnel_ior = FOIL_FRES_IOR
    l_hue.proc_contrast = FOIL_FRES_CONTRAST
    l_hue.proc_ramp_center = FOIL_FRES_RAMP_CENT
    _apply_holo_ramp(l_hue)          # ramp; view_shift harmless on a Fresnel fac

    # ─── 08. Holo Sparkle (NOISE SCREEN) ───
    l_spk = _add_proc(mat, "08 Holo Sparkle", "NOISE",
                      opacity=SPARK_OPACITY, blend_mode="SCREEN",
                      output_channel="BASE_COLOR")
    l_spk.proc_scale = SPARK_SCALE
    l_spk.proc_detail = 2.0
    l_spk.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_spk.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    l_spk.proc_contrast = SPARK_CONTRAST

    # ─── 09. Foil Microbump (→ BUMP, breaks the Fresnel into glitter) ───
    l_bump = _add_proc(mat, "09 Foil Microbump", "NOISE",
                       opacity=0.0, blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_bump.proc_scale = 180.0
    l_bump.proc_detail = 4.0
    l_bump.proc_roughness_proc = 0.7
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_bump.use_bump = True
    l_bump.bump_strength = 0.45
    l_bump.bump_distance = 0.0012

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    n_vis = sum(1 for l in tlm.layers if l.visible)
    print(f"[TLM] Holo Card built — {len(tlm.layers)} layers "
          f"({n_vis} visible)  (art: {CARD_IMAGE})")
    return mat


if __name__ == "__main__":
    build_holo_card()
