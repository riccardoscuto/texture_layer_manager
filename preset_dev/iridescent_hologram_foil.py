"""
TLM Showcase Preset — Iridescent Hologram Foil (FRESNEL mask source)
======================================================================

Hero F (v0.2) — uses the NEW `mask_source='FRESNEL'` feature added to TLM
specifically to unblock iridescent / hologram / oil-slick / bubble film /
mother-of-pearl materials.

Architecture insight: a single ColorRamp driven by Fresnel angle produces
the rainbow rim that 4+ stacked FILL layers couldn't (because each ADD
contributed a constant color × scalar fresnel — sum saturated to one hue).
With `mask_source='FRESNEL'` we wire Fresnel directly into a procedural's
mask, which gates a layer whose Color1/Color2 (and the ColorRamp band
shaped by proc_contrast + proc_ramp_center) span an angle-dependent range.

For the multi-band rainbow we stack TWO Fresnel-masked procedural layers
with different IORs + different colour pairs:
  • Cool band (IOR 1.10): cyan → magenta — wide rim covering most angles
  • Hot band  (IOR 1.50): gold → violet — narrow rim only at silhouette

Each layer's ColorRamp (band centred at proc_ramp_center, width controlled
by proc_contrast) cuts the smooth Fresnel gradient into a SHARP 2-color
transition. Stacked, they read as 4 distinct rings.

Layer stack (5 layers, bottom-to-top):
  01. Base Dark FILL                  — near-black, metallic 1, smooth
  02. Iridescent Cool NOISE+FRESNEL   — cyan/magenta band via IOR 1.10
  03. Iridescent Hot NOISE+FRESNEL    — gold/violet band via IOR 1.50
  04. Surface Distortion NOISE        — subtle bump for organic "foil" feel
  05. Microbump NOISE                 — finer bump
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Iridescent_Foil"
RESOLUTION = "1024"
TARGET_MESH = "TLM_FoilSphere"
SPHERE_SUBDIV = 3

# ── Base (Layer 01) ──
BASE_COLOR              = (0.020, 0.018, 0.035, 1.0)
BASE_METALLIC           = 1.00
BASE_ROUGHNESS          = 0.08

# ── Cool band (Layer 02 — wide rim via low IOR) ──
COOL_IOR                = 1.10                          # wide rim
COOL_COLOR_FACE         = (0.05, 0.85, 1.00, 1.0)       # at LOW fac (centre, facing)
COOL_COLOR_GRAZE        = (1.00, 0.20, 0.65, 1.0)       # at HIGH fac (rim, grazing)
COOL_CONTRAST           = 0.65
COOL_RAMP_CENTER        = 0.50
COOL_OPACITY            = 1.00

# ── Hot band (Layer 03 — narrow rim via high IOR) ──
HOT_IOR                 = 1.55                          # narrow rim
HOT_COLOR_FACE          = (1.00, 0.75, 0.10, 1.0)       # gold at lower fac
HOT_COLOR_GRAZE         = (0.55, 0.10, 1.00, 1.0)       # violet at silhouette
HOT_CONTRAST            = 0.70
HOT_RAMP_CENTER         = 0.55
HOT_OPACITY             = 0.85
HOT_BLEND_MODE          = "MIX"       # MIX replaces cool band only where Fresnel fires

# ── Surface distortion (Layer 04) ──
DISTORT_NOISE_SCALE     = 4.0
DISTORT_OPACITY         = 0.0   # bump only
DISTORT_BUMP_STRENGTH   = 0.20
DISTORT_BUMP_DISTANCE   = 0.003

# ── Microbump (Layer 05) ──
MICROBUMP_SCALE         = 80.0
MICROBUMP_STRENGTH      = 0.08
MICROBUMP_DISTANCE      = 0.0008


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_sphere():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        if len(obj.data.polygons) < 4000:
            _subdivide(obj)
        return obj
    print(f"[TLM] Creating '{TARGET_MESH}' UVSphere…")
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(0, 0, 1.0),
                                          segments=64, ring_count=32)
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
    mod.levels = SPHERE_SUBDIV
    mod.render_levels = SPHERE_SUBDIV
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
    try:
        scene.eevee.use_gtao = True
    except AttributeError:
        pass
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for sp in area.spaces:
                if sp.type == 'VIEW_3D' and sp.shading.type == 'SOLID':
                    sp.shading.type = 'MATERIAL'


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_iridescent_foil():
    _ensure_cycles()
    obj = _find_or_create_sphere()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"\n[TLM] Building Iridescent Hologram Foil v0.2 (FRESNEL source) on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    _clear_layers(mat)

    # ─── 01. Base Dark ───
    l_base = _add_fill(mat, "01 Base Dark", BASE_COLOR,
                       opacity=1.0, output_channel="BASE_COLOR")
    l_base.use_metallic = True
    l_base.metallic_fill = BASE_METALLIC
    l_base.use_roughness = True
    l_base.roughness_fill = BASE_ROUGHNESS

    # ─── 02. Cool Band (proc_type=FRESNEL, IOR low → wide angular sweep) ───
    # The NEW proc_type='FRESNEL' uses a Fresnel node directly as the
    # procedural's fac. Color1 maps to face-direct (0° angle), Color2 to
    # grazing silhouette (90°). With proc_contrast + proc_ramp_center the
    # ColorRamp shapes the transition band — sharp at high contrast, wide
    # at low. Low IOR (1.10) makes the sweep cover most viewing angles.
    l_cool = _add_proc(mat, "02 Iridescent Cool", "FRESNEL",
                       opacity=COOL_OPACITY,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_cool.proc_fresnel_ior = COOL_IOR
    l_cool.proc_color1 = COOL_COLOR_FACE
    l_cool.proc_color2 = COOL_COLOR_GRAZE
    l_cool.proc_contrast = COOL_CONTRAST
    l_cool.proc_ramp_center = COOL_RAMP_CENTER

    # ─── 03. Hot Band (proc_type=FRESNEL, IOR high → narrow grazing band) ───
    # Higher IOR (1.55) — the Fresnel curve rises sharply near silhouette,
    # so this band appears concentrated near the outer rim. Stacked on top
    # of the Cool layer with MIX, where the Hot fac is high (= silhouette
    # area), the cool gold/violet replaces cyan/magenta. Two layers ≈ four
    # visual rings: cyan → magenta → gold → violet from center to edge.
    l_hot = _add_proc(mat, "03 Iridescent Hot", "FRESNEL",
                      opacity=HOT_OPACITY,
                      blend_mode=HOT_BLEND_MODE,
                      output_channel="BASE_COLOR")
    l_hot.proc_fresnel_ior = HOT_IOR
    l_hot.proc_color1 = HOT_COLOR_FACE
    l_hot.proc_color2 = HOT_COLOR_GRAZE
    l_hot.proc_contrast = HOT_CONTRAST
    l_hot.proc_ramp_center = HOT_RAMP_CENTER

    # ─── 04. Surface Distortion (bump only) ───
    l_dist = _add_proc(mat, "04 Surface Distortion", "NOISE",
                       opacity=DISTORT_OPACITY,
                       blend_mode="MIX",
                       output_channel="BASE_COLOR")
    l_dist.proc_scale = DISTORT_NOISE_SCALE
    l_dist.proc_detail = 6.0
    l_dist.proc_roughness_proc = 0.55
    l_dist.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_dist.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    l_dist.proc_contrast = 0.50
    l_dist.use_bump = True
    l_dist.bump_strength = DISTORT_BUMP_STRENGTH
    l_dist.bump_distance = DISTORT_BUMP_DISTANCE

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

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Iridescent Hologram Foil v0.2 built — {len(tlm.layers)} layers")
    print(f"      FRESNEL-sourced layers: {sum(1 for l in tlm.layers if l.mask_source == 'FRESNEL')}")
    return mat


if __name__ == "__main__":
    build_iridescent_foil()
