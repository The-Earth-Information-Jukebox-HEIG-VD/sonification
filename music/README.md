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
| **Bass** E2–D4 | CO₂ (always) | **Contrabass**, an octave above a usual bass part so small speakers play it | pitch = value, re-bowed on every bar line | one root per bar |
| **Melody** G4–G6 | rain (sometimes) | **Violins** | pitch = value, loudness = amount, **tremolo** (up to 4 notes/beat) when it pours | gated legato line |
| **Melody** C5–A6 | dust (sometimes) | **Flute** | airy line while dust is present | gated legato line |
| **Melody** F3–D5 | smoke (sometimes) | **French horns** | line while there are fires | gated legato line |
| **Melody** G4–E6 (sounds 8va) | snowfall (sometimes) | **Glockenspiel** | pitch = value while it snows (real snow flag from the extraction) | gated line |
| **Percussion** E2–G3 | lightning (sometimes) | **Timpani** (thunder) | hits per beat = flash density, pitch = value | beat patterns, roll before the climax |
| **Swell** E2–C4 | fires (sometimes) | **Trombones** | held, louder with more fires | held swell |
| **Pad** D4–A5 | night lights (yearly) | **Synth pad**, the only electronic sound (human activity) | starts clearly when the yearly value changes, then fades slowly over the whole year | held swell |

### Planned: the two datasets still missing
Not in the `ORCHESTRA` table yet: the data has not arrived. These are the intended choices, to add when it does.

| variable | instrument | data style | why |
|---|---|---|---|
| **Arctic sea ice** (smooth, large seasonal cycle, long decline, polar only) | **Bowed glass** (GM 92, already in the `ice` palette), high and sustained | **loudness = amount of ice**, pitch = value; silent where the area has no sea ice | melting is heard as the sound thinning out and disappearing: the loss is audible as absence |
| **CO₂ exchange with plants** (signed: uptake in the growing season, release the rest of the year) | **A woodwind**, clarinet (GM 71) or bassoon (GM 70), low-middle register | a fixed note marks **zero**: uptake on one side of it, release on the other, so the sign is audible | the planet breathing; it pairs with the contrabass (the CO₂ in the air, and the plants that breathe it) |

To check by ear when the ice data arrives: bowed glass and the night-lights synth pad are both pad-like sounds. They should stay apart (the ice sits higher and follows its amount continuously; the night lights fade once a year), but an A/B listening test should confirm it before release.

## 2. Time: everything lives in bars
- **1 frame = 1 bar** of 4 beats (24 months → 24 bars), plus a 2-bar final cadence on the tonic.
- **Harmony:** one chord every **2 bars**, always changing on a bar line. CO₂ climbs a **tension ladder** I → vi → IV → ii → V (in minor: i → VI → iv → VII → v). Low CO₂ is home, high CO₂ is the dominant. Hysteresis keeps small wiggles from changing the chord. The contrabass plays that root, and every instrument takes its notes from the chord (the violins may add scale passing notes on weak beats).
- **Entries and exits are decided per bar** with hysteresis (enter at `gate`, leave below 70% of it). Density levels are per bar too, so instruments never flicker on and off beat by beat.
- Each section has **its own rhythmic value** (bass = whole notes, piano = whole/half/quarter, melodies = quarters to halves, timpani = beats). Parts interlock instead of piling up. The script prints a polyphony check: typically 3–6 notes sound at once, 8 at most.
- **Contour vs presence** (`--mapping hybrid`): pitch follows the value *relative to its range in the zone*, so every line is melodic. Entries and loudness follow the *absolute* amount: no dust over Europe means no piano.

Without a CO₂ map, temperature picks the chords (`--harmony surface_temperature`). With neither, the harmony falls back to I–vi–IV–V. Everything above can be tuned in the `ORCHESTRA` table: register, gain, gate, density thresholds, mix level and pan.

