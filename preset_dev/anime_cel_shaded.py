"""
TLM Showcase Preset — Anime Cel-Shaded (v0.2 with NEW NdotL feature)
======================================================================

Hero G v0.2 — uses the NEW `mask_source='NDOTL'` feature shipped in this
session to enable TRUE binary cel-shading. The shadow / light split is
now driven by the angle between the surface normal and the SUN direction
(not the camera direction as Fresnel was), so the lit side stays where
the sun shines even when the camera orbits — exactly what anime needs.

Anime render anatomy (technical):
  1. Hard light/shadow band from NdotL thresholding (3 fasce: lit/mid/dark)
  2. SHADER-INTEGRATED outline via Fresnel silhouette mask (no Freestyle,
     no Solidify modifier, no extra material slot — stays in the TLM material)
  3. Specular toon highlight on convex peaks (EDGE_WEAR mask + emission)
  4. Saturated flat color palette (not PBR realistic)

All four are handled inside the single TLM material — works the same
in Cycles and EEVEE, survives mesh edits, no separate render passes.

Layer stack (6 layers, bottom-to-top):
  01. Base Coral FILL                  — saturated mid-tone color
  02. NDOTL Mid Tone FILL              — slightly desaturated, gated by NdotL midband
  03. NDOTL Shadow FILL                — deep purple, gated by NdotL low band
  04. Toon Highlight FILL + EDGE_WEAR  — bright peach on convex peaks (emission)
  05. Microbump NOISE                  — fine grain
  06. Shader Outline FILL + FRESNEL    — dark ink line on silhouette band
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Anime_Cel"
RESOLUTION = "1024"
TARGET_MESH = "TLM_AnimeChar"
CHAR_SUBDIV = 3

# ── Base coral (Layer 01) ──
BASE_COLOR              = (0.96, 0.50, 0.50, 1.0)
BASE_ROUGHNESS          = 1.00      # pure matte — diffuse-only, no PBR specular leak
BASE_METALLIC           = 0.0

# ── Mid tone (Layer 02) ──
# Fires where NdotL is in the mid band (~0.3 < dot < 0.6). Slightly cooler
# than the lit color to give the "midtone" anime band.
MID_COLOR               = (0.78, 0.38, 0.45, 1.0)
MID_OPACITY             = 0.85
# NDOTL mask shaping via mask_contrast + mask_levels (TLM's standard mask refinement)
# Used here: at the mask side we'll layer ramps via mask_contrast.

# ── Shadow (Layer 03) ──
# Fires where NdotL is LOW (back of head, recesses) — deep saturated purple
SHADOW_COLOR            = (0.30, 0.08, 0.42, 1.0)
SHADOW_OPACITY          = 0.95

# ── Toon highlight (Layer 04) ──
HIGHLIGHT_COLOR         = (1.00, 0.92, 0.88, 1.0)
HIGHLIGHT_OPACITY       = 0.90
HIGHLIGHT_EMISSION_STR  = 1.5
HIGHLIGHT_MASK_SHARP    = 0.85
HIGHLIGHT_MASK_INTENSITY = 1.20

# ── Microbump (Layer 05) ──
MICROBUMP_SCALE         = 60.0
MICROBUMP_STRENGTH      = 0.08
MICROBUMP_DISTANCE      = 0.0008


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_char():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        if len(obj.data.polygons) < 5000:
            _subdivide(obj)
        return obj
    print(f"[TLM] Creating '{TARGET_MESH}' Suzanne…")
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 1.0))
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
    mod.levels = CHAR_SUBDIV
    mod.render_levels = CHAR_SUBDIV
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


def _setup_freestyle_outline(color=(0.92, 0.45, 0.20), thickness=4.0):
    """Enable Freestyle line render with the anime outline color/thickness."""
    scene = bpy.context.scene
    scene.render.use_freestyle = True
    vl = scene.view_layers[0]
    vl.use_freestyle = True
    vl.freestyle_settings.use_smoothness = True

    # Find or create the default linestyle
    ls = bpy.data.linestyles.get('LineStyle')
    if ls is None:
        ls = bpy.data.linestyles.new('LineStyle')
    ls.color = color
    ls.thickness = thickness
    ls.use_chaining = True
    ls.chaining = 'PLAIN'

    for lset in vl.freestyle_settings.linesets:
        lset.linestyle = ls
        lset.select_silhouette = True
        lset.select_border = True
        lset.select_crease = True
        lset.select_contour = True
    print(f"[TLM] Freestyle outline enabled (color={color}, thickness={thickness})")


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_anime_cel():
    _ensure_cycles()
    obj = _find_or_create_char()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Anime Cel-Shaded v0.2 (NdotL) on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Base Coral ───
    l_base = _add_fill(mat, "01 Base Coral", BASE_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = BASE_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = BASE_ROUGHNESS

    # ─── 02. NDOTL Mid Tone ───
    # FILL gated by NDOTL mask + contrast=0.5 (mid threshold) → covers the
    # transition zone between full-lit and shadow. Mid color sits between
    # base and shadow for the classic 3-band anime feel.
    l_mid = _add_fill(mat, "02 NdotL Mid Tone", MID_COLOR,
                      opacity=MID_OPACITY,
                      blend_mode="MIX",
                      output_channel="BASE_COLOR")
    l_mid.use_mask = True
    l_mid.mask_source = 'NDOTL'
    l_mid.mask_invert = True            # invert so mask is HIGH where NdotL is LOW (shadow side)
    l_mid.mask_contrast = 0.70          # sharpen the band
    l_mid.use_mask_levels = True
    l_mid.mask_levels_in_min = 0.45     # only the deep shadow side fires
    l_mid.mask_levels_in_max = 0.85
    l_mid.mask_levels_gamma = 1.0

    # ─── 03. NDOTL Shadow ───
    # Even deeper threshold — fires only at the darkest back-of-head region
    l_shadow = _add_fill(mat, "03 NdotL Shadow", SHADOW_COLOR,
                         opacity=SHADOW_OPACITY,
                         blend_mode="MIX",
                         output_channel="BASE_COLOR")
    l_shadow.use_mask = True
    l_shadow.mask_source = 'NDOTL'
    l_shadow.mask_invert = True
    l_shadow.mask_contrast = 0.90       # very sharp band
    l_shadow.use_mask_levels = True
    l_shadow.mask_levels_in_min = 0.70  # deeper than mid — only the darkest area
    l_shadow.mask_levels_in_max = 1.00
    l_shadow.mask_levels_gamma = 1.0

    # ─── 04. Toon Highlight ───
    # Bright peach on convex peaks (forehead, nose tip, brow ridge, ear lobes)
    l_hl = _add_fill(mat, "04 Toon Highlight", HIGHLIGHT_COLOR,
                     opacity=HIGHLIGHT_OPACITY,
                     blend_mode="MIX",
                     output_channel="BASE_COLOR")
    l_hl.use_emission = True
    l_hl.emission_color = HIGHLIGHT_COLOR
    l_hl.emission_strength = HIGHLIGHT_EMISSION_STR
    l_hl.use_mask = True
    l_hl.mask_source = 'EDGE_WEAR'
    l_hl.mask_gen_sharpness = HIGHLIGHT_MASK_SHARP
    l_hl.mask_gen_intensity = HIGHLIGHT_MASK_INTENSITY
    l_hl.mask_gen_breakup = 0.20

    # ─── 05. Microbump ───
    l_mb = _add_proc(mat, "05 Microbump", "NOISE",
                     opacity=0.0,
                     blend_mode="MIX",
                     output_channel="BASE_COLOR")
    l_mb.proc_scale = MICROBUMP_SCALE
    l_mb.proc_detail = 4.0
    l_mb.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_mb.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_mb.proc_contrast = 0.50
    l_mb.use_bump = True
    l_mb.bump_strength = MICROBUMP_STRENGTH
    l_mb.bump_distance = MICROBUMP_DISTANCE

    # ─── 06. SHADER-INTEGRATED OUTLINE ───────────────────────────────────
    # Dark FILL gated by Fresnel silhouette mask. Stays INSIDE the TLM
    # material — no Freestyle, no Solidify modifier, no extra material
    # slot. The outline survives mesh edits and works in real-time
    # viewports (Cycles + EEVEE) without a separate render pass.
    #
    # Tuning: very high IOR (15) makes Fresnel almost flat at 0.85-0.90
    # in the body of the mesh and only spikes to ~0.95+ near the exact
    # silhouette. Levels 0.85-0.93 gates the layer to that narrow band,
    # giving a crisp ink-line that's actually visible (raw Fresnel at
    # low IOR produces only a soft rim that gets lost in cel-shading).
    OUTLINE_COLOR = (0.0, 0.0, 0.0, 1.0)
    l_out = _add_fill(mat, "06 Shader Outline", OUTLINE_COLOR,
                      opacity=1.0, blend_mode="MIX",
                      output_channel="BASE_COLOR")
    l_out.use_mask = True
    l_out.mask_source = 'FRESNEL'
    l_out.mask_fresnel_ior = 15.0
    l_out.use_mask_levels = True
    l_out.mask_levels_in_min = 0.85
    l_out.mask_levels_in_max = 0.93

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Anime Cel-Shaded v0.3 built — {len(tlm.layers)} layers (incl. integrated outline)")
    print(f"      NDOTL-masked layers: {sum(1 for l in tlm.layers if l.mask_source == 'NDOTL')}")
    return mat


if __name__ == "__main__":
    build_anime_cel()
