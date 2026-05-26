# Texture Layer Manager - Program Analysis Log

Data: 2026-05-21  
Branch analizzato: `main`  
Ultimo commit noto: `cfb5281 Fix layer compositing and procedural controls`  
Addon: `Texture Layer Manager`  
Versione dichiarata: `0.5.0`  
Target Blender dichiarato: `4.0+`, sviluppo/test recente su Blender `5.0`

## 1. Sintesi

Texture Layer Manager e' un addon Blender che implementa un sistema di layer
non distruttivo per materiali PBR. L'utente lavora in una UI tipo stack:
Paint, Fill, Procedural, Adjustment, Group e Reference. Il programma converte
questa struttura in una node tree shader Blender, collegando i risultati ai
canali del `Principled BSDF`.

Il cuore tecnico e' `compositing.py`: costruisce, aggiorna e organizza i nodi.
Il cuore dati e' `properties.py`: definisce tutte le proprieta salvate nel
materiale. La UI vive in `panels.py`, mentre `operators/` contiene le azioni
utente: aggiunta layer, paint, import/export, bake, preset, visibilita, gruppi,
conversione in shader editabile.

Il programma e' gia molto potente. Le aree piu delicate sono:

- dimensione e complessita di `compositing.py`;
- coerenza fra data model, UI, serializzazione preset e builder nodi;
- gestione di Paint e alpha in Blender 5.0;
- prestazioni su stack grandi;
- mantenimento dei procedural nuovi;
- test automatizzati ancora limitati rispetto alla superficie funzionale.

## 2. Stato repository

File principali tracciati:

| File | Ruolo | Linee circa |
| --- | --- | ---: |
| `compositing.py` | Builder shader, hot update, mask, procedural, alpha, layout nodi | 6107 |
| `properties.py` | Data model Blender PropertyGroup | 1917 |
| `panels.py` | UI Properties panel e Viewport panel | 1244 |
| `operators/presets.py` | Preset built-in e salvataggio preset | 963 |
| `operators/io.py` | Import/export `.tlm`, serializzazione immagini | 714 |
| `tlm_test_scene.py` | Script scena/test manuale | 614 |
| `operators/bake.py` | Bake/export PBR | 491 |
| `operators/_common.py` | Helper comuni, bake guard, add layer comune | 420 |
| `operators/layers.py` | Add/remove/move/duplicate layer | 324 |
| `operators/importing.py` | Import texture e PBR set | 315 |
| `operators/masks.py` | Mask image e smart mask | 240 |
| `operators/compositing_ops.py` | Rebuild, flatten, convert/editable shader | 148 |
| `previews.py` | Icone/swatch preview layer | 142 |
| `operators/channel_pack.py` | Channel packing | 135 |
| `operators/groups.py` | Group layer operations | 112 |
| `profile_tlm.py` | Profiling helper | 111 |
| `operators/pbr.py` | Add/remove PBR channel images | 98 |
| `operators/visibility.py` | Visible/solo/active paint | 95 |
| `test_update_tag.py` | Test/tag helper | 75 |
| `__init__.py` | Entry point addon | 62 |
| `utils.py` | Utility base immagini/UV/materiali | 55 |
| `operators/__init__.py` | Registry operatori | 53 |
| `operators/keyframes.py` | Keyframe opacity | 41 |
| `operators/thumbnails.py` | Refresh thumbnail | 21 |

File locali non tracciati rilevati in `preset_dev/`:

- `_preview_scene.py`
- `carbon_fiber_3k.py`
- `engraved_rune_tablet.py`
- `promo_carbon_clearcoat_minimal.py`
- `promo_worn_metal_starter.py`
- `reverse_scifi_panel.py`
- `reverse_yellow_rust_paint.py`

Questi sembrano script sperimentali/promozionali e non fanno parte del core
pushato.

## 3. Entry point addon

File: `__init__.py`

Responsabilita:

- dichiara `bl_info`;
- importa i moduli principali:
  - `properties`
  - `previews`
  - `operators`
  - `panels`
  - `compositing`
- registra i moduli in ordine;
- sottoscrive `bpy.msgbus` su `bpy.types.Image.pixels`;
- invalida le preview con debounce quando cambiano i pixel delle immagini.

Flusso:

1. Blender carica addon.
2. `register()` stampa versione e path effettivo, utile contro cache o install duplicati.
3. Registra PropertyGroup, operatori, pannelli, compositing.
4. Attiva msgbus immagini.

Nota tecnica:

Il debounce `_invalidate_pending` evita che ogni sample del pennello in Texture
Paint scateni refresh pesanti della UI.

## 4. Data model

File: `properties.py`

### 4.1 TLM_MaterialProperties

Ogni materiale ha `mat.tlm`, che contiene:

- `layers`: collection di `TLM_LayerItem`;
- `active_layer_index`;
- `auto_composite`: ricostruzione automatica node tree;
- `shader_editable`: blocca lo stack quando il materiale e' convertito in shader Blender editabile;
- `use_base_color_alpha`;
- `alpha_blend_method`;
- `resolution` per nuovi paint layer;
- `uv_map`;
- impostazioni UI collapsible;
- metrica performance debug;
- impostazioni bake/export/preset.

`shader_editable` e' centrale: quando e' true, l'addon smette di rigenerare il
grafo, cosi l'utente puo modificare i nodi manualmente senza che TLM li perda.

### 4.2 TLM_LayerItem

Ogni layer contiene:

- nome;
- tipo layer;
- visibilita;
- lock;
- colore tag;
- opacity;
- blend mode principale;
- blend mode per canale;
- routing `output_channel`;
- dati paint/fill/procedural/adjustment/reference;
- PBR extra channels;
- mask A e mask B;
- clipping mask;
- mapping immagine;
- fresnel;
- normal/bump;
- emission/transmission/alpha.

Tipi layer:

- `PAINT`
- `FILL`
- `ADJUSTMENT`
- `GROUP`
- `PROCEDURAL`
- `REFERENCE`

Output routing principale:

- `BASE_COLOR`
- `ROUGHNESS`
- `METALLIC`
- `ALPHA`

Canali addizionali tramite toggle:

- roughness
- metallic
- normal
- emission
- transmission
- alpha
- bump

### 4.3 Update strategy

Ci sono due tipi di aggiornamento:

1. Rebuild strutturale via `_on_layer_update`.
2. Hot update mirato via `_make_hot_callback`.

Le property che cambiano solo un valore di socket provano hot update. Se il
grafo esistente non ha la topologia giusta, `hot_update_property()` restituisce
false e parte rebuild.

Il rebuild e' debounced con timer a 0.25s:

- riduce rebuild multipli durante drag slider;
- conserva reattivita sufficiente;
- limita stutter su stack grandi.

## 5. UI

File: `panels.py`

La UI principale vive nel Material Properties panel.

Componenti:

- `TLM_UL_LayerList`: lista layer con icone, stato visible/solo/lock.
- `draw_tlm_main`: header materiale, add row, operazioni layer, lista, dettaglio layer attivo.
- `_draw_active_layer`: dispatcher per tipo layer.
- `_draw_procedural`: controlli procedural.
- `_draw_mask_block`: mask A/B, combine, levels, softness.
- `_draw_reference`: source layer e parametri.
- `_draw_adjustment`: Hue/Sat, Bright/Contrast, Levels, Color Balance.
- `_draw_paint_fill`: Paint e Fill.
- `_draw_pbr_channels`: canali PBR extra.
- `_draw_canvas_section`: opzioni canvas.
- `_draw_composite_section`: rebuild/flatten/convert.
- `_draw_performance_section`: debug performance.
- `_draw_bake_section`: bake/export.
- `_draw_presets_section`: preset built-in/custom.
- `_draw_io_section`: import/export `.tlm`.

