# Texture Layer Manager - Material Study

Goal: design a small set of commercial-grade showcase materials before writing
more presets. This document studies what the materials should prove, how they
should be built with the current addon, and where the first pass felt weak.

No code changes are proposed here. This is an art-direction and material
authoring plan for future TLM presets.

## Sources used

- Blender Manual, material and shader-node model:
  https://docs.blender.org/manual/en/latest/render/materials/introduction.html
- Blender Manual, Principled BSDF / OpenPBR-style layer model:
  https://docs.blender.org/manual/en/4.4/render/shader_nodes/shader/principled.html
- Blender Manual, procedural texture nodes:
  https://docs.blender.org/manual/en/latest/render/shader_nodes/textures/index.html
- Blender Manual, Bump node:
  https://docs.blender.org/manual/en/4.5/render/shader_nodes/vector/bump.html
- Adobe / Substance PBR Guide, Part 1 and Part 2:
  https://www.adobe.com/learn/substance-3d-designer/web/the-pbr-guide-part-1
  https://www.adobe.com/us/learn/substance-3d-designer/web/the-pbr-guide-part-2
- Adobe Substance Painter masking, effects, generators:
  https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/interface/layer-stack/masking-and-effects
  https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/effects/generator
- Adobe Standard Material / Sampler channel reference:
  https://experienceleague.adobe.com/en/docs/substance-3d-sampler/using/features-and-workflows/adobe-standard-material
- OpenPBR / MaterialX context:
  https://www.aswf.io/blog/academy-software-foundation-announces-openpbr-a-new-subproject-of-materialx/
- Public benchmark libraries for quality expectations:
  https://polyhaven.com/
  https://ambientcg.com/

## Why the first showcase pass was weak

The first materials were useful as addon demos, but not strong enough as
commercial material studies.

Main problems:

- Too many layers behaved like color decoration instead of physically motivated
  material states.
- The materials did not have clear reference targets: "rust", "wood", "crystal"
  existed as ideas, but not as studied surfaces with a real-world signature.
- Roughness was often a constant per layer instead of the main storytelling map.
- Metallic values were not always treated as material identity masks.
- Bump was used for texture energy, but not always with convincing scale.
- Wear, dirt, patina, scratches and edge effects were not placed by a believable
  logic.
- Some presets showed procedural variety, but did not clearly show why TLM is a
  powerful layer manager rather than just another procedural shader stack.

The next materials should feel like "look-dev studies", not examples.

## Core PBR rules to follow

### Base color

Base color should describe reflected material color, not baked lighting. For
dielectrics, avoid crushed blacks and blown-out whites. The Substance PBR guide
recommends keeping dark dielectric values above roughly 30-50 sRGB and bright
dielectric values below about 240 sRGB.

Implication for TLM:

- Use darker fills for visual taste, but do not make dirt pure black.
- Do not fake ambient occlusion heavily in base color.
- Add micro-occlusion only when it helps small surface detail.

### Metallic

Metallic is mostly a material identity mask:

- 0.0 = dielectric: paint, rust, dirt, stone, ceramic, wood, fabric, plastic.
- 1.0 = raw exposed metal.
- Gray values are acceptable mainly for thin transitions, dust, grime, or mixed
  coverage, not as a generic "semi-metal" look.

Implication for TLM:

- Painted metal should have metallic 0 on paint and rust, metallic 1 only where
  exposed metal appears.
- Oxidized metal/patina should usually be metallic 0 where oxidation covers the
  surface.
- If a layer changes raw metal into dirt/oxidation, it must also lower metallic.

### Roughness

Roughness is the strongest visual storytelling channel. It describes handling,
age, polishing, dust, oil, scratches and weather exposure.

Implication for TLM:

- Every hero material needs a roughness story.
- The roughness layer should often reuse the same mask as color/wear, but with a
  different blend behavior.
- Smooth surfaces still need small roughness variation to catch highlights.
- Dirt, oxide, fabric and stone trend rough; polished lacquer, marble and exposed
  metal trend smoother, but never perfectly uniform.

### Normal, bump and height

Blender's Bump node perturbs normals from a height signal. It is excellent for
procedural preview and shader detail, but it is not true geometric displacement.

Implication for TLM:

