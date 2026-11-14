#!/usr/bin/env python3
"""
Monthly dataset made ONLY of the team's real extractions, in the INTENSITY_MAP_SPEC format.

Input  (from the data-extraction side, one file per year, monthly, 0.25 deg):
    precipitation_monthly_<YYYY>.npz
        months [T] 'YYYY-MM', precip_mmh [T,720,1440] (mm/h, mean rate of the month),
        snow_days [T,720,1440], n_days [T] (days with data in the month),
        lat [720] (north -> south), lon [1440], cell_area_km2 [720,1440]

Output (one monthly time axis, one frame = one bar):
    real_monthly_<first year>_<last year>_<variable>.npz + .json
      precipitation   REAL  mm/day (precip_mmh * 24), regridded to `--res` with an area-weighted block mean

Nothing is modelled here: a variable is in the dataset only when the team has extracted it.
To add one (e.g. temperature), add a line to SOURCES below.

Not converted: `snow_days` (number of days with snow) is not the amount of snow that the snowfall
instrument expects (mm/day), so it is left out until the team decides how to play it.

Usage
  python simulation/build_monthly_dataset.py --precip ../data_processing/precipitation/monthly_2000_2026
  python simulation/build_monthly_dataset.py --precip DIR --res 2 --start 2015-01 --min-days 20
Needs: numpy
"""
from __future__ import annotations

import argparse
import glob
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
                          source="NASA GPM IMERG daily precipitation rate (GIBS layer IMERG_Precipitation_Rate), "
                                 "monthly mean, extracted by the team (REAL)"),
}


def block_mean(stack: np.ndarray, f: int, area: np.ndarray) -> np.ndarray:
    """[T, H, W] -> [T, H/f, W/f], each coarse cell = area-weighted mean of its f x f fine cells."""
    if f == 1:
        return stack.astype(np.float32)
    t, h, w = stack.shape
    a = area.astype(np.float64).reshape(h // f, f, w // f, f)
    x = stack.astype(np.float32).reshape(t, h // f, f, w // f, f)
    return ((x * a[None]).sum(axis=(2, 4)) / a.sum(axis=(1, 3))[None]).astype(np.float32)


def read_variable(folder: Path, spec: dict, res: float):
    """All yearly files of one variable -> (months ['YYYY-MM'], data [T, H, W] in final units, n_days [T])."""
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
    ap.add_argument("--precip", required=True, help="folder with precipitation_monthly_<YYYY>.npz from the extraction team")
    ap.add_argument("--res", type=float, default=1.0, choices=[0.25, 0.5, 1.0, 2.0], help="output grid in degrees")
    ap.add_argument("--start", help="first month to keep, 'YYYY-MM'")
    ap.add_argument("--end", help="last month to keep, 'YYYY-MM'")
    ap.add_argument("--min-days", type=int, default=0,
                    help="drop months at the two ENDS of the series that have fewer days of data than this "
                         "(e.g. 20 removes a half-finished last month); months in the middle are always kept")
    ap.add_argument("--out", default=str(HERE / "output"))
    ap.add_argument("--prefix", default=None)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    inputs = {"precipitation": Path(a.precip)}
    lat, lon = make_grid(a.res)
    land = load_land_fraction(a.res).astype(np.float32)
    built = {}
    for var, folder in inputs.items():
        months, data, days = read_variable(folder, SOURCES[var], a.res)
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

    first = next(iter(built.values()))[0]
    prefix = a.prefix or f"real_monthly_{first[0][:4]}_{first[-1][:4]}"
    print(f"{len(first)} months {first[0]} .. {first[-1]}  ->  {prefix}_*.npz  ({a.res:g} deg)")
    for var, (months, data, days) in built.items():
        units, vmin, vmax, _ = svar.VARIABLES[var]
        times = np.array([f"{m}-15T00:00:00Z" for m in months])
        partial = {m: d for m, d in zip(months, days) if d < 25}
        meta = {"variable": var, "units": units, "source": SOURCES[var]["source"], "real": True,
                "vmin": vmin, "vmax": vmax, "resolution_deg": a.res, "shape": list(data.shape),
                "lat_order": "north_to_south", "lon_range": [-180, 180],
                "time_start": str(times[0]), "time_end": str(times[-1]), "time_step": "1 month",
                "partial_months": partial}        # months averaged over fewer than 25 days of data
        intensity = np.clip((data - vmin) / (vmax - vmin), 0, 1).astype(np.float16)     # float16: halves the size
        np.savez_compressed(out / f"{prefix}_{var}.npz", data=data.astype(np.float32), intensity=intensity,
                            lat=lat, lon=lon, time=times, land_fraction=land, n_days=np.array(days, dtype=np.uint8))
        (out / f"{prefix}_{var}.json").write_text(json.dumps(meta, indent=2))
        size = (out / f"{prefix}_{var}.npz").stat().st_size / 1e6
        w = np.cos(np.deg2rad(lat))[:, None] * np.ones((1, len(lon)))
        print(f"  {var:15s} {np.nanmin(data):7.2f} .. {np.nanmax(data):7.2f} {units}, "
              f"global mean {float((data * w[None]).sum() / (w.sum() * len(data))):.2f} {units}, "
              f"grid {data.shape[1:]}, {size:.0f} MB")
        if partial:
            print(f"  months with fewer than 25 days of data: {partial}")


if __name__ == "__main__":
    main()