## 3. Storytelling: anomalies, form, expression
**Anomalies → tension.** For every variable the zone's series is compared to its **seasonal normal** (mean + annual cycle fitted on a 3-year reference period, like a climatology baseline). The departure, as a robust z-score (log scale for skewed fields like rain and lightning), becomes **tension per bar**: temperature counts both ways (too hot or too cold), smoke, lightning and dust only above normal. CO₂ is excluded, because its rise is already heard in the bass and the chord ladder.
- tension ≥ 0.5 → the harmony **turns minor** (borrowed chords: I→i, IV→iv, vi→♭VI, V stays major), with hysteresis so it doesn't flicker
- tension ≥ 0.6 → **agitation**: the melodies move in restless eighth notes, the contrabass plays a driving ostinato, the piano pulses faster
- tension ≥ 0.75 → the chords get a **7th** (more dissonance)
- louder overall, and fuller CC11 swells

**Form.**
- **Intro, "meet the orchestra":** the instruments that actually play in this zone enter one bar at a time (contrabass → piano → timpani → then the melodies: violins, flute, horns), then a bar on V leads into the data. The JSON lists each entry with its time, so the UI can announce "CO₂ → contrabass" (good for accessibility).
- **Climax:** the most anomalous bar (ties go to temperature). Everyone louder for 2 bars, melodies doubled an octave up, timpani roll into it.
- **Outro:** a 2-bar tonic cadence with a **ritardando** (tempo map down to 70%). Every note in the JSON also has `time_s` and `duration_s` in real seconds.

**Expression.** Phrase hairpins (crescendo to bar 3, relax at bar 4), CC11 swells inside long string notes, and small human timing and velocity variations (`humanize` 0–1).

On real data the climaxes land on real events: Switzerland → Feb 2024 (record-warm winter), El Niño region → Nov 2015, global → Sep 2023 (record month), India → spring 2022 heatwave.
Options: `compose(..., intro=True, tension=True, climax=True, humanize=0.5)`.

## 5. Genres and the realistic mode (`--genre`)
`compose(..., genre=...)` (API: `"genre"`) plays the **whole piece in one style**, so the instruments stay the same
and are easy to recognise: `orchestra` (default), `ragtime`, `swing`, `rocknroll`, `funk`, `synthpop`, `house`,
`trap`, or `realistic`. The styles are the ones of the table below (`music/eras.py`); each has its usual tempo when
no `bpm` is given (orchestra and realistic 76, ragtime 96, swing 126, rock'n'roll 150, funk 112, synth-pop 116,
house 124, trap 140). Same data notes on the same beats in every genre; the instrument names in the score follow
the genre (`"Rain - Tenor sax"`).

**`realistic`** (`music/soundscape.py`): no instruments, the sound of each phenomenon, mixed from real recordings:
rain (+ a downpour layer when it pours), snowy wind, campfire (+ fireplace and a low roar for big fires), real
thunderclaps (claps per beat = lightning activity), a heartbeat for the temperature (one lub-dub per beat: the tempo follows the temperature, 60 bpm when cold to 130 bpm when hot, louder when hot; synthesised), city traffic for CO₂, the 50 Hz hum
of street lamps for night lights (synthesised). Levels measured per layer (about −27 dBFS for the temperature bed,
−18 to −25 when a phenomenon is strong). The recordings (CC0 / public domain / CC BY, ~30 MB) are downloaded once
from GitHub at pinned commits: `python music/soundscape.py --download` (credits: `soundfonts/README.md`).

Examples (`music/examples/genres/`): the same data, Switzerland 2017–2018, in all 9 genres
(`switzerland_2017_2018_<genre>.mp3`, measured within 3.5 dB of each other), and Australia's 2019–20 fires in
`realistic` and `rocknroll`.
```bash
O=simulation/output; python music/orchestra.py $O/real_temperature_1880_2025_ $O/real_precipitation_2000_2026_ \
    $O/real_snow_2000_2026_ $O/real_fires_2002_2026_ --zone switzerland --start 2017-01 --end 2018-12 --genre rocknroll --render --mp3
```

