# Texture Layer Manager - Layer and Node Build Log

Data: 2026-05-21  
Stato: analisi tecnica del comportamento corrente del programma  
Repo: `texture_layer_manager`

Questo documento completa `PROGRAM_ANALYSIS_LOG.md`.

Il primo log descrive architettura, moduli e rischi generali. Questo file
descrive invece, in modo piu' pratico, cosa viene costruito nello Shader
Editor quando l'utente aggiunge layer, abilita canali PBR, usa procedural,
paint, maschere, alpha, normal e bump.

Nota: il codice e' molto dinamico. Il numero preciso di nodi puo' cambiare
in base a blend mode, maschere, opacity, clipping mask, fresnel, Color 3,
coordinate transform, user slots e versione Blender. Qui viene descritta la
topologia logica, cioe' i blocchi che il programma genera.

---

## 1. Modello mentale generale

Texture Layer Manager non crea un singolo materiale fisso. Ricostruisce una
node tree Blender a partire dallo stack di layer salvato in `material.tlm`.

Pipeline semplificata:

```text
TLM Layer Stack
  -> filtra layer visibili / solo / gruppi
  -> espande i layer per canale PBR
  -> crea una corsia di nodi per ogni canale attivo
  -> miscela i layer in ordine bottom-to-top
  -> collega i risultati al Principled BSDF
  -> collega BSDF al Material Output
```

Canali principali:

- `base_color`
- `roughness`
- `metallic`
- `normal`
- `bump`
- `emission`
- `transmission`
- `alpha`

I canali non vengono costruiti tutti sempre. Il rebuild decide quali corsie
creare in base a `output_channel` e ai toggle `use_roughness`,
`use_metallic`, `use_normal`, `use_bump`, `use_emission`,
`use_transmission`, `use_alpha`.

Il nodo finale di ogni canale viene collegato al Principled BSDF tramite
reroute/tag dedicati, cosi' il grafo resta leggibile e il sistema puo'
ritrovare i propri nodi nei rebuild successivi.

---

## 2. Layer stack e ordine di compositing

Nel rebuild, i layer vengono letti da `material.tlm.layers`, poi ordinati in
modo che la composizione avvenga dal basso verso l'alto.

Effetto pratico:

- il primo layer valido diventa la base;
- ogni layer successivo viene miscelato sopra il risultato corrente;
- un layer nascosto non contribuisce;
- un layer dentro un gruppo viene gestito come figlio del gruppo;
- un solo layer attivo modifica la lista dei layer processati.

I tipi layer principali sono:

- `PAINT`
- `FILL`
- `PROCEDURAL`
- `ADJUSTMENT`
- `GROUP`
- `REFERENCE`

---

## 3. Routing dei canali

Ogni layer ha un `output_channel` principale:

- `BASE_COLOR`
- `ROUGHNESS`
- `METALLIC`
- `ALPHA`

Questo e' il canale primario del layer.

In piu', un layer puo' contribuire anche ad altri canali tramite toggle:

- `use_roughness`
- `use_metallic`
- `use_normal`
- `use_bump`
- `use_emission`
- `use_transmission`
- `use_alpha`

La logica e' cumulativa:

```text
output_channel = ROUGHNESS
use_metallic = True

=> il layer contribuisce sia a roughness sia a metallic
```

Questo e' importante per i materiali promozionali: un singolo procedural puo'
disegnare la base color, ma anche pilotare roughness, metallic e bump se lo
si abilita esplicitamente.

---

## 4. Nodi comuni creati per il blending

### 4.1 Blend colore

Per i canali colore, il sistema usa un nodo:

```text
ShaderNodeMix
data_type = RGBA
blend_type = blend mode del layer
```

Schema:

```text
current color  -> A
layer color    -> B
factor         -> Factor
Result         -> nuovo current
```

Canali tipici:

- Base Color
- Emission

Blend mode supportate dal layer:

- Mix
- Multiply
- Screen
- Overlay
- Add
- Subtract
- Difference
- Divide
- Darken
- Lighten
- Color Dodge
- Color Burn
- Soft Light
- Linear Light
- Exclusion
- Hue
- Saturation
- Color
- Luminosity

### 4.2 Blend scalar

Per roughness, metallic, transmission e alpha il sistema usa un trucco
voluto:

```text
ShaderNodeMix
data_type = RGBA
blend_type = blend mode del layer
  -> ShaderNodeRGBToBW
```

Motivo: Blender non espone sempre tutte le blend mode in modo equivalente
sui socket float. Usando Mix RGBA e poi `RGB to BW`, Subtract, Multiply,
Difference e simili funzionano anche su roughness e metallic.

