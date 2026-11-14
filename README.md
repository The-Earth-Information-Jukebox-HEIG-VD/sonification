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
python simulation/build_daily_dataset.py --precip precipitation_pixels_2024-01-01_2024-12-31.npz   # REAL daily 2024 (default)
python simulation/build_realistic_dataset.py   # REAL monthly NASA GISTEMP + NOAA CO2 (2015 -> today)
python simulation/build_monthly_dataset.py --precip ../data_processing/precipitation/monthly_2000_2026   # the team's REAL monthly precipitation, 2000 -> 2026
python simulation/build_monthly_dataset.py --temperature temperature_anomaly_common_grid_npy --res 2 --frames-per-bar 12   # REAL temperature anomaly 1880 -> 2025
# the team's datasets repo (datasets/data): precipitation + temperature anomaly + CO2, see simulation/README.md
pip install --no-deps tinysoundfont          # audio rendering
python music/render_audio.py --download      # fetch the SoundFont once (~32 MB), so the first audio request does not wait
python music/sampler.py --download           # real orchestral samples (VSCO 2 CE, CC0, ~440 MB, once)
python music/orchestra.py simulation/output/real_daily_2024_ --zone switzerland --render --mp3
```
Listen to `music/output/orchestra_switzerland_all.mp3`, or open the `.mid` in any DAW (GarageBand, Ableton, Logic, MuseScore…). Every instrument has its own track. Examples are in `music/examples/`.

## Backend API (for the frontend)
```bash
uvicorn backend.app:app --reload --port 8000     # docs: http://localhost:8000/docs
python backend/test_api.py                        # end-to-end tests
```
Full contract: [`backend/API.md`](backend/API.md).

## Docker (what is deployed)
```bash
docker build -t earth-jukebox-sonification .                       # ~1 GB; add --build-arg WITH_VSCO=1 for the orchestral samples (+440 MB)
docker run -p 8000:8000   -v /path/to/datasets/data:/app/data:ro   -v jukebox-cache:/app/cache   earth-jukebox-sonification                                       # then http://localhost:8000/docs
```
The image is self-contained for audio: `tinysoundfont`, `ffmpeg` and the GeneralUser GS SoundFont are inside (fetched at build time, so the server needs no `git` and no network).

**Data.** `/app/data` receives the team's raw folders from the `datasets` repository, read-only, exactly as the `deployment` compose file mounts them:
```
/app/data/precipitation_2000_2026/precipitation_<YYYY>_<MM>.npy          (one file per month, mm/h)
/app/data/temperature_anomaly_1880_2025/temperature_anomaly_<YYYY>_<MM>.npy
/app/data/lightning_2017_2023/lightning_<YYYY>_<MM>.npy                  (share of each pixel with lightning, 0-1)
```
At start-up `backend/serve.py` runs `backend/prepare_data.py` in the background. It converts those folders with `simulation/build_monthly_dataset.py` into `/app/cache/_datasets`, which the API also scans:

| dataset | content |
|---|---|
| `real_temperature_1880_2025` | temperature anomaly, every month since 1880, 2° |
| `real_precipitation_2000_2026` | precipitation, June 2000 → November 2026, 1° |
| `real_lightning_2017_2023` | lightning activity, March 2017 → July 2023, 1°, 55°S–55°N |
| `real_monthly_2000_2025` | precipitation + temperature, on the months they share, 1° |
| `real_all_2017_2023` | precipitation + temperature + lightning, on the months all three share, 1° |

One bar is one month in all of them. The frontend decides what it asks for: the start date, the length (`duration_s`), and a faster time scale if it wants one (`"frames_per_bar": 12` in the request makes one bar a year).

- The API answers at once; `GET /api/health` has `"preparing_data": true` until the conversion is over, and each dataset appears in `GET /api/datasets` when it is ready. The first start takes a few minutes; with `/app/cache` on a volume, later starts convert nothing unless the raw files changed.
- The recipes are the `RECIPES` list at the top of `backend/prepare_data.py`. To try them without Docker: `python backend/prepare_data.py --raw ../datasets/data --out simulation/output`.
- Ready-made `<prefix>_<variable>.npz` + `.json` files placed directly in `/app/data` are served as before.
- `co2_2002_2022/` is not part of the deployed datasets yet: the data team is correcting those files. The converter can already read them (`--co2`), so adding CO₂ later is one entry in `INPUTS` and one in `RECIPES` in `backend/prepare_data.py`. Any other folder is ignored.
- `JUKEBOX_PREPARE=0` skips the conversion. `PORT` changes the port (default 8000).
- The GitHub workflow publishes the image on a version tag: `git tag v0.1.0 && git push origin v0.1.0`.
