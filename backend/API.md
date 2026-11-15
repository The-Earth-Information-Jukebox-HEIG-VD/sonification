# Sonification API: the engine, for development

> **Frontend developers: read [`FRONTEND_API.md`](FRONTEND_API.md).** It has the calls you need. This file documents the engine's own endpoints, which the deployed backend does not serve (`JUKEBOX_FULL_API=0` in the Docker image).

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
| `JUKEBOX_FULL_API` | `1` | `0`: only `/api/instruments`, `/api/sonify` and `/api/health` are served (set in the Docker image) |
| `JUKEBOX_CHUNK_SECONDS` | `30` | length of one piece returned by `/api/sonify` |
| `JUKEBOX_MERGE_DATASETS` | `0` | `1`: a request that names no dataset takes each variable from its own best dataset, lined up by date (set in the Docker image) |
| `JUKEBOX_BUILT_DIR` | `<cache>/_datasets` | datasets converted from the team's raw folders by `backend/prepare_data.py`; scanned in addition to `JUKEBOX_DATA_DIR`. In the Docker image this happens by itself at start-up (see the README) |
| `JUKEBOX_CORS` | `*` | allowed origins, comma-separated |

## Endpoints
| method | path | what |
|---|---|---|
| GET | `/api/health` | status, number of datasets, whether audio/MP3 rendering is available, and `preparing_data` (true while the server is still converting raw data at start-up: datasets can still be missing) |
| GET | `/api/options` | everything the UI needs for its menus: zones (with bbox), palettes, keys, scales, the orchestra (which instrument plays which variable), default request |
| GET | `/api/instruments` | **frontend call**: the datasets and the instrument that plays each (`FRONTEND_API.md`) |
| GET | `/api/datasets` | available datasets: id, variables (units, range, instrument), number of frames, time range |
| **POST** | **`/api/zones/zone-data`** | **frontend call**: the same as `/api/sonify` with the webapp's field names (`dataset_ids`, `values`); see `FRONTEND_API.md` |
| **POST** | **`/api/zones/series`** | **frontend call**: the monthly series of the selected points for each dataset (chart data, no audio); see `FRONTEND_API.md` section 3 |
| **POST** | **`/api/sonify`** | **frontend call**: selected points + one time period in, 30 s of MP3 inside the JSON out (`FRONTEND_API.md`) |
| POST | `/api/sonify/debug` | development only: the same with the engine's knobs and a detailed answer |
| **POST** | **`/api/compose`** | **compose a piece**, with every option and the full score (see below) |
| GET | `/api/compositions/{id}` | fetch an earlier composition again (`?include_notes=false` for a light payload) |
| GET | `/api/files/{id}/{file}` | download a generated file (MIDI, MP3/WAV, JSON, PNG); the compose response gives you the full URLs |

## The frontend's calls
`GET /api/instruments`, `POST /api/sonify` / `POST /api/zones/zone-data`, `POST /api/zones/series` (chart data) and `POST /api/zones/groove` (vinyl mode) are described in **[`FRONTEND_API.md`](FRONTEND_API.md)**, one page, no options. They are all the deployed backend serves.

For development, `POST /api/sonify/debug` takes the same request plus the engine's knobs (`duration_s`, `audio_format`, `embed_audio`, and `options` = any field of `/api/compose` below) and answers with every detail: `values` and `units` of each instrument, `time_end`, `duration_s`, `audio_url`, `composition_id`, `source_dataset(s)`, `real_data`, `anomaly`, `cell_count`.

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
| `dataset` | string | id from `/api/datasets`. Left out = chosen by the server: a dataset that has the requested `variables`, covers `start` if possible, and has the most real variables |
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

### New in v2.5 (speed)
- `mask` on `POST /api/zones/zone-data`, `/api/sonify` and `/api/zones/series`: the selected area as a zlib-compressed bitmap of the 0.1° point grid, instead of `points` (an ocean: ~3 kB instead of ~3 MB; `FRONTEND_API.md`, "mask"). Same cells, same music.
- The mean of an area is kept in memory per (map file, area) (`orchestra.area_mean`, 512 entries): the next pieces of the same area no longer re-read the map's whole history. It is computed on the selected cells only (`zone_to_midi.zone_series`).
- `build_monthly_dataset.py` writes uncompressed `.npz` (reading is ~7x faster, the files ~2x larger); `prepare_data.py` rebuilds the datasets once after the update.
- Listing the datasets reads the array shapes from the file headers instead of loading every array (~360 MB less memory).
- The reverb's hall response and its spectrum are computed once per process.

### New in v2.4 (chart data)
- `POST /api/zones/series`: points + `dataset_ids` in, one value per month for each dataset out (`start`, `values` with `null` gaps, `units`, `anomaly`, `yearly`). The same area mean as the music (`orchestra.area_series`); cached in memory (256 areas x datasets). Served by the deployed backend too (`FRONTEND_API.md`, section 3).