**The temperature sets the tempo** (every genre, data style, default; `--fixed-tempo` / `"tempo_from_temperature": false` to turn it off): each bar's tempo = the genre's tempo x (0.75 + 0.5 t), t = the bar's mean temperature on the 5th..95th percentile of the whole series of the area (robust: a few extreme months do not squeeze the others into the middle), so the same temperature is the same tempo in every piece. In `realistic`, the heartbeat goes from 60 to 130 bpm. The MIDI has a tempo change on each bar; `score.tempo_map` and `score.tempo_range` give them. With `duration_s`, the number of bars is chosen with these tempos and the whole map is nudged to last exactly `duration_s`: 30 s covers 11 bars of 2023 against 9 of 1900 in Switzerland (orchestra), 16 against 10 worldwide (realistic: the heart beats at about 127 bpm in 2023, 64–90 in 1900).

## 6. Music through the ages (fun mode, `--eras`)
`compose(..., eras=True)` (API: `"eras": true`) plays each bar in the style of its **date**, so a long timeline goes from a Romantic orchestra in 1880 to trap in 2026:

| from | era | chords (temperature) | bass (CO₂) | melodies (rain …) | drums (GS kit) |
|---|---|---|---|---|---|
| — | Romantic orchestra | the orchestra, unchanged (VSCO samples) | | | none |
| 1910 | Ragtime & early jazz | honky-tonk **stride** (low on 1-3, chord on 2-4) | tuba on 1 and 3 | clarinet | Brush |
| 1930 | Swing & big band | jazz guitar, short chord + 7th on every beat | **walking** (data note on 1, then up the chord, then a step into the next bar) | trumpet | Jazz: ride, hi-hat on 2-4, swing 2:1 |
| 1950 | Rock'n'roll | clean guitar eighths | eighths | tenor sax | Standard: backbeat, light shuffle |
| 1965 | Rock, funk & disco | clavinet stabs | disco octaves | overdriven guitar | Power: four-on-the-floor, open hats |
| 1980 | Synth-pop | polysynth pad | synth bass eighths | square lead | Electronic |
| 1995 | House & electronic | piano stabs on the off-beats | off-beat synth bass | saw lead | Dance |
| 2010 | Modern pop & trap | electric-piano arpeggio | one long **808** per bar | bass+lead synth | 808/909: half-time snare, hi-hat rolls when hot |

- **What stays the data:** every chord root, bass note and melody note is the one data style chose, at the moment it chose it; velocities come from the data notes. A style only changes the instrument, the rhythm that repeats the note, and swing. The walking bass and the stride left hand add passing notes between data notes.
- **What is added:** a drum kit per era, louder and busier when the temperature is high (energy = the piano's presence per bar), a crash and a snare fill at each era change.
- **Sound:** the Romantic bars stay on the orchestra's channels (VSCO samples). The other eras play on free MIDI channels with a program change at each boundary, rendered with the GeneralUser GS SoundFont (a channel that changes program is never sent to VSCO). Each era has a `gain` so the very different GM sounds stay at a similar loudness (measured: −26 dB in the Romantic part, −29 in ragtime, rising to about −21 for house and trap).
- Night lights keep their synth pad in every era. No anomaly markers in this mode. Data style only.
- Examples (`music/examples/eras/`), all on the team's data: global and Switzerland 1880–2025 (one bar = 2 years, about 3 min: about 20–35 s per era; the whole band joins from 2000), Australia and Switzerland 2000–2025 (one bar = 4 months: house then trap with every instrument; Australia's 2019–20 fires, Swiss snow every winter), India 2004–2017.
```bash
O=simulation/output; python music/orchestra.py $O/real_temperature_1880_2025_ $O/real_precipitation_2000_2026_ $O/real_snow_2000_2026_ $O/real_fires_2002_2026_ \
    $O/real_lightning_2017_2023_ $O/real_nightlights_2012_2023_ $O/real_co2_2002_2022_ --zone global --frames-per-bar 24 --bpm 100 --eras --render --mp3
```

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

Demo pieces on the **real daily 2024 data** (`music/examples/daily/`): Switzerland (snow in winter, summer storms), India (monsoon), Valencia (the 29 Oct flood is the wettest beat of the year).

Demo pieces on **real monthly data** (`music/examples/real/`): Switzerland 2022–24, El Niño 2015–17, global 2022–24, India 2021–23, each as `*_data` (default style) and `*_musical`.

Good zones to try: `india` (monsoon violins, pre-monsoon dust flute, storm timpani), `sahara` (flute almost always: dust), `europe` (summer heat), `arctic`, `global`.
