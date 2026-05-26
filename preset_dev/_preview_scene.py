"""
TLM — Preset Preview Scene Builder
===================================

Run-once script that builds the canonical preview scene used to render
every Showcase Pro preset for the commercial pack.

How to use:
  1. Open Blender 5.0 (or 4.2+).
  2. Window > File > New > General (or just any empty scene).
  3. Open this file in the Text Editor and press "Run Script".
  4. The scene is built and saved as preset_dev/_preview_scene.blend.
  5. Open _preview_scene.blend whenever you want to preview a preset:
     select TLM_Sphere → run the preset script → render.

What's in the scene:
  - TLM_Sphere    : UV sphere, 64 segments, smooth-shaded. Hero subject.
  - TLM_Cube      : Bevelled cube, 0.05 bevel, 3 segments. Shows flat panels.
  - TLM_Panel     : Flat plane (1 m × 0.6 m), tilted slightly. Shows
                    veins / scratches / weave in raking light.
  - TLM_Ground    : Large dark backdrop plane (matte black material).
  - Lights        : 3-point area lighting (key/fill/rim) tuned to expose
                    PBR storytelling (roughness shifts, metal vs paint).
  - Camera        : Fixed 3/4 angle, slight depth-of-field, looks at the
                    sphere as the hero subject.
  - World         : Neutral grey environment, low strength (lighting
                    comes from the area lights, world only fills the
                    deepest shadow recesses).
  - Render        : Cycles 256 samples, denoiser ON, color management
                    Filmic / Medium High Contrast. Output 1080×1080 by
                    default (override per-preset for hero shots).

Re-run safe: deletes any TLM_* objects in the current scene before
rebuilding, so iterating on the scene design doesn't pollute the file.
"""

import bpy
import math
import os
from mathutils import Vector


# ─── Settings ────────────────────────────────────────────────────────────────

SCENE_FILE_NAME = "_preview_scene.blend"

# Object names — all prefixed with TLM_ so re-run can wipe them safely.
SPHERE_NAME = "TLM_Sphere"
CUBE_NAME   = "TLM_Cube"
PANEL_NAME  = "TLM_Panel"
GROUND_NAME = "TLM_Ground"

KEY_LIGHT_NAME  = "TLM_KeyLight"
FILL_LIGHT_NAME = "TLM_FillLight"
RIM_LIGHT_NAME  = "TLM_RimLight"

CAMERA_NAME = "TLM_Camera"

# Render settings (override per-preset if needed)
DEFAULT_RESOLUTION = (1080, 1080)
DEFAULT_SAMPLES_CYCLES = 256
DEFAULT_SAMPLES_EEVEE = 64


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _purge_tlm_objects():
    """Remove all objects whose name starts with TLM_ from the active scene."""
    to_remove = [obj for obj in bpy.data.objects if obj.name.startswith("TLM_")]
    for obj in to_remove:
        bpy.data.objects.remove(obj, do_unlink=True)


def _new_mesh_object(name, mesh):
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


# ─── Geometry ────────────────────────────────────────────────────────────────

def _build_sphere():
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=64, ring_count=32, radius=0.5,
        location=(0.0, 0.0, 0.5),
    )
    obj = bpy.context.active_object
    obj.name = SPHERE_NAME
    # Smooth shade
    for poly in obj.data.polygons:
        poly.use_smooth = True
    return obj


def _build_cube():
    bpy.ops.mesh.primitive_cube_add(size=0.7, location=(1.1, -0.3, 0.35))
    obj = bpy.context.active_object
    obj.name = CUBE_NAME
    # Bevel modifier — gives roundness on edges so highlights are readable.
    bev = obj.modifiers.new(name="Bevel", type='BEVEL')
    bev.width = 0.04
    bev.segments = 4
    bev.profile = 0.7
    return obj


def _build_panel():
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(-1.1, -0.2, 0.55))
    obj = bpy.context.active_object
    obj.name = PANEL_NAME
    obj.scale = (1.0, 0.6, 1.0)
    # Stand the panel up like an easel board so raking light reveals
    # surface texture.
    obj.rotation_euler = (math.radians(80.0), 0.0, math.radians(20.0))
    # Subdivide once so displacement / future bump tests have geometry to bend.
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.subdivide(number_cuts=4)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


