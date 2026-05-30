"""Material Output wiring + BSDF discovery + alpha-via-transparent shader."""

import bpy

from . import _next_id, _tag, _find_tagged
from . import TLM_PREFIX  # noqa: F401
# _LEGACY_BLEND_METHOD + _NEW_RENDER_METHOD live in procedurals.py
# (loaded BEFORE output in __init__.py), so eager import works.
from . import _LEGACY_BLEND_METHOD, _NEW_RENDER_METHOD


__all__ = [
    '_sync_material_alpha_method',
    '_find_bsdf',
    '_ensure_bsdf_to_output',
    '_wire_alpha_via_transparent_bsdf',
]

def _sync_material_alpha_method(material, alpha_connected, tlm):
    """Set the material's blend method based on the alpha pipeline state.

    Even when TLM correctly wires something to BSDF.Alpha, Eevee with
    mat.blend_method='OPAQUE' (the default for new materials) silently
    ignores the input and renders the surface opaque. This was the
    single biggest UX surprise for cutout / decal / foliage workflows —
    "I set output_channel to Alpha but nothing happens".

    Behaviour:
      - tlm.alpha_blend_method == 'AUTO' (default):
          * alpha_connected → HASHED (good general default — supports
            smooth alpha, anti-aliased edges, no manual sorting)
          * else → OPAQUE (no perf overhead when alpha isn't used)
      - any other value: forced regardless of connection state. Users
        who explicitly want BLEND for a glass material keep that even
        when no alpha layer is present.

    The Blender 4.2+ API replaced ``mat.blend_method`` with
    ``mat.surface_render_method`` (only DITHERED / BLENDED — CLIP and
    HASHED collapsed). We write to whichever attribute exists so the
    same TLM addon works on 4.0 / 4.1 / 4.2 / 5.0 without branching at
    install time.
    """
    requested = getattr(tlm, 'alpha_blend_method', 'AUTO')
    if requested == 'AUTO':
        resolved = 'HASHED' if alpha_connected else 'OPAQUE'
    else:
        resolved = requested

    # In Blender 5.0, forcing surface_render_method='DITHERED' while TLM has
    # no alpha route can make Cycles show a black material. For AUTO/no-alpha,
    # do not touch the new API. On legacy Blender versions that only expose
    # blend_method, still reset to OPAQUE so Eevee leaves transparency mode.
    if not alpha_connected and requested == 'AUTO':
        if not hasattr(material, 'surface_render_method') and hasattr(material, 'blend_method'):
            try:
                material.blend_method = 'OPAQUE'
            except (TypeError, AttributeError):
                pass
        return

    # Legacy attribute (Blender ≤ 4.1) — still respected on 4.2+ as a
    # deprecated alias. Safe to write only when we have a non-default
    # reason to (alpha actually wired, or user explicitly overrode).
    if hasattr(material, 'blend_method'):
        try:
            material.blend_method = _LEGACY_BLEND_METHOD.get(resolved, 'OPAQUE')
        except (TypeError, AttributeError):
            pass

    # New attribute (Blender 4.2+). Only set BLENDED for explicit BLEND
    # mode; otherwise DITHERED is the right transparency style for
    # cutout / hashed and is safe when alpha is genuinely driven.
    if alpha_connected and hasattr(material, 'surface_render_method'):
        try:
            material.surface_render_method = _NEW_RENDER_METHOD.get(resolved, 'DITHERED')
        except (TypeError, AttributeError):
            pass


# ── Selective emission selector ──────────────────────────────────────────────

def _find_bsdf(node_tree):
    for node in node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED' and not node.name.startswith(TLM_PREFIX):
            return node
    # Create one if missing
    bsdf = node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (400, 0)
    out = next((n for n in node_tree.nodes if n.type == 'OUTPUT_MATERIAL'
                and not n.name.startswith(TLM_PREFIX)), None)
    if out:
        node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return bsdf


