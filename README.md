# sonification: Earth Information Jukebox (HEIG-VD)

The music side of the NASA Space Apps 2026 "Earth Information Jukebox" challenge: turning NASA EIC intensity maps into an orchestra, with one instrument per variable.

```
simulation/   fake intensity maps for every variable + the data contract (INTENSITY_MAP_SPEC.md)
soundfonts/   free sampled instruments (GeneralUser GS, auto-downloaded) + licenses
backend/      FastAPI server: POST /api/compose -> score JSON + MIDI + MP3 (see backend/API.md)
music/        zone_to_midi.py : one variable  -> time series -> chords -> MIDI
              orchestra.py    : all variables -> one multi-track score
              render_audio.py : MIDI -> WAV with real samples + hall reverb
```

Quick start:
```bash
pip install -r requirements.txt
python simulation/simulate_variables.py --scenario both --warming 0.5 --years 2 --frames 24
pip install --no-deps tinysoundfont          # for good-quality audio rendering
python music/orchestra.py simulation/output/sim_both_24f_1deg_ --zone india --preview --render
```
Listen to `music/output/orchestra_india_all.wav` (sampled instruments), or open `music/output/orchestra_india_all.mid` in any DAW (GarageBand, Ableton, Logic, MuseScore…). Every instrument has its own track. Examples are in `music/examples/`.

## Backend API (for the frontend)
```bash
uvicorn backend.app:app --reload --port 8000     # docs: http://localhost:8000/docs
python backend/test_api.py                        # end-to-end tests
```
Full contract: [`backend/API.md`](backend/API.md).
