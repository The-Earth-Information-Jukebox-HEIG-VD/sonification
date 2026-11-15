# The API the frontend uses

API v2.14. The frontend calls `GET /api/genres`, `GET /api/instruments`, `POST /api/zones/zone-data`, `POST /api/zones/series` and `POST /api/zones/groove` (plus `GET /api/health`); none has required options. Optional extras of section 2 (API v2.7–2.13): a musical `genre` (with `GET /api/genres` for the menu), music through the ages (`eras`), the CO₂ that sets the tempo, and the same month year after year (`step`).

- Base URL: the backend, e.g. `http://localhost:8000` (behind the webapp's nginx: the same origin, `/api/...`).
- Everything is JSON. Errors have a `detail` message.

## 1. `GET /api/instruments`: which instrument plays which dataset

```json
{
  "palette": "orchestra",
  "by_dataset": {"temperature": "Piano (chords)", "co2": "Contrabass", "precipitation": "Violins (melody)",
                 "snow": "Glockenspiel (melody)", "fires": "Trombones", "lightning": "Timpani",
                 "night_lights": "Synth pad", "sea_ice": "Bowed glass (melody)"},
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
| `sea_ice` | Bowed glass (melody) | % of the sea covered by ice | 1979-01 → 2026-10, no value in 1987-12 and 1988-01 |
| `vegetation` | Clarinet (melody) | vegetation index (NDVI) | 2000-03 → 2026-10, land only |
| `carbon_monoxide` | Bassoon | 10^18 molecules/cm² | 2000-04 → 2024-10, 20 months without value |
| `chlorophyll` | Vibraphone | mg/m³ (played on a log scale) | 2002-07 → 2026-10, sea only |

`dataset_id` is what you send below, and it is the file prefix of the dataset's folder in the data volume (`fires`, `co2`, `lightning`) wherever the two can match. Also accepted: `co2_atmosphere` for `co2`, `wildfires` for `fires`, `rain` for `precipitation`, `snowfall` for `snow`, `nightlights` for `night_lights`, `ice` and `seaice` for `sea_ice`, `ndvi` for `vegetation`, `co` for `carbon_monoxide`, `plankton` for `chlorophyll`.

`sea_ice` has no value on land: over an area it is the share of the sea that is frozen. Over land only (a country without coast) it is `null` or 0 and the bowed glass stays silent; it enters above about 5 % of ice.

## 2. Points + a time period in, music out

Two spellings of the same call. Use either.

### `POST /api/zones/zone-data` (the one `webapp/src/services/sonificationApi.ts` calls)
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
  "point_count": 2,
  "duration_s": 30.0,
  "bar_starts_s": [0.0, 2.774, 6.006, 8.918, 11.885, 14.966, 17.964, 20.6, 23.237, 26.658]
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
| `audio_data` | 30 seconds of music as a base64 MP3. `decodeAudioData` in `sonificationApi.ts` plays it as is |
| `next_time_period` | send it as the next `time_period` to continue. `null` when the data ends |
| `value` / `values` | the mean of each dataset over the points at `time_period`, in the units of `/api/instruments` |
| `duration_s` | the exact length of the audio in seconds (30, v2.10) |
| `bar_starts_s` | start second of each bar of the chunk, in order (v2.10): `bar_starts_s[0]` is `0` and the list is increasing; bar `i` lasts until `bar_starts_s[i+1]` (the last one until `duration_s`). One bar = one month, so `bar_starts_s.length` is the number of months from `time_period` to `next_time_period` (monthly data; with daily data one bar is several days). Use it to time what the listener hears (which month is playing) instead of assuming 3 s per month |
| `point_count` | the number of points received (with `mask`: the number of 0.1° cells it selects) |

### Optional: a musical genre (`"genre": "rocknroll"`)
Add `"genre"` to either call to hear the whole piece in one style. The instrument set stays the same from the
first bar to the last, so listeners can learn which instrument is which dataset. Without it: the orchestra.

`GET /api/genres` lists them for a menu (`{"default": "orchestra", "genres": [{id, name, bpm, description}]}`):

| `genre` | sound |
|---|---|
| `orchestra` | the orchestra (default) |
| `ragtime` | honky-tonk stride piano, tuba, clarinet, brushes |
| `swing` | jazz guitar, walking bass, trumpet, vibraphone, ride cymbal |
| `rocknroll` | electric guitar, electric bass, tenor sax, backbeat drums |
| `funk` | clavinet, octave bass, overdriven guitar, Hammond organ, disco drums |
| `synthpop` | polysynth, synth bass, square lead, synth brass, drum machine |
| `house` | off-beat piano and bass, saw lead, four-on-the-floor |
| `trap` | electric-piano arpeggios, 808 bass, synth lead, hi-hat rolls |
| `realistic` | **no instruments: the sound of each phenomenon** (below) |

Aliases are accepted (`jazz`, `rock`, `disco`, `electro`, `pop`, `classical`). `GET /api/instruments?genre=rocknroll`
gives the instrument of each dataset in that genre (without `?genre=` the answer is unchanged). The answer of the call
gets `"genre": "rocknroll"`.

- **Tempo**: each genre has its usual tempo (orchestra 76 bpm, rock'n'roll 150, house 124 ...; in `realistic` the heart's rate), and the temperature speeds it up or slows it down (below). A chunk is
  still 30 seconds, so it covers more months in a fast genre (19 months in rock'n'roll instead of 10): always use
  `next_time_period`.
- **The data is the same in every genre**: the same notes on the same beats; the genre changes the instruments, the
  rhythm they repeat the notes in, and adds drums (louder in hot periods).

**`realistic`**: every dataset is heard as what it is, from real recordings (free licences, see `soundfonts/README.md`):

| dataset | sound | what follows the data |
|---|---|---|
| precipitation | rain, plus a downpour layer | loudness = amount of rain |
| snow | soft airy wind | loudness = amount of snow |
| fires | campfire, plus a fireplace and a low roar for big fires | loudness and size = number of fire days |
| lightning | real thunderclaps | how often = lightning activity |
| co2 | a heartbeat, the "pulse of the planet" (synthesised); it beats only when `co2` is selected, even in years without CO₂ maps | it beats on every beat of the tempo, which follows the global CO₂: 60 bpm (a heart at rest) in 1880, about 103 in 2000, 132 today; irregular (skipped and extra beats) above 400 ppm, from 2015 |
| temperature | a low heat drone (synthesised) | warmer = its tones tremble faster, and a clashing semitone joins when hot |
| night_lights | the 50 Hz hum of street lamps | loudness = brightness |
| sea_ice | "singing" ice: falling pings of cracks and low creaks of floes (synthesised) | how often = how much ice |
| vegetation | crickets and a cicada (synthesised) | loudness = how green |
| carbon_monoxide | idling engines and machines (synthesised) | loudness = pollution above the background |
| chlorophyll | bubbles rising in the sea (synthesised) | how often = how much plankton |

### Optional: music through the ages (`"eras": true`)
A fun mode, off by default. Add `"eras": true` to either call and the music takes the style of its date:

| from | era | sound |
|---|---|---|
| 1880 | Romantic orchestra | the normal orchestra, no drums |
| 1910 | Ragtime & early jazz | honky-tonk stride piano, tuba, clarinet, brushes |
| 1930 | Swing & big band | jazz guitar, walking bass, trumpet, ride cymbal, swung eighths |
| 1950 | Rock'n'roll | electric guitar, bass, tenor sax, backbeat |
| 1965 | Rock, funk & disco | clavinet, octave bass, overdriven guitar, disco hi-hats |
| 1980 | Synth-pop | polysynth, synth bass, square lead, drum machine |
| 1995 | House & electronic | off-beat piano and bass, saw lead, four-on-the-floor |
| 2010 | Modern pop & trap | electric-piano arpeggios, 808 bass, hi-hat rolls |

The data still decides every pitch and loudness; the drums hit harder in hot years. The answer gets two extra fields:
```json
{"era": "Synth-pop", "eras": [{"name": "Synth-pop", "time_period": "1985-01", "start_s": 0.0}]}
```
`era` is the era at the start of the chunk; `eras` lists every era of the chunk with the second it starts (one
chunk can cross a boundary, e.g. 1905-1914). Without `"eras": true` nothing changes, and these fields are absent.

### What to expect
- **The CO₂ sets the tempo** (v2.13, every genre): each bar (one month) is played at the tempo of the global CO₂ of its date, so the music speeds up with the years: from 0.8x the genre's tempo in 1880 (290 ppm) to about 1.23x today (425 ppm); in `realistic` a heartbeat of 60 bpm in 1880, 75 in 1960, 103 in 2000, 132 today, which turns irregular above 400 ppm and over which the whole soundscape gets louder. CO₂ almost never goes down, so the heart never slows: this is how the piece makes the climate crisis heard. The same date is the same tempo in every chunk and every area. A chunk is still 30 seconds, so **30 seconds is about 8 months in 1880 and 12 to 13 months today**: always use `next_time_period`, do not add one month yourself. Month `i` of the chunk starts at `bar_starts_s[i]` seconds (the bars are not 3 s long): follow the clock with `bar_starts_s` and `duration_s` (v2.10).
- **`time_period` in the answer is where the music really starts.** It differs from the request only when you ask for a date before the first data.
- **Several datasets**: each instrument plays where its own data exists and is silent elsewhere (temperature and precipitation from 1999: the violins come in in June 2000).
- **Yearly data** (`night_lights`): the value is the year's. The synth pad starts clearly each January, when the value changes, then fades slowly over the year to a softer background note.
- **The same value is the same note** in every piece of the same area, so consecutive pieces connect.
- **Errors**: `400` unknown dataset or no data for these points and this period; `422` malformed request (no points, latitude out of range, another `aggregation`).
- A new area takes about a second on the server (its whole series is read once); the following pieces of the same area are faster (the series is kept in memory), and the same request again is immediate.

### Optional: the same month, year after year (`"step": "year"`, v2.12)
Add `"step": "year"` and each bar is the same month of the next year, at the same place:

```json
{"zone_id": "france", "dataset_ids": ["temperature", "precipitation"], "points": [...],
 "time_period": "2015-11", "step": "year"}
```
plays November 2015, November 2016, ... November 2025 (30 s, about 8 to 13 years like the months of the normal
mode), and answers `"next_time_period": "2026-11"` and `"step": "year"`. Ask for the next piece with that period and
`step` again. Bar `i` is year `time_period + i`, starting at `bar_starts_s[i]`; `values` are those of the first
November. Each instrument is scaled on that month over all the years, so a warmer November is a higher note than a
colder one. Without `step` (or `"step": "month"`) nothing changes.

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
- `genre` (optional, v2.10): the same genres as zone-data (`orchestra` default, `ragtime`, `swing`, `rocknroll`, `funk`, `synthpop`, `house`, `trap`, `realistic`; aliases `jazz`, `rock`, ...; `GET /api/genres`). Example: `{"time_period": "2018-07", "points_per_bar": 4, "genre": "funk", "path": ["..."]}`; the answer then has `"genre": "funk"`; `400` for an unknown genre. **The tempo stays fixed in vinyl mode**: bars are always 3 s whatever the genre (the genre's own tempo and the temperature-driven tempo are not used), so `duration_s`, `bar_seconds` and `stop_seconds` do not change and consecutive stretches of the same genre still join. Send the same genre for every stretch of a groove.

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
