# Signal Layer Spec

## Goal

Add a new layer type dedicated to building reusable grayscale procedural signals before final colorization.

This feature exists to unlock materials whose structure depends on intermediate scalar fields reused across multiple channels, for example:

- burnt sand / rock breakup
- layered erosion masks
- reusable roughness + bump drivers
- procedural cutout / wear systems
- toon ramps driven by shared fac fields

Today, most procedural layers in TLM follow this effective path:

1. procedural source
2. optional color ramp / colorization
3. blend into a final channel

That is strong for visually oriented approximation, but weak for node graphs where the important work happens before colorization.

The new Signal Layer introduces an explicit scalar stage.

## Problem Statement

Some reference materials are hard to reproduce with quality because the reference graph is built from grayscale fields combined with math nodes, then reused in several destinations.

Typical reference pattern:

1. build grayscale signal A
2. build grayscale signal B
3. combine A and B with math
4. use the resulting field to drive:
   - base color separation
   - roughness breakup
   - bump height
   - displacement
   - alpha masks

In the current addon, if we try to translate those structures as standard PROCEDURAL layers, the signal gets colorized too early and blend modes operate on final visible layers rather than on intermediate scalar data.

Result:

- materials can look close from a distance
- but they are not structurally faithful
- and many materials become unstable or washed out when we try to follow the original graph more literally

## Proposed Feature

Add a new `layer_type = 'SIGNAL'`.

A Signal Layer produces a scalar field in the range 0..1 and does not directly contribute visible color to the material unless another layer references it.

This layer is conceptually similar to a reusable procedural mask, but richer than the current mask system because it:

- can be stored as a first-class layer in the stack
- can use the full procedural generator set
- can combine with another signal using math
- can be referenced by other layers as a source
- can optionally preview itself in the viewport for debugging

## High-Level Behavior

A SIGNAL layer should:

- appear in the layer stack like other layers
- generate a scalar output only
- not route directly to Base Color / Roughness / Metallic / etc by default
- be available as a source in mask and driver contexts
- optionally expose a preview toggle to visualize the signal as grayscale

A SIGNAL layer should not:

- create Principled BSDF contribution by itself
- create direct PBR channel outputs unless explicitly previewed
- behave like a normal visible layer with color + opacity semantics

## Primary Use Cases

### 1. Reusable material breakup

One signal defines where rock appears.

That same signal is reused to drive:

- darker rock color
- lower or higher roughness
- bump height
- alpha clipping

### 2. Multi-stage procedural construction

Signal A = broad islands.
Signal B = cellular edge breakup.
Signal C = A combined with B using math.

Visible layers use Signal C as mask.

### 3. Toon / stylized control

Signal from `NDOTL`, `NDOTH`, `FRESNEL`, `GRADIENT`, or `GABOR` can be combined and reused for:

- emissive banding
- highlight masks
- rim masks
- alphaized light effects

### 4. Smarter procedural presets

Future presets can build clean logical blocks:

- signals first
- visible fills after
- bump/roughness from the same source

## Data Model

### New Layer Type

In `properties.py`, extend `layer_type` with:

- `('SIGNAL', "Signal", "Reusable grayscale procedural signal")`

### Core Signal Properties

A Signal Layer should reuse most of the existing PROCEDURAL property set rather than introducing a second unrelated system.

Recommended properties:

- `signal_type`
  - mirrors current procedural source list
  - can be an alias to `proc_type`, or SIGNAL can literally reuse `proc_type`
- mapping / coords
  - reuse existing procedural mapping properties
- thresholding / shaping
  - reuse `proc_contrast`, `proc_ramp_center`, normalization, invert
- combine mode
  - math used when this signal references another signal
- signal source A / B
  - optional references to previously defined signals
- preview toggle
  - show signal as grayscale in Base Color temporarily

### New Properties

Recommended additions:

- `signal_combine_mode: EnumProperty`
- `signal_input_a: StringProperty`
- `signal_input_b: StringProperty`
- `signal_use_input_b: BoolProperty`
- `signal_invert: BoolProperty`
- `signal_clamp: BoolProperty`
- `signal_preview: BoolProperty`
- `signal_output_range_min: FloatProperty`
- `signal_output_range_max: FloatProperty`

