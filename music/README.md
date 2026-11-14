# Music: zone → time series → chords → MIDI

`zone_to_midi.py` turns an intensity map (see `../simulation/INTENSITY_MAP_SPEC.md`) into one MIDI track per variable/instrument. It needs only numpy; matplotlib is needed for `--preview` (piano roll PNG plus a rough WAV synth preview).

## Pipeline
1. **Zone:** a preset (`--zone europe`) or a custom box (`--bbox lat_min,lat_max,lon_min,lon_max`; boxes that cross the dateline work). `--surface land|ocean` filters cells.
2. **Time series:** area-weighted (cos lat) mean of the zone for each frame. NaN cells are ignored. The spatial std is kept too.
3. **Resample:** `--steps-per-frame 4` adds smooth (Catmull-Rom) interpolation between frames. Add `--loop` for cyclic data (12 months).
4. **Pitch:** each value maps onto a scale (`--key A --scale minor`) within `--low/--high` (MIDI numbers).
   - `--mapping relative` (default): the zone's own min..max fills the whole range, which is the most melodic option.
   - `--mapping absolute`: the fixed vmin..vmax from the metadata, so the same °C gives the same note in every zone. Use this to compare zones or to combine instruments in the orchestra.
5. **Chordify:** a diatonic chord built on that scale degree, so it always stays in key. The data note is the **top voice** (`--voice top`), so the ear follows the data.
   - `--chord auto` (default): a triad, plus a 7th when the value changes quickly and a 9th when it changes very quickly. **Fast change = harmonic tension.**
   - `none | power | triad | seventh | ninth` are fixed chord types. `--bass` adds the data note 2 octaves lower.
6. **Dynamics:** `--velocity variability` (default) makes it louder when the zone is more heterogeneous (spatial std). `value` makes it louder when hotter. `fixed` keeps one level.
7. **Rhythm:** `--style block | arpeggio | broken`, `--bpm`, `--beats-per-step`, `--sustain-repeats`.

## Outputs (in `music/output/`)
| file | for |
|---|---|
| `<var>_<zone>_<surface>.mid` | DAW / orchestra (one track; GM program from `INSTRUMENTS`) |
| `.json` | web player: every step with its time, value, melody note and chord notes |
| `.csv` | quick inspection |
| `_pianoroll.png`, `_preview.wav` | with `--preview` |

## Examples
```bash
python simulation/simulate_intensity_map.py --scenario both --warming 0.5 --years 2 --frames 24
M=simulation/output/temperature_sim_both_24f_1deg.npz

python music/zone_to_midi.py $M --zone europe --preview
python music/zone_to_midi.py $M --zone nino34 --surface ocean --key A --scale minor --style arpeggio --bass --preview
python music/zone_to_midi.py $M --bbox 45.8,47.8,5.9,10.5 --name switzerland      # custom zone
python music/zone_to_midi.py --list-zones
```

---

# Orchestra: `orchestra.py`
Combines several variables over the **same zone** into one coherent multi-track score.

```bash
python simulation/simulate_variables.py --scenario both --warming 0.5 --years 2 --frames 24
python music/orchestra.py simulation/output/sim_both_24f_1deg_ --zone india --preview
python music/orchestra.py simulation/output/sim_both_24f_1deg_ --zone sahara --key A --scale minor
python music/orchestra.py simulation/output/sim_both_24f_1deg_ --zone arctic --only surface_temperature,co2
python music/orchestra.py simulation/output/sim_both_24f_1deg_ --cells positions.npy   # any-shape area: (N, 2) int array of [row, col] on the data array (--cells-grid rows,cols if picked on another grid)
```

## 0. Two styles: `data` (default) and `musical`
| | **`style="data"` (default)** | `style="musical"` |
|---|---|---|
| goal | hear the data as precisely as possible | a pleasant orchestral piece that tells the story |
| pitch | **= the variable's value**, linear onto the scale grid of the instrument's register (`quantize`: `scale`, `pentatonic` or `chromatic` for the finest resolution) | chord tones from a shared harmony, smoothed contour |
| loudness / density | = the amount (rain plucks per beat, lightning hits per beat, dust and smoke loudness), deterministic | role-based patterns (arpeggios, comping, rolls) |
| anomalies | **markers**: triangle on unusually hot months, woodblock on cold ones (≥ 1.5 σ) | minor mode, 7ths, agitation, climax |
| added material | none (no harmony, intro, outro, humanisation) | intro, climax, ritardando outro, phrasing |
| fidelity: correlation(value, pitch) on real Swiss data 2022–24 | temperature (chord root) **≥ 0.99**, CO₂ **0.996**, rain **0.995** | 0.92 / 0.46 / 0.22 |

