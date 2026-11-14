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


def test_monthly_npy_precipitation_and_co2(tmp_path):
    """The datasets repo: precipitation_<YYYY>_<MM>.npy (720 x 1440, mm/h) and co2_<YYYY>_<MM>.npy (356 x 720, 89N..89S)."""
    psrc, csrc, out = tmp_path / "p", tmp_path / "c", tmp_path / "out"
    psrc.mkdir(), csrc.mkdir()
    for m in ("2003-01", "2003-02", "2003-03"):
        p = np.zeros((720, 1440), np.float16)
        p[:360] = 0.5                                                       # mm/h in the north
        np.save(psrc / f"precipitation_{m.replace('-', '_')}.npy", p)
    for m in ("2003-02", "2003-03", "2003-04"):
        c = np.full((356, 720), 380.0, np.float32)
        c[:178] = 400.0                                                     # north: 400 ppm
        np.save(csrc / f"co2_{m.replace('-', '_')}.npy", c)
    r = run("--precip", psrc, "--co2", csrc, "--out", out, "--res", 2, "--prefix", "team")
    assert r.returncode == 0, r.stderr
    p, c = np.load(out / "team_precipitation.npz"), np.load(out / "team_co2.npz")
    assert [str(t)[:7] for t in c["time"]] == ["2003-02", "2003-03"] == [str(t)[:7] for t in p["time"]]
    assert np.allclose(p["data"][0, :45], 12.0, atol=0.05) and np.all(p["data"][0, 45:] == 0)   # mm/h * 24
    assert c["data"].shape == (2, 90, 180)
    assert np.allclose(c["data"][0, 1:45], 400) and np.allclose(c["data"][0, 45:89], 380)
    assert np.allclose(c["data"][0, 0], 400) and np.allclose(c["data"][0, 89], 380)   # half-covered polar rows
    assert json.loads((out / "team_co2.json").read_text())["vmin"] == 365


def fake_precip_monthly(folder: Path, months: list[str]):
    """The datasets repository's layout: precipitation_<YYYY>_<MM>.npy, 720 x 1440 float16, mm/h.
    1 mm/h north of the equator (+ 0.1 per month number), 0 south of it, like fake_year()."""
    for m in months:
        a = np.zeros((720, 1440), np.float16)
        a[:360] = 1.0 + 0.1 * int(m[5:7])
        np.save(folder / f"precipitation_{m.replace('-', '_')}.npy", a)


def test_precipitation_one_file_per_month(tmp_path):
    """Same data in the two layouts (one .npz per year / one .npy per month) gives the same dataset."""
    yearly, monthly, out = tmp_path / "yearly", tmp_path / "monthly", tmp_path / "out"
    yearly.mkdir(), monthly.mkdir()
    fake_year(yearly, 2000, [11, 12])
    fake_year(yearly, 2001, [1, 2, 3])
    fake_precip_monthly(monthly, ["2000-11", "2000-12", "2001-01", "2001-02", "2001-03"])
    assert run("--precip", yearly, "--out", out / "y", "--res", 2).returncode == 0
    r = run("--precip", monthly, "--out", out / "m", "--res", 2)
    assert r.returncode == 0, r.stderr
    y = np.load(out / "y" / "real_monthly_2000_2001_precipitation.npz")
    m = np.load(out / "m" / "real_monthly_2000_2001_precipitation.npz")
    assert list(y["time"]) == list(m["time"]) and m["data"].shape == (5, 90, 180)
    assert np.allclose(y["data"], m["data"], rtol=1e-4, atol=1e-4)
    assert np.allclose(m["data"][0, :45], (1.0 + 0.1 * 11) * 24, rtol=1e-3)          # mm/h -> mm/day
    meta = json.loads((out / "m" / "real_monthly_2000_2001_precipitation.json").read_text())
    assert meta["units"] == "mm/day" and meta["real"] is True
    # an empty folder is refused with both layouts named
    (tmp_path / "empty").mkdir()
    r = run("--precip", tmp_path / "empty", "--out", out / "e")
    assert r.returncode != 0 and "precipitation_monthly_*.npz" in (r.stderr + r.stdout)


def fake_lightning(folder: Path, months: list[str]):
    """lightning_<YYYY>_<MM>.npy like the team's: 720 x 1440 float16, share of the pixel with lightning (0-1).
    0.2 everywhere between 30S and 30N, and 0 elsewhere, INCLUDING beyond 55 deg where nothing is observed."""
    for m in months:
        a = np.zeros((720, 1440), np.float16)
        a[240:480] = 0.2
        np.save(folder / f"lightning_{m.replace('-', '_')}.npy", a)


