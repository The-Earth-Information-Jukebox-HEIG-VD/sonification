"""The start-up data preparation of the Docker image, with small made-up raw folders:
       python -m pytest backend/test_prepare_data.py -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "simulation"))
from test_build_monthly_dataset import fake_precip_monthly, fake_temperature, fake_year  # noqa: E402


def prepare(raw, out, *extra):
    r = subprocess.run([sys.executable, str(ROOT / "backend" / "prepare_data.py"), "--raw", str(raw), "--out", str(out),
                        *extra], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def raw_like_the_datasets_repo(tmp_path):
    """<raw>/precipitation_x/precipitation_<YYYY>_<MM>.npy  +  <raw>/temperature_y/temperature_anomaly_<YYYY>_<MM>.npy"""
    raw = tmp_path / "data"
    (raw / "precipitation_2000_2001").mkdir(parents=True)
    (raw / "temperature_anomaly_2000_2001").mkdir()
    fake_precip_monthly(raw / "precipitation_2000_2001", ["2000-11", "2000-12", "2001-01", "2001-02", "2001-03"])
    fake_temperature(raw / "temperature_anomaly_2000_2001", ["2000-12", "2001-01", "2001-02", "2001-03", "2001-04"])
    return raw


def test_prepare_builds_once_and_only_what_changed(tmp_path):
    raw, out = raw_like_the_datasets_repo(tmp_path), tmp_path / "built"
    log = prepare(raw, out)
    assert "3 dataset(s) built" in log
    names = sorted(p.name for p in out.glob("*.npz"))
    # both recipes cover 2000-2001, and still nothing is overwritten: each has its own prefix
    assert names == ["real_monthly_2000_2001_precipitation.npz", "real_monthly_2000_2001_surface_temperature.npz",
                     "real_precipitation_2000_2001_precipitation.npz", "real_temperature_2000_2001_surface_temperature.npz"]
    assert all((out / n).with_suffix(".json").exists() for n in names)
    assert not (out / ".preparing").exists() and not list(out.glob(".building-*"))
    hist = json.loads((out / "real_temperature_2000_2001_surface_temperature.json").read_text())
    assert hist["anomaly"] is True and hist["shape"][0] == 5
    assert "frames_per_bar" not in hist                            # one bar = one month, like every other dataset
    both = json.loads((out / "real_monthly_2000_2001_precipitation.json").read_text())
    assert both["shape"][0] == 4                                   # the common months: 2000-12 .. 2001-03

    # nothing changed -> nothing is rebuilt
    stamp = {p.name: p.stat().st_mtime_ns for p in out.glob("*.npz")}
    assert "0 dataset(s) built" in prepare(raw, out)
    assert stamp == {p.name: p.stat().st_mtime_ns for p in out.glob("*.npz")}

    # a new year of temperature arrives -> the two datasets that use temperature are rebuilt, precipitation is not,
    # and the file whose name contained the old year range is removed
    fake_temperature(raw / "temperature_anomaly_2000_2001", ["2002-01"])
    (raw / "temperature_anomaly_2000_2001" / "temperature_anomaly_2001_05.npy").write_bytes(
        (raw / "temperature_anomaly_2000_2001" / "temperature_anomaly_2001_04.npy").read_bytes())
    for m in range(6, 13):
        (raw / "temperature_anomaly_2000_2001" / f"temperature_anomaly_2001_{m:02d}.npy").write_bytes(
            (raw / "temperature_anomaly_2000_2001" / "temperature_anomaly_2001_04.npy").read_bytes())
    log = prepare(raw, out)
    assert "2 dataset(s) built" in log and "up to date: 'precipitation'" in log
    names = sorted(p.name for p in out.glob("*.npz"))
    assert "real_temperature_2000_2002_surface_temperature.npz" in names
    assert "real_temperature_2000_2001_surface_temperature.npz" not in names
    assert stamp["real_precipitation_2000_2001_precipitation.npz"] == \
        (out / "real_precipitation_2000_2001_precipitation.npz").stat().st_mtime_ns


def test_no_raw_data_is_not_an_error(tmp_path):
    (tmp_path / "data").mkdir()
    log = prepare(tmp_path / "data", tmp_path / "built")
    assert "nothing to do" in log and not (tmp_path / "built").exists()


def test_api_serves_the_prepared_datasets(tmp_path):
    """Like the deployment: the data folder holds only RAW folders (read-only), the datasets come from the built folder."""
    raw, cache = raw_like_the_datasets_repo(tmp_path), tmp_path / "cache"
    prepare(raw, cache / "_datasets")
    code = """
