# sonification: Earth Information Jukebox (HEIG-VD)

The music side of the NASA Space Apps 2026 "Earth Information Jukebox" challenge: turning NASA EIC intensity maps into an orchestra, with one instrument per variable.

```
simulation/   realistic dataset (real NASA/NOAA data) + simulated maps + the data contract (INTENSITY_MAP_SPEC.md)
soundfonts/   VSCO 2 CE orchestral samples (CC0) + GeneralUser GS fallback, auto-downloaded; licenses
backend/      FastAPI server: POST /api/compose -> score JSON + MIDI + MP3 (see backend/API.md)
music/        zone_to_midi.py : one variable  -> time series -> chords -> MIDI
              orchestra.py    : all variables -> one multi-track score
              sampler.py      : SFZ sampler for the VSCO orchestral recordings
              render_audio.py : MIDI -> WAV/MP3 (VSCO samples + SoundFont fallback) + hall reverb
```

Quick start:
```bash
pip install -r requirements.txt
python simulation/build_daily_dataset.py --precip ../data_processing/precipitation/2024/precipitation_pixels_2024-01-01_2024-12-31.npz   # REAL daily 2024 (default; the file lives in data_processing/precipitation/2024/)
python simulation/build_realistic_dataset.py   # REAL monthly NASA GISTEMP + NOAA CO2 (2015 -> today)
python simulation/build_monthly_dataset.py --precip ../data_processing/precipitation/monthly_2000_2026   # the team's REAL monthly precipitation, 2000 -> 2026
python simulation/build_monthly_dataset.py --temperature ../datasets/data/temperature_anomaly_1880_2025 --res 2 --prefix real_temperature_1880_2025   # REAL temperature anomaly 1880 -> 2025
# the team's datasets repo (datasets/data) has these folders: co2_2002_2022, fires_2002_2026, lightning_2017_2023, nightlights_2012_2023, precipitation_rain_2000_2026, precipitation_snow_2000_2026, sea_ice_1979_2026, temperature_anomaly_1880_2025 (and rain_belt_2000_2026, not played); see simulation/README.md
pip install --no-deps tinysoundfont          # audio rendering
python music/render_audio.py --download      # fetch the SoundFont once (~32 MB), so the first audio request does not wait
python music/sampler.py --download           # real orchestral samples (VSCO 2 CE, CC0, ~440 MB, once)
python music/soundscape.py --download        # recordings for genre "realistic" (rain, fire, thunder ...; ~30 MB, once)
python music/orchestra.py simulation/output/real_daily_2024_ --zone switzerland --render --mp3
```
Listen to `music/output/orchestra_switzerland_all.mp3`, or open the `.mid` in any DAW (GarageBand, Ableton, Logic, MuseScore…). Every instrument has its own track. Examples are in `music/examples/`.

## Backend API (for the frontend)
```bash
uvicorn backend.app:app --reload --port 8000     # docs: http://localhost:8000/docs
python backend/test_api.py                        # end-to-end tests
```
**What the frontend uses: [`backend/FRONTEND_API.md`](backend/FRONTEND_API.md)**, four calls with no options: `GET /api/instruments`, `POST /api/zones/zone-data` (the music of the selected points, 30 s per chunk), `POST /api/zones/series` (the monthly series, for the chart) and `POST /api/zones/groove` (vinyl mode: the music along a path at one date); `POST /api/sonify` (one dataset, one value) is also served. The engine's own endpoints, for development: [`backend/API.md`](backend/API.md).

## Docker (what is deployed)
```bash
docker build -t earth-jukebox-sonification .                       # ~1 GB; add --build-arg WITH_VSCO=1 for the orchestral samples (+440 MB)
docker run -p 8000:8000   -v /path/to/datasets/data:/app/data:ro   -v jukebox-cache:/app/cache   earth-jukebox-sonification                                       # then http://localhost:8000/docs
```
The image is self-contained for audio: `tinysoundfont`, `ffmpeg` and the GeneralUser GS SoundFont are inside (fetched at build time, so the server needs no `git` and no network).

