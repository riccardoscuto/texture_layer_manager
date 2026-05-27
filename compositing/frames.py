"""Frame assignment + cluster wrapping (visual node grouping)."""

import bpy

from . import _next_id, _tag, _find_tagged
from . import TLM_PREFIX  # noqa: F401


__all__ = [
    '_is_chain_role',
    '_FRAME_Y_GAP',
    '_FRAME_X_GAP',
    '_FRAME_X_SPAN',
    '_assign_layer_frames',
    '_wrap_frame_cluster_by_x',
    '_wrap_frame_cluster',
    '_DEBUG_TAGS',
    '_validate_tags',
    '_LAYER_FRAME_COLORS',
    '_LAYER_FRAME_FALLBACK',
]


# drown out the colors of the nodes themselves.
_LAYER_FRAME_COLORS = {
    'PAINT':       (0.25, 0.35, 0.55),   # blue
    'FILL':        (0.45, 0.45, 0.45),   # neutral gray
    'PROCEDURAL':  (0.40, 0.25, 0.50),   # purple
    'ADJUSTMENT':  (0.55, 0.55, 0.25),   # yellow
    'REFERENCE':   (0.25, 0.50, 0.35),   # green
    'GROUP':       (0.50, 0.25, 0.25),   # dark red
}
_LAYER_FRAME_FALLBACK = (0.35, 0.35, 0.35)


def _is_chain_role(role):
    """True if the role identifies a chain Mix node — these flow between layers
    at the far-right of the channel and would stretch the frame across the
    whole canvas. We leave them outside the frames.

    Covers:
      - `mix`, `mix_{channel}`, `mix_scalar`, `mix_vector` (from `_new_mix*`)
      - `opacity_target_{channel}` (overwrites from `_set_factor` / clipping)

    Preserves inside-frame nodes like `vdist_mix`, `marble_mult`, `mask_*`,
    `proc_*`, `normal_*`, `adj_*`, `fill_*`, `ref_*` — they are clustered near
    the layer's source and don't stretch the frame.
    """
    if not role:
        return False
    return (
        role == "mix"
        or role.startswith("mix_")
        or role.startswith("opacity_target_")
    )


# Y distance that separates channel bands in the generated layout. Two tagged
# nodes of the same layer sitting farther apart than this are considered to
# belong to different channel bands and get their own frame. Matches the
# typical channel Y spacing in _build_channel (~300-400 units).
_FRAME_Y_GAP = 250.0
_FRAME_X_GAP = 700.0
_FRAME_X_SPAN = 620.0


def _assign_layer_frames(node_tree, layers):
    """Wrap each layer's SOURCE nodes in colored NodeFrames, split by channel band.

    Multi-channel layers (FILL with img_* per channel, ADJUSTMENT with adj_*
    replicated per channel, etc.) have source nodes placed at DIFFERENT Y
    positions — one per channel band. A single frame wrapping all of them
    would span the entire vertical layout with empty middle bands, which is
    exactly what we want to avoid.

    Algorithm per layer:
      1. Collect all candidate nodes (skip chain-mixes, material-level, already
         parented, Frame nodes).
      2. Sort by Y descending.
      3. Split into clusters wherever consecutive Ys differ by more than
         `_FRAME_Y_GAP`.
      4. Wrap each cluster in its own NodeFrame.

    Each resulting frame is:
      - Labeled with `layer.name` (auto-syncs on rename via rebuild)
      - Colored by `layer.layer_type` for quick visual identification
      - Tight around its Y band (no empty middle space)

    A layer that touches 3 channels with distant Y gets 3 small frames, all
    the same color & label — the user still sees "these belong to Layer X"
    at a glance, but the canvas stays readable.
    """
    type_by_name = {layer.name: layer.layer_type for layer in layers}

    # 1. Collect candidate nodes per layer.
    # Prefer `tlm_frame_owner` (set on Reference pattern copies so they group
    # under the reference's own frame instead of the source's) and fall back
    # to `tlm_layer` for normal nodes.
    per_layer = {}
    for node in list(node_tree.nodes):
        ln = node.get("tlm_frame_owner") or node.get("tlm_layer")
        if not ln or ln == "__material__":
            continue
        if node.type == 'FRAME':
            continue  # never nest frames in frames
        if node.parent is not None:
            continue  # don't stomp on user-made groupings
        if _is_chain_role(node.get("tlm_role")):
            continue  # chain mixes flow visibly outside the frame
        per_layer.setdefault(ln, []).append(node)

    # 2-4. Cluster by Y band, create one frame per cluster.
    for ln, nodes in per_layer.items():
        if not nodes:
            continue
        color = _LAYER_FRAME_COLORS.get(
            type_by_name.get(ln), _LAYER_FRAME_FALLBACK
        )
        # Sort top-to-bottom on canvas (Blender Y increases upward).
        nodes.sort(key=lambda n: -n.location.y)
        cluster = [nodes[0]]
        for n in nodes[1:]:
            # Gap is measured from cluster's LOWEST node to this one.
            # (cluster is sorted top-down, so cluster[-1] is the lowest so far.)
            if cluster[-1].location.y - n.location.y > _FRAME_Y_GAP:
                _wrap_frame_cluster_by_x(node_tree, cluster, ln, color)
                cluster = [n]
            else:
                cluster.append(n)
        _wrap_frame_cluster_by_x(node_tree, cluster, ln, color)


