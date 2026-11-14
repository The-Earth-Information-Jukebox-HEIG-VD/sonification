#!/usr/bin/env python3
"""
Realistic dataset built on REAL observations, in the INTENSITY_MAP_SPEC format.

  surface_temperature  = NASA GISTEMP v4 monthly anomaly (real, 2deg grid, 1200 km smoothing)
                         + a modelled seasonal climatology (simulate_intensity_map.py, no noise)
                         -> real heatwaves, cold spells, El Nino 2015-16 and 2023-24, warming trend
  co2                  = NOAA Mauna Loa monthly CO2 (real, deseasonalised trend)
                         + modelled seasonal cycle and spatial pattern (sources, NH breathing)
  precipitation, aerosol_dust, aerosol_smoke, lightning
                       = modelled seasonal fields (simulate_variables.py), physically coupled to the
                         REAL local temperature anomaly: hotter-than-normal months get more fire smoke,
                         more lightning and drier land.
                         (Real precipitation from GPCP can replace the model when available.)

Sources (downloaded automatically into simulation/raw/, not committed):
  https://data.giss.nasa.gov/pub/gistemp/gistemp1200_GHCNv4_ERSSTv5.nc.gz   (NASA GISS)
  https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv                 (NOAA GML)

Usage
  python simulation/build_realistic_dataset.py                       # 2015-01 .. latest month
  python simulation/build_realistic_dataset.py --start 2019-01 --end 2025-12 --res 1
Needs: numpy, netCDF4 (pip install netCDF4)
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import simulate_intensity_map as tsim
import simulate_variables as svar
from simulate_intensity_map import load_land_fraction, make_grid

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
GISTEMP_URL = "https://data.giss.nasa.gov/pub/gistemp/gistemp1200_GHCNv4_ERSSTv5.nc.gz"
CO2_URL = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv"


def fetch(url: str, dest: Path, gunzip=False) -> Path:
    if dest.exists():
        return dest
    RAW.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url} ...")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    if gunzip:
        with gzip.open(tmp, "rb") as g, open(dest, "wb") as f:
            shutil.copyfileobj(g, f)
        tmp.unlink()
    else:
        tmp.rename(dest)
    return dest


def month_list(start: str, end: str):
    y, m = map(int, start.split("-")[:2])
    ye, me = map(int, end.split("-")[:2])
    out = []
    while (y, m) <= (ye, me):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def load_gistemp(path: Path):
    import netCDF4 as nc
    d = nc.Dataset(str(path))
    t = d.variables["time"]
    dates = nc.num2date(t[:], t.units)
    anom = np.ma.filled(d.variables["tempanomaly"][:].astype(np.float32), np.nan)  # [T, lat(S->N), lon]
    lat = np.array(d.variables["lat"][:])
    if lat[0] < lat[-1]:                       # make it north -> south like our spec
        anom = anom[:, ::-1, :]
    index = {(dt.year, dt.month): i for i, dt in enumerate(dates)}
    return anom, index


def load_co2(path: Path):
    out = {}
    with open(path) as f:
        rows = [r for r in csv.reader(line for line in f if not line.startswith("#"))]
    head = rows[0]
    iy, im, ide = head.index("year"), head.index("month"), head.index("deseasonalized")
    for r in rows[1:]:
        try:
            out[(int(r[iy]), int(r[im]))] = float(r[ide])
        except (ValueError, IndexError):
            pass
    return out


def upsample(field2deg: np.ndarray, res: float) -> np.ndarray:
    """2deg grid -> finer grid by block repeat (GISTEMP is already smooth, 1200 km)."""
    f = int(round(2 / res))
    return np.repeat(np.repeat(field2deg, f, axis=-2), f, axis=-1) if f > 1 else field2deg


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2015-01")
    ap.add_argument("--end", default=None, help="default: latest month available in both sources")
    ap.add_argument("--res", type=float, default=1.0, choices=[0.5, 1.0, 2.0])
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--out", default=str(HERE / "output"))
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    gist_path = fetch(GISTEMP_URL, RAW / "gistemp1200_GHCNv4_ERSSTv5.nc", gunzip=True)
    co2_path = fetch(CO2_URL, RAW / "co2_mm_mlo.csv")
    anom2, gidx = load_gistemp(gist_path)
    co2 = load_co2(co2_path)
    latest = min(max(gidx), max(co2))
    end = a.end or f"{latest[0]}-{latest[1]:02d}"
    months = [ym for ym in month_list(a.start, end) if ym in gidx and ym in co2]
    if not months:
        raise SystemExit("no overlapping months in the requested window")
    n = len(months)
    times = np.array([f"{y}-{m:02d}-15T00:00:00Z" for y, m in months])
    prefix = a.prefix or f"real_{months[0][0]}_{months[-1][0]}_{a.res:g}deg"
    print(f"{n} months {months[0]} .. {months[-1]}  ->  {prefix}_*.npz")

    lat, lon = make_grid(a.res)
    land = load_land_fraction(a.res)
    LAT, LON, annual_mean, seas_amp = tsim.static_fields(lat, lon, land)
    month_pos = np.array([m - 1 for _, m in months], dtype=float)            # 0 = mid-January
    season = -np.cos(2 * np.pi * month_pos / 12)

    # ---- temperature: modelled climatology + REAL anomaly
    anom = np.stack([upsample(anom2[gidx[ym]], a.res) for ym in months])     # [T, H, W]
    temp = (annual_mean[None] + seas_amp[None] * season[:, None, None] + anom).astype(np.float32)

    # ---- CO2: REAL Mauna Loa trend + modelled seasonal/spatial pattern
    ctx = {"scenario": "none", "warming": 0.0, "co2_trend": 0.0}
    rng = np.random.default_rng(a.seed)
    noise = svar.NoiseField(rng, LAT.shape, max(1.0, 15 / a.res), 0.8)
    co2_field = np.stack([svar.co2(LAT, LON, land, mp, s, 0.0, noise.step() * 0.5, ctx) - 420.0 + co2[ym]
                          for mp, s, ym in zip(month_pos, season, months)]).astype(np.float32)

    # ---- other variables: modelled, coupled to the REAL local temperature anomaly
    an = np.nan_to_num(anom)
    fields = {}
    for var, (fn, sig, rho) in svar.MODELS.items():
        if var == "co2":
            continue
        nz = svar.NoiseField(np.random.default_rng(a.seed + sum(map(ord, var))), LAT.shape, max(1.0, sig / a.res), rho)
        stack = np.stack([fn(LAT, LON, land, mp, s, 0.0, nz.step(), ctx) for mp, s in zip(month_pos, season)])
        if var == "aerosol_smoke":          # heat + dryness -> more and bigger fires
            stack *= np.exp(0.3 * np.clip(an, -2, 3))
        elif var == "lightning":            # warmer -> more convection (~+10 %/degC, exaggerated a bit)
            stack *= np.clip(1 + 0.15 * an, 0.4, 2.0)
        elif var == "precipitation":        # hot anomalies over land are usually dry
            stack *= np.clip(1 - 0.08 * an * land[None], 0.3, 1.6)
        elif var == "aerosol_dust":         # hot, dry months lift more dust
            stack *= np.clip(1 + 0.1 * an, 0.5, 1.8)
        fields[var] = stack.astype(np.float32)
    fields["surface_temperature"] = temp
    fields["co2"] = co2_field

    sources = {
        "surface_temperature": "NASA GISTEMP v4 anomaly (REAL) + modelled seasonal climatology",
        "co2": "NOAA Mauna Loa monthly CO2 (REAL trend) + modelled seasonal/spatial pattern",
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for var, data in fields.items():
        units, vmin, vmax, _ = svar.VARIABLES[var]
        intensity = np.clip((data - vmin) / (vmax - vmin), 0, 1).astype(np.float32)
        meta = {"variable": var, "units": units,
                "source": sources.get(var, "MODELLED seasonal field coupled to REAL GISTEMP anomaly"),
                "real": var in sources, "vmin": vmin, "vmax": vmax, "resolution_deg": a.res,
                "shape": list(data.shape), "lat_order": "north_to_south", "lon_range": [-180, 180],
                "time_start": str(times[0]), "time_end": str(times[-1]),
                "references": [GISTEMP_URL, CO2_URL]}
        name = f"{prefix}_{var}"
        extra = {"anomaly": anom.astype(np.float32)} if var == "surface_temperature" else {}
        np.savez_compressed(out / f"{name}.npz", data=data, intensity=intensity, lat=lat, lon=lon,
                            time=times, land_fraction=land.astype(np.float32), **extra)
        (out / f"{name}.json").write_text(json.dumps(meta, indent=2))
        print(f"  {var:20s} {np.nanmin(data):8.2f} .. {np.nanmax(data):8.2f} {units}")

    # quick sanity print: global mean anomaly per year
    w = np.cos(np.deg2rad(lat))[None, :, None] * np.ones_like(anom)
    w = np.where(np.isfinite(anom), w, 0)
    g = np.nansum(np.nan_to_num(anom) * w, axis=(1, 2)) / w.sum(axis=(1, 2))
    years = sorted({y for y, _ in months})
    print("global mean GISTEMP anomaly by year: " +
          ", ".join(f"{y}: {g[[i for i, (yy, _) in enumerate(months) if yy == y]].mean():+.2f}" for y in years))


if __name__ == "__main__":
    main()
