"""
TLM Showcase Preset — Anime Genshin-Style Shader
==================================================

Replicates the architecture of Ben Ayers' Genshin Shader manual (v2.2.1)
using TLM features. The key technical insights from the manual:

  1. **Half-Lambert NdotL** — instead of clipping NdotL at 0, remap
     [-1,1] → [0,1]. This is what TLM's `mask_source='NDOTL'` already
     does internally (via MapRange).

  2. **5-zone terminator gradient** — the boundary between light and
     shadow has FIVE color zones, not just light/dark:
        Lit Base → Start Color (warm) → End Color (cool) →
        SSS Color (red-ish, fake subsurface) → Shadow Color (deep)
     We build this with 4 FILL layers, each gated by a NdotL band
     (mask_levels_in_min/in_max narrow window).

  3. **Inverted-hull outline** — the dark outline around characters
     is NOT a shader effect. It's a Solidify modifier with FLIP
     NORMALS + a second material slot containing a black emission
     shader. We automate the whole setup here.

  4. **Standard View Transform** — Filmic crushes saturated anime
     colours. Standard preserves them.

This preset produces a SUN-LOCKED, MULTI-BAND, OUTLINED anime look
that is the closest TLM can get to a Genshin-style render without
shader-graph rewrites.

Layer stack (7 layers, bottom-to-top):
  01. Lit Base FILL                 — saturated coral (the lit zone)
  02. Terminator Start NdotL band   — warm beige in the narrow band where NdotL crosses ~0.5
  03. Terminator End NdotL band     — cool tone in band 0.6
  04. SSS Color NdotL band          — red-pink (fake subsurface) at band ~0.75
  05. Shadow Color NdotL band       — deep blue-purple at NdotL < 0.85
  06. Toon Specular FILL+EDGE_WEAR  — fixed-size highlight on convex peaks (emission)
  07. Microbump NOISE               — fine grain

Plus: scene-level setup applies a black inverted-hull outline.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Anime_Genshin"
RESOLUTION = "1024"
TARGET_MESH = "TLM_GenshinChar"
CHAR_SUBDIV = 3

# ── Anime palette (Ben Ayers manual-inspired defaults) ──
# Anime palette tuned for HIGH CONTRAST cel-shading visibility:
# the previous "anime soft palette" colours (warm beige → cool tan →
# dusky pink → blue) blended too smoothly and looked like a single
# pinkish-cream blob. These high-contrast values give five clearly
# distinguishable cel zones on the target render.
LIT_COLOR               = (1.00, 0.55, 0.55, 1.0)    # bright coral pink (lit zone)
TERM_START_COLOR        = (1.00, 0.75, 0.55, 1.0)    # warm salmon/peach (warm transition)
TERM_END_COLOR          = (0.85, 0.50, 0.60, 1.0)    # dusky pink (cool transition)
SSS_COLOR               = (0.65, 0.25, 0.40, 1.0)    # vivid red-pink (fake SSS)
SHADOW_COLOR            = (0.25, 0.10, 0.35, 1.0)    # deep purple (saturated shadow)

# ── Terminator band positions (in inverted NdotL space; mask_invert=True) ──
# inverted NdotL = 1 means "fully in shadow", 0 means "fully lit"
# Each layer fires at a RAZOR-SHARP threshold (in_max ≈ in_min + 0.005 so
# the MapRange acts as a binary step function). This is the anime/Genshin
# convention: discrete colour bands at the terminator, not a smooth gradient.
#
# Thresholds tuned for VISIBLE band coverage on a frontally-viewed mesh:
# - Layer 02 fires for inv > 0.10 (= NdotL < 0.80) → covers ~80% of front
# - Layer 03 fires for inv > 0.22 → covers ~55%
# - Layer 04 fires for inv > 0.32 → covers ~35%
# - Layer 05 fires for inv > 0.42 → covers ~15% (deepest shadow)
# This gives the "warm beige dominates with coral lit zone + progressively
# deeper colours toward shadow" look from the Hero G v3 render.
TERM_START_MIN          = 0.10
TERM_START_MAX          = 0.105
TERM_END_MIN            = 0.22
TERM_END_MAX            = 0.225
SSS_MIN                 = 0.32
SSS_MAX                 = 0.325
SHADOW_MIN              = 0.42
SHADOW_MAX              = 0.425

# ── Toon specular (Ben Ayers Specular Size/Intensity) ──
SPEC_COLOR              = (1.00, 0.95, 0.90, 1.0)
SPEC_EMISSION_STR       = 4.0
SPEC_MASK_SHARP         = 0.85    # narrow = small specular dot
SPEC_MASK_INTENSITY     = 1.5
SPEC_MASK_BREAKUP       = 0.10

# ── Microbump ──
MICROBUMP_SCALE         = 60.0
MICROBUMP_STRENGTH      = 0.08
MICROBUMP_DISTANCE      = 0.0008

# ── Outline (shader-integrated via Fresnel silhouette) ──
# Two options for the outline:
#   (A) Shader-integrated: Fresnel mask with high IOR + razor-sharp levels
#       gates a dark FILL layer to the silhouette band only. NO modifier,
#       NO extra material slot — everything in the single TLM material.
#   (B) Inverted-hull: Solidify modifier + slot 1 material (legacy approach).
#
# Default is (B) inverted-hull for stronger, more anime-authentic outlines.
# (A) shader-based works but produces only a thin Fresnel rim — fine for
# subtle outlines but doesn't match the dramatic ink-line look of Genshin-
# style renders. Toggle USE_INVERTED_HULL_OUTLINE=False to use shader-only
# outline (no modifier, no extra material slot) — useful when geometry
# must stay mesh-pure (e.g. for cloth simulation, real-time game export).
USE_INVERTED_HULL_OUTLINE = True                    # default: inverted-hull (Solidify)
OUTLINE_COLOR           = (0.10, 0.04, 0.08, 1.0)   # dark almost-black with hint of base hue
OUTLINE_THICKNESS       = 0.012                     # only used for inverted-hull mode
# Shader-based outline parameters:
OUTLINE_FRESNEL_IOR     = 5.0                       # high IOR → narrow silhouette rim
OUTLINE_LEVELS_MIN      = 0.85                      # rim fires above NdotV-based mask 0.85
OUTLINE_LEVELS_MAX      = 0.86                      # razor-sharp transition


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


def _get_or_create_material(obj, name, slot_index=0):
    """Place a material in a specific slot (creating slots as needed)."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    while len(obj.data.materials) <= slot_index:
        obj.data.materials.append(None)
    obj.data.materials[slot_index] = mat
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