**Data.** `/app/data` receives the team's raw folders from the `datasets` repository, read-only, exactly as the `deployment` compose file mounts them:
```
/app/data/precipitation_rain_2000_2026/rain_<YYYY>_<MM>.npy              (one file per month, mm/h)
/app/data/precipitation_snow_2000_2026/snow_<YYYY>_<MM>.npy              (one file per month, mm/h of melted water)
/app/data/nightlights_2012_2023/nightlights_<YYYY>.npy                   (ONE FILE PER YEAR, brightness 0-1)
/app/data/fires_2002_2026/fires_<YYYY>_<MM>.npy                          (days of the month with a fire, 0-31)
/app/data/temperature_anomaly_1880_2025/temperature_anomaly_<YYYY>_<MM>.npy
/app/data/lightning_2017_2023/lightning_<YYYY>_<MM>.npy                  (share of each pixel with lightning, 0-1)
/app/data/co2_2002_2022/co2_<YYYY>_<MM>.npy                              (ppm)
/app/data/sea_ice_1979_2026/sea_ice_<YYYY>_<MM>.npy                      (% of each cell covered by sea ice, NaN on land)
```
At start-up `backend/serve.py` runs `backend/prepare_data.py` in the background. It converts those folders with `simulation/build_monthly_dataset.py` into `/app/cache/_datasets`, which the API also scans:

| dataset | content |
|---|---|
| `real_temperature_1880_2025` | temperature anomaly, every month since 1880, 2° |
| `real_precipitation_2000_2026` | rain, June 2000 → November 2026, 1° |
| `real_snow_2000_2026` | snow (melted water), June 2000 → November 2026, 1° |
| `real_fires_2002_2026` | fires, July 2002 → November 2026, 1°: days of the month with at least one fire |
| `real_nightlights_2012_2023` | night lights, 2012 → 2023, 1°; one map per year, repeated for its 12 months |
| `real_lightning_2017_2023` | lightning activity, March 2017 → July 2023, 1°, 55°S–55°N |
| `real_sea_ice_1979_2026` | sea ice, January 1979 → October 2026, 1°: % of the sea covered by ice; December 1987 and January 1988 are missing in the source and stay empty |
| `real_co2_2002_2022` | CO₂, September 2002 → March 2022, 1°, outline pixels removed (see below) |

One dataset per variable, one bar per month in all of them. **A request can mix them freely** (`JUKEBOX_MERGE_DATASETS=1`, set in the image): each variable is read from its own dataset and the composer lines them up by date, so a piece can start in 1950 with the piano alone, or in 1999 with the violins joining in June 2000. An instrument is silent for the months its data does not cover. The frontend decides what it asks for: the start date, the length (`duration_s`), and a faster time scale if it wants one (`"frames_per_bar": 12` in the request makes one bar a year).

- The API answers at once; `GET /api/health` has `"preparing_data": true` until the conversion is over, and each dataset appears in `GET /api/datasets` when it is ready. The first start takes a few minutes; with `/app/cache` on a volume, later starts convert nothing unless the raw files changed.
- The recipes are the `RECIPES` list at the top of `backend/prepare_data.py`. To try them without Docker: `python backend/prepare_data.py --raw ../datasets/data --out simulation/output`.
- Ready-made `<prefix>_<variable>.npz` + `.json` files placed directly in `/app/data` are served as before.
- **CO₂ outline pixels.** The delivered CO₂ maps still carry the outline of the source picture (coasts, borders) as values near the top of the colour scale, 10–20 ppm above their neighbours, on about a fifth of the pixels. Left in, a small region full of borders reads far too high (Switzerland: about +15 ppm). The converter sets those pixels to "no data" (`drop_lines` in `SOURCES["co2"]`, `simulation/build_monthly_dataset.py`): a pixel more than 4.5 ppm above the lower quartile of its 10° neighbourhood, and its four neighbours. It is a stopgap: once the files have these pixels as NaN, it changes nothing. Each dataset's `.json` records how many pixels were dropped.
- **Yearly data.** Night lights come as one map per year. The converter repeats each map for the 12 months of its year, so the dataset has the same monthly time axis as the others and lines up with them by date; nothing is invented between two years. In the music the synth pad starts clearly each January and fades slowly over the year.
- **Same value, same note.** `/api/sonify` plays a long series chunk after chunk, so each instrument's pitch is scaled on the whole series of the selected area (`pitch_range: "series"`), not on the 30 seconds of one chunk. Without it a yearly value, constant inside a chunk, would always be the same middle note.
- A folder the preparation step does not know is ignored.
- The image serves only the frontend's calls (`backend/FRONTEND_API.md`) and `/api/health` (`JUKEBOX_FULL_API=0`). `-e JUKEBOX_FULL_API=1` also serves the engine's endpoints, e.g. to explore them at `/docs`.
- `JUKEBOX_MERGE_DATASETS=0` goes back to one dataset per request (the default outside Docker).
- `JUKEBOX_PREPARE=0` skips the conversion. `PORT` changes the port (default 8000).
- The GitHub workflow publishes the image on a version tag: `git tag v0.1.0 && git push origin v0.1.0`.
