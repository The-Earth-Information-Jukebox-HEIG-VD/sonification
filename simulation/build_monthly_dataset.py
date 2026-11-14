#!/usr/bin/env python3
"""
Monthly dataset made ONLY of the team's real extractions, in the INTENSITY_MAP_SPEC format.

Input  (from the data-extraction side, monthly, on the team's 0.25 deg common grid):
    precipitation_monthly_<YYYY>.npz            (--precip DIR, one file per year)
        months [T] 'YYYY-MM', precip_mmh [T,720,1440] (mm/h, mean rate of the month),
        snow_days [T,720,1440], n_days [T] (days with data in the month),
        lat [720] (north -> south), lon [1440], cell_area_km2 [720,1440]
    precipitation_<YYYY>_<MM>.npy               (--precip DIR also accepts one file per month, as in the
        [720,1440] float16, mm/h                    team's datasets repo: datasets/data/precipitation_2000_2026)
    temperature_anomaly_<YYYY>_<MM>.npy         (--temperature DIR, one file per month, 1880-01 ->)
        [720,1440] float16, temperature anomaly in degC, NaN = no data,
        row 0 = 90N, column 0 = 180W (see the README.md of that folder)
    rain_<YYYY>_<MM>.npy / snow_<YYYY>_<MM>.npy (--precip DIR / --snow DIR, one file per month, 2000-06 ->)
        [720,1440] float16, mm/h: the team's precipitation split into rain and snow (melted water)
    nightlights_<YYYY>.npy                      (--nightlights DIR, ONE FILE PER YEAR, 2012 -> 2023)
        [720,1440] float32, brightness 0-1. Repeated for the 12 months of its year (monthly time axis)
    fires_<YYYY>_<MM>.npy                       (--fires DIR, one file per month, 2002-07 ->)
        [720,1440] float16, days of the month with at least one fire detected (0-31)
    lightning_<YYYY>_<MM>.npy                   (--lightning DIR, one file per month, 2017-03 -> 2023-07)
        [720,1440] float16, share of the pixel with lightning (0-1), observed between 55S and 55N only
    co2_<YYYY>_<MM>.npy                         (--co2 DIR, one file per month, 2002-09 -> 2022-03)
        [356,720] float32, ppm, 0.5 deg covering 89N -> 89S (assumed: row 0 = north, column 0 = 180W,
        like the other files; that folder has no README)

Any .npy grid that covers 360 deg of longitude in W columns is read: step = 360 / W, and the H rows are
centred on the equator (rows missing at the poles become NaN).

Output (one monthly time axis, one frame = one bar):
    real_monthly_<first year>_<last year>_<variable>.npz + .json
      precipitation         REAL  mm/day (precip_mmh * 24), regridded to `--res` with an area-weighted block mean
      co2                   REAL  ppm, vmin/vmax 365..435 (the range of the delivered files)
      lightning             REAL  share of area with lightning (0-1), vmin/vmax 0..0.4, NaN beyond 55 deg
      snowfall              REAL  mm/day of melted water (snow_mmh * 24), vmin/vmax 0..3
      fires                 REAL  fire days per month (mean over the area), vmin/vmax 0..8
      nightlights           REAL  brightness 0-1, vmin/vmax 0..0.1, one value per year held for 12 months
      surface_temperature   REAL  temperature ANOMALY in degC (meta "anomaly": true), played by the piano like the
                                  temperature of the other datasets; vmin/vmax -5..+5 instead of -40..+40.
                                  The source is a 2 deg map copied onto 0.25 deg, so `--res 2` loses nothing.

Nothing is modelled here: a variable is in the dataset only when the team has extracted it.
To add one (e.g. temperature), add a line to SOURCES below.

Not converted: `snow_days` (number of days with snow) is not the amount of snow that the snowfall
instrument expects (mm/day), so it is left out until the team decides how to play it.

With several inputs, only the months that ALL of them have are kept (one time axis per dataset).

Usage
  python simulation/build_monthly_dataset.py --precip ../data_processing/precipitation/monthly_2000_2026
  python simulation/build_monthly_dataset.py --precip DIR --res 2 --start 2015-01 --min-days 20
  python simulation/build_monthly_dataset.py --temperature temperature_anomaly_common_grid_npy --res 2 --frames-per-bar 12
  D=datasets/data; python simulation/build_monthly_dataset.py --temperature $D/temperature_anomaly_1880_2025 \
      --precip $D/precipitation_2000_2026 --co2 $D/co2_2002_2022 --prefix team_monthly    # all three, 2002-09 .. 2022-03
Needs: numpy
"""
from __future__ import annotations