def _add_ndotl_band(mat, name, color, level_min, level_max):
    """Add a FILL layer gated by a NARROW NdotL band — one of the 5 terminator zones.

    mask_invert=True so the layer fires in SHADOW areas (where NdotL is low).
    mask_levels_in_min/in_max define the precise band (e.g. 0.45→0.55 = the
    transition zone where the terminator's "warm start" color appears).
    Outside the band, the mask is 0 → layer invisible.
    """
    l = _add_fill(mat, name, color, opacity=1.0, blend_mode="MIX",
                  output_channel="BASE_COLOR")
    l.use_mask = True
    l.mask_source = 'NDOTL'
    l.mask_invert = True              # high = shadow side
    l.use_mask_levels = True
    l.mask_levels_in_min = level_min
    l.mask_levels_in_max = level_max
    l.mask_levels_gamma = 1.0
    return l


def _ensure_cycles_and_standard_view():
    """Cycles + Standard View Transform (Filmic crushes anime colours)."""
    scene = bpy.context.scene
    if scene.render.engine != 'CYCLES':
        scene.render.engine = 'CYCLES'
    # Standard transform — Genshin manual: filmic destroys saturated anime palette
    try:
        scene.view_settings.view_transform = 'Standard'
    except Exception:
        pass
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for sp in area.spaces:
                if sp.type == 'VIEW_3D' and sp.shading.type == 'SOLID':
                    sp.shading.type = 'MATERIAL'


