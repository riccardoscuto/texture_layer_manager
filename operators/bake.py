"""PBR bake and export operator."""

import os
import bpy
import numpy as np
from bpy.types import Operator
from ._common import _get_material, compositing, _BakeGuard, _bake_preflight


# ─── Bake PBR Export ──────────────────────────────────────────────────────────

class TLM_OT_BakePBR(Operator):
    """Bake all active PBR channels to image files ready for Unity/Unreal/GLTF."""
    bl_idname = "tlm.bake_pbr"
    bl_label = "Bake & Export PBR"
    bl_options = {'REGISTER', 'UNDO'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    # User-customisable prefix for every output file. Defaults to the
    # material name (set in invoke). Suffixes like _BaseColor / _Normal /
    # _ORM are still appended automatically so the channel is recognisable.
    filename_prefix: bpy.props.StringProperty(
        name="Filename Prefix",
        description=(
            "Prefix for every exported file — e.g. 'rusty_metal' produces "
            "rusty_metal_BaseColor.png, rusty_metal_Normal.png, etc. "
            "Defaults to the material name"
        ),
        default="",
    )

    preset: bpy.props.EnumProperty(
        name="Preset",
        items=[
            ('UNREAL',  "Unreal Engine", "Albedo, Normal, ORM (R=Occlusion G=Roughness B=Metallic)"),
            ('UNITY',   "Unity HDRP",    "Albedo, Normal, Mask (R=Metallic A=Smoothness)"),
            ('GLTF',    "glTF",          "BaseColor, Normal, MetallicRoughness"),
            ('CUSTOM',  "Custom",        "Bake each channel separately"),
        ],
        default='UNREAL',
    )

    resolution: bpy.props.EnumProperty(
        name="Resolution",
        items=[
            ("512",  "512",  ""),
            ("1024", "1024", ""),
            ("2048", "2048", ""),
            ("4096", "4096", ""),
        ],
        default="1024",
    )

    file_format: bpy.props.EnumProperty(
        name="Format",
        items=[
            ('PNG',  "PNG",  ""),
            ('JPEG', "JPEG", ""),
            ('TIFF', "TIFF", ""),
            ('OPEN_EXR', "EXR", ""),
        ],
        default='PNG',
    )

    # Per-channel selection (only used when preset='CUSTOM').
    # All defaults True to preserve backward-compatible behavior.
    bake_base_color:   bpy.props.BoolProperty(name="Base Color",   default=True)
    bake_roughness:    bpy.props.BoolProperty(name="Roughness",    default=True)
    bake_metallic:     bpy.props.BoolProperty(name="Metallic",     default=True)
    bake_normal:       bpy.props.BoolProperty(name="Normal",       default=True)
    bake_emission:     bpy.props.BoolProperty(name="Emission",     default=False)
    bake_transmission: bpy.props.BoolProperty(name="Transmission", default=False)
    bake_alpha:        bpy.props.BoolProperty(name="Alpha",        default=False)

    # When both Base Color and Alpha are baked, embed the alpha INTO the
    # base-color PNG (R/G/B = base color, A = baked alpha). Produces a real
    # transparent PNG usable for cutout / decal / foliage workflows. Without
    # this, BaseColor.png stays opaque and Alpha.png is a separate greyscale
    # file the user has to recombine in another tool.
    pack_alpha_into_base_color: bpy.props.BoolProperty(
        name="Pack Alpha into Base Color",
        description=(
            "When both Base Color and Alpha are selected, output a single "
            "RGBA PNG (RGB = base color, A = alpha) instead of two "
            "separate files. Required for true PNG transparency"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def invoke(self, context, event):
        # Pre-fill the prefix with the material name so the user only
        # needs to override it when they want a custom name.
        mat = _get_material(context)
        if mat and not self.filename_prefix:
            self.filename_prefix = mat.name
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        """Sidebar shown by the file browser dialog. Surfaces the per-channel
        checkboxes when preset='CUSTOM' so the user can pick exactly which
        maps to export."""
        layout = self.layout
        layout.prop(self, "filename_prefix")
        layout.prop(self, "preset")
        layout.prop(self, "resolution")
        layout.prop(self, "file_format")
        if self.preset == 'CUSTOM':
            layout.separator()
            box = layout.box()
            box.label(text="PBR Channels:", icon='NODE_COMPOSITING')
            box.prop(self, "bake_base_color")
            box.prop(self, "bake_roughness")
            box.prop(self, "bake_metallic")
            box.prop(self, "bake_normal")
            box.prop(self, "bake_emission")
            box.prop(self, "bake_transmission")
            box.prop(self, "bake_alpha")
            # Surface the pack option only when it's actually meaningful —
            # both Base Color and Alpha must be selected.
            if self.bake_base_color and self.bake_alpha:
                box.separator(factor=0.4)
                box.prop(self, "pack_alpha_into_base_color")

    def execute(self, context):
        mat = _get_material(context)
        if not mat:
            return {'CANCELLED'}

        # Pre-flight: fail fast with a useful message rather than leaving the user
        # to read a silent bake error in the console.
        ok, err = _bake_preflight(context, mat)
        if not ok:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        res = int(self.resolution)
        ext = {'PNG': 'png', 'JPEG': 'jpg', 'TIFF': 'tif', 'OPEN_EXR': 'exr'}[self.file_format]
        # Strip the prefix of any path separators / dodgy filename chars
        # the user might have pasted in. Empty / blank → fall back to mat.name.
        prefix = (self.filename_prefix or "").strip()
        if not prefix:
            prefix = mat.name
        # Sanitise: only keep filename-safe characters
        import re
        prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', prefix)
        base = prefix
        out_dir = bpy.path.abspath(self.directory)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            self.report({'ERROR'}, f"Cannot create output dir '{out_dir}': {e}")
            return {'CANCELLED'}

        # Rebuild node tree to ensure it's current
        compositing.rebuild_node_tree(mat)
        node_tree = mat.node_tree

        baked = []

        # _BakeGuard handles render-engine forcing (CYCLES), node-selection
        # save/restore, and orphan-image cleanup on failure.
        with _BakeGuard(context, node_tree, context.active_object) as guard:
            def _bake_channel(suffix, bsdf_input, colorspace="sRGB"):
                """Bake a single PBR channel via temporary Emission shader (no lighting)."""
                bsdf = next((n for n in node_tree.nodes
                             if n.type == 'BSDF_PRINCIPLED'
                             and not n.name.startswith('TLM_')), None)
                if not bsdf:
                    return None

                socket = bsdf.inputs.get(bsdf_input)
                if not socket or not socket.links:
                    return None

                # Get the node/socket feeding the BSDF input
                source_link = socket.links[0]
                source_socket = source_link.from_socket

                # Find Material Output
                mat_output = next((n for n in node_tree.nodes
                                   if n.type == 'OUTPUT_MATERIAL'), None)
                if not mat_output:
                    return None

                # Save original connection to Material Output Surface
                orig_surface_links = []
                surface_input = mat_output.inputs.get("Surface")
                if surface_input and surface_input.links:
                    for lnk in surface_input.links:
                        orig_surface_links.append(lnk.from_socket)

                # Normal channel needs special handling — see below. For
                # every other channel we go through the EMIT path: route
                # the source socket through a temp Emission shader so
                # the bake captures the raw value without lighting.
                is_normal = (bsdf_input == "Normal")

                # The Normal channel can be fed by three different shapes
                # of upstream graph:
                #   a) NORMAL_MAP node      → bake its Color input via
                #      EMIT to preserve the exact tangent-space RGB
                #      authored upstream.
                #   b) BUMP node            → vector output; EMIT-baking
                #      a vector encodes the world-space normal as raw
                #      RGB which is wrong for an engine.
                #   c) Mix(VECTOR) chain    → same as (b).
                # Case (a) is the legacy path we kept. Cases (b) / (c)
                # now switch to bake(type='NORMAL') which samples the
                # surface shading normal and writes tangent-space RGB
                # correctly without touching the shader graph.
                use_normal_bake = False
                emit_node = None
                if is_normal:
                    normal_node = source_socket.node
                    if normal_node.type == 'NORMAL_MAP':
                        color_input = normal_node.inputs.get("Color")
                        if color_input and color_input.links:
                            source_socket = color_input.links[0].from_socket
                            emit_node = node_tree.nodes.new("ShaderNodeEmission")
                            emit_node.name = "TLM_bake_emit"
                            emit_node.location = (400, 200)
                            node_tree.links.new(source_socket, emit_node.inputs["Color"])
                            node_tree.links.new(emit_node.outputs["Emission"], surface_input)
                        else:
                            # NORMAL_MAP node with no Color input → nothing
                            # to bake. Fall through to NORMAL bake mode
                            # so we at least produce a flat-blue normal.
                            use_normal_bake = True
                    else:
                        # BUMP / vector Mix / anything else: use Blender's
                        # native NORMAL bake. Leave the shader graph alone.
                        use_normal_bake = True
                else:
                    # Standard EMIT path for color / scalar channels.
                    emit_node = node_tree.nodes.new("ShaderNodeEmission")
                    emit_node.name = "TLM_bake_emit"
                    emit_node.location = (400, 200)
                    node_tree.links.new(source_socket, emit_node.inputs["Color"])
                    node_tree.links.new(emit_node.outputs["Emission"], surface_input)

                # Create bake target image — register as orphan in case bake fails.
                img_name = f"{base}_{suffix}"
                if img_name in bpy.data.images:
                    bpy.data.images.remove(bpy.data.images[img_name])
                img = bpy.data.images.new(img_name, width=res, height=res, alpha=True)
                guard.register_orphan(img)
                if colorspace == "Non-Color":
                    try:
                        img.colorspace_settings.name = "Non-Color"
                    except Exception:
                        pass

                bake_node = node_tree.nodes.new("ShaderNodeTexImage")
                bake_node.name = "TLM_bake_tmp"
                bake_node.image = img
                bake_node.location = (600, 0)
                for n in node_tree.nodes:
                    n.select = False
                bake_node.select = True
                node_tree.nodes.active = bake_node

                bake_ok = False
                try:
                    if use_normal_bake:
                        # NORMAL bake samples the shading normal directly
                        # — works regardless of whether the upstream is a
                        # BUMP, a Mix(VECTOR), or a raw geometry normal.
                        # Tangent space is Blender's default; the saved
                        # PNG ends up engine-compatible.
                        bpy.ops.object.bake(type='NORMAL',
                                            save_mode='INTERNAL',
                                            normal_space='TANGENT')
                    else:
                        bpy.ops.object.bake(type='EMIT', save_mode='INTERNAL')
                    filepath = os.path.join(out_dir, f"{img_name}.{ext}")
                    img.filepath_raw = filepath
                    img.file_format = self.file_format
                    img.save()
                    baked.append(f"{suffix} → {img_name}.{ext}")
                    bake_ok = True
                    guard.commit()  # keep img — subsequent bakes re-register from scratch
                except Exception as e:
                    self.report({'WARNING'}, f"Bake failed for {suffix}: {e}")
                finally:
                    # Restore original connections (always)
                    if bake_node.name in node_tree.nodes:
                        node_tree.nodes.remove(bake_node)
                    if emit_node is not None and emit_node.name in node_tree.nodes:
                        node_tree.nodes.remove(emit_node)
                    # Re-link original shader to Material Output (only if
                    # we actually rerouted it — NORMAL bake leaves it alone).
                    if surface_input and emit_node is not None:
                        for orig_sock in orig_surface_links:
                            try:
                                node_tree.links.new(orig_sock, surface_input)
                            except Exception:
                                pass

                return img if bake_ok else None

            def _pack_orm(roughness_img, metallic_img):
                """Pack into ORM: R=AO(white), G=Roughness, B=Metallic."""
                img_name = f"{base}_ORM"
                if img_name in bpy.data.images:
                    bpy.data.images.remove(bpy.data.images[img_name])
                orm = bpy.data.images.new(img_name, width=res, height=res, alpha=False)
                try:
                    orm.colorspace_settings.name = "Non-Color"
                except Exception:
                    pass

                px = np.ones(res * res * 4, dtype=np.float32)  # all white (AO=1)

                if roughness_img:
                    r_px = np.zeros(res * res * 4, dtype=np.float32)
                    roughness_img.pixels.foreach_get(r_px)
                    px[1::4] = r_px[0::4]  # G = Roughness red channel
                else:
                    px[1::4] = 0.5  # default roughness

                if metallic_img:
                    m_px = np.zeros(res * res * 4, dtype=np.float32)
                    metallic_img.pixels.foreach_get(m_px)
                    px[2::4] = m_px[0::4]  # B = Metallic red channel
                else:
                    px[2::4] = 0.0  # default metallic

                px[3::4] = 1.0  # Alpha = 1
                orm.pixels.foreach_set(px)
                orm.update()

                filepath = os.path.join(out_dir, f"{img_name}.{ext}")
                orm.filepath_raw = filepath
                orm.file_format = self.file_format
                orm.save()
                baked.append(f"ORM → {img_name}.{ext}")

                # Clean up temp separate images
                if roughness_img and roughness_img.name != img_name:
                    bpy.data.images.remove(roughness_img)
                if metallic_img and metallic_img.name != img_name:
                    bpy.data.images.remove(metallic_img)
                return orm

            def _pack_unity_mask(metallic_img, roughness_img):
                """Pack Unity Mask: R=Metallic, G=0, B=0, A=Smoothness (1-Roughness)."""
                img_name = f"{base}_Mask"
                if img_name in bpy.data.images:
                    bpy.data.images.remove(bpy.data.images[img_name])
                mask = bpy.data.images.new(img_name, width=res, height=res, alpha=True)
                try:
                    mask.colorspace_settings.name = "Non-Color"
                except Exception:
                    pass

                px = np.zeros(res * res * 4, dtype=np.float32)

                if metallic_img:
                    m_px = np.zeros(res * res * 4, dtype=np.float32)
                    metallic_img.pixels.foreach_get(m_px)
                    px[0::4] = m_px[0::4]  # R = Metallic
                # G, B = 0

                if roughness_img:
                    r_px = np.zeros(res * res * 4, dtype=np.float32)
                    roughness_img.pixels.foreach_get(r_px)
                    px[3::4] = 1.0 - r_px[0::4]  # A = Smoothness (1 - Roughness)
                else:
                    px[3::4] = 0.5  # default smoothness

                mask.pixels.foreach_set(px)
                mask.update()

                filepath = os.path.join(out_dir, f"{img_name}.{ext}")
                mask.filepath_raw = filepath
                mask.file_format = self.file_format
                mask.save()
                baked.append(f"Mask → {img_name}.{ext}")

                if roughness_img:
                    bpy.data.images.remove(roughness_img)
                if metallic_img:
                    bpy.data.images.remove(metallic_img)
                return mask

            def _pack_gltf_mr(metallic_img, roughness_img):
                """Pack glTF MetallicRoughness: R=0, G=Roughness, B=Metallic, A=1."""
                img_name = f"{base}_MetallicRoughness"
                if img_name in bpy.data.images:
                    bpy.data.images.remove(bpy.data.images[img_name])
                mr = bpy.data.images.new(img_name, width=res, height=res, alpha=False)
                try:
                    mr.colorspace_settings.name = "Non-Color"
                except Exception:
                    pass

                px = np.zeros(res * res * 4, dtype=np.float32)

                if roughness_img:
                    r_px = np.zeros(res * res * 4, dtype=np.float32)
                    roughness_img.pixels.foreach_get(r_px)
                    px[1::4] = r_px[0::4]  # G = Roughness
                else:
                    px[1::4] = 0.5

                if metallic_img:
                    m_px = np.zeros(res * res * 4, dtype=np.float32)
                    metallic_img.pixels.foreach_get(m_px)
                    px[2::4] = m_px[0::4]  # B = Metallic
                # R = 0 (unused in glTF spec)

                px[3::4] = 1.0
                mr.pixels.foreach_set(px)
                mr.update()

                filepath = os.path.join(out_dir, f"{img_name}.{ext}")
                mr.filepath_raw = filepath
                mr.file_format = self.file_format
                mr.save()
                baked.append(f"MetallicRoughness → {img_name}.{ext}")

                if roughness_img:
                    bpy.data.images.remove(roughness_img)
                if metallic_img:
                    bpy.data.images.remove(metallic_img)
                return mr

            def _pack_base_alpha(base_img, alpha_img):
                """Combine RGB(base) + R(alpha) into a single RGBA PNG.

                The result is what users mean by 'PNG with alpha' — open it in
                any tool and the alpha channel actually masks the colour. The
                two temp images are removed afterwards (only the combined
                output is saved to disk).
                """
                if base_img is None:
                    return None
                img_name = f"{base}_BaseColor"
                if img_name in bpy.data.images:
                    bpy.data.images.remove(bpy.data.images[img_name])
                out = bpy.data.images.new(img_name, width=res, height=res, alpha=True)
                # sRGB — base colour space, alpha is straight (PNG default)
                try:
                    out.colorspace_settings.name = "sRGB"
                except Exception:
                    pass

                base_px = np.zeros(res * res * 4, dtype=np.float32)
                base_img.pixels.foreach_get(base_px)
                # Copy RGB straight from the base
                px = np.empty(res * res * 4, dtype=np.float32)
                px[0::4] = base_px[0::4]  # R
                px[1::4] = base_px[1::4]  # G
                px[2::4] = base_px[2::4]  # B
                # A = R channel of the alpha bake (alpha bake is greyscale,
                # value replicated across R/G/B; we read R)
                if alpha_img is not None:
                    a_px = np.zeros(res * res * 4, dtype=np.float32)
                    alpha_img.pixels.foreach_get(a_px)
                    px[3::4] = a_px[0::4]
                else:
                    px[3::4] = 1.0  # no alpha bake → fully opaque

                out.pixels.foreach_set(px)
                out.update()

                filepath = os.path.join(out_dir, f"{img_name}.{ext}")
                out.filepath_raw = filepath
                out.file_format = self.file_format
                out.save()
                baked.append(f"BaseColor (RGBA) → {img_name}.{ext}")

                # Cleanup temp inputs — only the packed output stays.
                if base_img and base_img.name != img_name:
                    bpy.data.images.remove(base_img)
                if alpha_img is not None:
                    bpy.data.images.remove(alpha_img)
                return out

            if self.preset == 'UNREAL':
                _bake_channel("Albedo",     "Base Color",  "sRGB")
                _bake_channel("Normal",     "Normal",      "Non-Color")
                rough_img = _bake_channel("_tmp_Roughness", "Roughness", "Non-Color")
                metal_img = _bake_channel("_tmp_Metallic",  "Metallic",  "Non-Color")
                _pack_orm(rough_img, metal_img)

            elif self.preset == 'UNITY':
                _bake_channel("Albedo",     "Base Color",  "sRGB")
                _bake_channel("Normal",     "Normal",      "Non-Color")
                metal_img = _bake_channel("_tmp_Metallic",  "Metallic",  "Non-Color")
                rough_img = _bake_channel("_tmp_Roughness", "Roughness", "Non-Color")
                _pack_unity_mask(metal_img, rough_img)

            elif self.preset == 'GLTF':
                _bake_channel("BaseColor",  "Base Color",  "sRGB")
                _bake_channel("Normal",     "Normal",      "Non-Color")
                metal_img = _bake_channel("_tmp_Metallic",  "Metallic",  "Non-Color")
                rough_img = _bake_channel("_tmp_Roughness", "Roughness", "Non-Color")
                _pack_gltf_mr(metal_img, rough_img)

            elif self.preset == 'CUSTOM':
                # Each channel honors its own checkbox. Use these to export
                # specific PBR maps (e.g. Base Color + Alpha only for cutout
                # decals; Roughness + Metallic only for material refinement).
                pack_ba = (self.pack_alpha_into_base_color
                           and self.bake_base_color
                           and self.bake_alpha)

                if pack_ba:
                    # Bake to temp images so we can pack RGB(base)+A(alpha)
                    # into a single transparent PNG. Temp images are
                    # removed inside _pack_base_alpha after the save.
                    base_img  = _bake_channel("_tmp_BaseColor", "Base Color", "sRGB")
                    alpha_img = _bake_channel("_tmp_Alpha",     "Alpha",      "Non-Color")
                    _pack_base_alpha(base_img, alpha_img)
                else:
                    if self.bake_base_color:
                        _bake_channel("BaseColor", "Base Color", "sRGB")
                    if self.bake_alpha:
                        _bake_channel("Alpha",     "Alpha",      "Non-Color")

                if self.bake_roughness:
                    _bake_channel("Roughness",    "Roughness",      "Non-Color")
                if self.bake_metallic:
                    _bake_channel("Metallic",     "Metallic",       "Non-Color")
                if self.bake_normal:
                    _bake_channel("Normal",       "Normal",         "Non-Color")
                if self.bake_emission:
                    _bake_channel("Emission",     "Emission Color", "sRGB")
                if self.bake_transmission:
                    _bake_channel("Transmission", "Transmission Weight", "Non-Color")

        # End of _BakeGuard context — engine and selection restored here.

        if baked:
            self.report({'INFO'}, f"Baked {len(baked)} maps to {out_dir}")
        else:
            self.report({'WARNING'}, "Nothing to bake — no PBR channels connected")

        return {'FINISHED'}


classes = [
    TLM_OT_BakePBR,
]
