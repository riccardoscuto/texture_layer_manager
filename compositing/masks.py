"""Mask system: smart generators, mask slot resolution, mask refinement.

The mask pipeline builds a 0..1 factor that gates a layer's contribution
to the cumulative mix. Two slots (A + B) can be combined, with optional
refinement (contrast, Levels remap, Softness, Blur).

Public entry: ``_apply_mask(node_tree, mix_node, layer, uv_map, x, y,
layer_alpha=None)`` — builds the full pipeline and wires it into the
mix node's factor socket. Returns the final Multiply Math node (caller
sets inputs[1] to opacity) or None if the mask source is invalid.

``_cleanup_tlm_mask_blur_groups()`` sweeps orphaned blur node groups
after every rebuild.

Supported mask sources (see mask_source EnumProperty in properties.py):
  IMAGE, AO, POINTINESS, WIREFRAME, EDGE_WEAR (smart), DIRT (smart),
  CURVATURE_SMART (smart), FRESNEL, NDOTL, NDOTH, VORONOI.
"""

import bpy

# Late imports — parent __init__ defines these before doing
# ``from .masks import *`` at end of its body.
from . import (
    _next_id,
    _tag,
    _new_img_tex,
    _factor_socket,
)
from . import TLM_PREFIX, TLM_GROUP_PREFIX  # noqa: F401


__all__ = [
    '_get_or_build_mask_blur_group',
    '_cleanup_tlm_mask_blur_groups',
    '_build_smart_generator',
    '_build_blurred_image_mask',
    '_build_mask_slot',
    '_apply_mask',
]


def _get_or_build_mask_blur_group(img):
    """Get or create the shared 5-tap cross blur NodeGroup for a given image.

    One group per image is created (image is baked into the group's
    ShaderNodeTexImage nodes and can't be parameterized via socket). Groups
    are reused across all layers/materials that sample the same image, and
    orphans are swept by `_cleanup_tlm_mask_blur_groups` after each rebuild.

    Group interface:
        Inputs:  UV (Vector), Blur (Float)
        Outputs: Value (Float 0..1)
    """
    group_name = f"{TLM_GROUP_PREFIX}{img.name}"
    existing = bpy.data.node_groups.get(group_name)
    if existing is not None:
        return existing

    tree = bpy.data.node_groups.new(group_name, 'ShaderNodeTree')
    tree.use_fake_user = True  # survive save/load even when temporarily unreferenced

    # Blender 4.0+ interface API (required for 5.0)
    tree.interface.new_socket(name="UV",    in_out='INPUT',  socket_type='NodeSocketVector')
    tree.interface.new_socket(name="Blur",  in_out='INPUT',  socket_type='NodeSocketFloat')
    tree.interface.new_socket(name="Value", in_out='OUTPUT', socket_type='NodeSocketFloat')

    gi = tree.nodes.new("NodeGroupInput")
    gi.location = (-1000, 0)
    go = tree.nodes.new("NodeGroupOutput")
    go.location = (900, 0)

    # -blur = blur × -1 (shared by west + south taps)
    neg = tree.nodes.new("ShaderNodeMath")
    neg.operation = 'MULTIPLY'
    neg.inputs[1].default_value = -1.0
    neg.location = (-800, -200)
    tree.links.new(gi.outputs["Blur"], neg.inputs[0])

    # 5-tap cross kernel: (x_offset_socket_or_None, y_offset_socket_or_None, weight)
    # None = zero offset; weights sum to 1.0 (center=0.40, cardinals=0.15 each).
    taps = [
        (None,                None,                0.40),  # center
        (gi.outputs["Blur"],  None,                0.15),  # east
        (neg.outputs[0],      None,                0.15),  # west
        (None,                gi.outputs["Blur"],  0.15),  # north
        (None,                neg.outputs[0],      0.15),  # south
    ]

    weighted = []
    for i, (x_sock, y_sock, w) in enumerate(taps):
        row_y = 300 - i * 180

        combine = tree.nodes.new("ShaderNodeCombineXYZ")
        combine.location = (-600, row_y)
        if x_sock is not None:
            tree.links.new(x_sock, combine.inputs["X"])
        if y_sock is not None:
            tree.links.new(y_sock, combine.inputs["Y"])

        mp = tree.nodes.new("ShaderNodeMapping")
        mp.location = (-420, row_y)
        tree.links.new(gi.outputs["UV"], mp.inputs["Vector"])
        tree.links.new(combine.outputs["Vector"], mp.inputs["Location"])

        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        if img.colorspace_settings.name != "Non-Color":
            img.colorspace_settings.name = "Non-Color"
        tex.location = (-220, row_y)
        tree.links.new(mp.outputs["Vector"], tex.inputs["Vector"])

        sep = tree.nodes.new("ShaderNodeSeparateColor")
        sep.location = (0, row_y)
        tree.links.new(tex.outputs["Color"], sep.inputs["Color"])

        ms = tree.nodes.new("ShaderNodeMath")
        ms.operation = 'MULTIPLY'
        ms.inputs[1].default_value = w
        ms.location = (180, row_y)
        tree.links.new(sep.outputs["Red"], ms.inputs[0])
        weighted.append(ms.outputs[0])

    # Sum 5 weighted taps via a small Add-tree
    a1 = tree.nodes.new("ShaderNodeMath")
    a1.operation = 'ADD'
    a1.location = (400, 180)
    tree.links.new(weighted[0], a1.inputs[0])
    tree.links.new(weighted[1], a1.inputs[1])

    a2 = tree.nodes.new("ShaderNodeMath")
    a2.operation = 'ADD'
    a2.location = (400, -180)
    tree.links.new(weighted[2], a2.inputs[0])
    tree.links.new(weighted[3], a2.inputs[1])

    a3 = tree.nodes.new("ShaderNodeMath")
    a3.operation = 'ADD'
    a3.location = (580, 0)
    tree.links.new(a1.outputs[0], a3.inputs[0])
    tree.links.new(a2.outputs[0], a3.inputs[1])

    afinal = tree.nodes.new("ShaderNodeMath")
    afinal.operation = 'ADD'
    afinal.use_clamp = True
    afinal.location = (760, 0)
    tree.links.new(a3.outputs[0], afinal.inputs[0])
    tree.links.new(weighted[4], afinal.inputs[1])

    tree.links.new(afinal.outputs[0], go.inputs["Value"])
    return tree


