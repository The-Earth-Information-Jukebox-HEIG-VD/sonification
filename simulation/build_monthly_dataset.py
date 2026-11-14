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
    co2_<YYYY>_<MM>.npy                         (--co2 DIR, one file per month, 2002-09 -> 2022-03)
        [356,720] float32, ppm, 0.5 deg covering 89N -> 89S (assumed: row 0 = north, column 0 = 180W,
        like the other files; that folder has no README)

Any .npy grid that covers 360 deg of longitude in W columns is read: step = 360 / W, and the H rows are
centred on the equator (rows missing at the poles become NaN).

Output (one monthly time axis, one frame = one bar):
    real_monthly_<first year>_<last year>_<variable>.npz + .json
      precipitation         REAL  mm/day (precip_mmh * 24), regridded to `--res` with an area-weighted block mean
      co2                   REAL  ppm, vmin/vmax 365..435 (the range of the delivered files)
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
                          npy_pattern="precipitation_*_*.npy",          # or one .npy per month (datasets repo)
                          source="NASA GPM IMERG daily precipitation rate (GIBS layer IMERG_Precipitation_Rate), "
                                 "monthly mean, extracted by the team (REAL)"),
    # one .npy per month; the values are anomalies, so they get their own range (and "anomaly": true in the
    # metadata: the composer then uses the value itself as the anomaly instead of fitting a seasonal normal)
    "surface_temperature": dict(pattern="temperature_anomaly_*.npy", reader="monthly_npy", factor=1.0,
                                units="degC", vmin=-5.0, vmax=5.0, anomaly=True,
                                source="Monthly surface temperature anomaly (degC), 2 deg map copied onto the team's "
                                       "0.25 deg common grid, extracted by the team (REAL)"),
}
SOURCES["co2"] = dict(pattern="co2_*.npy", reader="monthly_npy", factor=1.0, units="ppm", vmin=365.0, vmax=435.0,
                      source="Monthly CO2 (ppm), 0.5 deg grid, extracted by the team (REAL)")
INPUT_ARGS = {"precipitation": "precip", "surface_temperature": "temperature", "co2": "co2"}  # variable -> option


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


def read_monthly_npy(folder: Path, spec: dict, res: float, start=None, end=None, pattern=None):
    """One [H, W] .npy per month (name ..._YYYY_MM.npy) -> (months, data [T, H', W'], n_days [T]).
    The input step is 360 / W degrees; the H rows are centred on the equator, north first.
    Only the months inside [start, end] are read (the full 1880 -> 2025 temperature folder is 3.4 GB)."""
    pattern = pattern or spec["pattern"]
    found = []
    for path in glob.glob(str(folder / pattern)):
        m = re.search(r"(\d{4})_(\d{2})\.npy$", path)
        if m:
            found.append((f"{m.group(1)}-{m.group(2)}", path))
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
    frames = []
    for i, (month, path) in enumerate(found):
        a = np.load(path, allow_pickle=False)
        if a.shape != shape:
            raise SystemExit(f"{path}: expected {shape} like the other months, got {a.shape}")
        if pad:                                    # e.g. 89N -> 89S: the polar rows have no data
            a = np.pad(a.astype(np.float32), ((pad, pad), (0, 0)), constant_values=np.nan)
        frames.append(block_mean(a[None], int(round(f)), area)[0] * np.float32(spec["factor"]))
        if i % 240 == 0 or i == len(found) - 1:
            print(f"  read {Path(path).name} ({i + 1}/{len(found)})", flush=True)
    months = [m for m, _ in found]
    days = [calendar.monthrange(int(m[:4]), int(m[5:7]))[1] for m in months]
    return months, np.stack(frames), days


def read_variable(folder: Path, spec: dict, res: float, start=None, end=None):
    """All yearly files of one variable -> (months ['YYYY-MM'], data [T, H, W] in final units, n_days [T])."""
    if spec.get("reader") == "monthly_npy":
        return read_monthly_npy(folder, spec, res, start, end)
    if spec.get("npy_pattern") and not glob.glob(str(folder / spec["pattern"])) \
            and glob.glob(str(folder / spec["npy_pattern"])):        # one .npy per month instead of yearly .npz
        return read_monthly_npy(folder, spec, res, start, end, pattern=spec["npy_pattern"])
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
    ap.add_argument("--precip", help="folder with precipitation_monthly_<YYYY>.npz (or precipitation_<YYYY>_<MM>.npy)")
    ap.add_argument("--temperature", help="folder with temperature_anomaly_<YYYY>_<MM>.npy (the team's common grid)")
    ap.add_argument("--co2", help="folder with co2_<YYYY>_<MM>.npy")
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
        units, vmin, vmax, _ = svar.VARIABLES[var]
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
