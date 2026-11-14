# Sonification API: contract for the frontend

Backend for the Earth Information Jukebox. The frontend sends **what to listen to** (dataset, zone, time window, a few musical options). The backend composes the orchestra and returns **the score as JSON plus URLs** to the MIDI, the per-instrument stems and the rendered audio.

- Base URL (dev): `http://localhost:8000`
- Interactive docs (try every call in the browser): **`http://localhost:8000/docs`**
- CORS is open by default (`JUKEBOX_CORS=*`), so the frontend can call it from any dev port.

## Run it
```bash
pip install -r requirements.txt && pip install --no-deps tinysoundfont
python simulation/simulate_variables.py --scenario both --warming 0.5 --years 2 --frames 24   # demo data
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
| `bbox` | `[lat_min, lat_max, lon_min, lon_max]` | **custom area drawn on the map**; overrides `zone`. `lon_min > lon_max` = crosses the dateline |
| `surface` | `all` / `land` / `ocean` | which cells of the zone to average |
| `start`, `end` | ISO date prefix (`"2024-06"`) or frame index | time window; one frame = one bar |
| `variables` | list | subset of instruments, e.g. `["surface_temperature","co2"]` |
| `palette` | string | `orchestra` (default), `auto` (regional instruments for the zone, fun mode), or a palette id |
| `key`, `scale`, `bpm` | | musical options; `scale` default = major (or the palette's mode) |
| `audio`, `audio_format` | bool, `mp3`/`wav` | render audio with sampled instruments (≈ +2 s). `false` = MIDI + JSON only (≈ 0.5 s) |
| `include_notes` | bool | `false` drops the note lists from the response (≈ half the size) |
| `score_image` | bool | also make a PNG of the score |

**Timing:** first request ≈ 2–3 s with MP3, ≈ 0.5 s without audio. **The same request again is served from cache in about 10 ms** (the `id` is a hash of the request and the data).

**Errors:** `422` invalid input (unknown zone, bad bbox…), `404` unknown dataset, `400` nothing to play (e.g. a time window with no data, unknown variable). The body always has a `detail` message you can show.

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
        "name": "Temperature - Violins",
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

### How to use it in the UI
- **Just play it:** `<audio src={files.audio}>`. The MP3 is the whole orchestra with real sampled instruments.
- **Sync visuals with the music:** a note plays at `start * 60 / bpm` seconds (`start` and `duration` are in beats). Bar *i* starts at `bars[i].start_beat * 60 / bpm` seconds and corresponds to the data date `bars[i].time`. Use that to move a cursor on the map or timeline, or highlight the current chord.
- **Show what each instrument means:** `instruments[var].name` (e.g. "Temperature - Violins"), `units`, `values_per_beat` (the real data value, 4 per bar) and `presence_per_beat` (0–1, how strongly that phenomenon is present). Good for live gauges, accessible text ("Temperature 18.4 °C, rising"), or mute/solo buttons.
- **Mute/solo or a custom web synth:** use `files.stems` (one MIDI per instrument) or the `notes` arrays directly, e.g. with Tone.js, smplr or SpessaSynth.
- **Data timeline:** `time_start`, `time_end`, `duration_s`, and `n_bars` (= number of data frames).

### Examples
```bash
curl -X POST localhost:8000/api/compose -H 'Content-Type: application/json' \
     -d '{"zone":"india"}'
curl -X POST localhost:8000/api/compose -H 'Content-Type: application/json' \
     -d '{"bbox":[45.8,47.8,5.9,10.5],"start":"2024-06","end":"2024-12","audio":false}'
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
