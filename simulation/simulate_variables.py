#!/usr/bin/env python3
"""
Simulated intensity maps for the whole orchestra, all on the SAME time axis:

  surface_temperature  °C               (from simulate_intensity_map.py)
  precipitation        mm/day           ITCZ following the sun, monsoons, storm tracks, dry deserts
  co2                  ppm              global trend + NH seasonal "breathing" + industrial & fire sources
  aerosol_dust         AOD              Sahara plume across the Atlantic (summer), Asian spring dust
  aerosol_smoke        AOD              African / Amazon / boreal fire seasons (+ Mediterranean fires in heatwave)
  lightning            flashes/km²/mo   tropical land convection, Congo, Maracaibo, US plains, spiky

Each file follows simulation/INTENSITY_MAP_SPEC.md:
  output/<prefix>_<variable>.npz  +  .json      (prefix = sim_<scenario>_<frames>f_<res>deg)

Usage
  python simulation/simulate_variables.py                                  # all variables, 12 monthly frames
  python simulation/simulate_variables.py --scenario both --warming 0.5 --years 2 --frames 24 --preview
  python simulation/simulate_variables.py --variables precipitation,co2
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import simulate_intensity_map as tsim
from simulate_intensity_map import gauss2d, load_land_fraction, make_grid, smooth

HERE = Path(__file__).resolve().parent

VARIABLES = {
    #  name               units              vmin   vmax   preview colormap
    "surface_temperature": ("degC",           -40.0, 40.0, "RdYlBu_r"),
    "precipitation":       ("mm/day",           0.0, 20.0, "YlGnBu"),
    "co2":                 ("ppm",            400.0, 440.0, "magma"),
    "aerosol_dust":        ("AOD",              0.0,  1.2, "pink_r"),
    "aerosol_smoke":       ("AOD",              0.0,  1.2, "Oranges"),
    "lightning":           ("flashes/km2/month", 0.0, 10.0, "inferno"),
}


def month_bump(month, m0, width):
    """Cyclic gaussian in month space (peaks at month m0; 0 = mid-Jan)."""
    d = (month - m0 + 6) % 12 - 6
    return float(np.exp(-0.5 * (d / width) ** 2))


class NoiseField:
    """Smooth AR(1) random field, unit std."""

    def __init__(self, rng, shape, sigma, rho):
        self.rng, self.shape, self.sigma, self.rho = rng, shape, sigma, rho
        self.state = self._fresh()

    def _fresh(self):
        f = smooth(self.rng.standard_normal(self.shape), self.sigma)
        return f / (f.std() + 1e-9)

    def step(self):
        self.state = self.rho * self.state + np.sqrt(1 - self.rho**2) * self._fresh()
        return np.clip(self.state, -2.5, 2.5)  # no absurd log-normal tails


# ----------------------------------------------------------------------------
# Per-variable physics (toy but plausible), evaluated at one time step
# ----------------------------------------------------------------------------
def precipitation(LAT, LON, land, month, season, yrs, noise, ctx):
    # ITCZ: north in July, south in January; over land it swings further
    c = 3 + 7 * season + land * 4 * season
    P = 9 * np.exp(-(((LAT - c) / 7) ** 2))
    # Storm tracks, stronger in the winter hemisphere
    winter = 1 + 0.5 * (-season * np.tanh(LAT / 8))
    P += 2.5 * np.exp(-(((np.abs(LAT) - 48) / 10) ** 2)) * winter
    # Dry subtropics
    P *= 1 - 0.7 * np.exp(-(((np.abs(LAT) - 25) / 7) ** 2))
    # Monsoons (applied after the subtropical drying: they override it seasonally)
    P += 18 * gauss2d(LAT, LON, 20, 82, 7, 14) * max(season, 0) ** 1.5      # India / SE Asia
    P += 6 * gauss2d(LAT, LON, 10, 0, 5, 15) * max(season, 0)                # West Africa
    P += 7 * gauss2d(LAT, LON, -10, -60, 10, 14) * max(-season, 0)           # Amazon / S. America
    P += 6 * gauss2d(LAT, LON, -14, 135, 6, 15) * max(-season, 0)            # N. Australia
    P += 4 * gauss2d(LAT, LON, 0, 22, 8, 10)                                 # Congo, year-round
    # Deserts
    desert = (gauss2d(LAT, LON, 22, 10, 7, 25) + gauss2d(LAT, LON, 23, 47, 6, 10)
              + gauss2d(LAT, LON, -24, 133, 7, 12) + gauss2d(LAT, LON, -22, -70, 8, 4))
    P *= 1 - 0.85 * np.clip(desert, 0, 1)
    P *= np.cos(np.deg2rad(LAT)) ** 0.6
    if ctx["warming"]:
        P *= 1 + 0.03 * ctx["warming"] * yrs  # wetter wet regions (≈ Clausius-Clapeyron, exaggerated)
    # Intermittent weather: log-normal multiplicative noise
    return P * np.exp(0.8 * noise - 0.32)


def co2(LAT, LON, land, month, season, yrs, noise, ctx):
    C = 420 + ctx["co2_trend"] * yrs
    # NH biosphere breathing: max ~May, min ~Sep, bigger at high latitudes
    amp = 1 + 7 * np.clip(LAT / 60, 0, 1)
    C = C + amp * np.cos(2 * np.pi * (month - 3.8) / 12)
    # Industrial sources (smoothed plumes drifting east)
    src = (12 * gauss2d(LAT, LON, 32, 117, 7, 12) + 7 * gauss2d(LAT, LON, 50, 12, 6, 14)
           + 7 * gauss2d(LAT, LON, 40, -78, 6, 11) + 4 * gauss2d(LAT, LON, 24, 80, 6, 8))
    # Fires
    src += 6 * gauss2d(LAT, LON, -10, 22, 8, 12) * month_bump(month, 7.8, 1.3)
    src += 5 * gauss2d(LAT, LON, -9, -60, 7, 10) * month_bump(month, 8.7, 1.0)
    return C + src + 0.8 * noise


def aerosol_dust(LAT, LON, land, month, season, yrs, noise, ctx):
    D = 0.8 * gauss2d(LAT, LON, 21, 5, 6, 18) * (0.6 + 0.4 * season)        # Sahara
    D += 0.55 * gauss2d(LAT, LON, 16, -35, 5, 22) * max(season, 0) ** 1.2     # trans-Atlantic plume
    D += 0.6 * gauss2d(LAT, LON, 40, 100, 7, 18) * month_bump(month, 3.5, 1.2)  # Gobi / Taklamakan spring
    D += 0.4 * gauss2d(LAT, LON, 23, 48, 6, 10) * (0.5 + 0.5 * season)        # Arabia
    D += 0.15 * gauss2d(LAT, LON, -25, 135, 6, 10)                            # Australia
    return np.clip(D, 0, None) * np.exp(0.5 * noise - 0.125)


def aerosol_smoke(LAT, LON, land, month, season, yrs, noise, ctx):
    S = 1.1 * gauss2d(LAT, LON, -10, 22, 7, 12) * month_bump(month, 7.8, 1.3)   # Southern Africa
    S += 0.5 * gauss2d(LAT, LON, 9, 10, 5, 15) * month_bump(month, 0.5, 1.3)     # Sahel
    S += 0.6 * gauss2d(LAT, LON, -9, -60, 7, 10) * month_bump(month, 8.7, 1.0)   # Amazon
    boreal = gauss2d(LAT, LON, 62, 120, 6, 25) + gauss2d(LAT, LON, 58, -110, 6, 20)
    S += 0.5 * boreal * month_bump(month, 6.8, 1.0) * (1 + 0.15 * ctx["warming"] * yrs)
    S += 0.4 * gauss2d(LAT, LON, 18, 100, 5, 8) * month_bump(month, 2.8, 0.8)    # SE Asia
    if ctx["scenario"] in ("heatwave", "both"):
        S += 0.7 * gauss2d(LAT, LON, 41, 15, 5, 12) * month_bump(month, 6.5, 0.7)  # Mediterranean fires
    return np.clip(S, 0, None) * np.exp(0.6 * noise - 0.18)


def lightning(LAT, LON, land, month, season, yrs, noise, ctx):
    c_land = 5 + 12 * season
    c_ocean = 3 + 7 * season
    L = land * 6 * np.exp(-(((LAT - c_land) / 13) ** 2)) + (1 - land) * 0.6 * np.exp(-(((LAT - c_ocean) / 8) ** 2))
    L += land * 7 * gauss2d(LAT, LON, -1, 22, 6, 8)                           # Congo basin
    L += 6 * gauss2d(LAT, LON, 9.5, -71.5, 2.5, 2.5)                          # Lake Maracaibo
    L += land * 5 * gauss2d(LAT, LON, 27, 78, 4, 10) * month_bump(month, 4.5, 1.2)  # pre-monsoon India
    L += land * 5 * gauss2d(LAT, LON, 37, -95, 7, 12) * max(season, 0) ** 2   # US Great Plains
    L += land * 5 * gauss2d(LAT, LON, -30, -62, 6, 8) * max(-season, 0) ** 2  # Argentina
    L += land * 3 * gauss2d(LAT, LON, 45, 15, 6, 15) * max(season, 0) ** 2    # European summer storms
    if ctx["scenario"] in ("heatwave", "both"):
        L += land * 4 * gauss2d(LAT, LON, 47, 5, 6, 12) * month_bump(month, 6.4, 0.6)
    L *= 1 + 0.05 * ctx["warming"] * yrs
    return np.clip(L, 0, None) * np.exp(1.1 * noise - 0.6)  # very spiky


MODELS = {
    "precipitation": (precipitation, 6.0, 0.4),   # (function, noise sigma in degrees, persistence)
    "co2":           (co2,           15.0, 0.8),
    "aerosol_dust":  (aerosol_dust,   8.0, 0.6),
    "aerosol_smoke": (aerosol_smoke,  6.0, 0.6),
    "lightning":     (lightning,      3.0, 0.0),
}


def simulate_variable(var, res, frames, years, start, scenario, warming, co2_trend, seed):
    rng = np.random.default_rng(seed + sum(map(ord, var)))  # stable per variable (hash() is randomized)
    lat, lon = make_grid(res)
    land = load_land_fraction(res)
    LON, LAT = np.meshgrid(lon, lat)
    fn, sig_deg, rho = MODELS[var]
    noise = NoiseField(rng, LAT.shape, max(1.0, sig_deg / res), rho ** (12 * years / frames))
    ctx = {"scenario": scenario, "warming": warming, "co2_trend": co2_trend}
    t0 = datetime.fromisoformat(start)
    total_days = 365.25 * years
    data = np.empty((frames, *LAT.shape), np.float32)
    times = []
    for i in range(frames):
        day = i / frames * total_days
        times.append((t0 + timedelta(days=day)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        month = ((t0.month - 1) + day / 30.44) % 12
        season = -np.cos(2 * np.pi * month / 12)
        data[i] = fn(LAT, LON, land, month, season, day / 365.25, noise.step(), ctx)
    return data, lat, lon, np.array(times), land.astype(np.float32)


def package(var, data, lat, lon, times, land, args):
    units, vmin, vmax, _ = VARIABLES[var]
    intensity = np.clip((data - vmin) / (vmax - vmin), 0, 1).astype(np.float32)
    meta = {
        "variable": var, "units": units, "source": "SIMULATED (simulate_variables.py)",
        "scenario": args.scenario, "warming_degC_per_year": args.warming,
        "vmin": vmin, "vmax": vmax, "resolution_deg": args.res, "shape": list(data.shape),
        "lat_order": "north_to_south", "lon_range": [-180, 180],
        "time_start": str(times[0]), "time_end": str(times[-1]), "seed": args.seed,
    }
    return intensity, meta


def preview_gif(out, name, var, data, lat, lon, times):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image
    units, vmin, vmax, cmap = VARIABLES[var]
    imgs = []
    for i, f in enumerate(data):
        fig, ax = plt.subplots(figsize=(8, 4.3), dpi=90)
        im = ax.imshow(f, extent=[lon[0], lon[-1], lat[-1], lat[0]], cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
        ax.set_title(f"SIMULATED {var}  {str(times[i])[:10]}")
        fig.colorbar(im, ax=ax, label=units, fraction=0.03, pad=0.02)
        fig.tight_layout()
        fig.canvas.draw()
        imgs.append(Image.frombuffer("RGBA", fig.canvas.get_width_height(),
                                     fig.canvas.buffer_rgba()).convert("P", palette=Image.ADAPTIVE))
        plt.close(fig)
    gif = out / f"{name}.gif"
    imgs[0].save(gif, save_all=True, append_images=imgs[1:], duration=400, loop=0)
    return gif


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variables", default="all", help="comma list or 'all': " + ",".join(VARIABLES))
    ap.add_argument("--res", type=float, default=1.0)
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--years", type=float, default=1.0)
    ap.add_argument("--start", default="2024-01-15")
    ap.add_argument("--scenario", choices=["none", "elnino", "heatwave", "both"], default="none")
    ap.add_argument("--warming", type=float, default=0.0, help="°C/yr; also intensifies rain, fires, lightning")
    ap.add_argument("--co2-trend", type=float, default=2.4, help="ppm per year")
    ap.add_argument("--noise", type=float, default=1.0, help="temperature weather noise multiplier")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=str(HERE / "output"))
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()

    vars_ = list(VARIABLES) if a.variables == "all" else [v.strip() for v in a.variables.split(",")]
    prefix = a.prefix or f"sim_{a.scenario}_{a.frames}f_{a.res:g}deg"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for var in vars_:
        if var == "surface_temperature":
            data, _, lat, lon, times, land, _ = tsim.simulate(
                a.res, a.frames, a.years, a.start, a.scenario, a.warming, a.noise, a.seed)
        else:
            data, lat, lon, times, land = simulate_variable(
                var, a.res, a.frames, a.years, a.start, a.scenario, a.warming, a.co2_trend, a.seed)
        intensity, meta = package(var, data, lat, lon, times, land, a)
        name = f"{prefix}_{var}"
        np.savez_compressed(out / f"{name}.npz", data=data, intensity=intensity, lat=lat, lon=lon,
                            time=times, land_fraction=land)
        (out / f"{name}.json").write_text(json.dumps(meta, indent=2))
        print(f"{var:20s} {data.min():8.2f} .. {data.max():8.2f} {meta['units']:18s} "
              f"mean intensity {intensity.mean():.2f}  -> {name}.npz")
        if a.preview:
            preview_gif(out, name, var, data, lat, lon, times)


if __name__ == "__main__":
    main()