import argparse
import calendar
import glob
import re
import json
import warnings
from pathlib import Path

import numpy as np

import simulate_variables as svar
from simulate_intensity_map import load_land_fraction, make_grid

HERE = Path(__file__).resolve().parent

# variable -> how to read it from the team's yearly files
#   pattern : file name inside the folder      array : the [T, 720, 1440] array to read
#   factor  : multiply to get the units of simulate_variables.VARIABLES (mm/h * 24 = mm/day)
SOURCES = {
    "precipitation": dict(pattern="precipitation_monthly_*.npz", array="precip_mmh", factor=24.0,
                          # or one .npy per month (datasets repo). Since the team split precipitation into
                          # rain and snow, the files are rain_YYYY_MM.npy: the violins then play the rain alone.
                          npy_pattern=("precipitation_*_*.npy", "rain_*_*.npy"),
                          source="NASA GPM IMERG daily precipitation rate (GIBS layer IMERG_Precipitation_Rate), "
                                 "monthly mean, extracted by the team (REAL)"),
    # one .npy per month; the values are anomalies, so they get their own range (and "anomaly": true in the
    # metadata: the composer then uses the value itself as the anomaly instead of fitting a seasonal normal)
    "surface_temperature": dict(pattern="temperature_anomaly_*.npy", reader="monthly_npy", factor=1.0,
                                units="degC", vmin=-5.0, vmax=5.0, anomaly=True,
                                source="Monthly surface temperature anomaly (degC), 2 deg map copied onto the team's "
                                       "0.25 deg common grid, extracted by the team (REAL)"),
}
# drop_lines: the delivered CO2 maps still carry the outline of the visualization (coasts, borders) as pixels
# near the top of the colour scale, 10-20 ppm above their neighbours, where real monthly CO2 differs by a few
# ppm at most. Those pixels are set to NaN (see drop_overlay_lines). A stopgap until the files themselves
# have them as NaN; it then does nothing.
SOURCES["co2"] = dict(pattern="co2_*.npy", reader="monthly_npy", factor=1.0, units="ppm", vmin=365.0, vmax=435.0,
                      drop_lines=dict(block_deg=10.0, above=4.5),
                      source="Monthly CO2 (ppm), extracted by the team (REAL); pixels of the map outline removed "
                             "(more than 4.5 ppm above the lower quartile of their 10 deg neighbourhood)")
# share (0-1) of each pixel where the instrument saw lightning in the month. The space station only sees
# 55S -> 55N: beyond that the files hold 0, which means "not observed", so those rows become NaN (lat_limit).
# vmax 0.4: over a region the monthly mean is about 0.03 (median) to 0.1 (stormy season), so that range
# drives the timpani from a few hits to a roll; a single stormy pixel can reach 1.
SOURCES["lightning"] = dict(pattern="lightning_*.npy", reader="monthly_npy", factor=1.0,
                            units="share of area with lightning (0-1)", vmin=0.0, vmax=0.4, lat_limit=55.0,
                            source="ISS LIS lightning from the NASA SVS 5011 visualization, monthly share of each "
                                   "0.25 deg pixel with lightning, 55S-55N, extracted by the team (REAL)")
# snow in mm/h of melted water, monthly mean (the part of the precipitation that fell as snow). Over a region
# the monthly mean is about 0.05 to 1 mm/day, hence vmax 3 (the daily dataset, with its peaks, uses 10).
SOURCES["snowfall"] = dict(pattern="snow_*_*.npy", reader="monthly_npy", factor=24.0,
                           units="mm/day", vmin=0.0, vmax=3.0,
                           source="NASA GPM IMERG daily precipitation rate, the part that fell as snow (melted "
                                  "water), monthly mean, extracted by the team (REAL)")
