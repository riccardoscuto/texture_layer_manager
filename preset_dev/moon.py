"""
TLM Showcase Preset — Moon (Lunar Regolith)
=============================================

A deeply layered, 100% procedural Moon: dusty regolith base, lighter
highlands, dark basaltic MARIA (thresholded), four scales of crater relief
(bump), fine dust grain and a subtle tonal mottle. 8 layers, no UVs, no
images.

Layer stack (bottom → top):
  01. Regolith Base     FILL    matte gray dust (rough 0.94)
  02. Highlands         NOISE   lighter broad tonal variation
  03. Mare Basalt       NOISE   dark seas — thresholded ramp (Contrast/Center)
  04. Big Craters       VORONOI bump-only crater relief (large)
  05. Med Craters       VORONOI bump-only (medium)
  06. Small Craters     VORONOI bump-only (small)
  07. Tiny Craters      VORONOI bump-only (fine pitting)
  08. Dust Mottle       NOISE   subtle OVERLAY tonal grain

Best rendered with a low raking sun on a near-black (space) world —
see _setup_moon_lighting() below. Looks great as a gibbous phase.

──────────────────────────────────────────────────────────────────────────────
DESIGN NOTES / PROCEDURAL CEILING (learned 2026-06-01):
──────────────────────────────────────────────────────────────────────────────

1. MARIA need scale >= ~2 on OBJECT coords. At scale < 1 the noise is
   lower-frequency than the sphere, so the whole visible disc is one flat
   blob and no seas appear. They also need proc_use_manual_stops=False so
   Contrast + Ramp Center actually threshold the noise into distinct dark
   patches (with manual stops on, the ramp is a flat 0→1 and the seas wash
   out — this bit hard during development).

2. RELIEF ↔ MARIA TRADE-OFF. Strong crater relief (heavy bump OR real
   displacement) perceptually BURIES the maria: the lit micro-slopes raise
   the average brightness and the dark albedo of the seas disappears. To
   keep the maria readable the crater relief must stay gentle — hence the
   moderate bump values here. You can't have both razor-sharp dense craters
   AND strong dark seas from this procedural approach.

3. VORONOI ISN'T CRATERS. Voronoi tessellates space into connected cells,
   so its displacement is a continuous lumpy "orange-peel / brain" surface,
   not isolated circular impact craters with raised rims. There is no
   dedicated crater generator. For a PHOTO-ACCURATE Moon matching real
   imagery, load a NASA Moon albedo + heightmap (public-domain CGI Moon Kit)
   as image layers (PAINT colour + the Height-Blend IMAGE slot / a
   displacement-routed image) instead of going fully procedural.
"""

import bpy
from math import radians

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


MATERIAL_NAME = "TLM_Moon"
TARGET_MESH   = "TLM_MoonBall"
RESOLUTION    = "2048"


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_or_create_mesh():
    obj = bpy.data.objects.get(TARGET_MESH)
    if obj is not None and obj.type == 'MESH':
        return obj
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_ico_sphere_add(radius=1.0, subdivisions=5,
                                           location=(0, 0, 1.2))
    obj = bpy.context.active_object
    obj.name = TARGET_MESH
    for p in obj.data.polygons:
        p.use_smooth = True
    return obj


def _get_or_create_material(obj, name):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    # bump-only displacement (real displacement washes the maria — see notes)
    if hasattr(mat, 'displacement_method'):
        mat.displacement_method = 'BUMP'
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


def _add(mat, kind):
    # CONTEXT NOTE: _add_layer_common targets context.active_object's active
    # material, so the caller MUST set obj active + this material active first.
    _add_layer_common(bpy.context, kind)
    return mat.tlm.layers[mat.tlm.active_layer_index]


def _crater(mat, name, scale, bump_strength, bump_distance):
    c = _add(mat, "PROCEDURAL")
    c.name = name
    c.proc_type = "VORONOI"
    c.proc_coord_type = "OBJECT"
    c.proc_scale = scale
    c.proc_randomness = 1.0
    c.proc_color1 = (0.0, 0.0, 0.0, 1.0)   # bump-only → no colour wash on the maria
    c.proc_color2 = (0.0, 0.0, 0.0, 1.0)
    c.opacity = 0.0
    c.use_bump = True
    c.bump_strength = bump_strength
    c.bump_distance = bump_distance
    return c


