# TLM — Test Plan

**Aggiornato**: 2026-05-28 — copre features fino al commit `a16134b` (PAINT-only brush icon).
**Target Blender**: 5.0 / 5.1 (la suite supporta entrambe).

Piano di test manuale. Ogni test ha: **Setup** → **Expected** → ☐ Pass / ☐ Fail / **Note**.

Oggetto di test consigliato: una **Plane** con UV unwrappato (U → Unwrap), sotto una HDRI neutra, viewport shading **Rendered**.
Per i test che richiedono displacement reale, usa una mesh con subdivision (Subdiv ≥3 + Cycles). Per i test NDOTL/NDOTH, serve una **Sun light** nella scena.
Salva spesso. Apri la console Blender (Window → Toggle System Console) per vedere errori Python.

**Legenda**:
- `☑` = da spuntare quando il test passa
- **Crit** = test critico (se fallisce blocca tutto il resto)
- **Reg** = regression (ha rotto in passato, merita attenzione extra)
- **New** = aggiunto dopo il pass precedente (≥ 2026-04-22)

## Cosa è NUOVO dall'ultimo pass (2026-04-22 → 2026-05-28)

Sezioni interamente nuove (in fondo al file):
- §26 — Material-Level Settings (BSDF IOR, Volume Absorption, Volume Scatter)
- §27 — Geometric Displacement (master + per-layer)
- §28 — Anime / Cel-Shading (NdotL mask, integrated outline, emission bypass)
- §29 — Alpha Channel (output_channel=ALPHA + alpha_math_operation)
- §30 — ColorRamp UI Controls (Manual Stops, Mode/Interp, multi-stop)
- §31 — Image Mapping (paint/fill UV controls — Source/Projection/Extension + Loc/Rot/Scale)
- §32 — UI Phase 2 Structure (Surface Effects collapsible, Mask always-on collapsible, PAINT-only brush)
- §33 — Preset Library Integrity (post Phase 2 refactor — tutti i .tlm devono caricare con BSDF wired)

Sezioni che hanno ricevuto modifiche significative:
- §2 — HARD_LIGHT rimosso dall'enum (cross-version inconsistente)
- §3 — Aggiunti Alpha channel + Transmission (canale completato)
- §5 — Procedurals: passati da **8** a **17** tipi (BRICK / CRACKS / DOTS / FRESNEL / GABOR / HEX_GRID / MAGIC / RIDGED / STRIPES / WHITE_NOISE aggiunti, MARBLE/CLOUDS riassorbiti come WAVE/NOISE variants)
- §6 — Adjustments: da 5 a **4** tipi (CURVES rimosso)
- §9 — Mask sources: aggiunti WIREFRAME, FRESNEL, NDOTL, NDOTH, VORONOI (oltre alle 6 originarie)
- §13 — Fresnel UI: ora dentro la collapsible "Surface Effects", non più riga inline
- §14 — Coordinates: UI riorganizzata dentro la collapsible "Mapping"
- §18 — Lista preset aggiornata (28+ presets, inclusi tutti gli Hero A → Q)

---

## 0. Sanity & Setup

### 0.1 [Crit] Addon loads without errors
**Setup**: Blender → Edit → Preferences → Add-ons → cerca "Texture Layer Manager" → attiva.
**Expected**: Nessun errore in console. La N-panel (tasto N in viewport 3D) mostra tab "TLM".
☐ Pass ☐ Fail — Note:

### 0.2 No orphan nodes on fresh material
**Setup**: Crea nuovo material su Plane, non aggiungere layer TLM.
**Expected**: Shader Editor mostra solo Principled BSDF + Output. Nessun nodo `TLM_*`.
☐ Pass ☐ Fail — Note:

### 0.3 TLM section visible in material properties
**Setup**: Properties editor → Material tab → scroll.
**Expected**: Sezione "Texture Layers" presente.
☐ Pass ☐ Fail — Note:

---

## 1. Layer Stack — Basi

### 1.1 [Crit] Add Paint Layer
**Setup**: TLM panel → Add → Paint.
**Expected**:
- Compare layer "Layer 1" in lista
- Shader Editor mostra catena TLM (ImageTex → BSDF)
- Immagine `Layer 1` creata in bpy.data.images (nera trasparente 1024×1024 di default)
☐ Pass ☐ Fail — Note:

### 1.2 [Crit] Add Fill Layer
**Setup**: Add → Fill.
**Expected**: Layer "Fill 2" (o simile) compare sopra il precedente. Nessuna immagine creata.
☐ Pass ☐ Fail — Note:

### 1.3 [Crit] Add Adjustment Layer (default Hue/Sat)
**Setup**: Add → Adjustment.
**Expected**: Layer "Hue/Sat" compare sopra l'active. `adj_type` = HUE_SAT.
☐ Pass ☐ Fail — Note:

### 1.4 [Crit] Add Procedural Layer (default Noise)
**Setup**: Add → Procedural.
**Expected**: Layer "Noise" compare. `proc_type` = NOISE. Pattern noise visibile sul Plane.
☐ Pass ☐ Fail — Note:

### 1.5 Add Reference Layer (needs a pattern layer first)
**Setup**: Con almeno 1 layer PAINT/FILL/PROCEDURAL → Add → Reference.
**Expected**: Layer "Reference N" compare. Il campo `reference_layer_name` è auto-compilato.
☐ Pass ☐ Fail — Note:

### 1.6 Add Group
**Setup**: Add → Group (da header o operatore Groups).
**Expected**: Layer di tipo GROUP compare, comportamento folder.
☐ Pass ☐ Fail — Note:

### 1.7 Remove active layer
**Setup**: Seleziona un layer → Remove.
**Expected**: Layer rimosso. `active_layer_index` clamped al nuovo range. Shader rebuilt.
☐ Pass ☐ Fail — Note:

### 1.8 Remove last layer → empty stack
**Setup**: Rimuovi fino a 0 layer.
**Expected**: Nessun crash. Material torna a stato "fresh" (solo BSDF + Output).
☐ Pass ☐ Fail — Note:

### 1.9 Move Up / Move Down
**Setup**: 3 layer → seleziona middle → freccia su/giù.
**Expected**: Ordine cambia. Shader rebuilt. Active index segue il layer mosso.
☐ Pass ☐ Fail — Note:

### 1.10 Move to Top / Bottom
**Setup**: Layer al centro → Move to End Top / Bottom.
**Expected**: Layer va in cima/fondo istantaneamente.
☐ Pass ☐ Fail — Note:

### 1.11 [Reg] Duplicate Layer
**Setup**: Layer PAINT con immagine dipinta → Duplicate.
**Expected**:
- Nuovo layer "XYZ Copy" sopra l'originale
- Image duplicata (datablock separato)
- **TUTTE le properties copiate** (blend, opacity, channels, mask, branching, procedural params, adj params, fresnel, coords). Verifica con almeno 3 properties non-default.
☐ Pass ☐ Fail — Note:

### 1.12 Rename layer
**Setup**: Doppio click sul nome nella UIList → edita.
**Expected**: Nome cambiato. Se è PAINT, immagine associata **non** si rinomina (image_name resta).
☐ Pass ☐ Fail — Note:

### 1.13 Visibility toggle (eye icon)
**Setup**: Click icona occhio su un layer.
**Expected**: Layer scomparso dal composite. Re-click ripristina. `visible` property toggled.
☐ Pass ☐ Fail — Note:

### 1.14 Solo layer
**Setup**: Con 3+ layer → click icona solo su uno.
**Expected**: Solo quel layer visibile. Re-click ripristina tutti.
☐ Pass ☐ Fail — Note:

### 1.15 [Reg] Lock layer
**Setup**: Click lock icon. Prova a muovere slider (blend, opacity), rimuovere, muovere su/giù, duplicare.
**Expected**:
- Tutti i parametri del layer (blend, opacity, channels, mask, branching, proc params) sono **grayed out** (non cliccabili)
- Remove / Move Up / Move Down / Move to Top/Bottom → bloccati con WARNING "`Layer X is locked. Unlock it to …`"
- Duplicate → permesso (crea copia anch'essa locked)
- Visibility toggle (eye) e Solo toggle → sempre funzionanti
- Name rename e Color tag → sempre funzionanti
- Unlocca e verifica che tutto torna editabile
**Bug risolto 2026-04-20**: lock era cosmetico, solo bloccava paint mode. Nessun UI disable, nessun block su Remove/Move.
☐ Pass ☐ Fail — Note:

### 1.16 Color tag
**Setup**: Imposta color tag (Red/Orange/...) su un layer.
**Expected**: Indicatore colorato visibile nella UIList.
☐ Pass ☐ Fail — Note:

---

## 2. Blend Modes

TLM supporta **19** blend modes: MIX, MULTIPLY, SCREEN, OVERLAY, ADD, SUBTRACT, DIFFERENCE, DIVIDE, DARKEN, LIGHTEN, COLOR_DODGE, COLOR_BURN, SOFT_LIGHT, LINEAR_LIGHT, EXCLUSION, HUE, SATURATION, COLOR, LUMINOSITY.

**Nota 2026-05-xx**: HARD_LIGHT è stato **rimosso** dall'enum user-facing per inconsistenza cross-version (Blender 4.x vs 5.x). I .tlm/JSON che ancora contengono "HARD_LIGHT" cadono su MIX via `.get(..., "MIX")` in `BLEND_TO_MIX_MODE`. Non aggiungere nuove istanze.

### 2.1 [Crit] MIX (default)
**Setup**: 2 layer Fill, uno rosso (1,0,0) sopra uno blu (0,0,1), opacity 0.5.
**Expected**: Viola (~0.5, 0, 0.5). Stesso risultato di un Blender Mix node.
☐ Pass ☐ Fail — Note:

### 2.2 MULTIPLY
**Setup**: Fill grigio 50% sopra Fill rosso puro, MIX → MULTIPLY.
**Expected**: Risultato più scuro del rosso originale.
☐ Pass ☐ Fail — Note:

### 2.3 SCREEN
**Setup**: Fill grigio 50% sopra Fill nero, blend SCREEN.
**Expected**: Risultato chiaro.
☐ Pass ☐ Fail — Note:

### 2.4 OVERLAY
**Setup**: Noise bianco/nero sopra Fill 50% grigio, blend OVERLAY.
**Expected**: Contrasto aumentato.
☐ Pass ☐ Fail — Note:

### 2.5 ADD
**Setup**: 2 fill, uno rosso 0.5 sopra uno verde 0.5, blend ADD.
**Expected**: Giallo (1, 1, 0).
☐ Pass ☐ Fail — Note:

### 2.6 Spot-check modes restanti
**Setup**: Ciclare attraverso SUBTRACT, DIFFERENCE, DIVIDE, DARKEN, LIGHTEN, DODGE, BURN, SOFT_LIGHT, HARD_LIGHT, LINEAR_LIGHT, EXCLUSION, HUE, SATURATION, COLOR, LUMINOSITY.
**Expected**: Nessun crash per ciascuno. Risultato visivo sensato (anche se non verificato pixel-perfect).
☐ Pass ☐ Fail — Note:

### 2.7 Opacity slider (hot-path)
**Setup**: Sposta opacity lentamente da 0 a 1.
**Expected**: Update live nel viewport, NO rebuild visibile (sliders fluidi). Debounce ~180ms.
☐ Pass ☐ Fail — Note:

---

## 3. PBR Channels

**Nota architetturale importante**: Base Color è **sempre implicitamente attivo** per ogni layer — non ha checkbox. Per FILL il base color è il field "Color:" nel pannello. Per PAINT è il contenuto dell'immagine. Per PROCEDURAL sono i colori della Color Ramp.

Canali **opt-in** (con "+" nella sezione PBR Channels): **Roughness, Metallic, Normal, Bump, Emission, Transmission**. Questi devi abilitarli esplicitamente se vuoi che il layer li scriva.

### 3.1 [Crit] Base Color (Fill layer)
**Setup**: Fill layer → field "Color:" (sopra la sezione Mask) → clicca lo swatch → imposta rosso (1,0,0). Viewport shading = Material Preview o Rendered.
**Expected**: Piano rosso nel viewport.
☐ Pass ☐ Fail — Note:

### 3.2 [Crit] Roughness override (Fill)
**Setup**: Fill → PBR Channels → clicca "+" accanto a Roughness per abilitarla → slider = 0.1.
**Expected**: Superficie diventa specchiante (se metallic=1) o molto lucida (se metallic=0).
☐ Pass ☐ Fail — Note:

### 3.3 [Crit] Metallic override (Fill)
**Setup**: Fill → PBR Channels → "+" Metallic → slider = 1.0.
**Expected**: Riflesso metallico. Colore del riflesso = base_color del layer.
☐ Pass ☐ Fail — Note:

### 3.4 Channel disabled → passes through
**Setup**: 2 layer Fill. Layer sopra con SOLO Color impostato (Roughness/Metallic entrambi OFF, cioè non abilitati nella PBR Channels).
**Expected**: Il layer sopra scrive base_color ma Roughness e Metallic vengono dal layer sotto.
☐ Pass ☐ Fail — Note:

### 3.5 Emission channel
**Setup**: Fill → Emission ON → colore giallo intenso.
**Expected**: Piano emette luce gialla visibile in Rendered mode.
☐ Pass ☐ Fail — Note:

### 3.6 Transmission channel
**Setup**: Fill → Transmission ON → fill = 1.0. Roughness = 0.
**Expected**: Oggetto diventa trasparente (serve Cycles per vederlo bene).
☐ Pass ☐ Fail — Note:

### 3.7 Bump channel
**Setup**: Procedural Noise → abilita Bump → strength > 0.
**Expected**: Rilievo fake visibile (senza vera deformazione geometria).
☐ Pass ☐ Fail — Note:

### 3.8 [Reg] Normal channel (per-layer NormalMap + Strength)
**Setup**: 2 layer, ciascuno con un normal map diverso + strength differente (es. 0.3 e 1.0).
**Expected**:
- Due NormalMap node nel shader tree, ciascuno con la sua Strength
- Blending dei vettori normali (Mix data_type=VECTOR, non RGBA)
☐ Pass ☐ Fail — Note:

### 3.9 [Reg] Normal tile scale
**Setup**: Layer con normal map → imposta `normal_tile_scale` = 2.0.
**Expected**: Mapping node compare nel tree. Pattern ripetuto 2x.
☐ Pass ☐ Fail — Note:

### 3.10 [Reg] Normal rotation
**Setup**: `normal_rotation` = 45.
**Expected**: Pattern ruotato di 45°.
☐ Pass ☐ Fail — Note:

### 3.11 Normal + Bump insieme
**Setup**: Layer A normal, Layer B bump.
**Expected**: Output Normal → ingresso Normal del Bump node → BSDF. Nessun conflitto.
☐ Pass ☐ Fail — Note:

---

## 4. Branching (per-channel blend override)

Permette a un singolo layer di avere blend mode diversi per ciascun canale PBR.

**5 canali supportati** (vedi `compositing._effective_blend_mode`):
`blend_mode_base_color`, `blend_mode_roughness`, `blend_mode_metallic`, `blend_mode_emission`, `blend_mode_transmission`.

**Non supportati per branching**: Normal e Bump — usano math vettoriale dedicato (`_build_normal_channel`, `_build_bump_channel`) che ignora `blend_mode`. Rispettano `opacity` ma non override per channel.

**Default**: tutti su `INHERIT` → fallback su `layer.blend_mode` (il blend principale).

**Risoluzione**: `_effective_blend_mode(layer, channel_id)` ritorna `blend_mode_<channel>` se ≠ INHERIT, altrimenti `layer.blend_mode`.

### 4.1 [Crit] INHERIT comportamento default
**Setup**: 2 Fill (rosso sotto, blu sopra). Layer top: main `blend_mode = MIX`, tutti i branching = INHERIT. Abilita roughness, metallic, emission, transmission sul layer top con valori distinti dai default.
**Expected**: Su tutti e 5 i canali il blend è MIX → in viewport vedi solo la layer top (che copre quella sotto). Lo Shader Editor mostra tutti i Mix node con `blend_type='MIX'`.
☐ Pass ☐ Fail — Note:

### 4.2 [Crit] Override singolo channel: base_color = MULTIPLY
**Setup**: Fill nero sopra Fill bianco. Top main `blend_mode = MIX`. Branching: `blend_mode_base_color = MULTIPLY`, gli altri = INHERIT.
**Expected**: Base color = nero (multiply blackens). Gli altri channel non influenzati. Apri Shader Editor → il Mix node del base_color ha `blend_type='MULTIPLY'`, quelli degli altri canali (se presenti) hanno `blend_type='MIX'`.
☐ Pass ☐ Fail — Note:

### 4.3 Override singolo channel: roughness = OVERLAY
**Setup**: 2 Fill, top con use_roughness=True (`roughness_fill = 0.3`). Main = MIX. Branching: `blend_mode_roughness = OVERLAY`.
**Expected**: Base color del top copre interamente quella sotto (MIX). Roughness fonde via OVERLAY (verifica nei tooltip o Shader Editor: Mix node sul canale roughness ha `blend_type='OVERLAY'`).
☐ Pass ☐ Fail — Note:

### 4.4 Override singolo channel: metallic = ADD
**Setup**: 2 Fill, top con use_metallic=True (`metallic_fill = 0.5`). Branching: `blend_mode_metallic = ADD`.
**Expected**: Mix node metallic ha `blend_type='ADD'`. La layer sotto + 0.5 sul canale metallic (clamp a 1).
☐ Pass ☐ Fail — Note:

### 4.5 Override singolo channel: emission = SCREEN
**Setup**: 2 Fill, top con use_emission=True. Branching: `blend_mode_emission = SCREEN`.
**Expected**: Mix node emission ha `blend_type='SCREEN'`. Glow additivo sul canale emission.
☐ Pass ☐ Fail — Note:

### 4.6 Override singolo channel: transmission = MIX (no-op explicit)
**Setup**: Top main = MULTIPLY, `blend_mode_transmission = MIX`.
**Expected**: Transmission = MIX (l'override esplicito vince). Base color = MULTIPLY.
**Verifica**: dimostra che `MIX esplicito` ≠ `INHERIT con main=MULTIPLY`.
☐ Pass ☐ Fail — Note:

### 4.7 [Crit] Override multipli: scenario realistico (rust patches)
**Setup**: Fill marrone scuro sopra Fill metallo argento. Top abilita roughness (0.8 valore), metallic (0.0), emission (off). Branching:
- `blend_mode_base_color = MIX` (sostituisce il colore)
- `blend_mode_roughness = INHERIT` (main MIX → MIX, sostituisce)
- `blend_mode_metallic = MULTIPLY` (smolla il metallic della layer sotto)
**Expected**: Aree top = marrone opaco non-metallico. Aree sotto = ancora metallico. Confine netto.
☐ Pass ☐ Fail — Note:

### 4.8 INHERIT rispetta cambio main blend_mode
**Setup**: 2 Fill. Top branching tutti su INHERIT. Cambia main `blend_mode` da MIX a SCREEN.
**Expected**: Tutti i Mix node dei canali attivi cambiano da MIX a SCREEN. Cambia da SCREEN a MULTIPLY → tutti diventano MULTIPLY.
**Hot path**: il main `blend_mode` è hot-update (`_hot_blend_mode`). Verifica nello Shader Editor che lo stesso Mix node sopravviva al cambio (no rebuild — controlla che il TLM_id non cambi).
☐ Pass ☐ Fail — Note:

### 4.9 [Reg] Branching NON influenza Normal
**Setup**: 2 layer entrambi con normal map. Top branching `blend_mode_base_color = MULTIPLY`. Cambia anche `blend_mode_emission = SCREEN`.
**Expected**: I normal blendano vettorialmente come al solito (vector mix in `_build_normal_channel`), invariati dal branching. Verifica visualmente che le scratch del normal del top siano composite con quelle sotto identicamente al caso INHERIT.
☐ Pass ☐ Fail — Note:

### 4.10 [Reg] Branching NON influenza Bump
**Setup**: 2 layer entrambi con use_bump. Top branching come 4.9.
**Expected**: I bump blendano via i Bump node concatenati. Branching non altera nulla.
☐ Pass ☐ Fail — Note:

### 4.11 Branching + Mask
**Setup**: Top con mask AO live, branching `blend_mode_base_color = MULTIPLY`, `blend_mode_metallic = ADD`.
**Expected**: La mask AO controlla DOVE entrambi gli override agiscono. Il Factor del Mix node è guidato dalla pipeline mask → opacity (non solo opacity).
☐ Pass ☐ Fail — Note:

### 4.12 Branching + Opacity
**Setup**: Top branching `blend_mode_base_color = MULTIPLY`, opacity = 0.5.
**Expected**: Il MULTIPLY è applicato al 50%. Verifica nel Shader Editor: `Factor = layer_alpha · 0.5` (cablato sulla A/B socket del Mix MULTIPLY).
☐ Pass ☐ Fail — Note:

### 4.13 Branching + first-layer modulator
**Setup**: Una sola layer (Fill) con `blend_mode_base_color = MULTIPLY` e `mask_source = AO`. Niente layer sotto.
**Expected**: Il branching MULTIPLY si applica contro un baseline grigio sintetizzato (vedi compositing.py:3473 — `needs_modulator` triggera il bg). La mask controlla l'apparizione.
**Bug history**: prima del fix Bug #5 questo caso droppava silenziosamente sia mask che branching. Verifica che ora funzioni.
☐ Pass ☐ Fail — Note:

### 4.14 Branching su Procedural
**Setup**: Voronoi layer sopra Fill rosso. use_roughness su top (`roughness_fill=0.5` o procedural-driven). Branching: `blend_mode_base_color = INHERIT`, `blend_mode_roughness = OVERLAY`.
**Expected**: Il pattern Voronoi guida sia il colore (mix normale) sia la roughness (overlay con la layer sotto).
☐ Pass ☐ Fail — Note:

### 4.15 Branching su Reference
**Setup**: Reference layer che punta a un Voronoi. Reference branching: `blend_mode_emission = ADD`.
**Expected**: Il pattern Voronoi del reference è additivamente blendato sul canale emission. Base color usa il main blend.
☐ Pass ☐ Fail — Note:

### 4.16 [Reg] Adjustment ignora branching
**Setup**: Adjustment layer (HUE_SAT). Anche se imposti `blend_mode_base_color = MULTIPLY`, l'adjustment non usa Mix node (modifica `current` in-place).
**Expected**: Il branching su Adjustment è no-op silenzioso. Nessun comportamento anomalo.
☐ Pass ☐ Fail — Note:

### 4.17 Save/Load JSON preserva branching
**Setup**: Layer con 3 override impostati (es. base_color=MULTIPLY, roughness=ADD, emission=SCREEN). Export JSON → New material → Import JSON.
**Expected**: I 3 override sono ripristinati identici. Verifica anche `INHERIT` (default) sui channel non toccati.
☐ Pass ☐ Fail — Note:

### 4.18 Apply Preset preserva branching
**Setup**: Salva layer corrente come Preset utente. Apply su nuovo materiale.
**Expected**: Override conservati.
☐ Pass ☐ Fail — Note:

### 4.19 Duplicate Layer preserva branching
**Setup**: Layer con override impostati. Duplicate Layer.
**Expected**: La duplicata ha gli stessi override del sorgente.
☐ Pass ☐ Fail — Note:

### 4.20 Branching dropdown UI — INHERIT default visibile
**Setup**: Apri pannello layer con tutti gli override su INHERIT (default).
**Expected**: I 5 dropdown mostrano "Inherit (Layer)". Cambiare uno solo a un valore esplicito non modifica gli altri.
☐ Pass ☐ Fail — Note:

### 4.21 [Reg] Toggle channel off non rompe branching state
**Setup**: Layer con `use_roughness=True` e `blend_mode_roughness=OVERLAY`. Disattiva use_roughness, riattivalo.
**Expected**: L'override `OVERLAY` è preservato quando riattivi (rimane stored sulla property anche quando il channel è disabled).
☐ Pass ☐ Fail — Note:

---

## 5. Procedural Types (17 tipi)

Shared params (visibili in tutti tranne dove specificato): `proc_scale`, `proc_color1/2/3 + extras`, `proc_contrast`, `proc_ramp_center`, `proc_offset_x/y/z`, `proc_rotation_x/y/z`, `proc_mapping_scale_x/y/z`, `proc_vector_distortion`.

**Sezioni UI** (per ogni procedural layer, in ordine):
1. Pattern Params (collapsible) — knob per-type + scale
2. Mapping (collapsible) — Location/Rotation/Scale + Coords + Transform
3. Color Ramp (collapsible) — Mode/Interp + manual stops + extras + Contrast/Center
4. Mask (collapsible — sempre visibile)
5. Surface Effects (collapsible — Fresnel/Displacement/Volume)
6. PBR Channels (collapsible)
7. Clipping Mask + Group Assignment (inline)

### 5.1 [Crit] NOISE
**Setup**: Procedural → Type = Noise. Regola scale, detail, distortion.
**Expected**: Pattern noise perlin/FBM. Scale cambia densità. Detail aggiunge ottave.
☐ Pass ☐ Fail — Note:

### 5.2 [Crit] VORONOI
**Setup**: Type = Voronoi. Feature = F1. Randomness = 1.
**Expected**: Celle nitide stile Worley.
☐ Pass ☐ Fail — Note:

### 5.3 VORONOI — feature F2, Distance metric
**Setup**: Voronoi → Feature = F2 / Distance = Chebychev / Manhattan.
**Expected**: Pattern diversi ma coerenti (bordi squadrati per Chebychev/Manhattan).
☐ Pass ☐ Fail — Note:

### 5.4 VORONOI — Random Per Cell
**Setup**: Voronoi → Random Per Cell ON. 2 colori differenti.
**Expected**: Ogni cella ha colore random tra color1 e color2.
☐ Pass ☐ Fail — Note:

### 5.5 WAVE
**Setup**: Type = Wave.
**Expected**: Bande sinusoidali. Params: wave_type, scale, distortion.
☐ Pass ☐ Fail — Note:

### 5.6 GRADIENT
**Setup**: Type = Gradient. Prova gradient_type = Linear / Radial / Quadratic / Spherical.
**Expected**: Gradient corretto per ciascun tipo.
☐ Pass ☐ Fail — Note:

### 5.7 MUSGRAVE
**Setup**: Type = Musgrave. Prova varianti (Multifractal, Ridged, etc.).
**Expected**: Fractal noise più granulare/erratic di Perlin.
☐ Pass ☐ Fail — Note:

### 5.8 CHECKER
**Setup**: Type = Checker. Scale = 10.
**Expected**: Scacchiera bianco/nero (o color1/color2).
☐ Pass ☐ Fail — Note:

### 5.9 MARBLE
**Setup**: Type = Marble.
**Expected**: Venature stile marmo (wave distorto da noise).
☐ Pass ☐ Fail — Note:

### 5.10 CLOUDS
**Setup**: Type = Clouds.
**Expected**: Noise soffice tipo nuvole.
☐ Pass ☐ Fail — Note:

### 5.11 [Reg] Contrast slider (hot-path)
**Setup**: Qualsiasi procedural → sposta Contrast slider.
**Expected**: Update live senza rebuild.
☐ Pass ☐ Fail — Note:

### 5.12 Vec Distort
**Setup**: Noise → Vec Distort > 0.
**Expected**: Pattern distorto.
☐ Pass ☐ Fail — Note:

### 5.13 [New] BRICK
**Setup**: Type = Brick. Regola Mortar Size / Brick Width / Row Height / Offset / Bias.
**Expected**: Pattern mattoni con malta visibile. `use_proc_color3=True` → Color3 = colore malta (suggerito da hint UI).
☐ Pass ☐ Fail — Note:

### 5.14 [New] MAGIC
**Setup**: Type = Magic. Regola Depth + Distortion.
**Expected**: Pattern caleidoscopico colorato che cambia con Depth.
☐ Pass ☐ Fail — Note:

### 5.15 [New] WHITE_NOISE
**Setup**: Type = White Noise. Regola Scale.
**Expected**: Rumore per-pixel grossolano (grano fine, dust). Nessun parametro extra (label INFO conferma).
☐ Pass ☐ Fail — Note:

### 5.16 [New] STRIPES
**Setup**: Type = Stripes. Direction = X/Y/Diagonal. Width = 0.3, Sharpness = 0.95.
**Expected**: Strisce dure. Detail/Detail Scale aggiungono noise rotture.
☐ Pass ☐ Fail — Note:

### 5.17 [New] HEX_GRID
**Setup**: Type = Hex Grid. Edge Width = 0.1, Randomness = 0.
**Expected**: Pattern a nido d'ape pulito. Randomness > 0 → bordi rotti.
☐ Pass ☐ Fail — Note:

### 5.18 [New] GABOR (brushed metal)
**Setup**: Type = Gabor. Anisotropy = 1.0, Frequency = 4, Orientation = 0.
**Expected**: Streaks paralleli — ideale per metallo spazzolato. Frequency alta = streak più fine.
**Reg 2026-05-12**: `proc_gabor_frequency` max alzato da 20 a 500 — verifica che il slider arrivi fino a 500.
☐ Pass ☐ Fail — Note:

### 5.19 [New] DOTS
**Setup**: Type = Dots. Radius = 0.30, Softness = 0.05, Randomness = 1.0.
**Expected**: Polkadots packed. Randomness=0 → lattice rigido.
☐ Pass ☐ Fail — Note:

### 5.20 [New] RIDGED
**Setup**: Type = Ridged. Detail = 8, Gain = 3.0, Lacunarity = 2.0.
**Expected**: Cresta affilata fractal — mountain ridges / lightning.
☐ Pass ☐ Fail — Note:

### 5.21 [New] CRACKS
**Setup**: Type = Cracks. Width = 0.1, Sharpness = 0.9.
**Expected**: Network di crepe organiche. Aggiungi `proc_vector_distortion > 0` per crepe più organiche.
☐ Pass ☐ Fail — Note:

### 5.22 [New] FRESNEL (procedural gradient)
**Setup**: Type = Fresnel Gradient. IOR = 1.45.
**Expected**: Gradiente view-angle (0 facing → 1 grazing). Color1/Color2 fondono via ColorRamp.
**Caso d'uso**: pair con Color Ramp multi-stop per iridescente / oil-slick / hologram.
☐ Pass ☐ Fail — Note:

### 5.23 [New] MARBLE — Pattern + Profile
**Setup**: Type = Marble. Pattern = Rings (or Bands). Profile = Sin.
**Expected**: Venature marmo. Distortion + Marble Distortion (Turbulence) regolano la complessità.
**Reg 2026-05-xx**: Marble proc replicava la logica ColorRamp standalone — fixato per usare la shared `_build_proc_color_ramp` (mode/interp/manual stops/extras).
☐ Pass ☐ Fail — Note:

---

## 6. Adjustment Types (4 tipi)

**Cambiato 2026-05-xx**: CURVES rimosso dall'enum (l'API ShaderNodeRGBCurve era difficile da mappare a hot-update e cattiva UX da pannello). Per curve di base, usa LEVELS (gamma + input/output remap).

### 6.1 [Crit] HUE_SAT
**Setup**: Sotto layer colorato → aggiungi Adjustment Hue/Sat → Hue slider.
**Expected**: Rotazione hue visibile.
☐ Pass ☐ Fail — Note:

### 6.2 BRIGHT_CONTRAST
**Setup**: Adjustment Brightness/Contrast.
**Expected**: Sliders influenzano luminosità/contrasto del composite sotto.
☐ Pass ☐ Fail — Note:

### 6.3 LEVELS
**Setup**: Adjustment Levels. Regola input min/max, output min/max, gamma.
**Expected**: Remapping tonale (input → gamma → output range).
☐ Pass ☐ Fail — Note:

### 6.4 COLOR_BALANCE
**Setup**: Adjustment Color Balance → Lift / Gamma / Gain.
**Expected**: Grading cinematico. Lift tocca ombre, Gamma midtones, Gain highlight.
☐ Pass ☐ Fail — Note:

### 6.5 Adjustment ignora own channels
**Setup**: Adjustment layer tra 2 Fill.
**Expected**: Modifica composite sotto. Non contribuisce direttamente ai canali, solo modifica.
☐ Pass ☐ Fail — Note:

### 6.6 [New] Adjustment Target channel
**Setup**: Adjustment → "Target" dropdown → Roughness/Metallic/Alpha.
**Expected**: Adjustment opera SOLO sul canale target (non sul Base Color). Su scalar channels solo BRIGHT_CONTRAST e LEVELS hanno effetto — HUE_SAT/COLOR_BALANCE mostrano info-hint "has no effect on a scalar channel".
☐ Pass ☐ Fail — Note:

---

## 7. Reference Layer

Un Reference riusa il pattern (color+alpha) di un altro layer ma applica proprio blend/opacity/mask/channels.

### 7.1 [Crit][Reg] Reference contribuisce al Base Color
**Setup**: Fill 2 con colore rosso visibile. Add Reference → reference_layer_name = "Fill 2". Nessun'altra channel abilitata.
**Expected**: Il Reference contribuisce al base color (Base Color è sempre implicitamente on per ogni layer). Shader Editor: compare un nuovo fill node + mix node.
**Bug risolto 2026-04-20**: `_build_base_color` mancava della branch REFERENCE → Reference silenziosamente skippato per base_color.
☐ Pass ☐ Fail — Note:

### 7.1b [Crit] Reference a Voronoi
**Setup**: Voronoi layer → Add Reference → reference_layer_name = "Voronoi".
**Expected**: Reference mostra lo stesso pattern della sorgente.
☐ Pass ☐ Fail — Note:

### 7.2 Reference con blend diverso
**Setup**: Voronoi + Reference che punta a Voronoi. Voronoi: blend MIX. Reference: blend MULTIPLY.
**Expected**: Reference moltiplica il composite, pattern identico.
☐ Pass ☐ Fail — Note:

### 7.3 Reference scrive su altro canale
**Setup**: Voronoi base color. Reference con SOLO Roughness ON.
**Expected**: Stesso pattern → driva roughness invece di color.
☐ Pass ☐ Fail — Note:

### 7.4 Reference a layer inesistente
**Setup**: Reference con reference_layer_name = "NonEsisto".
**Expected**: Layer ignorato silenziosamente. Nessun crash.
☐ Pass ☐ Fail — Note:

### 7.5 Reference a se stesso → no infinite loop
**Setup**: Prova a impostare reference al proprio nome.
**Expected**: O impedito dall'UI o ignorato senza loop.
☐ Pass ☐ Fail — Note:

---

## 8. Groups

### 8.1 [Bug noto] Create group + move layer inside
**Setup**: Add Group → crea layer Fill → Move to Group.
**Expected**: Layer compare nested sotto il group. Indentazione visibile nella UIList.
**Bug rilevato 2026-04-22**: nei gruppi, le lampadine (solo) e l'occhio (visibility) non funzionano sul group header — si replica? Verificare e creare ticket fix.
☐ Pass ☐ Fail — Note:

### 8.2 Group collapse / expand
**Setup**: Click triangolo sul group.
**Expected**: Toggle show/hide dei children nella UIList.
☐ Pass ☐ Fail — Note:

### 8.3 [Bug noto] Group opacity
**Setup**: Group con 2 children colorati. Group opacity = 0.3.
**Expected**: Tutto il gruppo semitrasparente rispetto al fondo.
**Bug rilevato 2026-04-22**: "Non funziona" — verifica path `_build_group_chain` / mix factor sul group composite.
☐ Pass ☐ Fail — Note:

### 8.4 [Bug noto] Group blend mode
**Setup**: Group blend = MULTIPLY.
**Expected**: Tutto il gruppo moltiplica come un unico layer.
**Bug rilevato 2026-04-22**: "Non funziona" — il blend_mode sul group header probabilmente non viene letto da `_effective_blend_mode`.
☐ Pass ☐ Fail — Note:

### 8.5 [Bug noto] Group mask
**Setup**: Group con mask (immagine o smart).
**Expected**: Mask si applica a tutto il gruppo.
**Bug rilevato 2026-04-22**: "Non presente l'opzione" — il pannello GROUP in `_draw_active_layer` chiama `_draw_mask_block(col, active)` ma il group potrebbe non avere `use_mask` esposto o il path di compositing non lo legge. Verifica.
☐ Pass ☐ Fail — Note:

### 8.6 Remove from Group
**Setup**: Layer dentro group → Remove from Group.
**Expected**: Layer torna al root level, sopra il group.
☐ Pass ☐ Fail — Note:

### 8.7 Delete group with children
**Setup**: Group con 2 children → Remove group.
**Expected**: Group rimosso, children orfani (root level), NON cancellati.
☐ Pass ☐ Fail — Note:

### 8.8 [Rimuovere se non supportato] Nested groups
**Setup**: Group dentro Group.
**Expected**: Funziona o errore chiaro.
**Bug rilevato 2026-04-22**: "Non funziona, rimuovere". L'architettura attuale considera GROUP sempre root-level (vedi `rebuild_node_tree` → `_is_root`). Decisione: documentare come non-supportato + nascondere l'option da UI, o implementare correttamente.
☐ Pass ☐ Fail — Note:

---

## 9. Masks — Primary (Mask A)

`mask_source` (11 sorgenti): IMAGE / AO / POINTINESS / WIREFRAME / EDGE_WEAR / DIRT / CURVATURE_SMART / FRESNEL / NDOTL / NDOTH / VORONOI.

**UI nota 2026-05-28**: la sezione Mask è ora una collapsible **sempre visibile** (anche quando `use_mask=False`). Quando OFF, il box mostra "No mask configured" + buttons Add Mask / Bake Smart Mask. Il toggle nel header (icona MOD_MASK) flipa `use_mask` senza perdere settings.

### 9.1 [Crit] Mask IMAGE (bianca default)
**Setup**: Layer Fill → Add Mask (bianca).
**Expected**: Layer pienamente visibile. Immagine mask bianca creata.
☐ Pass ☐ Fail — Note:

### 9.2 Dipingi sulla mask
**Setup**: Seleziona layer → Texture Paint → dipingi nero sulla mask.
**Expected**: Zone nere = layer invisibile. Update live.
☐ Pass ☐ Fail — Note:  non ho capito cosa devo fare 

### 9.3 Mask source = AO (live)
**Setup**: Fill → mask_source = AO. Distance = 1.0.
**Expected**: Layer visibile solo nelle cavità. Live (no bake).
☐ Pass ☐ Fail — Note:

### 9.4 Mask source = POINTINESS (live)
**Setup**: mask_source = POINTINESS.
**Expected**: Layer visibile sui bordi convessi.
☐ Pass ☐ Fail — Note:

### 9.5 Mask source = EDGE_WEAR (live)
**Setup**: mask_source = EDGE_WEAR.
**Expected**: Banda stretta sui soli bordi convessi nitidi.
☐ Pass ☐ Fail — Note:

### 9.6 [Reg] Mask source = DIRT (live) — uses AO
**Setup**: mask_source = DIRT.
**Expected**: Sporco accumulato nelle cavità (AO invertito).
**Bug risolto 2026-04-20**: `ao.outputs["AO"]` (era "Fac").
☐ Pass ☐ Fail — Note:

### 9.7 Mask source = CURVATURE_SMART (live)
**Setup**: mask_source = CURVATURE_SMART.
**Expected**: Bordi convessi E concavi.
☐ Pass ☐ Fail — Note:

### 9.8 Invert Mask A
**Setup**: Con qualsiasi mask → checkbox Invert A.
**Expected**: Mask invertita (bianco↔nero).
☐ Pass ☐ Fail — Note:

### 9.9 Mask AO distance slider (hot-path)
**Setup**: Sposta `mask_ao_distance` con mask AO attiva.
**Expected**: Update live (anche se lento per via del sampling).
☐ Pass ☐ Fail — Note:

### 9.10 [New] Mask source = WIREFRAME
**Setup**: mask_source = WIREFRAME.
**Expected**: Mask bianca sui bordi reali della mesh (segue topologia/triangulation, non un pattern fake). Slider `mask_wireframe_size` controlla spessore.
☐ Pass ☐ Fail — Note:

### 9.11 [New] Mask source = FRESNEL
**Setup**: mask_source = FRESNEL.
**Expected**: Mask = view-angle (0 facing camera, 1 grazing silhouette). IOR slider visibile. Caso d'uso: iridescent / oil-slick / hologram.
☐ Pass ☐ Fail — Note:

### 9.12 [New] Mask source = NDOTL (cel-shading)
**Setup**: Aggiungi una Sun light alla scena. mask_source = NDOTL.
**Expected**: Mask = Normal · SunDir, remappata in [0,1]. 1 = pieno sole, 0 = ombra. Combina con proc_contrast=1.0 + ColorRamp manual stops per cel-shading binario.
**Hot-update**: ruotare la Sun deve rinfrescare via `hot_update_sun_direction` (depsgraph handler).
☐ Pass ☐ Fail — Note:

### 9.13 [New] Mask source = NDOTH (toon specular)
**Setup**: Sun light + Camera + mask_source = NDOTH.
**Expected**: Mask peakata sul classico Phong specular spot (tra sole e camera). Per highlights stilizzati anime sparkle.
☐ Pass ☐ Fail — Note:

### 9.14 [New] Mask source = VORONOI
**Setup**: mask_source = VORONOI. Feature = F1 or DISTANCE_TO_EDGE. Scala = condivisa con il proc_scale del layer (per allineamento cella↔pattern).
**Expected**: Mask Voronoi. Caso d'uso: cobblestone — stone Voronoi (colore) + dirt Voronoi (sporco fra le pietre) con SAME scale.
☐ Pass ☐ Fail — Note:

### 9.15 [New] mask_invert flip
**Setup**: Mask configurata → flip `mask_invert`.
**Expected**: Mask invertita.
☐ Pass ☐ Fail — Note:

### 9.16 [New] Mask collapsible sempre visibile
**Setup**: Layer senza mask configurata (use_mask=False).
**Expected**: Header "Mask" sempre presente nel pannello. Click → box espande mostrando "No mask configured" + buttons Add Mask / Bake Smart Mask.
**Reg 2026-05-28**: prima del fix UI Phase 2 il header appariva solo con use_mask=True.
☐ Pass ☐ Fail — Note:

---

## 10. Masks — Secondary (Mask B) + Combining

Mask B ha le stesse sorgenti di A (live). Combining: AND / OR / MULTIPLY / ADD / SUBTRACT.

### 10.1 Add Secondary Mask
**Setup**: Con Mask A già presente → Add Secondary Mask (B) → AO.
**Expected**: Slot B attivato. Dropdown mostra source.
☐ Pass ☐ Fail — Note:

### 10.2 Combine AND
**Setup**: A = AO, B = POINTINESS, combine = AND (Multiply).
**Expected**: Layer visibile SOLO dove entrambe le mask sono >0.
☐ Pass ☐ Fail — Note:

### 10.3 Combine OR
**Setup**: Combine = OR (Add/Screen).
**Expected**: Layer visibile dove UNA delle due è >0.
☐ Pass ☐ Fail — Note:

### 10.4 Invert B
**Setup**: Invert B checkbox.
**Expected**: B invertita prima del combine.
☐ Pass ☐ Fail — Note:

### 10.5 Remove B
**Setup**: Rimuovi B.
**Expected**: Solo A resta attiva. Nessun crash.
☐ Pass ☐ Fail — Note:

---

## 11. Smart Masks (Baked via "Add Smart Mask")

4 tipi bakati: **AO, CURVATURE, FACING, HEIGHT**. Richiedono: mesh + UV + Cycles.

### 11.1 [Crit] Bake AO Smart Mask
**Setup**: Layer Fill → Add Smart Mask → Type = Ambient Occlusion → Distance 1.0, Samples 16 → OK.
**Expected**:
- Progress bar durante bake
- Immagine `LayerName_SmartMask_AO` creata
- mask_source auto-settato a IMAGE con questa texture
- Layer visibile nelle cavità (come AO live, ma bakato)
☐ Pass ☐ Fail — Note:

### 11.2 Bake Curvature Smart Mask
**Setup**: Type = Curvature → Ridge 1.0, Valley 1.0.
**Expected**: Texture che evidenzia bordi.
☐ Pass ☐ Fail — Note:

### 11.3 Bake Facing Angle
**Setup**: Type = Facing Angle.
**Expected**: Texture chiara/scura basata su orientamento.
☐ Pass ☐ Fail — Note:

### 11.4 Bake Height
**Setup**: Type = Height.
**Expected**: Gradient basato su Z.
☐ Pass ☐ Fail — Note:

### 11.5 [Reg] Bake senza UV → errore chiaro
**Setup**: Mesh senza UV → Add Smart Mask.
**Expected**: Errore "`Mesh has no UV map. Unwrap it first`". No crash, no orphan image.
☐ Pass ☐ Fail — Note:

### 11.6 [Reg] Bake senza active object → errore chiaro
**Setup**: Deseleziona tutto → Add Smart Mask.
**Expected**: Errore "No active object". No crash.
☐ Pass ☐ Fail — Note:

### 11.7 [Reg] Bake fallito → no orphan images
**Setup**: Simula fail (es. annulla durante bake, o mesh senza facce).
**Expected**: Nessuna immagine orfana in bpy.data.images. `_BakeGuard` ha pulito.
☐ Pass ☐ Fail — Note:

### 11.8 [Reg] Render engine restored dopo bake
**Setup**: Prima del bake, render engine = Eevee. Bake → controlla engine post-bake.
**Expected**: Tornato a Eevee (BakeGuard restore).
☐ Pass ☐ Fail — Note:

### 11.9 [Reg] Node selection restored dopo bake
**Setup**: Seleziona un nodo specifico nello Shader Editor. Esegui Smart Mask bake.
**Expected**: Selezione + active node preservati dopo il bake.
☐ Pass ☐ Fail — Note:

---

## 12. Clipping Mask

Un layer diventa visibile SOLO dove il layer sotto ha alpha >0.

### 12.1 Enable Clipping Mask
**Setup**: Layer A (sotto) con mask, Layer B (sopra) con Clipping Mask ON.
**Expected**: B visibile solo dove A è visibile (clipped dal suo alpha).
☐ Pass ☐ Fail — Note:

### 12.2 Clipping chain (3 layer)
**Setup**: A, B, C tutti con clipping ON.
**Expected**: B clipped da A, C clipped da B (o tutto il clipping group).
☐ Pass ☐ Fail — Note:

---

## 13. Fresnel Rim (per-layer)

Modulatore view-angle che pesa l'opacità del layer. **UI rilocato 2026-05-28**: ora dentro la collapsible **Surface Effects** (non più riga inline post-mask).

### 13.1 Fresnel Strength slider
**Setup**: Fill layer → expand "Surface Effects" → toggle "Enable" sotto Fresnel Rim → Strength > 0.
**Expected**: Layer più visibile sui bordi (glancing angle).
☐ Pass ☐ Fail — Note:

### 13.2 Fresnel IOR
**Setup**: Cambia IOR da 1.45 a 3.0.
**Expected**: Effetto più pronunciato.
**Reg 2026-05-xx**: Fresnel UX bug — con default contrast/center, color2 era invisibile. Fixato.
☐ Pass ☐ Fail — Note:

### 13.3 [Removed] Fresnel Invert
**Stato**: rimosso. Per invertire il rim, usa `mask_invert` su una mask FRESNEL-source o flip color1/color2.
☐ Pass ☐ Fail — Note: N/A

### 13.4 [New] Fresnel su tutti i tipi di layer
**Setup**: PAINT / FILL / PROCEDURAL / REFERENCE → Surface Effects → Enable Fresnel.
**Expected**: Funziona su tutti e 4. ADJUSTMENT e GROUP non hanno Surface Effects (skippano).
☐ Pass ☐ Fail — Note:

---

## 14. Coordinates

`proc_coords`: UV / GENERATED / OBJECT / NORMAL. `coord_preset`: presets (Custom, Seamless, etc.).

### 14.1 Coords = UV
**Setup**: Procedural → Coords = UV.
**Expected**: Usa UV map della mesh.
☐ Pass ☐ Fail — Note:

### 14.2 Coords = GENERATED
**Setup**: Coords = Generated.
**Expected**: Normalizza a bbox 0-1 → distorto su oggetti non-cubici.
☐ Pass ☐ Fail — Note:

### 14.3 Coords = OBJECT (default)
**Setup**: Coords = Object.
**Expected**: Usa coordinate object-space, no distorsione.
☐ Pass ☐ Fail — Note:

### 14.4 Coords = NORMAL
**Setup**: Coords = Normal.
**Expected**: Pattern proiettato dalla normale (tri-planar-like).
☐ Pass ☐ Fail — Note:

### 14.5 Normalize Scale
**Setup**: Coords = Object, Normalize Scale ON. Scala oggetto 2x.
**Expected**: Pattern NON cambia scala con l'oggetto.
☐ Pass ☐ Fail — Note:

### 14.6 Offset X/Y/Z
**Setup**: Regola offset.
**Expected**: Pattern trasla. Hot-path, no rebuild.
☐ Pass ☐ Fail — Note:

### 14.7 Coord Preset
**Setup**: Prova i preset disponibili.
**Expected**: Preset applica set di valori coerenti.
☐ Pass ☐ Fail — Note:

---

## 15. Bake PBR (Export del risultato finale)

### 15.1 [Crit] Bake tutti i canali
**Setup**: Stack multi-layer completo → TLM Bake PBR → seleziona Base Color, Roughness, Metallic, Normal → scegli cartella output → OK.
**Expected**:
- 4 immagini PNG salvate in cartella
- Naming coerente (Material_BaseColor.png, etc.)
- Contenuto corretto (non nero, non bianco puro)
☐ Pass ☐ Fail — Note:

### 15.2 Bake single channel
**Setup**: Solo Base Color selezionato.
**Expected**: Solo quella immagine generata.
☐ Pass ☐ Fail — Note:

### 15.3 Bake resolution
**Setup**: Cambia resolution (512/1024/2048/4096).
**Expected**: Output al resolution richiesto.
☐ Pass ☐ Fail — Note:

### 15.4 [Reg] Bake preflight errore UV mancante
**Setup**: Mesh senza UV → Bake PBR.
**Expected**: Errore chiaro, no orphan images.
☐ Pass ☐ Fail — Note:

### 15.5 [Reg] Bake fail parziale → no orphan
**Setup**: Bake fallisce a metà canali.
**Expected**: Canali completati salvati, quelli falliti NON creano immagini orfane.
☐ Pass ☐ Fail — Note:

### 15.6 Normal map bake (tangent space)
**Setup**: Stack con normal maps → Bake Normal.
**Expected**: Normal map bakata in tangent space standard (azzurrina).
☐ Pass ☐ Fail — Note:

---

## 16. Channel Pack

Combina canali in RGBA per ottimizzare texture memory (es. Roughness in R, Metallic in G, AO in B).

### 16.1 Channel Pack base
**Setup**: TLM Channel Pack → assegna Roughness→R, Metallic→G, AO→B.
**Expected**: Singola immagine RGB con 3 canali packati.
☐ Pass ☐ Fail — Note:

---

## 17. Import / Export

### 17.1 Import Texture As Layer
**Setup**: Import Texture → seleziona file immagine → OK.
**Expected**: Nuovo Paint layer con quella immagine.
☐ Pass ☐ Fail — Note:

### 17.2 Import PBR Texture Set
**Setup**: Cartella con BaseColor, Roughness, Metallic, Normal (naming convention) → Import PBR Set.
**Expected**: Auto-riconoscimento dei canali, creazione layer configurato con tutti i canali.
☐ Pass ☐ Fail — Note:

### 17.3 Export JSON
**Setup**: Stack con vari layer → Export JSON.
**Expected**: File .json valido con dump di tutti i layer e properties.
☐ Pass ☐ Fail — Note:

### 17.4 Import JSON
**Setup**: Material nuovo → Import JSON del precedente.
**Expected**: Stack ricreato identico.
☐ Pass ☐ Fail — Note:

### 17.5 Layer from Clipboard
**Setup**: Layer JSON nel clipboard → Layer from Clipboard.
**Expected**: Layer creato da clipboard content.
☐ Pass ☐ Fail — Note:

---

## 18. Presets (.tlm files)

Presets attuali in `presets/` (28 totali, 2026-05-28):

**Hero series (curati, da spedire)**:
- Anime Cel-Shaded (Hero G v3 Integrated Outline)
- Anime Genshin Hero (v7 - High Contrast Anime Palette)
- Autumn Decaying Leaf (Hero E - Alpha)
- Bronze Verdigris Patina (Hero D)
- Brushed Metal Gabor (Hero B)
- Cracked Lava Crust (Hero I - CRACKS + Emission)
- Crystal Geode (Hero M - Random Color + Random Cells)
- Damascus Steel (Hero H - Folded Blade)
- Frozen Ice Glass (Hero P v6 - Full Volume + IOR persistent)
- Galaxy Marble (Hero K - 3-Color Ramp + MARBLE)
- Hero_Q_Rocky_Chunky_Pile
- Iridescent Rainbow Foil (Hero F2 - FRESNEL proc)
- Iridescent Rim (Hero F - Fresnel Single Color)
- Sci-Fi Orange Crate (Hero N - BRICK + Selective Emission)
- Anime Cel-Shaded NdotL (Hero G v2)
- Anime Cel-Shading (Persistent Emission v5)
- Anime Genshin Hero (v6 - Persistent + Wide Bands)

**Control / debug**:
- Control, Control resonant, Sci fi panel

**Dev junk (DA RIMUOVERE prima del release — task #8)**:
- Boh intanto lo salvo sembra adasd
- Fuoco
- hex strano
- Ice2
- Quasi sabbia
- Wireframe
- fiber
- sfera

### 18.1 [Crit] Apply Preset (Hero P — Frozen Ice Glass)
**Setup**: Material vuoto → Apply Preset → "Frozen Ice Glass (Hero P v6 ...)".
**Expected**: Stack ghiaccio caricato. Viewport mostra cristallo trasparente con volume scatter. BSDF.Base Color cablata, IOR=1.31, Volume Absorption ON.
☐ Pass ☐ Fail — Note:

### 18.2 Save Preset
**Setup**: Stack custom → Save Preset → nome "Test Preset" → OK.
**Expected**: File `Test Preset.tlm` compare in `presets/`. Visibile nel dropdown.
☐ Pass ☐ Fail — Note:

### 18.3 Delete Preset
**Setup**: Delete Preset → "Test Preset".
**Expected**: File rimosso, dropdown aggiornato.
☐ Pass ☐ Fail — Note:

### 18.4 Apply preset su material con layer esistenti
**Setup**: Material con 3 layer → Apply Preset.
**Expected**: Chiede conferma (sostituisci/aggiungi?) o comportamento documentato.
☐ Pass ☐ Fail — Note:

### 18.5 Preset con mask source AO/DIRT
**Setup**: Apply "Rusted Iron" o altro con DIRT/AO masks.
**Expected**: [Post-fix 2026-04-20] Nessun KeyError "Fac". Render corretto.
☐ Pass ☐ Fail — Note:

---

## 19. Thumbnails / Previews

### 19.1 Thumbnail per Paint layer
**Setup**: Paint layer dipinto.
**Expected**: Thumbnail mostra contenuto immagine nella UIList.
☐ Pass ☐ Fail — Note:

### 19.2 Thumbnail invalidation
**Setup**: Dipingi sul layer → thumbnail si aggiorna.
**Expected**: Dopo brush stroke (o commit), thumbnail rigenerata entro ~1s.
☐ Pass ☐ Fail — Note:

### 19.3 Refresh Thumbnails operator
**Setup**: Thumbnail corrotta → operatore Refresh Thumbnails.
**Expected**: Tutte rigenerate.
☐ Pass ☐ Fail — Note:

### 19.4 Thumbnail per Fill / Procedural
**Setup**: Layer non-paint.
**Expected**: Thumbnail rappresentativo (colore fill, preview procedurale o icona).
☐ Pass ☐ Fail — Note:

---

## 20. Hot Updates (performance)

Properties che usano `_make_hot_callback(...)` NON triggerano rebuild del tree.
Esempi: opacity, proc_scale, proc_contrast, blend_mode (sì rebuild ma ottimizzato), adj_hue, adj_saturation, adj_value.

### 20.1 [Crit] Opacity slider fluido
**Setup**: Stack 10+ layer → trascina opacity slider velocemente.
**Expected**: Nessuno stutter visibile. Update real-time.
☐ Pass ☐ Fail — Note:

### 20.2 Procedural scale slider fluido
**Setup**: Procedural → trascina scale.
**Expected**: Fluido. Debounce 180ms aggregazione.
☐ Pass ☐ Fail — Note:

### 20.3 Adjustment sliders fluidi
**Setup**: Hue/Sat adjustment → sposta hue/saturation/value.
**Expected**: Fluido.
☐ Pass ☐ Fail — Note:

### 20.4 Rebuild triggered da structural change
**Setup**: Cambia blend mode → verifica Shader Editor.
**Expected**: Rebuild veloce (<100ms). Console può loggare "TLM rebuild".
☐ Pass ☐ Fail — Note:

---

## 21. Undo / Redo

### 21.1 Undo add layer
**Setup**: Add Fill → Ctrl+Z.
**Expected**: Layer rimosso. Stack consistente.
☐ Pass ☐ Fail — Note:

### 21.2 Undo remove layer
**Setup**: Remove layer → Ctrl+Z.
**Expected**: Layer ripristinato con tutte properties.
☐ Pass ☐ Fail — Note:

### 21.3 Undo paint stroke
**Setup**: Paint layer → stroke → Ctrl+Z.
**Expected**: Stroke rimosso. Thumbnail refresh.
☐ Pass ☐ Fail — Note:

### 21.4 Undo slider change
**Setup**: Cambia opacity da 1 a 0.5 → Ctrl+Z.
**Expected**: Torna a 1.0.
☐ Pass ☐ Fail — Note:

---

## 22. Multi-material / Edge cases

### 22.1 Oggetto con 2 material slot
**Setup**: Mesh con 2 slot, entrambi con stack TLM diverso.
**Expected**: Active material drive il pannello. Switch slot → pannello aggiornato.
☐ Pass ☐ Fail — Note:

### 22.2 Due oggetti stesso material
**Setup**: 2 plane stesso material TLM.
**Expected**: Edit su uno → vedi su entrambi.
☐ Pass ☐ Fail — Note:

### 22.3 Copia material (duplicate)
**Setup**: Material → "Make Single User" (material.copy()).
**Expected**: Stack TLM duplicato pulitamente (con immagini? da documentare).
☐ Pass ☐ Fail — Note:

### 22.4 Salva/Riapri .blend file
**Setup**: Scena con TLM → salva → riapri.
**Expected**: Stack intatto, immagini linked correttamente, thumbnails ricaricati o rigenerati.
☐ Pass ☐ Fail — Note:

### 22.5 Delete del material
**Setup**: Remove material da slot.
**Expected**: Nessun crash. Immagini orfane con fake user NON cancellate (scelta di design).
☐ Pass ☐ Fail — Note:

### 22.6 Nessun active object
**Setup**: Deseleziona tutto → apri TLM panel.
**Expected**: Panel mostra messaggio "No active material" senza crash.
☐ Pass ☐ Fail — Note:

### 22.7 Active object non-mesh (light, camera)
**Setup**: Seleziona una Light → TLM panel.
**Expected**: Panel vuoto o messaggio informativo. No crash.
☐ Pass ☐ Fail — Note:

---

## 23. Resolution

### 23.1 Resolution change (material level)
**Setup**: `tlm.resolution` → da 1024 a 2048.
**Expected**: Nuovi Paint layers creati a 2048. Layer esistenti non resizati (documentato).
☐ Pass ☐ Fail — Note:

### 23.2 Resolution: 512 / 1024 / 2048 / 4096 / 8192
**Setup**: Prova ciascuno con un Paint layer.
**Expected**: Immagine creata al resolution giusto.
☐ Pass ☐ Fail — Note:

---

## 24. Keyframing / Animation

### 24.1 Keyframe opacity
**Setup**: Layer opacity = 0 a frame 1. Keyframe → frame 30 opacity 1.0 → keyframe.
**Expected**: Animation da invisible a visible. F-curve in Graph Editor.
☐ Pass ☐ Fail — Note:

### 24.2 Render animation con TLM
**Setup**: Render 10 frame di animazione keyframata.
**Expected**: Output coerente (nessun rebuild interrompe il render).
☐ Pass ☐ Fail — Note:

---

## 25. Smoke Tests — Scenari Reali

### 25.1 Material metallo usurato (manuale)
**Setup**: Segui il piano Weathered Bronze (6 layer: Bronze Base + Variation + Patina Wash + Patina Patches + Dirt + Edge Highlights) come istruzioni in doc.
**Expected**: Look credibile di bronzo patinato.
☐ Pass ☐ Fail — Note:

### 25.2 Material stone con displacement (bump only)
**Setup**: 1 Procedural Noise con Bump channel + 1 Fill base color grigio.
**Expected**: Pietra con micro-rilievo visibile.
☐ Pass ☐ Fail — Note:

### 25.3 Material painted wood
**Setup**: 1 Fill wood color + 1 Procedural Wave (vene) + 1 Paint layer per usura.
**Expected**: Legno verniciato credibile.
☐ Pass ☐ Fail — Note:

### 25.4 Material emissive sci-fi
**Setup**: 1 Fill dark base + 1 Procedural Voronoi con Emission ON (celle luminose).
**Expected**: Pannello sci-fi con celle che emettono.
☐ Pass ☐ Fail — Note:

---

## 26. [New] Material-Level Settings

Properties material-level (su `mat.tlm`) che non appartengono a un singolo layer ma all'intero materiale. Pannello: Composite section + shortcut dentro Surface Effects (per Volume).

### 26.1 [Crit] BSDF IOR
**Setup**: TLM panel → Composite → Surface → IOR slider.
**Expected**: Default 1.45. Range 1.0–3.0. Si propaga a `BSDF.IOR` socket. Test: 1.31 (ice), 1.33 (water), 1.50 (glass), 2.42 (diamond).
**Reg 2026-05-xx**: serializzato in .tlm/JSON sotto `material.bsdf_ior`.
☐ Pass ☐ Fail — Note:

### 26.2 [Crit] Volume Absorption toggle
**Setup**: Composite → Volume → Absorption ON. Color = ciano, Density = 1.0.
**Expected**: Volume Absorption node creato + collegato a Material Output.Volume. Oggetto trasparente assorbe luce ciano (cyan→arancio nei tratti spessi).
☐ Pass ☐ Fail — Note:

### 26.3 Volume Scatter toggle
**Setup**: Composite → Volume → Scatter ON. Color bianco, Density = 0.5, Anisotropy = 0.
**Expected**: Volume Scatter node creato. Effetto cloudy/milky.
☐ Pass ☐ Fail — Note:

### 26.4 [Reg] Absorption + Scatter insieme
**Setup**: Entrambi ON.
**Expected**: Add Shader combina i due output → Material Output.Volume. Verifica nello shader editor: TLM_volume_combine_N node tipo ShaderNodeAddShader.
☐ Pass ☐ Fail — Note:

### 26.5 Volume toggle OFF teardown
**Setup**: Disattiva entrambi.
**Expected**: Tutti i ShaderNodeVolumeAbsorption/Scatter/AddShader con prefix TLM_volume_* rimossi. Material Output.Volume socket vuoto.
☐ Pass ☐ Fail — Note:

### 26.6 Volume shortcut dentro Surface Effects (per-layer)
**Setup**: Su un layer PROCEDURAL → Surface Effects collapsible → la sezione Volume mostra le stesse property material-level.
**Expected**: Toggle qui = toggle in Composite (stesso datablock). Verifica bidirezionalità.
☐ Pass ☐ Fail — Note:

### 26.7 [Reg] Material-level props persistenti in .tlm
**Setup**: Set bsdf_ior=1.5 + volume_absorption ON + custom color → Save Preset → load su nuovo materiale.
**Expected**: Tutte e 3 le property ripristinate.
☐ Pass ☐ Fail — Note:

---

## 27. [New] Geometric Displacement

Sposta veri vertici via Material Output.Displacement (Cycles only, per ora). Master toggle `mat.tlm.use_displacement` + per-layer `layer.use_displacement` cumulativi.

### 27.1 [Crit] Master + per-layer toggle
**Setup**: Subdivide la mesh (mod Subdiv viewport ≥4 + render). Procedural Noise → Surface Effects → "Add to Displace" ON.
**Expected**: 
- Auto-enable del master via `_on_layer_use_displacement_change`.
- Cycles render mostra rilievo geometrico vero.
- Material-level shared box visibile sotto: Method/Strength/Midlevel/Adaptive.
☐ Pass ☐ Fail — Note:

### 27.2 Master OFF mentre layer ON → warning
**Setup**: Layer use_displacement=True, poi disattiva il master in Composite.
**Expected**: Warning row "Master Displacement OFF — layer is silent" mostrato dentro Surface Effects per quel layer.
☐ Pass ☐ Fail — Note:

### 27.3 Displacement methods enum
**Setup**: Cambia displacement_method tra BUMP / DISPLACEMENT / BOTH.
**Expected**: 
- BUMP: solo perturba la normale (no vertex move). Funziona anche in Eevee.
- DISPLACEMENT: muove vertici (richiede Cycles + subdivision).
- BOTH: combo.
- "Auto Adaptive Subdiv" toggle visibile solo se method ≠ BUMP.
☐ Pass ☐ Fail — Note:

### 27.4 Strength + Midlevel
**Setup**: Strength 0.1 → 1.0. Midlevel 0.5 → 0.3.
**Expected**: Strength scala l'intensità. Midlevel decide cosa è "zero displacement" nel grayscale.
☐ Pass ☐ Fail — Note:

### 27.5 [Reg] Cumulative across layers
**Setup**: 2 layer procedural entrambi con use_displacement, scale 1.0 e 0.5.
**Expected**: Combine cumulativo (somma) nello stack displacement.
☐ Pass ☐ Fail — Note:

### 27.6 Displacement adaptive subdiv (Cycles)
**Setup**: Method = DISPLACEMENT or BOTH. Auto Adaptive Subdiv ON.
**Expected**: Adaptive subdivision attivata automaticamente sull'oggetto (cycles object property).
☐ Pass ☐ Fail — Note:

---

## 28. [New] Anime / Cel-Shading

Toolkit per look toon — NdotL mask source + integrated outline + emission bypass.

### 28.1 [Crit] Apply preset "Anime Cel-Shaded (Hero G v3 Integrated Outline)"
**Setup**: Material vuoto + Sun light in scena → Apply preset.
**Expected**: Banded shading hard, outline visibile attorno al modello.
☐ Pass ☐ Fail — Note:

### 28.2 NdotL mask + ColorRamp manual stops binario
**Setup**: Procedural FRESNEL (o Fill) → mask_source = NDOTL → ColorRamp manual stops 2 zone (es. pos 0.5 split).
**Expected**: Hard cell shading (no gradient, banding netto).
☐ Pass ☐ Fail — Note:

### 28.3 5-zone Genshin-style terminator
**Setup**: Procedural FRESNEL + NDOTL → 5 color stops (Shadow / Dark Mid / Mid / Light Mid / Highlight).
**Expected**: Banding stile Genshin con 5 step di luminosità.
☐ Pass ☐ Fail — Note:

### 28.4 Integrated shader outline
**Setup**: Apply preset Hero G v3 → l'outline è generato DENTRO lo shader (no Solidify modifier, no slot extra).
**Expected**: Outline visibile. Verifica: niente modifier Solidify sulla mesh, slot material singolo.
☐ Pass ☐ Fail — Note:

### 28.5 [Helper] setup_inverted_hull_outline()
**Setup**: Da Python: `bpy.ops.tlm.setup_inverted_hull_outline(...)`.
**Expected**: Helper crea un secondo slot material con BSDF nero + flip normal + Solidify modifier. Alternativa allo shader-integrated quando vuoi più controllo.
☐ Pass ☐ Fail — Note:

### 28.6 [Helper] apply_emission_bypass()
**Setup**: Operator `tlm.apply_emission_bypass`.
**Expected**: Per cel-shading flat. La luce viene catturata dalla mask NdotL e iniettata via Emission, BSDF saltato. Risultato: piatto/anime senza dipendenza da BSDF lambert.
☐ Pass ☐ Fail — Note:

### 28.7 mat.tlm.use_emission_output persistente
**Setup**: Apply emission bypass → save .blend → reopen.
**Expected**: Flag `use_emission_output` persistente sul material. Rebuild rispetta lo stato.
☐ Pass ☐ Fail — Note:

### 28.8 [Reg] NdotL hot-update su Sun rotation
**Setup**: Layer con mask NDOTL. Ruota la Sun light.
**Expected**: Mask si aggiorna in viewport tramite depsgraph handler (`hot_update_sun_direction`).
**Bug fix 2026-05-28**: dopo Phase 2 refactor, `_find_first_sun_direction` non era importato in masks.py. Verifica che funzioni post-fix.
☐ Pass ☐ Fail — Note:

---

## 29. [New] Alpha Channel

`output_channel = ALPHA` su un layer scrive sul socket BSDF.Alpha invece di Base Color/etc.

### 29.1 [Crit] Output channel = ALPHA su Fill
**Setup**: Fill layer → Output dropdown → ALPHA. blend_mode → ALPHA usa `alpha_math_operation` (MULTIPLY/ADD/SUBTRACT/MIN/MAX), non i blend artistici.
**Expected**: Material diventa trasparente dove fill_color → alpha. Eevee+Cycles compatibile via wrap Mix Shader+Transparent BSDF.
☐ Pass ☐ Fail — Note:

### 29.2 alpha_math_operation = MULTIPLY (default)
**Setup**: 2 Fill alpha, valori 0.5 e 0.5.
**Expected**: Risultato 0.25 (moltiplicato).
☐ Pass ☐ Fail — Note:

### 29.3 alpha_math_operation = ADD
**Setup**: 2 Fill alpha, ADD.
**Expected**: Somma clampata.
☐ Pass ☐ Fail — Note:

### 29.4 [Crit] Procedural su ALPHA channel
**Setup**: Procedural NOISE → Output = ALPHA.
**Expected**: Buchi/transparency seguono il pattern. Hard via proc_contrast=1.0 ColorRamp.
**Reg 2026-05-xx**: prima del fix, NOISE → ALPHA non generava buchi visibili.
☐ Pass ☐ Fail — Note:

### 29.5 use_base_color_alpha (material-level)
**Setup**: `mat.tlm.use_base_color_alpha = True` (Composite → Surface → "Use Paint Alpha").
**Expected**: Il canale Alpha del Paint layer (alpha del PNG) viene usato come opacity globale del materiale.
☐ Pass ☐ Fail — Note:

### 29.6 alpha_blend_method AUTO/CLIP/HASHED/BLEND/OPAQUE
**Setup**: Composite → Alpha Mode dropdown.
**Expected**: 
- AUTO (default): HASHED se alpha cablata, OPAQUE altrimenti.
- Forced values override AUTO.
- Su Blender 4.2+/5.0 mappa a surface_render_method.
☐ Pass ☐ Fail — Note:

### 29.7 Hero E "Autumn Decaying Leaf"
**Setup**: Apply preset.
**Expected**: Foglia con buchi/strappi (alpha-driven via NOISE proc).
☐ Pass ☐ Fail — Note:

---

## 30. [New] ColorRamp UI Controls (Procedural)

Color Ramp di un Procedural ora ha controlli completi: Mode (RGB/HSV/HSL...) + Interpolation (Linear/Constant/Ease/...), Manual Stops toggle, posizioni manuali, multi-stop (3+).

### 30.1 [Crit] Manual Stops toggle
**Setup**: Procedural NOISE → Color Ramp collapsible → toggle "Manual Stops" ON.
**Expected**: Slider Pos 1 / Pos 2 appaiono accanto a Color 1 / Color 2. Auto Contrast/Center grayed out (greyed group).
☐ Pass ☐ Fail — Note:

### 30.2 [Crit] Manual Stops swap automatico
**Setup**: Manual Stops ON. Imposta Pos1 = 0.8, Pos2 = 0.3.
**Expected**: Swap automatico per mantenere monotonicità (Pos1 ≤ Pos2). Nessun collasso su singolo colore.
**Reg 2026-05-xx**: prima del fix, manual stops invertiti collassavano lo shader su singolo colore.
☐ Pass ☐ Fail — Note:

### 30.3 [Crit] Multi-color stops (3+)
**Setup**: Add Color Stop button → aggiungi 1-2 extra stops oltre Color 1/2. Imposta posizioni e colori distinti.
**Expected**: ColorRamp con 3-5 elementi visibili nel shader editor. Pos labels = "Pos 3", "Pos 4", ...
☐ Pass ☐ Fail — Note:

### 30.4 Remove extra stop
**Setup**: Stop con bottone X.
**Expected**: Rimosso. ColorRamp rebuilt con n-1 stops.
☐ Pass ☐ Fail — Note:

### 30.5 ColorRamp Mode (RGB/HSV/HSL)
**Setup**: Mode = HSV. Color1 = rosso, Color2 = blu.
**Expected**: Interpolazione passa attraverso violetti via HSV (non grigio come RGB).
☐ Pass ☐ Fail — Note:

### 30.6 ColorRamp Interpolation
**Setup**: Interp = Constant.
**Expected**: Banding hard (no smoothing). Per cel-shading.
☐ Pass ☐ Fail — Note:

### 30.7 [Reg] proc_ramp_center per asimmetria
**Setup**: Auto stops (Manual Stops OFF). proc_ramp_center = 0.7.
**Expected**: Pivot del ramp spostato a 0.7 — color1 occupa 70%, color2 30%.
☐ Pass ☐ Fail — Note:

### 30.8 [Reg] Hot-update non viola monotonicità
**Setup**: 4 color stops. Sposta Pos 2 oltre Pos 3 (via hot-update).
**Expected**: _hot_proc_color riordina/clamp gli elements in modo monotonic. Nessun crash.
**Bug fix 2026-05-xx**: prima del fix, `_hot_proc_color` lasciava elements non-monotonic → ColorRamp diventava buggy.
☐ Pass ☐ Fail — Note:

### 30.9 Manual Stops support per ALL proc using ColorRamp
**Setup**: NOISE, VORONOI, WAVE, MUSGRAVE, MARBLE, CRACKS, DOTS, RIDGED, GABOR, FRESNEL → toggle Manual Stops.
**Expected**: Funziona su tutti i procedural che usano ColorRamp. Skip su STRIPES, HEX_GRID, CHECKER, GRADIENT, MAGIC, BRICK (non usano la shared ColorRamp).
☐ Pass ☐ Fail — Note:

---

## 31. [New] Image Mapping (Paint / Fill PBR images)

Sezione collapsible "Image Mapping" che mirror i controlli del native ShaderNodeTexImage di Blender.

### 31.1 Image Source / Interpolation / Projection / Extension
**Setup**: Paint layer → expand "Image Mapping" → cambia ciascuno dei 4 dropdown.
**Expected**: 4 dropdown matching ShaderNodeTexImage (Source FILE/GENERATED, Interp Linear/Closest/Cubic/Smart, Projection FLAT/BOX/SPHERE/TUBE, Extension REPEAT/EXTEND/CLIP/MIRROR).
☐ Pass ☐ Fail — Note:

### 31.2 [Reg] Box Projection blend
**Setup**: Projection = BOX → slider "Blend" visibile.
**Expected**: Blend slider controlla la transizione fra le 3 proiezioni. Box projection sostituisce il custom Triplanar.
☐ Pass ☐ Fail — Note:

### 31.3 Location / Rotation / Scale per-axis
**Setup**: paint_location_x/y/z, paint_rotation_x/y/z, paint_scale_x/y/z.
**Expected**: Mapping node appare nello shader tree SOLO se i valori sono non-default (no Mapping node inutile per layer untouched).
☐ Pass ☐ Fail — Note:

### 31.4 [Reg] Paint canvas pixel preservation
**Setup**: Paint un PAINT layer → cambia output_channel da Base Color a Roughness.
**Expected**: I pixel dipinti SOPRAVVIVONO al colorspace flip. Nessun azzeramento del buffer.
**Bug fix history**: serie di fix (eed83d6, c5454bd) per Blender 5.0 image cache che azzera GENERATED images su colorspace change. Snapshot+restore in `_new_img_tex`.
☐ Pass ☐ Fail — Note:

---

## 32. [New] UI Phase 2 Structure

Verifica della riorganizzazione UI 2026-05-28.

### 32.1 [Crit] Surface Effects collapsible presente su PAINT/FILL/PROCEDURAL/REFERENCE
**Setup**: Crea un layer di ogni tipo. Verifica il pannello.
**Expected**: Tutti e 4 mostrano la collapsible "Surface Effects" con Fresnel + Displacement + Volume. ADJUSTMENT e GROUP NON mostrano Surface Effects (per design).
☐ Pass ☐ Fail — Note:

### 32.2 [Crit] Surface Effects badge counter
**Setup**: Su un layer, attiva Fresnel + use_displacement + volume_absorption.
**Expected**: Header mostra "Surface Effects (3)". Disattiva uno → "(2)".
☐ Pass ☐ Fail — Note:

### 32.3 [Crit] Mask collapsible sempre visibile
**Setup**: Layer con use_mask=False.
**Expected**: Header "Mask" presente. Click → box con "No mask configured" + 2 buttons.
**Reg 2026-05-28**: prima del fix, header solo con use_mask=True.
☐ Pass ☐ Fail — Note:

### 32.4 PAINT-only brush icon
**Setup**: Verifica la UIList con layer di vari tipi.
**Expected**: Icona BRUSH_DATA visibile SOLO sulla riga del PAINT layer. FILL/PROCEDURAL/REFERENCE non la mostrano.
**Reg 2026-05-28**: prima del fix, brush mostrato su tutto tranne GROUP/ADJUSTMENT.
☐ Pass ☐ Fail — Note:

### 32.5 Order delle collapsible consistente
**Setup**: Su ogni layer type, verifica l'ordine: blend+opacity+output → type-specific → Mapping/Color Ramp (se applicabile) → Mask → Surface Effects → PBR Channels → Clipping → Group.
**Expected**: Ordine identico su PAINT, FILL, PROCEDURAL, REFERENCE.
☐ Pass ☐ Fail — Note:

### 32.6 Displacement spostato fuori da PBR Channels
**Setup**: Espandi PBR Channels su un layer.
**Expected**: Bump presente. "Add to Displace" NON presente (è in Surface Effects).
**Reg 2026-05-28**: prima del fix, Displacement era duplicato in PBR Channels + Composite.
☐ Pass ☐ Fail — Note:

---

## 33. [New] Preset Library Integrity (post Phase 2 refactor)

Test critico: la Phase 2 refactor (split di compositing.py in 9 sub-modules) ha rotto silenziosamente alcuni preset perché funzioni cross-module non erano importate. Verificare CADENZA: dopo OGNI refactor + prima del release.

### 33.1 [Crit] Bulk load tutti i .tlm con BSDF.Base Color check
**Setup**: Per ogni .tlm in `presets/`:
  ```python
  bpy.ops.tlm.apply_preset(preset_name=...)
  bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED' and not n.name.startswith("TLM_"))
  assert bsdf.inputs["Base Color"].is_linked
  ```
**Expected**: 28/28 preset OK. Nessun NameError nella console.
**Bug fix 2026-05-28** (commit `0c9772e`): 5 NameError post-refactor (_find_first_sun_direction, _clamped_ramp_position, _LEGACY_BLEND_METHOD, _USE_NEW_MIX, _find_bsdf). Tutti fixati.
☐ Pass ☐ Fail — Note:

### 33.2 [Crit] 4 preset multi-color ColorRamp
**Setup**: Apply ciascuno → verifica BSDF cablato + no console errors:
- Crystal Geode (Hero M)
- Frozen Ice Glass (Hero P v6)
- Iridescent Rainbow Foil (Hero F2)
- Galaxy Marble (Hero K)
**Expected**: Tutti caricano con colori multipli applicati alla ColorRamp.
**Reg 2026-05-28**: il refactor aveva rotto questi 4 perché _clamped_ramp_position era usata da _build_proc_color_ramp senza import.
☐ Pass ☐ Fail — Note:

### 33.3 Anime preset post-fix
**Setup**: Apply "Anime Cel-Shaded (Hero G v3 Integrated Outline)".
**Expected**: NDOTL mask attiva, outline shader-integrated. No errori NDOTL/sun-direction in console.
**Reg 2026-05-28** (commit `1241c11`): NDOTL/NDOTH richiedevano _find_first_sun_direction late-import in masks.py.
☐ Pass ☐ Fail — Note:

### 33.4 Material-level props persistono nei preset
**Setup**: Apply Hero P (Frozen Ice).
**Expected**: bsdf_ior=1.31, use_volume_absorption=True, volume_absorption_color custom, volume_scatter=True. Verifica con `print(mat.tlm.bsdf_ior)`, etc.
**Reg 2026-05-xx**: serialize/deserialize material-level props in presets/io fixato.
☐ Pass ☐ Fail — Note:

### 33.5 Cleanup dev junk
**Setup**: Visivamente: nessun preset con nome "Boh ..." / "Quasi sabbia" / "Fuoco" / etc. nella build.
**Expected**: Preset cleanup completato (task #8).
☐ Pass ☐ Fail — Note:

---

## Triage Post-Test

Dopo aver eseguito tutti i test:

1. **Conta i fail** per sezione → prioritizza fix su aree con >2 fail
2. **[Reg] fail** = regression critica, fixare per prima cosa
3. **[Crit] fail** = blocca release, fix immediato
4. **Scenari reali (25.x) fail** = problema di usabilità, non di correttezza — valuta UX redesign

Risultati vanno in `memory/bugs_and_fixes.md` con root cause e fix applicato.

---

## Come eseguire il test plan

- **Single pass**: scorri dall'alto, segna pass/fail ma non fissare bug in-line → lista finale.
- **Area focus**: scegli una sezione (es. "10. Masks B"), fai tutti i test, fixa subito.
- **Smoke run**: solo test marcati [Crit] in 30 minuti → sanity check rapido.
- **Full regression**: tutto, ~3-4 ore. Fare prima di ogni release.

Ogni volta che trovi un bug non ovvio: annotalo qui nella sezione Note del test che l'ha scoperto, e replicalo anche in `bugs_and_fixes.md`.