def apply_emission_bypass(mat):
    """Bypass Cycles' natural NdotL diffuse shading so the TLM cel-shading
    bands are visible as flat colours (instead of being multiplied by the
    cosine of incidence, which would darken the SHADOW band to near-black).

    Strategy:
      - Set BSDF.Base Color = BLACK so the diffuse component contributes 0
      - Redirect TLM's full base-color stack output → BSDF.Emission Color
      - Emission Strength = 1.0 (1:1 colour passthrough)

    Result: the Principled BSDF acts as a pure emissive shader. The TLM
    layer stack (lit / start / end / sss / shadow bands gated by NdotL)
    is the FINAL visual — no extra shading applied. This is the textbook
    anime cel-shading approach.
    """
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    base_route = next((n for n in nt.nodes if 'route_base_color' in n.name), None)
    if not bsdf or not base_route:
        print("[TLM] apply_emission_bypass: BSDF or base_color route missing")
        return False

    # Disconnect existing Base Color + Emission Color links
    for link in list(nt.links):
        if link.to_node == bsdf and link.to_socket.name in ('Base Color', 'Emission Color'):
            nt.links.remove(link)

    # Black base color → diffuse contribution is 0
    bsdf.inputs['Base Color'].default_value = (0.0, 0.0, 0.0, 1.0)
    bsdf.inputs['Roughness'].default_value = 1.0
    bsdf.inputs['Metallic'].default_value = 0.0

    # Redirect base color output → Emission Color (no shading)
    out_sock = base_route.outputs.get('Output') or base_route.outputs[0]
    nt.links.new(out_sock, bsdf.inputs['Emission Color'])
    bsdf.inputs['Emission Strength'].default_value = 1.0
    print("[TLM] Emission bypass applied — TLM stack → BSDF.Emission")
    return True


