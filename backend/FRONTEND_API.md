# The API the frontend uses

Two calls. No options.

- Base URL: the backend, e.g. `http://localhost:8000` (behind the webapp's nginx: the same origin, `/api/...`).
- Everything is JSON. Errors have a `detail` message.

## 1. `GET /api/instruments`: which instrument plays which dataset

```json
{
  "instruments": [
    {"dataset_id": "temperature",   "name": "Temperature", "instrument": "Piano (chords)",   "units": "degC anomaly",
     "first_time_period": "1880-01", "last_time_period": "2025-12"},
    {"dataset_id": "co2",           "name": "CO2",         "instrument": "Contrabass",       "units": "ppm",
     "first_time_period": "2002-09", "last_time_period": "2022-03"},
    {"dataset_id": "precipitation", "name": "Rain",        "instrument": "Violins (melody)", "units": "mm/day",
     "first_time_period": "2000-06", "last_time_period": "2026-11"},
    {"dataset_id": "lightning",     "name": "Lightning",   "instrument": "Xylophone",        "units": "share of area with lightning (0-1)",
     "first_time_period": "2017-03", "last_time_period": "2023-07"}
  ]
}
```

Only the datasets the server has data for are listed. `dataset_id` is what you send to `/api/sonify`.

## 2. `POST /api/sonify`: points + a time period in, music out

Request:
```json
{
  "zone_id": "france",
  "zone_name": "France",
  "dataset_id": "temperature",
  "points": [
    {"lat": 48.8566, "lng": 2.3522, "x": 512, "y": 234},
    {"lat": 48.1173, "lng": -1.6778, "x": 508, "y": 236},
    {"lat": 43.2965, "lng": 5.3698, "x": 520, "y": 248}
  ],
  "time_period": "1880-01",
  "aggregation": "mean"
}
```

Response:
```json
{
  "zone_id": "france",
  "dataset_id": "temperature",
  "time_period": "1880-01",
  "next_time_period": "1880-11",
  "audio_data": "<base64 of an MP3 file>",
  "value": -0.23,
  "point_count": 3
}
```

| field | meaning |
|---|---|
| `zone_id`, `zone_name` | your own labels; `zone_id` is sent back as is |
| `dataset_id` | one id of `/api/instruments`. A list (`["temperature", "precipitation"]`) plays several instruments together |
| `points` | the selected points. `lat` and `lng` are used; `x`, `y` are accepted and ignored |
| `time_period` | where the music starts, `"YYYY-MM"` |
| `aggregation` | `"mean"` (the only one) |
| `audio_data` | 30 seconds of music as a base64 MP3. Play it with `new Audio("data:audio/mpeg;base64," + audio_data)` |
| `next_time_period` | send it as the next `time_period` to continue. `null` when the data ends |
| `value` | the mean of the dataset over the points at `time_period`, in the units of `/api/instruments`. `null` when the dataset has no data yet at that date |
| `point_count` | the number of points received |

What to expect:
- **30 seconds is 10 months**: one bar of music per month. So the next period after `1880-01` is `1880-11`.
- **`time_period` in the answer is where the music really starts.** It differs from the request only when you ask for a date before the first data.
- **A list of datasets**: each instrument plays where its own data exists and is silent elsewhere (ask for temperature and precipitation in 1999, the violins come in in June 2000). `value` is the first dataset's.
- **Errors**: `400` unknown `dataset_id` or no data for these points and this period; `422` malformed request (no points, latitude out of range, another `aggregation`).
- A first request takes 2 to 4 seconds; the same request again is immediate.

`GET /api/health` answers `{"status": "ok", ...}` when the server is up; `"preparing_data": true` means it is still loading its datasets after a start (a few minutes, once).

Nothing else is served by the deployed backend. The engine's own endpoints and options (`backend/API.md`) exist for development only (`JUKEBOX_FULL_API=1`).
