"""PBR bake and export operator."""

import os
import bpy
import numpy as np
from bpy.types import Operator
from ._common import _get_material, compositing


# ─── Bake PBR Export ──────────────────────────────────────────────────────────

class TLM_OT_BakePBR(Operator):
    """Bake all active PBR channels to image files ready for Unity/Unreal/GLTF."""
    bl_idname = "tlm.bake_pbr"
    bl_label = "Bake & Export PBR"
    bl_options = {'REGISTER'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH')

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

    @classmethod
    def poll(cls, context):
        return _get_material(context) is not None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        mat = _get_material(context)
        if not mat:
            return {'CANCELLED'}

        res = int(self.resolution)
        ext = {'PNG': 'png', 'JPEG': 'jpg', 'TIFF': 'tif', 'OPEN_EXR': 'exr'}[self.file_format]
        base = mat.name
        out_dir = bpy.path.abspath(self.directory)
        os.makedirs(out_dir, exist_ok=True)

        # Rebuild node tree to ensure it's current
        compositing.rebuild_node_tree(mat)
        node_tree = mat.node_tree

        baked = []

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

            # Create temp Emission shader
            emit_node = node_tree.nodes.new("ShaderNodeEmission")
            emit_node.name = "TLM_bake_emit"
            emit_node.location = (400, 200)

            # For scalar channels (Roughness, Metallic), we need to convert
            # the value to color. Check if source is a color or value.
            # If the BSDF input is a scalar type, route through a converter.
            is_normal = (bsdf_input == "Normal")

            if is_normal:
                # Normal maps: bake the color data from the Normal Map node's input
                # Find the Normal Map node
                normal_node = source_socket.node
                if normal_node.type == 'NORMAL_MAP':
                    color_input = normal_node.inputs.get("Color")
                    if color_input and color_input.links:
                        source_socket = color_input.links[0].from_socket
                    else:
                        # No color input to normal map, skip
                        node_tree.nodes.remove(emit_node)
                        return None

                node_tree.links.new(source_socket, emit_node.inputs["Color"])
            else:
                node_tree.links.new(source_socket, emit_node.inputs["Color"])

            # Connect Emission → Material Output
            node_tree.links.new(emit_node.outputs["Emission"], surface_input)

            # Create bake target image
            img_name = f"{base}_{suffix}"
            if img_name in bpy.data.images:
                bpy.data.images.remove(bpy.data.images[img_name])
            img = bpy.data.images.new(img_name, width=res, height=res, alpha=True)
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

            try:
                bpy.ops.object.bake(type='EMIT', save_mode='INTERNAL')
                filepath = os.path.join(out_dir, f"{img_name}.{ext}")
                img.filepath_raw = filepath
                img.file_format = self.file_format
                img.save()
                baked.append(f"{suffix} → {img_name}.{ext}")
            except Exception as e:
                self.report({'WARNING'}, f"Bake failed for {suffix}: {e}")
            finally:
                # Restore original connections
                node_tree.nodes.remove(bake_node)
                node_tree.nodes.remove(emit_node)
                # Re-link original shader to Material Output
                for orig_sock in orig_surface_links:
                    node_tree.links.new(orig_sock, surface_input)

            return img

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
            _bake_channel("BaseColor", "Base Color",  "sRGB")
            _bake_channel("Roughness", "Roughness",   "Non-Color")
            _bake_channel("Metallic",  "Metallic",    "Non-Color")
            _bake_channel("Normal",    "Normal",      "Non-Color")
            _bake_channel("Emission",  "Emission Color", "sRGB")

        if baked:
            self.report({'INFO'}, f"Baked {len(baked)} maps to {out_dir}")
        else:
            self.report({'WARNING'}, "Nothing to bake — no PBR channels connected")

        return {'FINISHED'}


classes = [
    TLM_OT_BakePBR,
]