def setup_inverted_hull_outline(obj, color, thickness, vertex_group=None):
    """Set up the classic anime inverted-hull outline.

    Procedure (from Genshin Shader manual):
      1. Add a Solidify modifier with Normals.Flip=True + Material Offset=1
      2. Create a second material slot containing a black emission shader
         with backface culling so the outline shows only on flipped normals

    The outline is a SHELL: when the Solidify flips its normals, the inverted
    surface points outward, gets the offset material (slot 1 = the black
    outline mat), and gets backface-culled away on the inside — visually
    appearing as a thick line around the silhouette.

    Returns the outline material (caller may further configure it).
    """
    # ─── Solidify modifier ───
    mod = obj.modifiers.get("TLM_Outline_Solidify")
    if mod is None:
        mod = obj.modifiers.new(name="TLM_Outline_Solidify", type='SOLIDIFY')
    mod.thickness = thickness
    mod.offset = 1.0
    mod.use_flip_normals = True
    mod.material_offset = 1
    mod.use_rim = False               # disable rim fill — gives cleaner outlines
    if vertex_group:
        mod.vertex_group = vertex_group

    # ─── Outline material (slot 1) ───
    out_mat = bpy.data.materials.get("TLM_OutlineMat")
    if out_mat is None:
        out_mat = bpy.data.materials.new("TLM_OutlineMat")
    out_mat.use_nodes = True
    out_mat.use_backface_culling = True   # EEVEE backface culling

    nt = out_mat.node_tree
    nt.nodes.clear()

    # Emission shader (the outline ink)
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.location = (-200, 0)
    emit.inputs["Color"].default_value = color
    emit.inputs["Strength"].default_value = 1.0

    # Transparent BSDF (for Cycles backface culling via Mix Shader)
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    transp.location = (-200, -150)

    # Geometry node for Backfacing factor
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-400, -100)

    # Mix Shader — Cycles selects transparent for front-facing (mask the
    # inverted hull's front side) so only the back (flipped to outside) shows.
    # Since Solidify flips normals, the WORLD-facing surface becomes the
    # "backface" of that material — and the outline emission shows there.
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (50, -50)
    nt.links.new(geo.outputs["Backfacing"], mix.inputs[0])
    nt.links.new(emit.outputs["Emission"], mix.inputs[1])
    nt.links.new(transp.outputs["BSDF"], mix.inputs[2])

    out_node = nt.nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (250, -50)
    nt.links.new(mix.outputs["Shader"], out_node.inputs["Surface"])

    # Ensure slot 1 exists and points to outline material
    while len(obj.data.materials) <= 1:
        obj.data.materials.append(None)
    obj.data.materials[1] = out_mat
    print(f"[TLM]   Outline: Solidify thick={thickness}m, color={color}")
    return out_mat


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_anime_genshin():
    _ensure_cycles_and_standard_view()
    obj = _find_or_create_char()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Anime Genshin-Style v0.1 on '{obj.name}'…")

    # Slot 0 = the TLM shader material
    mat = _get_or_create_material(obj, MATERIAL_NAME, slot_index=0)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    # CRITICAL for anime cel-shading: route the TLM stack to Emission Color
    # instead of Base Color, so Cycles' diffuse cosine attenuation doesn't
    # crush the NDOTL band shadow colours to near-black. This flag is now
    # respected by compositing.rebuild_node_tree() → persistent across rebuilds.
    tlm.use_emission_output = True
    _clear_layers(mat)

    # ─── 01. Lit Base ───
    l_base = _add_fill(mat, "01 Lit Base", LIT_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = 0.0
    l_base.use_roughness = True
    l_base.roughness_fill = 1.00      # pure matte — no PBR spec leak

    # ─── 02-05. Terminator 4-band gradient via NdotL ───
    _add_ndotl_band(mat, "02 Terminator Start (warm)", TERM_START_COLOR,
                    TERM_START_MIN, TERM_START_MAX)
    _add_ndotl_band(mat, "03 Terminator End (cool)",   TERM_END_COLOR,
                    TERM_END_MIN, TERM_END_MAX)
    _add_ndotl_band(mat, "04 SSS Color (red-pink)",    SSS_COLOR,
                    SSS_MIN, SSS_MAX)
    _add_ndotl_band(mat, "05 Shadow Color (deep)",     SHADOW_COLOR,
                    SHADOW_MIN, SHADOW_MAX)

    # ─── 06. Toon Specular via NDOTH (Genshin-style Phong highlight) ───
    # NDOTH = Normal · normalize(Sun + View). Peaks where the surface faces
    # midway between the sun and the camera — classic Phong specular
    # highlight position. Narrow ColorRamp band + emission gives the small,
    # razor-edged "anime sparkle" on lit-side convex peaks.
    l_spec = _add_fill(mat, "06 Toon Specular (NdotH)", SPEC_COLOR,
                       opacity=1.0, blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_spec.use_emission = True
    l_spec.emission_color = SPEC_COLOR
    l_spec.emission_strength = SPEC_EMISSION_STR
    l_spec.use_mask = True
    l_spec.mask_source = 'NDOTH'
    l_spec.use_mask_levels = True
    l_spec.mask_levels_in_min = 0.85  # only the sharpest specular peak
    l_spec.mask_levels_in_max = 0.95

    # ─── 07. Microbump ───
    l_mb = _add_proc(mat, "07 Microbump", "NOISE",
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

    # ─── 08. SHADER-INTEGRATED OUTLINE (default) ───────────────────────────
    # Dark FILL layer gated by Fresnel mask at razor-sharp silhouette threshold.
    # Everything STAYS IN THE TLM MATERIAL — no Solidify modifier, no extra
    # material slot. Cleaner than inverted-hull and survives mesh-edit
    # operations that would break a Solidify shell.
    if not USE_INVERTED_HULL_OUTLINE:
        l_out = _add_fill(mat, "08 Shader Outline", OUTLINE_COLOR,
                          opacity=1.0, blend_mode="MIX",
                          output_channel="BASE_COLOR")
        l_out.use_mask = True
        l_out.mask_source = 'FRESNEL'           # NdotV-based silhouette detection
        l_out.mask_fresnel_ior = OUTLINE_FRESNEL_IOR
        l_out.use_mask_levels = True
        l_out.mask_levels_in_min = OUTLINE_LEVELS_MIN
        l_out.mask_levels_in_max = OUTLINE_LEVELS_MAX
        l_out.mask_levels_gamma = 1.0

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)
    # tlm.use_emission_output=True is honored inside rebuild_node_tree(),
    # so no manual apply_emission_bypass() call needed — the bypass now
    # survives any subsequent rebuild trigger (layer edit, preset reload, etc.)

    # ─── INVERTED-HULL OUTLINE (optional legacy mode) ──────────────────────
    if USE_INVERTED_HULL_OUTLINE:
        setup_inverted_hull_outline(obj, OUTLINE_COLOR, OUTLINE_THICKNESS)

    print(f"[TLM] Anime Genshin-Style built — {len(tlm.layers)} layers + outline shell")
    print(f"      NDOTL-banded layers: {sum(1 for l in tlm.layers if l.mask_source == 'NDOTL')}")
    return mat


if __name__ == "__main__":
    build_anime_genshin()
