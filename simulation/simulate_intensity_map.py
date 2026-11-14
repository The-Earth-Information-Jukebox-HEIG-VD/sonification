#!/usr/bin/env python3
"""
Simulated global temperature "intensity map" for the Earth Information Jukebox.

Stand-in for the real data-extraction output, so the music side can be built
before the extraction is ready. It produces the same format the extraction
should produce (see INTENSITY_MAP_SPEC.md):

    data       float32 [T, H, W]   physical value (°C)
    intensity  float32 [T, H, W]   normalized 0..1 using fixed vmin/vmax
    lat        float32 [H]         cell-center latitude, +90 (north) -> -90
    lon        float32 [W]         cell-center longitude, -180 -> +180
    time       str     [T]         ISO timestamps
    + metadata JSON (variable, units, vmin, vmax, resolution...)

Physical model (simple, but realistic in shape):
  * latitude gradient: warm tropics (~28 °C), cold poles
  * land vs ocean: land has a much bigger seasonal swing and colder polar interiors
  * seasons: northern summer peaks in July, southern summer in January
  * ocean currents: warm Gulf Stream / Kuroshio, cold Humboldt / Benguela / California
  * weather noise: smooth random anomalies that drift over time
  * optional scenarios: El Niño warm tongue, global warming trend, heatwave

Usage:
  python simulate_intensity_map.py                       # 12 monthly frames, 1° grid
  python simulate_intensity_map.py --frames 48 --res 2   # smoother time, coarser grid
  python simulate_intensity_map.py --scenario elnino --warming 0.3 --years 3
  python simulate_intensity_map.py --preview             # also writes PNG frames + GIF

Only numpy is required; matplotlib + Pillow are needed for --preview.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MASK_FILE = HERE / "land_mask_025deg.npz"

# Fixed normalization range for temperature (°C). Fixed, not per-frame, so the
# same note always means the same temperature across frames and datasets.
VMIN, VMAX = -40.0, 40.0


# ----------------------------------------------------------------------------
# Grid + land mask
# ----------------------------------------------------------------------------
def make_grid(res: float):
    lat = np.arange(90 - res / 2, -90, -res, dtype=np.float32)
    lon = np.arange(-180 + res / 2, 180, res, dtype=np.float32)
    return lat, lon


def load_land_fraction(res: float) -> np.ndarray:
    """Land fraction (0..1) per cell at the requested resolution."""
    z = np.load(MASK_FILE)
    h, w = z["shape"]
    mask = np.unpackbits(z["mask"])[: h * w].reshape(h, w).astype(np.float32)
    f = res / 0.25
    if abs(f - round(f)) > 1e-6 or round(f) < 1:
        raise ValueError("res must be a multiple of 0.25 (e.g. 0.25, 0.5, 1, 2, 5)")
    f = int(round(f))
    if 180 % res or 360 % res:
        raise ValueError("res must divide 180 and 360 evenly")
    return mask.reshape(h // f, f, w // f, f).mean(axis=(1, 3))


def smooth(field: np.ndarray, sigma_cells: float) -> np.ndarray:
    """Gaussian blur via FFT (wraps in longitude, fine for a toy model)."""
    h, w = field.shape
    ky = np.fft.fftfreq(h)[:, None]
    kx = np.fft.fftfreq(w)[None, :]
    g = np.exp(-2 * (np.pi * sigma_cells) ** 2 * (kx**2 + ky**2))
    return np.real(np.fft.ifft2(np.fft.fft2(field) * g))


def gauss2d(LAT, LON, lat0, lon0, slat, slon, tilt=0.0):
    """Elliptical gaussian bump; tilt shifts lon center with latitude (°lon per °lat)."""
    dlon = (LON - (lon0 + tilt * (LAT - lat0)) + 180) % 360 - 180
    return np.exp(-0.5 * (((LAT - lat0) / slat) ** 2 + (dlon / slon) ** 2))


# ----------------------------------------------------------------------------
# Temperature model
# ----------------------------------------------------------------------------
def static_fields(lat, lon, land):
    LON, LAT = np.meshgrid(lon, lat)
    rlat = np.deg2rad(LAT)

    # Annual-mean temperature by latitude: ~28 °C at equator, ~-2 °C polar ocean
    base_ocean = -2 + 30 * np.cos(rlat) ** 2
    # Land: slightly warmer tropics, much colder high latitudes (Siberia, Greenland, Antarctica)
    base_land = base_ocean + 2 * np.cos(rlat) ** 4 - 22 * (np.abs(LAT) / 90) ** 2.2
    antarctic = np.clip((-LAT - 62) / 10, 0, 1) * 18  # extra cold ice sheet
    greenland = gauss2d(LAT, LON, 74, -41, 8, 14) * 12
    base_land = base_land - antarctic - greenland
    # Hot deserts (Sahara, Arabia, Australia interior)
    deserts = (gauss2d(LAT, LON, 22, 10, 7, 25) * 4
               + gauss2d(LAT, LON, 23, 47, 6, 10) * 4
               + gauss2d(LAT, LON, -24, 133, 7, 12) * 3)
    base_land = base_land + deserts

    # Ocean currents (ocean only)
    currents = (
        + 5 * gauss2d(LAT, LON, 45, -45, 9, 18, tilt=1.6)   # Gulf Stream / N. Atlantic Drift
        + 3 * gauss2d(LAT, LON, 36, 150, 6, 14)              # Kuroshio
        - 4 * gauss2d(LAT, LON, -18, -78, 12, 5)             # Humboldt (Peru)
        - 3 * gauss2d(LAT, LON, -22, 10, 10, 5)              # Benguela (Namibia)
        - 2.5 * gauss2d(LAT, LON, 32, -124, 9, 5)            # California
        + 1.5 * gauss2d(LAT, LON, 5, 140, 10, 30)            # W. Pacific warm pool
    )

    annual_mean = land * base_land + (1 - land) * (base_ocean + currents)

    # Seasonal amplitude: land swings a lot (continentality), ocean little
    s = np.sin(np.abs(rlat))
    land_amp = 3 + 20 * s ** 1.5
    land_amp = land_amp - 8 * gauss2d(LAT, LON, 50, 5, 10, 18)     # maritime W. Europe: mild seasons
    land_amp = land_amp + 10 * gauss2d(LAT, LON, 63, 125, 10, 30)  # E. Siberia: extreme continentality
    land_amp = land_amp + 5 * gauss2d(LAT, LON, 55, -100, 10, 25)  # Canadian interior
    land_amp = np.where(LAT < -65, np.minimum(land_amp, 14), land_amp)  # Antarctica
    # Southern-hemisphere land is small and ocean-surrounded -> milder seasons
    land_amp = np.where((LAT < 0) & (LAT > -65), 0.55 * land_amp, land_amp)
    amp = land * land_amp + (1 - land) * (0.5 + 5 * s)
    hemi = np.tanh(LAT / 8.0)  # smooth sign flip across the equator (no seam)
    return LAT, LON, annual_mean.astype(np.float32), (amp * hemi).astype(np.float32)


def simulate(res=1.0, frames=12, years=1.0, start="2024-01-15", scenario="none",
             warming=0.0, noise=1.0, seed=42):
    rng = np.random.default_rng(seed)
    lat, lon = make_grid(res)
    land = load_land_fraction(res)
    LAT, LON, annual_mean, seas_amp = static_fields(lat, lon, land)
    h, w = annual_mean.shape

    # Weather noise: AR(1) random field, smoothed to ~1500 km blobs
    sigma = max(1.0, 12.0 / res)
    noise_state = smooth(rng.standard_normal((h, w)), sigma)
    noise_state /= noise_state.std() + 1e-9
    rho = 0.85 ** (12 * years / frames)  # temporal persistence scaled to frame step

    t0 = datetime.fromisoformat(start)
    total_days = 365.25 * years
    data = np.empty((frames, h, w), np.float32)
    times = []
    for i in range(frames):
        frac = i / frames
        day = frac * total_days
        times.append((t0 + timedelta(days=day)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        # month position in [0,12): 0 = mid-Jan. NH peak in mid-July (~6.0)
        month = ((t0.month - 1) + day / 30.44) % 12
        season = -np.cos(2 * np.pi * month / 12)  # -1 in Jan, +1 in Jul
        # land lags less than ocean, but keep it simple: same phase
        T = annual_mean + seas_amp * season

        # Weather noise (bigger over land and high latitudes)
        fresh = smooth(rng.standard_normal((h, w)), sigma)
        fresh /= fresh.std() + 1e-9
        noise_state = rho * noise_state + np.sqrt(1 - rho**2) * fresh
        noise_scale = noise * (0.6 + 1.6 * land + 1.2 * np.abs(LAT) / 90)
        T = T + noise_scale * noise_state

        # Scenarios
        yrs = day / 365.25
        if warming:
            # Arctic amplification: poles warm ~2.5x faster than the tropics
            T = T + warming * yrs * (1 + 1.5 * (np.clip(LAT, 0, 90) / 90) ** 2)
        if scenario in ("elnino", "both"):
            # Warm tongue in the east-central equatorial Pacific, peaks mid-run
            strength = 3.0 * np.sin(np.pi * frac)
            T = T + (1 - land) * strength * gauss2d(LAT, LON, 0, -120, 6, 35)
        if scenario in ("heatwave", "both"):
            # European summer heatwave: Gaussian pulse in time around July
            pulse = np.exp(-0.5 * (((month - 6.3 + 6) % 12 - 6) / 0.6) ** 2)
            T = T + 8 * pulse * land * gauss2d(LAT, LON, 47, 5, 7, 12)

        data[i] = T

    intensity = np.clip((data - VMIN) / (VMAX - VMIN), 0, 1).astype(np.float32)
    meta = {
        "variable": "surface_temperature",
        "units": "degC",
        "source": "SIMULATED (simulate_intensity_map.py)",
        "scenario": scenario,
        "warming_degC_per_year": warming,
        "vmin": VMIN,
        "vmax": VMAX,
        "resolution_deg": res,
        "shape": [frames, h, w],
        "lat_order": "north_to_south",
        "lon_range": [-180, 180],
        "time_start": times[0],
        "time_end": times[-1],
        "seed": seed,
    }
    return data, intensity, lat, lon, np.array(times), land.astype(np.float32), meta


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------
def save(out_dir: Path, name: str, data, intensity, lat, lon, times, land, meta):
    out_dir.mkdir(parents=True, exist_ok=True)
    npz = out_dir / f"{name}.npz"
    np.savez_compressed(npz, data=data, intensity=intensity, lat=lat, lon=lon,
                        time=times, land_fraction=land)
    (out_dir / f"{name}.json").write_text(json.dumps(meta, indent=2))
    return npz


def preview(out_dir: Path, name: str, data, lat, lon, times):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image

    pdir = out_dir / f"{name}_frames"
    pdir.mkdir(exist_ok=True)
    paths = []
    ext = [lon[0], lon[-1], lat[-1], lat[0]]
    for i, T in enumerate(data):
        fig, ax = plt.subplots(figsize=(10, 5.4), dpi=100)
        im = ax.imshow(T, extent=ext, cmap="RdYlBu_r", vmin=VMIN, vmax=VMAX, aspect="auto")
        ax.set_title(f"SIMULATED surface temperature  {times[i][:10]}")
        ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
        fig.colorbar(im, ax=ax, label="°C", fraction=0.03, pad=0.02)
        fig.tight_layout()
        p = pdir / f"{name}.{i:04d}.png"
        fig.savefig(p); plt.close(fig)
        paths.append(p)
    imgs = [Image.open(p).convert("P", palette=Image.ADAPTIVE) for p in paths]
    gif = out_dir / f"{name}.gif"
    imgs[0].save(gif, save_all=True, append_images=imgs[1:], duration=400, loop=0)
    return gif


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--res", type=float, default=1.0, help="grid resolution in degrees (0.25, 0.5, 1, 2, 5...)")
    ap.add_argument("--frames", type=int, default=12, help="number of time steps")
    ap.add_argument("--years", type=float, default=1.0, help="time span covered by the frames")
    ap.add_argument("--start", default="2024-01-15", help="ISO date of first frame")
    ap.add_argument("--scenario", choices=["none", "elnino", "heatwave", "both"], default="none")
    ap.add_argument("--warming", type=float, default=0.0, help="°C per year global trend (exaggerate for demos, e.g. 0.5)")
    ap.add_argument("--noise", type=float, default=1.0, help="weather noise multiplier (0 = perfectly smooth)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=str(HERE / "output"))
    ap.add_argument("--name", default=None, help="output basename")
    ap.add_argument("--preview", action="store_true", help="write PNG frames + animated GIF")
    a = ap.parse_args()

    name = a.name or f"temperature_sim_{a.scenario}_{a.frames}f_{a.res:g}deg"
    data, inten, lat, lon, times, land, meta = simulate(
        a.res, a.frames, a.years, a.start, a.scenario, a.warming, a.noise, a.seed)
    out = Path(a.out)
    npz = save(out, name, data, inten, lat, lon, times, land, meta)
    wts = np.cos(np.deg2rad(lat))[None, :, None] * np.ones_like(data)
    gmean = float((data * wts).sum() / wts.sum())
    print(f"wrote {npz}  shape={data.shape}  T range {data.min():.1f}..{data.max():.1f} °C  "
          f"area-weighted global mean {gmean:.1f} °C")
    if a.preview:
        print(f"wrote {preview(out, name, data, lat, lon, times)}")


if __name__ == "__main__":
    main()
