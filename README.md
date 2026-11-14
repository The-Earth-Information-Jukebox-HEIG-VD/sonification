# sonification: Earth Information Jukebox (HEIG-VD)

The music side of the NASA Space Apps 2026 "Earth Information Jukebox" challenge: turning NASA EIC intensity maps into an orchestra, with one instrument per variable.

```
simulation/   fake temperature intensity maps + the data contract (INTENSITY_MAP_SPEC.md)
music/        zone -> time series -> chords -> MIDI (zone_to_midi.py)
```

Quick start:
```bash
pip install -r requirements.txt
python simulation/simulate_intensity_map.py --scenario both --warming 0.5 --years 2 --frames 24 --preview
python music/zone_to_midi.py simulation/output/temperature_sim_both_24f_1deg.npz --zone europe --preview
```
Then open `music/output/*.mid` in any DAW (GarageBand, Ableton, MuseScore…).