def _build_ground():
    bpy.ops.mesh.primitive_plane_add(size=12.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = GROUND_NAME

    # Matte dark backdrop — keeps focus on the preview subjects.
    mat = bpy.data.materials.get("TLM_GroundMat")
    if mat is None:
        mat = bpy.data.materials.new("TLM_GroundMat")
        mat.use_nodes = True
        nt = mat.node_tree
        # Clear default
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)
        bsdf.inputs["Base Color"].default_value = (0.04, 0.04, 0.045, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.85
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    obj.data.materials.append(mat)
    return obj


# ─── Lights (3-point) ────────────────────────────────────────────────────────

def _add_area_light(name, location, rotation_deg, size, energy, color=(1, 1, 1)):
    light_data = bpy.data.lights.new(name=name, type='AREA')
    light_data.shape = 'RECTANGLE'
    light_data.size = size
    light_data.size_y = size * 0.6
    light_data.energy = energy
    light_data.color = color
    obj = bpy.data.objects.new(name, light_data)
    bpy.context.collection.objects.link(obj)
    obj.location = Vector(location)
    obj.rotation_euler = tuple(math.radians(d) for d in rotation_deg)
    return obj


def _build_lights():
    # Key light: warm, strong, front-right, slightly above.
    # Position chosen to rake across the sphere's right side and catch
    # the bevelled cube's edges.
    _add_area_light(
        KEY_LIGHT_NAME,
        location=(2.5, -2.5, 3.0),
        rotation_deg=(-35, 30, 0),
        size=1.8,
        energy=420.0,
        color=(1.00, 0.96, 0.90),  # warm tungsten-leaning
    )

    # Fill light: cooler, dimmer, opposite side. Lifts shadows so dark
    # dielectrics don't crush to black on the camera-left side.
    _add_area_light(
        FILL_LIGHT_NAME,
        location=(-2.5, -2.0, 2.0),
        rotation_deg=(-30, -25, 0),
        size=2.4,
        energy=120.0,
        color=(0.85, 0.92, 1.00),  # cool sky-leaning
    )

    # Rim light: behind subject, high. Defines silhouettes and catches
    # the back edge of the sphere. Critical for showing metallic clean
    # rim reflections on the bronze/marble heroes.
    _add_area_light(
        RIM_LIGHT_NAME,
        location=(0.0, 2.2, 2.6),
        rotation_deg=(-130, 0, 0),
        size=1.4,
        energy=320.0,
        color=(1.00, 1.00, 1.00),
    )


# ─── Camera ──────────────────────────────────────────────────────────────────

def _build_camera():
    cam_data = bpy.data.cameras.new(name=CAMERA_NAME)
    cam_data.lens = 60.0  # slightly long — flatters spheres, reduces distortion
    cam_data.sensor_width = 36.0
    cam_data.dof.use_dof = True
    cam_data.dof.aperture_fstop = 5.6

    cam = bpy.data.objects.new(CAMERA_NAME, cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = Vector((2.6, -3.4, 1.6))
    # Aim at the sphere centre
    target = Vector((0.0, 0.0, 0.5))
    direction = target - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

    # Auto-focus on the sphere
    cam_data.dof.focus_distance = direction.length

    bpy.context.scene.camera = cam
    return cam


# ─── World ───────────────────────────────────────────────────────────────────

def _build_world():
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("TLM_World")
        bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (300, 0)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.location = (0, 0)
    # Very neutral, very dim — area lights do the work.
    bg.inputs["Color"].default_value = (0.05, 0.055, 0.06, 1.0)
    bg.inputs["Strength"].default_value = 0.3
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


# ─── Render settings ─────────────────────────────────────────────────────────

def _configure_render():
    scene = bpy.context.scene
    # Default to Cycles for the canonical hero render (PBR-correct,
    # better caustics + transmission handling). Eevee Next is the
    # secondary check.
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = DEFAULT_SAMPLES_CYCLES
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = 'OPENIMAGEDENOISE'
    # Resolution
    scene.render.resolution_x = DEFAULT_RESOLUTION[0]
    scene.render.resolution_y = DEFAULT_RESOLUTION[1]
    scene.render.resolution_percentage = 100
    # Color management
    scene.view_settings.view_transform = 'Filmic'
    scene.view_settings.look = 'Medium High Contrast'
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    # Output
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.film_transparent = False

    # Eevee Next backup
    if hasattr(scene, 'eevee'):
        scene.eevee.taa_render_samples = DEFAULT_SAMPLES_EEVEE
        scene.eevee.use_shadows = True


# ─── Save ────────────────────────────────────────────────────────────────────

def _save_scene():
    """Save the scene as preset_dev/_preview_scene.blend (next to this file)."""
    here = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(here, SCENE_FILE_NAME)
    bpy.ops.wm.save_as_mainfile(filepath=out_path)
    print(f"[TLM Preview Scene] Saved to {out_path}")


# ─── Entry point ─────────────────────────────────────────────────────────────

def build_preview_scene(save=True):
    """Build the canonical TLM preset preview scene.

    Parameters
    ----------
    save : bool
        If True, save the scene to preset_dev/_preview_scene.blend.
        Set to False when iterating in an open Blender session.
    """
    print("[TLM Preview Scene] Building…")
    _purge_tlm_objects()

    _build_ground()
    _build_sphere()
    _build_cube()
    _build_panel()

    _build_lights()
    _build_camera()
    _build_world()
    _configure_render()

    # Select the sphere as the default active object — preset scripts
    # apply to bpy.context.active_object, so this primes the workflow.
    bpy.ops.object.select_all(action='DESELECT')
    sphere = bpy.data.objects.get(SPHERE_NAME)
    if sphere is not None:
        sphere.select_set(True)
        bpy.context.view_layer.objects.active = sphere

    if save:
        _save_scene()

    print("[TLM Preview Scene] Done. Active object: TLM_Sphere")


if __name__ == "__main__":
    build_preview_scene(save=True)