- Keep bump distances material-scale-aware.
- Use separate macro, mid and micro bump layers instead of one noisy bump layer.
- Avoid high bump on polished materials unless the surface is actually carved,
  chipped or cracked.
- Scratches should be shallow and narrow; cracks can be deeper.

### Masking and generators

Substance's layer workflow is powerful because masks and generators are reusable
and editable. TLM already has the ingredients for a similar story: procedural
layers, masks, reference layers, blend overrides and Fresnel.

Implication for TLM:

- Build "mask driver" layers and reuse them through REFERENCE layers.
- One mask should drive color, roughness, metallic and bump in related but not
  identical ways.
- Smart material behavior should be visible: dirt in cavities, wear on exposed
  surfaces, edge/rim effects via Fresnel when appropriate.

### Scale

Procedural materials fail quickly when scale is wrong. A marble vein, a weave, a
scratch and a lava crack should not live at the same frequency.

Implication for TLM:

- Each preset should define macro, mid and micro scales explicitly.
- Prefer OBJECT coordinates for general procedural consistency.
- Use UV only when a material must follow unwraps.
- Use GENERATED only when object-bounding-box behavior is intentional.

## Current TLM capability map

Strong current features:

- Layer types: FILL, PAINT, PROCEDURAL, ADJUSTMENT, GROUP, REFERENCE.
- Main channels: Base Color, Roughness, Metallic, Alpha.
- Additional per-layer channels: Normal, Emission, Transmission, Bump.
- Per-channel blend overrides for base color, roughness, metallic, emission,
  transmission and alpha.
- Procedurals: Noise, Voronoi, Wave, Marble, Musgrave, Brick, Checker, Gradient,
  Magic, Stripes, Hex Grid, White Noise.
- Coordinates: Generated, Object, UV.
- Coordinate transforms: Polar, Spherical, Swirl, Cylindrical.
- Fresnel mask support for rim/glancing effects.
- Reference layer workflow, useful for reusing a procedural pattern as a mask.

Current limitations to respect:

- No direct coat/clearcoat channel in the TLM layer model.
- No direct sheen/fuzz channel.
- No direct anisotropy channel.
- No true displacement output as a first-class layer target.
- No baked curvature/ambient-occlusion/cavity generator yet.
- No direct Gabor procedural exposed in the current procedural list, so brushed
  metal and woven fibers need to be approximated with Wave/Stripes/Noise.

Commercial implication:

- Do not choose hero materials whose beauty depends mainly on coat, sheen,
  anisotropy, curvature baking or displacement.
- Choose materials that look excellent with base color, roughness, metallic,
  bump, masks, emission, alpha/transmission and Fresnel.

## Commercial quality rubric

Each future preset should score well on these points before it ships.

1. Thumbnail readability
   The material must be identifiable from a small preview sphere.

2. PBR plausibility
   Metallic, roughness, color and bump must agree with the material state.

3. Layer-story clarity
   Opening the stack should teach the user something: base, wear, grime,
   scratches, glow, veins, weave, chips.

4. Addon showcase value
   The preset should show at least two TLM-specific strengths, such as reference
   layers, per-channel blend overrides, Fresnel masking, emission thresholding,
   coordinate transforms, or multi-channel procedural wear.

5. Art direction
   The material should feel intentional, not generic procedural noise.

6. Customization
   A buyer should be able to tweak colors, scale, wear strength and roughness
   without breaking the look.

7. Performance sanity
   Use enough layers to impress, not so many that material rebuilds and viewport
   preview become frustrating.

Suggested target: 7-10 layers per hero material, with 1-2 reusable mask drivers.

## Stack grammar for future presets

Recommended layer order:

1. Material base
   FILL layer. Sets physical identity: color, metallic, roughness.

2. Macro variation
   Procedural layer. Big color/roughness variation at object scale.

3. Primary material structure
   Procedural layer. Veins, grain, weave, panels, cracks, tiles or patina.

4. Wear/damage mask driver
   Procedural layer, often low visible color contribution. It should be reused
   by REFERENCE layers to drive channels consistently.

5. Channel-specific references
   REFERENCE layers that reuse the driver pattern for metallic exposure,
   roughness shifts, dirt, edge chips, glow, alpha, or bump.

6. Micro detail
   Noise/White Noise/Wave layer. Fine bump and roughness breakup.

7. Final color correction
   Adjustment layer only if needed.

