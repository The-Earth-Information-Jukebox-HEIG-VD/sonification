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
    body = {"zone": "india", "audio": True, "audio_format": "mp3"}
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
    r = client.post("/api/compose", json={"bbox": [45.8, 47.8, 5.9, 10.5], "start": "2024-06", "end": "2024-12",
                                          "audio": False, "variables": ["surface_temperature", "co2"]})
    assert r.status_code == 200, r.text
    s = r.json()["score"]
    assert s["zone"] == "custom" and s["n_bars"] == 7 and set(s["instruments"]) == {"surface_temperature", "co2"}
    assert r.json()["files"]["audio"] is None


def test_regional_palette():
    r = client.post("/api/compose", json={"zone": "japan", "palette": "auto", "audio": False})
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
