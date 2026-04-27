# TLM Bug Hunting Guide — Test Completo Passo per Passo

## Preparazione Iniziale

1. Apri Blender 5.0, crea un nuovo file (`File → New → General`)
2. Apri la System Console: `Window → Toggle System Console`
3. Vai nel workspace **Shading** (tab in alto)
4. Seleziona il Cube nella viewport
5. Nel pannello Properties (destra), vai su **Material Properties** (icona sfera)
6. Verifica che il materiale "Material" esista, altrimenti clicca `New`
7. Nel pannello **Texture Layers**, verifica che compaia "Material" con "0 layers"
8. Imposta il viewport su **Material Preview** (icona sfera nel header della viewport, oppure Z → Material Preview)

> **Convenzione**: quando scrivo "verifica nel Shader Editor" intendo guardare la metà inferiore dello Shading workspace dove si vedono i nodi.

---

## SEZIONE 1 — Fill Layer Base

### Test 1.1 — Creare un Fill Layer
1. Nel pannello Texture Layers, clicca il bottone **Fill**
2. **Verifica pannello**: compare "Fill 1" nella lista layer, con opacità 1.00
3. **Verifica Shader Editor**: compare un nodo RGB (Color) collegato a Base Color del Principled BSDF
4. **Verifica viewport**: il cubo mostra il colore bianco (default fill color)
5. **Verifica console**: dovrebbe stampare messaggi `[TLM]`

### Test 1.2 — Cambiare colore Fill
1. Nel pannello del layer attivo, clicca sul rettangolo colorato accanto a "Color:"
2. Scegli un rosso acceso (R=1, G=0, B=0)
3. **Verifica viewport**: il cubo diventa rosso
4. **Verifica Shader Editor**: il nodo RGB mostra rosso

### Test 1.3 — Cambiare opacità
1. Cambia il valore di opacità da 1.00 a 0.50
2. **Verifica**: con un solo layer, l'opacità non ha effetto visibile (normale, non c'è niente sotto con cui blendare)

### Test 1.4 — Cambiare blend mode
1. Cambia il dropdown blend mode da "Normal" a "Multiply"
2. **Verifica**: con un solo layer, il blend mode non ha effetto visibile (normale)
3. Rimetti su "Normal"

---

## SEZIONE 2 — Più Fill Layer + Blend Modes

### Test 2.1 — Due Fill Layer
1. Assicurati che "Fill 1" sia rosso (dal test precedente)
2. Clicca **Fill** per aggiungere un secondo layer
3. "Fill 2" appare nella lista SOPRA "Fill 1" (nuovo layer si inserisce sopra il layer attivo, convenzione standard)
4. Seleziona "Fill 2" nella lista, cambia il suo colore a blu (R=0, G=0, B=1)
5. **Verifica viewport**: il cubo è blu (Fill 2 è sopra e copre Fill 1)
6. **Verifica Shader Editor**: due nodi RGB collegati a un nodo Mix → Principled BSDF

### Test 2.2 — Opacità con due layer
1. Con "Fill 2" (blu) selezionato, cambia opacità a 0.50
3. **Verifica viewport**: il cubo mostra un viola/magenta (mix tra rosso e blu al 50%)

### Test 2.3 — Blend Modes (testa tutti)
1. Con "Fill 2" selezionato (blu, opacità 1.00), cambia blend mode per ognuno di questi e verifica che l'effetto sia diverso:
   - **Normal**: cubo blu (copre il rosso)
   - **Multiply**: cubo molto scuro/nero (rosso × blu ≈ nero)
   - **Screen**: cubo magenta brillante
   - **Overlay**: colore saturo scuro
   - **Add**: cubo magenta (rosso + blu)
   - **Subtract**: cubo scuro
   - **Difference**: cubo magenta
   - **Darken**: cubo nero (min di rosso e blu)
   - **Lighten**: cubo rosso (max di rosso e blu per canale)
   - **Color Dodge**: effetto schiarimento
   - **Color Burn**: effetto scurimento
   - **Soft Light**: effetto morbido
   - **Hard Light**: effetto duro
   - **Linear Light**: effetto lineare
   - **Divide**: effetto divisione
