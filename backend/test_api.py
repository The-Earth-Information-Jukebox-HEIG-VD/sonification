"""End-to-end API tests:  python -m pytest backend/test_api.py -q   (or: python backend/test_api.py)"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("JUKEBOX_CACHE_DIR", tempfile.mkdtemp(prefix="jukebox_cache_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from backend.app import app  # noqa: E402

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.json()["datasets"] >= 1, "generate data first: python simulation/simulate_variables.py ..."


def test_options_and_datasets():
    o = client.get("/api/options").json()
    assert any(z["id"] == "switzerland" for z in o["zones"])
    assert "orchestra" in [p["id"] for p in o["palettes"]]
    d = client.get("/api/datasets").json()
    assert d["default"] and d["datasets"][0]["variables"]


def test_compose_zone_with_audio_and_cache():
    body = {"zone": "india", "start": "2022-01", "end": "2023-12", "audio": True, "audio_format": "mp3"}
    t = time.perf_counter()
    r = client.post("/api/compose", json=body)
    first = time.perf_counter() - t
    assert r.status_code == 200, r.text
    j = r.json()
    assert not j["cached"] and j["files"]["midi"] and j["files"]["audio"]
    assert j["score"]["n_bars"] > 0 and "surface_temperature" in j["score"]["instruments"]
    assert j["score"]["instruments"]["co2"]["notes"][0].keys() >= {"pitch", "start", "duration", "velocity"}
    # files are downloadable
    for url in (j["files"]["midi"], j["files"]["audio"], *j["files"]["stems"].values()):
        f = client.get(url)
        assert f.status_code == 200 and len(f.content) > 0
    assert client.get(j["files"]["midi"]).content[:4] == b"MThd"
    # same request again -> cache hit, fast
    t = time.perf_counter()
    r2 = client.post("/api/compose", json=body)
    assert r2.json()["cached"] and r2.json()["id"] == j["id"]
    print(f"compose india: first {first:.2f}s, cached {time.perf_counter() - t:.3f}s")
    # fetch by id, light payload
    r3 = client.get(f"/api/compositions/{j['id']}?include_notes=false")
    assert "notes" not in next(iter(r3.json()["score"]["instruments"].values()))


def test_compose_bbox_time_window_no_audio():
    r = client.post("/api/compose", json={"dataset": "sim_both_24f_1deg", "bbox": [45.8, 47.8, 5.9, 10.5],
                                          "start": "2024-06", "end": "2024-12",
                                          "audio": False, "variables": ["surface_temperature", "co2"]})
    assert r.status_code == 200, r.text
    s = r.json()["score"]
    assert s["zone"] == "custom" and s["n_bars"] == 7 and set(s["instruments"]) - {"anomaly_markers"} == {"surface_temperature", "co2"}
    assert s["intro_bars"] == 0 and s["style"] == "data"                   # data style: no intro
    assert r.json()["files"]["audio"] is None


def test_real_dataset_tension_and_form():
    ds = client.get("/api/datasets").json()
    real = [d for d in ds["datasets"] if not d["simulated"]]
    if not real:
        print("skip: no real dataset (python simulation/build_realistic_dataset.py)"); return
    assert ds["default"] == real[0]["id"]
    r = client.post("/api/compose", json={"dataset": real[0]["id"], "zone": "switzerland", "style": "musical",
                                          "start": "2022-01", "end": "2024-12", "audio": False})
    assert r.status_code == 200, r.text
    s = r.json()["score"]
    names = [x["name"] for x in s["sections"]]
    assert names[0] == "intro" and "data" in names and "outro" in names
    climax = [x for x in s["sections"] if x["name"] == "climax"]
    assert climax and climax[0]["time"].startswith("2024-02"), climax      # record-warm Feb 2024
    assert any(b["mode"] == "minor" for b in s["bars"]) and any(b["mode"] == "major" for b in s["bars"])
    n = next(iter(s["instruments"].values()))["notes"][0]
    assert "time_s" in n and "duration_s" in n
    assert s["duration_s"] > s["sections"][-1]["start_s"]                   # ritardando outro included
    flat = client.post("/api/compose", json={"dataset": real[0]["id"], "zone": "switzerland", "start": "2022-01",
                                             "end": "2024-12", "audio": False, "intro": False, "tension": False,
                                             "style": "musical",
                                             "humanize": 0}).json()["score"]
    assert flat["intro_bars"] == 0 and all(b["mode"] == "major" for b in flat["bars"])


def test_data_style_follows_the_data():
    import numpy as np
    r = client.post("/api/compose", json={"zone": "switzerland", "start": "2022-01", "end": "2024-12", "audio": False})
    assert r.status_code == 200, r.text
    s = r.json()["score"]
    assert s["style"] == "data" and s["intro_bars"] == 0 and all(b["chord"] is None for b in s["bars"])
    assert [x["name"] for x in s["sections"]] == ["data"]
    inst = s["instruments"]["surface_temperature"]
    vals = np.array([v if v is not None else np.nan for v in inst["values_per_beat"]])
    root = np.full(len(vals), np.nan)                 # temperature = piano chords: the ROOT follows the value
    for n in inst["notes"]:
        k = int(n["start"])
        if 0 <= k < len(vals):
            root[k] = n["pitch"] if np.isnan(root[k]) else min(root[k], n["pitch"])
    ok = np.isfinite(vals) & np.isfinite(root)
    assert ok.mean() > 0.95 and np.corrcoef(vals[ok], root[ok])[0, 1] > 0.98
    assert len(inst["notes"]) >= 3 * ok.sum()           # a chord (3 notes) on every beat
    if "anomaly_markers" in s["instruments"]:
        m = s["instruments"]["anomaly_markers"]
        assert all(n["pitch"] in (81, 77) for n in m["notes"])


def test_regional_palette():
    r = client.post("/api/compose", json={"dataset": "sim_both_24f_1deg", "zone": "japan", "palette": "auto",
                                          "style": "musical", "audio": False})
    assert r.json()["score"]["palette"] == "japan" and r.json()["score"]["scale"] == "minor"


def test_errors():
    assert client.post("/api/compose", json={"zone": "atlantis"}).status_code == 422
    assert client.post("/api/compose", json={"bbox": [50, 40, 0, 10]}).status_code == 422
    assert client.post("/api/compose", json={"dataset": "nope"}).status_code == 404
    assert client.post("/api/compose", json={"zone": "india", "variables": ["unicorns"]}).status_code == 400
    assert client.post("/api/compose", json={"zone": "india", "start": "1990", "end": "1991"}).status_code == 400
    assert client.get("/api/files/abc/../../etc/passwd").status_code == 404


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok ", name)