Schema:

```text
current scalar -> A grigio
layer scalar   -> B grigio
ShaderNodeMix  -> Result color
RGB to BW      -> nuovo scalar
```

Questo e' il motivo per cui, nei canali scalar, si vedono nodi colore anche
se il risultato finale e' un valore float.

### 4.3 Blend vector

Normal e Bump non vengono miscelati come colori.

Usano:

```text
ShaderNodeMix
data_type = VECTOR
blend_type = MIX
```

Schema:

```text
normal corrente -> A
normal layer    -> B
factor          -> Factor
Result          -> nuova normal
```

Qui non ha senso usare Multiply, Subtract, Overlay ecc. Le normal sono
vettori, quindi il programma usa sempre Mix.

### 4.4 Alpha math

Il canale alpha usa nodi Math, non le blend mode colore.

Schema:

```text
current alpha -> Math input A
layer alpha   -> Math input B
Math result   -> Mix scalar con opacity/mask/fresnel
```

Operazioni disponibili:

- Add
- Subtract
- Multiply
- Divide
- Power
- Minimum
- Maximum
- Compare
- Smooth Minimum
- Smooth Maximum
- Round / Floor / Ceil
- Sine / Cosine / Tangent
- conversioni e altre funzioni Math di Blender

Motivo: alpha e' opacita', non colore. Usare Overlay/Hue/Saturation su alpha
non sarebbe prevedibile.

---

## 5. Factor pipeline: opacity, mask, alpha, fresnel, clipping

Il `Factor` del Mix non e' sempre solo `layer.opacity`.

Il sistema puo' moltiplicare piu' sorgenti:

```text
opacity
  * layer alpha
  * mask
  * fresnel
  * clipping mask / previous alpha
```

In pratica:

- opacity decide la forza globale del layer;
- paint alpha evita che le zone non dipinte sovrascrivano tutto;
- mask limita dove il layer appare;
- fresnel limita il layer in base all'angolo di vista;
- clipping mask usa l'alpha del layer sotto per ritagliare quello sopra.

Caso importante: se il primo layer ha opacity minore di 1, una mask o alpha
paint, il programma crea un background sintetico. Senza questo, il primo
layer non avrebbe nulla sotto contro cui miscelarsi e il suo factor verrebbe
di fatto perso.

Baseline usate:

- Base Color: bianco sintetico quando serve mostrare zone trasparenti.
- Scalar: valore default del canale.
- Normal/Bump: Geometry Normal.
- Emission: nero.
- Alpha: 1.0.

Default scalar:

```text
roughness    = 0.5
metallic     = 0.0
transmission = 0.0
alpha        = 1.0
```

---

## 6. Layer PAINT

Il Paint layer usa una immagine Blender.

### 6.1 Paint su Base Color

Nodi tipici:

```text
UV Map
  -> Image Texture
       Color -> layer color
       Alpha -> layer alpha
```

Poi:

```text
current color + layer color
  -> ShaderNodeMix RGBA
  -> Base Color chain
```

Il canale alpha dell'immagine e' fondamentale. Serve a impedire che una
texture paint vuota o semivuota sporchi l'intero materiale.

### 6.2 Paint su Roughness / Metallic / Transmission

Quando un Paint layer e' routato su un canale scalar:

```text
UV Map
  -> Image Texture Non-Color
       Color -> Separate Color
                  Red -> valore scalar
       Alpha -> factor aggiuntivo
```

Poi:

```text
current scalar + Red channel
  -> ShaderNodeMix RGBA
  -> RGB to BW
  -> canale scalar
```

Perche' usa il canale Red:

- e' il modo piu' stabile per leggere un valore grayscale da un'immagine;
- funziona anche se l'immagine e' RGB;
- il valore viene convertito poi in float.

Perche' usa ancora l'Alpha:

- se una zona non e' dipinta, alpha e' 0;
- quindi quella zona non deve sovrascrivere roughness/metallic;
- questo risolve il caso in cui una paint su roughness rendeva tutto il cubo
  lucido o nero.

### 6.3 Paint su Alpha

Nodi:

```text
Image Texture Alpha -> layer alpha output
```

Poi il valore va nella pipeline alpha math.

### 6.4 Paint su Emission

Se il layer ha una immagine:

```text
Image Texture Color -> emission color
Image Texture Alpha -> factor
```

Se non ha immagine:

```text
RGB fill emission_color -> emission color
```

L'emission strength non e' per-layer nel nodo finale. Il programma calcola il
massimo tra `emission_strength * opacity` dei layer emission attivi e lo
collega al Principled BSDF.

### 6.5 Paint su Bump

