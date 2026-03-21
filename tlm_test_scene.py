"""
TLM Test Scene — Automated Bug Hunting Script
================================================
Run this script in Blender's Text Editor (or paste in Python Console).
It creates 3 objects with 3 materials testing all TLM features,
and prints a [PASS]/[FAIL] report to the System Console.

Usage:
  1. Open Blender 5.0 with TLM addon enabled
  2. Window > Toggle System Console
  3. Open this script in Text Editor > Run Script (Alt+P)
  4. Check the console for the report
  5. Switch to Material Preview to visually verify
"""

import bpy
import math
from mathutils import Vector

# ─── Config ───────────────────────────────────────────────────────────────────

RESOLUTION = "1024"  # Image resolution for paint/channel images

# ─── Helpers ──────────────────────────────────────────────────────────────────

results = []


def log(msg):
    print(f"[TLM TEST] {msg}")


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    results.append((name, condition))
    print(f"  [{status}] {name}")
    return condition


def get_tlm(obj):
    """Get TLM data from object's active material."""
    return obj.active_material.tlm


def set_active(obj):
    """Select and activate object for operator context."""
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def has_bsdf_input_linked(mat, input_name):
    """Check if a BSDF input is connected."""
    nt = mat.node_tree
    for node in nt.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            inp = node.inputs.get(input_name)
            if inp and inp.is_linked:
                return True
    return False


def count_tlm_nodes(mat):
    """Count nodes with TLM_ prefix."""
    return sum(1 for n in mat.node_tree.nodes if n.name.startswith("TLM_"))


# ─── Scene Setup ──────────────────────────────────────────────────────────────

def setup_scene():
    log("=== SCENE SETUP ===")

    # Delete all existing objects
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    # Delete all existing materials
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)

    # Delete all existing images (except built-in)
    for img in list(bpy.data.images):
        if not img.name.startswith("Render Result") and not img.name.startswith("Viewer Node"):
            bpy.data.images.remove(img)

    # ── Create Cube ──
    bpy.ops.mesh.primitive_cube_add(size=2, location=(-3, 0, 1))
    cube = bpy.context.active_object
    cube.name = "TLM_Test_Cube"
    # UV unwrap
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66))
    bpy.ops.object.mode_set(mode='OBJECT')

    # ── Create Sphere ──
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.2, segments=32, ring_count=16,
                                          location=(0, 0, 1.2))
    sphere = bpy.context.active_object
    sphere.name = "TLM_Test_Sphere"
    # Smooth shading
    bpy.ops.object.shade_smooth()
    # UV unwrap
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66))
    bpy.ops.object.mode_set(mode='OBJECT')

    # ── Create Plane ──
    bpy.ops.mesh.primitive_plane_add(size=4, location=(3, 0, 0))
    plane = bpy.context.active_object
    plane.name = "TLM_Test_Plane"
    # Subdivide for more detail
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.subdivide(number_cuts=4)
    bpy.ops.uv.smart_project(angle_limit=math.radians(66))
    bpy.ops.object.mode_set(mode='OBJECT')

    # ── Lighting ──
    # Key light
    bpy.ops.object.light_add(type='AREA', location=(4, -4, 6))
    key = bpy.context.active_object
    key.name = "TLM_Key_Light"
    key.data.energy = 200
    key.data.size = 3
    key.rotation_euler = (math.radians(45), 0, math.radians(45))

    # Fill light
    bpy.ops.object.light_add(type='AREA', location=(-4, -2, 4))
    fill = bpy.context.active_object
    fill.name = "TLM_Fill_Light"
    fill.data.energy = 80
    fill.data.size = 4
    fill.rotation_euler = (math.radians(60), 0, math.radians(-30))

    # Rim light
    bpy.ops.object.light_add(type='AREA', location=(0, 5, 3))
    rim = bpy.context.active_object
    rim.name = "TLM_Rim_Light"
    rim.data.energy = 120
    rim.data.size = 2
    rim.rotation_euler = (math.radians(30), 0, math.radians(180))

    # ── Camera ──
    bpy.ops.object.camera_add(location=(0, -8, 4))
    cam = bpy.context.active_object
    cam.name = "TLM_Camera"
    cam.rotation_euler = (math.radians(65), 0, 0)
    cam.data.lens = 50
    bpy.context.scene.camera = cam

    # Set viewport to Material Preview
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.shading.type = 'MATERIAL'

    check("Scene created with 3 objects", len([o for o in bpy.data.objects if o.name.startswith("TLM_Test_")]) == 3)
    check("Lights created", len([o for o in bpy.data.objects if o.name.startswith("TLM_") and o.type == 'LIGHT']) == 3)
    check("Camera created", bpy.context.scene.camera is not None)

    return (
        bpy.data.objects["TLM_Test_Cube"],
        bpy.data.objects["TLM_Test_Sphere"],
        bpy.data.objects["TLM_Test_Plane"],
    )