def _setup_moon_lighting():
    """Optional: dark-space world + low raking sun for a gibbous beauty shot."""
    import mathutils
    sc = bpy.context.scene
    if sc.render.engine != 'CYCLES':
        sc.render.engine = 'CYCLES'
    sc.render.film_transparent = True
    wd = sc.world
    if wd and wd.use_nodes:
        bg = next((n for n in wd.node_tree.nodes if n.type == 'BACKGROUND'), None)
        if bg:
            bg.inputs[1].default_value = 0.04   # near-black space ambient
    sun = bpy.data.objects.get("TLM_MoonSun")
    if sun is None:
        sd = bpy.data.lights.new("TLM_MoonSun", "SUN")
        sun = bpy.data.objects.new("TLM_MoonSun", sd)
        sc.collection.objects.link(sun)
    sun.data.energy = 4.2
    sun.data.angle = 0.004
    sun.data.color = (1.0, 0.98, 0.95)
    moon = mathutils.Vector((0, 0, 1.2)); P = mathutils.Vector((-3.7, -2.9, 1.5))
    sun.rotation_euler = (moon - P).to_track_quat('-Z', 'Y').to_euler()
    return sun


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_moon():
    obj = _find_or_create_mesh()
    bpy.context.view_layer.objects.active = obj
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)

    print(f"\n[TLM] Building Moon on '{obj.name}'…")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False
    tlm.use_displacement = False
    _clear_layers(mat)

    # 01 — regolith base
    b = _add(mat, "FILL"); b.name = "01 Regolith Base"
    b.fill_color = (0.21, 0.20, 0.19, 1.0)
    b.use_roughness = True;  b.roughness_fill = 0.94
    b.use_metallic = True;   b.metallic_fill = 0.0

    # 02 — highlands (broad lighter tone)
    h = _add(mat, "PROCEDURAL"); h.name = "02 Highlands"
    h.proc_type = "NOISE"; h.proc_coord_type = "OBJECT"
    h.proc_use_manual_stops = False
    h.proc_scale = 1.3; h.proc_detail = 5.0
    h.proc_contrast = 0.4; h.proc_ramp_center = 0.5
    h.proc_color1 = (0.24, 0.23, 0.22, 1.0)
    h.proc_color2 = (0.43, 0.42, 0.40, 1.0)
    h.opacity = 0.8

    # 03 — mare basalt (dark seas, thresholded — see notes #1)
    m = _add(mat, "PROCEDURAL"); m.name = "03 Mare Basalt"
    m.proc_type = "NOISE"; m.proc_coord_type = "OBJECT"
    m.proc_use_manual_stops = False
    m.proc_scale = 2.0; m.proc_detail = 3.0
    m.proc_contrast = 0.72; m.proc_ramp_center = 0.55
    m.proc_color1 = (0.35, 0.34, 0.32, 1.0)   # "no mare" = highland tone
    m.proc_color2 = (0.05, 0.05, 0.066, 1.0)  # mare basalt (dark)
    m.opacity = 1.0; m.blend_mode = "MIX"

    # 04-07 — crater relief, four scales (bump-only — see notes #2)
    _crater(mat, "04 Big Craters",   5.0,  1.00, 0.020)
    _crater(mat, "05 Med Craters",   11.0, 0.95, 0.012)
    _crater(mat, "06 Small Craters", 24.0, 0.55, 0.008)
    _crater(mat, "07 Tiny Craters",  55.0, 0.20, 0.004)

    # 08 — subtle dust mottle
    d = _add(mat, "PROCEDURAL"); d.name = "08 Dust Mottle"
    d.proc_type = "NOISE"; d.proc_coord_type = "OBJECT"
    d.proc_use_manual_stops = False
    d.proc_scale = 5.0; d.proc_detail = 6.0
    d.proc_color1 = (0.27, 0.26, 0.25, 1.0)
    d.proc_color2 = (0.34, 0.33, 0.31, 1.0)
    d.opacity = 0.08; d.blend_mode = "OVERLAY"

    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"[TLM] Moon built — {len(tlm.layers)} layers")
    return mat


if __name__ == "__main__":
    build_moon()
    _setup_moon_lighting()
