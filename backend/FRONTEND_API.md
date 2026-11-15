# The API the frontend uses

Four calls. No options. (API v2.6: section 4, vinyl mode, is new)

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
| `mask` | the same area as a compressed bitmap, **instead of** `points` (v2.5; recommended for big zones, see below) |
| `time_period` | where the music starts, `"YYYY-MM"` |
| `aggregation` | `"mean"` (the only one; `sum`, `max`, `min` are refused with a `422`) |
| `audio_data` | 30 seconds of music as a base64 MP3. `decodeAudioData` in `zoneApi.ts` plays it as is |
| `next_time_period` | send it as the next `time_period` to continue. `null` when the data ends |
| `value` / `values` | the mean of each dataset over the points at `time_period`, in the units of `/api/instruments` |
| `point_count` | the number of points received (with `mask`: the number of 0.1° cells it selects) |

### What to expect
- **30 seconds is 10 months**: one bar of music per month. The next period after `2015-01` is `2015-11`, not `2015-02`: use `next_time_period`, do not add one month yourself.
- **`time_period` in the answer is where the music really starts.** It differs from the request only when you ask for a date before the first data.
- **Several datasets**: each instrument plays where its own data exists and is silent elsewhere (temperature and precipitation from 1999: the violins come in in June 2000).
- **Yearly data** (`night_lights`): the value is the year's. The synth pad starts clearly each January, when the value changes, then fades slowly over the year to a softer background note.
- **The same value is the same note** in every piece of the same area, so consecutive pieces connect.
- **Errors**: `400` unknown dataset or no data for these points and this period; `422` malformed request (no points, latitude out of range, another `aggregation`).
- A new area takes about a second on the server (its whole series is read once); the following pieces of the same area are faster (the series is kept in memory), and the same request again is immediate.

### `mask`: big zones in a few kB (v2.5)
As points, an ocean is ~57 000 entries, about 3 MB of JSON sent again with every piece (the whole globe: 14 MB). `mask` describes the same cells in 2 to 4 kB:

1. a grid of 1800 × 3600 booleans (0.1°), row 0 = 90°N, column 0 = 180°W, the same grid the points are placed on: point → `row = Math.min(Math.floor((90 - lat) * 1800 / 180), 1799)`, `col = Math.min(Math.floor(((lng + 180) % 360) * 3600 / 360), 3599)` (exactly these operations, in this order, so the floating-point rounding is the server's);
2. packed 8 cells per byte, row-major, first cell = highest bit (numpy `packbits`): 810 000 bytes;
3. compressed with zlib (the browser's `new CompressionStream("deflate")`), then base64.

The server finds exactly the cells the points would give, so the music and the values are identical. Send `mask` or `points` (if both are sent, `mask` is used). A mask that does not inflate to exactly 810 000 bytes, or selects no cell, is a `422`.

`GET /api/health` answers `{"status": "ok", ...}` when the server is up; `"preparing_data": true` means it is still loading its datasets after a start (a few minutes, once).

## 3. `POST /api/zones/series`: the whole monthly series of the points, for a chart (API v2.4)

No audio: one value per month for each requested dataset, the same area-weighted mean (cos latitude, cells of 0.1°) as the `values` of `POST /api/zones/zone-data`. The value zone-data plays for a month is the value of the chart for that month.

```json
{
  "zone_id": "france", "zone_name": "France",
  "dataset_ids": ["temperature", "night_lights"],
  "points": [{"lat": 48.8566, "lng": 2.3522}, {"lat": 43.2965, "lng": 5.3698}],
  "aggregation": "mean"
}
```
`dataset_ids`: at least one id of `GET /api/instruments`. `points`: 1 to 20 000 `{lat, lng}` (`x`, `y` accepted, not used), or instead `mask` (v2.5, no size limit, the same format as for zone-data). `zone_id`, `zone_name` optional. `aggregation`: only `"mean"`.

```json
{
  "zone_id": "france",
  "step": "month",
  "point_count": 2,
  "series": {
    "temperature":  {"units": "degC anomaly", "anomaly": true, "start": "1880-01", "yearly": false,
                     "values": [-0.4215, -0.1022, null, ...]},
    "night_lights": {"units": "brightness (0-1)", "anomaly": false, "start": "2012-01", "yearly": true,
                     "values": [0.1532, 0.1532, ...]}
  },
  "missing": []
}
```
- `values[i]` is the month `start + i`; the axis has no gap (one value per month up to the dataset's last month), a month without data is `null`. Values are rounded to 4 decimals.
- `start` is **per dataset**: the first month of that dataset's own data (temperature 1880-01, precipitation 2000-06, night lights 2012-01 ...), the same as its `first_time_period` in `GET /api/instruments`.
- `units` is the same text as `units` in `GET /api/instruments`; `anomaly: true` = degrees away from a normal.
- `yearly: true` (`night_lights`): one map per year, so the 12 months of a year have the same value. Draw it as steps.
- Daily data is averaged per month.
- `missing`: the requested ids the server has no data for. They are left out of `series`, not an error, as long as one id is known.
- **Errors**: `400` when none of the ids is known (same message as zone-data); `422` malformed request (no points and no mask, more than 20 000 points, an invalid `mask`, no `dataset_ids`, another `aggregation`).
- The first request for an area takes up to about a second (the 1880 → today temperature); the same area again is immediate (cached).

## 4. `POST /api/zones/groove`: vinyl mode, the needle moves through space (API v2.6)

The date stays fixed; what moves is the place. The frontend sends the stretch of path the needle crosses next, the backend answers with its music. Bars are 3 s, as in the normal mode; with `points_per_bar: 4` every point is one beat (0.75 s), so **a 30 s chunk = 40 points**.

```json
{
  "zone_id": "record-1",
  "dataset_ids": ["temperature", "precipitation", "lightning", "fires"],
  "time_period": "2018-07",
  "points_per_bar": 4,
  "radius_deg": 2,
  "path": [{"lat": -0.5, "lng": 0.0}, {"lat": -0.5, "lng": 9.0}, {"lat": -0.5, "lng": 18.0}, "... 40 points ..."]
}
```
```js
// 30 s of an outer groove: a circle of latitude, 9 degrees per point (longitudes past 180 are wrapped)
const path = Array.from({ length: 40 }, (_, i) => ({ lat: -0.5, lng: i * 9 }));
```
- `time_period`: the fixed date, a month `"2018-07"` or a whole year `"2018"` (the mean of its months: calmer, and covers more datasets).
- `points_per_bar`: 1, 2 or 4 (default 1). How many points of `path` share one 3 s bar: 1 = one point per bar (3 s), 4 = one point per beat (0.75 s).
- `path`: the points the needle crosses, in order. Whole bars: a multiple of `points_per_bar`, at most 20 bars (60 s) and 80 points. 30 s = 10 bars = 40 points at 4 per bar. Points closer than about 2° add nothing: the maps are 1° (temperature 2°).
- `radius_deg` (default 1.5, from 0.1 to 10): how wide the needle reads. Each point is the mean over a disc of that radius. Wide = smooth music, narrow = detailed and jumpy. With points 5–10° apart, 2–3° reads more of the ground in between.
- `dataset_ids`: as for zone-data; omitted = every dataset. `zone_id` optional, echoed back.

```json
{
  "zone_id": "record-1",
  "time_period": "2018-07",
  "dataset_ids": ["temperature", "precipitation", "lightning", "fires"],
  "audio_data": "<base64 mp3>",
  "duration_s": 30.0,
  "bar_seconds": 3.0,
  "points_per_bar": 4,
  "stop_seconds": 0.75,
  "radius_deg": 2.0,
  "stops": [
    {"lat": -0.5, "lng": 0.0, "values": {"temperature": 1.23, "precipitation": 0.03, "lightning": 0.0, "fires": 0.0}},
    {"lat": -0.5, "lng": 9.0, "values": {"temperature": 1.32, "precipitation": 0.28, "lightning": 0.0, "fires": 0.78}}
  ]
}
```
- `stops[i]` plays from `i × stop_seconds` to `(i + 1) × stop_seconds`: move the needle and show its values on that clock. `null` = no data under the needle there (lightning beyond 55°); that instrument is silent there. `0` = data that says "none" (no fire over the ocean): silent too, but known.
- Places are **snapped to a 0.5° grid** (`stops` gives the snapped positions). Two listeners crossing the same places get the same piece from the cache, in about 0.1 s.
- **One scale for the whole planet**: a value is the same note wherever the needle is (the dataset's global range), so hot and cold places really sound different. In zone-data, the scale is the zone's own history.
- Every piece has the same tempo and key: consecutive stretches of a groove join without a jump. Send the next stretch **about 5 s before** the current piece ends: a new stretch takes a few seconds to compose the first time.
- `dataset_ids` in the answer: the datasets that have data along the path at that date. A dataset whose dates do not cover `time_period` (lightning before 2017) is left out.
- **Speed** is the frontend's choice: at 9° per point (4 per bar) the needle circles the equator in 30 s. A slower needle = points closer together, not more points per chunk.
- **Errors**: `400` unknown `dataset_ids`, or no data at that date along the whole path; `422` malformed request (`time_period` not `YYYY-MM` / `YYYY`, no `path`, a `path` that is not whole bars, more than 20 bars or 80 points, `points_per_bar` other than 1, 2, 4, `radius_deg` out of range).
- The first request at a new date reads each map once (a few seconds); the next requests at that date only read the needle.

Nothing else is served by the deployed backend. The engine's own endpoints and options (`backend/API.md`) exist for development only (`JUKEBOX_FULL_API=1`).