2. **Nota per ogni blend mode**: cambia il colore del Fill 2 a giallo (R=1, G=1, B=0) se il risultato con blu è troppo scuro per distinguere gli effetti
3. Rimetti su **Normal** quando hai finito

### Test 2.4 — Visibilità layer
1. Nella UIList, clicca l'icona occhio accanto a "Fill 2"
2. **Verifica viewport**: il cubo torna rosso (solo Fill 1 visibile)
3. Riattiva la visibilità
4. **Verifica**: torna blu

### Test 2.5 — Ordine layer (move up/down)
1. Situazione attuale: "Fill 2" (blu) sopra, "Fill 1" (rosso) sotto → il cubo è blu
2. Seleziona "Fill 2" e clicca la freccia **giù** nella toolbar
3. **Verifica**: "Fill 1" è ora sopra "Fill 2" nella lista → il cubo diventa rosso
4. Clicca la freccia **su** per rimettere l'ordine originale
5. **Verifica**: torna blu

### Test 2.6 — Eliminare un layer
1. Seleziona "Fill 2" e clicca il bottone **X** (elimina)
2. **Verifica**: resta solo "Fill 1" rosso
3. Ricrea "Fill 2" blu per i test successivi

---

## SEZIONE 3 — Paint Layer

### Test 3.1 — Creare un Paint Layer
1. Elimina tutti i layer esistenti
2. Clicca il bottone **Paint**
3. **Verifica pannello**: compare "Paint 1" nella lista
4. **Verifica Shader Editor**: compare un nodo Image Texture con un'immagine nuova
5. **Verifica viewport**: il cubo mostra il colore dell'immagine (probabilmente nero/trasparente)

### Test 3.2 — Dipingere
1. Passa a **Texture Paint** mode (dropdown in alto a sinistra della viewport, oppure il tab "Paint" in alto)
2. Scegli un colore nel color picker (es. verde)
3. Dipingi sul cubo
4. **Verifica viewport**: le pennellate verdi appaiono sul cubo
5. Torna in Object Mode

### Test 3.3 — Paint sopra Fill
1. Aggiungi un Fill layer (giallo) — dovrebbe andare sotto il Paint
2. **Verifica**: dove hai dipinto si vede verde, dove non hai dipinto si vede giallo (il Paint layer ha alpha dalle pennellate)

---

## SEZIONE 4 — Procedural Layer

### Test 4.1 — Noise
1. Elimina tutti i layer, aggiungi un Fill bianco come base
2. Clicca **Proc** per aggiungere un layer procedurale
3. Il tipo di default dovrebbe essere "Noise"
4. **Verifica viewport**: pattern noise visibile sul cubo
5. **Verifica Shader Editor**: nodi TexCoord → Mapping → Noise Texture → ColorRamp (o simile) → Mix → BSDF
6. Cambia `proc_scale` (es. da 5 a 20)
7. **Verifica**: il pattern diventa più fino

### Test 4.2 — Voronoi
1. Seleziona il layer procedurale, cambia tipo a **Voronoi**
2. **Verifica viewport**: pattern celle Voronoi visibile
3. Testa i feature mode: F1, F2, SMOOTH_F1, DISTANCE_TO_EDGE
4. **Verifica**: ogni feature mostra un pattern diverso

### Test 4.3 — Wave
1. Cambia tipo a **Wave**
2. **Verifica viewport**: bande ondulate
3. Testa wave_type BANDS vs RINGS
4. Testa wave_profile SIN vs SAW vs TRI
5. **Verifica**: ogni combinazione mostra un pattern diverso

### Test 4.4 — Gradient
1. Cambia tipo a **Gradient**
2. **Verifica viewport**: gradiente lineare visibile
3. Testa gradient_type: LINEAR, RADIAL, SPHERICAL, DIAGONAL, EASING, QUADRATIC
4. **Verifica**: ogni tipo mostra un gradiente diverso

