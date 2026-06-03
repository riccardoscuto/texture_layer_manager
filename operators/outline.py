"""Inverted-hull outline operators (anime / toon).

A *true* crisp outline like the one every anime addon ships is an
**inverted hull**: a slightly larger shell (Solidify modifier with flipped
normals) painted with a flat black, backface-culled material. It is
object-level geometry — it is NOT part of the TLM material/preset (a .tlm
saves only the layer stack), so it is applied with a one-click button and
lives on the object, exactly like outlines in Unity/other toon kits.

Why not pure-shader: a uniform-width ink line needs a real shell (extra
geometry). Shader-only rims (Fresnel/NdotV) only graze the silhouette and
break on hard edges; a displacement shell can't be built because Cycles
does not expose Backfacing during displacement. Hence: Solidify.
"""

import bpy
from bpy.types import Operator

_OUTLINE_MOD = "TLM_Outline_Solidify"
_OUTLINE_MAT = "TLM_OutlineMat"


def _build_outline_material(color):
    """(Re)build the shared flat-ink outline material.

    Designed for EEVEE Next: ``use_backface_culling`` removes the shell's
    camera-facing side (with Solidify's flipped normals), leaving only the
    silhouette rim painted with a flat, unlit emission ink. EEVEE is the
    right engine for cel/toon anyway. (Cycles has no material backface
    culling, so the hull there reads as a solid dark shell — render toon
    looks in EEVEE Next.)
    """
    mat = bpy.data.materials.get(_OUTLINE_MAT) or bpy.data.materials.new(_OUTLINE_MAT)
    mat.use_nodes = True
    mat.use_backface_culling = True
    nt = mat.node_tree
    nt.nodes.clear()
    emit = nt.nodes.new("ShaderNodeEmission"); emit.location = (-200, 0)
    emit.inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
    emit.inputs["Strength"].default_value = 1.0
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (40, 0)
    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    return mat


class TLM_OT_AddOutline(Operator):
    """Add a crisp inverted-hull outline (Solidify shell + flat-black material) to the active object.

    Object-level: not stored in the material/preset. Tweak thickness and colour in the redo panel (F9)."""
    bl_idname = "tlm.add_outline"
    bl_label = "Add Outline"
    bl_options = {'REGISTER', 'UNDO'}

    thickness: bpy.props.FloatProperty(
        name="Thickness", default=0.02, min=0.0, max=1.0, step=1, precision=3,
        description="Outline shell thickness, in object units (scale to your model)")
    color: bpy.props.FloatVectorProperty(
        name="Color", subtype='COLOR', size=3, min=0.0, max=1.0,
        default=(0.02, 0.02, 0.03),
        description="Outline ink colour")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        mat = _build_outline_material(self.color)
        if mat.name not in [m.name for m in obj.data.materials if m]:
            obj.data.materials.append(mat)
        outline_idx = list(obj.data.materials).index(mat)

        mod = obj.modifiers.get(_OUTLINE_MOD)
        if mod is None:
            mod = obj.modifiers.new(name=_OUTLINE_MOD, type='SOLIDIFY')  # appended last
        mod.thickness = self.thickness
        mod.offset = 1.0
        mod.use_flip_normals = True
        mod.material_offset = outline_idx       # shell faces → outline slot
        mod.use_rim = False
        self.report({'INFO'}, f"Outline added (thickness {self.thickness:.3f})")
        return {'FINISHED'}


class TLM_OT_RemoveOutline(Operator):
    """Remove the inverted-hull outline (Solidify shell + material slot) from the active object"""
    bl_idname = "tlm.remove_outline"
    bl_label = "Remove Outline"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH'
                and obj.modifiers.get(_OUTLINE_MOD) is not None)

    def execute(self, context):
        obj = context.active_object
        mod = obj.modifiers.get(_OUTLINE_MOD)
        if mod:
            obj.modifiers.remove(mod)
        for i, m in enumerate(obj.data.materials):
            if m and m.name == _OUTLINE_MAT:
                obj.data.materials.pop(index=i)
                break
        self.report({'INFO'}, "Outline removed")
        return {'FINISHED'}


classes = [TLM_OT_AddOutline, TLM_OT_RemoveOutline]