If we want to keep the first version simpler, we can defer min/max range remap and just keep:

- combine mode
- optional second input
- preview
- invert
- clamp

## Signal Sources

Version 1 should support all procedural sources that already generate a meaningful Fac today.

That includes:

- NOISE
- VORONOI
- CRACKS
- DOTS
- BRICK
- GABOR
- HEX_GRID
- GRADIENT
- MARBLE
- RIDGED
- STRIPES
- WAVE
- FRESNEL
- WHITE_NOISE
- CHECKER

Anything already covered by `_build_proc_fac_node(...)` is a strong candidate.

Important design note:

Signal Layer should operate on the procedural **Fac** output path, not the colorized output path.

That is the whole reason this feature exists.

## Math / Combine Modes

At minimum, expose scalar combine modes that map cleanly to Blender math or mix behavior.

Recommended V1 list:

- MIX
- ADD
- SUBTRACT
- MULTIPLY
- MINIMUM
- MAXIMUM
- SCREEN
- LINEAR_LIGHT
- DIFFERENCE
- DIVIDE

Recommended implementation note:

- for physically stable scalar logic, prefer Math nodes when possible
- for modes that are more naturally represented by Mix nodes, use a dedicated helper
- clamp behavior should be explicit and controllable

If `LINEAR_LIGHT` is too awkward in strict scalar math, it can be deferred to V2.

### V1 Recommendation

To keep the first implementation clean, V1 can ship with:

- MIX
- ADD
- SUBTRACT
- MULTIPLY
- MINIMUM
- MAXIMUM

That already unlocks a lot.

## Referencing Signals from Other Layers

This is the real unlock.

### Mask Integration

Extend mask source enums with something like:

- `LAYER_SIGNAL`

When selected, show a picker for a previously defined SIGNAL layer.

Example:

- Fill `Rock Color`
- `use_mask = True`
- `mask_source = LAYER_SIGNAL`
- `mask_signal_layer = "Rock Coverage"`

That lets visible layers consume reusable signals.

### Bump / Roughness / Alpha Integration

There are two paths.

#### Option A: mask-only integration first

Signals can only be consumed through mask slots.

Pros:

- smallest change
- piggybacks on current layer logic
- already very useful

Cons:

- less direct for bump-only or roughness-only signal routing

#### Option B: direct signal routing

Allow layers to choose signal source for internal bump/roughness shaping.

Pros:

- more powerful

Cons:

- larger surface area

### Recommendation

Ship V1 with **mask integration first**.

That gives us most of the practical benefit while keeping implementation risk much lower.

## UI Design

### Layer Header

Signal layers should look distinct in the stack.

Recommended label:

- `Signal Layer`

Recommended frame color:

- a new neutral-cyan or dark teal distinct from:
  - PROCEDURAL purple
  - FILL orange
  - PAINT blue

### Main Controls

In `panels.py`, the Signal layer panel should contain:

1. `Type`
2. mapping / coords controls
3. procedural-specific controls
4. shaping block
   - contrast
   - center
   - invert
   - clamp
5. combine block
   - Input A
   - optional Input B
   - combine mode
6. preview block
   - `Preview Signal`

### Mask UI

In `_draw_mask_slot(...)`, when `mask_source == 'LAYER_SIGNAL'`, show:

- a searchable dropdown listing prior SIGNAL layers
- optional invert
- levels / blur if desired

### UX Rule: only prior layers

To avoid cyclic graphs, the picker should only list SIGNAL layers with stack index lower than the consuming layer.

That rule should be enforced both in UI and in build logic.

## Compositing Design

### New Builder

Add a dedicated builder in `compositing.py`, conceptually:

- `_build_signal_layer(...)`
- or `_build_signal_fac_node(...)`

This builder should:

1. build the procedural fac source
2. optionally combine it with referenced prior signal(s)
3. apply invert / clamp / shaping
4. register the output socket for later reuse

### Internal Registry

During graph build, maintain a dictionary such as:

- `signal_outputs[layer.name] = socket`

or a stable-id-based map if names are too fragile.

Recommended stronger version:

- store a per-layer UUID / stable key
- use names only for UI labels

But if TLM currently uses names as identity in other places, V1 can start with names while documenting the fragility.

### Build Order