# ONE MAP PER YEAR (nightlights_YYYY.npy): brightness 0-1 of each pixel. The dataset keeps the monthly time
# axis of all the others, so the yearly map is simply repeated for the 12 months of its year: nothing is
# invented between two years, and it lines up by date with the monthly variables.
# vmax 0.1: over a region the mean brightness is about 0.002 (Amazon) to 0.1 (Switzerland).
SOURCES["nightlights"] = dict(pattern="nightlights_*.npy", reader="yearly_npy", factor=1.0,
                              units="brightness (0-1)", vmin=0.0, vmax=0.1,
                              source="NASA Earth at Night (SVS 5276), yearly map of night-time lights, brightness "
                                     "0-1, extracted by the team (REAL); each yearly map is repeated for its 12 months")
# number of days of the month with at least one fire detected in the pixel (0-31). Over a region the monthly
# mean is about 0.2 (quiet) to 7 (West Africa or the Amazon arc in their burning season), hence vmax 8.
SOURCES["fires"] = dict(pattern="fires_*_*.npy", reader="monthly_npy", factor=1.0,
                        units="fire days per month", vmin=0.0, vmax=8.0,
                        source="NASA MODIS active fires (Terra + Aqua, GIBS layer MODIS_Combined_Thermal_Anomalies_All), "
                               "days of the month with at least one fire per 0.25 deg pixel, extracted by the team (REAL)")
INPUT_ARGS = {"precipitation": "precip", "surface_temperature": "temperature", "co2": "co2",
              "lightning": "lightning", "snowfall": "snow", "nightlights": "nightlights",
              "fires": "fires"}                                                            # variable -> option


