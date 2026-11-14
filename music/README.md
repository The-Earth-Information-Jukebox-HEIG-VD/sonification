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

## Orchestra: adding a variable
Add an entry to `INSTRUMENTS` (GM program + its own MIDI channel). Any map whose metadata `variable` matches an entry picks up that instrument automatically. Then load all the `.mid` files into one DAW session, or merge them (next step).
