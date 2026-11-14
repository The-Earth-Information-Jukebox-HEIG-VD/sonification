# Intensity Map: data contract (extraction → music)

This is the format the **data-extraction** side delivers and the **music/orchestra** side consumes.
`simulate_intensity_map.py` produces exactly this format with fake temperature data, so both sides can work in parallel.

## One file per variable (instrument)
`<variable>_<dataset>.npz` (numpy, compressed) + `<same name>.json` (metadata)

### Arrays in the `.npz`
| key | dtype | shape | meaning |
|---|---|---|---|
| `data` | float32 | `[T, H, W]` | physical value in `units` (e.g. °C). `NaN` = no data (land in an ocean dataset, outside the colorbar, labels…) |
| `intensity` | float32 | `[T, H, W]` | `(data - vmin) / (vmax - vmin)`, clipped to 0..1. **This is what the music side reads.** |
| `lat` | float32 | `[H]` | cell-center latitude, **north → south** (+90 → −90), row 0 = top of the image |
| `lon` | float32 | `[W]` | cell-center longitude, **−180 → +180**, column 0 = left edge (dateline) |
| `time` | str | `[T]` | ISO-8601 UTC timestamp of each frame (`2024-07-15T00:00:00Z`) |
| `land_fraction` | float32 | `[H, W]` | optional; 0 = ocean, 1 = land |

### Metadata `.json`
```json
{
  "variable": "surface_temperature",   // temperature | precipitation | aerosol_dust | co2 | lightning ...
  "units": "degC",
  "source": "SVS 3912 ECCO2 SST",      // or "SIMULATED"
  "vmin": -40, "vmax": 40,             // FIXED range used for intensity (not per-frame!)
  "resolution_deg": 1.0,
  "shape": [T, H, W],
  "lat_order": "north_to_south",
  "lon_range": [-180, 180],
  "time_start": "...", "time_end": "..."
}
```

## Rules
1. **Equirectangular** (plate carrée) grid, the same as the flat SVS maps. Re-grid if the source is a globe or a crop.
2. **Fixed `vmin`/`vmax` per variable** (from the dataset's colorbar), so the same intensity always means the same physical value.
3. **NaN for "no data"**: never encode missing data as 0, because 0 is a valid intensity (coldest).
4. Preferred grid: **1°** (180×360) or **2°** (90×180). The music side downsamples anyway.
5. If a variable has categories rather than a scale (e.g. aerosol type), deliver one file per category (`aerosol_dust`, `aerosol_smoke`…), each with its own intensity 0..1.

## Suggested fixed ranges
| variable | units | vmin | vmax | source colorbar |
|---|---|---|---|---|
| surface / sea-surface temperature | °C | −40 | 40 | SVS 3912: blue≈0, green 10–20, yellow≈25, red≈32 |
| forest height | m | 0 | 50 | SVS 4950 |
| salinity | PSU | 33 | 36 | SVS 5425 |
| night-light change | relative | −1 | 1 | SVS 5313 (orange = loss, purple = gain) |
| others | — | from colorbar | from colorbar | |
