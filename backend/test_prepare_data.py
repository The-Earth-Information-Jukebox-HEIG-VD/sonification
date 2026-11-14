"""The start-up data preparation of the Docker image and the merge of datasets by date, with small made-up
raw folders:
       python -m pytest backend/test_prepare_data.py -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "simulation"))
sys.path.insert(0, str(ROOT / "music"))
from test_build_monthly_dataset import fake_lightning, fake_precip_monthly, fake_temperature, fake_year  # noqa: E402


def prepare(raw, out, *extra):
    r = subprocess.run([sys.executable, str(ROOT / "backend" / "prepare_data.py"), "--raw", str(raw), "--out", str(out),
                        *extra], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def raw_like_the_datasets_repo(tmp_path):
    """<raw>/precipitation_x/precipitation_<YYYY>_<MM>.npy   2000-11 .. 2001-03
       <raw>/temperature_y/temperature_anomaly_<YYYY>_<MM>.npy 2000-12 .. 2001-04"""
    raw = tmp_path / "data"
    (raw / "precipitation_2000_2001").mkdir(parents=True)
    (raw / "temperature_anomaly_2000_2001").mkdir()
    fake_precip_monthly(raw / "precipitation_2000_2001", ["2000-11", "2000-12", "2001-01", "2001-02", "2001-03"])
    fake_temperature(raw / "temperature_anomaly_2000_2001", ["2000-12", "2001-01", "2001-02", "2001-03", "2001-04"])
    return raw


def test_prepare_builds_once_and_only_what_changed(tmp_path):
    raw, out = raw_like_the_datasets_repo(tmp_path), tmp_path / "built"
    log = prepare(raw, out)
    assert "2 dataset(s) built" in log
    names = sorted(p.name for p in out.glob("*.npz"))
    # one dataset per variable, each with its own prefix and its own months
    assert names == ["real_precipitation_2000_2001_precipitation.npz", "real_temperature_2000_2001_surface_temperature.npz"]
    assert all((out / n).with_suffix(".json").exists() for n in names)
    assert not (out / ".preparing").exists() and not list(out.glob(".building-*"))
    hist = json.loads((out / "real_temperature_2000_2001_surface_temperature.json").read_text())
    assert hist["anomaly"] is True and hist["shape"][0] == 5
    assert "frames_per_bar" not in hist                            # one bar = one month, like every other dataset

    # nothing changed -> nothing is rebuilt
    stamp = {p.name: p.stat().st_mtime_ns for p in out.glob("*.npz")}
    assert "0 dataset(s) built" in prepare(raw, out)
    assert stamp == {p.name: p.stat().st_mtime_ns for p in out.glob("*.npz")}

    # more temperature arrives -> only the temperature dataset is rebuilt, and the file whose name contained
    # the old year range is removed
    src = raw / "temperature_anomaly_2000_2001" / "temperature_anomaly_2001_04.npy"
    for m in ["2001_05", "2001_06", "2001_07", "2001_08", "2001_09", "2001_10", "2001_11", "2001_12", "2002_01"]:
        (src.parent / f"temperature_anomaly_{m}.npy").write_bytes(src.read_bytes())
    log = prepare(raw, out)
    assert "1 dataset(s) built" in log and "up to date: 'precipitation'" in log
    names = sorted(p.name for p in out.glob("*.npz"))
    assert "real_temperature_2000_2002_surface_temperature.npz" in names
    assert "real_temperature_2000_2001_surface_temperature.npz" not in names
    assert stamp["real_precipitation_2000_2001_precipitation.npz"] == \
        (out / "real_precipitation_2000_2001_precipitation.npz").stat().st_mtime_ns


def test_no_raw_data_is_not_an_error(tmp_path):
    (tmp_path / "data").mkdir()
    log = prepare(tmp_path / "data", tmp_path / "built")
    assert "nothing to do" in log and not (tmp_path / "built").exists()


def test_yearly_precipitation_files_still_work(tmp_path):
    """The older layout (one .npz per year, as in data_processing) is recognised too."""
    raw = tmp_path / "data"
    (raw / "precipitation").mkdir(parents=True)
    fake_year(raw / "precipitation", 2000, [11, 12])
    fake_year(raw / "precipitation", 2001, [1, 2, 3])
    log = prepare(raw, tmp_path / "built")
    assert "1 dataset(s) built" in log and "skip 'temperature history'" in log
    assert (tmp_path / "built" / "real_precipitation_2000_2001_precipitation.npz").exists()


def test_lightning_and_unknown_folders_and_removed_recipes(tmp_path):
    raw, out = raw_like_the_datasets_repo(tmp_path), tmp_path / "built"
    (raw / "lightning_2001_2001").mkdir()
    fake_lightning(raw / "lightning_2001_2001", ["2001-02", "2001-03", "2001-04"])
    (raw / "co2_2002_2022").mkdir()                              # a folder the data step does not use (yet)
    (raw / "co2_2002_2022" / "co2_2002_09.npy").write_bytes(b"not read")
    # a dataset left in the cache by an older version of the recipes (e.g. a combined one) is cleaned up
    out.mkdir()
    (out / "real_monthly_2000_2001_precipitation.npz").write_bytes(b"old")
    (out / "real_monthly_2000_2001_precipitation.json").write_text("{}")
    (out / "prepared.json").write_text(json.dumps({"precipitation + temperature": {
        "signature": "x", "files": ["real_monthly_2000_2001_precipitation.npz", "real_monthly_2000_2001_precipitation.json"]}}))
    log = prepare(raw, out)
    assert "3 dataset(s) built" in log and "removed the datasets of 'precipitation + temperature'" in log
    names = sorted(p.name for p in out.glob("*.npz"))
    assert names == ["real_lightning_2001_2001_lightning.npz", "real_precipitation_2000_2001_precipitation.npz",
                     "real_temperature_2000_2001_surface_temperature.npz"]
    assert not (out / "real_monthly_2000_2001_precipitation.json").exists()
    shape = lambda n: json.loads((out / n).with_suffix(".json").read_text())["shape"][0]   # noqa: E731
    assert shape("real_lightning_2001_2001_lightning.npz") == 3 and shape("real_precipitation_2000_2001_precipitation.npz") == 5


def test_composer_lines_up_maps_of_different_datasets_by_date(tmp_path):
    """One map per variable, each with its own first and last month: one piece, instruments silent where they have no data."""
    import orchestra
    raw, built = raw_like_the_datasets_repo(tmp_path), tmp_path / "built"
    prepare(raw, built)
    temp = built / "real_temperature_2000_2001_surface_temperature.npz"       # 2000-12 .. 2001-04
    rain = built / "real_precipitation_2000_2001_precipitation.npz"           # 2000-11 .. 2001-03
    s = orchestra.compose([str(temp), str(rain)], zone="northern_hemi", out_dir=tmp_path / "o", name="a")["doc"]
    assert s["n_bars"] == 6 and s["time_start"].startswith("2000-11") and s["time_last_frame"].startswith("2001-04")
    t = s["instruments"]["surface_temperature"]["values_per_beat"][::4]
    p = s["instruments"]["precipitation"]["values_per_beat"][::4]
    assert t[0] is None and all(v is not None for v in t[1:])                 # no temperature in 2000-11
    assert p[-1] is None and all(v is not None for v in p[:-1])               # no precipitation in 2001-04
    bar = lambda n: int(n["start"]) // 4                                      # noqa: E731
    assert 0 not in {bar(n) for n in s["instruments"]["surface_temperature"]["notes"]}      # the piano is silent there
    assert 5 not in {bar(n) for n in s["instruments"]["precipitation"]["notes"]}
    # a window where only one of them has data: the other instrument is simply not in the piece
    s = orchestra.compose([str(temp), str(rain)], zone="northern_hemi", start="2001-04", out_dir=tmp_path / "o", name="b")["doc"]
    assert set(s["instruments"]) - {"anomaly_markers"} == {"surface_temperature"} and s["n_bars"] == 1
    # a start date and a length: 3 bars from 2000-12, both present
    s = orchestra.compose([str(temp), str(rain)], zone="northern_hemi", start="2000-12", duration_s=9.5,
                          out_dir=tmp_path / "o", name="c")["doc"]
    assert s["n_bars"] == 3 and s["time_last_frame"].startswith("2001-02") and abs(s["duration_s"] - 9.5) < 0.05
    # maps that do not share a time step are refused, not silently mixed
    z = dict(np.load(rain))
    z["time"] = np.array([x.replace("-15T", "-01T") for x in z["time"]])
    np.savez_compressed(tmp_path / "shifted_precipitation.npz", **z)
    (tmp_path / "shifted_precipitation.json").write_text(rain.with_suffix(".json").read_text())
    try:
        orchestra.compose([str(temp), str(tmp_path / "shifted_precipitation.npz")], zone="northern_hemi",
                          out_dir=tmp_path / "o", name="d")
        raise AssertionError("expected a ComposeError")
    except orchestra.ComposeError as e:
        assert "time step" in str(e)
    # two maps for the same variable are refused
    try:
        orchestra.compose([str(rain), str(tmp_path / "shifted_precipitation.npz")], zone="northern_hemi",
                          out_dir=tmp_path / "o", name="e")
        raise AssertionError("expected a ComposeError")
    except orchestra.ComposeError as e:
        assert "one per variable" in str(e)


API_CHECK = """
import json
from fastapi.testclient import TestClient
from backend.app import app
c = TestClient(app)
pts = [{"lat": 46.5, "lng": 6.6}]
def sonify(**kw):
    r = c.post("/api/sonify", json={"zone_id": "x", "points": pts, "embed_audio": False, "duration_s": 6, **kw})
    return {"status": r.status_code, **r.json()}