Nodi:

```text
UV Map
  -> Image Texture Non-Color
  -> Separate Color
       Red -> Bump Height
  -> Bump node
  -> Mix Vector con normal corrente
```

Il rosso dell'immagine diventa altezza.

---

## 7. Layer FILL

Il Fill layer produce valori solidi.

### 7.1 Fill su Base Color

Nodi:

```text
ShaderNodeRGB
  Color = fill_color
```

Poi:

```text
current color + fill color
  -> ShaderNodeMix RGBA
```

### 7.2 Fill su Roughness / Metallic / Transmission / Alpha

Se il Fill e' routato direttamente a un canale scalar, il colore viene
convertito a valore:

```text
ShaderNodeRGB
  -> RGB to BW
  -> scalar
```

Se invece usa un canale PBR dedicato:

```text
ShaderNodeValue
  value = roughness_fill / metallic_fill / transmission_fill / alpha_fill
```

Poi:

```text
current scalar + layer scalar
  -> ShaderNodeMix RGBA
  -> RGB to BW
```

### 7.3 Fill su Emission

Nodi:

```text
ShaderNodeRGB
  Color = emission_color
```

Poi viene miscelato nella corsia emission.

---

## 8. Layer PROCEDURAL

Il Procedural layer e' il piu' importante commercialmente, perche' genera
texture senza immagini esterne.

Ogni procedural ha una parte comune:

```text
Texture Coordinate
  -> eventuale UV Map / Object / Generated
  -> eventuale coordinate normalization
  -> eventuale coordinate transform
  -> Mapping
  -> eventuale vector distortion
  -> procedural texture
  -> ColorRamp o Mix colori custom
```

### 8.1 Coordinate

Coordinate disponibili:

- Generated
- Object
- UV

Per `Object`, puo' essere attiva la normalizzazione delle coordinate, utile
per ridurre deformazioni su oggetti non uniformi.

### 8.2 Mapping

Il layer crea un nodo:

```text
ShaderNodeMapping
```

Parametri:

- Location X/Y/Z
- Rotation X/Y/Z
- Scale X/Y/Z
- Mapping Type: Point, Texture, Vector, Normal

Questi valori trasformano le coordinate prima che entrino nella texture.

### 8.3 Coordinate transform

Prima o attorno al Mapping possono essere inseriti nodi matematici per:

- None
- Polar
- Spherical
- Swirl
- Cylindrical

Questi trasformano lo spazio, non il colore. Servono per pattern circolari,
sferici, spirali o cilindrici.

### 8.4 Vector distortion

Se `proc_vector_distortion` e' maggiore di 0, viene iniettato rumore nelle
coordinate.

Schema logico:

```text
Mapping Vector
  -> Noise/Math distortion
  -> Vector distorto
  -> Procedural Texture Vector
```

Serve per rendere meno geometrici pattern come cracks, hex, voronoi e noise.

### 8.5 Procedural su Base Color

Schema standard:

```text
procedural Fac
  -> ColorRamp
       Color1
       Color2
       optional Color3
  -> layer color
```

Poi:

```text
current base color + procedural color
  -> ShaderNodeMix RGBA
```

Alcuni procedural non usano il ColorRamp finale perche' generano gia' colore
o hanno una logica a soglia:

- Checker
- Brick
- Magic
- Stripes
- Hex Grid

### 8.6 Procedural su Roughness / Metallic / Transmission / Alpha

Il comportamento desiderato e' coerente con Base Color:

```text
procedural texture
  -> ColorRamp / custom color mix
  -> RGB to BW
  -> scalar layer output
```

Poi:

```text
current scalar + procedural scalar
  -> ShaderNodeMix RGBA con blend mode del layer
  -> RGB to BW
  -> canale finale
```

Questo permette di usare Multiply/Subtract/Difference anche su roughness e
metallic.

### 8.7 Procedural su Emission

L'emission non usa semplicemente il colore del procedural. Costruisce una
maschera di accensione.

Schema:

```text
procedural Fac
  -> invert / power / threshold / falloff
  -> optional emission selector
  -> Mix nero / emission_color
  -> emission color
```

Parametri importanti:

- `proc_emission_threshold`
- `proc_emission_falloff`
- `emission_color`
- `emission_strength`

Questo serve per accendere solo alcune aree di pattern come crepe, celle,
linee o pannelli sci-fi.

### 8.8 Procedural su Bump

Per il bump viene usato un helper scalar:

```text
procedural Fac
  -> Bump Height
  -> ShaderNodeBump
  -> Mix Vector con normal corrente
```

Parametri:

- `bump_strength`
- `bump_distance`