La UI ora protegge bene la modalita shader editabile: quando `shader_editable`
e' attivo, i controlli di mutazione stack vengono disabilitati e appare il
pulsante `Return to TLM`.

Punto importante:

Il pannello procedural espone controlli specifici per tipo. Questo significa
che ogni nuovo procedural richiede coerenza in almeno quattro posti:

- property in `properties.py`;
- UI in `panels.py`;
- builder in `compositing.py`;
- serializzazione in `operators/io.py` e `operators/presets.py`.

## 6. Operatori

Cartella: `operators/`

### 6.1 Registry

File: `operators/__init__.py`

Aggrega classi da:

- layers
- groups
- visibility
- compositing_ops
- masks
- io
- pbr
- importing
- bake
- presets
- channel_pack
- keyframes
- thumbnails

Prima di registrare prova a unregisterare eventuali classi stale. Questo aiuta
durante sviluppo con reload addon.

### 6.2 Layer operations

File: `operators/layers.py`

Operatori:

- add paint
- add fill
- add adjustment
- add procedural
- add reference
- remove
- move up/down
- duplicate
- move top/bottom

`TLM_OT_AddPaintLayer` ha una buona UX:

- crea immagine trasparente;
- prova a settarla come canvas attivo;
- se possibile entra in Texture Paint;
- passa viewport a Material Preview.

Il layer duplicate copia le property registrate in modo generico e duplica
l'immagine se esiste.

### 6.3 Common helpers

File: `operators/_common.py`

Funzioni rilevanti:

- `_get_material`
- `_can_edit_tlm_stack`
- `_normalize_blend_mode`
- `_bake_preflight`
- `_BakeGuard`
- `_ensure_nodes`
- `_add_layer_common`

`_add_layer_common` e' il punto unico per creare layer. Gestisce:

- materiali senza nodi;
- blocco in modalita editable;
- group parent;
- default property;
- immagini paint generate;
- fake user immagini;
- spostamento del nuovo layer nello stack;
- rebuild se `auto_composite`.

`_BakeGuard` e' una protezione importante:

- forza Cycles;
- salva/restaura selezione, active object, mode, active node;
- evita orphan image se il bake fallisce;
- riduce danni collaterali sulla scena utente.

### 6.4 Compositing ops

File: `operators/compositing_ops.py`

Operatori:

- `tlm.rebuild_composite`
- `tlm.flatten_layers`
- `tlm.convert_to_editable_shader`
- `tlm.return_to_managed_shader`

La conversione a shader editabile:

1. rebuilda il grafo corrente;
2. trova i nodi TLM tramite prefisso/custom props;
3. rimuove props `tlm_*`;
4. rinomina nodi in modo umano;
5. disabilita `auto_composite`;
6. imposta `shader_editable=True`.

Return to TLM elimina i nodi marcati come editable e ricostruisce da stack.

### 6.5 Preset

File: `operators/presets.py`

Contiene:

- preset built-in;
- import/apply preset;
- save preset;
- delete preset;
- serializzazione layer simile a `io.py`.

La presenza sia di `operators/io.py` sia di `operators/presets.py` con logiche
simili e' utile ma rischiosa: ogni nuova property deve essere aggiunta in due
posti, altrimenti preset e import `.tlm` divergono.

### 6.6 IO

File: `operators/io.py`

Formato `.tlm` JSON:

- top-level object;
- material;
- resolution;
- uv_map;
- layers array;
- immagini paint codificate PNG base64.

Punti robusti:

- valida top-level e array layers;
- normalizza blend mode legacy;
- supporta merge o replace;
- migra `AUTO` a `BASE_COLOR`;
- crea immagini blank se mancano pixel data.

Punto critico:

La serializzazione e' lunga e manuale. Aggiungere property procedural/PBR
richiede disciplina.

### 6.7 Bake

File: `operators/bake.py`

Funzioni:

- bake PBR per Unreal, Unity, glTF o Custom;
- export BaseColor, Roughness, Metallic, Normal, Emission, Transmission, Alpha;
- channel packing per engine;
- alpha pack dentro base color;
- preflight scena;
- gestione normal bake distinta tra Normal Map, Bump e mix vector.

Rischi:

- bake dipende molto dal contesto Blender;
- oggetti multi-material slot sono bloccati da preflight;
- performance e memoria dipendono da risoluzione.

## 7. Compositor shader

File: `compositing.py`

Questo e' il motore reale del programma.

### 7.1 Canali supportati

`CHANNELS`:

- base_color
- roughness
- metallic
- normal
- emission
- transmission
- alpha
- bump

Ogni canale ha:

- flag di attivazione se necessario;
- property immagine;
- nome input BSDF;
- tipo scalar/color/vector.

### 7.2 Rebuild

Funzione centrale: `rebuild_node_tree(material)`

Flusso logico:

1. valida materiale e node tree;
2. deduplica nomi layer;
3. filtra layer visibili;
4. salva custom links;
5. pulisce nodi TLM;
6. crea o trova Principled BSDF e Material Output;
7. costruisce gruppi/root layers;
8. calcola layout canali;
9. costruisce base color;
10. costruisce roughness, metallic, emission, transmission, alpha;
11. costruisce normal e bump;
12. collega ai socket BSDF;
13. gestisce alpha Eevee/Cycles;
14. ripristina custom links;
15. assegna frame per layer;
16. registra metriche performance.

### 7.3 Build canale generico

Funzione: `_build_channel(...)`

Gestisce:

- PAINT;
- FILL;
- PROCEDURAL;
- REFERENCE;
- ADJUSTMENT;
- scalar/color behavior;
- alpha math;
- clipping mask;
- mask/fresnel/opacity;
- blend mode per canale.

Punto molto importante:

I canali scalar come Roughness e Metallic ora usano Mix RGBA + RGBToBW, non un
Mix FLOAT semplice. Questo serve per rendere blend mode come Add, Subtract,
Multiply, Difference piu coerenti con Base Color.

Per il primo layer scalar viene creata una baseline:

- roughness: 0.5
- metallic: 0.0
- transmission: 0.0
- alpha: 1.0

Questo evita che il primo layer bypassi blend/mask e che canali come Metallic
partano da valori sbagliati.

### 7.4 Procedural

Funzioni:

- `_build_procedural_node`
- `_build_proc_fac_node`
- helper hot update specifici.

Tipi supportati:

- Brick
- Checker
- Cracks
- Dots
- Gabor
- Gradient
- Hex Grid
- Magic
- Marble
- Musgrave
- Noise
- Ridged
- Stripes
- Voronoi
- Wave
- White Noise

Procedural base color e scalar usano la stessa logica colore: texture/fac ->
ColorRamp -> output. Per scalar il colore viene convertito con RGBToBW. Questo
mantiene ColorRamp, Color 3 e Contrast anche su Roughness/Metallic.

Controlli mapping:

- coordinate source;
- mapping type;
- location XYZ;
- rotation XYZ;
- scale XYZ;
- coordinate normalization;
- vector distortion.

Punti forti:

- molti procedural sono costruiti con nodi Blender reali;
- fallback dove i nodi non esistono;
- Gabor usato per streak/scratch;
- Stripes/Hex usano topology custom per Color 3.

Punti fragili:

- ogni tipo procedural ha path build + path fac + path UI + path hot update;
- duplicazione fra `_build_procedural_node` e `_build_proc_fac_node`;
- alcuni controlli sono socket/version dependent in Blender.

### 7.5 Mask

Funzioni:

- `_build_smart_generator`
- `_build_mask_slot`
- `_apply_mask`
- `_build_blurred_image_mask`

Sorgenti mask:

- Image
- Ambient Occlusion
- Pointiness
- Edge Wear
- Dirt
- Curvature Smart

Mask A e Mask B si combinano con:

- Multiply
- Minimum
- Maximum
- Add
- Subtract
- Screen
- Difference

Refinement:

- contrast;
- levels;
- gamma;
- output min/max;
- softness;
- blur image mask.

Nota:

Le mask live procedurali sono potenti ma possono diventare costose su stack
molto grandi o materiali con molti canali.

### 7.6 Alpha

Alpha e' stato uno dei punti piu delicati.

Aspetti gestiti:

- output channel Alpha;
- `use_base_color_alpha`;
- alpha math operations;
- transparent BSDF wrapper per compatibilita Cycles;
- Eevee/Material Preview blend method;
- protezione contro superfici nere in Cycles;
- alpha_mode `STRAIGHT` per paint canvas.

La soluzione attuale distingue:

- alpha come canale dedicato;
- alpha della paint image come coverage;
- base color alpha opzionale.

### 7.7 Paint canvas preservation

In `_new_img_tex` c'e' una protezione specifica per immagini Paint generate.

Problema affrontato:

Blender 5.0 puo azzerare buffer di immagini generate quando cambiano source o
colorspace senza file backing.

Soluzione:

- snapshot pixel prima di assegnare node image/colorspace;
- evita source FILE se immagine GENERATED non ha filepath;
- forza alpha mode STRAIGHT per canvas paint;
- ripristina buffer se viene azzerato.

Questo e' fondamentale per evitare perdita paint.

### 7.8 Hot update

Funzione: `hot_update_property(material, layer, prop_name)`

Mappa property -> handler in `_HOT_DISPATCH`.

Esempi:

- opacity;
- blend mode;
- fill color;
- scalar fill;
- procedural input;
- procedural color ramp;
- mask contrast/levels/softness;
- paint mapping;
- image swap;
- normal/bump strength;
- fresnel.

Se hot update non riesce, si torna a rebuild.

Questa architettura e' buona per performance, ma richiede che i nodi siano
sempre taggati correttamente.

### 7.9 Tagging nodi

Ogni nodo TLM ha custom props:

- `tlm_layer`
- `tlm_role`
- `tlm_frame_owner`
- altre props specifiche.

Questi tag servono per:

- hot update;
- frame grouping;
- cleanup;
- convert to editable shader;
- tag validation debug.

Se un nodo non viene taggato o viene taggato con nome vecchio, gli update live
possono non trovarlo.

### 7.10 Layout nodi

Funzioni:

- `_layer_width`
- `_layer_positions`
- `_layer_x_positions`
- `_assign_layer_frames`
- `_wrap_frame_cluster_by_x`
- `_wrap_frame_cluster`

Obiettivo:

- mantenere corsie per canale;
- evitare accumulo nodi;
- raggruppare layer in frame;
- ridurre caos nel node editor.

Stato:

Il layout e' migliorato, ma resta un tema importante. Grafo generato con molti
layer/canali puo comunque diventare molto largo e denso.

## 8. Flussi principali

### 8.1 Aggiunta Paint layer

1. UI chiama `tlm.add_paint_layer`.
2. `_add_layer_common` crea layer e immagine trasparente.
3. Immagine inizializzata black transparent.
4. Alpha mode STRAIGHT.
5. Fake user true.
6. Rebuild se auto composite.
7. Operatore prova a entrare in Texture Paint e selezionare canvas.

### 8.2 Aggiunta Procedural layer

1. UI chiama `tlm.add_procedural_layer`.
2. `_add_layer_common` crea layer con default `NOISE`.
3. User modifica proc_type/parametri.
4. Property update prova hot update o rebuild.
5. Builder crea coordinate, mapping, texture, color ramp, output.

### 8.3 Cambio parametro slider

1. Property callback `_make_hot_callback`.
2. `hot_update_property`.
3. Se trova nodi taggati, aggiorna socket/attribute.
4. Se topology non compatibile, ritorna false.
5. `_on_layer_update` programma rebuild debounced.

### 8.4 Rebuild manuale