def _cleanup_tlm_mask_blur_groups():
    """Remove TLM mask blur NodeGroups not referenced by any material.

    Called at the end of rebuild_node_tree. Without this, every image
    rename/bake would leak an orphan NodeGroup in bpy.data.node_groups.
    """
    used = set()
    for mat in bpy.data.materials:
        nt = mat.node_tree
        if nt is None:
            continue
        for n in nt.nodes:
            if n.type == 'GROUP' and n.node_tree is not None \
                    and n.node_tree.name.startswith(TLM_GROUP_PREFIX):
                used.add(n.node_tree.name)

    for ng in list(bpy.data.node_groups):
        if ng.name.startswith(TLM_GROUP_PREFIX) and ng.name not in used:
            bpy.data.node_groups.remove(ng)


# ── Hot-update tagging infrastructure ────────────────────────────────────────


def _build_smart_generator(node_tree, layer, gen_type, ao_distance, x, y, name_tag=""):
    """Build a physics-based smart mask generator output socket (0-1).

    EDGE_WEAR: Pointiness convex edges + noise breakup + sharpness — simulates worn edges
    DIRT: Inverted AO × grunge noise — accumulates in cavities
    CURVATURE_SMART: Bipolar pointiness (|p-0.5|*2) — both convex + concave edges

    Shared layer properties: mask_gen_intensity, mask_gen_breakup,
    mask_gen_breakup_scale, mask_gen_sharpness. These are shared by slot A and B.
    """
    intensity = getattr(layer, 'mask_gen_intensity', 1.0)
    breakup   = getattr(layer, 'mask_gen_breakup', 0.3)
    bsc       = getattr(layer, 'mask_gen_breakup_scale', 15.0)
    sharpness = getattr(layer, 'mask_gen_sharpness', 0.5)

    x_base = x - 640
    y_base = y - 100

    # ── 1. Base source ────────────────────────────────────────────────────
    if gen_type == 'DIRT':
        src = node_tree.nodes.new("ShaderNodeAmbientOcclusion")
        src.samples = 16
        src.inputs["Distance"].default_value = ao_distance
        src.location = (x_base, y_base)
        src.name = f"{TLM_PREFIX}gen_ao_{name_tag}_{_next_id()}"
        _tag(src, layer.name, f"gen_ao_{name_tag}")
        # Invert AO — dirt accumulates in cavities which AO marks dark
        inv = node_tree.nodes.new("ShaderNodeMath")
        inv.operation = 'SUBTRACT'
        inv.name = f"{TLM_PREFIX}gen_ao_inv_{name_tag}_{_next_id()}"
        inv.inputs[0].default_value = 1.0
        inv.use_clamp = True
        inv.location = (x_base + 200, y_base)
        # Blender 5.0: ShaderNodeAmbientOcclusion outputs are "Color" and "AO"
        # (the old "Fac" output was removed). Use "AO" for the scalar occlusion factor.
        node_tree.links.new(src.outputs["AO"], inv.inputs[1])
        base = inv.outputs["Value"]
    else:
        geo = node_tree.nodes.new("ShaderNodeNewGeometry")
        geo.location = (x_base, y_base)
        geo.name = f"{TLM_PREFIX}gen_geo_{name_tag}_{_next_id()}"
        _tag(geo, layer.name, f"gen_geo_{name_tag}")
        p = geo.outputs["Pointiness"]
        if gen_type == 'EDGE_WEAR':
            # Convex edges only: MapRange 0.5..0.7 → 0..1 (narrow band, clamped)
            mr = node_tree.nodes.new("ShaderNodeMapRange")
            mr.clamp = True
            mr.inputs["From Min"].default_value = 0.5
            mr.inputs["From Max"].default_value = 0.7
            mr.inputs["To Min"].default_value = 0.0
            mr.inputs["To Max"].default_value = 1.0
            mr.location = (x_base + 200, y_base)
            mr.name = f"{TLM_PREFIX}gen_mr_{name_tag}_{_next_id()}"
            node_tree.links.new(p, mr.inputs["Value"])
            base = mr.outputs["Result"]
        else:  # CURVATURE_SMART: bipolar edges
            sub = node_tree.nodes.new("ShaderNodeMath")
            sub.operation = 'SUBTRACT'
            sub.name = f"{TLM_PREFIX}gen_curv_sub_{name_tag}_{_next_id()}"
            sub.location = (x_base + 200, y_base)
            node_tree.links.new(p, sub.inputs[0])
            sub.inputs[1].default_value = 0.5
            ab = node_tree.nodes.new("ShaderNodeMath")
            ab.operation = 'ABSOLUTE'
            ab.name = f"{TLM_PREFIX}gen_curv_abs_{name_tag}_{_next_id()}"
            ab.location = (x_base + 320, y_base)
            node_tree.links.new(sub.outputs[0], ab.inputs[0])
            m2 = node_tree.nodes.new("ShaderNodeMath")
            m2.operation = 'MULTIPLY'
            m2.name = f"{TLM_PREFIX}gen_curv_mul_{name_tag}_{_next_id()}"
            m2.use_clamp = True
            m2.location = (x_base + 440, y_base)
            node_tree.links.new(ab.outputs[0], m2.inputs[0])
            m2.inputs[1].default_value = 2.0
            base = m2.outputs[0]

    # ── 2. Noise breakup ──────────────────────────────────────────────────
    if breakup > 1e-4:
        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.noise_dimensions = '3D'
        noise.inputs["Scale"].default_value = bsc
        noise.inputs["Detail"].default_value = 2.0
        noise.inputs["Roughness"].default_value = 0.5
        noise.location = (x_base + 440, y_base - 160)
        noise.name = f"{TLM_PREFIX}gen_noise_{name_tag}_{_next_id()}"
        _tag(noise, layer.name, f"gen_noise_{name_tag}")

        # modulation = 1 - breakup + breakup*noise  (noise âˆˆ 0..1)
        # Use MULTIPLY_ADD:  noise * breakup + (1 - breakup)
        mod = node_tree.nodes.new("ShaderNodeMath")
        mod.operation = 'MULTIPLY_ADD'
        mod.name = f"{TLM_PREFIX}gen_breakup_madd_{name_tag}_{_next_id()}"
        mod.location = (x_base + 620, y_base - 160)
        node_tree.links.new(noise.outputs["Fac"], mod.inputs[0])
        mod.inputs[1].default_value = breakup
        mod.inputs[2].default_value = 1.0 - breakup

        mm = node_tree.nodes.new("ShaderNodeMath")
        mm.operation = 'MULTIPLY'
        mm.name = f"{TLM_PREFIX}gen_breakup_mul_{name_tag}_{_next_id()}"
        mm.use_clamp = True
        mm.location = (x_base + 740, y_base)
        node_tree.links.new(base, mm.inputs[0])
        node_tree.links.new(mod.outputs[0], mm.inputs[1])
        base = mm.outputs[0]

    # ── 3. Sharpness via POWER ────────────────────────────────────────────
    if abs(sharpness - 0.5) > 1e-4:
        if sharpness < 0.5:
            exp = 0.3 + (sharpness / 0.5) * 0.7     # 0.0 → 0.3 (very soft)
        else:
            exp = 1.0 + ((sharpness - 0.5) / 0.5) * 4.0  # 1.0 → 5.0 (very sharp)
        pwr = node_tree.nodes.new("ShaderNodeMath")
        pwr.operation = 'POWER'
        pwr.use_clamp = True
        pwr.location = (x_base + 860, y_base)
        pwr.name = f"{TLM_PREFIX}gen_sharp_{name_tag}_{_next_id()}"
        node_tree.links.new(base, pwr.inputs[0])
        pwr.inputs[1].default_value = exp
        base = pwr.outputs[0]

    # ── 4. Intensity multiplier ───────────────────────────────────────────
    if abs(intensity - 1.0) > 1e-4:
        im = node_tree.nodes.new("ShaderNodeMath")
        im.operation = 'MULTIPLY'
        im.use_clamp = True
        im.location = (x_base + 980, y_base)
        im.name = f"{TLM_PREFIX}gen_intensity_{name_tag}_{_next_id()}"
        node_tree.links.new(base, im.inputs[0])
        im.inputs[1].default_value = intensity
        base = im.outputs[0]

    return base