Se esiste gia' una Normal Map, il bump la riceve come input `Normal`, quindi
il bump perturba la normal gia' costruita invece di ripartire da una superficie
piatta.

---

## 9. Procedural type: nodi specifici

Questa sezione descrive cosa crea ogni `proc_type`.

### 9.1 Noise

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Noise Texture
  -> ColorRamp
```

Input collegati:

- Scale
- Detail
- Roughness
- Lacunarity
- Distortion

Output:

- `Fac` del Noise guida la ColorRamp.

Uso ideale:

- sporco;
- variazioni colore;
- ruggine;
- roughness breakup;
- bump fine.

### 9.2 Voronoi

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Voronoi Texture
  -> ColorRamp
```

Input:

- Feature
- Distance
- Scale
- Randomness
- Detail / Roughness / Lacunarity se supportati dalla versione Blender

Modalita' speciale:

```text
Voronoi Position
  -> White Noise
  -> ColorRamp
```

Questa modalita' produce random per cell, utile per mosaici, celle sci-fi,
greeble e pattern a tasselli.

### 9.3 Wave

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Wave Texture
  -> ColorRamp
```

Input:

- Wave Type: Bands / Rings
- Profile: Sine / Saw / Triangle
- Bands Direction
- Rings Direction
- Scale
- Distortion
- Detail Scale
- Detail Roughness
- Phase Offset

Uso ideale:

- scanline;
- onde;
- fibre;
- venature;
- rigature leggere.

### 9.4 Gradient

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Gradient Texture
  -> ColorRamp
```

Input:

- Gradient Type
- Offset X/Y/Z tramite Mapping Location
- Rotation tramite Mapping
- Scale X/Y/Z tramite Mapping

Nota importante:

- la vecchia idea di `Scale` globale sul gradient era poco utile;
- il controllo reale dello spostamento visibile e' soprattutto `Offset X`;
- Color 3 Position deve restare significativo sulla ColorRamp.

Uso ideale:

- maschere direzionali;
- fade;
- edge tint;
- verniciature a banda;
- effetti layerati manualmente.

### 9.5 Musgrave

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Musgrave Texture
  -> ColorRamp
```

Fallback:

```text
Texture Coordinate
  -> Mapping
  -> Noise Texture
  -> ColorRamp
```

Motivo: in Blender recenti Musgrave e' stato fuso o rimosso in alcune API.

Input:

- Scale
- Detail
- Roughness
- Lacunarity

Uso ideale:

- superfici naturali;
- roccia;
- terreno;
- pattern organici.

### 9.6 Checker

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Checker Texture
```

Input:

- Scale
- Color1
- Color2

Output:

- usa direttamente `Color`, non passa per ColorRamp standard.

Uso ideale:

- griglie;
- pattern tecnici;
- test UV;
- maschere hard-edge.

### 9.7 Brick

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Brick Texture
```

Input:

- Offset
- Offset Frequency
- Squash
- Squash Frequency
- Scale
- Color1
- Color2
- Mortar / Color3
- Mortar Size
- Mortar Smooth
- Bias
- Brick Width
- Row Height

Output:

- usa direttamente `Color`.

Nota per i canali scalar/bump/emission:

Per ottenere una maschera utile, il codice usa colori sentinella:

```text
Color1 = bianco
Color2 = bianco
Mortar = nero
Color -> Separate Color Red
```

In questo modo il valore scalar distingue brick/mortar in modo intuitivo,
invece di prendere l'alternanza casuale fra Color1 e Color2.

Uso ideale:

- mattoni;
- pannelli;
- fughe;
- pattern tile;
- maschere di separazione.

### 9.8 Magic

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Magic Texture
```

Input:

- Scale
- Depth
- Distortion dedicata (`proc_magic_distortion`)

Output:

- usa direttamente `Color`.

Uso ideale:

- effetti astratti;
- pattern organici;
- iridescenze stilizzate.

### 9.9 White Noise

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> White Noise Texture
  -> ColorRamp
```

Output:

- `Value` guida la ColorRamp.

Uso ideale:

- polvere;
- grain;
- dithering;
- micro variazioni roughness.

### 9.10 Dots

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Voronoi Texture F1
  -> Map Range Smoothstep
  -> ColorRamp
```

Input:

- Scale
- Randomness
- Detail / Roughness / Lacunarity se supportati
- Dot Radius
- Dot Softness

Logica:

```text
Distance < Radius => dot
Distance > Radius => background
Softness controlla il bordo
```

Uso ideale:

- splatter;
- pois;
- fori;
- freckles;
- pattern puntinati.

### 9.11 Ridged

