# The API the frontend uses

Two calls. No options.

- Base URL: the backend, e.g. `http://localhost:8000` (behind the webapp's nginx: the same origin, `/api/...`).
- Everything is JSON. Errors have a `detail` message.

## 1. `GET /api/instruments`: which instrument plays which dataset

```json
{
  "palette": "orchestra",
  "by_dataset": {"temperature": "Piano (chords)", "co2": "Contrabass", "precipitation": "Violins (melody)",
                 "snow": "Glockenspiel (melody)", "fires": "Trombones", "lightning": "Timpani",
                 "night_lights": "Synth pad"},
  "instruments": [
    {"dataset_id": "temperature", "name": "Temperature", "instrument": "Piano (chords)", "units": "degC anomaly",
     "first_time_period": "1880-01", "last_time_period": "2025-12",
     "midi_program": 0, "pitch_range": [48, 72], "role": "harmony", "always_playing": true}
  ]
}
```

One entry per dataset the server has data for:

| `dataset_id` | instrument | units | data |
|---|---|---|---|
| `temperature` | Piano (chords) | °C anomaly | 1880-01 → 2025-12 |
| `co2` | Contrabass | ppm | 2002-09 → 2022-03 |
| `precipitation` | Violins (melody) | mm/day of rain | 2000-06 → 2026-11 |
| `snow` | Glockenspiel (melody) | mm/day of melted water | 2000-06 → 2026-11 |
| `fires` | Trombones | days of the month with a fire | 2002-07 → 2026-11 |
| `lightning` | Timpani | share of the area with lightning (0–1) | 2017-03 → 2023-07 |
| `night_lights` | Synth pad | brightness (0–1) | 2012-01 → 2023-12, one value per year |

`dataset_id` is what you send below, and it is the file prefix of the dataset's folder in the data volume (`fires`, `co2`, `lightning`) wherever the two can match. Also accepted: `co2_atmosphere` for `co2`, `wildfires` for `fires`, `rain` for `precipitation`, `snowfall` for `snow`, `nightlights` for `night_lights`.

## 2. Points + a time period in, music out

Two spellings of the same call. Use either.

### `POST /api/zones/zone-data` (the one `webapp/src/services/zoneApi.ts` calls)
```json
{
  "zone_id": "france",
  "zone_name": "France",
  "dataset_ids": ["temperature", "night_lights"],
  "points": [{"lat": 48.8566, "lng": 2.3522, "x": 512, "y": 234}, {"lat": 43.2965, "lng": 5.3698, "x": 520, "y": 248}],
  "time_period": "2015-01",
  "aggregation": "mean"
}
```
```json
{
  "zone_id": "france",
  "dataset_ids": ["temperature", "night_lights"],
  "time_period": "2015-01",
  "next_time_period": "2015-11",
  "audio_data": "<base64 of an MP3 file>",
  "values": {"temperature": 1.42, "night_lights": 0.031},
  "point_count": 2
}
```
A dataset the server has no data for is left out of the piece (no error, as long as one remains): `dataset_ids` in the answer says which ones were combined. `values` has an entry for each dataset that has data at `time_period`.

### `POST /api/sonify` (one dataset, one value)
Request: the same with `"dataset_id": "temperature"` (a single id, or a list).
Response: `zone_id`, `dataset_id`, `time_period`, `next_time_period`, `audio_data`, `value`, `point_count`.

### Fields
| field | meaning |
|---|---|
| `zone_id`, `zone_name` | your own labels; `zone_id` is sent back as is |
| `points` | the selected points. `lat` and `lng` are used; `x`, `y` are accepted and ignored |
| `time_period` | where the music starts, `"YYYY-MM"` |
| `aggregation` | `"mean"` (the only one; `sum`, `max`, `min` are refused with a `422`) |
| `audio_data` | 30 seconds of music as a base64 MP3. `decodeAudioData` in `zoneApi.ts` plays it as is |
| `next_time_period` | send it as the next `time_period` to continue. `null` when the data ends |
| `value` / `values` | the mean of each dataset over the points at `time_period`, in the units of `/api/instruments` |
| `point_count` | the number of points received |

### What to expect
- **30 seconds is 10 months**: one bar of music per month. The next period after `2015-01` is `2015-11`, not `2015-02`: use `next_time_period`, do not add one month yourself.
- **`time_period` in the answer is where the music really starts.** It differs from the request only when you ask for a date before the first data.
- **Several datasets**: each instrument plays where its own data exists and is silent elsewhere (temperature and precipitation from 1999: the violins come in in June 2000).
- **Yearly data** (`night_lights`): the value is the year's. The synth pad starts clearly each January, when the value changes, then fades slowly over the year to a softer background note.
- **The same value is the same note** in every piece of the same area, so consecutive pieces connect.
- **Errors**: `400` unknown dataset or no data for these points and this period; `422` malformed request (no points, latitude out of range, another `aggregation`).
- A first request takes 2 to 5 seconds; the same request again is immediate.

`GET /api/health` answers `{"status": "ok", ...}` when the server is up; `"preparing_data": true` means it is still loading its datasets after a start (a few minutes, once).

Nothing else is served by the deployed backend. The engine's own endpoints and options (`backend/API.md`) exist for development only (`JUKEBOX_FULL_API=1`).
