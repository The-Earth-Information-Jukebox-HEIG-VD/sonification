"""Converter test with small made-up input files:  python -m pytest simulation/test_build_monthly_dataset.py -q"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def fake_year(folder: Path, year: int, months: list[int], n_days=None):
    """A yearly file like the extraction team's: 0.25 deg grid, 1 mm/h north of the equator, 0 south of it."""
    lat = (90 - 0.125 - 0.25 * np.arange(720)).astype(np.float32)
    lon = (-180 + 0.125 + 0.25 * np.arange(1440)).astype(np.float32)
    area = (np.cos(np.deg2rad(lat))[:, None] * np.ones((1, 1440))).astype(np.float32)
    p = np.zeros((len(months), 720, 1440), np.float16)
    for i, m in enumerate(months):
        p[i, :360] = 1.0 + 0.1 * m                        # mm/h: north only, a little more each month
    np.savez_compressed(folder / f"precipitation_monthly_{year}.npz",
                        months=np.array([f"{year}-{m:02d}" for m in months]), precip_mmh=p,
                        snow_days=np.zeros((len(months), 720, 1440), np.uint8),
                        n_days=np.array(n_days or [30] * len(months), np.uint8), lat=lat, lon=lon, cell_area_km2=area)


def run(*args):
    return subprocess.run([sys.executable, str(HERE / "build_monthly_dataset.py"), *map(str, args)],
                          capture_output=True, text=True)


def test_build_monthly_dataset(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    fake_year(src, 2000, [11, 12])
    fake_year(src, 2001, [1, 2, 3], n_days=[31, 28, 9])               # the last month is half-finished
    r = run("--precip", src, "--out", out, "--res", 2)
    assert r.returncode == 0, r.stderr
    z = np.load(out / "real_monthly_2000_2001_precipitation.npz")
    meta = json.loads((out / "real_monthly_2000_2001_precipitation.json").read_text())
    assert z["data"].shape == (5, 90, 180) and z["data"].dtype == np.float32
    assert [str(t)[:7] for t in z["time"]] == ["2000-11", "2000-12", "2001-01", "2001-02", "2001-03"]
    assert z["lat"][0] == 89.0 and z["lat"][-1] == -89.0 and z["lon"][0] == -179.0          # north -> south, 180W first
    assert np.allclose(z["data"][0, :45], (1.0 + 0.1 * 11) * 24, rtol=1e-3)                  # mm/h -> mm/day
    assert np.all(z["data"][:, 45:] == 0)                                                    # the south stays dry
    assert np.allclose(z["intensity"], np.clip(z["data"] / 20, 0, 1), atol=2e-3)
    assert meta["variable"] == "precipitation" and meta["real"] is True and meta["units"] == "mm/day"
    assert meta["partial_months"] == {"2001-03": 9} and list(z["n_days"]) == [30, 30, 31, 28, 9]
    # --min-days drops the half-finished month at the end; --start trims the beginning
    r = run("--precip", src, "--out", out, "--res", 2, "--min-days", 20, "--start", "2000-12", "--prefix", "trimmed")
    assert r.returncode == 0, r.stderr
    z = np.load(out / "trimmed_precipitation.npz")
    assert [str(t)[:7] for t in z["time"]] == ["2000-12", "2001-01", "2001-02"]


def test_missing_month_is_refused(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    fake_year(src, 2000, [1, 2, 4])                                    # March is missing
    r = run("--precip", src, "--out", tmp_path / "out", "--res", 2)
    assert r.returncode != 0 and "2000-02 -> 2000-04" in (r.stderr + r.stdout)


def fake_temperature(folder: Path, months: list[str]):
    """Monthly .npy files like the team's temperature_anomaly_common_grid_npy: 720 x 1440 float16, NaN = no data.
    North +1 degC, south -1 degC, the last row band (Antarctica) without data, + 0.1 degC per month."""
    for i, m in enumerate(months):
        a = np.full((720, 1440), -1.0, np.float16)
        a[:360] = 1.0
        a += np.float16(0.1 * i)
        a[704:] = np.nan
        np.save(folder / f"temperature_anomaly_{m.replace('-', '_')}.npy", a)


def test_temperature_anomaly(tmp_path):
    src, out = tmp_path / "temp", tmp_path / "out"
    src.mkdir()
    fake_temperature(src, ["1880-11", "1880-12", "1881-01", "1881-02"])
    r = run("--temperature", src, "--out", out, "--res", 2, "--start", "1880-12", "--frames-per-bar", 12)
    assert r.returncode == 0, r.stderr
    z = np.load(out / "real_monthly_1880_1881_surface_temperature.npz")
    meta = json.loads((out / "real_monthly_1880_1881_surface_temperature.json").read_text())
    assert z["data"].shape == (3, 90, 180) and [str(t)[:7] for t in z["time"]] == ["1880-12", "1881-01", "1881-02"]
    assert np.allclose(z["data"][0, :45], 1.1, atol=1e-2) and np.allclose(z["data"][0, 45:88], -0.9, atol=1e-2)
    assert np.isnan(z["data"][:, 88:]).all()                     # no data stays no data (not 0)
    assert meta["anomaly"] is True and meta["vmin"] == -5 and meta["frames_per_bar"] == 12 and meta["real"] is True
    assert list(z["n_days"]) == [31, 31, 28]


def test_common_months(tmp_path):
    """Precipitation 2000-11 .. 2001-03 + temperature 2001-01 .. 2001-06: one dataset with the 3 shared months."""
    psrc, tsrc, out = tmp_path / "p", tmp_path / "t", tmp_path / "out"
    psrc.mkdir(), tsrc.mkdir()
    fake_year(psrc, 2000, [11, 12])
    fake_year(psrc, 2001, [1, 2, 3])
    fake_temperature(tsrc, [f"2001-{m:02d}" for m in range(1, 7)])
    r = run("--precip", psrc, "--temperature", tsrc, "--out", out, "--res", 2)
    assert r.returncode == 0, r.stderr
    for var in ("precipitation", "surface_temperature"):
        z = np.load(out / f"real_monthly_2001_2001_{var}.npz")
        assert [str(t)[:7] for t in z["time"]] == ["2001-01", "2001-02", "2001-03"]
