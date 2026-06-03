"""
TLM preset render rig
======================
A consistent stage for the website's preset thumbnails: a smooth Suzanne, a
fixed 3-point studio light set, a fixed camera, and locked render settings
(800x800, Cycles, AgX, transparent film). Every preset is rendered on the
SAME object under the SAME light, so the gallery looks coherent.

Workflow
--------
    from <path> import render_rig as R   (or run this file once)
    R.setup_rig()                         # build / refresh the stage (idempotent)
    # → apply a preset material to the object 'TLM_PresetRig' (TLM UI)
    R.prep("bronze-verdigris-patina")     # isolates the rig + sets the output path
    # → F12,  OR:
    R.render_current("bronze-verdigris-patina")   # prep + render in one call

Filenames must match the website slugs (see SLUGS). Output lands directly in
the website's assets/images/presets/ folder.
"""

import bpy, os, math, mathutils

WEBSITE_PRESETS = r"C:\Users\Riccardo\tlm-website\assets\images\presets"
RIG_OBJ  = "TLM_PresetRig"
RIG_CAM  = "TLM_RigCam"
RIG_LIGHTS = ("TLM_Rig_Key", "TLM_Rig_Fill", "TLM_Rig_Rim")

# The 13 preset slugs the gallery expects (LED dropped, Moon added).
SLUGS = [
    "anime-genshin-hero", "bronze-verdigris-patina", "burn-dissolve",
    "galaxy-marble", "holo-card", "inverted-checker", "mars", "mirror",
    "moon", "oil-slick", "plasma-core", "watercolor", "wireframe",
]

_CENTER = mathutils.Vector((0.0, 0.0, 1.2))


def _aim(obj, target=_CENTER):
    obj.rotation_euler = (target - obj.location).to_track_quat('-Z', 'Y').to_euler()


def setup_rig():
    """Build (or refresh) Suzanne + camera + 3-point area lights. Idempotent."""
    sc = bpy.context.scene

    # ── Suzanne, subdivided + smooth ──
    obj = bpy.data.objects.get(RIG_OBJ)
    if obj is None:
        bpy.ops.mesh.primitive_monkey_add(size=2.2, location=_CENTER)
        obj = bpy.context.active_object
        obj.name = RIG_OBJ
        obj.rotation_euler = (0.0, 0.0, math.radians(18))
        for p in obj.data.polygons:
            p.use_smooth = True
        if not any(m.type == 'SUBSURF' for m in obj.modifiers):
            m = obj.modifiers.new("Subsurf", 'SUBSURF')
            m.levels = 2
            m.render_levels = 2
    obj.hide_render = False

    # ── Camera (3/4, 85mm) ──
    cam = bpy.data.objects.get(RIG_CAM)
    if cam is None:
        cam = bpy.data.objects.new(RIG_CAM, bpy.data.cameras.new(RIG_CAM))
        sc.collection.objects.link(cam)
    cam.location = (0.7, -5.3, 1.55)
    cam.data.lens = 50
    _aim(cam)

    # ── 3-point area lights (neutral white) ──
    def area(name, loc, energy, size):
        o = bpy.data.objects.get(name)
        if o is None:
            o = bpy.data.objects.new(name, bpy.data.lights.new(name, 'AREA'))
            sc.collection.objects.link(o)
        o.data.energy = energy
        o.data.size = size
        o.data.color = (1.0, 1.0, 1.0)
        o.location = loc
        _aim(o)
        o.hide_render = False
        return o

    area("TLM_Rig_Key",  (-3.4, -2.6,  3.4), 1100, 3.0)   # key  (upper front-left)
    area("TLM_Rig_Fill", ( 3.4, -1.6,  1.4),  280, 3.5)   # fill (right, soft)
    area("TLM_Rig_Rim",  ( 0.4,  3.2,  3.2),  650, 2.5)   # rim  (behind, top)

    # ── Soft neutral world so emissive materials still read ──
    wd = sc.world
    if wd and wd.use_nodes:
        bg = next((n for n in wd.node_tree.nodes if n.type == 'BACKGROUND'), None)
        if bg:
            bg.inputs[0].default_value = (0.05, 0.05, 0.055, 1.0)
            bg.inputs[1].default_value = 0.6

    print(f"[TLM] preset render rig ready — apply a material to '{RIG_OBJ}', "
          f"then prep('<slug>') + F12")
    return obj


def prep(slug):
    """Isolate the rig, lock render settings, and point the output at <slug>.png."""
    sc = bpy.context.scene
    keep = {RIG_OBJ, RIG_CAM, *RIG_LIGHTS}
    for o in sc.objects:
        if o.type in ('MESH', 'LIGHT'):
            o.hide_render = o.name not in keep
    sc.camera = bpy.data.objects[RIG_CAM]
    sc.render.engine = 'CYCLES'
    sc.cycles.samples = 128
    sc.render.resolution_x = 800
    sc.render.resolution_y = 800
    sc.render.film_transparent = True
    try:
        sc.view_settings.view_transform = 'AgX'
    except Exception:
        pass
    os.makedirs(WEBSITE_PRESETS, exist_ok=True)
    sc.render.filepath = os.path.join(WEBSITE_PRESETS, f"{slug}.png")
    return sc.render.filepath


def render_current(slug):
    """prep(slug) then render the material currently on TLM_PresetRig."""
    fp = prep(slug)
    bpy.ops.render.render(write_still=True)
    print(f"[TLM] rendered → {fp}")
    return fp


if __name__ == "__main__":
    setup_rig()
