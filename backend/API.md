# Sonification API: contract for the frontend

Backend for the Earth Information Jukebox. The frontend sends **what to listen to** (dataset, zone or selected cells, time window, a few musical options). The backend composes the orchestra and returns **the score as JSON plus URLs** to the MIDI, the per-instrument stems and the rendered audio.

- Base URL (dev): `http://localhost:8000`
- Interactive docs (try every call in the browser): **`http://localhost:8000/docs`**
- CORS is open by default (`JUKEBOX_CORS=*`), so the frontend can call it from any dev port.

## Run it
```bash
pip install -r requirements.txt && pip install --no-deps tinysoundfont
python music/render_audio.py --download   # fetch the SoundFont once (~32 MB); otherwise the first audio request waits for it
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
| GET | `/api/instruments` | **which instrument plays which dataset**: one row per dataset with `dataset_id` (what `/api/sonify` accepts), `label`, `instrument`, `role`, `units`, `always_playing`, plus `by_dataset`, a plain `{dataset_id: instrument}` map. `?palette=auto&zone=japan` gives the regional instruments |
| GET | `/api/datasets` | available datasets: id, variables (units, range, instrument), number of frames, time range |
| **POST** | **`/api/sonify`** | **the frontend's own format**: selected points + one time period in, 30 s of audio inside the JSON out (see the next section) |
| **POST** | **`/api/compose`** | **compose a piece**, with every option and the full score (see below) |
| GET | `/api/compositions/{id}` | fetch an earlier composition again (`?include_notes=false` for a light payload) |
| GET | `/api/files/{id}/{file}` | download a generated file (MIDI, MP3/WAV, JSON, PNG); the compose response gives you the full URLs |

## GET `/api/instruments` (dataset ↔ instrument)
```json
{
  "palette": "orchestra",
  "by_dataset": {
    "temperature": "Piano (chords)", "co2": "Contrabass", "precipitation": "Violins (melody)",
    "dust": "Flute (melody)", "smoke": "French Horns (melody)", "lightning": "Xylophone"
  },
  "instruments": [
    {"dataset_id": "temperature", "variable": "surface_temperature", "label": "Temperature",
     "instrument": "Piano (chords)", "role": "harmony", "always_playing": true,
     "units": "degC", "available": true, "real_data": false,
     "also_accepted": ["surface_temperature", "temp"], "midi_program": 0, "pitch_range": [48, 72]}
  ]
}
```
`always_playing: false` means the instrument is heard only while its phenomenon is present in the area (rain, dust, smoke, lightning). `available` says whether the server's default dataset has that variable, and `real_data` whether it is observed rather than modelled. The table is read from the orchestration in `music/orchestra.py`, so it follows any change made there.

## POST `/api/sonify` (points + one date → a chunk of audio)
The frontend sends the selected points and one time period. The backend answers with 30 seconds of music starting at that period, and with the period to ask for next.
```json
{
  "zone_id": "france",
  "zone_name": "France",
  "dataset_id": "temperature",
  "points": [
    {"lat": 48.8566, "lng": 2.3522, "x": 512, "y": 234},
    {"lat": 48.1173, "lng": -1.6778, "x": 508, "y": 236}
  ],
  "time_period": "2024-03",
  "aggregation": "mean"
}
```
| field | notes |
|---|---|
| `points` | the selected points. **`lat` and `lng` are what the backend uses** (`lon` also works); `x`, `y` are accepted and ignored, so the frontend's array can have any resolution. Every data cell that contains at least one point is averaged |
| `zone_id`, `zone_name` | the frontend's own labels, echoed back. Without `points`, `zone_id` must be a preset zone of `/api/options` |
| `dataset_id` | which variable to hear: `temperature`, `precipitation` (`rain`), `co2`, `dust`, `smoke`, `lightning`, or the names of `/api/datasets`. A list plays several together. `all`, or leaving it out, plays the whole orchestra |
| `time_period` | where to start: `"2024-03"`, `"2024-3"` or `"2024"`. Left out = the first date of the data |
| `aggregation` | only `"mean"` (area-weighted); anything else is a `422` |
| `duration_s` | optional, default `30` |
| `audio_format` | optional, `mp3` (default) or `wav` |
| `embed_audio` | optional; `false` leaves `audio_data` empty, for use with `audio_url` (smaller answer) |
| `options` | optional object with any field of `/api/compose` below, e.g. `{"style": "musical", "bpm": 96}` |

```json
{
  "zone_id": "france", "zone_name": "France", "dataset_id": "temperature",
  "time_period": "2024-03",
  "next_time_period": "2025-01",
  "audio_data": "<base64 of the MP3 or WAV file>",
  "audio_format": "mp3",
  "audio_url": "http://localhost:8000/api/files/3f0c…/score.mp3",
  "value": 9.42, "units": "degC",
  "values": {"surface_temperature": 9.42},
  "point_count": 2, "cell_count": 2,
  "aggregation": "mean",
  "time_end": "2024-12", "duration_s": 30.0,
  "composition_id": "3f0c…", "cached": false
}
```
- **`time_period`** is the date the music really starts at. It differs from the request when the requested date is before the data begins.
- **`next_time_period`** is the period right after this chunk: send it as the next `time_period` to continue. It is `null` at the end of the data.
- **`audio_data`** in the browser: `new Audio("data:audio/mpeg;base64," + audio_data)` (`audio/wav` for WAV), or simply `new Audio(audio_url)`.
- **`value`** is the mean of the first variable over the selected cells at `time_period`, in `units`; `values` has it for every variable played.
- **`point_count`** is the number of points received; `cell_count` is the number of data cells they cover.
- For the notes, bars and per-beat values of the same piece: `GET /api/compositions/{composition_id}`.

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
| `start`, `end` | date as text (`"2024-06"`, `"2024-6"`, `"2024"`) or frame index (a number) | time window, both ends included; one frame = one bar. A year alone must be text: the number `2024` means frame 2024 |
| `year`, `month` | numbers (`2024`, `3`) | the start date as numbers instead of `start`. `month` is optional (1–12) |
| `duration_s` | seconds (e.g. `30`) | **one date + a length**: play this many seconds of data from the start date, instead of sending `end`. As many bars as fit at about `bpm` are taken, then the tempo is nudged so they last exactly `duration_s` (30 s at the default tempo = 10 bars at 80 bpm) |
| `variables` | list | subset of instruments, e.g. `["surface_temperature","co2"]` |
| `palette` | string | `orchestra` (default), `auto` (regional instruments for the zone, fun mode), or a palette id |
| `key`, `scale`, `bpm` | | musical options; `scale` default = major (or the palette's mode) |
| `frames_per_bar` | number | data frames per bar. Default = the dataset's hint: **daily data → 7 (one bar per week)**, monthly → 1. Each beat averages its frames |
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
  "id": "8f104320be01425f",
  "cached": false,
  "elapsed_s": 3.996,
  "request": {
    "dataset": "sim_both_24f_1deg",
    "zone": "switzerland",
    "bbox": null,
    "n_cells": null,
    "start": null,
    "duration_s": null,
    "palette": "orchestra",
    "key": "C",
    "scale": null,
    "bpm": 76,
    "style": "data",
    "audio": true,
    "audio_format": "mp3"
  },
  "files": {
    "midi": "http://localhost:8000/api/files/8f104320be01425f/score.mid",
    "score_json": "…",
    "stems": {
      "aerosol_dust": "http://localhost:8000/api/files/8f104320be01425f/score_stems/aerosol_dust.mid",
      "…": "…"
    },
    "audio": "http://localhost:8000/api/files/8f104320be01425f/score.mp3",
    "score_image": null
  },
  "score": {
    "style": "data",
    "quantize": "scale",
    "zone": "switzerland",
    "bbox": [
      45.8,
      47.8,
      5.9,
      10.5
    ],
    "cells": null,
    "surface": "all",
    "palette": "orchestra",
    "key": "C",
    "scale": "major",
    "bpm": 76,
    "beats_per_bar": 4,
    "frames_per_bar": 1.0,
    "n_bars": 24,
    "intro_bars": 0,
    "total_beats": 96,
    "duration_s": 75.79,
    "tempo_map": [
      {
        "beat": 0.0,
        "bpm": 76
      }
    ],
    "time_start": "2024-01-15T00:00:00Z",
    "time_end": "2025-12-15T01:30:00Z",
    "time_last_frame": "2025-12-15T01:30:00Z",
    "harmony_source": "co2",
    "sections": [
      {
        "name": "data",
        "start_bar": 0,
        "bars": 24,
        "start_s": 0.0
      }
    ],
    "bars": [
      {
        "index": 0,
        "start_beat": 0,
        "start_s": 0.0,
        "time": "2024-01-15T00:00:00Z",
        "chord": null,
        "tension": 0.249,
        "mode": null,
        "climax": false
      },
      {
        "index": 1,
        "start_beat": 4,
        "start_s": 3.158,
        "time": "2024-02-14T10:30:00Z",
        "chord": null,
        "tension": 0.224,
        "mode": null,
        "climax": false
      },
      "…"
    ],
    "instruments": {
      "surface_temperature": {
        "name": "Temperature - Piano (chords)",
        "role": "harmony",
        "program": 0,
        "channel": 0,
        "pan": 64,
        "register": [
          48,
          72
        ],
        "units": "degC",
        "source": "SIMULATED (simulate_variables.py)",
        "values_per_beat": [
          -0.0226,
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
        "pitch_range": [
          48,
          79
        ],
        "anomaly_z_per_bar": [
          -1.0,
          -0.8,
          -0.45,
          -0.27,
          "…"
        ],
        "levels_per_bar": [
          2,
          2,
          2,
          2,
          "…"
        ],
        "bars_playing": 24,
        "notes": [
          {
            "pitch": 48,
            "start": 0.0,
            "duration": 0.9,
            "velocity": 50,
            "time_s": 0.0,
            "duration_s": 0.7105
          },
          {
            "pitch": 48,
            "start": 1.0,
            "duration": 0.9,
            "velocity": 41,
            "time_s": 0.7895,
            "duration_s": 0.7105
          },
          "…"
        ]
      },
      "…": "…"
    },
    "stats": {
      "notes_total": 547,
      "polyphony_mean": 4.4,
      "polyphony_max": 7,
      "tense_bars": 2,
      "max_tension": 0.675
    }
  }
}

```