Everything below about harmony, form and expression applies to `style="musical"`. The instrument and register table (section 1) applies to both.

## 1. Orchestration: always on = foundation, sometimes = melody
Variables that are **present all the time** (temperature, CO₂) form the harmonic bed. Variables that **only appear sometimes** (rain, dust, smoke, lightning) carry the **melodies** when they happen, so a melody entering is itself information ("it's raining", "there's dust"). Rain and dust rarely happen together (wet places versus dry places), so their melodies naturally take turns.

| voice | variable | instrument | data style (default) | musical style |
|---|---|---|---|---|
| **Harmony** C3–C5 | temperature (always) | **Piano, chords** | a chord on every beat; **its root = the temperature** (diatonic triad), accent on each bar | comping on the shared harmony, register follows temperature |
| **Bass** E1–D3 | CO₂ (always) | **Contrabass** | pitch = value, held while unchanged | one root per bar |
| **Melody** G4–G6 | rain (sometimes) | **Violins** | pitch = value, loudness = amount, **tremolo** (up to 4 notes/beat) when it pours | gated legato line |
| **Melody** C5–A6 | dust (sometimes) | **Flute** | airy line while dust is present | gated legato line |
| **Melody** F3–D5 | smoke (sometimes) | **French horns** | line while there are fires | gated legato line |
| **Sparkle** F5–F7 | lightning (sometimes) | **Xylophone** | hits per beat = flash density, pitch = value | beat patterns, roll before the climax |

## 2. Time: everything lives in bars
- **1 frame = 1 bar** of 4 beats (24 months → 24 bars), plus a 2-bar final cadence on the tonic.
- **Harmony:** one chord every **2 bars**, always changing on a bar line. CO₂ climbs a **tension ladder** I → vi → IV → ii → V (in minor: i → VI → iv → VII → v). Low CO₂ is home, high CO₂ is the dominant. Hysteresis keeps small wiggles from changing the chord. The contrabass plays that root, and every instrument takes its notes from the chord (the violins may add scale passing notes on weak beats).
- **Entries and exits are decided per bar** with hysteresis (enter at `gate`, leave below 70% of it). Density levels are per bar too, so instruments never flicker on and off beat by beat.
- Each section has **its own rhythmic value** (bass = whole notes, piano = whole/half/quarter, melodies = quarters to halves, xylophone = beats). Parts interlock instead of piling up. The script prints a polyphony check: typically 3–6 notes sound at once, 8 at most.
- **Contour vs presence** (`--mapping hybrid`): pitch follows the value *relative to its range in the zone*, so every line is melodic. Entries and loudness follow the *absolute* amount: no dust over Europe means no piano.

Without a CO₂ map, temperature picks the chords (`--harmony surface_temperature`). With neither, the harmony falls back to I–vi–IV–V. Everything above can be tuned in the `ORCHESTRA` table: register, gain, gate, density thresholds, mix level and pan.

## 3. Storytelling: anomalies, form, expression
**Anomalies → tension.** For every variable the zone's series is compared to its **seasonal normal** (mean + annual cycle fitted on a 3-year reference period, like a climatology baseline). The departure, as a robust z-score (log scale for skewed fields like rain and lightning), becomes **tension per bar**: temperature counts both ways (too hot or too cold), smoke, lightning and dust only above normal. CO₂ is excluded, because its rise is already heard in the bass and the chord ladder.
- tension ≥ 0.5 → the harmony **turns minor** (borrowed chords: I→i, IV→iv, vi→♭VI, V stays major), with hysteresis so it doesn't flicker
- tension ≥ 0.6 → **agitation**: the melodies move in restless eighth notes, the contrabass plays a driving ostinato, the piano pulses faster
- tension ≥ 0.75 → the chords get a **7th** (more dissonance)
- louder overall, and fuller CC11 swells

