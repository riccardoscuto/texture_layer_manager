"""Mask operators (standard and smart masks)."""

import bpy
from bpy.types import Operator
from ._common import _get_material, compositing, _BakeGuard, _bake_preflight


class TLM_OT_AddLayerMask(Operator):
    """Add a white (fully visible) mask to the active layer."""
    bl_idname = "tlm.add_layer_mask"
    bl_label = "Add Mask"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and mat.tlm.active_layer is not None

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        layer = tlm.active_layer

        if layer.use_mask and layer.mask_image:
            self.report({'WARNING'}, "Layer already has a mask.")
            return {'CANCELLED'}

        res = int(tlm.resolution)
        mask_img = bpy.data.images.new(
            name=f"{layer.name}_mask",
            width=res, height=res,
            alpha=False,
        )
        import numpy as np
        px = np.ones(res * res * 4, dtype=np.float32)
        mask_img.pixels.foreach_set(px)
        mask_img.use_fake_user = True

        layer.mask_image_name = mask_img.name
        layer.use_mask = True

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        self.report({'INFO'}, f"Mask added to '{layer.name}'")
        return {'FINISHED'}


class TLM_OT_AddSmartMask(Operator):
    """Bake a geometry-based mask (AO / Curvature / Facing / Height) to a static image.

    This is the BAKE workflow: the procedural nodes are used once to bake a
    texture, then discarded. The resulting image feeds the mask pipeline via
    mask_source='IMAGE'. For real-time alternatives that update with geometry
    changes, use the "Mask A Source" dropdown and pick one of the "(Live)"
    entries (Edge Wear / Dirt / Curvature).
    """
    bl_idname = "tlm.add_smart_mask"
    bl_label = "Bake Smart Mask"
    bl_options = {'REGISTER', 'UNDO'}

    mask_type: bpy.props.EnumProperty(
        name="Type",
        items=[
            ('AO',        "Ambient Occlusion", "Darken crevices and cavities"),
            ('CURVATURE', "Curvature",          "Highlight edges and ridges"),
            ('FACING',    "Facing Angle",       "Based on angle to camera/world up"),
            ('HEIGHT',    "Height",             "Based on world Z position"),
        ],
        default='AO',
    )

    ao_distance: bpy.props.FloatProperty(name="Distance", default=1.0, min=0.001, max=100.0)
    ao_samples:  bpy.props.IntProperty(name="Samples",   default=16,  min=1,     max=128)
    curv_ridge:  bpy.props.FloatProperty(name="Ridge",   default=1.0, min=0.0, max=2.0)
    curv_valley: bpy.props.FloatProperty(name="Valley",  default=1.0, min=0.0, max=2.0)

    @classmethod
    def poll(cls, context):
        mat = _get_material(context)
        return mat is not None and mat.tlm.active_layer is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "mask_type")
        layout.separator()
        if self.mask_type == 'AO':
            layout.prop(self, "ao_distance")
            layout.prop(self, "ao_samples")
        elif self.mask_type == 'CURVATURE':
            layout.prop(self, "curv_ridge")
            layout.prop(self, "curv_valley")

    def execute(self, context):
        mat = _get_material(context)
        tlm = mat.tlm
        layer = tlm.active_layer
        if not layer:
            return {'CANCELLED'}

        # Pre-flight: UVs / mesh / active object. Smart masks use Cycles bake
        # ('AO' / 'DIFFUSE') which requires all of these — fail fast with a
        # useful message rather than a silent console error.
        ok, err = _bake_preflight(context)
        if not ok:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        node_tree = mat.node_tree
        if not node_tree:
            mat.use_nodes = True
            node_tree = mat.node_tree

        mask_node = self._build_smart_mask_nodes(node_tree)
        if mask_node is None:
            self.report({'ERROR'}, "Failed to build smart mask nodes")
            return {'CANCELLED'}

        res = int(tlm.resolution)
        img_name = f"{layer.name}_SmartMask_{self.mask_type}"
        if img_name in bpy.data.images:
            bpy.data.images.remove(bpy.data.images[img_name])

        bake_ok = False
        # _BakeGuard forces CYCLES, restores node selection, and auto-removes
        # the bake image on failure so bpy.data.images is left clean.
        with _BakeGuard(context, node_tree) as guard:
            img = bpy.data.images.new(img_name, width=res, height=res, alpha=False)
            guard.register_orphan(img)
            try:
                img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass

            bake_node = node_tree.nodes.new("ShaderNodeTexImage")
            bake_node.name = "TLM_smartmask_bake"
            bake_node.image = img
            bake_node.location = (800, -200)
            for n in node_tree.nodes:
                n.select = False
            bake_node.select = True
            node_tree.nodes.active = bake_node

            try:
                if self.mask_type == 'AO':
                    bpy.ops.object.bake(type='AO', save_mode='INTERNAL')
                else:
                    bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'}, save_mode='INTERNAL')
                img.pack()
                layer.use_mask = True
                # Force mask_source back to IMAGE: otherwise, if the user had
                # set a LIVE source (AO / POINTINESS / EDGE_WEAR / …) on this
                # layer before baking, the baked texture would be silently
                # ignored by the mask pipeline.
                layer.mask_source = 'IMAGE'
                layer.mask_image_name = img.name
                bake_ok = True
                guard.commit()  # bake succeeded — keep img
                self.report(
                    {'INFO'},
                    f"Baked '{self.mask_type}' mask to image '{img.name}' on layer '{layer.name}'"
                )
            except Exception as e:
                self.report({'WARNING'}, f"Bake failed: {e}. Nodes added but mask not baked.")
            finally:
                # Always clean up the temp bake node + procedural smart-mask nodes,
                # regardless of bake success.
                if bake_node.name in node_tree.nodes:
                    node_tree.nodes.remove(bake_node)
                for n in [n for n in node_tree.nodes if n.name.startswith("TLM_sm_")]:
                    node_tree.nodes.remove(n)

        if tlm.auto_composite:
            compositing.rebuild_node_tree(mat)

        return {'FINISHED'} if bake_ok else {'CANCELLED'}

    def _build_smart_mask_nodes(self, node_tree):
        """Build procedural smart mask nodes (not baked yet)."""
        mt = self.mask_type

        if mt == 'AO':
            ao = node_tree.nodes.new("ShaderNodeAmbientOcclusion")
            ao.name = "TLM_sm_ao"
            ao.samples = self.ao_samples
            ao.inputs["Distance"].default_value = self.ao_distance
            ao.location = (400, -200)
            emit = node_tree.nodes.new("ShaderNodeEmission")
            emit.name = "TLM_sm_emit"
            emit.location = (600, -200)
            node_tree.links.new(ao.outputs["AO"], emit.inputs["Color"])
            out = node_tree.nodes.new("ShaderNodeOutputMaterial")
            out.name = "TLM_sm_out"
            out.location = (800, -200)
            out.target = 'ALL'
            node_tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
            return ao

        elif mt == 'CURVATURE':
            bevel = node_tree.nodes.new("ShaderNodeBevel")
            bevel.name = "TLM_sm_bevel"
            bevel.samples = 4
            bevel.inputs["Radius"].default_value = 0.05
            bevel.location = (400, -200)
            geo = node_tree.nodes.new("ShaderNodeNewGeometry")
            geo.name = "TLM_sm_geo"
            geo.location = (200, -200)
            dot = node_tree.nodes.new("ShaderNodeVectorMath")
            dot.operation = 'DOT_PRODUCT'
            dot.name = "TLM_sm_dot"
            dot.location = (600, -200)
            node_tree.links.new(bevel.outputs["Normal"], dot.inputs[0])
            node_tree.links.new(geo.outputs["Normal"], dot.inputs[1])
            emit = node_tree.nodes.new("ShaderNodeEmission")
            emit.name = "TLM_sm_emit"
            emit.location = (800, -200)
            node_tree.links.new(dot.outputs["Value"], emit.inputs["Color"])
            out = node_tree.nodes.new("ShaderNodeOutputMaterial")
            out.name = "TLM_sm_out"
            out.location = (1000, -200)
            out.target = 'ALL'
            node_tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
            return bevel

        elif mt == 'FACING':
            geo = node_tree.nodes.new("ShaderNodeNewGeometry")
            geo.name = "TLM_sm_geo"
            geo.location = (400, -200)
            emit = node_tree.nodes.new("ShaderNodeEmission")
            emit.name = "TLM_sm_emit"
            emit.location = (600, -200)
            node_tree.links.new(geo.outputs["Backfacing"], emit.inputs["Color"])
            out = node_tree.nodes.new("ShaderNodeOutputMaterial")
            out.name = "TLM_sm_out"
            out.location = (800, -200)
            out.target = 'ALL'
            node_tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
            return geo

        elif mt == 'HEIGHT':
            geo = node_tree.nodes.new("ShaderNodeNewGeometry")
            geo.name = "TLM_sm_geo"
            geo.location = (200, -200)
            sep = node_tree.nodes.new("ShaderNodeSeparateXYZ")
            sep.name = "TLM_sm_sep"
            sep.location = (400, -200)
            node_tree.links.new(geo.outputs["Position"], sep.inputs["Vector"])
            emit = node_tree.nodes.new("ShaderNodeEmission")
            emit.name = "TLM_sm_emit"
            emit.location = (600, -200)
            node_tree.links.new(sep.outputs["Z"], emit.inputs["Color"])
            out = node_tree.nodes.new("ShaderNodeOutputMaterial")
            out.name = "TLM_sm_out"
            out.location = (800, -200)
            out.target = 'ALL'
            node_tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
            return geo

        return None


classes = [
    TLM_OT_AddLayerMask,
    TLM_OT_AddSmartMask,
]