def _wrap_frame_cluster_by_x(node_tree, nodes, layer_name, color):
    """Split a Y band into smaller frames when nodes sit far apart in X."""
    if not nodes:
        return
    nodes = sorted(nodes, key=lambda n: n.location.x)
    cluster = [nodes[0]]
    for n in nodes[1:]:
        if (n.location.x - cluster[-1].location.x > _FRAME_X_GAP
                or n.location.x - cluster[0].location.x > _FRAME_X_SPAN):
            _wrap_frame_cluster(node_tree, cluster, layer_name, color)
            cluster = [n]
        else:
            cluster.append(n)
    _wrap_frame_cluster(node_tree, cluster, layer_name, color)


def _wrap_frame_cluster(node_tree, nodes, layer_name, color):
    """Create one NodeFrame and parent every node in this cluster to it."""
    if not nodes:
        return
    margin_x = 40.0
    margin_y = 50.0
    min_x = min(n.location.x for n in nodes)
    max_y = max(n.location.y for n in nodes)
    max_x = max(n.location.x + max(getattr(n, "width", 140.0), 140.0)
                for n in nodes)
    min_y = min(n.location.y - max(getattr(n, "height", 100.0), 100.0)
                for n in nodes)
    frame_x = min_x - margin_x
    frame_y = max_y + margin_y

    frame = node_tree.nodes.new("NodeFrame")
    frame.name = f"{TLM_PREFIX}frame_{_next_id()}"
    frame.label = layer_name
    frame.label_size = 20
    frame.location = (frame_x, frame_y)
    try:
        frame.width = max(220.0, max_x - min_x + margin_x * 2.0)
        frame.height = max(160.0, max_y - min_y + margin_y * 2.0)
    except Exception:
        pass
    frame.use_custom_color = True
    frame.color = color
    _tag(frame, layer_name, "layer_frame")
    for n in nodes:
        abs_x = n.location.x
        abs_y = n.location.y
        n.parent = frame
        n.location = (abs_x - frame_x, abs_y - frame_y)


# ── Tag validation (debug mode) ───────────────────────────────────────────────

_DEBUG_TAGS = False  # flip to True during development


def _validate_tags(node_tree, layers):
    """Post-rebuild check: warn about expected tags that are missing."""
    if not _DEBUG_TAGS:
        return
    visible = [l for l in layers if l.visible and l.layer_type != "ADJUSTMENT"]
    for i, layer in enumerate(visible):
        if i == 0:
            continue  # first visible layer has no mix node
        if not _find_tagged(node_tree, layer.name, "mix_base_color"):
            print(f"[TLM TAG WARNING] Missing ({layer.name}, mix_base_color)")
    # Material-level tags (only warn if channels are actually in use)
    has_emission = any(l.use_emission and l.visible for l in layers)
    if has_emission and not _find_tagged(node_tree, "__material__", "emission_strength"):
        print("[TLM TAG WARNING] Missing (__material__, emission_strength)")
    # Bump is now per-layer (one bump_node per use_bump layer) rather than a
    # single bump_final node. Warn if any use_bump layer is missing its tag.
    for layer in layers:
        if layer.visible and getattr(layer, 'use_bump', False):
            if not _find_tagged(node_tree, layer.name, "bump_node"):
                print(f"[TLM TAG WARNING] Missing ({layer.name}, bump_node)")