1. `tlm.rebuild_composite`.
2. Se shader editabile, blocca.
3. `compositing.rebuild_node_tree`.
4. Rigenera il grafo TLM.

### 8.5 Convert to editable shader

1. Rebuild finale.
2. Detach dei nodi TLM.
3. Rimuove metadata `tlm_*`.
4. Imposta `shader_editable=True`.
5. Blocca futuri rebuild automatici.

### 8.6 Return to TLM

1. Rimuove nodi marcati editable.
2. Imposta `shader_editable=False`.
3. Riattiva auto composite.
4. Ricostruisce da stack.

## 9. Funzioni commercialmente rilevanti

Queste sono le feature che danno valore al prodotto:

- layer stack stile painting app;
- routing PBR per Base Color/Roughness/Metallic/Alpha;
- canali extra Normal/Bump/Emission/Transmission;
- procedural avanzati;
- paint reale dentro Blender;
- smart mask edge/dirt/curvature;
- blend modes anche su scalar channels;
- branching per canale;
- groups;
- reference layer per riuso pattern;
- conversione a shader editabile;
- bake/export PBR per engine;
- import/export `.tlm`;
- preset system.

## 10. Punti critici tecnici

### 10.1 `compositing.py` troppo grande

E' il centro del sistema ma supera 6000 linee. Contiene:

- hot update;
- node creation;
- procedural;
- masks;
- alpha;
- layout;
- rebuild;
- bake flatten helper;
- debug performance.

Rischio:

Modifiche piccole possono avere effetti laterali lontani.

Suggerimento:

Estrarre in moduli:

- `compositing/hot_update.py`
- `compositing/nodes.py`
- `compositing/procedurals.py`
- `compositing/masks.py`
- `compositing/channels.py`
- `compositing/layout.py`
- `compositing/alpha.py`

### 10.2 Serializzazione duplicata

Le property layer vengono salvate in:

- `operators/io.py`;
- `operators/presets.py`.

Rischio:

Una property nuova funziona in UI ma non viene salvata/importata.

Suggerimento:

Creare una tabella schema centralizzata per property serializzabili.

### 10.3 Procedural duplicated path

Ogni procedural ha due builder:

- colore completo;
- fac scalar per bump/mask/emission.

Rischio:

Il procedural su Base Color e lo stesso procedural su Roughness/Bump possono
divergere.

Suggerimento:

Costruire un builder unico che ritorna:

- color output;
- factor output;
- alpha output eventuale;
- metadata nodi.

### 10.4 Test automatici insufficienti

Esistono script/test helper, ma non una suite strutturata.

Servirebbero test Blender background per:

- add layer di ogni tipo;
- rebuild senza errori;
- blend mode scalar;
- paint alpha preservation;
- import/export roundtrip;
- preset roundtrip;
- bake preflight;
- convert/return editable;
- procedural coverage per ogni tipo.

### 10.5 Gestione Paint ancora delicata

Il codice ha gia molte protezioni. Questo indica che il tema e' reale:

- source FILE/GENERATED;
- colorspace;
- alpha mode;
- texture paint context;
- cache immagini Blender.

Rischio:

Regressioni facili se si cambia `_new_img_tex`, output channel paint o
import/export immagini.

### 10.6 Performance

Sono presenti:

- hot update;
- rebuild debounce;
- performance debug panel;
- mesh stats;
- frame/layout;
- smart mask timing.

Resta da monitorare:

- numero nodi con molti layer;
- smart mask live;
- mask blur;
- paint layer grandi;
- bake 4K;
- node editor usability.

## 11. Qualita architetturale

Punti forti:

- separazione chiara fra data model, UI, operatori e compositor;
- PropertyGroup salva tutto nel `.blend`;
- non distruttivo per design;
- fallback rebuild se hot update non riesce;
- forte attenzione a compatibilita Blender 5.0;
- UX paint ben considerata;
- bake guard robusto;
- convert to editable shader risolve il conflitto con modifiche manuali.