import json
from fastapi.testclient import TestClient
from backend.app import app
c = TestClient(app)
h = c.get("/api/health").json()
d = c.get("/api/datasets").json()["datasets"]
pts = [{"lat": 46.5, "lng": 6.6}]
j = c.post("/api/sonify", json={"zone_id": "x", "dataset_id": "precipitation", "points": pts,
                                 "time_period": "2001-01", "embed_audio": False, "duration_s": 6})
t = c.post("/api/sonify", json={"zone_id": "x", "dataset_id": "temperature", "points": pts,
                                 "time_period": "2001-04", "embed_audio": False, "duration_s": 6})
m = c.post("/api/sonify", json={"zone_id": "x", "dataset_id": "temperature", "points": pts, "embed_audio": False,
                                 "time_period": "2000-12", "duration_s": 6, "options": {"dataset": "real_temperature_2000_2001"}})
y = c.post("/api/sonify", json={"zone_id": "x", "dataset_id": "temperature", "points": pts, "embed_audio": False,
                                 "time_period": "2000-12", "duration_s": 6,
                                 "options": {"dataset": "real_temperature_2000_2001", "frames_per_bar": 2}})
blocked = c.get("/api/files/_datasets/real_precipitation_2000_2001_precipitation.npz").status_code
print(json.dumps({"health": h, "ids": sorted(x["id"] for x in d), "status": [j.status_code, t.status_code],
                  "precip": j.json(), "temp": t.json(), "blocked": blocked,
                  "monthly_next": m.json().get("next_time_period"), "faster_next": y.json().get("next_time_period")}))
"""
    env = {**os.environ, "JUKEBOX_DATA_DIR": str(raw), "JUKEBOX_CACHE_DIR": str(cache)}
    env.pop("JUKEBOX_BUILT_DIR", None)
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["health"]["datasets"] == 3 and res["health"]["preparing_data"] is False
    assert res["ids"] == ["real_monthly_2000_2001", "real_precipitation_2000_2001", "real_temperature_2000_2001"]
    assert res["status"] == [200, 200], res
    assert res["precip"]["real_data"] and res["precip"]["units"] == "mm/day" and res["precip"]["time_period"] == "2001-01"
    # April 2001 exists only in the temperature history
    assert res["temp"]["source_dataset"] == "real_temperature_2000_2001" and res["temp"]["time_period"].startswith("2001")
    assert res["blocked"] == 404                                   # the built datasets are not downloadable as "files"
    # the temperature history is monthly like the rest: 6 s = 2 bars = 2 months, and the frontend can ask for
    # a faster time scale itself (2 months per bar -> 4 months)
    assert res["monthly_next"] == "2001-02" and res["faster_next"] == "2001-04"


def test_yearly_precipitation_files_still_work(tmp_path):
    """The older layout (one .npz per year, as in data_processing) is recognised too."""
    raw = tmp_path / "data"
    (raw / "precipitation").mkdir(parents=True)
    fake_year(raw / "precipitation", 2000, [11, 12])
    fake_year(raw / "precipitation", 2001, [1, 2, 3])
    log = prepare(raw, tmp_path / "built")
    assert "1 dataset(s) built" in log and "skip 'temperature history'" in log
    assert (tmp_path / "built" / "real_precipitation_2000_2001_precipitation.npz").exists()

