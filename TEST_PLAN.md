# TLM — Test Plan

Piano di test manuale da eseguire in Blender 5.0.
Ogni test ha: **Setup** (cosa fare) → **Expected** (cosa deve succedere) → ☐ Pass / ☐ Fail / **Note**.

Usa sempre lo stesso oggetto di test: una **Plane** con UV unwrappato (U → Unwrap), sotto una HDRI neutra, viewport shading **Rendered**.
Salva spesso. Apri la console Blender (Window → Toggle System Console) per vedere errori Python.

**Legenda**:
- `☑` = da spuntare quando il test passa
- **Crit** = test critico (se fallisce blocca tutto il resto)
- **Reg** = regression (ha rotto in passato, merita attenzione extra)

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

TLM supporta 20 blend modes: MIX, MULTIPLY, SCREEN, OVERLAY, ADD, SUBTRACT, DIFFERENCE, DIVIDE, DARKEN, LIGHTEN, COLOR_DODGE, COLOR_BURN, SOFT_LIGHT, HARD_LIGHT, LINEAR_LIGHT, EXCLUSION, HUE, SATURATION, COLOR, LUMINOSITY.

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

Permette a un singolo layer di avere blend diversi per canale.
Proprietà: `blend_mode_base_color`, `blend_mode_roughness`, `blend_mode_metallic`, `blend_mode_normal`, `blend_mode_emission`, `blend_mode_transmission`. Default: INHERIT.

### 4.1 Branching base: MULTIPLY solo su base_color
**Setup**: Fill nero sopra Fill bianco. Branching → Base Color = MULTIPLY, Roughness = INHERIT.
**Expected**: Base color moltiplicata (nero). Roughness usa il main blend.
☐ Pass ☐ Fail — Note:

### 4.2 Branching su layer procedurale
**Setup**: Voronoi layer. Branching → Base Color = MIX, Roughness = OVERLAY.
**Expected**: Voronoi driva roughness con contrasto aumentato, base color piatto.
☐ Pass ☐ Fail — Note:

### 4.3 INHERIT rispetta main blend
**Setup**: Cambia main `blend_mode` da MIX a SCREEN con tutti i branching su INHERIT.
**Expected**: Tutti i canali attivi usano SCREEN.
☐ Pass ☐ Fail — Note:

---

## 5. Procedural Types (8 tipi)

Shared params: `proc_scale`, `proc_offset_x/y/z`, `proc_color1`, `proc_color2`, `proc_contrast`.

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

---

## 6. Adjustment Types (5 tipi)

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
**Expected**: Remapping tonale come in Photoshop.
☐ Pass ☐ Fail — Note:

### 6.4 COLOR_BALANCE
**Setup**: Adjustment Color Balance → Lift / Gamma / Gain.
**Expected**: Grading cinematico. Lift tocca ombre, Gamma midtones, Gain highlight.
☐ Pass ☐ Fail — Note:

### 6.5 CURVES
**Setup**: Adjustment Curves.
**Expected**: Curva RGB editabile con handle.
☐ Pass ☐ Fail — Note:

### 6.6 Adjustment ignora own channels
**Setup**: Adjustment layer tra 2 Fill.
**Expected**: Modifica composite sotto. Non contribuisce direttamente ai canali, solo modifica.
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

### 8.1 Create group + move layer inside
**Setup**: Add Group → crea layer Fill → Move to Group.
**Expected**: Layer compare nested sotto il group. Indentazione visibile nella UIList.
☐ Pass ☐ Fail Nie gruppi, non funzionano le lampadine e l'occhio dei gruppi  Note:

### 8.2 Group collapse / expand
**Setup**: Click triangolo sul group.
**Expected**: Toggle show/hide dei children nella UIList.
☐ Pass ☐ Fail — Note:

### 8.3 Group opacity
**Setup**: Group con 2 children colorati. Group opacity = 0.3.
**Expected**: Tutto il gruppo semitrasparente rispetto al fondo.
☐ Pass ☐ Fail — Note: Non funziona 

### 8.4 Group blend mode
**Setup**: Group blend = MULTIPLY.
**Expected**: Tutto il gruppo moltiplica come un unico layer.
☐ Pass ☐ Fail — Note: non funziona 

### 8.5 Group mask
**Setup**: Group con mask (immagine o smart).
**Expected**: Mask si applica a tutto il gruppo.
☐ Pass ☐ Fail — Note: non presente l'opzione 

### 8.6 Remove from Group
**Setup**: Layer dentro group → Remove from Group.
**Expected**: Layer torna al root level, sopra il group.
☐ Pass ☐ Fail — Note:

### 8.7 Delete group with children
**Setup**: Group con 2 children → Remove group.
**Expected**: Group rimosso, children orfani (root level), NON cancellati.
☐ Pass ☐ Fail — Note:

### 8.8 Nested groups
**Setup**: Group dentro Group (se supportato).
**Expected**: Funziona o errore chiaro.
☐ Pass ☐ Fail — Note: Non funziona. Rimuovere 

---

## 9. Masks — Primary (Mask A)

`mask_source`: IMAGE / AO / POINTINESS / EDGE_WEAR / DIRT / CURVATURE_SMART.

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

## 13. Fresnel

Controlla blending basato sull'angolo di vista.

### 13.1 Fresnel Factor slider
**Setup**: Fill → Fresnel → Factor > 0.
**Expected**: Layer visibile preferenzialmente sui bordi (glancing angle).
☐ Pass ☐ Fail — Note:

### 13.2 Fresnel IOR
**Setup**: Cambia IOR da 1.45 a 3.0.
**Expected**: Effetto più pronunciato.
☐ Pass ☐ Fail — Note:

### 13.3 Fresnel Invert
**Setup**: Fresnel Invert ON.
**Expected**: Layer visibile al centro invece che sui bordi.
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

### 17.2 Import PBR Set (Substance-style)
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

Presets attuali in `presets/`: Alien Crystal, Brushed Gold, Brushed Steel, Control, copper test, Lava Rock, Rusted Iron, sfera, sfera bellissima, Weathered Marble, My Preset.

### 18.1 [Crit] Apply Preset
**Setup**: Material vuoto → Apply Preset → Brushed Gold.
**Expected**: Stack Brushed Gold caricato. Viewport mostra oro spazzolato.
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