### Test 4.5 — Musgrave
1. Cambia tipo a **Musgrave**
2. **Verifica viewport**: pattern frattale visibile
3. Cambia scale e detail
4. **Verifica**: il pattern cambia

### Test 4.6 — Checker
1. Cambia tipo a **Checker**
2. **Verifica viewport**: scacchiera con i colori proc_color1 e proc_color2
   - Il Checker usa direttamente Color1/Color2 del nodo (non il ColorRamp come gli altri procedurali)
3. Cambia `proc_color1` a rosso e `proc_color2` a blu
4. Se i colori non cambiano nel viewport, fai `F3 → Rebuild Composite` (il debounce potrebbe non triggerare)
5. **Verifica viewport**: scacchiera rossa/blu
6. Cambia `checker_scale`
7. **Verifica**: la scacchiera cambia scala

### Test 4.7 — Colori procedurali (Noise/Voronoi/Wave/Gradient/Musgrave)
1. Su un layer procedurale **non-Checker**, cambia `proc_color1` (rosso) e `proc_color2` (blu)
2. **Verifica viewport**: il pattern usa i due colori scelti (tramite ColorRamp)
3. **Nota**: il Checker ha i colori integrati (vedi test 4.6), gli altri usano il ColorRamp

---

## SEZIONE 5 — Adjustment Layer

### Test 5.1 — Hue/Saturation
1. Crea uno stack: Fill rosso (base)
2. Clicca **Adj** → tipo **Hue/Sat**
3. Cambia `adj_hue` a 0.0
4. **Verifica viewport**: il rosso cambia tonalità
5. Cambia `adj_hue` a 0.3, poi 0.7
6. **Verifica**: il colore ruota nello spettro
7. Cambia `adj_saturation` a 0.0
8. **Verifica**: il cubo diventa grigio (desaturato)
9. Rimetti saturation a 1.0, cambia `adj_value` a 2.0
10. **Verifica**: il colore diventa più luminoso

### Test 5.2 — Brightness/Contrast
1. Cambia tipo adjustment a **Brightness/Contrast** (o aggiungi un nuovo Adj)
2. Imposta `adj_brightness` = 0.5
3. **Verifica viewport**: colore più chiaro
4. Imposta `adj_brightness` = -0.5
5. **Verifica**: colore più scuro
6. Rimetti brightness a 0, imposta `adj_contrast` = 0.5
7. **Verifica**: contrasto aumentato

### Test 5.3 — Levels
I Levels rimappano l'intervallo tonale dell'immagine (input → gamma → output).
- **Input Black** (`adj_in_min`): tutti i valori sotto questo diventano nero → alza per scurire le ombre
- **Input White** (`adj_in_max`): tutti i valori sopra questo diventano bianco → abbassa per schiarire le luci
- **Gamma** (`adj_levels_gamma`): corregge i mezzitoni (>1 schiarisce, <1 scurisce)
- **Output Black/White** (`adj_out_min`/`adj_out_max`): comprime l'intervallo di uscita

