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
```

## 1. Orchestration: register = role
Arranged the way a conductor would: the most continuous and important variable sings the melody up high, the slowest and most cumulative one is the foundation down low, and punctual events become rhythm.

| voice (high → low) | variable | instrument | why | rhythmic values |
|---|---|---|---|---|
| **Melody** G4–G6 | temperature | **Violins** | the main, continuous story: the line you follow | one legato line; quarters, halves and tied notes; a breath every 4 bars |
| **Rhythm** C4–A5 | precipitation | **Pizzicato strings** | rain = many small drops | chord arpeggio: half → quarter → eighth notes as rain increases |
| **Harmony** C3–G4 | dust | **Piano** | slow, diffuse haze filling the middle | chord: whole → half → quarter notes; voice-led |
| **Swell** F2–C4 | smoke | **Trombones** | episodic and threatening | held open fifth with a crescendo; a suspended 4th (unresolved) when smoke is thick |
| **Percussion** E2–G3 | lightning | **Timpani** (tuned to the chord) | sudden events | beat 1 → beats 1 & 3 → every beat → roll; accented downbeat when a storm arrives |
| **Bass** E1–D3 | CO₂ | **Contrabass** | slowest and cumulative: the ground everything stands on | one root per bar, smallest leap |

## 2. Time: everything lives in bars
- **1 frame = 1 bar** of 4 beats (24 months → 24 bars), plus a 2-bar final cadence on the tonic.
- **Harmony:** one chord every **2 bars**, always changing on a bar line. CO₂ climbs a **tension ladder** I → vi → IV → ii → V (in minor: i → VI → iv → VII → v). Low CO₂ is home, high CO₂ is the dominant. Hysteresis keeps small wiggles from changing the chord. The contrabass plays that root, and every instrument takes its notes from the chord (the violins may add scale passing notes on weak beats).
- **Entries and exits are decided per bar** with hysteresis (enter at `gate`, leave below 70% of it). Density levels are per bar too, so instruments never flicker on and off beat by beat.
- Each section has **its own rhythmic value** (bass = whole notes, piano = whole/half/quarter, pizzicato = up to 8ths, timpani = beats). Parts interlock instead of piling up. The script prints a polyphony check: typically 3–6 notes sound at once, 8 at most.
- **Contour vs presence** (`--mapping hybrid`): pitch follows the value *relative to its range in the zone*, so every line is melodic. Entries and loudness follow the *absolute* amount: no dust over Europe means no piano.

Without a CO₂ map, temperature picks the chords (`--harmony surface_temperature`). With neither, the harmony falls back to I–vi–IV–V. Everything above can be tuned in the `ORCHESTRA` table: register, gain, gate, density thresholds, mix level and pan.

## 3. Regional palettes (fun mode)
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
- with `--render`: **`<name>.wav` with real sampled instruments** (GeneralUser GS SoundFont + concert-hall reverb, see `../soundfonts/README.md`). Any MIDI file can also be rendered with `python music/render_audio.py file.mid`

Good demo zones: `india` (monsoon pizzicato + storm timpani), `sahara` (dust piano + storms), `europe` (summer heatwave: smoke trombones + thunder), `arctic` (violins + contrabass, CO₂ breathing), `global`.