Naming convention:

- `Base - <material>`
- `Macro - <variation>`
- `Structure - <signature pattern>`
- `Mask - <wear/dirt/veins/cracks>`
- `Channel - Roughness <reason>`
- `Channel - Metallic <reason>`
- `Detail - Micro <surface>`
- `FX - Emission/Fresnel/Alpha`

## Hero material studies

### 1. Painted Industrial Metal

Commercial purpose:

Show the addon as a smart material stack: paint, chips, primer, raw metal,
scratches, dirt and roughness all reacting together.

Visual target:

Weathered machine casing, industrial door, equipment panel, old painted metal.
The signature is not "rust everywhere"; it is layered material history.

Observed traits:

- Paint is dielectric, often medium roughness.
- Primer is also dielectric, usually duller and warmer/darker.
- Exposed raw metal is metallic 1 and smoother on fresh scratches.
- Rust is dielectric, rough, orange/brown, often raised.
- Dirt gathers in recess-like procedural pockets and around chips.
- Scratches are directional or localized; they should not cover the object
  evenly.

TLM recipe:

1. `Base - Painted Metal`
   FILL. Desaturated paint color, metallic 0, roughness 0.45-0.65.

2. `Macro - Faded Paint`
   NOISE or MUSGRAVE, low scale, soft contrast. Slight color variation and
   roughness 0.55-0.75.

3. `Mask - Chipped Paint`
   VORONOI distance-to-edge or NOISE with high contrast. This becomes the main
   wear mask.

4. `Channel - Exposed Steel`
   REFERENCE to chipped mask. Base color cool gray, metallic 1, roughness
   0.22-0.38, shallow bump on chip border.

5. `Channel - Primer Undercoat`
   REFERENCE to a softer/inverted chip mask. Reddish or gray primer, metallic 0,
   roughness 0.65-0.80.

6. `Damage - Rust Bloom`
   NOISE/MUSGRAVE. Rust color, metallic 0, roughness 0.85-0.98, bump distance
   0.015-0.05.

7. `Detail - Fine Scratches`
   STRIPES/WAVE plus Noise. Very low opacity, roughness contrast, shallow bump.

8. `Detail - Dust`
   WHITE_NOISE/NOISE. Light color, metallic 0, roughness high, opacity low.

TLM strengths shown:

- Metallic changed only where paint is gone.
- Same chip mask reused across color, metallic, roughness and bump.
- Per-channel blend overrides can make chips brighten color but mix metallic.

Failure modes:

- If rust is metallic, the material looks physically wrong.
- If chip mask is too uniform, it looks like camouflage.
- If exposed metal is too smooth everywhere, it looks like chrome.

Preview scene:

Sphere plus bevelled cube/panel. Strong area light so roughness shifts are
obvious. A close-up thumbnail should show paint edge, primer and metal.

### 2. Oxidized Bronze / Ancient Patina

Commercial purpose:

Show multi-state metal: raw bronze, oxidation, polished contact zones and
green-blue patina.

Visual target:

Old statue, antique hardware, ritual object, aged ornament.

Observed traits:

- Raw bronze/copper alloy is metallic, warm, reflective.
- Patina/verdigris is an oxidation layer: dielectric, rough, green/blue.
- Polished raised zones become warmer and smoother from handling.
- Dark grime collects between patina islands.

TLM recipe:

1. `Base - Warm Bronze`
   FILL. Warm brown/orange metal color, metallic 1, roughness 0.28-0.45.

2. `Macro - Cast Metal Variation`
   NOISE. Slight color and roughness variation.

3. `Mask - Patina Islands`
   VORONOI or MUSGRAVE, medium-large scale, organic distortion.

4. `Layer - Verdigris Patina`
   REFERENCE to patina mask. Green/teal color, metallic 0, roughness 0.85-0.95,
   bump 0.01-0.035.

5. `Layer - Dark Oxide`
   NOISE/MUSGRAVE, multiply. Very dark brown/green, metallic 0, roughness high.

6. `FX - Polished Rim`
   Fresnel or gradient-driven highlight. Warm bronze, metallic 1, roughness
   0.18-0.28.

7. `Detail - Pitted Surface`
   NOISE or VORONOI fine. Small bump, roughness breakup.

TLM strengths shown:

- Fresnel mask for glancing polished edges.
- Patina mask reused to lower metallic and raise roughness.
- Organic procedural oxidation without image textures.

Failure modes:

- Patina too saturated becomes fantasy slime.
- Too much Fresnel makes the bronze look emissive or plastic.
- Uniform green coverage hides the metal story.

Preview scene:

Sculptural bust, ornate knob, or torus/monkey head. Bronze needs curved forms to
show rim and patina breakup.

### 3. Black Marble With Gold Veins

Commercial purpose:

Show luxury material: controlled veins, polished roughness, subtle depth and a
premium render.

Visual target:

High-end black marble with sparse white/gray veins and optional metallic gold
inlays.

Observed traits:

- Marble is dielectric, not metallic.
- It is usually polished: low roughness, but with micro variation.
- Veins follow directional flow; they should not look like random spiderwebs.
- Gold inlay, if present, is a separate material state and metallic.

TLM recipe:

1. `Base - Polished Black Stone`
   FILL. Dark charcoal, metallic 0, roughness 0.08-0.18.

2. `Macro - Stone Depth`
   NOISE/MUSGRAVE. Very subtle dark gray variation, roughness 0.12-0.22.

3. `Structure - Marble Veins`
   MARBLE or WAVE with distortion. White/gray veins, narrow, directional.

4. `Reference - Vein Roughness`
   Same vein pattern, roughness slightly higher on white stone veins.

5. `Optional - Gold Inlay`
   REFERENCE to a subset of vein mask. Metallic 1, warm gold color, roughness
   0.18-0.28, very shallow bump.

6. `Detail - Polishing Micro Scratches`
   STRIPES/WAVE or WHITE_NOISE at low opacity. Roughness variation only, very
   subtle.

TLM strengths shown:

- One vein pattern can drive color, roughness and optional metallic inlay.
- Per-channel override can keep veins visible in color but restrained in bump.
- The material shows restraint, which helps commercial perception.

Failure modes:

- Too much bump makes marble look like cracked rock.
- Too many veins destroys luxury feel.
- Gold veins everywhere look cheap; sparse is better.

Preview scene:

Sphere and slab/column. Use long rectangular object too, because marble vein
direction matters.

### 4. Lacquered Burl Wood

Commercial purpose:

Show organic layered material and a simulated clear topcoat using current
roughness controls.

Visual target:

Polished walnut/burl wood, instrument finish, luxury furniture.

Observed traits:

- Wood is dielectric.
- Grain is directional and layered, not generic noise.
- Burl has knots, waves, swirls and local color changes.
- Lacquer creates a smooth top layer, with fine scratches and dust.
- Pores/grain can be darker and slightly raised/indented.

TLM recipe:

1. `Base - Warm Wood`
   FILL. Warm brown, metallic 0, roughness 0.32-0.48.

2. `Structure - Long Grain`
   WAVE bands with distortion. Directional grain, color variation.

3. `Structure - Burl Swirl`
   SWIRL coordinate transform plus NOISE/WAVE. Local knot-like movement.

4. `Layer - Dark Pores`
   NOISE/WAVE, multiply. Dark thin features, roughness slightly higher, shallow
   bump.

5. `Layer - Amber Lacquer`
   FILL or soft gradient. Low roughness 0.12-0.22, color warming, low opacity.

6. `Detail - Clearcoat Scratches`
   STRIPES/WHITE_NOISE. Roughness-only, shallow/no bump.

7. `Detail - Dust Specks`
   WHITE_NOISE. Very low opacity, roughness high.

TLM strengths shown:

- Coordinate transforms for organic swirl.
- Separate visual grain and clearcoat roughness simulation.
- Roughness tells lacquer condition.

Failure modes:

- If wave scale is wrong, it reads as zebra stripes.
- Too much contrast makes wood cartoony.
- Heavy bump on lacquer destroys polished finish.

Preview scene:

Curved furniture-like shape, guitar-pick silhouette, bevelled box or cylinder.

### 5. Tactical Woven Fabric

Commercial purpose:

Show non-metal, high-roughness, micro-pattern material with a strong surface
signature.

Visual target:

Ballistic nylon, backpack fabric, tactical strap, coarse woven textile.

Observed traits:

- Fabric is dielectric, metallic 0.
- High roughness, broad soft highlights.
- The weave is the primary signature.
- Fibers and lint appear at micro scale.
- Directionality matters; it should not be isotropic noise.

TLM recipe:

1. `Base - Dyed Fabric`
   FILL. Olive/black/tan, metallic 0, roughness 0.82-0.96.

2. `Structure - Warp Threads`
   STRIPES or WAVE in one direction. Slight color change, bump shallow.

3. `Structure - Weft Threads`
   STRIPES/WAVE perpendicular via coordinate offset/rotation. Slight alternate
   color, bump shallow.

4. `Layer - Thread Interlock`
   CHECKER/HEX/NOISE to break perfect regularity.

5. `Detail - Fiber Fuzz`
   WHITE_NOISE/NOISE. Light speckles, high roughness, tiny bump.

6. `Layer - Worn High Spots`
   Fresnel or gradient-based light wear. Lower saturation, roughness shift.

7. `Layer - Dirt`
   NOISE/MUSGRAVE. Muted brown/gray, roughness high.

TLM strengths shown:

- Multi-procedural pattern composition.
- Directional surface built without external textures.
- Bump and roughness coordinated at multiple scales.

Failure modes:

- If stripes are too clean, it looks like a graphic pattern.
- If bump is too strong, it becomes basket or rope.
- If roughness is low, it looks like plastic.

Preview scene:

Strap, folded plane, cylinder with cloth band. A sphere alone is not enough for
weave direction.

### 6. Sci-Fi Emissive Hull

Commercial purpose:

Show TLM's procedural, emission and mask-routing strengths in a visually
marketable material.

Visual target:

Dark hard-surface armor/hull with panel variation, dirt, edge wear and glowing
technical seams.

Observed traits:

- The base can be painted metal or dark anodized metal.
- Panels should look designed, not random cells.
- Emission should be sparse and intentional.
- Roughness variation is critical to avoid flat dark material.
- Edge/rim glow can be stylized, but should not replace real material response.

TLM recipe:

1. `Base - Dark Hull`
   FILL. Dark graphite, metallic 0.6-1 depending target, roughness 0.35-0.55.

2. `Structure - Panel Blocks`
   VORONOI/HEX_GRID with low randomness or STRIPES layers. Subtle base color and
   roughness differences.

3. `Mask - Panel Seams`
   HEX_GRID/VORONOI distance-to-edge. Dark lines, raised/lowered bump.

4. `Layer - Edge Wear`
   Fresnel or high-contrast mask. Slight exposed metal, roughness lower.

5. `Layer - Dirt in Panels`
   NOISE/MUSGRAVE. Multiply dark, roughness high.

6. `FX - Emissive Seams`
   REFERENCE to selected seam/panel mask. Emission color cyan/orange/green,
   emission strength 1.5-5.0, narrow threshold.

7. `Detail - Micro Scratches`
   STRIPES/NOISE. Low opacity, roughness variation.

TLM strengths shown:

- Emission threshold and mask reuse.
- Per-channel behavior: seams can darken color, bump normals, and emit only in
  selected areas.
- Shows addon power quickly in screenshots.

Failure modes:

- Too much emission makes it a neon material, not hull material.
- Random Voronoi panels can look organic instead of manufactured.
- If everything is dark, buyers cannot read the layer work.

Preview scene:

Bevelled hard-surface block, sci-fi crate, or panel wall. Include at least one
flat surface for panel readability.

### 7. Alien Energy Crystal

Commercial purpose:

Show transmission/alpha/emission/Fresnel interactions and a more fantastical
material that still has a controlled stack.

Visual target:

Semi-transparent crystal with internal veins, glowing cores and rim response.

Observed traits:

- Crystal/glass is dielectric with transmission/alpha behavior.
- Realistic crystal is not uniformly transparent; internal inclusions matter.
- Edges catch light strongly.
- Emission can be stylized, but should appear internal or vein-based.

TLM recipe:

1. `Base - Tinted Crystal`
   FILL. Saturated but not neon color, metallic 0, roughness 0.02-0.15,
   transmission 0.3-0.8 or alpha 0.35-0.75 depending target.

2. `Structure - Internal Veins`
   MARBLE/WAVE with spherical/object coords. Slight color variation.

3. `FX - Core Glow`
   GRADIENT spherical or NOISE mask. Emission low-to-medium, soft falloff.