Nodi:

```text
Noise Texture
  -> Math Multiply Add
  -> Math Absolute
  -> Math Subtract
  -> Math Power
  -> Math Multiply
  -> ColorRamp
```

Formula logica:

```text
ridge = 1 - abs(noise * 2 - 1)
ridge = pow(ridge, gain)
ridge = ridge * offset
```

Input:

- Scale
- Detail
- Roughness
- Lacunarity
- Distortion
- Ridge Gain
- Ridge Offset

Uso ideale:

- creste;
- vene;
- fulmini;
- roccia;
- crackle.

### 9.12 Cracks

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Voronoi Texture Distance To Edge
  -> Map Range Smoothstep
  -> ColorRamp
```

Input:

- Scale
- Randomness
- Detail / Roughness / Lacunarity se supportati
- Crack Width
- Crack Sharpness

Logica:

```text
Distance to edge bassa => linea di crack
Map Range stringe o ammorbidisce la linea
```

Uso ideale:

- crepe;
- marmo;
- lava;
- ghiaccio;
- ceramica rotta.

### 9.13 Gabor

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Gabor Texture
  -> ColorRamp
```

Fallback:

```text
Texture Coordinate
  -> Mapping
  -> Wave Texture Bands
  -> ColorRamp
```

Input:

- Scale
- Frequency
- Anisotropy
- Orientation

Uso ideale:

- metallo spazzolato;
- graffi direzionali;
- fibre;
- tessuti;
- pattern anisotropici.

### 9.14 Stripes

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Wave Texture BANDS SAW
  -> Map Range Smoothstep
  -> Mix Color1/Color2
```

Con Color 3 attivo:

```text
Wave Fac
  -> Map Range main stripe
  -> Map Range inner stripe
  -> Mix Color2/Color3
  -> Mix Color1/inner result
```

Input:

- Scale
- Direction
- Width
- Sharpness
- Distortion
- Detail
- Detail Scale
- Detail Roughness
- Phase Offset
- Color3 Position come core fraction

Perche' non usa una normale ColorRamp a 3 stop:

- il pattern e' quasi binario;
- una ColorRamp a 3 stop rischia di non campionare davvero il colore medio;
- i due Mix separati rendono Color3 realmente controllabile.

Uso ideale:

- strisce;
- bande sci-fi;
- scanline;
- fibre parallele;
- pattern hard surface.

### 9.15 Hex Grid

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Voronoi Texture Distance To Edge
  -> Map Range Smoothstep
  -> Mix Color1/Color2
```

Con Color 3 attivo:

```text
Distance to edge
  -> Map Range edge
  -> Map Range inner edge
  -> Mix Color2/Color3
  -> Mix Color1/inner result
```

Input:

- Scale
- Randomness
- Detail / Roughness / Lacunarity se supportati
- Edge Width
- Color3 Position come core della linea

Nota:

- con Randomness 0 produce un look honeycomb piu' regolare;
- con Randomness alta diventa piu' Voronoi/cellulare.

Uso ideale:

- honeycomb;
- pannelli;
- scudi energetici;
- sci-fi;
- pattern cellulari.

### 9.16 Marble

Nodi:

```text
Texture Coordinate
  -> Mapping
  -> Wave Texture
  -> ColorRamp

Texture Coordinate
  -> Mapping
  -> Noise Texture
  -> Math Multiply
  -> Phase Offset della Wave
```

Se `Phase Offset` non e' disponibile:

```text
Noise * Turbulence
  -> Math Add
  -> Wave Distortion
```

Input:

- Scale
- Detail
- Roughness
- Distortion
- Turbulence
- Pattern Bands/Rings
- Bands Direction
- Rings Direction
- Wave Profile

Uso ideale:

- marmo;
- pietra venata;
- ghiaccio;
- materiali organici con flusso.

---

## 10. Layer ADJUSTMENT

Gli Adjustment non creano una texture autonoma. Modificano il risultato
corrente del canale.

Schema:

```text
current channel
  -> adjustment nodes
  -> Mix originale/adjusted con opacity
```

Target tramite `output_channel`:

- BASE_COLOR -> modifica Base Color
- ROUGHNESS -> modifica Roughness
- METALLIC -> modifica Metallic
- ALPHA -> modifica Alpha

### 10.1 Adjustment su canali colore

Supporta:

- Hue/Saturation
- Bright/Contrast
- Levels
- Color Balance

Esempio logico:

```text
current color
  -> Hue Saturation node
  -> Mix current/adjusted
```

### 10.2 Adjustment su canali scalar

Supporta solo operazioni sensate su float:

- Bright/Contrast
- Levels