def _ensure_bsdf_to_output(node_tree, bsdf, only_if_surface_empty=False):
    """Make sure BSDF.BSDF → MaterialOutput.Surface is connected.

    Called after _clear_tlm_nodes removes the alpha-wrap (Mix Shader +
    Transparent BSDF). Without this, removing the wrap would leave
    Material Output's Surface input dangling → solid black render.
    Idempotent: no-op if already connected to the BSDF directly. When
    only_if_surface_empty is true, custom non-TLM Surface links are left alone.
    """
    mat_out = next((n for n in node_tree.nodes
                    if n.type == 'OUTPUT_MATERIAL'
                    and not n.name.startswith(TLM_PREFIX)), None)
    if mat_out is None:
        return False
    surface_input = mat_out.inputs.get("Surface")
    if surface_input is None:
        return False
    # Skip if already connected to this BSDF
    for link in surface_input.links:
        if link.from_socket.node == bsdf:
            return True
    if only_if_surface_empty and surface_input.links:
        return False
    try:
        node_tree.links.new(bsdf.outputs["BSDF"], surface_input)
        return True
    except Exception:
        return False


def _wire_alpha_via_transparent_bsdf(node_tree, alpha_out, bsdf):
    """Route alpha through Mix Shader + Transparent BSDF for engine portability.

    Why this exists:
      Blender 5.0 Cycles doesn't reliably honour a value driven into
      Principled BSDF.Alpha — the surface stays opaque even when the
      alpha is meant to be 0 (user-reported: object placed behind the
      cube is not visible through alpha=0 regions in Cycles, while
      Eevee correctly shows the cutout). The portable, engine-agnostic
      pattern is to wrap the surface output:

          Principled BSDF ──â”
                            â”œ── Mix Shader (factor = alpha) ── Output
          Transparent BSDF ─â”˜

      Mix Shader factor convention: 0 → input 1, 1 → input 2. So we
      put Transparent in slot 1 and Principled in slot 2, giving
      alpha = 0 → invisible, alpha = 1 → opaque. Works identically in
      both Eevee and Cycles regardless of blend_method or
      surface_render_method.

    Also keeps the alpha → BSDF.Alpha connection (for Eevee bake paths
    and for any external tool that reads BSDF.Alpha directly).
    """
    mat_out = next((n for n in node_tree.nodes
                    if n.type == 'OUTPUT_MATERIAL'
                    and not n.name.startswith(TLM_PREFIX)), None)
    if mat_out is None:
        return False
    surface_input = mat_out.inputs.get("Surface")
    if surface_input is None:
        return False

    # Connect alpha to BSDF.Alpha too — preserves the previous Eevee
    # path and the bake operator's "read BSDF.Alpha to get alpha
    # output" assumption.
    if "Alpha" in bsdf.inputs:
        try:
            node_tree.links.new(alpha_out, bsdf.inputs["Alpha"])
        except Exception:
            pass

    trans = None
    mix = None
    try:
        trans = node_tree.nodes.new("ShaderNodeBsdfTransparent")
        trans.name = f"{TLM_PREFIX}alpha_transparent"
        trans.location = (bsdf.location.x + 220, bsdf.location.y - 200)

        mix = node_tree.nodes.new("ShaderNodeMixShader")
        mix.name = f"{TLM_PREFIX}alpha_mix_shader"
        mix.location = (bsdf.location.x + 440, bsdf.location.y)

        node_tree.links.new(trans.outputs["BSDF"], mix.inputs[1])
        node_tree.links.new(bsdf.outputs["BSDF"], mix.inputs[2])
        node_tree.links.new(alpha_out, mix.inputs[0])  # Fac

        # Drop the existing BSDF → Surface link only after the replacement
        # shader is internally complete. If the final Surface link fails, the
        # except block restores the plain BSDF route.
        for link in list(surface_input.links):
            if link.from_socket.node == bsdf:
                node_tree.links.remove(link)
        node_tree.links.new(mix.outputs["Shader"], surface_input)
    except Exception:
        _ensure_bsdf_to_output(node_tree, bsdf, only_if_surface_empty=True)
        for node in (mix, trans):
            if node is not None:
                try:
                    node_tree.nodes.remove(node)
                except Exception:
                    pass
        return False
    return True


# ── Flatten ───────────────────────────────────────────────────────────────────

