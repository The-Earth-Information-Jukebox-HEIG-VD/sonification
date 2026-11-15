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

## The team's real monthly data: `build_monthly_dataset.py`
Turns the extraction team's monthly files (one `.npz` per year, 0.25°) into one dataset in the `INTENSITY_MAP_SPEC.md` format. Nothing is modelled: a variable is in the dataset only when the team has extracted it.

```bash
python simulation/build_monthly_dataset.py --precip ../data_processing/precipitation/monthly_2000_2026
# -> output/real_monthly_2000_2026_precipitation.npz / .json   (318 months, 1°, about 88 MB)
python simulation/build_monthly_dataset.py --precip DIR --res 2 --start 2015-01 --min-days 20
python simulation/build_monthly_dataset.py --temperature ../datasets/data/temperature_anomaly_1880_2025 --res 2 --prefix real_temperature_1880_2025
# -> output/real_temperature_1880_2025_surface_temperature.npz / .json   (1752 months, 2°, about 58 MB, ~1 min)
D=datasets/data      # the team's datasets repo, cloned inside this one (gitignored)
python simulation/build_monthly_dataset.py --temperature $D/temperature_anomaly_1880_2025 --precip $D/precipitation_rain_2000_2026 \
    --snow $D/precipitation_snow_2000_2026 --co2 $D/co2_2002_2022 --res 1 --frames-per-bar 3 --prefix team_monthly_2002_2022
# -> output/team_monthly_2002_2022_{precipitation,snowfall,surface_temperature,co2}   (235 common months 2002-09 .. 2022-03, ~2 min)
python -m pytest simulation/test_build_monthly_dataset.py -q      # converter test with small made-up files
```

| variable | units | source | how |
|---|---|---|---|
| precipitation | mm/day | NASA GPM IMERG (GIBS layer `IMERG_Precipitation_Rate`), monthly mean; yearly `.npz` or one `precipitation_YYYY_MM.npy` per month | `precip_mmh` × 24, area-weighted block mean from 0.25° to `--res` |
| co2 | ppm | the team's `co2_YYYY_MM.npy` (September 2002 → March 2022, 720 × 1440 float32 = 0.25°) | `--co2 DIR`; area-weighted block mean; `vmin/vmax` 365..435; **outline pixels removed**: the maps still carry the coasts and borders of the source picture as values 10–20 ppm above their neighbours, so a pixel more than 4.5 ppm above the lower quartile of its 10° block, and its four neighbours, become NaN (about 19% of the pixels; `dropped_outline_pixels` in the `.json`) |
| precipitation (rain) | mm/day | the team's `rain_YYYY_MM.npy`: since precipitation was split into rain and snow, `--precip` reads the rain alone | × 24, area-weighted block mean |
| snowfall | mm/day of melted water | the team's `snow_YYYY_MM.npy` (June 2000 → November 2026) | `--snow DIR`; × 24; `vmin/vmax` 0..3 (monthly means over a region are 0.05 to 1 mm/day) |
| fires | fire days per month | the team's `fires_YYYY_MM.npy` (NASA MODIS active fires, July 2002 → November 2026): days of the month with at least one fire in the pixel | `--fires DIR`; area-weighted block mean, so a region's value is its average number of fire days; `vmin/vmax` 0..8 |
| nightlights | brightness 0–1 | the team's `nightlights_YYYY.npy`, **one map per year** (2012 → 2023, NASA Earth at Night, SVS 5276) | `--nightlights DIR`; each yearly map is repeated for the 12 months of its year (`"source_time_step": "1 year"` in the `.json`); `vmin/vmax` 0..0.1 |
| lightning | share of area with lightning (0–1) | the team's `lightning_YYYY_MM.npy` (ISS LIS, from NASA SVS 5011; March 2017 → July 2023) | `--lightning DIR`; area-weighted block mean; **NaN beyond 55°** (the station does not see further: the 0 in the files there means "not observed"); `vmin/vmax` 0..0.4 |
| surface_temperature | degC **anomaly** | the team's `temperature_anomaly_YYYY_MM.npy` (1880-01 → 2025-12, 2° values copied onto the 0.25° common grid) | block mean ignoring NaN (no data stays NaN); `vmin/vmax` −5..+5; `"anomaly": true` in the `.json` |

- One frame is one month, so one bar is one month (June 2000 to November 2026 = 318 bars for the whole series).
- Months averaged over fewer than 25 days of data are listed in the `.json` as `partial_months` (October 2025: 24 days, November 2026: 13 days). `--min-days 20` drops such a month when it is the first or the last of the series.
- `snow_days` (number of days with snow) is not converted: the snowfall instrument expects an amount in mm/day.
- Temperature: the values are already anomalies, so the composer takes the value itself as the anomaly (z = value / robust spread of the whole series) instead of fitting a seasonal normal. The piano plays it like any temperature: higher = warmer than normal. Use `--res 2`: the source has 2° information, a finer grid only makes the file bigger. Like every dataset of the API it has one bar per month (30 s = about 8 months in 1880 to 12-13 today, since the global CO₂ sets the tempo, see `backend/FRONTEND_API.md`); to hear the whole history, a request sends `"frames_per_bar": 12` (one bar = one year, one beat = one season) or 24.
- With several inputs (`--precip DIR --temperature DIR`) only the months they all have are kept, because a dataset has one time axis. Build the 1880 → 2025 temperature on its own to keep the whole history.
- To add a variable when the team delivers it, add a line to `SOURCES` (and to `INPUT_ARGS`) in the script.
