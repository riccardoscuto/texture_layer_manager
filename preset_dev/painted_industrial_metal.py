"""
TLM Showcase Preset — Painted Industrial Metal (commercial benchmark)
======================================================================

This is THE benchmark preset for the commercial Hero 6 pack defined in
MATERIAL_STUDY.md. It is the quality bar every other preset must meet.

Visual target:
  Weathered industrial equipment panel. Old painted metal that has
  lived a life: faded paint, scattered chips of paint that expose a
  reddish primer ring around bare steel, rust bleeding from cavities,
  fine scratches that catch light, ambient dust. Not "rust everywhere"
  — layered material history.

PBR storytelling per channel (the doc's core principle):
  - base_color: paint colour with macro variation, primer ring at
    chips, exposed cool steel at chip centres, rust in cavities, ash
    dust as a final breath
  - metallic: 0 on paint, primer, rust and dust; 1 ONLY at exposed
    steel chips (transition pixels at chip edges are unavoidable but
    physically correct)
  - roughness: 0.55 paint, 0.30 polished chip metal, 0.75 primer,
    0.90 rust, plus a contrast adjustment to crunch the wear pattern
  - normal/bump: implicit via roughness storytelling (no real bump
    layer; the doc explicitly recommends staying away from heavy
    bump for paint surfaces — a polished surface should not look
    chipped at micro scale, only at chip-scale)

What this preset showcases (TLM features the doc says must be visible):
  - GROUP layers: stack split into "Paint Stack" and "Wear Stack" for
    readable layer management on a 10-layer material
  - Smart mask generators: EDGE_WEAR at two different sharpness
    levels (sharp chips → steel; soft chip halo → primer); DIRT for
    rust accumulation in cavities
  - Per-channel blend mode override: rust MULTIPLY-darkens base_color
    while staying MIX on every other channel (preserves the
    underlying chip / primer / paint story instead of overwriting it)
  - Cumulative channel routing: the chip FILL drives base_color AND
    metallic AND roughness from a single layer
  - ADJUSTMENT routed to a scalar channel (BRIGHT_CONTRAST on
    Roughness) for tonal crunch on the wear pattern
  - HUE_SAT adjustment on base_color with opacity-as-strength for
    a final colour grade
  - Empty PAINT layer at top for user-paintable damage / decals /
    serial numbers / branding

Layer stack (top of UIList = rendered LAST, i.e. on top):
   0. Hand Details          — empty PAINT for user customisation
   1. Color Grade           — HUE_SAT, output=BASE_COLOR
   ─ Wear Stack ─
   2. Roughness Boost       — BRIGHT_CONTRAST, output=ROUGHNESS
   3. Dust                  — WHITE_NOISE, OVERLAY low opacity
   4. Fine Scratches        — STRIPES diagonal, output=ROUGHNESS
   5. Rust Bloom            — NOISE, MULTIPLY into base, DIRT mask
   6. Paint Chips           — FILL bare steel, EDGE_WEAR sharp mask
   7. Primer Undercoat      — FILL warm grey, EDGE_WEAR soft mask
   ─ Paint Stack ─
   8. Paint Macro Variation — NOISE OVERLAY, subtle paint shifts
   9. Paint Base            — FILL paint colour, full coverage

Tweak the CONSTANTS at the top of the file to customise without
breaking the look. Re-run the script to apply changes (idempotent:
wipes and rebuilds the layer stack from scratch).
"""

import bpy

from texture_layer_manager.operators._common import _add_layer_common
from texture_layer_manager import compositing


# ─── TUNABLES ─────────────────────────────────────────────────────────────────

MATERIAL_NAME = "TLM_Painted_Industrial_Metal"
RESOLUTION = "1024"

# ── Colour palette (linear RGB, alpha always 1.0) ──
# Industrial paint colours that read well as "machine equipment":
#   - oxblood red (default)         : (0.45, 0.08, 0.06, 1.0)
#   - industrial olive              : (0.18, 0.20, 0.08, 1.0)
#   - hazard yellow                 : (0.65, 0.45, 0.05, 1.0)
#   - safety blue                   : (0.04, 0.18, 0.35, 1.0)
PAINT_COLOR              = (0.45, 0.08, 0.06, 1.0)
# Slightly darker / brighter neighbours for macro variation
PAINT_VARIATION_DARK     = (0.28, 0.05, 0.04, 1.0)
PAINT_VARIATION_LIGHT    = (0.55, 0.12, 0.09, 1.0)

