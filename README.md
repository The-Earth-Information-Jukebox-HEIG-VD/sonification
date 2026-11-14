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