Hue/Saturation e Color Balance non hanno senso su un singolo valore float e
vengono ignorati/pass-through.

Bright/Contrast scalar:

```text
current
  -> Math Subtract 0.5
  -> Math Multiply contrast
  -> Math Add brightness + 0.5
  -> Mix scalar
```

Levels scalar:

```text
current
  -> Map Range input min/max
  -> Math Power gamma
  -> Map Range output min/max
  -> Mix scalar
```

---

## 11. Layer GROUP

Il Group layer non e' una texture. E' un contenitore.

Per Base Color:

```text
children visibili
  -> composizione interna
  -> group color output
  -> mix del gruppo nella stack principale
```

Il gruppo sintetizza anche un alpha:

```text
ShaderNodeValue = 1.0
```

Questo serve per il clipping mask dei layer sopra.

Per i canali PBR non-base, il rebuild usa la lista espansa dei figli: in
pratica i figli contribuiscono alle corsie roughness/metallic/emission/etc.
come layer normali.

Limite attuale:

- i gruppi non sono veri node groups Blender;
- sono una struttura logica dell'addon;
- il rebuild ricrea i nodi, non incapsula i figli in un gruppo shader
  modificabile manualmente.

---

## 12. Layer REFERENCE

Il Reference layer riusa il pattern di un altro layer, ma mantiene i propri:

- opacity;
- blend mode;
- mask;
- fresnel;
- clipping;
- output channel;
- channel fill values;
- per-channel blend override.

Schema:

```text
referenced layer pattern
  -> duplicato come sorgente
  -> convertito al canale richiesto
  -> miscelato usando le proprieta' del Reference
```

Tipi sorgente supportati:

- PROCEDURAL
- PAINT
- FILL

Tipi evitati:

- REFERENCE verso REFERENCE, per evitare cicli;
- GROUP/ADJUSTMENT come sorgente diretta non sono pienamente supportati.

Caso scalar:

```text
referenced color
  -> Separate Color Red
  -> optional multiply by roughness_fill/metallic_fill/transmission_fill/alpha_fill
  -> Mix scalar
```

Uso ideale:

- riutilizzare lo stesso noise come base color e roughness con blend diversi;
- creare variazioni dello stesso procedural senza duplicare a mano;
- fare materiali piu' coerenti con meno layer reali.

---

## 13. Normal channel

Normal e' gestita da una funzione separata perche' non e' un colore.

Per ogni layer che contribuisce a `normal`:

```text
UV Map
  -> Mapping normal dedicato
  -> Image Texture Non-Color
  -> Normal Map
  -> Mix Vector
```

Parametri:

- normal image;
- normal strength;
- normal tile scale;
- normal rotation;
- opacity;
- mask;
- fresnel;
- clipping.

Se e' il primo normal layer e ha modulatore, viene creata una baseline:

```text
Geometry Normal
```

Cosicche':

```text
mask = 0 => normal originale
mask = 1 => normal map del layer
```

---

## 14. Bump channel

Bump viene costruito dopo Normal.

Se esiste una normal map, il bump la riceve come input:

```text
normal_out -> Bump Normal input
```

Per procedural:

```text
procedural Fac
  -> Bump Height
  -> Bump Normal
  -> Mix Vector
```

Per paint:

```text
Image Texture Non-Color
  -> Separate Color Red
  -> Bump Height
  -> Bump Normal
  -> Mix Vector
```

Parametri:

- bump strength;
- bump distance;
- opacity;
- mask;
- fresnel;
- clipping.

Risultato:

```text
final_normal = bump_out or normal_out
final_normal -> Principled BSDF Normal
```

---

## 15. Alpha channel e trasparenza Cycles/Eevee

Il canale Alpha e' speciale.

Il valore alpha viene collegato sia al Principled BSDF Alpha sia a un wrapper
shader:

```text
Transparent BSDF
Principled BSDF
  -> Mix Shader factor = alpha
  -> Material Output Surface
```

Motivo:

- in alcune versioni di Blender/Cycles, collegare solo `Principled Alpha`
  non rende davvero trasparente l'oggetto;
- il Mix Shader con Transparent BSDF e' piu' portabile tra Eevee e Cycles.

Convenzione:

```text
alpha = 0 => trasparente
alpha = 1 => opaco
```

L'addon sincronizza anche le impostazioni materiale per evitare che Eevee
ignori il canale alpha.

---

## 16. Maschere

Ogni layer puo' avere una mask principale e una mask B opzionale.

### 16.1 Fonti mask

Fonti supportate:

- IMAGE
- AO
- POINTINESS
- EDGE_WEAR
- DIRT
- CURVATURE_SMART