**Form.**
- **Intro, "meet the orchestra":** the instruments that actually play in this zone enter one bar at a time (contrabass → piano → xylophone → then the melodies: violins, flute, horns), then a bar on V leads into the data. The JSON lists each entry with its time, so the UI can announce "CO₂ → contrabass" (good for accessibility).
- **Climax:** the most anomalous bar (ties go to temperature). Everyone louder for 2 bars, melodies doubled an octave up, xylophone roll into it.
- **Outro:** a 2-bar tonic cadence with a **ritardando** (tempo map down to 70%). Every note in the JSON also has `time_s` and `duration_s` in real seconds.

**Expression.** Phrase hairpins (crescendo to bar 3, relax at bar 4), CC11 swells inside long string notes, and small human timing and velocity variations (`humanize` 0–1).

On real data the climaxes land on real events: Switzerland → Feb 2024 (record-warm winter), El Niño region → Nov 2015, global → Sep 2023 (record month), India → spring 2022 heatwave.
Options: `compose(..., intro=True, tension=True, climax=True, humanize=0.5)`.

## 4. Regional palettes (fun mode)
`--palette auto` swaps in traditional instruments for the zone. The roles, registers and timing stay the same; only the band changes (plus a matching default mode, unless you pass `--scale`).
```bash
python music/orchestra.py simulation/output/sim_both_24f_1deg_ --zone switzerland --palette auto --render
python music/orchestra.py simulation/output/sim_both_24f_1deg_ --zone europe --palette celtic --render   # force one
python music/orchestra.py --list-palettes
```
| palette | zones | band | mode |
|---|---|---|---|
| alps | switzerland, alps | clarinet, Hackbrett, Schwyzerörgeli, alphorn, cowbells/church bells, Bassgeige | major |
| india | india | sitar, santoor, harmonium, shehnai, tabla, drone | harmonic minor |
| japan | japan | shakuhachi, koto, shamisen, shō-like glass, taiko | minor |
| china | china | dizi, guzheng, pipa, sheng, gongs | major |
| north_africa | sahara | ney, oud, desert-blues guitar, mizmar, darbuka | harmonic minor |
| west_africa | west_africa | balafon, kora, guitar, horns, djembe | major |
| celtic | north_atlantic, scotland_ireland | fiddle, harp, accordion, bagpipe drones, bodhrán, cello | dorian |
| americana | usa | fiddle, banjo, steel guitar, blues harmonica, upright bass | major |
| andes | andes | pan flute, charango, guitar, ocarina, bombo | minor |
| tropical | amazon | flute, marimba, bossa guitar, brass, wood block | lydian |
| caribbean | caribbean | steel pan, marimba, guitar, brass, claves | major |
| pacific | nino34, pacific_center | Hawaiian steel guitar, ukulele, marimba, pan flute, steel drums | major |
| russia | siberia | bayan, balalaika, strings, tuba, Orthodox bells | minor |
| ice | arctic, antarctica | celesta, glockenspiel, bowed glass, horns, tubular bells | lydian |
| australia | australia | whistle, clapsticks, guitar, didgeridoo-like drone | dorian |

Instruments in parentheses in the code are the closest General MIDI sound (e.g. Hackbrett = dulcimer, tabla = melodic tom). Every mode has its own chord ladder with no diminished chords.

## Outputs (`music/output/`)
- `orchestra_<zone>_<surface>.mid`: the full score, one track per instrument (pan, volume, expression) plus a chord-name marker track
- `orchestra_<zone>_<surface>_stems/<variable>.mid`: one MIDI per instrument
- `.json`: bars with times and chords, plus per-instrument values per beat, levels per bar and notes (for the web player)
- with `--preview`: `_score.png` (lanes with bar lines and chord names) and `_preview.wav` (rough built-in synth)
- with `--render`: **`<name>.wav` (or `--mp3`) with real orchestral recordings**: VSCO 2 Community Edition samples (CC0) when downloaded (`python music/sampler.py --download`), the GeneralUser GS SoundFont otherwise, plus a concert-hall reverb (see `../soundfonts/README.md`). Any MIDI file can also be rendered with `python music/render_audio.py file.mid [--engine auto|vsco|sf2]`

Demo pieces on **real data** (`music/examples/real/`): Switzerland 2022–24, El Niño 2015–17, global 2022–24, India 2021–23, each as `*_data` (default style) and `*_musical`.

Good zones to try: `india` (monsoon violins, pre-monsoon dust flute, storm xylophone), `sahara` (flute almost always: dust), `europe` (summer heat), `arctic`, `global`.