### New in v2.3 (instruments after a listening test)
- lightning: **timpani** (was a xylophone, too close to the snow's glockenspiel).
- fires: **trombones that swell and hold**, below the piano (was a melody in the piano's register).
- night lights: a **synth pad** that starts clearly each time the yearly value changes, then fades slowly over the whole year to a softer background (was a harp plucked on every beat).
- CO₂: the contrabass plays **an octave higher**, is re-bowed on every bar line and is louder, so laptop and phone speakers play it.
- `/api/instruments` reports the new names.

### New in v2.2 (fires)
- new variable from the team's data: **fires** (`fires`, trombones): the average number of days per month with at least one fire detected, July 2002 to November 2026. `wildfires` is accepted as another name.

### New in v2.1 (rain, snow, yearly night lights; the webapp's own path)
- new variables from the team's data: **snow** (`snow`, glockenspiel; mm/day of melted water) and **night lights** (`night_lights`, harp; brightness 0–1, **yearly**: one value per year, held for its 12 months). `precipitation` is now the rain alone.
- `POST /api/zones/zone-data`: the same call as `/api/sonify` under the path and field names of the webapp's code (`dataset_ids` in; `dataset_ids` and `values` out). Datasets without data on the server are left out, not an error.
- `GET /api/instruments` also has what the webapp's `InstrumentInfo` declares (`midi_program`, `pitch_range`, `role`, `always_playing`) and `palette`, `by_dataset`.
- the ids of `webapp/src/data/tracks.ts` are accepted: `night_lights`, and `co2_atmosphere` as another name for `co2`.
- `/api/compose`: `pitch_range` = `window` (default: lowest/highest note = lowest/highest value of the piece) or `series` (of the whole series of the area). `/api/sonify` uses `series`, so the same value is the same note in every chunk.

### New in v2.0 (the frontend API is two calls)
- `POST /api/sonify` takes exactly the frontend's request (`zone_id`, `zone_name`, `dataset_id`, `points`, `time_period`, `aggregation`) and answers with exactly `zone_id`, `dataset_id`, `time_period`, `next_time_period`, `audio_data`, `value`, `point_count`. Its former options moved to `POST /api/sonify/debug`.
- `GET /api/instruments` is a plain list: `dataset_id`, `name`, `instrument`, `units`, `first_time_period`, `last_time_period`, for the datasets the server has. No parameters.
- `JUKEBOX_FULL_API=0` (the Docker image): nothing else is served. Outside Docker the engine endpoints below stay available.

### New in v1.9 (lightning, and what the Docker image serves)
- new variable from the team's data: **lightning** (`dataset_id: "lightning"`, xylophone; timpani since v2.3). Its value is the share of the selected area with lightning in the month, from 0 to 1, not a number of flashes. It exists from March 2017 to July 2023 and only between 55°S and 55°N; an area entirely outside that band has no data (`400`).
- **CO₂** (`dataset_id: "co2"`, contrabass) from the team's files, September 2002 to March 2022. The converter removes the outline of the source picture that is still in those files (about a fifth of the pixels); a very small area can therefore have little or no CO₂ data in some months.
- the Docker image serves one dataset per variable (`real_temperature_*`, `real_precipitation_*`, `real_lightning_*`, `real_co2_*`), all at one bar per month, and **merges them per request**: each requested variable is read from its own dataset and they are lined up by date. Any combination can be asked for any start date. An instrument is silent for the months its data does not cover (its entry in `values` is `null` there), and a variable with no data at all in the period is left out of the piece.
- `/api/sonify` answers with `source_datasets` (`{variable: dataset}`) when several datasets were merged; `source_dataset` is then the main variable's. `value` and `units` are those of the first requested variable that is in the piece.
- with no `dataset_id`, the piece has every variable the server has data for in that period.
- the merge is switched on with `JUKEBOX_MERGE_DATASETS=1` (the image sets it; `GET /api/health` reports `merge_datasets`). Without it, or when a `dataset` is named, a request is served from one dataset as before. In `/api/compose`, `request.datasets` gives the `{variable: dataset}` that was used.

### New in v1.8 (temperature anomaly 1880 → 2025)
- new dataset `real_monthly_1880_2025`: the team's monthly temperature anomaly, January 1880 to December 2025, 2° (`python simulation/build_monthly_dataset.py --temperature DIR --res 2 --frames-per-bar 12`). One bar = one year by default, so a 30 s chunk of `/api/sonify` is 10 years.
- its values are **anomalies** (degC warmer or colder than normal), not temperatures. They are played by the same piano and requested with the same `dataset_id: "temperature"`. The server picks this dataset when the date is before the other datasets start (e.g. `"time_period": "1950"`); to force it, send `options.dataset`.
- new dataset `team_monthly_2002_2022`: only the team's own extractions (precipitation, temperature anomaly, CO₂), September 2002 to March 2022, 1°, one bar = 3 months by default.
- no `anomaly_markers` track for this data: the piano already plays the anomaly.
- new boolean `anomaly`: in `GET /api/datasets` (`variables[v].anomaly`), in `/api/compose` (`score.instruments[v].anomaly`) and in `/api/sonify` (for the main variable). When it is `true`, show the value as "+1.2 °C vs normal", not "1.2 °C".

### New in v1.7 (the team's real monthly data, dataset chosen per request)
- new dataset `real_monthly_2000_2026`: the extraction team's real monthly precipitation, June 2000 to November 2026 (`python simulation/build_monthly_dataset.py --precip DIR`).
- **the server now chooses the dataset for each request** that does not name one. It takes the datasets that have every requested variable, then prefers one whose dates cover the start date, then the one with the most real variables. So `precipitation` in `2003-05` is served from `real_monthly_2000_2026`, and `temperature` from a dataset that has it. To force one, send `dataset` (`/api/compose`) or `options.dataset` (`/api/sonify`). A request with no variable and no date still gets the default dataset of `GET /api/datasets`.
- `/api/sonify` answers with `source_dataset` (the dataset that was played) and `real_data` (whether the main variable is observed rather than modelled).
- `/api/instruments`: `available`, `units` and `real_data` now describe the dataset a request for that variable would get, with its `time_start` and `time_end`; `found_in` lists every dataset that has the variable.

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