IMAGE:

```text
Image Texture Non-Color
  -> Separate Color Red
```

AO:

```text
Ambient Occlusion
  -> AO output
```

POINTINESS:

```text
Geometry
  -> Pointiness
```

Smart generators:

```text
geometry / AO / math nodes
  -> generated mask
```

### 16.2 Invert

Se invert e' attivo:

```text
1.0 - mask
```

Nodo:

```text
ShaderNodeMath SUBTRACT
```

### 16.3 Combinare mask A e mask B

Modalita':

- Multiply
- Minimum
- Maximum
- Add
- Subtract
- Difference
- Screen

Screen viene costruito come:

```text
1 - (1 - A) * (1 - B)
```

Difference:

```text
abs(A - B)
```

### 16.4 Refinement mask

Dopo la combinazione:

```text
combined
  -> contrast
  -> levels input/gamma/output
  -> softness
  -> multiply opacity
  -> optional multiply layer alpha
  -> Mix Factor
```

Contrast:

```text
Math Power
```

Levels:

```text
Map Range
Math Power
Map Range
```

Softness:

```text
Map Range Smoothstep
```

---

## 17. Per-channel blend overrides

Ogni layer ha una blend mode principale, ma puo' sovrascriverla per canale:

- base color;
- roughness;
- metallic;
- emission;
- transmission;
- alpha.

Default:

```text
INHERIT
```

Significa:

```text
usa la blend_mode principale del layer
```

Esempio:

```text
Layer Noise
main blend = Multiply
roughness override = Add
metallic override = Subtract

=> base color usa Multiply
=> roughness usa Add
=> metallic usa Subtract
```

Su alpha, pero', il sistema usa `alpha_math_operation`, non le blend mode
colore.

---

## 18. User/custom slot dopo il canale

Dopo che una corsia PBR e' stata costruita, prima del Principled BSDF puo'
esserci un punto di passaggio:

```text
channel output
  -> user slot / saved custom link restore
  -> reroute
  -> BSDF input
```

Questo serve all'idea di permettere modifiche manuali nello shader editor
senza perderle completamente al rebuild.

Nota:

- il bottone Custom Slots e' stato rimosso dalla UI;
- alcune parti di preservazione collegamenti custom restano nel codice;
- la modalita' piu' sicura per edit manuale resta `Convert to Editable Shader`,
  perche' blocca i rebuild automatici.

---

## 19. Convert to Editable Shader

Quando il materiale e' in editable shader:

```text
tlm.shader_editable = True
```

Il rebuild viene saltato.

Effetto:

- lo shader resta modificabile a mano;
- i nodi non vengono ricreati dall'addon;
- i controlli che modificherebbero la stack non dovrebbero permettere
  interazioni distruttive;
- e' una modalita' di consegna/finalizzazione piu' che di editing layer.

Questo risolve il conflitto:

```text
modifica manuale nello Shader Editor
  + nuova interazione addon
  = rebuild che sovrascrive i nodi manuali
```

Con editable shader, il rebuild non parte.

---

## 20. Esempi di topologia reale

### 20.1 Fill giallo + Noise ruggine su Base Color

Layer 1 Fill:

```text
RGB yellow -> current base color
```

Layer 2 Procedural Noise:

```text
TexCoord -> Mapping -> Noise -> ColorRamp ruggine
current yellow + rust color -> Mix RGBA Multiply/Mix
```

Risultato:

```text
Mix result -> BSDF Base Color
```

### 20.2 Noise su Roughness con blend Subtract

```text
Value default roughness 0.5
Noise -> ColorRamp -> RGB to BW
default/current + noise scalar -> Mix RGBA Subtract
Mix result -> RGB to BW -> BSDF Roughness
```

Questo e' corretto anche se sembra strano vedere nodi colore nella corsia
roughness.

### 20.3 Paint su Metallic

```text
Paint Image Non-Color
  Color -> Separate Color Red -> metallic value
  Alpha -> factor

current metallic + paint red
  -> Mix RGBA
  -> RGB to BW
  -> BSDF Metallic
```

Zone non dipinte:

```text
Alpha = 0 => non sovrascrive il metallic sotto
```

### 20.4 Procedural Cracks su Emission

```text
TexCoord -> Mapping -> Voronoi Distance To Edge -> Map Range
  -> threshold/falloff emission
  -> Mix black/emission_color
  -> BSDF Emission Color

max emission strength -> BSDF Emission Strength
```

### 20.5 Normal + Bump

```text
Normal image
  -> Normal Map
  -> normal_out

Procedural noise
  -> Bump Height
  -> Bump Normal input riceve normal_out
  -> final normal

final normal -> BSDF Normal
```