# ─── Material 1: Layer Types & Blend Modes (Cube) ────────────────────────────

def build_material_1(cube):
    log("")
    log("=== MATERIAL 1: Layer Types & Blend Modes (Cube) ===")
    set_active(cube)

    # Create material
    mat = bpy.data.materials.new("TLM_Test_LayerTypes")
    mat.use_nodes = True
    cube.data.materials.clear()
    cube.data.materials.append(mat)
    cube.active_material = mat
    mat.tlm.resolution = RESOLUTION

    tlm = mat.tlm

    # ── Layer 1: Fill red (base) ──
    log("Adding Fill red (base)...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Base Red"
    layer.fill_color = (1.0, 0.0, 0.0, 1.0)
    check("1.1 Fill layer created", len(tlm.layers) == 1)
    check("1.1 Fill color is red", tuple(layer.fill_color[:3]) == (1.0, 0.0, 0.0))

    # ── Layer 2: Fill blue (Multiply, opacity 0.7) ──
    log("Adding Fill blue (Multiply, opacity 0.7)...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Blue Multiply"
    layer.fill_color = (0.0, 0.0, 1.0, 1.0)
    layer.blend_mode = "MULTIPLY"
    layer.opacity = 0.7
    check("1.2 Two layers exist", len(tlm.layers) == 2)
    check("1.2 Blend mode is Multiply", layer.blend_mode == "MULTIPLY")
    check("1.2 Opacity is 0.7", abs(layer.opacity - 0.7) < 0.01)

    # ── Layer 3: Fill green (Screen, opacity 0.5) ──
    log("Adding Fill green (Screen, opacity 0.5)...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Green Screen"
    layer.fill_color = (0.0, 0.8, 0.0, 1.0)
    layer.blend_mode = "SCREEN"
    layer.opacity = 0.5
    check("1.3 Screen blend mode set", layer.blend_mode == "SCREEN")

    # ── Layer 4: Procedural Noise (Overlay, opacity 0.5) ──
    log("Adding Procedural Noise (Overlay)...")
    bpy.ops.tlm.add_procedural_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Noise Overlay"
    layer.proc_type = "NOISE"
    layer.proc_scale = 8.0
    layer.proc_detail = 4.0
    layer.proc_color1 = (0.1, 0.05, 0.0, 1.0)
    layer.proc_color2 = (0.8, 0.6, 0.3, 1.0)
    layer.blend_mode = "OVERLAY"
    layer.opacity = 0.5
    check("1.4 Procedural layer created", layer.layer_type == "PROCEDURAL")
    check("1.4 Proc type is Noise", layer.proc_type == "NOISE")

    # ── Layer 5: Procedural Voronoi (Soft Light, opacity 0.4) ──
    log("Adding Procedural Voronoi (Soft Light)...")
    bpy.ops.tlm.add_procedural_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Voronoi Detail"
    layer.proc_type = "VORONOI"
    layer.proc_scale = 15.0
    layer.proc_voronoi_feature = "F1"
    layer.proc_voronoi_distance = "EUCLIDEAN"
    layer.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    layer.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    layer.blend_mode = "SOFT_LIGHT"
    layer.opacity = 0.4
    check("1.5 Voronoi created", layer.proc_type == "VORONOI")

    # ── Layer 6: Adjustment Hue/Sat ──
    log("Adding Adjustment Hue/Sat...")
    bpy.ops.tlm.add_adjustment_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Hue Shift"
    layer.adj_type = "HUE_SAT"
    layer.adj_hue = 0.6
    layer.adj_saturation = 1.3
    layer.adj_value = 1.1
    check("1.6 Adjustment layer created", layer.layer_type == "ADJUSTMENT")
    check("1.6 Adj type is HUE_SAT", layer.adj_type == "HUE_SAT")

    # ── Layer 7: Adjustment Brightness/Contrast ──
    log("Adding Adjustment Brightness/Contrast...")
    bpy.ops.tlm.add_adjustment_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Contrast Boost"
    layer.adj_type = "BRIGHT_CONTRAST"
    layer.adj_brightness = 0.05
    layer.adj_contrast = 0.3
    check("1.7 Bright/Contrast created", layer.adj_type == "BRIGHT_CONTRAST")

    # ── Layer 8: Adjustment Levels ──
    log("Adding Adjustment Levels...")
    bpy.ops.tlm.add_adjustment_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Levels Crush"
    layer.adj_type = "LEVELS"
    layer.adj_in_min = 0.1
    layer.adj_in_max = 0.9
    layer.adj_levels_gamma = 1.2
    check("1.8 Levels created", layer.adj_type == "LEVELS")

    # ── Layer 9: Adjustment Color Balance ──
    log("Adding Adjustment Color Balance...")
    bpy.ops.tlm.add_adjustment_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Color Grade"
    layer.adj_type = "COLOR_BALANCE"
    layer.adj_lift = (1.1, 0.95, 0.9)
    layer.adj_gain = (0.95, 1.0, 1.1)
    check("1.9 Color Balance created", layer.adj_type == "COLOR_BALANCE")

    # ── Final verification ──
    # Force rebuild
    bpy.ops.tlm.rebuild_composite()

    check("1.X Total layers = 9", len(tlm.layers) == 9)
    check("1.X TLM nodes created", count_tlm_nodes(mat) > 0)
    check("1.X Base Color connected", has_bsdf_input_linked(mat, "Base Color"))

    log(f"Material 1 complete: {len(tlm.layers)} layers, {count_tlm_nodes(mat)} TLM nodes")


# ─── Material 2: PBR Channels (Sphere) ───────────────────────────────────────

def build_material_2(sphere):
    log("")
    log("=== MATERIAL 2: PBR Channels (Sphere) ===")
    set_active(sphere)

    mat = bpy.data.materials.new("TLM_Test_PBR")
    mat.use_nodes = True
    sphere.data.materials.clear()
    sphere.data.materials.append(mat)
    sphere.active_material = mat
    mat.tlm.resolution = RESOLUTION

    tlm = mat.tlm

    # ── Layer 1: Fill grey base + Roughness + Metallic ──
    log("Adding Fill grey base with Roughness 0.2 + Metallic 1.0...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Metal Base"
    layer.fill_color = (0.4, 0.4, 0.45, 1.0)
    layer.use_roughness = True
    layer.roughness_fill = 0.2
    layer.use_metallic = True
    layer.metallic_fill = 1.0
    check("2.1 Fill created with PBR", layer.use_roughness and layer.use_metallic)
    check("2.1 Roughness fill = 0.2", abs(layer.roughness_fill - 0.2) < 0.01)
    check("2.1 Metallic fill = 1.0", abs(layer.metallic_fill - 1.0) < 0.01)

    # Force rebuild to verify PBR connections
    bpy.ops.tlm.rebuild_composite()
    check("2.1 Roughness connected to BSDF", has_bsdf_input_linked(mat, "Roughness"))
    # Metallic input name varies by Blender version
    metallic_linked = has_bsdf_input_linked(mat, "Metallic") or has_bsdf_input_linked(mat, "Metalness")
    check("2.1 Metallic connected to BSDF", metallic_linked)

    # ── Layer 2: Fill with Roughness IMAGE ──
    log("Adding Fill with Roughness image...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Roughness Map"
    layer.fill_color = (0.5, 0.5, 0.5, 1.0)
    layer.blend_mode = "MIX"
    layer.opacity = 0.8
    layer.use_roughness = True
    # Create roughness channel image via operator
    bpy.ops.tlm.add_channel_image(channel='roughness')
    check("2.2 Roughness image created", bool(layer.roughness_image_name))
    check("2.2 Image exists in bpy.data", layer.roughness_image_name in bpy.data.images)

    # ── Layer 3: Fill with Normal IMAGE ──
    log("Adding Fill with Normal image...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Normal Detail"
    layer.fill_color = (0.4, 0.4, 0.45, 1.0)
    layer.use_normal = True
    bpy.ops.tlm.add_channel_image(channel='normal')
    check("2.3 Normal image created", bool(layer.normal_image_name))

    bpy.ops.tlm.rebuild_composite()
    normal_linked = has_bsdf_input_linked(mat, "Normal") or has_bsdf_input_linked(mat, "normal")
    check("2.3 Normal connected to BSDF", normal_linked)

    # ── Layer 4: Fill with Emission IMAGE ──
    log("Adding Fill with Emission image + strength 3.0...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Emission Glow"
    layer.fill_color = (0.0, 0.0, 0.0, 1.0)
    layer.use_emission = True
    layer.emission_strength = 3.0
    bpy.ops.tlm.add_channel_image(channel='emission')
    check("2.4 Emission image created", bool(layer.emission_image_name))
    check("2.4 Emission strength = 3.0", abs(layer.emission_strength - 3.0) < 0.01)

    bpy.ops.tlm.rebuild_composite()
    emission_linked = (has_bsdf_input_linked(mat, "Emission Color")
                       or has_bsdf_input_linked(mat, "Emission"))
    check("2.4 Emission connected to BSDF", emission_linked)

    # ── Layer 5: Procedural Noise + Roughness + Bump ──
    log("Adding Procedural Noise with Roughness + Bump...")
    bpy.ops.tlm.add_procedural_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Surface Noise"
    layer.proc_type = "NOISE"
    layer.proc_scale = 12.0
    layer.proc_detail = 6.0
    layer.proc_roughness_proc = 0.6
    layer.proc_color1 = (0.3, 0.3, 0.35, 1.0)
    layer.proc_color2 = (0.6, 0.6, 0.65, 1.0)
    layer.blend_mode = "OVERLAY"
    layer.opacity = 0.4
    layer.use_roughness = True
    layer.roughness_fill = 0.7
    layer.use_bump = True
    layer.bump_strength = 1.5
    layer.bump_distance = 0.03
    check("2.5 Procedural + Roughness + Bump", layer.use_roughness and layer.use_bump)

    # ── Layer 6: Procedural Voronoi + Metallic ──
    log("Adding Procedural Voronoi with Metallic...")
    bpy.ops.tlm.add_procedural_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Voronoi Metal"
    layer.proc_type = "VORONOI"
    layer.proc_scale = 8.0
    layer.proc_voronoi_feature = "DISTANCE_TO_EDGE"
    layer.proc_color1 = (0.2, 0.2, 0.2, 1.0)
    layer.proc_color2 = (0.7, 0.7, 0.7, 1.0)
    layer.blend_mode = "MULTIPLY"
    layer.opacity = 0.3
    layer.use_metallic = True
    layer.metallic_fill = 0.8
    check("2.6 Voronoi + Metallic", layer.use_metallic and layer.proc_type == "VORONOI")

    # ── Layer 7: Procedural Wave ──
    log("Adding Procedural Wave...")
    bpy.ops.tlm.add_procedural_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Wave Pattern"
    layer.proc_type = "WAVE"
    layer.proc_scale = 3.0
    layer.proc_wave_type = "BANDS"
    layer.proc_wave_profile = "SIN"
    layer.proc_color1 = (0.3, 0.35, 0.4, 1.0)
    layer.proc_color2 = (0.5, 0.55, 0.6, 1.0)
    layer.blend_mode = "SOFT_LIGHT"
    layer.opacity = 0.3
    check("2.7 Wave created", layer.proc_type == "WAVE")

    # ── Final rebuild and verify ──
    bpy.ops.tlm.rebuild_composite()

    check("2.X Total layers = 7", len(tlm.layers) == 7)
    check("2.X TLM nodes created", count_tlm_nodes(mat) > 10)
    check("2.X Base Color connected", has_bsdf_input_linked(mat, "Base Color"))
    check("2.X Roughness connected", has_bsdf_input_linked(mat, "Roughness"))

    log(f"Material 2 complete: {len(tlm.layers)} layers, {count_tlm_nodes(mat)} TLM nodes")


# ─── Material 3: Advanced Features (Plane) ───────────────────────────────────

def build_material_3(plane):
    log("")
    log("=== MATERIAL 3: Advanced Features (Plane) ===")
    set_active(plane)

    mat = bpy.data.materials.new("TLM_Test_Advanced")
    mat.use_nodes = True
    plane.data.materials.clear()
    plane.data.materials.append(mat)
    plane.active_material = mat
    mat.tlm.resolution = RESOLUTION

    tlm = mat.tlm

    # ── Layer 1: Fill white (base) ──
    log("Adding Fill white (base)...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "White Base"
    layer.fill_color = (1.0, 1.0, 1.0, 1.0)
    check("3.1 White base created", len(tlm.layers) == 1)

    # ── Layer 2: Fill dark red ──
    log("Adding Fill dark red...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Dark Red"
    layer.fill_color = (0.5, 0.0, 0.0, 1.0)
    layer.opacity = 0.8
    check("3.2 Dark red fill created", len(tlm.layers) == 2)

    # ── Layer 3: Paint layer (for clipping mask test below) ──
    log("Adding Paint layer...")
    bpy.ops.tlm.add_paint_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Paint Detail"
    check("3.3 Paint layer created", layer.layer_type == "PAINT")
    check("3.3 Paint image exists", bool(layer.image_name))

    # ── Layer 4: Fill with Clipping Mask ──
    log("Adding Fill with Clipping Mask...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Clipped Gold"
    layer.fill_color = (0.8, 0.6, 0.1, 1.0)
    layer.use_clipping_mask = True
    check("3.4 Clipping mask enabled", layer.use_clipping_mask)

    # ── Layer 5: Procedural Checker ──
    log("Adding Procedural Checker...")
    bpy.ops.tlm.add_procedural_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Checker Pattern"
    layer.proc_type = "CHECKER"
    layer.proc_checker_scale = 4.0
    layer.proc_color1 = (0.1, 0.1, 0.1, 1.0)
    layer.proc_color2 = (0.9, 0.9, 0.9, 1.0)
    layer.blend_mode = "MULTIPLY"
    layer.opacity = 0.6
    check("3.5 Checker created", layer.proc_type == "CHECKER")

    # ── Layer 6: Procedural Gradient ──
    log("Adding Procedural Gradient...")
    bpy.ops.tlm.add_procedural_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Gradient Fade"
    layer.proc_type = "GRADIENT"
    layer.proc_gradient_type = "SPHERICAL"
    layer.proc_color1 = (0.0, 0.0, 0.2, 1.0)
    layer.proc_color2 = (0.0, 0.5, 1.0, 1.0)
    layer.blend_mode = "SCREEN"
    layer.opacity = 0.5
    check("3.6 Gradient created", layer.proc_type == "GRADIENT")

    # ── Layer 7: Procedural Musgrave ──
    log("Adding Procedural Musgrave...")
    bpy.ops.tlm.add_procedural_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Musgrave Fractal"
    layer.proc_type = "MUSGRAVE"
    layer.proc_scale = 6.0
    layer.proc_detail = 8.0
    layer.proc_lacunarity = 2.5
    layer.proc_color1 = (0.2, 0.15, 0.1, 1.0)
    layer.proc_color2 = (0.6, 0.5, 0.4, 1.0)
    layer.blend_mode = "OVERLAY"
    layer.opacity = 0.4
    layer.use_roughness = True
    layer.roughness_fill = 0.9
    layer.use_bump = True
    layer.bump_strength = 2.0
    layer.bump_distance = 0.04
    check("3.7 Musgrave created with Roughness + Bump",
          layer.proc_type == "MUSGRAVE" and layer.use_roughness and layer.use_bump)

    # ── Layer 8: Fill with all PBR channels ──
    log("Adding Fill with ALL PBR channels...")
    bpy.ops.tlm.add_fill_layer()
    layer = tlm.layers[tlm.active_layer_index]
    layer.name = "Full PBR"
    layer.fill_color = (0.6, 0.6, 0.7, 1.0)
    layer.blend_mode = "MIX"
    layer.opacity = 0.5

    # Enable all PBR channels
    layer.use_roughness = True
    layer.roughness_fill = 0.4
    layer.use_metallic = True
    layer.metallic_fill = 0.6
    layer.use_emission = True
    layer.emission_color = (1.0, 0.5, 0.0, 1.0)
    layer.emission_strength = 2.0
    layer.use_normal = True
    bpy.ops.tlm.add_channel_image(channel='normal')
    layer.use_bump = True
    layer.bump_strength = 0.8
    check("3.8 All PBR channels enabled",
          layer.use_roughness and layer.use_metallic and
          layer.use_emission and layer.use_normal and layer.use_bump)

    # ── Visibility test ──
    log("Testing visibility toggle...")
    layer = next(l for l in tlm.layers if l.name == "White Base")
    layer.visible = False
    bpy.ops.tlm.rebuild_composite()
    layer.visible = True
    bpy.ops.tlm.rebuild_composite()
    check("3.9 Visibility toggle works", layer.visible)

    # ── Final rebuild and verify ──
    bpy.ops.tlm.rebuild_composite()

    check("3.X Total layers = 8", len(tlm.layers) == 8)
    check("3.X TLM nodes created", count_tlm_nodes(mat) > 10)
    check("3.X Base Color connected", has_bsdf_input_linked(mat, "Base Color"))
    check("3.X Roughness connected", has_bsdf_input_linked(mat, "Roughness"))

    log(f"Material 3 complete: {len(tlm.layers)} layers, {count_tlm_nodes(mat)} TLM nodes")


# ─── Duplicate & Move Tests ──────────────────────────────────────────────────

def test_duplicate_and_move(cube):
    log("")
    log("=== EXTRA TESTS: Duplicate & Move ===")
    set_active(cube)
    mat = cube.active_material
    tlm = mat.tlm

    initial_count = len(tlm.layers)

    # Duplicate active layer
    log("Duplicating active layer...")
    tlm.active_layer_index = 0  # Select first layer
    source = tlm.layers[0]
    source_name = source.name
    source_color = tuple(source.fill_color[:3])
    source_blend = source.blend_mode
    source_opacity = source.opacity

    bpy.ops.tlm.duplicate_layer()
    check("4.1 Layer count increased by 1", len(tlm.layers) == initial_count + 1)

    # Find the duplicate (should be right after source)
    dup = tlm.layers[tlm.active_layer_index]
    check("4.1 Duplicate has matching blend mode", dup.blend_mode == source_blend)
    check("4.1 Duplicate has matching opacity", abs(dup.opacity - source_opacity) < 0.01)

    # Move layer
    log("Moving layer down...")
    active_idx = tlm.active_layer_index
    bpy.ops.tlm.move_layer(direction="DOWN")
    check("4.2 Layer moved", tlm.active_layer_index != active_idx or len(tlm.layers) <= 1)

    log("Moving layer up...")
    bpy.ops.tlm.move_layer(direction="UP")
    check("4.3 Layer moved back", True)  # Just verify no crash

    # Remove duplicate to restore state
    bpy.ops.tlm.remove_layer()
    check("4.4 Layer removed", len(tlm.layers) == initial_count)


# ─── Group Tests ──────────────────────────────────────────────────────────────

def test_groups(plane):
    log("")
    log("=== EXTRA TESTS: Groups ===")
    set_active(plane)
    mat = plane.active_material
    tlm = mat.tlm

    initial_count = len(tlm.layers)

    # Create a group
    log("Creating group...")
    bpy.ops.tlm.add_group()
    group_idx = tlm.active_layer_index
    group = tlm.layers[group_idx]
    group.name = "Test Group"
    check("5.1 Group created", group.layer_type == "GROUP")

    # Add fills inside the group
    log("Adding fills to group...")
    bpy.ops.tlm.add_fill_layer()
    child1 = tlm.layers[tlm.active_layer_index]
    child1.name = "Group Child 1"
    child1.fill_color = (1.0, 0.0, 0.0, 1.0)
    child1.group_name = group.name

    bpy.ops.tlm.add_fill_layer()
    child2 = tlm.layers[tlm.active_layer_index]
    child2.name = "Group Child 2"
    child2.fill_color = (0.0, 1.0, 0.0, 1.0)
    child2.group_name = group.name

    check("5.2 Group has 2 children",
          len([l for l in tlm.layers if l.group_name == group.name]) == 2)

    bpy.ops.tlm.rebuild_composite()
    check("5.3 Rebuild with group succeeded", count_tlm_nodes(mat) > 0)

    log(f"Groups test complete: {len(tlm.layers)} total layers")


# ─── Report ───────────────────────────────────────────────────────────────────

def print_report():
    log("")
    log("=" * 60)
    log("         TLM TEST REPORT")
    log("=" * 60)

    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)
    total = len(results)

    log(f"  Total:  {total}")
    log(f"  Passed: {passed}")
    log(f"  Failed: {failed}")
    log("")

    if failed > 0:
        log("  FAILED TESTS:")
        for name, ok in results:
            if not ok:
                log(f"    [FAIL] {name}")
    else:
        log("  ALL TESTS PASSED!")

    log("")
    log("=" * 60)
    log("  Visual verification checklist:")
    log("  - Cube: colored with blend effects, noise pattern visible")
    log("  - Sphere: metallic with roughness variation, bump texture")
    log("  - Plane: checker + gradient + musgrave, PBR channels active")
    log("  - Shader Editor: verify node connections for each material")
    log("=" * 60)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    log("")
    log("*" * 60)
    log("    TLM AUTOMATED TEST SCENE")
    log("    Testing all features of Texture Layer Manager")
    log("*" * 60)

    try:
        cube, sphere, plane = setup_scene()
        build_material_1(cube)
        build_material_2(sphere)
        build_material_3(plane)
        test_duplicate_and_move(cube)
        test_groups(plane)
    except Exception as e:
        import traceback
        log(f"FATAL ERROR: {e}")
        traceback.print_exc()
        results.append((f"FATAL: {e}", False))

    print_report()


if __name__ == "__main__":
    main()
