"""
TLM Showcase Preset — Holographic Trading Card (IMPORTED IMAGE + holo foil)
===========================================================================

An imported image (the card art) overlaid with a holographic rainbow-foil
sheen — the look of a Pokémon/sports "holo" card catching the light. The
STAR is the user's imported image; the procedural layers add the foil.

This is the bread-and-butter TLM pitch: drop in an image, then layer
procedural effects on top to turn it into a finished, sellable material.

Showcases:
  1. **PAINT layer = imported image** (`image_name`) → BASE_COLOR, the art.
  2. **proc_type='FRESNEL' rainbow** — the foil's Fac is the Fresnel
     view·normal, NOT a UV coordinate, so the rainbow shifts hue when
     the card is rotated — exactly like a real holographic foil. The
     7-stop ramp (red→violet) paints the full spectrum from face to
     silhouette.
  3. **Strong microbump** breaks the flat card normal into thousands of
     micro-facets — each reads a DIFFERENT Fresnel slot → rainbow turns
     into scintillating glitter that updates LIVE with camera & light.
  4. **Sparkle NOISE** (SCREEN) adds an extra fine "cosmos holo" speckle.
  5. **Glossy lamination** — low roughness so the card mirrors the sky.

Layer stack (4):
  01. Card Art PAINT (image)        — BASE_COLOR, glossy laminate
  02. Holo Foil FRESNEL  ADD        — VIEW-DEPENDENT rainbow
  03. Holo Sparkle NOISE  SCREEN    — fine glitter
  04. Foil Microbump → BUMP         — breaks Fresnel into scintillating specks
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
CARD_ROT_DEG = (90.0, 0.0, 28.0)            # face camera + 28° yaw — exposes grazing area
                                            # where the FRESNEL-masked foil blooms

# ── Card art (L01) ──
CARD_ROUGHNESS    = 0.09        # glossy lamination
CARD_METALLIC     = 0.0

# ── Holo foil — TWO-LAYER DYNAMIC stack ──
#
# A real holo foil has two ingredients:
#   (A) A static DIFFRACTION PATTERN — diagonal stripes etched into the
#       laminate. This is the WAVE layer.
#   (B) A view-dependent HUE SHIFT — what hue each stripe shows depends
#       on the viewing angle. This is the FRESNEL layer driving the
#       SAME 7-stop spectral ramp.
# Stacked, you see the diagonal foil pattern AND the colours scroll
# through the spectrum as you tilt the card. Pair with the strong
# microbump (L05) and every micro-facet catches a different Fresnel
# slot → the rainbow ALSO scintillates on a tiny scale → real foil.
#
# Layer A — static stripes
FOIL_STRIPES_SCALE   = 3.5
FOIL_STRIPES_OPACITY = 0.13
FOIL_STRIPES_BLEND   = "ADD"
# Layer B — Fresnel hue shift
FOIL_FRES_IOR        = 1.45
FOIL_FRES_OPACITY    = 0.32
FOIL_FRES_BLEND      = "OVERLAY"   # shifts hue, doesn't blow out values
FOIL_FRES_CONTRAST   = 0.50
FOIL_FRES_RAMP_CENT  = 0.50
# bright spectral rainbow (cleaner / brighter than the oil-slick palette)
HOLO_RED          = (1.00, 0.10, 0.20, 1.0)   # 0.00 (color1)
HOLO_ORANGE       = (1.00, 0.55, 0.05, 1.0)   # 0.17
HOLO_YELLOW       = (0.95, 0.95, 0.10, 1.0)   # 0.34 (color3)
HOLO_GREEN        = (0.10, 0.95, 0.30, 1.0)   # 0.50
HOLO_CYAN         = (0.05, 0.85, 1.00, 1.0)   # 0.66
HOLO_BLUE         = (0.20, 0.30, 1.00, 1.0)   # 0.83
HOLO_VIOLET       = (0.75, 0.15, 1.00, 1.0)   # 1.00 (color2)

# ── Sparkle (L03 — NOISE SCREEN) ──
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

    # ─── 02A. Foil Stripes (WAVE bands → static diffraction pattern) ───
    l_stripes = _add_proc(mat, "02A Foil Stripes", "WAVE",
                          opacity=FOIL_STRIPES_OPACITY,
                          blend_mode=FOIL_STRIPES_BLEND,
                          output_channel="BASE_COLOR", coord="UV")
    l_stripes.proc_scale = FOIL_STRIPES_SCALE
    l_stripes.proc_wave_type = "BANDS"
    l_stripes.proc_wave_profile = "SAW"
    l_stripes.proc_wave_bands_direction = "DIAGONAL"
    # NEW feature: view-driven UV parallax — bands SLIDE across the card
    # as the camera moves (real holo foil look). 0.25 = gentle scroll.
    l_stripes.proc_uv_view_shift = 0.25
    l_stripes.proc_use_manual_stops = True
    l_stripes.proc_color1 = HOLO_RED
    l_stripes.proc_color2 = HOLO_VIOLET
    l_stripes.use_proc_color3 = True
    l_stripes.proc_color3 = HOLO_YELLOW
    l_stripes.proc_color3_position = 0.34
    _add_stop(l_stripes, HOLO_ORANGE, 0.17)
    _add_stop(l_stripes, HOLO_GREEN,  0.50)
    _add_stop(l_stripes, HOLO_CYAN,   0.66)
    _add_stop(l_stripes, HOLO_BLUE,   0.83)

    # ─── 02B. Foil Hue Shift (FRESNEL → view-dependent hue) ───
    # SAME 7-stop spectral ramp, but the Fac comes from a Fresnel node
    # instead of UV. Tilt the card → the Fresnel readout slides → the
    # OVERLAY tint shifts through the spectrum. Combined with 02A's
    # stripe structure, you see the foil pattern AND its colour live-
    # updates with the viewing angle — like a real holo card in hand.
    l_hue = _add_proc(mat, "02B Foil Hue Shift", "FRESNEL",
                      opacity=FOIL_FRES_OPACITY,
                      blend_mode=FOIL_FRES_BLEND,
                      output_channel="BASE_COLOR")
    l_hue.proc_fresnel_ior = FOIL_FRES_IOR
    l_hue.proc_contrast = FOIL_FRES_CONTRAST
    l_hue.proc_ramp_center = FOIL_FRES_RAMP_CENT
    l_hue.proc_use_manual_stops = True
    l_hue.proc_color1 = HOLO_RED
    l_hue.proc_color2 = HOLO_VIOLET
    l_hue.use_proc_color3 = True
    l_hue.proc_color3 = HOLO_YELLOW
    l_hue.proc_color3_position = 0.34
    _add_stop(l_hue, HOLO_ORANGE, 0.17)
    _add_stop(l_hue, HOLO_GREEN,  0.50)
    _add_stop(l_hue, HOLO_CYAN,   0.66)
    _add_stop(l_hue, HOLO_BLUE,   0.83)

    # ─── 03. Holo Sparkle (NOISE SCREEN) ───
    l_spk = _add_proc(mat, "03 Holo Sparkle", "NOISE",
                      opacity=SPARK_OPACITY, blend_mode="SCREEN",
                      output_channel="BASE_COLOR")
    l_spk.proc_scale = SPARK_SCALE
    l_spk.proc_detail = 2.0
    l_spk.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_spk.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    l_spk.proc_contrast = SPARK_CONTRAST

    # ─── 04. Foil Microbump (→ BUMP, breaks the Fresnel into glitter) ───
    # The Fresnel above is uniform across the flat card → without bump
    # it'd just be a smooth gradient from centre to edge. A fine, strong
    # microbump perturbs the per-pixel normal so each tiny micro-facet
    # reads a DIFFERENT Fresnel value → the rainbow ramp lights up as
    # thousands of glittering coloured specks, and they shift with every
    # camera/light move. This is the real-foil scintillation.
    l_bump = _add_proc(mat, "04 Foil Microbump", "NOISE",
                       opacity=0.0, blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_bump.proc_scale = 180.0
    l_bump.proc_detail = 4.0
    l_bump.proc_roughness_proc = 0.7
    l_bump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_bump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_bump.use_bump = True
    l_bump.bump_strength = 0.45        # strong: needed to split the Fresnel into specks
    l_bump.bump_distance = 0.0012

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Holo Card built — {len(tlm.layers)} layers  (art: {CARD_IMAGE})")
    return mat


if __name__ == "__main__":
    build_holo_card()