def _build_blurred_image_mask(node_tree, img, uv_map, blur, x, y, name_tag="",
                              layer_name=""):
    """Pseudo-blur via 5-tap cross, encapsulated in a reusable NodeGroup.

    The actual kernel (5 Image Texture samples + Mapping offsets + weighted
    sum) lives inside the shared `TLM_maskblur_<image>` NodeGroup. In the
    material's node tree we place only two nodes: a UV Map and a single
    Group instance — instead of the ~20 nodes the inline version used.

    Runtime cost is unchanged (still 5 texture samples per pixel); this is
    a pure visual/architectural cleanup.

    The Group instance is tagged with role ``mask_blur_group_<slot>`` so the
    ``_hot_mask_blur`` handler can update the Blur input without rebuilding.
    """
    uv = node_tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = uv_map
    uv.name = f"{TLM_PREFIX}mask_blur_uv_{name_tag}_{_next_id()}"
    uv.location = (x - 380, y - 100)

    group = node_tree.nodes.new("ShaderNodeGroup")
    group.node_tree = _get_or_build_mask_blur_group(img)
    group.name = f"{TLM_PREFIX}mask_blur_group_{name_tag}_{_next_id()}"
    group.label = f"Mask Blur ({img.name})"
    group.location = (x - 180, y - 100)
    group.inputs["Blur"].default_value = blur
    if layer_name:
        _tag(group, layer_name, f"mask_blur_group_{name_tag}")
    node_tree.links.new(uv.outputs["UV"], group.inputs["UV"])

    return group.outputs["Value"]