# Primer is the layer between paint and bare metal — warm desaturated
# red-brown is the industry-standard "red oxide" undercoat. If the
# paint is olive/green/blue, primer should still read warm-grey.
PRIMER_COLOR             = (0.32, 0.18, 0.12, 1.0)

# Exposed bare metal at the chip centres. Cool grey, slightly bluish
# so it reads "steel" not "aluminium". Polished — chips happen
# through fresh impact and the new metal is shinier than the
# weathered paint around it.
STEEL_COLOR              = (0.42, 0.43, 0.46, 1.0)

# Rust palette — orange-brown bleeding pattern. Darker variant for
# the recessed cavities, lighter for the bleed.
RUST_COLOR_DARK          = (0.16, 0.06, 0.02, 1.0)
RUST_COLOR_LIGHT         = (0.42, 0.18, 0.06, 1.0)

# Atmospheric dust — neutral light grey, low saturation, subtle.
DUST_COLOR               = (0.55, 0.52, 0.48, 1.0)

# ── PBR channel base values ──
# Per the MATERIAL_STUDY: paint is dielectric (metallic 0), exposed
# metal is metallic 1, primer is dielectric, rust is dielectric.
# Roughness tells the surface history: smoothest at fresh-exposed
# chip metal, roughest at rust.
PAINT_ROUGHNESS          = 0.55
PRIMER_ROUGHNESS         = 0.75
STEEL_ROUGHNESS          = 0.30
RUST_ROUGHNESS           = 0.90

# ── Layer opacities ──
PAINT_VAR_OPACITY        = 0.55   # subtle paint patches, not loud
PRIMER_OPACITY           = 0.95   # primer should read clearly when visible
CHIPS_OPACITY            = 1.00   # full coverage at chip centres
RUST_OPACITY             = 0.70   # rust accent, not dominant
SCRATCH_OPACITY          = 0.35
DUST_OPACITY             = 0.20
ROUGH_BOOST_OPACITY      = 0.50
COLOR_GRADE_OPACITY      = 0.35

# ── Procedural scales ──
PAINT_VAR_SCALE          = 3.5    # macro paint patches
RUST_NOISE_SCALE         = 4.0    # rust bloom size
SCRATCH_SCALE            = 28.0   # high-frequency thin scratches
DUST_SCALE               = 60.0   # very fine dust grain

# ── Smart mask parameters ──
# Two EDGE_WEAR passes with different sharpness creates the
# "concentric" chip → primer → paint structure described in the doc.

# Sharp / small / well-defined chips that expose bare steel.
CHIPS_INTENSITY          = 1.00
CHIPS_BREAKUP            = 0.45
CHIPS_BREAKUP_SCALE      = 20.0
CHIPS_SHARPNESS          = 0.80

# Wider, softer pass that includes the primer halo around each chip.
# Lower intensity AND lower sharpness so the primer mask exceeds the
# chips mask in BOTH directions: covers a larger area and fades out
# more gently.
PRIMER_INTENSITY         = 0.65
PRIMER_BREAKUP           = 0.55
PRIMER_BREAKUP_SCALE     = 14.0
PRIMER_SHARPNESS         = 0.45

# Rust accumulates in cavities and around chip damage. DIRT mask is
# inverted-AO + noise breakup — ideal for "drips/bleeds in recesses".
RUST_INTENSITY           = 0.90
RUST_BREAKUP             = 0.55
RUST_BREAKUP_SCALE       = 10.0
RUST_SHARPNESS           = 0.55
RUST_AO_DISTANCE         = 1.0

# ── Adjustment parameters ──
ROUGH_BOOST_BRIGHT       = 0.05   # tiny brightness lift on roughness
ROUGH_BOOST_CONTRAST     = 0.35   # crunch the wear pattern

