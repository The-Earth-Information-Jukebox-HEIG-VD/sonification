# Sonification API: contract for the frontend

Backend for the Earth Information Jukebox. The frontend sends **what to listen to** (dataset, zone or selected cells, time window, a few musical options). The backend composes the orchestra and returns **the score as JSON plus URLs** to the MIDI, the per-instrument stems and the rendered audio.

- Base URL (dev): `http://localhost:8000`
- Interactive docs (try every call in the browser): **`http://localhost:8000/docs`**
- CORS is open by default (`JUKEBOX_CORS=*`), so the frontend can call it from any dev port.

## Run it
```bash
pip install -r requirements.txt && pip install --no-deps tinysoundfont
python music/sampler.py --download        # optional: real orchestral samples (VSCO 2 CE, ~440 MB, once)
pip install netCDF4 && python simulation/build_realistic_dataset.py     # real NASA/NOAA data (default)
python simulation/simulate_variables.py --scenario both --warming 0.5 --years 2 --frames 24   # simulated demo data
uvicorn backend.app:app --reload --port 8000
```
| env var | default | meaning |
|---|---|---|
| `JUKEBOX_DATA_DIR` | `simulation/output` | folder with the intensity maps (`<dataset>_<variable>.npz` + `.json`, see `simulation/INTENSITY_MAP_SPEC.md`). When the real extraction is ready, point this at its output folder: nothing else changes. |
| `JUKEBOX_CACHE_DIR` | `backend/cache` | generated compositions (safe to delete) |
| `JUKEBOX_CORS` | `*` | allowed origins, comma-separated |

## Endpoints
| method | path | what |
|---|---|---|
| GET | `/api/health` | status, number of datasets, whether audio/MP3 rendering is available |
| GET | `/api/options` | everything the UI needs for its menus: zones (with bbox), palettes, keys, scales, the orchestra (which instrument plays which variable), default request |
| GET | `/api/datasets` | available datasets: id, variables (units, range, instrument), number of frames, time range |
| **POST** | **`/api/compose`** | **compose a piece** (see below) |
| GET | `/api/compositions/{id}` | fetch an earlier composition again (`?include_notes=false` for a light payload) |
| GET | `/api/files/{id}/{file}` | download a generated file (MIDI, MP3/WAV, JSON, PNG); the compose response gives you the full URLs |