def _build_mask_slot(node_tree, layer, slot, uv_map, x, y, name_tag=""):
    """Build a single mask value socket (0-1) for slot 'a' or 'b'.

    Returns the output socket of the final node in the slot's mini-chain,
    or None if the mask source is invalid (e.g. IMAGE with no image assigned).
    The slot reads the appropriate properties:
        slot='a' → mask_source, mask_image_name, mask_invert, mask_ao_distance
        slot='b' → mask_source_b, mask_image_name_b, mask_invert_b, mask_ao_distance_b
    Supports sources: IMAGE (with optional blur), AO, POINTINESS,
    EDGE_WEAR, DIRT, CURVATURE_SMART.
    """
    if slot == 'a':
        source      = getattr(layer, 'mask_source', 'IMAGE')
        img_name    = getattr(layer, 'mask_image_name', "")
        invert      = getattr(layer, 'mask_invert', False)
        ao_distance = getattr(layer, 'mask_ao_distance', 0.5)
    else:
        source      = getattr(layer, 'mask_source_b', 'IMAGE')
        img_name    = getattr(layer, 'mask_image_name_b', "")
        invert      = getattr(layer, 'mask_invert_b', False)
        ao_distance = getattr(layer, 'mask_ao_distance_b', 0.5)

    val = None
    if source == 'IMAGE':
        img = bpy.data.images.get(img_name) if img_name else None
        if img is None:
            return None
        blur = getattr(layer, 'mask_blur', 0.0)
        if blur > 1e-4:
            val = _build_blurred_image_mask(node_tree, img, uv_map, blur, x, y, name_tag,
                                            layer_name=layer.name)
        else:
            tex = _new_img_tex(node_tree, img, uv_map, x - 440, y - 100, "Non-Color")
            sep = node_tree.nodes.new("ShaderNodeSeparateColor")
            sep.name = f"{TLM_PREFIX}mask_sep_{name_tag}_{_next_id()}"
            sep.location = (x - 280, y - 100)
            node_tree.links.new(tex.outputs["Color"], sep.inputs["Color"])
            val = sep.outputs["Red"]
    elif source == 'AO':
        ao = node_tree.nodes.new("ShaderNodeAmbientOcclusion")
        ao.name = f"{TLM_PREFIX}mask_ao_{name_tag}_{_next_id()}"
        ao.location = (x - 300, y - 100)
        ao.samples = 16
        ao.inputs["Distance"].default_value = ao_distance
        _tag(ao, layer.name, f"mask_ao_{name_tag}")
        # Blender 5.0: ShaderNodeAmbientOcclusion outputs are "Color" and "AO"
        # (the old "Fac" output was removed). Use "AO" for the scalar occlusion factor.
        val = ao.outputs["AO"]
    elif source == 'POINTINESS':
        geo = node_tree.nodes.new("ShaderNodeNewGeometry")
        geo.name = f"{TLM_PREFIX}mask_geo_{name_tag}_{_next_id()}"
        geo.location = (x - 300, y - 100)
        _tag(geo, layer.name, f"mask_geo_{name_tag}")
        val = geo.outputs["Pointiness"]
    elif source == 'WIREFRAME':
        wire = node_tree.nodes.new("ShaderNodeWireframe")
        wire.name = f"{TLM_PREFIX}mask_wireframe_{name_tag}_{_next_id()}"
        wire.location = (x - 300, y - 100)
        wire.inputs["Size"].default_value = getattr(layer, 'mask_wireframe_size', 0.01)
        if hasattr(wire, "use_pixel_size"):
            wire.use_pixel_size = getattr(layer, 'mask_wireframe_use_pixel_size', True)
        _tag(wire, layer.name, f"mask_wireframe_{name_tag}")
        val = wire.outputs["Fac"]
    elif source == 'FRESNEL':
        # View-angle gradient: 0 facing the camera, 1 at grazing silhouette.
        # Same IOR semantics as use_fresnel_mask but here Fresnel is the
        # SOURCE (drives a procedural's Fac for iridescent gradients via the
        # downstream ColorRamp) rather than a per-layer modifier multiplied
        # into the factor. This unlocks oil-slick / bubble / hologram /
        # mother-of-pearl materials that need a continuous Fresnel → colour
        # gradient instead of a single-colour rim halo.
        fr = node_tree.nodes.new("ShaderNodeFresnel")
        fr.name = f"{TLM_PREFIX}mask_fresnel_{name_tag}_{_next_id()}"
        fr.location = (x - 300, y - 100)
        fr.inputs["IOR"].default_value = getattr(layer, 'mask_fresnel_ior', 1.45)
        _tag(fr, layer.name, f"mask_fresnel_{name_tag}")
        val = fr.outputs["Fac"]
    elif source == 'NDOTL':
        # Light angle: Normal · Sun direction → [0,1]. Pair with
        # proc_contrast=1.0 ColorRamp on a downstream procedural for binary
        # anime/toon cel-shading. The sun direction is baked from the first
        # Sun light in the scene at build time (changes to the sun require
        # rebuilding the material via the rebuild button).
        # Late import — _find_first_sun_direction lives in hot_update.py
        # which loads AFTER masks.py. At call time the package is fully
        # initialised so this resolves fine.
        from . import _find_first_sun_direction as _fsd
        sun_dir = _fsd()
        # Build the geometry → normal → dot-product chain
        geo = node_tree.nodes.new("ShaderNodeNewGeometry")
        geo.name = f"{TLM_PREFIX}mask_ndotl_geo_{name_tag}_{_next_id()}"
        geo.location = (x - 480, y - 100)
        _tag(geo, layer.name, f"mask_ndotl_geo_{name_tag}")

        # Vector Math DOT_PRODUCT(normal, sun_dir)
        dot = node_tree.nodes.new("ShaderNodeVectorMath")
        dot.operation = 'DOT_PRODUCT'
        dot.name = f"{TLM_PREFIX}mask_ndotl_dot_{name_tag}_{_next_id()}"
        dot.location = (x - 320, y - 100)
        _tag(dot, layer.name, f"mask_ndotl_dot_{name_tag}")
        node_tree.links.new(geo.outputs["Normal"], dot.inputs[0])
        # inputs[1] is a Vector socket — set its default_value
        dot.inputs[1].default_value = sun_dir

        # Map [-1, 1] → [0, 1] so the value works as a standard mask
        mr = node_tree.nodes.new("ShaderNodeMapRange")
        mr.clamp = True
        mr.inputs["From Min"].default_value = -1.0
        mr.inputs["From Max"].default_value = 1.0
        mr.inputs["To Min"].default_value = 0.0
        mr.inputs["To Max"].default_value = 1.0
        mr.name = f"{TLM_PREFIX}mask_ndotl_mr_{name_tag}_{_next_id()}"
        mr.location = (x - 180, y - 100)
        # Vector Math DOT_PRODUCT outputs a scalar via the "Value" socket
        node_tree.links.new(dot.outputs["Value"], mr.inputs["Value"])
        val = mr.outputs["Result"]
    elif source == 'NDOTH':
        # Half-Vector specular: Normal · normalize(SunDir + ViewDir).
        # Peaks where the surface points exactly between the sun and the
        # camera = the classic Phong specular highlight position. With a
        # narrow ColorRamp (proc_contrast=1.0) this gives a small "anime
        # toon highlight" shape on convex peaks aligned with the light.
        # Late import — _find_first_sun_direction lives in hot_update.py
        # which loads AFTER masks.py. At call time the package is fully
        # initialised so this resolves fine.
        from . import _find_first_sun_direction as _fsd
        sun_dir = _fsd()

        geo = node_tree.nodes.new("ShaderNodeNewGeometry")
        geo.name = f"{TLM_PREFIX}mask_ndoth_geo_{name_tag}_{_next_id()}"
        geo.location = (x - 580, y - 100)
        _tag(geo, layer.name, f"mask_ndoth_geo_{name_tag}")

        # H = normalize(L + V). L is the baked sun direction (= direction
        # TO the light). V is Geometry.Incoming = direction from surface
        # TO the viewer. Both unit vectors → sum then normalize.
        add_lv = node_tree.nodes.new("ShaderNodeVectorMath")
        add_lv.operation = 'ADD'
        add_lv.name = f"{TLM_PREFIX}mask_ndoth_addlv_{name_tag}_{_next_id()}"
        add_lv.location = (x - 460, y - 100)
        node_tree.links.new(geo.outputs["Incoming"], add_lv.inputs[0])
        add_lv.inputs[1].default_value = sun_dir

        norm_h = node_tree.nodes.new("ShaderNodeVectorMath")
        norm_h.operation = 'NORMALIZE'
        norm_h.name = f"{TLM_PREFIX}mask_ndoth_normh_{name_tag}_{_next_id()}"
        norm_h.location = (x - 380, y - 100)
        node_tree.links.new(add_lv.outputs["Vector"], norm_h.inputs[0])

        # NdotH = Normal · H
        dot = node_tree.nodes.new("ShaderNodeVectorMath")
        dot.operation = 'DOT_PRODUCT'
        dot.name = f"{TLM_PREFIX}mask_ndoth_dot_{name_tag}_{_next_id()}"
        dot.location = (x - 240, y - 100)
        _tag(dot, layer.name, f"mask_ndoth_dot_{name_tag}")
        node_tree.links.new(geo.outputs["Normal"], dot.inputs[0])
        node_tree.links.new(norm_h.outputs["Vector"], dot.inputs[1])

        # Map [-1, 1] → [0, 1] (Half-Lambert style for the highlight too)
        mr = node_tree.nodes.new("ShaderNodeMapRange")
        mr.clamp = True
        mr.inputs["From Min"].default_value = -1.0
        mr.inputs["From Max"].default_value = 1.0
        mr.inputs["To Min"].default_value = 0.0
        mr.inputs["To Max"].default_value = 1.0
        mr.name = f"{TLM_PREFIX}mask_ndoth_mr_{name_tag}_{_next_id()}"
        mr.location = (x - 100, y - 100)
        node_tree.links.new(dot.outputs["Value"], mr.inputs["Value"])
        val = mr.outputs["Result"]
    elif source in ('EDGE_WEAR', 'DIRT', 'CURVATURE_SMART'):
        val = _build_smart_generator(node_tree, layer, source, ao_distance, x, y, name_tag)
    elif source == 'VORONOI':
        # Voronoi-driven procedural mask — the "Stone+Dirt mask alignment"
        # trick from cobblestone-style reference materials. Builds a
        # ShaderNodeTexVoronoi reading object-space coordinates and emits
        # either F1 or Distance-to-Edge as the fac. Pair the mask's scale
        # with a colour layer's proc_scale to lock the mask cells onto
        # the same cell layout (dirt lands EXACTLY between stones).
        if slot == 'a':
            v_feature    = getattr(layer, 'mask_voronoi_feature', 'DISTANCE_TO_EDGE')
            v_scale      = getattr(layer, 'mask_voronoi_scale', 10.0)
            v_randomness = getattr(layer, 'mask_voronoi_randomness', 1.0)
            v_edge_width = getattr(layer, 'mask_voronoi_edge_width', 1.0)
        else:
            v_feature    = getattr(layer, 'mask_voronoi_feature_b', 'DISTANCE_TO_EDGE')
            v_scale      = getattr(layer, 'mask_voronoi_scale_b', 10.0)
            v_randomness = getattr(layer, 'mask_voronoi_randomness_b', 1.0)
            v_edge_width = getattr(layer, 'mask_voronoi_edge_width_b', 1.0)

        # Object-space coords so mask follows the geometry, not the UVs
        tex_coord = node_tree.nodes.new("ShaderNodeTexCoord")
        tex_coord.name = f"{TLM_PREFIX}mask_vorocoord_{name_tag}_{_next_id()}"
        tex_coord.location = (x - 480, y - 100)
        _tag(tex_coord, layer.name, f"mask_vorocoord_{name_tag}")

        voro = node_tree.nodes.new("ShaderNodeTexVoronoi")
        voro.name = f"{TLM_PREFIX}mask_voronoi_{name_tag}_{_next_id()}"
        voro.location = (x - 300, y - 100)
        voro.feature = v_feature
        voro.distance = 'EUCLIDEAN'
        voro.inputs["Scale"].default_value = v_scale
        voro.inputs["Randomness"].default_value = v_randomness
        _tag(voro, layer.name, f"mask_voronoi_{name_tag}")
        node_tree.links.new(tex_coord.outputs["Object"], voro.inputs["Vector"])

        # Voronoi Distance output is NOT normalised to 0..1:
        # * Scale parameter divides the input coords by N — so cell width
        #   in input space is 1/N. The DTE peak (cell-centre distance to
        #   nearest edge) is roughly half a cell width = 0.5/N.
        # * For scale=10 the peak is ~0.05 — way below 1.0. Without
        #   remapping, the "mask high" plateau is only 5% and dirt
        #   barely registers anywhere; with bad remapping it becomes
        #   ~100% everywhere and dirt floods the whole surface.
        # Auto-compute From Max = 0.5/scale so the mask is properly
        # normalised at whatever cell density the user picked.
        mr = node_tree.nodes.new("ShaderNodeMapRange")
        mr.name = f"{TLM_PREFIX}mask_vorange_{name_tag}_{_next_id()}"
        mr.location = (x - 200, y - 100)
        mr.clamp = True
        mr.inputs["From Min"].default_value = 0.0
        # Auto-normalise based on scale. Safe lower bound on scale to
        # avoid div-by-zero, even though properties.py min is 0.1.
        # edge_width scales how much of the cell width is mapped to
        # 0..1. With invert=True, this controls how broad the "edge band"
        # of the mask is. edge_width=1.0 → smooth gradient edge→centre
        # (broad bands); edge_width=0.3 → mask saturates well before
        # centre, producing thin crack ink.
        mr.inputs["From Max"].default_value = (0.5 * v_edge_width) / max(v_scale, 0.1)
        mr.inputs["To Min"].default_value = 0.0
        mr.inputs["To Max"].default_value = 1.0
        _tag(mr, layer.name, f"mask_vorange_{name_tag}")
        node_tree.links.new(voro.outputs["Distance"], mr.inputs["Value"])
        val = mr.outputs["Result"]

    if val is None:
        return None

    if invert:
        inv = node_tree.nodes.new("ShaderNodeMath")
        inv.operation = 'SUBTRACT'
        inv.use_clamp = True
        inv.name = f"{TLM_PREFIX}mask_inv_{name_tag}_{_next_id()}"
        inv.location = (x - 180, y - 100)
        inv.inputs[0].default_value = 1.0
        node_tree.links.new(val, inv.inputs[1])
        val = inv.outputs["Value"]

    return val