def test_lightning(tmp_path):
    src, out = tmp_path / "l", tmp_path / "out"
    src.mkdir()
    fake_lightning(src, ["2017-03", "2017-04"])
    r = run("--lightning", src, "--out", out, "--res", 1)
    assert r.returncode == 0, r.stderr
    z = np.load(out / "real_monthly_2017_2017_lightning.npz")
    meta = json.loads((out / "real_monthly_2017_2017_lightning.json").read_text())
    d, lat = z["data"], z["lat"]
    assert d.shape == (2, 180, 360) and meta["vmax"] == 0.4 and meta["real"] is True and "share" in meta["units"]
    assert np.allclose(d[0][np.abs(lat) < 30], 0.2, atol=1e-3)                 # the tropics
    assert np.all(d[0][(np.abs(lat) > 30) & (np.abs(lat) < 55)] == 0)          # observed, no lightning
    assert np.isnan(d[0][np.abs(lat) > 55]).all()                              # not observed: no data, not zero
    assert np.allclose(z["intensity"][0][np.abs(lat) < 30], 0.5, atol=2e-3)    # 0.2 / 0.4


def fake_co2(folder: Path, months: list[str], lines: bool = True):
    """co2_<YYYY>_<MM>.npy on the team grid (720 x 1440 float32, ppm): a smooth field (400 ppm at the south pole
    to 404 at the north pole, + 0.2 ppm per month), with the outline of the source picture left in it when
    `lines`: 2-pixel-wide lines at 432 ppm with a blurred edge at 415, every 5 deg of longitude over the
    northern hemisphere (dense, like a continent full of borders), and one line of latitude in the south."""
    lat = 90 - 0.125 - 0.25 * np.arange(720)
    for i, m in enumerate(months):
        a = np.repeat((402 + lat / 45 + 0.2 * i)[:, None], 1440, axis=1).astype(np.float32)
        if lines:
            for c in range(10, 1440, 20):
                a[:360, c - 1], a[:360, c + 2] = 415.0, 415.0
                a[:360, c: c + 2] = 432.0
            a[500:502] = 432.0
        np.save(folder / f"co2_{m.replace('-', '_')}.npy", a)


def test_co2_outline_pixels_are_dropped(tmp_path):
    """Lines of values near the top of the scale (the outline of the source picture) must not reach the dataset."""
    dirty, clean, out = tmp_path / "dirty", tmp_path / "clean", tmp_path / "out"
    dirty.mkdir(), clean.mkdir()
    fake_co2(dirty, ["2010-01", "2010-02"])
    fake_co2(clean, ["2010-01", "2010-02"], lines=False)
    assert run("--co2", dirty, "--out", out / "d", "--res", 1).returncode == 0
    r = run("--co2", clean, "--out", out / "c", "--res", 1)
    assert r.returncode == 0, r.stderr
    d = np.load(out / "d" / "real_monthly_2010_2010_co2.npz")["data"]
    c = np.load(out / "c" / "real_monthly_2010_2010_co2.npz")["data"]
    md = json.loads((out / "d" / "real_monthly_2010_2010_co2.json").read_text())
    mc = json.loads((out / "c" / "real_monthly_2010_2010_co2.json").read_text())
    assert np.isfinite(c).all() and mc["dropped_outline_pixels"]["share_mean_min_max"] == [0.0, 0.0, 0.0]   # clean data is untouched
    assert 0.08 < md["dropped_outline_pixels"]["share_mean_min_max"][0] < 0.25      # 20% of the north + one line in the south
    assert np.nanmax(d) < 406 and np.nanmax(np.abs(d - c)) < 0.3                    # what is left is the smooth field
    assert np.isfinite(d).mean() > 0.85                 # a 1 deg cell is lost only where a line covers all of it
    assert "outline" in md["source"]


def test_drop_overlay_lines_keeps_real_gradients(tmp_path):
    """A real, smooth difference between regions (here 8 ppm between the hemispheres) is not a line."""
    sys.path.insert(0, str(HERE))
    from build_monthly_dataset import drop_overlay_lines
    a = np.full((720, 1440), 400.0, np.float32)
    a[:360] = 408.0                                                                 # the block edges follow the equator
    a[100:102, :] = 430.0                                                           # one line
    a[300, 700] = np.nan
    cleaned, n = drop_overlay_lines(a, 0.25)
    assert n == 4 * 1440 and np.isnan(cleaned[99:103]).all()                        # the line and its two edges
    assert np.nanmin(cleaned[:360]) == 408 and np.nanmax(cleaned[360:]) == 400      # both hemispheres intact
    assert np.isnan(cleaned[300, 700]) and a[100, 0] == 430                         # NaN stays NaN; the input is not modified