## POST `/api/compose`
All fields are optional. The smallest request is `{}` (global zone, default dataset).
```json
{
  "dataset": "sim_both_24f_1deg",
  "zone": "switzerland",
  "cells": null, "cells_grid": null,
  "bbox": null,
  "surface": "all",
  "start": "2024-06", "end": "2025-03",
  "variables": null,
  "palette": "orchestra",
  "key": "C", "scale": null, "bpm": 76,
  "audio": true, "audio_format": "mp3",
  "include_notes": true,
  "score_image": false
}
```
| field | type | notes |
|---|---|---|
| `dataset` | string | id from `/api/datasets`; default = the real data if present, otherwise the simulation |
| `zone` | string | preset id from `/api/options` (`switzerland`, `india`, `europe`, `global`…) |
| `cells` | `[[row, col], ...]` | **custom area selected on the map, any shape**: the positions of the selected cells on the data array. Row 0 is the top of the array (north), column 0 is the left edge (180°W). Overrides `zone` and `bbox`. Order and duplicates do not matter |
| `cells_grid` | `[rows, cols]` | shape of the array the positions refer to. Leave it out when that is the dataset's own grid (`variables[v].grid` in `/api/datasets`, e.g. `[180, 360]`). Send it when your array has another resolution (e.g. `[720, 1440]`): the selection is rescaled onto the data |
| `bbox` | `[lat_min, lat_max, lon_min, lon_max]` | older way to send a custom area, as a lat/lon box; still accepted. Overrides `zone`. `lon_min > lon_max` = crosses the dateline |
| `surface` | `all` / `land` / `ocean` | which cells of the zone to average |
| `start`, `end` | ISO date prefix (`"2024-06"`) or frame index | time window; one frame = one bar |
| `variables` | list | subset of instruments, e.g. `["surface_temperature","co2"]` |
| `palette` | string | `orchestra` (default), `auto` (regional instruments for the zone, fun mode), or a palette id |
| `key`, `scale`, `bpm` | | musical options; `scale` default = major (or the palette's mode) |
| `audio`, `audio_format` | bool, `mp3`/`wav` | render audio (≈ +2 s with the SoundFont, ≈ +5–10 s with orchestral samples). `false` = MIDI + JSON only (≈ 0.5 s) |
| `audio_engine` | `auto` / `vsco` / `sf2` | `auto`: real orchestral recordings (VSCO 2 CE) if installed on the server, otherwise the SoundFont. `GET /api/health` reports `orchestral_samples` |
| `style` | `data` (default) / `musical` | **data**: every note follows the data (pitch = value, loudness/density = amount, anomaly markers). **musical**: orchestral arrangement with harmony, intro, climax |
| `quantize` | `scale` / `pentatonic` / `chromatic` | data style: pitch grid (chromatic = finest resolution) |
| `markers` | bool (true) | data style: triangle on unusually hot months, woodblock on cold ones |
| `intro` | bool (true) | musical style only: "meet the orchestra" intro |
| `tension` | bool (true) | musical style only: anomalies → minor mode, 7ths, agitation |
| `climax` | bool (true) | musical style only: highlight the most anomalous moment |
| `humanize` | 0–1 (0.5) | musical style only: phrase dynamics and human timing |
| `include_notes` | bool | `false` drops the note lists from the response (≈ half the size) |
| `score_image` | bool | also make a PNG of the score |

**Timing:** first request ≈ 2–3 s with MP3, ≈ 0.5 s without audio. **The same request again is served from cache in about 10 ms** (the `id` is a hash of the request and the data).

**Errors:** `422` invalid input (unknown zone, bad bbox, malformed `cells`…), `404` unknown dataset, `400` nothing to play (e.g. a time window with no data, unknown variable, positions outside the array). The body always has a `detail` message you can show.

### Response
```json
{
  "id": "195820c264227acb",
  "cached": false,
  "elapsed_s": 2.64,
  "request": {
    "dataset": "sim_both_24f_1deg",
    "zone": "switzerland",
    "bbox": null,
    "palette": "orchestra",
    "key": "C",
    "scale": null,
    "bpm": 76,
    "audio": true,
    "audio_format": "mp3"
  },
  "files": {
    "midi": "http://localhost:8000/api/files/195820c264227acb/score.mid",
    "score_json": "…",
    "stems": {
      "co2": "http://localhost:8000/api/files/195820c264227acb/score_stems/co2.mid",
      "…": "…"
    },
    "audio": "http://localhost:8000/api/files/195820c264227acb/score.mp3",
    "score_image": null
  },
  "score": {
    "zone": "switzerland",
    "bbox": [
      45.8,
      47.8,
      5.9,
      10.5
    ],
    "surface": "all",
    "palette": "orchestra",
    "key": "C",
    "scale": "major",
    "bpm": 76,
    "beats_per_bar": 4,
    "n_bars": 24,
    "total_beats": 104,
    "duration_s": 82.11,
    "time_start": "2024-01-15T00:00:00Z",
    "time_end": "2025-12-15T01:30:00Z",
    "harmony_source": "co2",
    "bars": [
      {
        "index": 0,
        "start_beat": 0,
        "time": "2024-01-15T00:00:00Z",
        "chord": "Am"
      },
      {
        "index": 1,
        "start_beat": 4,
        "time": "2024-02-14T10:30:00Z",
        "chord": "Am"
      },
      "…"
    ],
    "instruments": {
      "surface_temperature": {
        "name": "Temperature - Piano (chords)",
        "role": "melody",
        "program": 49,
        "channel": 0,
        "pan": 40,
        "register": [
          67,
          91
        ],
        "units": "degC",
        "bars_playing": 24,
        "values_per_beat": [
          -0.0225,
          -0.0547,
          -0.0869,
          -0.1191,
          "…"
        ],
        "presence_per_beat": [
          0.5,
          0.499,
          0.499,
          0.499,
          "…"
        ],
        "levels_per_bar": [
          1,
          1,
          1,
          1,
          "…"
        ],
        "notes": [
          {
            "pitch": 69,
            "start": 0.0,
            "duration": 0.97,
            "velocity": 66
          },
          {
            "pitch": 67,
            "start": 1.0,
            "duration": 0.97,
            "velocity": 59
          },
          "…"
        ]
      },
      "…": "…"
    },
    "stats": {
      "notes_total": 192,
      "polyphony_mean": 3.45,
      "polyphony_max": 7
    }
  }
}

```

### New in v1.4 (area as array positions)
- request: `cells` (+ optional `cells_grid`) replaces `bbox` for custom areas, so the area can have any shape. Positions are `[row, col]` indices into the 2-D data array, the same indices you would use on the NumPy array (`array[row, col]`).
- response: `score.zone` is `"custom"`, `score.cells` is the number of data cells that were averaged, and `score.bbox` is the lat/lon box around them (cell centres), handy for zooming the map.
- the echoed `request` does not repeat the positions; it has `n_cells` (how many were sent).

### New in v1.2
- `style` (default **`data`**): in data style `bars[i].chord` and `mode` are `null`, `sections` is just `[data]`, `intro_bars` = 0, and there's an extra instrument `anomaly_markers` (GM drums: 81 = triangle = hot, 77 = woodblock = cold). `bars[i].tension` is still reported (from the anomalies) in both styles, which is handy for the visuals.
- each instrument: `pitch_range` (lowest and highest note used), so the UI can draw a pitch ↔ value legend from `values_per_beat`.

### New in v1.1 (storytelling)
- `score.sections`: `[{name: "intro"|"data"|"climax"|"outro", start_bar, bars, start_s, ...}]`. The intro has `entries` (`{bar, start_s, variable, instrument, role, units}`) for announcing each instrument; the climax has `time` (data date) and `tension`.
- `score.bars[i]`: now also `start_s`, `tension` (0–1), `mode` (`major`/`minor`), `climax` (bool).
- `score.instruments[v]`: also `anomaly_z_per_bar` (departure from normal, in standard deviations) and `source` (which dataset; `"REAL"` when observed).
- every note: also `time_s` and `duration_s` in **real seconds**. **Use these** rather than `start * 60 / bpm`, because the outro slows down (`score.tempo_map`).
- `score.intro_bars`: the data starts at bar `intro_bars`, at `sections[name=data].start_s` seconds.
- `score.stats`: also `tense_bars` and `max_tension`.

### How to use it in the UI
- **Just play it:** `<audio src={files.audio}>`. The MP3 is the whole orchestra with real sampled instruments.
- **Sync visuals with the music:** each note has `time_s`. Bar *i* starts at `bars[i].start_s` seconds and corresponds to the data date `bars[i].time`. Use that to move a cursor on the map or timeline, or highlight the current chord. `bars[i].tension` is great for a red "anomaly" glow, and the climax section for a highlight.
- **Show what each instrument means:** `instruments[var].name` (e.g. "Temperature - Piano (chords)", "Rain - Violins (melody)"), `units`, `values_per_beat` (the real data value, 4 per bar) and `presence_per_beat` (0–1, how strongly that phenomenon is present). Good for live gauges, accessible text ("Temperature 18.4 °C, rising"), or mute/solo buttons.
- **Mute/solo or a custom web synth:** use `files.stems` (one MIDI per instrument) or the `notes` arrays directly, e.g. with Tone.js, smplr or SpessaSynth.
- **Data timeline:** `time_start`, `time_end`, `duration_s`, and `n_bars` (= number of data frames).

### Examples
```bash
curl -X POST localhost:8000/api/compose -H 'Content-Type: application/json' \
     -d '{"zone":"india"}'
curl -X POST localhost:8000/api/compose -H 'Content-Type: application/json' \
     -d '{"cells":[[42,186],[42,187],[43,186],[43,187],[43,188]],"start":"2024-06","end":"2024-12","audio":false}'
curl -X POST localhost:8000/api/compose -H 'Content-Type: application/json' \
     -d '{"cells":[[170,744],[170,745],[171,744]],"cells_grid":[720,1440]}'
curl -X POST localhost:8000/api/compose -H 'Content-Type: application/json' \
     -d '{"zone":"japan","palette":"auto","include_notes":false}'
```
```js
const res = await fetch("http://localhost:8000/api/compose", {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({ zone: "switzerland" })
});
const { files, score } = await res.json();
audio.src = files.audio;
```