Punti da consolidare:

- ridurre dimensione compositor;
- schema unico property/serialization/UI;
- test automatici;
- documentazione dev;
- preset/materiali demo professionali;
- gestione asset promozionali separata dal core.

## 12. Possibili bug o regressioni da monitorare

1. Paint layer su canali scalar:
   - verificare sempre che alpha image agisca come coverage;
   - evitare overwrite globale con RGB nero trasparente.

2. Blend mode scalar:
   - Metallic con Subtract puo restare visivamente nero se baseline e' 0;
   - user education necessaria.

3. Alpha:
   - Cycles vs Eevee hanno comportamenti diversi;
   - `alpha_blend_method` Auto va testato su ogni release Blender.

4. Reference layers:
   - rischio cicli se reference a reference viene aggirato;
   - source rinominato deve restare sincronizzato.

5. Group layers:
   - group mask e child ordering sono sensibili all'ordine stack.

6. Preset import:
   - property nuove mancanti possono cambiare look del materiale.

7. Procedural:
   - socket name Blender puo cambiare;
   - Gabor e nuovi node type sono version dependent.

8. Node layout:
   - molti layer generano ancora grafi larghi;
   - frames aiutano ma non risolvono il problema di leggibilita totale.

9. Bake:
   - normal/bump bake e' fragile per definizione;
   - multi-material object bloccato, ma UX potrebbe richiedere spiegazioni migliori.

10. Untracked preset_dev:
   - se diventano parte del prodotto, vanno tracciati o ignorati esplicitamente.

## 13. Priorita consigliate

### Prima della 1.0

1. Stabilizzare Paint save/load e preset con immagini.
2. Coprire roundtrip `.tlm` e preset.
3. Aggiungere test background per tutti i procedural.
4. Verificare bake/export su Unreal, Unity, glTF.
5. Pulire eventuali file temporanei non tracciati o aggiungerli a `.gitignore`.
6. Documentare workflow `Convert to Editable Shader`.
7. Creare 3-5 materiali promozionali veri, non solo script tecnici.

### Dopo la 1.0

1. Refactor modulare di `compositing.py`.
2. Schema declarativo property -> UI -> serialization -> node builder.
3. Node group opzionali per ridurre caos nel node editor.
4. Sistema asset/preset marketplace-ready.
5. Performance profiling su scene grandi.

## 14. Analisi commerciale del programma

Il prodotto ha senso per venderlo se viene presentato come:

"Layer-based procedural and paint material authoring inside Blender, with PBR
channels, smart masks and export-ready bake."

Valore per utente:

- non deve costruire nodi complessi a mano;
- puo lavorare per layer;
- puo dipingere e usare procedural insieme;
- puo esportare mappe PBR;
- puo convertire a shader editabile quando vuole controllo manuale.

Feature da mostrare nei materiali promo:

- ruggine/vernice scrostata;
- carbon fiber/clearcoat;
- sci-fi panel emissivo;
- legno verniciato consumato;
- pietra/marmo con roughness e bump;
- decals/paint manuale sopra procedural.

Il reverse engineering dei materiali reference e' una strategia corretta:
permette di verificare se il tool copre casi reali e mostra subito feature
mancanti.

## 15. Conclusione

Texture Layer Manager non e' piu solo un prototipo di layer shader: e' un
sistema completo con data model, UI, rebuild engine, paint workflow,
procedural stack, mask system, PBR routing e bake/export.

La parte piu matura e' la visione funzionale. La parte da rendere piu solida
prima della vendita e' la manutenibilita interna: test, serializzazione unica,
refactor del compositor e materiali dimostrativi di alta qualita.

Il prossimo lavoro piu utile non e' aggiungere molte feature nuove, ma:

1. chiudere test e bug critici;
2. produrre materiali promo professionali;
3. documentare workflow utente;
4. ridurre rischi di regressione su paint, alpha e procedural.