def fake_split_precipitation(rain_dir: Path, snow_dir: Path, months: list[str]):
    """The team's precipitation split in two folders: rain_<YYYY>_<MM>.npy and snow_<YYYY>_<MM>.npy (mm/h).
    Rain 1 mm/h south of 45N; snow 0.05 mm/h north of 45N."""
    for m in months:
        rain, snow = np.zeros((720, 1440), np.float16), np.zeros((720, 1440), np.float16)
        rain[180:] = 1.0
        snow[:180] = 0.05
        np.save(rain_dir / f"rain_{m.replace('-', '_')}.npy", rain)
        np.save(snow_dir / f"snow_{m.replace('-', '_')}.npy", snow)


def fake_nightlights(folder: Path, years: list[int]):
    """nightlights_<YYYY>.npy: ONE MAP PER YEAR, 720 x 1440 float32, brightness 0-1. The northern hemisphere gets
    brighter every year (0.02, 0.03, ...); the south stays dark."""
    for i, y in enumerate(years):
        a = np.zeros((720, 1440), np.float32)
        a[:360] = 0.02 + 0.01 * i
        np.save(folder / f"nightlights_{y}.npy", a)


def test_rain_and_snow(tmp_path):
    rain, snow, out = tmp_path / "rain", tmp_path / "snow", tmp_path / "out"
    rain.mkdir(), snow.mkdir()
    fake_split_precipitation(rain, snow, ["2001-01", "2001-02"])
    r = run("--precip", rain, "--snow", snow, "--out", out, "--res", 2)
    assert r.returncode == 0, r.stderr
    p, s = np.load(out / "real_monthly_2001_2001_precipitation.npz"), np.load(out / "real_monthly_2001_2001_snowfall.npz")
    meta = json.loads((out / "real_monthly_2001_2001_snowfall.json").read_text())
    lat = p["lat"]
    assert np.allclose(p["data"][0][lat < 44], 24.0, rtol=1e-3) and np.all(p["data"][0][lat > 46] == 0)      # mm/h -> mm/day
    assert np.allclose(s["data"][0][lat > 46], 1.2, rtol=1e-2) and np.all(s["data"][0][lat < 44] == 0)
    assert meta["variable"] == "snowfall" and meta["units"] == "mm/day" and meta["vmax"] == 3 and meta["real"] is True


def test_yearly_maps_are_held_for_their_twelve_months(tmp_path):
    """One map per year -> the monthly time axis of every other dataset, nothing invented between two years."""
    src, out = tmp_path / "nl", tmp_path / "out"
    src.mkdir()
    fake_nightlights(src, [2012, 2013, 2014])
    r = run("--nightlights", src, "--out", out, "--res", 2)
    assert r.returncode == 0, r.stderr
    z = np.load(out / "real_monthly_2012_2014_nightlights.npz")
    meta = json.loads((out / "real_monthly_2012_2014_nightlights.json").read_text())
    months = [str(x)[:7] for x in z["time"]]
    assert z["data"].shape == (36, 90, 180) and months[0] == "2012-01" and months[11] == "2012-12" and months[-1] == "2014-12"
    assert meta["source_time_step"] == "1 year" and meta["time_step"] == "1 month" and meta["vmax"] == 0.1
    north = z["data"][:, 10, 10]
    assert np.allclose(north[:12], 0.02) and np.allclose(north[12:24], 0.03) and np.allclose(north[24:], 0.04)
    assert np.all(z["data"][:, 80] == 0)
    # a window in months keeps the years it touches, cut to the months asked for
    r = run("--nightlights", src, "--out", out, "--res", 2, "--start", "2012-11", "--end", "2013-02", "--prefix", "cut")
    assert r.returncode == 0, r.stderr
    z = np.load(out / "cut_nightlights.npz")
    assert [str(x)[:7] for x in z["time"]] == ["2012-11", "2012-12", "2013-01", "2013-02"]
    assert np.allclose(z["data"][:, 10, 10], [0.02, 0.02, 0.03, 0.03])
    # a missing year is refused, like a missing month
    (src / "nightlights_2013.npy").unlink()
    r = run("--nightlights", src, "--out", out, "--res", 2, "--prefix", "gap")
    assert r.returncode != 0 and "2012 -> 2014" in (r.stderr + r.stdout)