**Test**:
1. Crea stack: Fill rosso (base) + Adj tipo **Levels**
2. Imposta `adj_in_min` = 0.3 → **Verifica**: il rosso diventa più scuro (i valori sotto 0.3 vengono schiacciati a nero)
3. Rimetti `adj_in_min` = 0.0, imposta `adj_in_max` = 0.7 → **Verifica**: il rosso diventa più brillante/clippato
4. Rimetti `adj_in_max` = 1.0, imposta `adj_levels_gamma` = 0.5 → **Verifica**: i mezzitoni si scuriscono
5. Imposta `adj_levels_gamma` = 2.0 → **Verifica**: i mezzitoni si schiariscono
6. Prova `adj_out_min` = 0.3, `adj_out_max` = 0.7 → **Verifica**: il contrasto si riduce (l'output è compresso)

### Test 5.4 — Color Balance
1. Cambia tipo a **Color Balance**
2. Imposta `adj_lift` R=1.5 (lascia G e B a 1.0)
3. **Verifica viewport**: le ombre tendono al rosso
4. Imposta `adj_gain` B=1.5
5. **Verifica**: le luci tendono al blu

### Test 5.5 — Adjustment su layer corretto
1. Crea stack: Fill rosso (base) + Fill verde + Adjustment Hue/Sat
2. L'adjustment dovrebbe modificare SOLO i layer sottostanti (il composito fino a quel punto)
3. **Verifica**: l'hue shift cambia il colore risultante
4. Sposta l'adjustment sotto il Fill verde
5. **Verifica**: il verde non è più influenzato dall'adjustment

---

## SEZIONE 6 — Group Layer

### Test 6.1 — Creare un Group
1. Elimina tutto. Crea: Fill bianco (base)
2. Clicca il bottone per creare un **Group** (icona cartella)
3. Il gruppo appare nella lista
4. Crea 2 Fill layer dentro al gruppo (rosso e blu)
   - Seleziona ogni Fill e nel dropdown "Group" assegnalo al gruppo creato
5. **Verifica viewport**: il risultato del gruppo (composito dei 2 fill) viene blendato sopra il fill bianco base

### Test 6.2 — Opacità del Group
1. Seleziona il layer Group nella lista
2. Cambia opacità a 0.5
3. **Verifica viewport**: il risultato del gruppo si vede al 50% sopra il bianco base

---

## SEZIONE 7 — Canali PBR su Fill Layer

### Test 7.1 — Roughness con fill value
1. Elimina tutto, crea un Fill layer bianco
2. Nella sezione PBR Channels, clicca il toggle **Roughness** per attivarlo
3. Lo slider mostra 0.50 (default)
4. Cambia a 0.00
5. **Verifica viewport**: il cubo diventa completamente lucido (riflette l'HDRI)
6. Cambia a 1.00
7. **Verifica viewport**: il cubo diventa completamente opaco/ruvido
8. **Verifica Shader Editor**: nodo Value → Math(Add) → BSDF.Roughness
9. **Verifica console**: `[TLM] FILL roughness: no image, using fill value X.X for layer 'Fill 1'`

### Test 7.2 — Roughness con immagine
1. Con Roughness attiva, clicca **New** accanto allo slider
2. **Verifica pannello**: lo slider sparisce, compare il nome immagine "Fill 1_Roughness"
3. **Verifica Shader Editor**: nodo Image Texture → Separate Color → Math(Add) → BSDF.Roughness
4. **Verifica console**: `[TLM] FILL roughness: using image 'Fill 1_Roughness' for layer 'Fill 1'`
5. Clicca **X** per rimuovere l'immagine
6. **Verifica**: torna lo slider con fill value

### Test 7.3 — Metallic con fill value
1. Attiva il toggle **Metallic**
2. Imposta a 1.00
3. **Verifica viewport**: il cubo diventa metallico (riflette l'ambiente in modo metallico)
4. **Verifica console**: `[TLM] FILL metallic: no image, using fill value 1.0 for layer 'Fill 1'`

### Test 7.4 — Metallic con immagine
1. Clicca **New** su Metallic
2. **Verifica Shader Editor**: Image Texture → Separate Color → Math(Add) → BSDF.Metallic
3. Rimuovi con X

### Test 7.5 — Normal con immagine
1. Attiva il toggle **Normal**, clicca **New**
2. **Verifica Shader Editor**: Image Texture → Normal Map → BSDF.Normal
3. **Verifica console**: `[TLM] FILL normal: using image 'Fill 1_Normal' for layer 'Fill 1'`
4. L'effetto visivo potrebbe essere sottile con la normal map flat di default (0.5, 0.5, 1.0)

### Test 7.6 — Emission con immagine
1. Attiva il toggle **Emission**, clicca **New**
2. **Verifica Shader Editor**: Image Texture → BSDF.Emission Color + Value → BSDF.Emission Strength
3. **Verifica console**: `[TLM] FILL emission: using image 'Fill 1_Emission' for layer 'Fill 1'`
4. Cambia Emission Strength a 5.0
5. **Verifica viewport**: il cubo emette luce (visibile se HDRI è scuro o in Rendered mode)

### Test 7.7 — Emission con fill color (senza immagine)
1. Rimuovi l'immagine emission (X)
2. L'emission dovrebbe usare `emission_color`
3. Cambia emission_color a giallo brillante
4. **Verifica viewport**: il cubo emette luce gialla
5. **Verifica console**: `[TLM] FILL emission: no image, using emission_color for layer 'Fill 1'`

### Test 7.8 — Bump
1. Attiva il toggle **Bump**
2. Regola `bump_strength` a 1.0 e `bump_distance` a 0.1
3. **Verifica viewport**: potrebbe non essere visibile su un Fill layer senza texture sorgente (normale — bump ha bisogno di variazione)

### Test 7.9 — Più canali PBR contemporaneamente
1. Attiva Roughness (value 0.2) + Metallic (value 1.0) + Normal (New image) + Emission (New image, strength 2.0)
2. **Verifica Shader Editor**: ci sono nodi per TUTTI e 4 i canali, collegati ai rispettivi input del BSDF
3. **Verifica viewport**: cubo lucido, metallico, con normal map e emissione

---

## SEZIONE 8 — Canali PBR su Paint Layer

### Test 8.1 — Paint con Roughness image
1. Elimina tutto, crea un Paint layer
2. Dipingi qualcosa (per avere un'immagine base)
3. Attiva Roughness, clicca New
4. **Verifica Shader Editor**: l'immagine roughness è collegata al BSDF via Separate Color
5. Vai in Texture Paint, seleziona l'immagine roughness nello slot e dipingi
6. **Verifica viewport**: le zone dipinte cambiano roughness

### Test 8.2 — Paint con Metallic image
1. Attiva Metallic, clicca New
2. **Verifica**: come sopra ma per metallic

### Test 8.3 — Paint con Normal image
1. Attiva Normal, clicca New
2. **Verifica**: Image Texture → Normal Map → BSDF.Normal

---

## SEZIONE 9 — Canali PBR su Procedural Layer

### Test 9.1 — Procedural Noise con Roughness
1. Elimina tutto, crea Fill bianco + Procedural Noise sopra
2. Sul Procedural, attiva Roughness
3. **Verifica viewport**: la roughness varia con il pattern noise (zone lucide e opache)
4. Cambia roughness_fill per regolare l'intensità
5. **Verifica console**: messaggi sui nodi procedurali

### Test 9.2 — Procedural con Metallic
1. Attiva Metallic sul Procedural
2. Imposta metallic_fill a 1.0
3. **Verifica viewport**: il metallic varia col pattern

### Test 9.3 — Procedural con Bump
1. Attiva Bump sul Procedural
2. Imposta bump_strength a 2.0, bump_distance a 0.05
3. **Verifica viewport**: superficie ondulata che segue il pattern noise

### Test 9.4 — Procedural con tutti i canali
1. Attiva Roughness + Metallic + Bump contemporaneamente
2. **Verifica viewport**: effetto combinato
3. **Verifica Shader Editor**: nodi per tutti i canali presenti e collegati

---

## SEZIONE 10 — Mask

> **Nota**: la Mask funziona solo quando ci sono 2+ layer. Su un layer singolo non ha effetto (non c'è un layer sotto con cui blendare).

### Test 10.1 — Mask base
1. Crea stack: Fill rosso (base) + Fill blu (sopra)
2. Seleziona Fill blu, clicca **Mask**
3. Clicca **New** per creare un'immagine maschera
4. **Verifica pannello**: il toggle Mask è attivo, compare il nome dell'immagine
5. **Importante**: la nuova immagine mask è tutta **nera** → il layer blu è completamente **nascosto**
6. **Verifica viewport**: vedi solo rosso (il blu è mascherato dal nero = 0% visibilità)
7. **Verifica Shader Editor**: compare un nodo Image Texture (mask) collegato al Factor del Mix node

### Test 10.2 — Dipingere la Mask
1. Vai in **Texture Paint** mode
2. Seleziona l'immagine maschera nello slot di pittura (dropdown immagini nell'header)
3. Seleziona colore **bianco** come pennello
4. Dipingi **bianco** dove vuoi che il blu sia visibile
   - Bianco = layer visibile (100%)
   - Nero = layer nascosto (0%)
   - Grigio = layer semi-trasparente (0–100%)
5. **Verifica viewport**: il blu appare nelle zone dipinte di bianco, il rosso resta visibile altrove
6. Dipingi **nero** per "cancellare" e riportare il rosso

### Test 10.3 — Mask su Procedural
1. Crea stack: Fill bianco (base) + Procedural Noise (sopra) con Mask
2. Crea e dipingi la maschera
3. **Verifica**: il noise appare solo dove la maschera è bianca

---

## SEZIONE 11 — Clipping Mask

### Test 11.1 — Clipping Mask base
1. Crea stack: Paint layer (base, dipingi un cerchio) + Fill rosso (sopra)
2. Seleziona Fill rosso, attiva **Clipping Mask**
3. **Verifica viewport**: il rosso è visibile SOLO dove il Paint layer sotto ha alpha (dove hai dipinto)
4. **Verifica Shader Editor**: c'è un nodo Math(Multiply) che combina alpha del layer sotto col fattore di blend

### Test 11.2 — Clipping Mask con Group
1. Crea: Fill bianco (base) + Group con 2 fill (rosso e verde) + Fill blu sopra il gruppo con Clipping Mask
2. **Verifica**: il blu dovrebbe essere visibile sull'intera area del gruppo (alpha sintetizzata = 1.0)

---

## SEZIONE 12 — Smart Mask

### Test 12.1 — Smart Mask AO
1. Seleziona la UV Sphere (o un oggetto con cavità)
2. Crea materiale con: Fill base + Fill scuro sopra
3. Sul Fill scuro, clicca **Smart** → **Ambient Occlusion**
4. **Verifica**: viene generata un'immagine maschera basata sull'AO della mesh
5. **Verifica viewport**: lo sporco scuro appare nelle cavità

### Test 12.2 — Smart Mask Curvature
1. Stessa setup, clicca **Smart** → **Curvature**
2. **Verifica**: la maschera evidenzia bordi e spigoli della mesh

---

## SEZIONE 13 — Triplanar Projection

### Test 13.1 — Paint con Triplanar
1. Crea un Paint layer, assegna un'immagine con pattern riconoscibile (es. checker)
2. Attiva **Triplanar**
3. **Verifica Shader Editor**: invece di UV Map → Image Texture, ci sono 3 proiezioni (X, Y, Z) blendati
4. **Verifica viewport**: la texture si proietta senza seam UV visibili
5. Cambia `triplanar_scale` e `triplanar_sharpness`
6. **Verifica**: scala e blending cambiano

### ~~Test 13.2~~ — RIMOSSO
> Triplanar non si applica ai Procedural layer: usano già coordinate 3D (Generated/Object).

---

## SEZIONE 14 — Operazioni Layer

### Test 14.1 — Duplica Layer
1. Crea un Fill layer con: colore rosso, opacità 0.7, blend mode Multiply, Roughness attiva (value 0.3), Normal con immagine New
2. Clicca il bottone **Duplica** (icona copia)
3. **Verifica**: il nuovo layer ha ESATTAMENTE gli stessi valori:
   - Colore rosso ✓
   - Opacità 0.7 ✓
   - Blend mode Multiply ✓
   - Roughness attiva con value 0.3 ✓
   - Normal con immagine (nome diverso ma contenuto identico) ✓

### Test 14.2 — Rinomina Layer
1. Fai doppio click sul nome del layer nella UIList
2. Scrivi un nuovo nome (es. "Base Metal")
3. **Verifica**: il nome cambia e viene mantenuto dopo rebuild

### Test 14.3 — Flatten
1. Crea uno stack complesso: Fill + Procedural + Adjustment
2. Clicca **Flatten**
3. **Verifica**: tutti i layer vengono sostituiti da una singola immagine baked
4. **Verifica viewport**: il risultato visivo è identico a prima del flatten

---

## SEZIONE 15 — Import/Export

### Test 15.1 — Export JSON
1. Crea uno stack complesso con 3+ layer di tipi diversi, PBR channels attivi
2. Clicca **Export** (icona freccia giù)
3. Scegli un percorso di salvataggio
4. **Verifica**: il file .json.gz viene creato
5. Apri il file (rinomina in .json e apri con notepad se vuoi verificare): contiene tutti i dati layer + immagini in Base64

### Test 15.2 — Import JSON
1. Elimina tutti i layer dal materiale
2. Clicca **Import** (icona freccia su)
3. Seleziona il file .json.gz esportato
4. **Verifica**: lo stack viene ricreato identico
5. **Verifica viewport**: il risultato visivo è identico a prima dell'export

---

## SEZIONE 16 — Bake PBR Export

### Test 16.1 — Bake Unreal Preset
1. Crea uno stack con: Fill base + Roughness (0.3) + Metallic (1.0) + Normal (immagine)
2. Apri **TLM Settings** (fondo del pannello)
3. Seleziona preset **Unreal**
4. Scegli risoluzione (1024) e formato (PNG)
5. Clicca **Bake PBR**
6. Scegli cartella di output
7. **Verifica**: vengono creati 3 file:
   - `*_Albedo.png` (base color)
   - `*_Normal.png` (normal map)
   - `*_ORM.png` (Occlusion/Roughness/Metallic packed)

### Test 16.2 — Bake Unity Preset
1. Stesso stack, seleziona preset **Unity**
2. **Verifica**: file output nel formato Unity (Albedo, Normal, Mask)

### Test 16.3 — Bake glTF Preset
1. Seleziona preset **glTF**
2. **Verifica**: file output nel formato glTF (BaseColor, Normal, MetallicRoughness)

---

## SEZIONE 17 — Symmetry Paint

### Test 17.1 — Symmetry X
1. Crea un Paint layer
2. Nella toolbar TLM, clicca **X** (symmetry X)
3. Vai in Texture Paint e dipingi su un lato del cubo
4. **Verifica**: la pennellata appare specchiata sull'asse X

### Test 17.2 — Symmetry Y
1. Clicca **Y** (symmetry Y)
2. Dipingi
3. **Verifica**: la pennellata appare specchiata sull'asse Y

---

## SEZIONE 18 — Stress Test Combinato

### Test 18.1 — Stack complesso 7+ layer
1. Crea questo stack esatto (dall'alto verso il basso nella UIList):
   - **Adjustment** Color Balance (lift R=1.2)
   - **Procedural** Noise (scale 10, opacity 0.5, blend Overlay) + Bump attivo (strength 1.0)
   - **Fill** grigio (R=0.3, G=0.3, B=0.3) + Roughness 0.8 + Metallic 0.0
   - **Paint** (dipingi graffi dorati) + Roughness immagine New + Metallic immagine New
   - **Fill** arancione + Emission (color arancione, strength 3.0)
   - **Procedural** Voronoi (scale 15, blend Multiply, opacity 0.3) + Bump attivo
   - **Fill** grigio scuro (base) + Roughness 0.5
2. **Verifica viewport**: materiale complesso visibile con tutte le proprietà
3. **Verifica Shader Editor**: nodi per tutti i canali PBR collegati al BSDF
4. **Verifica console**: nessun errore, tutti i canali connessi
5. Cambia opacità di vari layer rapidamente (stress test debounce)
6. Nascondi/mostra layer rapidamente
7. Riordina layer con le frecce

### Test 18.2 — Multi-materiale
1. Seleziona la UV Sphere
2. Crea un nuovo materiale con stack diverso
3. Alterna tra i due oggetti
4. **Verifica**: ogni oggetto mantiene il proprio stack TLM separato

---

## Come Reportare un Bug

Per ogni bug trovato, annota:
1. **Sezione e numero test** (es. "7.5 — Normal con immagine")
2. **Screenshot** della viewport e/o dello Shader Editor
3. **Copia il log della console** (messaggi `[TLM]` e eventuali errori/traceback)
4. **Cosa ti aspettavi** vs **cosa è successo**
5. **È riproducibile?** (succede ogni volta o solo a volte?)