Signal layers should be built in stack order with the rest of the graph.

When a visible layer asks for `mask_source = LAYER_SIGNAL`, resolve the socket from the already-built signal registry.

If not found:

- fail safely to a constant 1.0 or 0.0 depending on context
- log a debug warning
- do not hard-crash the build

## Preview Behavior

Because Signal layers are not visible by default, debugging matters.

Recommended preview behavior:

- if `signal_preview = True`, temporarily route the signal into a grayscale Base Color preview branch
- only one signal preview can be active at a time
- preview should be clearly indicated in UI

Alternative V1 shortcut:

- skip persistent preview property
- add an operator `Preview Active Signal`

That is simpler and may be better if we want to avoid state complexity.

### Recommendation

Use an operator-based preview first.

## Serialization / Presets / I/O

### Presets

In `operators/presets.py`, Signal layers must serialize:

- `layer_type = SIGNAL`
- procedural source properties
- combine properties
- referenced signal names / ids
- preview flag if implemented

### Import / Export

In `operators/io.py`, mirror the same fields.

### Backward Compatibility

No existing preset should break.

Rules:

- unknown `SIGNAL` layer type in older versions is expected to fail gracefully there
- new code must still load all old presets
- signal reference fields should default safely when absent

## Layout / Node Editor Presentation

Signal layers should not add unnecessary width to the visible shading chain.

Recommended layout approach:

- place signal construction lanes above or below visible channels
- keep them grouped on the left, before visible fill/procedural consumers
- keep a narrow width because they do not need Principled routing blocks

This is especially important because one of TLM's current pain points is graph sprawl.

## Performance Considerations

This feature can actually improve performance and readability when used well, because one signal can be reused instead of being rebuilt multiple times.

Potential costs:

- more bookkeeping in compositing
- signal preview logic
- dependency resolution

Potential wins:

- less duplicated procedural logic
- cleaner visible layer chain
- fewer separate noise/voronoi blocks duplicated across channels

### Important Constraint

V1 should avoid general arbitrary graph references.

Only allow:

- references to prior SIGNAL layers
- no forward references
- no circular references
- no nested recursive evaluation

That keeps rebuild logic sane.

## Minimal Viable Version (Recommended)

### V1 Scope

1. new `SIGNAL` layer type
2. SIGNAL reuses current procedural fac generators
3. one combine stage with optional prior signal input
4. mask source `LAYER_SIGNAL`
5. visible layers can consume signals through masks
6. presets / io support
7. no direct displacement integration yet
8. no arbitrary signal graph editor
9. no nested / cyclic references

### What V1 Already Solves

- burnt sand / rock materials
- reusable wear masks
- reusable edge / fresnel / ndotl masks
- shared alpha / roughness / bump breakup
- much cleaner stylized materials

## Future Extensions

### V2

- direct signal-to-bump source
- direct signal-to-roughness source
- signal remap curve widget
- more math modes
- operator to convert a PROCEDURAL layer into SIGNAL + visible Fill consumer

### V3

- signal groups
- cached shared generators
- displacement integration
- advanced signal preview matrix

## Suggested Implementation Order

1. `properties.py`
   - add `SIGNAL` layer type
   - add signal properties
   - add `LAYER_SIGNAL` mask source
2. `panels.py`
   - draw new Signal layer UI
   - draw signal picker in mask UI
3. `compositing.py`
   - build signal fac nodes
   - store outputs in signal registry
   - resolve signal masks from registry
4. `operators/io.py`
   - import/export support
5. `operators/presets.py`
   - save/load support
6. optional preview operator
7. one built-in demo preset proving the feature

## Recommended Demo Presets After Implementation

- Burnt Sand Rock
- Layered Rust Coverage
- Toon Shadow Bands
- Holographic Rim Decal

## Why This Is the Right Next Feature

This is not a cosmetic addition.

It fills a real structural gap between:

- current visible-layer workflows
- and more advanced node graphs built from reusable scalar logic

It lets TLM stay approachable while becoming capable of reconstructing a much wider class of procedural materials with fidelity rather than approximation alone.

In short:

- WIREFRAME solved a missing topological mask
- SIGNAL would solve a missing procedural logic stage

That is exactly the missing piece surfaced by the burnt sand / rock reverse-engineering test.