### New in v1.6 (the frontend's format, one date + a length)
- new `POST /api/sonify` (points + one `time_period` → 30 s of audio inside the JSON, with `next_time_period`) and `GET /api/instruments` (dataset ↔ instrument); see their sections above.
- `/api/compose`: `{"cells": [...], "year": 2024, "month": 3, "duration_s": 30}` plays 30 seconds starting in March 2024. Read `score.time_start`, `score.time_end`, `score.n_bars`, `score.bpm` and `score.duration_s` to know what was actually played.
- `duration_s` counts bars, so it follows `frames_per_bar`: 30 s is 10 bars, which is 10 months of monthly data or 10 weeks of daily data. With daily data `/api/sonify` answers with days (`"time_period": "2024-01-01"`, `"next_time_period": "2024-03-11"`).
- `score.time_last_frame`: the date of the last frame played. `score.time_end` is the start of the last bar, which is up to 6 days earlier with weekly bars.
- `duration_s` is exact in the default `data` style. The rendered audio file is about 1.7 s longer, because the hall reverb rings out after the last note. In `musical` style the intro and the outro come on top (send `"intro": false` to drop the intro).
- a start date before the data begins at the first frame; a start date after the data is a `400`. Close to the end of the data, where fewer frames are left, the piece is shorter (same tempo).
- with both `end` and `duration_s`, the piece stops at whichever comes first.

### New in v1.5 (real daily data, snowfall)
- **Default dataset = the one with the most real variables**: `real_daily_2024` (the extraction team's real daily precipitation and snowfall + real temperature and CO₂). `GET /api/datasets` lists it with 366 frames, and `start`/`end` accept days (`"2024-10-01"`).
- new instrument `snowfall` → **glockenspiel** (melody while it snows).
- `frames_per_bar` request field; `score.frames_per_bar` in the response (bars = weeks for daily data; `bars[i].time` is the first day of the week).

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
     -d '{"cells":[[170,744],[170,745],[171,744]],"cells_grid":[720,1440],"year":2024,"month":3,"duration_s":30}'
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
