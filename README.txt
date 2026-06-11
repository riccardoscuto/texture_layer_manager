TEXTURE LAYER MANAGER 1.0.0
Layer-based material authoring for Blender
============================================

Stack layers, get clean Cycles node setups. Paint, fill, procedural,
adjustment, group and reference layers; per-channel routing to Base
Color, Roughness, Metallic, Emission, Transmission and Alpha; masks,
blend modes, presets. Everything stays editable.


REQUIREMENTS
------------
- Blender 5.0 or newer (tested on 5.0 and 5.1)
- Cycles for full feature coverage. EEVEE handles the core channels;
  emission-routed layers and volume shading render best in Cycles.
  Details in the manual.


INSTALL
-------
1. Blender > Edit > Preferences > Add-ons.
2. Top-right arrow menu > "Install from Disk...".
3. Pick TextureLayerManager_v1.0.0.zip.
4. Enable "Texture Layer Manager" in the list.

The panel appears in Properties > Material > Texture Layers.


UPDATING
--------
Remove the old version in Preferences > Add-ons, restart Blender,
install the new zip.


FIRST MATERIAL IN 60 SECONDS
----------------------------
1. Select an object and give it a material.
2. Open Properties > Material > Texture Layers.
3. Add a Fill layer for the base colour, then a Procedural layer on
   top and pick a pattern. Drop its opacity or add a mask.
4. Or skip all that: open Presets and apply one of the 13 included
   materials, then open its stack to see how it is built.

The full manual (PDF) ships next to this file.


SUPPORT
-------
Questions and bug reports: reply to your Gumroad receipt or message
through the Gumroad product page. Include your Blender version and,
if possible, the .blend or a screenshot of the stack.