---

## 21. Cosa guardare quando un materiale non convince

Se il risultato visivo e' povero, spesso il problema non e' il numero di
layer, ma il ruolo assegnato ai layer.

Checklist tecnica:

1. Il base color sta descrivendo solo colore, non anche profondita'?
2. Roughness ha micro-variazione separata dal colore?
3. Metallic e' quasi sempre binario o molto controllato?
4. Bump/Normal stanno raccontando rilievo coerente con il pattern visibile?
5. I layer Paint stanno aggiungendo intenzione umana, usura direzionale,
   graffi, bordi, colature, zone logiche?
6. I procedural sono usati come maschere, non solo come texture decorative?
7. Le blend mode scalar stanno lavorando sul canale giusto?
8. Il ColorRamp del procedural esiste anche sui canali roughness/metallic?
9. La mask alpha del paint sta impedendo override globali indesiderati?
10. Il materiale ha troppe frequenze simili sovrapposte?

Un materiale buono di solito non ha tanti layer casuali. Ha pochi layer con
ruoli chiari:

```text
macro forma
  -> variazione media
  -> micro dettaglio
  -> danno/usura intenzionale
  -> risposta fisica PBR
```

---

## 22. Punti di attenzione tecnici

### 22.1 Procedural alpha

Molti procedural ritornano:

```text
(color_out, None)
```

Quindi non hanno un vero alpha socket proprio. Se vuoi buchi, cutout o zone
non contribuenti, servono:

- output alpha dedicato;
- paint alpha;
- mask;
- procedural routato ad alpha;
- clipping mask.

### 22.2 Checker/Brick/Magic

Questi procedural bypassano la ColorRamp standard sul Base Color perche'
Blender fornisce gia' un output Color utile.

Conseguenza:

- Color1/Color2 sono diretti;
- per scalar/bump il sistema deve ricavare un fac coerente separatamente.

### 22.3 Stripes/Hex con Color3

Non usano una ColorRamp classica a 3 stop. Usano Mix separati perche' la
maschera e' binaria e il colore medio rischierebbe di non apparire.

### 22.4 Alpha in Cycles

La trasparenza non si affida solo al Principled Alpha. Il wrapper
Transparent BSDF + Mix Shader e' intenzionale.

### 22.5 Editable shader

Quando si entra in editable shader, lo shader non deve piu' essere trattato
come uno stack TLM rigenerabile liberamente. Ogni nuova interazione addon che
forza un rebuild rischia di cancellare modifiche manuali.

---

## 23. File e funzioni principali

`properties.py`

- definisce tipi layer;
- definisce blend mode;
- definisce output channel;
- definisce parametri paint/fill/procedural/mask/PBR;
- definisce callback hot update e rebuild.

`compositing.py`

- `_build_base_color`: costruisce Base Color e gestisce Group alpha;
- `_build_channel`: costruisce roughness, metallic, emission, transmission,
  alpha e parte della logica reference/adjustment;
- `_build_procedural_node`: costruisce il colore dei procedural;
- `_build_proc_fac_node`: costruisce un fac scalar dei procedural per bump e
  canali scalar speciali;
- `_build_normal_channel`: costruisce normal map;
- `_build_bump_channel`: costruisce bump;
- `_new_mix`: mix colore;
- `_new_mix_scalar`: mix scalar con supporto blend mode;
- `_new_mix_vector`: mix normal/bump;
- `_new_alpha_math_composite`: compositing alpha;
- `_build_mask_slot`: sorgente mask;
- `_apply_mask`: pipeline mask completa;
- `_wire_alpha_via_transparent_bsdf`: trasparenza engine-portable;
- `rebuild_node_tree`: orchestrazione completa.

---

## 24. Conclusione operativa

Il programma non e' solo un generatore di nodi casuali: e' un compositor PBR
per-layer. Ogni layer puo' essere:

- sorgente colore;
- sorgente scalar;
- sorgente normal/bump;
- sorgente emission;
- sorgente alpha;
- maschera;
- adjustment;
- riferimento riusabile;
- contenitore logico.

Per costruire materiali promozionali validi, il punto non e' aumentare il
numero di layer. Il punto e' progettare il materiale come una catena fisica:

```text
colore locale
  + variazione cromatica
  + roughness coerente
  + metallic controllato
  + bump leggibile
  + paint manuale nei punti narrativi
  + maschere per usura e selezione
```

Questa mappa dei nodi serve proprio a verificare, materiale per materiale,
se ogni layer sta facendo un lavoro reale o se sta solo aggiungendo rumore.