4. `FX - Edge Energy`
   Fresnel mask. Emission or brighter base color at edges, controlled strength.

5. `Detail - Facet Imperfections`
   VORONOI/NOISE. Low bump/roughness variation.

6. `Layer - Cloudy Inclusions`
   NOISE/MUSGRAVE. Pale internal-looking patches, low opacity.

TLM strengths shown:

- Fresnel mask and coordinate transforms.
- Emission and transparency/transmission in one material.
- Strong before/after if layers are toggled.

Failure modes:

- Too much alpha can make it disappear in viewport/render settings.
- Too much emission hides PBR response.
- No geometry facets means the material may look like colored glass, not
  crystal.

Preview scene:

Faceted mesh, shard, or ico sphere. A smooth sphere alone is not ideal.

### 8. Cracked Lava / Volcanic Rock

Commercial purpose:

Show contrast between rough dielectric rock and emissive cracks.

Visual target:

Cooling lava rock: matte black crust, red/orange cracks, heat glow, ash.

Observed traits:

- Rock crust is dielectric, very rough.
- Hot cracks are emissive and recessed-looking.
- Crack edges can be brighter/darker and rough.
- Surface should have porous micro detail.

TLM recipe:

1. `Base - Basalt Crust`
   FILL. Charcoal/black-brown, metallic 0, roughness 0.9-1.0.

2. `Structure - Crust Plates`
   VORONOI distance-to-edge or HEX_GRID distorted. Large crack network.

3. `FX - Lava Cracks`
   REFERENCE to crack mask. Orange/red emission, strength 2-8, base color hot.

4. `Layer - Heated Edge`
   REFERENCE to softer crack mask. Dark red/brown near cracks, roughness 0.7-0.9.

5. `Detail - Porous Rock`
   NOISE/MUSGRAVE. Strong roughness, bump 0.03-0.08.

6. `Layer - Ash Dust`
   WHITE_NOISE/NOISE. Gray dust, high roughness.

TLM strengths shown:

- Emission masks and roughness/bump around the same cracks.
- Procedural cracks without image textures.
- Clear channel contrast.

Failure modes:

- Crack pattern too regular becomes tile/honeycomb.
- Emission too wide makes the material read as molten surface, not cracked
  crust.
- Excessive bump can make lava look like foam.

Preview scene:

Ground plane plus sphere. Cracks read well on flat surfaces and curved previews.

### 9. Ceramic Mosaic Tile

Commercial purpose:

Show tiling, mortar, gloss contrast and color variation. This is useful for
archviz buyers.

Visual target:

Handmade glazed ceramic tiles with grout, imperfect color, chipped edges and
subtle dirt.

Observed traits:

- Ceramic is dielectric.
- Glazed tile is smoother than grout.
- Grout is rough, porous and lighter/darker depending dirt.
- Handmade tiles have small color and height variation.
- Cracks/chips expose rougher ceramic body.

TLM recipe:

1. `Base - Tile Color`
   FILL. Ceramic/glaze color, metallic 0, roughness 0.18-0.35.

2. `Structure - Tile Grid`
   BRICK/CHECKER/HEX_GRID. Mortar color, high roughness, small bump.

3. `Layer - Per Tile Variation`
   VORONOI random per cell if available. Subtle color shifts.

4. `Layer - Grout Dirt`
   REFERENCE to mortar/grid mask plus noise. Roughness high, darkening.

5. `Layer - Glaze Variation`
   NOISE low opacity. Roughness variation 0.12-0.28.

6. `Damage - Chipped Edges`
   Noise/Voronoi near grid lines. Rough ceramic body color, roughness high,
   bump small.

7. `Detail - Hairline Cracks`
   STRIPES/WAVE/NOISE with high contrast. Thin dark lines, very shallow bump.

TLM strengths shown:

- Procedural brick/grid controls.
- Different roughness for tile and grout.
- Good example of architectural material with customization.

Failure modes:

- If tile grid is perfectly sharp and uniform, it feels CG.
- If glaze is too smooth, it looks like plastic.
- Too many cracks make it horror/ruin instead of saleable archviz.

Preview scene:

Flat wall/floor patch with grazing light. Sphere preview is secondary.

### 10. Weathered Concrete

Commercial purpose:

Provide a useful, grounded material that shows subtle procedural layering rather
than spectacle.

Visual target:

Architectural concrete with aggregate, stains, pores, edge wear and dust.

Observed traits:

- Concrete is dielectric, rough, low saturation.
- Macro color variation is broad and soft.
- Pores and aggregate appear at mid/micro scale.
- Water stains and dirt have vertical/gravity logic if possible.
- Roughness is high but not perfectly uniform.

TLM recipe:

1. `Base - Concrete`
   FILL. Mid gray/warm gray, metallic 0, roughness 0.78-0.95.

2. `Macro - Cement Clouding`
   NOISE/MUSGRAVE low scale. Subtle color patches.

3. `Structure - Aggregate`
   VORONOI/NOISE. Small flecks, mild color difference, bump.

4. `Detail - Pores`
   WHITE_NOISE/VORONOI fine. Small dark pits, bump/roughness.

5. `Layer - Water Stains`
   GRADIENT plus NOISE if possible. Darker vertical staining, roughness shift.

6. `Layer - Dust/Chalk`
   NOISE, light gray, high roughness, low opacity.

TLM strengths shown:

- Subtle, production-useful procedural material.
- Multi-scale texture discipline.
- Good contrast to flashy sci-fi/crystal materials.

Failure modes:

- Too much contrast becomes stone/camouflage.
- Too much bump makes it gravel.
- Too flat looks like default gray material.

Preview scene:

Large plane/wall plus bevelled cube. Concrete needs scale context.

## Recommended first commercial pack

Do not ship too many materials first. A focused pack is easier to polish and
market.

Recommended "Hero 6" pack:

1. Painted Industrial Metal
2. Oxidized Bronze / Ancient Patina
3. Black Marble With Gold Veins
4. Lacquered Burl Wood
5. Tactical Woven Fabric
6. Sci-Fi Emissive Hull

Optional "Spectacle 2" add-ons:

7. Alien Energy Crystal
8. Cracked Lava / Volcanic Rock

Optional "Utility 2" add-ons:

9. Ceramic Mosaic Tile
10. Weathered Concrete

Rationale:

- The Hero 6 cover metal, stone, organic, fabric and sci-fi.
- They demonstrate most of TLM's best features without relying on missing
  shader channels.
- They provide strong screenshot variety for a product page.
- Spectacle materials attract attention, while utility materials increase buyer
  trust.

## Feature gaps discovered by the study

These are not required before making better presets, but they would improve the
addon later.

High-value future features:

- Coat / clearcoat channel support for lacquer, car paint, glazed ceramic and
  polished marble.
- Sheen / fuzz support for fabric, dust and velvet-like materials.
- Anisotropy support for brushed metal, hairline scratches and satin finishes.
- Height/displacement output separate from bump.
- Curvature / AO / cavity smart masks, or a bake-assisted generator workflow.
- Gabor texture or directional noise procedural for fibers, scratches and
  brushed surfaces.
- Dedicated mask preview/debug output.
- Material thumbnail render automation for preset QA.

## QA checklist before writing a preset

For each material:

- Can the material be identified from a thumbnail?
- Are metallic values physically plausible?
- Does roughness tell the surface history?
- Is bump scale believable?
- Are macro, mid and micro details separated?
- Does at least one mask drive multiple channels?
- Does toggling layers reveal a clear material-building story?
- Does the material look acceptable on a sphere and on a domain-relevant object?
- Does it avoid relying on unavailable TLM channels?
- Is the preset easy to tweak without destroying the look?

## Next execution plan

1. Build one material only: Painted Industrial Metal.
2. Use it as the quality benchmark for every later material.
3. Make a preview scene with sphere, bevelled cube and panel.
4. Iterate until the material looks sellable in thumbnail and close-up.
5. Then build Oxidized Bronze and Black Marble.
6. Only after three strong materials, decide whether to continue with the rest
   or improve missing addon features first.

Recommended first implementation target:

`Showcase Pro - Painted Industrial Metal.tlm`

Success criteria:

- 8-10 layers.
- At least one reusable wear mask.
- Paint, primer, exposed metal, rust and scratches all separated.
- Metallic is 1 only on exposed metal.
- Rust and dirt are rough and dielectric.
- Fine scratches affect roughness more than color.
- The material looks good on a product page screenshot without explanation.