inst = {r["dataset_id"]: r for r in c.get("/api/instruments").json()["instruments"]}
print(json.dumps({
    "health": c.get("/api/health").json(),
    "ids": sorted(x["id"] for x in c.get("/api/datasets").json()["datasets"]),
    "precip": sonify(dataset_id="precipitation", time_period="2001-01"),
    "temp": sonify(dataset_id="temperature", time_period="2001-04"),
    "nodate": sonify(dataset_id="temperature"),
    "faster": sonify(dataset_id="temperature", time_period="2000-12", options={"frames_per_bar": 2}),
    "both_early": sonify(dataset_id=["temperature", "precipitation"], time_period="2000-11"),
    "both_late": sonify(dataset_id=["precipitation", "temperature"], time_period="2001-03"),
    "orchestra": sonify(time_period="2000-12"),
    "forced": sonify(dataset_id="precipitation", time_period="2001-01",
                     options={"dataset": "real_precipitation_2000_2001"}),
    "missing": sonify(dataset_id=["temperature", "co2"], time_period="2001-01"),
    "blocked": c.get("/api/files/_datasets/real_precipitation_2000_2001_precipitation.npz").status_code,
    "ranges": {k: [v["time_start"][:7], v["time_end"][:7]] for k, v in inst.items() if v["available"]},
}))
"""


def run_api(raw, cache, **env):
    full = {**os.environ, "JUKEBOX_DATA_DIR": str(raw), "JUKEBOX_CACHE_DIR": str(cache), **env}
    full.pop("JUKEBOX_BUILT_DIR", None)
    r = subprocess.run([sys.executable, "-c", API_CHECK], cwd=str(ROOT), env=full, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_api_serves_the_prepared_datasets_merged_by_date(tmp_path):
    """Like the deployment: the data folder holds only RAW folders (read-only), the datasets come from the built
    folder, one per variable, and JUKEBOX_MERGE_DATASETS=1 lets a request mix them."""
    raw, cache = raw_like_the_datasets_repo(tmp_path), tmp_path / "cache"
    prepare(raw, cache / "_datasets")
    res = run_api(raw, cache, JUKEBOX_MERGE_DATASETS="1")
    assert res["health"]["datasets"] == 2 and res["health"]["preparing_data"] is False and res["health"]["merge_datasets"]
    assert res["ids"] == ["real_precipitation_2000_2001", "real_temperature_2000_2001"]
    assert res["blocked"] == 404                                   # the built datasets are not downloadable as "files"
    # one variable: served from its own dataset, one bar = one month (6 s = 2 bars)
    p = res["precip"]
    assert p["status"] == 200 and p["real_data"] and p["units"] == "mm/day" and p["time_period"] == "2001-01"
    assert p["source_dataset"] == "real_precipitation_2000_2001" and p["next_time_period"] == "2001-03"
    t = res["temp"]
    assert t["source_dataset"] == "real_temperature_2000_2001" and t["time_period"] == "2001-04" and t["next_time_period"] is None
    assert res["nodate"]["time_period"] == "2000-12"               # no date: from the first month of that variable
    assert res["faster"]["next_time_period"] == "2001-04"          # the frontend asked for 2 months per bar
    # two variables from two datasets in one piece. 2000-11: precipitation only, the piano joins in 2000-12
    b = res["both_early"]
    assert b["status"] == 200 and b["time_period"] == "2000-11" and b["next_time_period"] == "2001-01"
    assert b["values"]["surface_temperature"] is None and b["values"]["precipitation"] > 0
    assert b["source_datasets"] == {"surface_temperature": "real_temperature_2000_2001",
                                    "precipitation": "real_precipitation_2000_2001"}
    assert b["value"] is None and b["units"] == "degC"             # the main variable is the first one asked for
    # 2001-03 .. 2001-04: precipitation ends in 2001-03, temperature goes on; the piece covers both months
    b = res["both_late"]
    assert b["time_period"] == "2001-03" and b["time_end"] == "2001-04" and b["next_time_period"] is None
    assert b["values"]["precipitation"] > 0 and b["value"] == b["values"]["precipitation"]
    # no dataset_id: every variable the server has
    assert set(res["orchestra"]["values"]) == {"surface_temperature", "precipitation"}
    # a named dataset is used alone, as before
    assert res["forced"]["source_dataset"] == "real_precipitation_2000_2001" and res["forced"]["source_datasets"] is None
    assert res["missing"]["status"] == 400                         # co2 exists in no dataset
    # /api/instruments: the whole period each variable can be asked for
    assert res["ranges"] == {"temperature": ["2000-12", "2001-04"], "precipitation": ["2000-11", "2001-03"]}


def test_without_merge_mode_one_dataset_per_request(tmp_path):
    """The default (JUKEBOX_MERGE_DATASETS unset): a request is served from ONE dataset, as before."""
    raw, cache = raw_like_the_datasets_repo(tmp_path), tmp_path / "cache"
    prepare(raw, cache / "_datasets")
    res = run_api(raw, cache, JUKEBOX_MERGE_DATASETS="0")
    assert res["health"]["merge_datasets"] is False
    assert res["precip"]["status"] == 200 and res["precip"]["source_datasets"] is None
    assert res["both_early"]["status"] == 400 and "no single dataset" in res["both_early"]["detail"]
    assert len(res["orchestra"]["values"]) == 1                    # the default dataset only
