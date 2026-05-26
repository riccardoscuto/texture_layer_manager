"""
TLM Showcase Preset — Aged Bronze Statue with Verdigris Patina
==============================================================

Goal: reproduce the classic art-historical bronze patination look — warm
chocolate bronze base, exposed bright bronze on the convex edges, deep
cyan-green verdigris accumulated in every concavity and recess, vertical
streaks where rainwater pools and runs.

This is a STRESS TEST for TLM combining:
  • Cumulative scalar routing (metallic dropdown on patina, roughness boost)
  • Smart masks (EDGE_WEAR for exposed bronze, DIRT for cavity patina)
  • Multi-procedural colour variation (NOISE + VORONOI mix)
  • Bump-stack across multiple layers
  • Channel routing on a non-trivial PBR composition

Approach: pure procedural — no painted layers. The reference is the
weathered bronze on the Statue of Liberty / Renaissance equestrian
monuments: bright bronze on raised features, green patina sinking into
every detail.

Layer stack (8 layers):
  01. Bronze Base FILL                — warm brown, metallic 1.0
  02. Bronze Warmth Variation NOISE   — subtle hue breakup (low opacity)
  03. Bronze Microbump NOISE          — surface roughness texture (bump only)
  04. Verdigris Patina VORONOI        — blue-green base, masked by DIRT
  05. Patina Hue Variation NOISE      — cyan/green spots within patina
  06. Bright Bronze EDGE FILL         — bright bronze on convex edges (EDGE_WEAR)
  07. Patina Roughness Boost NOISE    — rough scalar boost in patina zones
  08. Patina Bump NOISE               — fine bumps in patina (additional bump)

Designed for TLM_Cube on the standard test scene; the EDGE_WEAR mask
makes the cube edges sharply visible as bright bronze, the rest gradually
darkening into deep patina. Looks even better on a sculpted mesh (statue,
relief) where the curvature mask has more to bite into.
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Bronze_Verdigris"
RESOLUTION = "1024"
# Smart masks (DIRT, EDGE_WEAR) need a mesh with real curvature to read.
# TLM_Cube is flat-faced so they collapse to ~0 across the face interior.
# Suzanne has crevices (eyes, ears, mouth) + convex highlights (nose,
# brow ridge) that produce a dramatic patina-vs-bronze split.
TARGET_MESH = "Suzanne"
# Subdivision level applied to Suzanne so the curvature sampling has
# enough detail to drive the AO + pointiness inputs smoothly. Without
# subdivision, the low-poly Suzanne produces blocky mask transitions.
SUZANNE_SUBDIV = 3

# ── Bronze Base ──
# Old patinated bronze base — warm dark brown with red-orange undertone.
# Values are linear (Blender colour pickers default to sRGB display).
BRONZE_BASE_COLOR       = (0.085, 0.045, 0.020, 1.0)   # dark warm bronze
BRONZE_METALLIC         = 1.00                         # full metallic
BRONZE_ROUGHNESS        = 0.45                         # moderately polished but aged

# ── Bronze Warmth Variation (Layer 02) ──
# Subtle warm-to-cool hue variation across the bronze — large-scale noise,
# low contrast and low opacity so it just breaks up the perfectly uniform
# fill without dominating.
WARMTH_NOISE_SCALE      = 1.8
WARMTH_COLOR_COOL       = (0.060, 0.040, 0.025, 1.0)   # cooler brown
WARMTH_COLOR_WARM       = (0.110, 0.055, 0.025, 1.0)   # warmer redder bronze
WARMTH_OPACITY          = 0.25                         # was 0.40 — OVERLAY was crushing
                                                       # the verdigris saturation; lower wins

# ── Bronze Microbump (Layer 03) ──
# Fine surface texture — the patina of micro-scratches and tool marks left
# by the original casting + polishing. Bump only, no colour.
MICROBUMP_SCALE         = 60.0                         # very fine
MICROBUMP_BUMP_STRENGTH = 0.25
MICROBUMP_BUMP_DISTANCE = 0.002

# ── Verdigris Patina (Layer 04) ──
# Cyan-green oxide colour. Voronoi DTE gives organic "patches" of patina
# with darker rims (where the chemistry hasn't fully developed) and lighter
# centres (mature patina).
VERDIGRIS_DEEP          = (0.080, 0.380, 0.290, 1.0)
VERDIGRIS_BRIGHT        = (0.180, 0.620, 0.460, 1.0)
# v0.5: switched from VORONOI DTE to NOISE for the patina colour layer.
# Voronoi cells were too large relative to Suzanne — entire eye sockets
# fell inside one cell, painting them uniformly with color1=DEEP and
# never reaching color2=BRIGHT. NOISE gives continuous gradient across
# the cavities so the brighter mature patina shows through.
PATINA_NOISE_SCALE      = 3.5                          # broad organic variation
PATINA_NOISE_DETAIL     = 6.0
PATINA_CONTRAST         = 0.30                         # lower contrast → both colours mix
PATINA_OPACITY          = 1.00
PATINA_ROUGHNESS        = 0.65                         # was 0.78 — too matte was killing colour
# DIRT smart mask params — "patina accumulates in cavities + organic grunge"
PATINA_MASK_AO_DIST     = 0.60                         # was 0.40 — wider AO reach so the
                                                       # patina spreads INTO the eye sockets
                                                       # rather than hugging only the eyelid rims
PATINA_MASK_NOISE_SCALE = 8.0
PATINA_MASK_NOISE_INFL  = 0.45
PATINA_MASK_SHARPNESS   = 0.30                         # was 0.40 — softer threshold widens
                                                       # the patina coverage into mid-tone cavities
PATINA_MASK_INTENSITY   = 1.4

# ── Patina Hue Variation (Layer 05) ──
# Mix in some bluer + greener patches inside the patina to break up uniform
# verdigris. Heavily masked by DIRT (same source as layer 04).
HUE_BLUE                = (0.085, 0.310, 0.380, 1.0)   # blue-leaning copper carbonate
HUE_GREEN               = (0.140, 0.345, 0.140, 1.0)   # green-leaning copper hydroxide
HUE_NOISE_SCALE         = 7.0
HUE_OPACITY             = 0.55
HUE_BLEND_MODE          = "MIX"

# ── Bright Bronze Edges (Layer 06) ──
# Where the metal is exposed by pointiness — convex edges, raised details.
# Brighter, more polished bronze.
BRONZE_EDGE_COLOR       = (0.450, 0.230, 0.095, 1.0)   # bright polished bronze
BRONZE_EDGE_METALLIC    = 1.00
BRONZE_EDGE_ROUGHNESS   = 0.25                         # more polished (worn smooth)
BRONZE_EDGE_OPACITY     = 1.00
# EDGE_WEAR mask params — sharper convex-only mask with noise breakup
EDGE_MASK_SHARPNESS     = 0.82                         # was 0.88 — too restrictive, edges
                                                       # were under-emphasized after the patina
                                                       # widened in. 0.82 = clean sharpness still
EDGE_MASK_NOISE_SCALE   = 14.0
EDGE_MASK_NOISE_INFL    = 0.35
EDGE_MASK_INTENSITY     = 1.10                         # was 0.75 — now that the patina mask
                                                       # is softer (sharpness 0.30), the edge
                                                       # bronze needs to assert itself harder
                                                       # on the convex peaks to stay readable

# ── Patina Roughness Boost (Layer 07) ──
# Additional roughness wherever patina sits — bumps the rough channel up,
# masked by DIRT so it follows the patina exactly.
ROUGH_BOOST_NOISE_SCALE = 5.0
ROUGH_BOOST_FILL        = 0.65                         # was 0.85 — same colour-killing issue

# ── Patina Bump (Layer 08) ──
# Crystalline / crusty bumps that real verdigris has — small irregular
# bumps masked to the patina zones.
PATINA_BUMP_SCALE       = 25.0
PATINA_BUMP_STRENGTH    = 0.12                         # was 0.25 — even subtler. Deep cavities
                                                       # were going pitch-black because bump+rough
                                                       # killed all light bouncing in the eye
                                                       # sockets. Very low bump preserves the
                                                       # verdigris colour readability there.
PATINA_BUMP_DISTANCE    = 0.004


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_target_mesh():
    """Find or create a Suzanne mesh, subdivided for smooth smart masks."""
    target = bpy.data.objects.get(TARGET_MESH)
    if target is not None and target.type == 'MESH':
        # Already exists — ensure it's at least dense enough. If poly count
        # is very low (< 5000) we ALSO subdivide it. This handles the case
        # where Suzanne was created previously but subdiv-apply failed.
        if len(target.data.polygons) < 5000:
            print(f"[TLM] '{TARGET_MESH}' is low-poly ({len(target.data.polygons)} polys) — subdividing")
            _ensure_subdivided(target)
        return target

    # No Suzanne in scene — add one and apply subdivision.
    print(f"[TLM] No '{TARGET_MESH}' in scene — creating Suzanne with subdiv {SUZANNE_SUBDIV}")
    # Deselect everything so the new Suzanne becomes the only selection
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 1.0))
    suz = bpy.context.active_object
    suz.name = TARGET_MESH
    _ensure_subdivided(suz)
    return suz


def _ensure_subdivided(obj):
    """Apply a SUBSURF modifier at SUZANNE_SUBDIV levels + shade smooth.

    Uses a context-override dict so the operator works regardless of which
    area is currently active. modifier_apply is sensitive to context —
    without override it silently fails when called from a script that's
    not run from the 3D viewport.
    """
    # Make obj the only selected + active object
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Add SUBSURF
    mod = obj.modifiers.new(name="TLM_Subdiv", type='SUBSURF')
    mod.levels = SUZANNE_SUBDIV
    mod.render_levels = SUZANNE_SUBDIV

    # Apply via context override — guarantees the operator runs even when
    # the script is invoked from the text editor or a console.
    # Blender 4.0+ uses temp_override; older versions need an override dict.
    try:
        with bpy.context.temp_override(active_object=obj, selected_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception as exc:
        print(f"[TLM] modifier_apply failed via temp_override: {exc}")
        # Fallback: call directly and pray for context
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception as exc2:
            print(f"[TLM] modifier_apply fallback failed: {exc2}")
            print(f"[TLM]   Suzanne stays low-poly ({len(obj.data.polygons)} polys)")
            return

    # Shade smooth via the dedicated operator (more reliable than setting
    # poly.use_smooth individually after subdiv).
    try:
        with bpy.context.temp_override(active_object=obj, selected_objects=[obj]):
            bpy.ops.object.shade_smooth()
    except Exception:
        for p in obj.data.polygons:
            p.use_smooth = True
    print(f"[TLM]   subdiv applied — {len(obj.data.polygons)} polys, shade smooth")


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
    print(f"  + FILL        '{name}'")
    _add_layer_common(bpy.context, "FILL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.fill_color = color
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    return layer


def _add_proc(mat, name, proc_type, opacity=1.0, blend_mode="MIX",
              output_channel="BASE_COLOR"):
    print(f"  + PROCEDURAL  '{name}'  ({proc_type})")
    _add_layer_common(bpy.context, "PROCEDURAL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.proc_type = proc_type
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    layer.proc_coord_type = "OBJECT"
    return layer


# ─── BUILD ────────────────────────────────────────────────────────────────────

def _ensure_engine_supports_smart_masks():
    """Smart masks (DIRT/EDGE_WEAR/POINTINESS) require the engine to actually
    sample geometry inputs. In Blender 5.0 Eevee Next, the Pointiness output
    and ShaderNodeAmbientOcclusion either collapse to constants or return
    1.0 everywhere — verified empirically with the debug_smart_masks.py
    sanity test (Suzanne came out uniformly green even though POINTINESS
    should peak on edges). Cycles always works.

    This forces Cycles for the viewport + render, and as a fallback enables
    GTAO in Eevee (won't fix Pointiness but at least AO).
    """
    scene = bpy.context.scene
    original_engine = scene.render.engine
    if original_engine != 'CYCLES':
        print(f"[TLM] Switching engine '{original_engine}' → 'CYCLES' for smart-mask sampling")
        scene.render.engine = 'CYCLES'
        # Also bump device to GPU if available — Cycles preview can be slow
        try:
            scene.cycles.device = 'GPU'
        except Exception:
            pass
    # Eevee fallback (in case user switches back) — enable AO
    try:
        scene.eevee.use_gtao = True
    except AttributeError:
        pass

    # Force the 3D viewport to Material Preview / Rendered so smart masks
    # actually evaluate (Solid uses Workbench which ignores shaders entirely).
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    if space.shading.type == 'SOLID':
                        print("[TLM] Viewport was SOLID — switching to MATERIAL preview")
                        space.shading.type = 'MATERIAL'


def build_bronze_verdigris():
    _ensure_engine_supports_smart_masks()

    obj = _find_target_mesh()
    if obj is None:
        raise RuntimeError("No mesh found.")

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Bronze Verdigris Patina v0.6 on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─────────────────────────────────────────────────────────────────
    # 01. Bronze Base — warm dark bronze fill, full metallic
    # ─────────────────────────────────────────────────────────────────
    l_base = _add_fill(mat, "01 Bronze Base", BRONZE_BASE_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = BRONZE_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = BRONZE_ROUGHNESS

    # ─────────────────────────────────────────────────────────────────
    # 02. Bronze Warmth Variation — subtle hue breakup
    # ─────────────────────────────────────────────────────────────────
    # Without this, the bronze fill reads as flat plastic. Large-scale
    # noise + low opacity OVERLAY shifts warm/cool subtly across the mesh.
    l_warmth = _add_proc(mat, "02 Bronze Warmth", "NOISE",
                         opacity=WARMTH_OPACITY,
                         blend_mode="OVERLAY",
                         output_channel="BASE_COLOR")
    l_warmth.proc_scale = WARMTH_NOISE_SCALE
    l_warmth.proc_detail = 6.0
    l_warmth.proc_roughness_proc = 0.50
    l_warmth.proc_color1 = WARMTH_COLOR_COOL
    l_warmth.proc_color2 = WARMTH_COLOR_WARM
    l_warmth.proc_contrast = 0.30

    # ─────────────────────────────────────────────────────────────────
    # 03. Bronze Microbump — surface roughness texture (bump only)
    # ─────────────────────────────────────────────────────────────────
    # Cumulative bump — the procedural drives the BUMP scalar via the
    # additional path (use_bump=True). Colour output is BLACK so it
    # doesn't contaminate base_color.
    l_micro = _add_proc(mat, "03 Bronze Microbump", "NOISE",
                        opacity=1.0,
                        blend_mode="MIX",
                        output_channel="BASE_COLOR")
    l_micro.proc_scale = MICROBUMP_SCALE
    l_micro.proc_detail = 4.0
    l_micro.proc_roughness_proc = 0.50
    l_micro.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_micro.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_micro.proc_contrast = 0.50
    l_micro.opacity = 0.0          # no base_color contribution
    l_micro.use_bump = True
    l_micro.bump_strength = MICROBUMP_BUMP_STRENGTH
    l_micro.bump_distance = MICROBUMP_BUMP_DISTANCE

    # ─────────────────────────────────────────────────────────────────
    # 04. Verdigris Patina — green-cyan oxide accumulated in cavities
    # ─────────────────────────────────────────────────────────────────
    # The Voronoi DTE gives organic patches with darker rims and lighter
    # centres (matching real verdigris where the chemistry isn't uniform).
    # MASK = DIRT smart-generator: this is the KEY — patina accumulates
    # in geometry cavities (broad concavities + organic noise breakup).
    # On the cube this means the corners between faces get patina;
    # on a statue every fold and crevice does.
    #
    # Cumulative scalar routing on this layer:
    #   • metallic_fill = 0  → patina is NON-metallic
    #   • roughness_fill = 0.78 → patina is rougher than bronze
    # Both are gated by the layer's mask, so only patina zones get
    # metallic=0 / rough=0.78 applied (multiplicatively).
    l_patina = _add_proc(mat, "04 Verdigris Patina", "NOISE",
                         opacity=PATINA_OPACITY,
                         blend_mode="MIX",
                         output_channel="BASE_COLOR")
    l_patina.proc_scale = PATINA_NOISE_SCALE
    l_patina.proc_detail = PATINA_NOISE_DETAIL
    l_patina.proc_roughness_proc = 0.55
    l_patina.proc_color1 = VERDIGRIS_DEEP
    l_patina.proc_color2 = VERDIGRIS_BRIGHT
    l_patina.proc_contrast = PATINA_CONTRAST
    # Cumulative scalar — break the metallic continuity where patina sits
    l_patina.use_metallic = True
    l_patina.metallic_fill = 0.0
    l_patina.use_roughness = True
    l_patina.roughness_fill = PATINA_ROUGHNESS
    # SMART MASK: DIRT = inverted AO × noise grunge (cavities + organic)
    l_patina.use_mask = True   # CRITICAL: mask_source ignored if use_mask=False
    l_patina.mask_source = 'DIRT'
    l_patina.mask_ao_distance = PATINA_MASK_AO_DIST
    l_patina.mask_gen_breakup_scale = PATINA_MASK_NOISE_SCALE
    l_patina.mask_gen_breakup = PATINA_MASK_NOISE_INFL
    l_patina.mask_gen_sharpness = PATINA_MASK_SHARPNESS
    l_patina.mask_gen_intensity = PATINA_MASK_INTENSITY

    # ─────────────────────────────────────────────────────────────────
    # 05. Patina Hue Variation — bluer/greener spots within patina
    # ─────────────────────────────────────────────────────────────────
    # Real verdigris isn't uniform — copper carbonate (greener) and copper
    # hydroxide (bluer) form different patches. NOISE proc with two cool
    # variants, masked again by DIRT so it stays inside the patina zones.
    l_hue = _add_proc(mat, "05 Patina Hue Variation", "NOISE",
                      opacity=HUE_OPACITY,
                      blend_mode=HUE_BLEND_MODE,
                      output_channel="BASE_COLOR")
    l_hue.proc_scale = HUE_NOISE_SCALE
    l_hue.proc_detail = 8.0
    l_hue.proc_roughness_proc = 0.55
    l_hue.proc_color1 = HUE_BLUE
    l_hue.proc_color2 = HUE_GREEN
    l_hue.proc_contrast = 0.35
    # MASK: same DIRT — keep hue variation inside patina
    l_hue.use_mask = True
    l_hue.mask_source = 'DIRT'
    l_hue.mask_ao_distance = PATINA_MASK_AO_DIST
    l_hue.mask_gen_breakup_scale = PATINA_MASK_NOISE_SCALE
    l_hue.mask_gen_breakup = PATINA_MASK_NOISE_INFL
    l_hue.mask_gen_sharpness = PATINA_MASK_SHARPNESS
    l_hue.mask_gen_intensity = PATINA_MASK_INTENSITY

    # ─────────────────────────────────────────────────────────────────
    # 06. Bright Bronze Edges — exposed metal on convex edges
    # ─────────────────────────────────────────────────────────────────
    # EDGE_WEAR smart mask = pointiness on convex edges + noise breakup.
    # Where pointiness peaks (sharp edges), the layer paints bright bronze
    # over whatever was there → simulates centuries of polishing by hands,
    # weather, abrasion that has worn away the patina back to clean metal.
    l_edge = _add_fill(mat, "06 Bright Bronze Edges", BRONZE_EDGE_COLOR,
                       opacity=BRONZE_EDGE_OPACITY,
                       output_channel="BASE_COLOR")
    # Cumulative — restore metallic=1 and lower roughness on the edges
    l_edge.use_metallic = True
    l_edge.metallic_fill = BRONZE_EDGE_METALLIC
    l_edge.use_roughness = True
    l_edge.roughness_fill = BRONZE_EDGE_ROUGHNESS
    # SMART MASK: EDGE_WEAR (Live) = pointiness + noise + sharpness
    l_edge.use_mask = True
    l_edge.mask_source = 'EDGE_WEAR'
    l_edge.mask_gen_sharpness = EDGE_MASK_SHARPNESS
    l_edge.mask_gen_breakup_scale = EDGE_MASK_NOISE_SCALE
    l_edge.mask_gen_breakup = EDGE_MASK_NOISE_INFL
    l_edge.mask_gen_intensity = EDGE_MASK_INTENSITY

    # ─────────────────────────────────────────────────────────────────
    # 07. Patina Roughness Boost — additional rough scalar in patina
    # ─────────────────────────────────────────────────────────────────
    # Pure scalar layer: drives ONLY roughness (output_channel='ROUGHNESS').
    # Procedural NOISE serves as the fac for variation within the boost so
    # roughness isn't perfectly uniform across patina. Masked by DIRT.
    l_rough = _add_proc(mat, "07 Patina Roughness Boost", "NOISE",
                        opacity=1.0,
                        blend_mode="MIX",
                        output_channel="ROUGHNESS")
    l_rough.proc_scale = ROUGH_BOOST_NOISE_SCALE
    l_rough.proc_detail = 5.0
    l_rough.proc_roughness_proc = 0.50
    # Scalar layers use color1/color2 as roughness values (BW pipeline)
    l_rough.proc_color1 = (0.55, 0.55, 0.55, 1.0)   # min rough boost
    l_rough.proc_color2 = (0.95, 0.95, 0.95, 1.0)   # max rough boost
    l_rough.proc_contrast = 0.35
    l_rough.use_mask = True
    l_rough.mask_source = 'DIRT'
    l_rough.mask_ao_distance = PATINA_MASK_AO_DIST
    l_rough.mask_gen_breakup_scale = PATINA_MASK_NOISE_SCALE
    l_rough.mask_gen_breakup = PATINA_MASK_NOISE_INFL
    l_rough.mask_gen_sharpness = PATINA_MASK_SHARPNESS
    l_rough.mask_gen_intensity = PATINA_MASK_INTENSITY

    # ─────────────────────────────────────────────────────────────────
    # 08. Patina Bump — crystalline crusty bumps on patina zones
    # ─────────────────────────────────────────────────────────────────
    # Cumulative bump only; base_color contribution disabled via opacity=0.
    # Masked by DIRT so the bumps only appear on patina.
    l_pbump = _add_proc(mat, "08 Patina Bump", "NOISE",
                        opacity=0.0,
                        blend_mode="MIX",
                        output_channel="BASE_COLOR")
    l_pbump.proc_scale = PATINA_BUMP_SCALE
    l_pbump.proc_detail = 6.0
    l_pbump.proc_roughness_proc = 0.65
    l_pbump.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_pbump.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_pbump.proc_contrast = 0.55
    l_pbump.use_bump = True
    l_pbump.bump_strength = PATINA_BUMP_STRENGTH
    l_pbump.bump_distance = PATINA_BUMP_DISTANCE
    l_pbump.use_mask = True
    l_pbump.mask_source = 'DIRT'
    l_pbump.mask_ao_distance = PATINA_MASK_AO_DIST
    l_pbump.mask_gen_breakup_scale = PATINA_MASK_NOISE_SCALE
    l_pbump.mask_gen_breakup = PATINA_MASK_NOISE_INFL
    l_pbump.mask_gen_sharpness = PATINA_MASK_SHARPNESS
    l_pbump.mask_gen_intensity = PATINA_MASK_INTENSITY

    # ─────────────────────────────────────────────────────────────────
    # 09. Cavity Ambient Tint — soft brown wash in DEEP cavities only
    # ─────────────────────────────────────────────────────────────────
    # Without this, the deepest cavities (eye sockets centre, mouth interior,
    # ear canal) go nearly pitch-black because the patina layers above are
    # rough (low specular) and the AO is total, so almost no light bounces
    # back to the camera. A subtle desaturated brown wash, painted only where
    # AO is EXTREMELY low (short rays, high sharpness on the mask), recovers
    # readable midtone instead of the abyss-black hole.
    l_amb = _add_fill(mat, "09 Cavity Ambient Tint",
                      (0.080, 0.055, 0.040, 1.0),  # warm dark grey-brown
                      opacity=0.65, blend_mode="MIX",
                      output_channel="BASE_COLOR")
    l_amb.use_metallic = True
    l_amb.metallic_fill = 0.0          # non-metallic in cavities
    l_amb.use_roughness = True
    l_amb.roughness_fill = 0.85        # very matte, no spec
    l_amb.use_mask = True
    l_amb.mask_source = 'DIRT'
    l_amb.mask_ao_distance = 0.20      # SHORT rays = only deepest cavities
    l_amb.mask_gen_breakup_scale = 4.0
    l_amb.mask_gen_breakup = 0.20      # less noise — clean dark wash
    l_amb.mask_gen_sharpness = 0.75    # SHARP threshold — only the absolute deepest
    l_amb.mask_gen_intensity = 1.0

    # ── Rebuild & finalise ────────────────────────────────────────────
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Bronze Verdigris Patina built — {len(tlm.layers)} layers\n")
    return mat


if __name__ == "__main__":
    build_bronze_verdigris()