def _apply_mask(node_tree, mix_node, layer, uv_map, x, y, layer_alpha=None):
    """Build the advanced mask pipeline and wire it into mix_node's factor.

    Pipeline: A [combine B] → contrast → multiply(opacity) → factor socket

    Returns the final Multiply Math node (caller sets inputs[1] = opacity)
    or None if the primary mask source is invalid.
    """
    a = _build_mask_slot(node_tree, layer, 'a', uv_map, x, y - 0, name_tag="a")
    if a is None:
        return None

    combined = a
    if getattr(layer, 'use_mask_b', False):
        b = _build_mask_slot(node_tree, layer, 'b', uv_map, x, y - 80, name_tag="b")
        if b is not None:
            mode = getattr(layer, 'mask_combine', 'MULTIPLY')
            if mode == 'SCREEN':
                # screen = 1 - (1-a)*(1-b) — smoother OR
                ia = node_tree.nodes.new("ShaderNodeMath")
                ia.operation = 'SUBTRACT'
                ia.name = f"{TLM_PREFIX}mask_screen_ia_{_next_id()}"
                ia.inputs[0].default_value = 1.0
                ia.location = (x - 120, y - 60)
                node_tree.links.new(a, ia.inputs[1])

                ib = node_tree.nodes.new("ShaderNodeMath")
                ib.operation = 'SUBTRACT'
                ib.name = f"{TLM_PREFIX}mask_screen_ib_{_next_id()}"
                ib.inputs[0].default_value = 1.0
                ib.location = (x - 120, y - 100)
                node_tree.links.new(b, ib.inputs[1])

                mul = node_tree.nodes.new("ShaderNodeMath")
                mul.operation = 'MULTIPLY'
                mul.name = f"{TLM_PREFIX}mask_screen_mul_{_next_id()}"
                mul.location = (x - 60, y - 80)
                node_tree.links.new(ia.outputs[0], mul.inputs[0])
                node_tree.links.new(ib.outputs[0], mul.inputs[1])

                scr = node_tree.nodes.new("ShaderNodeMath")
                scr.operation = 'SUBTRACT'
                scr.use_clamp = True
                scr.name = f"{TLM_PREFIX}mask_screen_{_next_id()}"
                scr.location = (x, y - 80)
                scr.inputs[0].default_value = 1.0
                node_tree.links.new(mul.outputs[0], scr.inputs[1])
                combined = scr.outputs[0]
            else:
                op_map = {
                    'MULTIPLY':  'MULTIPLY',
                    'MINIMUM':   'MINIMUM',
                    'MAXIMUM':   'MAXIMUM',
                    'ADD':       'ADD',
                    'SUBTRACT':  'SUBTRACT',
                    'DIFFERENCE':'ABSOLUTE',  # Built via Subtract → Absolute below
                }
                if mode == 'DIFFERENCE':
                    sub = node_tree.nodes.new("ShaderNodeMath")
                    sub.operation = 'SUBTRACT'
                    sub.name = f"{TLM_PREFIX}mask_diff_sub_{_next_id()}"
                    sub.location = (x - 80, y - 80)
                    node_tree.links.new(a, sub.inputs[0])
                    node_tree.links.new(b, sub.inputs[1])
                    ab = node_tree.nodes.new("ShaderNodeMath")
                    ab.operation = 'ABSOLUTE'
                    ab.use_clamp = True
                    ab.name = f"{TLM_PREFIX}mask_comb_{_next_id()}"
                    ab.location = (x - 20, y - 80)
                    node_tree.links.new(sub.outputs[0], ab.inputs[0])
                    combined = ab.outputs[0]
                else:
                    op = node_tree.nodes.new("ShaderNodeMath")
                    op.operation = op_map.get(mode, 'MULTIPLY')
                    op.use_clamp = True
                    op.name = f"{TLM_PREFIX}mask_comb_{_next_id()}"
                    op.location = (x - 80, y - 80)
                    node_tree.links.new(a, op.inputs[0])
                    node_tree.links.new(b, op.inputs[1])
                    combined = op.outputs[0]

    # Contrast via POWER: 0.5 = no change; <0.5 softer (exp<1); >0.5 harder (exp>1)
    contrast = getattr(layer, 'mask_contrast', 0.5)
    if abs(contrast - 0.5) > 1e-4:
        if contrast < 0.5:
            exp = 0.25 + (contrast / 0.5) * 0.75     # 0 → 0.25, 0.5 → 1.0
        else:
            exp = 1.0 + ((contrast - 0.5) / 0.5) * 3.0  # 0.5 → 1.0, 1.0 → 4.0
        pwr = node_tree.nodes.new("ShaderNodeMath")
        pwr.operation = 'POWER'
        pwr.use_clamp = True
        pwr.name = f"{TLM_PREFIX}mask_contrast_{_next_id()}"
        pwr.location = (x + 60, y - 80)
        node_tree.links.new(combined, pwr.inputs[0])
        pwr.inputs[1].default_value = exp
        _tag(pwr, layer.name, "mask_contrast")
        combined = pwr.outputs[0]

    # ── Levels: in range → gamma → out range (industry-standard tonal remap) ─
    if getattr(layer, 'use_mask_levels', False):
        in_min  = getattr(layer, 'mask_levels_in_min', 0.0)
        in_max  = getattr(layer, 'mask_levels_in_max', 1.0)
        gamma   = getattr(layer, 'mask_levels_gamma', 1.0)
        out_min = getattr(layer, 'mask_levels_out_min', 0.0)
        out_max = getattr(layer, 'mask_levels_out_max', 1.0)

        if in_max > in_min + 1e-4 and (in_min > 1e-4 or in_max < 1 - 1e-4):
            lv_in = node_tree.nodes.new("ShaderNodeMapRange")
            lv_in.clamp = True
            lv_in.inputs["From Min"].default_value = in_min
            lv_in.inputs["From Max"].default_value = in_max
            lv_in.inputs["To Min"].default_value = 0.0
            lv_in.inputs["To Max"].default_value = 1.0
            lv_in.location = (x + 180, y - 80)
            lv_in.name = f"{TLM_PREFIX}mask_lv_in_{_next_id()}"
            _tag(lv_in, layer.name, "mask_lv_in")
            node_tree.links.new(combined, lv_in.inputs["Value"])
            combined = lv_in.outputs["Result"]

        if abs(gamma - 1.0) > 1e-4:
            lv_g = node_tree.nodes.new("ShaderNodeMath")
            lv_g.operation = 'POWER'
            lv_g.use_clamp = True
            lv_g.location = (x + 260, y - 80)
            lv_g.name = f"{TLM_PREFIX}mask_lv_gamma_{_next_id()}"
            _tag(lv_g, layer.name, "mask_lv_gamma")
            node_tree.links.new(combined, lv_g.inputs[0])
            # Convention: gamma<1 brightens midtones, so exp = 1/gamma
            lv_g.inputs[1].default_value = 1.0 / gamma
            combined = lv_g.outputs[0]

        if out_min > 1e-4 or out_max < 1 - 1e-4:
            lv_out = node_tree.nodes.new("ShaderNodeMapRange")
            lv_out.clamp = True
            lv_out.inputs["From Min"].default_value = 0.0
            lv_out.inputs["From Max"].default_value = 1.0
            lv_out.inputs["To Min"].default_value = out_min
            lv_out.inputs["To Max"].default_value = out_max
            lv_out.location = (x + 340, y - 80)
            lv_out.name = f"{TLM_PREFIX}mask_lv_out_{_next_id()}"
            _tag(lv_out, layer.name, "mask_lv_out")
            node_tree.links.new(combined, lv_out.inputs["Value"])
            combined = lv_out.outputs["Result"]

    # ── Softness: smoothstep S-curve around 0.5 — widens transition zone ──
    softness = getattr(layer, 'mask_softness', 0.0)
    if softness > 1e-4:
        half = softness * 0.5
        sm = node_tree.nodes.new("ShaderNodeMapRange")
        sm.clamp = True
        sm.interpolation_type = 'SMOOTHSTEP'
        sm.inputs["From Min"].default_value = max(0.0, 0.5 - half)
        sm.inputs["From Max"].default_value = min(1.0, 0.5 + half)
        sm.inputs["To Min"].default_value = 0.0
        sm.inputs["To Max"].default_value = 1.0
        sm.location = (x + 420, y - 80)
        sm.name = f"{TLM_PREFIX}mask_soft_{_next_id()}"
        _tag(sm, layer.name, "mask_soft")
        node_tree.links.new(combined, sm.inputs["Value"])
        combined = sm.outputs["Result"]

    # Final multiplier — caller sets inputs[1] to opacity
    mult = node_tree.nodes.new("ShaderNodeMath")
    mult.operation = 'MULTIPLY'
    mult.name = f"{TLM_PREFIX}mask_mult_{_next_id()}"
    mult.location = (x + 520, y - 80)
    node_tree.links.new(combined, mult.inputs[0])

    # When the layer also has a paint alpha (PAINT image alpha or PROC fac
    # for emission), combine it INTO the factor: factor = mask × opacity ×
    # alpha. Previously the mask path overwrote the alpha contribution, so
    # a transparent paint stroke under a mask still painted as if opaque.
    if layer_alpha is not None:
        am = node_tree.nodes.new("ShaderNodeMath")
        am.operation = 'MULTIPLY'
        am.use_clamp = True
        am.name = f"{TLM_PREFIX}mask_alpha_mult_{_next_id()}"
        am.location = (x + 640, y - 80)
        node_tree.links.new(mult.outputs["Value"], am.inputs[0])
        node_tree.links.new(layer_alpha, am.inputs[1])
        node_tree.links.new(am.outputs["Value"], _factor_socket(mix_node))
    else:
        node_tree.links.new(mult.outputs["Value"], _factor_socket(mix_node))
    return mult


