#!/usr/bin/env python3
"""
Daily dataset built around the team's REAL precipitation extraction (one file per year, daily, 0.25 deg).

Input  (from the data-extraction side):
    precipitation_pixels_<start>_<end>.npz
        dates [T] 'YYYY-MM-DD', precip_mmh [T,720,1440] (mm/h, daily mean rate), is_snow [T,720,1440] bool,
        lat [720] (north -> south), lon [1440], cell_area_km2 [720,1440]

Output (INTENSITY_MAP_SPEC format, same daily time axis for every variable):
    real_daily_<start>_<end>_<variable>.npz + .json
      precipitation        REAL  mm/day (precip_mmh * 24), regridded to `--res` (area-weighted block mean)
      snowfall             REAL  mm/day of precipitation falling as snow (from is_snow)
      surface_temperature  REAL  NASA GISTEMP monthly anomaly, interpolated to days, + modelled climatology
      co2                  REAL  NOAA Mauna Loa monthly trend, interpolated to days, + modelled pattern
      aerosol_dust, aerosol_smoke, lightning   modelled daily fields coupled to the real temperature anomaly
    Each .json has "frames_per_bar": 7 -> the orchestra plays one bar per week by default.

Usage
  python simulation/build_daily_dataset.py --precip precipitation_pixels_2024-01-01_2024-12-31.npz
  python simulation/build_daily_dataset.py --precip FILE --res 0.5      # finer precipitation grid
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

import build_realistic_dataset as real
import simulate_intensity_map as tsim
import simulate_variables as svar
from simulate_intensity_map import load_land_fraction, make_grid

HERE = Path(__file__).resolve().parent
VARIABLES = dict(svar.VARIABLES)
VARIABLES["snowfall"] = ("mm/day", 0.0, 10.0, "Blues")


def block_mean(field: np.ndarray, f: int, weights: np.ndarray | None = None) -> np.ndarray:
    """Average f x f blocks of a 2-D field (optionally area-weighted)."""
    h, w = field.shape
    x = field.astype(np.float32).reshape(h // f, f, w // f, f)
    if weights is None:
        return x.mean(axis=(1, 3))
    ww = weights.reshape(h // f, f, w // f, f)
    return (x * ww).sum(axis=(1, 3)) / ww.sum(axis=(1, 3))


def monthly_to_daily(days: list[date], monthly: dict, key=lambda d: (d.year, d.month)):
    """Linear interpolation of monthly values (centred on the 15th) to each day."""
    months = sorted(monthly)
    t_m = np.array([date(y, m, 15).toordinal() for y, m in months], dtype=float)
    t_d = np.array([d.toordinal() for d in days], dtype=float)
    vals = np.stack([np.asarray(monthly[k], dtype=np.float32) for k in months])
    flat = vals.reshape(len(months), -1)
    out = np.empty((len(days), flat.shape[1]), np.float32)
    idx = np.clip(np.searchsorted(t_m, t_d) - 1, 0, len(t_m) - 2)
    w = np.clip((t_d - t_m[idx]) / (t_m[idx + 1] - t_m[idx]), 0, 1)[:, None]
    out[:] = flat[idx] * (1 - w) + flat[idx + 1] * w
    return out.reshape((len(days),) + vals.shape[1:])


def save(out: Path, prefix: str, var: str, data, lat, lon, times, land, source, real_flag, res, extra=None):
    units, vmin, vmax, _ = VARIABLES[var]
    data = data.astype(np.float32)
    intensity = np.clip((data - vmin) / (vmax - vmin), 0, 1).astype(np.float16)   # float16: halves the size
    meta = {"variable": var, "units": units, "source": source, "real": real_flag, "vmin": vmin, "vmax": vmax,
            "resolution_deg": res, "shape": list(data.shape), "lat_order": "north_to_south",
            "lon_range": [-180, 180], "time_start": str(times[0]), "time_end": str(times[-1]),
            "time_step": "1 day", "frames_per_bar": 7}
    np.savez_compressed(out / f"{prefix}_{var}.npz", data=data, intensity=intensity, lat=lat, lon=lon,
                        time=times, land_fraction=land.astype(np.float32), **(extra or {}))
    (out / f"{prefix}_{var}.json").write_text(json.dumps(meta, indent=2))
    print(f"  {var:20s} {np.nanmin(data):8.2f} .. {np.nanmax(data):8.2f} {units:18s} grid {data.shape[1:]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--precip", required=True, help="precipitation_pixels_*.npz from the extraction team")
    ap.add_argument("--res", type=float, default=1.0, choices=[0.25, 0.5, 1.0],
                    help="grid for precipitation/snowfall (other variables use 2 deg, GISTEMP's native grid)")
    ap.add_argument("--out", default=str(HERE / "output"))
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    z = np.load(a.precip, allow_pickle=False)
    dates = [str(d) for d in z["dates"]]
    days = [date.fromisoformat(d) for d in dates]
    times = np.array([f"{d}T12:00:00Z" for d in dates])
    lat_in, lon_in = z["lat"], z["lon"]
    if lat_in[0] < lat_in[-1]:
        raise SystemExit("expected latitudes north -> south (as in the extraction output)")
    f = int(round((lat_in[0] - lat_in[1]) and (a.res / abs(float(lat_in[0] - lat_in[1])))))
    prefix = a.prefix or f"real_daily_{dates[0][:4]}_{dates[-1][:4]}" if dates[0][:4] != dates[-1][:4] \
        else (a.prefix or f"real_daily_{dates[0][:4]}")
    print(f"{len(days)} days {dates[0]} .. {dates[-1]}  precipitation grid {lat_in.size}x{lon_in.size} "
          f"-> {a.res} deg (block {f}x{f})  ->  {prefix}_*.npz")

    # ---- REAL precipitation + snowfall (area-weighted block mean, one day at a time: low memory)
    area = z["cell_area_km2"].astype(np.float32) if "cell_area_km2" in z.files else None
    P, S = z["precip_mmh"], z["is_snow"]
    H, W = lat_in.size // f, lon_in.size // f
    precip = np.empty((len(days), H, W), np.float32)
    snow = np.empty((len(days), H, W), np.float32)
    for k in range(len(days)):
        p = P[k].astype(np.float32) * 24.0                      # mm/h -> mm/day
        precip[k] = block_mean(p, f, area)
        snow[k] = block_mean(p * S[k], f, area)
    lat_p, lon_p = make_grid(a.res)
    land_p = load_land_fraction(a.res)
    src = f"REAL daily precipitation (extraction team: {Path(a.precip).name})"
    save(out, prefix, "precipitation", precip, lat_p, lon_p, times, land_p, src, True, a.res)
    save(out, prefix, "snowfall", snow, lat_p, lon_p, times, land_p, src + ", snow flag", True, a.res)
    del precip, snow

    # ---- REAL temperature (GISTEMP monthly anomaly -> days) + climatology, on GISTEMP's 2 deg grid
    gist = real.fetch(real.GISTEMP_URL, real.RAW / "gistemp1200_GHCNv4_ERSSTv5.nc", gunzip=True)
    co2p = real.fetch(real.CO2_URL, real.RAW / "co2_mm_mlo.csv")
    anom2, gidx = real.load_gistemp(gist)
    co2 = real.load_co2(co2p)
    lat2, lon2 = make_grid(2.0)
    land2 = load_land_fraction(2.0)
    LAT, LON, annual_mean, seas_amp = tsim.static_fields(lat2, lon2, land2)
    need = sorted({(d.year, d.month) for d in days} | {((d - timedelta(days=31)).year, (d - timedelta(days=31)).month) for d in days}
                  | {((d + timedelta(days=31)).year, (d + timedelta(days=31)).month) for d in days})
    have = {ym: anom2[gidx[ym]] for ym in need if ym in gidx}
    last = max(have)
    for ym in need:                                            # months after the latest GISTEMP: persist
        have.setdefault(ym, have[last] if ym > last else have[min(have)])
    anom = monthly_to_daily(days, have)
    month_pos = np.array([(d.month - 1) + (d.day - 15) / 30.44 for d in days])
    season = -np.cos(2 * np.pi * month_pos / 12)
    temp = annual_mean[None] + seas_amp[None] * season[:, None, None] + anom
    save(out, prefix, "surface_temperature", temp, lat2, lon2, times, land2,
         "NASA GISTEMP v4 monthly anomaly (REAL) interpolated to days + modelled climatology", True, 2.0,
         extra={"anomaly": anom.astype(np.float32)})

    # ---- REAL CO2 trend (Mauna Loa monthly -> days) + modelled seasonal/spatial pattern
    co2_d = monthly_to_daily(days, {ym: co2.get(ym, co2[max(co2)]) for ym in need})
    ctx = {"scenario": "none", "warming": 0.0, "co2_trend": 0.0}
    rng = np.random.default_rng(a.seed)
    nz = svar.NoiseField(rng, LAT.shape, 7.5, 0.97)
    co2f = np.stack([svar.co2(LAT, LON, land2, mp, s, 0.0, nz.step() * 0.4, ctx) - 420.0 + c
                     for mp, s, c in zip(month_pos, season, co2_d)])
    save(out, prefix, "co2", co2f, lat2, lon2, times, land2,
         "NOAA Mauna Loa monthly CO2 (REAL trend) interpolated to days + modelled pattern", True, 2.0)

    # ---- modelled dust / smoke / lightning, daily, coupled to the real temperature anomaly
    an = np.nan_to_num(anom)
    for var in ("aerosol_dust", "aerosol_smoke", "lightning"):
        fn, sig, rho = svar.MODELS[var]
        nzv = svar.NoiseField(np.random.default_rng(a.seed + sum(map(ord, var))), LAT.shape, max(1.0, sig / 2), 0.7)
        stack = np.stack([fn(LAT, LON, land2, mp, s, 0.0, nzv.step(), ctx) for mp, s in zip(month_pos, season)])
        if var == "aerosol_smoke":
            stack *= np.exp(0.3 * np.clip(an, -2, 3))
        elif var == "lightning":
            stack *= np.clip(1 + 0.15 * an, 0.4, 2.0)
        else:
            stack *= np.clip(1 + 0.1 * an, 0.5, 1.8)
        save(out, prefix, var, stack, lat2, lon2, times, land2,
             "MODELLED daily field coupled to REAL GISTEMP anomaly", False, 2.0)


if __name__ == "__main__":
    main()