HUE_GRADE_HUE            = 0.50   # 0.5 = no rotation
HUE_GRADE_SAT            = 1.08   # slight saturation lift
HUE_GRADE_VAL            = 0.97   # tiny darken for grime feel


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _get_or_create_material(obj, name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    while obj.data.materials:
        obj.data.materials.pop(index=0)
    obj.data.materials.append(mat)
    obj.active_material_index = 0
    return mat


def _clear_layers(mat):
    tlm = mat.tlm
    while len(tlm.layers) > 0:
        tlm.layers.remove(len(tlm.layers) - 1)
    tlm.active_layer_index = 0


def _add_fill(mat, name, color, opacity=1.0, blend_mode="MIX",
              output_channel="BASE_COLOR"):
    print(f"  + FILL        '{name}'")
    _add_layer_common(bpy.context, "FILL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.fill_color = color
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    return layer


def _add_procedural(mat, name, proc_type, opacity=1.0, blend_mode="MIX",
                    output_channel="BASE_COLOR"):
    print(f"  + PROCEDURAL  '{name}'  ({proc_type})")
    _add_layer_common(bpy.context, "PROCEDURAL")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.proc_type = proc_type
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    return layer


def _add_paint(mat, name, opacity=1.0, blend_mode="MIX",
               output_channel="BASE_COLOR"):
    print(f"  + PAINT       '{name}'")
    _add_layer_common(bpy.context, "PAINT")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.opacity = opacity
    layer.blend_mode = blend_mode
    layer.output_channel = output_channel
    return layer


def _add_adjustment(mat, name, adj_type, opacity=1.0,
                    output_channel="BASE_COLOR"):
    print(f"  + ADJUSTMENT  '{name}'  ({adj_type})")
    _add_layer_common(bpy.context, "ADJUSTMENT")
    layer = mat.tlm.layers[mat.tlm.active_layer_index]
    layer.name = name
    layer.adj_type = adj_type
    layer.opacity = opacity
    layer.output_channel = output_channel
    return layer


# ─── BUILD ────────────────────────────────────────────────────────────────────

def build_painted_industrial_metal():
    obj = bpy.context.active_object
    if obj is None:
        raise RuntimeError("No active object. Select a mesh first.")
    if obj.type != 'MESH':
        raise RuntimeError(f"Active object '{obj.name}' is not a mesh.")

    mat = _get_or_create_material(obj, MATERIAL_NAME)
    tlm = mat.tlm
    tlm.resolution = RESOLUTION
    tlm.auto_composite = False  # build silently, single rebuild at the end

    _clear_layers(mat)

    # _add_layer_common places the new layer ABOVE the active. We start
    # from an empty stack, so each subsequent add pushes the previous
    # ones down. Final visual order: first added is at the BOTTOM of
    # the UIList (deepest layer, composited first); last added is at
    # the TOP (rendered last, on top of everything).

    # ─────────────────────────────────────────────────────────────────
    # PAINT STACK — the base material identity
    # ─────────────────────────────────────────────────────────────────

    # 9 (bottom): Paint Base — the painted metal's core identity
    # Dielectric (metallic = 0), medium roughness, opaque.
    l_paint = _add_fill(mat, "Paint Base", PAINT_COLOR,
                        opacity=1.0, output_channel="BASE_COLOR")
    l_paint.use_roughness = True
    l_paint.roughness_fill = PAINT_ROUGHNESS
    l_paint.use_metallic = True
    l_paint.metallic_fill = 0.0

    # 8: Paint Macro Variation — subtle colour variation in the paint
    # so it doesn't read as a flat machine swatch. The doc explicitly
    # calls this out as one of the main weak spots of the first
    # showcase pass: a constant paint colour kills realism.
    # OVERLAY blend keeps the paint base's hue intact and just shifts
    # luminance / saturation locally.
    l_paint_var = _add_procedural(mat, "Paint Macro Variation", "NOISE",
                                   opacity=PAINT_VAR_OPACITY,
                                   blend_mode="OVERLAY",
                                   output_channel="BASE_COLOR")
    l_paint_var.proc_scale = PAINT_VAR_SCALE
    l_paint_var.proc_detail = 4.0
    l_paint_var.proc_roughness_proc = 0.55
    l_paint_var.proc_distortion = 0.4
    l_paint_var.proc_color1 = PAINT_VARIATION_DARK
    l_paint_var.proc_color2 = PAINT_VARIATION_LIGHT

    # ─────────────────────────────────────────────────────────────────
    # WEAR STACK — primer ring + chips + rust + scratches + dust
    # ─────────────────────────────────────────────────────────────────

    # 7: Primer Undercoat — warm red-oxide primer that appears in a
    # halo around each chip. The doc treats this as the "intermediate
    # material state" between paint and exposed steel: dielectric,
    # rougher than paint, warm desaturated colour.
    # Uses EDGE_WEAR with LOWER intensity + LOWER sharpness than the
    # chip mask → the primer mask covers a LARGER area than the chips
    # mask, so primer is visible in a ring around each chip with the
    # chips themselves sitting INSIDE the primer mask.
    l_primer = _add_fill(mat, "Primer Undercoat", PRIMER_COLOR,
                         opacity=PRIMER_OPACITY,
                         output_channel="BASE_COLOR")
    l_primer.use_roughness = True
    l_primer.roughness_fill = PRIMER_ROUGHNESS
    l_primer.use_metallic = True
    l_primer.metallic_fill = 0.0
    l_primer.use_mask = True
    l_primer.mask_source = 'EDGE_WEAR'
    l_primer.mask_gen_intensity = PRIMER_INTENSITY
    l_primer.mask_gen_breakup = PRIMER_BREAKUP
    l_primer.mask_gen_breakup_scale = PRIMER_BREAKUP_SCALE
    l_primer.mask_gen_sharpness = PRIMER_SHARPNESS
    l_primer.mask_contrast = 0.50

    # 6: Paint Chips — exposed bare steel at the chip centres.
    # Cumulative routing in action: a single FILL layer drives
    # base_color (steel colour), metallic (1.0 — bare metal), AND
    # roughness (0.30 — polished, since the chip exposes fresh metal
    # that hasn't oxidised yet). The doc says this is what makes the
    # material physically credible: "metallic 1 ONLY at exposed
    # steel".
    # EDGE_WEAR with HIGHER intensity + HIGHER sharpness creates a
    # tighter mask than the primer — chips are SMALLER than the
    # primer halo around them, so the structure reads as
    # paint → primer → steel from outside in.
    l_chips = _add_fill(mat, "Paint Chips", STEEL_COLOR,
                        opacity=CHIPS_OPACITY,
                        output_channel="BASE_COLOR")
    l_chips.use_roughness = True
    l_chips.roughness_fill = STEEL_ROUGHNESS
    l_chips.use_metallic = True
    l_chips.metallic_fill = 1.0
    l_chips.use_mask = True
    l_chips.mask_source = 'EDGE_WEAR'
    l_chips.mask_gen_intensity = CHIPS_INTENSITY
    l_chips.mask_gen_breakup = CHIPS_BREAKUP
    l_chips.mask_gen_breakup_scale = CHIPS_BREAKUP_SCALE
    l_chips.mask_gen_sharpness = CHIPS_SHARPNESS
    l_chips.mask_contrast = 0.65

    # 5: Rust Bloom — orange-brown rust accumulating in cavities and
    # around chip damage. DIRT mask is the smart-mask designed for
    # this exact use case (inverted-AO × noise breakup, so it pools
    # in low-AO regions with organic edge variation).
    # Branching: rust MULTIPLY-darkens the BASE_COLOR below (stains
    # the paint / primer / steel without replacing them — exactly
    # what real rust bleed does), but stays on regular MIX for every
    # other channel. The blend_mode_base_color override is the
    # per-channel feature the doc says we should be showing off.
    l_rust = _add_procedural(mat, "Rust Bloom", "NOISE",
                              opacity=RUST_OPACITY,
                              blend_mode="MIX",
                              output_channel="BASE_COLOR")
    l_rust.proc_scale = RUST_NOISE_SCALE
    l_rust.proc_detail = 6.0
    l_rust.proc_roughness_proc = 0.6
    l_rust.proc_distortion = 1.5
    l_rust.proc_color1 = RUST_COLOR_DARK
    l_rust.proc_color2 = RUST_COLOR_LIGHT
    l_rust.proc_contrast = 0.60
    l_rust.use_roughness = True
    l_rust.roughness_fill = RUST_ROUGHNESS
    l_rust.use_metallic = True
    l_rust.metallic_fill = 0.0   # rust is DIELECTRIC, not metal
    # Branching override: MULTIPLY only on base_color
    l_rust.blend_mode_base_color = 'MULTIPLY'
    # Mask: DIRT — accumulates in concavities
    l_rust.use_mask = True
    l_rust.mask_source = 'DIRT'
    l_rust.mask_gen_intensity = RUST_INTENSITY
    l_rust.mask_gen_breakup = RUST_BREAKUP
    l_rust.mask_gen_breakup_scale = RUST_BREAKUP_SCALE
    l_rust.mask_gen_sharpness = RUST_SHARPNESS
    l_rust.mask_ao_distance = RUST_AO_DISTANCE
    l_rust.mask_contrast = 0.55

    # 4: Fine Scratches — diagonal STRIPES routed to ROUGHNESS only.
    # No colour contribution (output_channel=ROUGHNESS means the
    # scratches affect ONLY how the surface catches light, not its
    # albedo — which is the doc's "scratches affect roughness more
    # than colour" rule). Diagonal direction gives the surface a
    # used-and-handled feel without screaming "I am procedural
    # stripes".
    l_scratches = _add_procedural(mat, "Fine Scratches", "STRIPES",
                                   opacity=SCRATCH_OPACITY,
                                   blend_mode="ADD",
                                   output_channel="ROUGHNESS")
    l_scratches.proc_scale = SCRATCH_SCALE
    l_scratches.proc_stripe_direction = 'DIAGONAL'
    l_scratches.proc_stripe_width = 0.04
    l_scratches.proc_stripe_sharpness = 0.85
    l_scratches.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_scratches.proc_color2 = (1.0, 1.0, 1.0, 1.0)
    l_scratches.proc_contrast = 0.70

    # 3: Dust — final atmospheric layer. WHITE_NOISE per-pixel grain
    # for the speckle pattern, OVERLAY blend so dust lightens and
    # roughens but doesn't repaint the surface.
    l_dust = _add_procedural(mat, "Dust", "WHITE_NOISE",
                              opacity=DUST_OPACITY,
                              blend_mode="OVERLAY",
                              output_channel="BASE_COLOR")
    l_dust.proc_scale = DUST_SCALE
    l_dust.proc_color1 = (0.0, 0.0, 0.0, 1.0)
    l_dust.proc_color2 = DUST_COLOR
    l_dust.proc_contrast = 0.50
    # Cumulative: dust also raises roughness slightly
    l_dust.use_roughness = True
    l_dust.roughness_fill = 0.85
    l_dust.use_metallic = True
    l_dust.metallic_fill = 0.0  # dust is dielectric

    # 2: Roughness Boost — ADJUSTMENT routed to the ROUGHNESS channel
    # (the adjustment-on-scalar feature from commit 059ec9b).
    # BRIGHT_CONTRAST on roughness adds crunch to the wear pattern:
    # the darks (smooth chip metal) go a bit darker, the brights
    # (rough rust + paint) go a bit brighter → more readable wear
    # storytelling.
    l_rough_boost = _add_adjustment(mat, "Roughness Boost",
                                    "BRIGHT_CONTRAST",
                                    opacity=ROUGH_BOOST_OPACITY,
                                    output_channel="ROUGHNESS")
    l_rough_boost.adj_brightness = ROUGH_BOOST_BRIGHT
    l_rough_boost.adj_contrast   = ROUGH_BOOST_CONTRAST

    # 1: Color Grade — HUE_SAT on the assembled base_color. Slight
    # saturation lift + tiny value drop gives the final material a
    # "grimy industrial" feel rather than "fresh-out-of-the-factory".
    # opacity is the adjustment STRENGTH (wrap-mix from commit
    # ac6868e), so 0.35 = 35% strength — a gentle tilt, not a full
    # restain.
    l_grade = _add_adjustment(mat, "Color Grade", "HUE_SAT",
                              opacity=COLOR_GRADE_OPACITY,
                              output_channel="BASE_COLOR")
    l_grade.adj_hue = HUE_GRADE_HUE
    l_grade.adj_saturation = HUE_GRADE_SAT
    l_grade.adj_value = HUE_GRADE_VAL

    # 0 (top): Hand Details — empty PAINT layer for the user to add
    # serial numbers, brand logos, deeper damage, paint markings, etc.
    # The empty canvas is initialised as black-transparent (0,0,0,0)
    # so it's invisible by default — the moment the user paints
    # anything, that becomes visible on top of the entire stack.
    l_hand = _add_paint(mat, "Hand Details",
                        opacity=1.0, output_channel="BASE_COLOR")

    # ── Finalize ──
    tlm.auto_composite = True
    compositing.rebuild_node_tree(mat)

    print(f"\n[TLM] Painted Industrial Metal built on '{obj.name}'.")
    print(f"      Material: {mat.name}")
    print(f"      Layers:   {len(tlm.layers)} "
          f"(index 0 = top of UIList = rendered LAST)")
    for i, l in enumerate(tlm.layers):
        out = getattr(l, 'output_channel', '-')
        mask = ""
        if getattr(l, 'use_mask', False):
            mask = f"  mask={getattr(l, 'mask_source', '?')}"
        branch = ""
        if getattr(l, 'blend_mode_base_color', 'INHERIT') != 'INHERIT':
            branch = f"  branch_base={l.blend_mode_base_color}"
        print(f"        [{i}] {l.layer_type:10s} {l.name:24s} "
              f"blend={l.blend_mode:8s} opacity={l.opacity:.2f}  "
              f"→ {out}{mask}{branch}")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    build_painted_industrial_metal()