def block_mean(stack: np.ndarray, f: int, area: np.ndarray) -> np.ndarray:
    """[T, H, W] -> [T, H/f, W/f], each coarse cell = area-weighted mean of its f x f fine cells."""
    if f == 1:
        return stack.astype(np.float32)
    t, h, w = stack.shape
    a = area.astype(np.float64).reshape(h // f, f, w // f, f)
    x = stack.astype(np.float32).reshape(t, h // f, f, w // f, f)
    ok = np.isfinite(x)
    if ok.all():
        return ((x * a[None]).sum(axis=(2, 4)) / a.sum(axis=(1, 3))[None]).astype(np.float32)
    aw = np.where(ok, a[None], 0.0)              # NaN = no data: mean of the cells that have data, NaN if none
    num, den = (np.where(ok, x, 0.0) * aw).sum(axis=(2, 4)), aw.sum(axis=(2, 4))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan).astype(np.float32)


def drop_overlay_lines(a: np.ndarray, step: float, block_deg: float = 10.0, above: float = 4.5):
    """Set to NaN the pixels that sit far above their surroundings: lines drawn on the source picture.

    The surroundings of a pixel are its block of block_deg x block_deg degrees, summarised by the LOWER
    QUARTILE of the block: the lines only add high values, so the lower quartile stays the value of the clean
    pixels even when half of a block is covered by lines. A pixel more than `above` over it is dropped, and so
    are its 4 direct neighbours (the blurred edge of a line). Returns (cleaned copy, number of pixels dropped)."""
    a = a.astype(np.float32)
    n = max(2, int(round(block_deg / step)))
    h, w = a.shape
    hh, ww = -(-h // n) * n, -(-w // n) * n
    p = np.full((hh, ww), np.nan, np.float32)
    p[:h, :w] = a
    blocks = p.reshape(hh // n, n, ww // n, n).transpose(0, 2, 1, 3).reshape(hh // n, ww // n, n * n)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # blocks without any data
        low = np.nanpercentile(blocks, 25, axis=2)
        low = np.where(np.isfinite(low), low, np.nanmedian(a))
    around = np.repeat(np.repeat(low, n, axis=0), n, axis=1)[:h, :w]
    with np.errstate(invalid="ignore"):
        bad = a > around + above
    grown = bad.copy()
    grown[1:] |= bad[:-1]
    grown[:-1] |= bad[1:]
    grown[:, 1:] |= bad[:, :-1]
    grown[:, :-1] |= bad[:, 1:]
    grown &= np.isfinite(a)
    a[grown] = np.nan
    return a, int(grown.sum())


def read_monthly_npy(folder: Path, spec: dict, res: float, start=None, end=None, pattern=None):
    """One [H, W] .npy per month (name ..._YYYY_MM.npy) -> (months, data [T, H', W'], n_days [T]).
    The input step is 360 / W degrees; the H rows are centred on the equator, north first.
    Only the months inside [start, end] are read (the full 1880 -> 2025 temperature folder is 3.4 GB)."""
    pattern = pattern or spec["pattern"]
    yearly = spec.get("reader") == "yearly_npy"       # one map per year (name ..._YYYY.npy), see SOURCES
    found = []
    for path in glob.glob(str(folder / pattern)):
        m = re.search(r"_(\d{4})\.npy$" if yearly else r"(\d{4})_(\d{2})\.npy$", path)
        if m:
            found.append((m.group(1) if yearly else f"{m.group(1)}-{m.group(2)}", path))
    if yearly:      # keep a year when any of its months is inside [start, end]
        found = sorted(x for x in found if (not start or f"{x[0]}-12" >= start) and (not end or f"{x[0]}-01" <= end))
        gaps = [f"{a[0]} -> {b[0]}" for a, b in zip(found, found[1:]) if int(b[0]) - int(a[0]) != 1]
        if gaps:
            raise SystemExit(f"years are missing in {folder}: {gaps}")
    else:
        found = sorted(x for x in found if (not start or x[0] >= start) and (not end or x[0] <= end))
    if not found:
        raise SystemExit(f"no {pattern} in {folder} for the requested months")
    shape = np.load(found[0][1], mmap_mode="r").shape
    step = 360.0 / shape[1]
    full_h = int(round(180 / step))
    pad = (full_h - shape[0]) // 2
    if pad < 0 or full_h - shape[0] != 2 * pad:
        raise SystemExit(f"{found[0][1]}: {shape} is not a global grid centred on the equator")
    f = res / step
    if abs(f - round(f)) > 1e-6 or round(f) < 1:
        raise SystemExit(f"--res {res} must be a multiple of the input grid ({step:g} deg)")
    lat = 90 - step / 2 - step * np.arange(full_h)
    area = np.cos(np.deg2rad(lat))[:, None] * np.ones((1, shape[1]))
    frames, dropped = [], []
    for i, (month, path) in enumerate(found):
        a = np.load(path, allow_pickle=False)
        if a.shape != shape:
            raise SystemExit(f"{path}: expected {shape} like the other months, got {a.shape}")
        if pad:                                    # e.g. 89N -> 89S: the polar rows have no data
            a = np.pad(a.astype(np.float32), ((pad, pad), (0, 0)), constant_values=np.nan)
        if spec.get("lat_limit"):                  # not observed beyond this latitude: no data, not zero
            a = a.astype(np.float32)
            a[np.abs(lat) > spec["lat_limit"]] = np.nan
        if spec.get("drop_lines"):                 # outline of the source picture left in the values
            valid = int(np.isfinite(a).sum())
            a, n_dropped = drop_overlay_lines(a, step, **spec["drop_lines"])
            dropped.append(n_dropped / max(valid, 1))
        frames.append(block_mean(a[None], int(round(f)), area)[0] * np.float32(spec["factor"]))
        if i % 240 == 0 or i == len(found) - 1:
            print(f"  read {Path(path).name} ({i + 1}/{len(found)})", flush=True)
    months = [m for m, _ in found]
    if yearly:      # the map of a year is the value of each of its 12 months
        frames = [fr for fr in frames for _ in range(12)]
        months = [f"{y}-{m:02d}" for y in months for m in range(1, 13)]
    days = [calendar.monthrange(int(m[:4]), int(m[5:7]))[1] for m in months]
    if dropped:
        print(f"  outline pixels dropped: {100 * np.mean(dropped):.1f}% of the valid pixels on average "
              f"({100 * min(dropped):.1f}% .. {100 * max(dropped):.1f}% per month)", flush=True)
        spec["_dropped_share"] = [round(float(x), 4) for x in (np.mean(dropped), min(dropped), max(dropped))]
    return months, np.stack(frames), days


def read_variable(folder: Path, spec: dict, res: float, start=None, end=None):
    """All yearly files of one variable -> (months ['YYYY-MM'], data [T, H, W] in final units, n_days [T])."""
    if spec.get("reader") in ("monthly_npy", "yearly_npy"):
        return read_monthly_npy(folder, spec, res, start, end)
    if spec.get("npy_pattern") and not glob.glob(str(folder / spec["pattern"])):
        patterns = spec["npy_pattern"] if isinstance(spec["npy_pattern"], (tuple, list)) else [spec["npy_pattern"]]
        for pattern in patterns:                                     # one .npy per month instead of yearly .npz
            if glob.glob(str(folder / pattern)):
                return read_monthly_npy(folder, spec, res, start, end, pattern=pattern)
    files = sorted(glob.glob(str(folder / spec["pattern"])))
    if not files:
        raise SystemExit(f"no {spec['pattern']} in {folder}")
    months, days, frames = [], [], []
    for path in files:
        z = np.load(path, allow_pickle=False)
        lat = z["lat"]
        if lat[0] < lat[-1]:
            raise SystemExit(f"{path}: expected latitudes north -> south (as in the extraction output)")
        step = abs(float(lat[0] - lat[1]))
        f = res / step
        if abs(f - round(f)) > 1e-6 or round(f) < 1:
            raise SystemExit(f"--res {res} must be a multiple of the input grid ({step} deg)")
        frames.append(block_mean(z[spec["array"]], int(round(f)), z["cell_area_km2"]) * np.float32(spec["factor"]))
        months += [str(m) for m in z["months"]]
        days += [int(d) for d in z["n_days"]] if "n_days" in z.files else [31] * len(z["months"])
        print(f"  read {Path(path).name}: {len(z['months'])} months")
    if len(set(months)) != len(months):
        raise SystemExit("the same month appears in two files")
    order = np.argsort(months)
    return [months[i] for i in order], np.concatenate(frames)[order], [days[i] for i in order]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--precip", help="folder with precipitation_monthly_<YYYY>.npz, precipitation_<YYYY>_<MM>.npy "
                                     "or rain_<YYYY>_<MM>.npy")
    ap.add_argument("--temperature", help="folder with temperature_anomaly_<YYYY>_<MM>.npy (the team's common grid)")
    ap.add_argument("--co2", help="folder with co2_<YYYY>_<MM>.npy")
    ap.add_argument("--lightning", help="folder with lightning_<YYYY>_<MM>.npy (share of each pixel with lightning, 0-1)")
    ap.add_argument("--snow", help="folder with snow_<YYYY>_<MM>.npy (mm/h of melted water)")
    ap.add_argument("--fires", help="folder with fires_<YYYY>_<MM>.npy (days of the month with a fire, 0-31)")
    ap.add_argument("--nightlights", help="folder with nightlights_<YYYY>.npy, ONE MAP PER YEAR (brightness 0-1); "
                                          "each map is repeated for the 12 months of its year")
    ap.add_argument("--res", type=float, default=1.0, choices=[0.25, 0.5, 1.0, 2.0], help="output grid in degrees")
    ap.add_argument("--start", help="first month to keep, 'YYYY-MM'")
    ap.add_argument("--end", help="last month to keep, 'YYYY-MM'")
    ap.add_argument("--min-days", type=int, default=0,
                    help="drop months at the two ENDS of the series that have fewer days of data than this "
                         "(e.g. 20 removes a half-finished last month); months in the middle are always kept")
    ap.add_argument("--frames-per-bar", type=float, default=None,
                    help="hint for the composer: months per musical bar (12 = one bar per year; default 1)")
    ap.add_argument("--out", default=str(HERE / "output"))
    ap.add_argument("--prefix", default=None)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    inputs = {var: Path(getattr(a, opt)) for var, opt in INPUT_ARGS.items() if getattr(a, opt)}
    if not inputs:
        ap.error("give at least one input folder: " + ", ".join(f"--{o}" for o in INPUT_ARGS.values()))
    lat, lon = make_grid(a.res)
    land = load_land_fraction(a.res).astype(np.float32)
    built = {}
    for var, folder in inputs.items():
        months, data, days = read_variable(folder, SOURCES[var], a.res, a.start, a.end)
        keep = [i for i, m in enumerate(months) if (not a.start or m >= a.start) and (not a.end or m <= a.end)]
        while keep and days[keep[0]] < a.min_days:
            keep.pop(0)
        while keep and days[keep[-1]] < a.min_days:
            keep.pop()
        if not keep:
            raise SystemExit("no months left in the requested window")
        months, data, days = [months[i] for i in keep], data[keep], [days[i] for i in keep]
        idx = [int(m[:4]) * 12 + int(m[5:7]) for m in months]
        gaps = [f"{months[i]} -> {months[i + 1]}" for i in range(len(idx) - 1) if idx[i + 1] - idx[i] != 1]
        if gaps:
            raise SystemExit(f"months are missing in {folder}: {gaps}")
        built[var] = (months, data, days)

    if len(built) > 1:                     # one time axis per dataset: keep the months every variable has
        common = sorted(set.intersection(*(set(b[0]) for b in built.values())))
        if not common:
            raise SystemExit("the inputs have no month in common; build them separately (one dataset each)")
        for var, (months, data, days) in built.items():
            if len(months) != len(common):
                print(f"  {var}: kept the {len(common)} common months {common[0]} .. {common[-1]} "
                      f"(of {months[0]} .. {months[-1]})")
            pos = {m: i for i, m in enumerate(months)}
            keep = [pos[m] for m in common]
            built[var] = (common, data[keep], [days[i] for i in keep])

    first = next(iter(built.values()))[0]
    prefix = a.prefix or f"real_monthly_{first[0][:4]}_{first[-1][:4]}"
    print(f"{len(first)} months {first[0]} .. {first[-1]}  ->  {prefix}_*.npz  ({a.res:g} deg)")
    for var, (months, data, days) in built.items():
        units, vmin, vmax, _ = svar.VARIABLES.get(var, (None, 0.0, 1.0, None))   # SOURCES can give its own
        spec = SOURCES[var]
        units, vmin, vmax = spec.get("units", units), spec.get("vmin", vmin), spec.get("vmax", vmax)
        times = np.array([f"{m}-15T00:00:00Z" for m in months])
        partial = {m: d for m, d in zip(months, days) if d < 25}
        meta = {"variable": var, "units": units, "source": SOURCES[var]["source"], "real": True,
                "vmin": vmin, "vmax": vmax, "resolution_deg": a.res, "shape": list(data.shape),
                "lat_order": "north_to_south", "lon_range": [-180, 180],
                "time_start": str(times[0]), "time_end": str(times[-1]), "time_step": "1 month",
                "partial_months": partial}        # months averaged over fewer than 25 days of data
        if spec.get("anomaly"):
            meta["anomaly"] = True                 # values are already anomalies (degC away from a normal)
        if spec.get("reader") == "yearly_npy":     # one map per year, repeated for its 12 months
            meta["source_time_step"] = "1 year"
        if spec.get("drop_lines"):                 # how the outline pixels were removed, and how many
            meta["dropped_outline_pixels"] = {**spec["drop_lines"], "share_mean_min_max": spec.get("_dropped_share")}
        if a.frames_per_bar:
            meta["frames_per_bar"] = a.frames_per_bar
        intensity = np.clip((data - vmin) / (vmax - vmin), 0, 1).astype(np.float16)     # float16: halves the size
        np.savez_compressed(out / f"{prefix}_{var}.npz", data=data.astype(np.float32), intensity=intensity,
                            lat=lat, lon=lon, time=times, land_fraction=land, n_days=np.array(days, dtype=np.uint8))
        (out / f"{prefix}_{var}.json").write_text(json.dumps(meta, indent=2))
        size = (out / f"{prefix}_{var}.npz").stat().st_size / 1e6
        w = np.cos(np.deg2rad(lat))[:, None] * np.ones((1, len(lon)))
        ww = np.where(np.isfinite(data), w[None], 0.0)
        print(f"  {var:15s} {np.nanmin(data):7.2f} .. {np.nanmax(data):7.2f} {units}, "
              f"global mean {float(np.nansum(data * ww) / ww.sum()):.2f} {units}, "
              f"grid {data.shape[1:]}, {size:.0f} MB")
        if partial:
            print(f"  months with fewer than 25 days of data: {partial}")


if __name__ == "__main__":
    main()
