# Simulated intensity maps

Fake but realistic **global temperature maps** in the format of `INTENSITY_MAP_SPEC.md`, so the music side (temperature → notes) can be developed before the real NASA data extraction is ready.

## Run
```bash
pip install numpy matplotlib pillow      # matplotlib/pillow only for --preview
python simulation/simulate_intensity_map.py --preview
# -> simulation/output/temperature_sim_none_12f_1deg.npz / .json / .gif
```

Useful variants:
```bash
# 2 years, 24 frames, El Niño + European heatwave + exaggerated warming (good demo)
python simulation/simulate_intensity_map.py --scenario both --warming 0.5 --years 2 --frames 24 --preview
# smooth (no weather noise), coarse grid: easier to debug the note mapping
python simulation/simulate_intensity_map.py --noise 0 --res 5
```

## Load it
```python
import numpy as np, json
z = np.load("simulation/output/temperature_sim_none_12f_1deg.npz")
inten = z["intensity"]      # [T, H, W] in 0..1
temp  = z["data"]           # [T, H, W] in °C
lat, lon, time = z["lat"], z["lon"], z["time"]
meta = json.load(open("simulation/output/temperature_sim_none_12f_1deg.json"))
```

## What the model includes
- Latitude gradient (≈28 °C tropics → polar cold), real coastlines (0.25° land mask in `land_mask_025deg.npz`)
- Seasons with the correct hemisphere phase; continental vs maritime swings (Yakutsk −39/+20 °C, Zurich ≈0/+19 °C)
- Ocean currents: Gulf Stream, Kuroshio, Humboldt, Benguela, California; hot deserts; Greenland and Antarctic ice
- Smooth, time-correlated "weather" noise
- Scenarios: `elnino`, `heatwave`, `both`, plus a `--warming` trend with Arctic amplification

Note: ocean cells behave like sea-surface temperature (they never drop below about −2 °C); land cells behave like air temperature. That matches what the SVS visualizations show.

## All the other variables: `simulate_variables.py`
Generates every orchestra variable on the **same time axis** (same frames, years and scenario), named `output/<prefix>_<variable>.npz`:

| variable | units | fixed range | what's modelled |
|---|---|---|---|
| surface_temperature | °C | −40..40 | (same as above) |
| precipitation | mm/day | 0..20 | ITCZ following the sun, Indian/African/Amazon/Australian monsoons, storm tracks, dry deserts, intermittent showers |
| co2 | ppm | 400..440 | +2.4 ppm/yr trend, NH seasonal breathing (max May, min Sep), China/Europe/US/India sources, fire seasons |
| aerosol_dust | AOD | 0..1.2 | Sahara + trans-Atlantic summer plume, Gobi spring storms, Arabia, Australia |
| aerosol_smoke | AOD | 0..1.2 | Southern Africa (Aug), Sahel (Jan), Amazon (Sep), boreal summer fires, Mediterranean fires in `heatwave` |
| lightning | flashes/km²/month | 0..10 | tropical land convection following the sun, Congo, Maracaibo, pre-monsoon India, US plains, very spiky |

```bash
python simulation/simulate_variables.py --scenario both --warming 0.5 --years 2 --frames 24 --preview
```
`--warming` also intensifies rain, boreal fires and lightning, so the climate story stays consistent across instruments.

## Realistic dataset on REAL observations: `build_realistic_dataset.py`
```bash
pip install netCDF4
python simulation/build_realistic_dataset.py            # Jan 2015 .. latest month (≈140 months)
```
| variable | source |
|---|---|
| surface_temperature | **NASA GISTEMP v4** monthly anomaly (real, 2° grid) + modelled seasonal climatology |
| co2 | **NOAA Mauna Loa** monthly CO₂ (real trend) + modelled seasonal/spatial pattern |
| precipitation, dust, smoke, lightning | modelled seasonal fields, **coupled to the real local temperature anomaly** (hot months: more smoke and lightning, drier land) |

Raw files download automatically into `simulation/raw/` (not committed). Output: `output/real_<start>_<end>_1deg_<variable>.npz`, and each `.json` says which variables are real (`"real": true`). The backend prefers this dataset when present. Checks: global yearly anomalies match NASA's (2024 = +1.29 °C); Zurich Jun 2019 +4.1 °C; El Niño Dec 2015 +3.0 °C.

## Daily dataset around the team's REAL precipitation: `build_daily_dataset.py`
```bash
python simulation/build_daily_dataset.py --precip precipitation_pixels_2024-01-01_2024-12-31.npz
```
Input: the extraction team's file (daily, 0.25°, `precip_mmh`, `is_snow`, `dates`, `lat`, `lon`, `cell_area_km2`). Output `output/real_daily_2024_<variable>.npz`:

| variable | source | grid |
|---|---|---|
| precipitation | **REAL** (extraction team), mm/day = mm/h × 24, area-weighted block mean | 1° (`--res 0.5/0.25` for finer) |
| snowfall | **REAL**: precipitation where `is_snow` | 1° |
| surface_temperature | **REAL** NASA GISTEMP monthly anomaly, interpolated to days, + climatology | 2° |
| co2 | **REAL** NOAA Mauna Loa trend, interpolated to days, + pattern | 2° |
| dust, smoke, lightning | modelled daily, coupled to the real temperature anomaly | 2° |

Each `.json` has `"frames_per_bar": 7`, so the orchestra plays **one bar per week** (2024 → 53 bars, about 2 min 47 s), and each beat is the **mean of its days** (no day skipped). Checks: Valencia flood 29 Oct 2024 = 131 mm/day; India monsoon July = 12.7 mm/day; global mean 2.54 mm/day.

`output/` is generated: don't commit it (see `.gitignore`).
