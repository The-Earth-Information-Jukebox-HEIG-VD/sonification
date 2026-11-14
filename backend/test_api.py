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


def _cells_in_box(grid, box):
    """[row, col] of every cell whose centre is inside [lat_min, lat_max, lon_min, lon_max]."""
    h, w = grid
    la0, la1, lo0, lo1 = box
    return [[r, c] for r in range(h) for c in range(w)
            if la0 <= 90 - (r + 0.5) * 180 / h <= la1 and lo0 <= -180 + (c + 0.5) * 360 / w <= lo1]


def test_compose_cells_instead_of_bbox():
    import numpy as np
    ds = {d["id"]: d for d in client.get("/api/datasets").json()["datasets"]}
    if "sim_both_24f_1deg" not in ds:
        print("skip: no simulated dataset (python simulation/simulate_variables.py ...)"); return
    grid = ds["sim_both_24f_1deg"]["variables"]["surface_temperature"]["grid"]
    base = {"dataset": "sim_both_24f_1deg", "start": "2024-06", "end": "2024-12", "audio": False,
            "variables": ["surface_temperature", "co2"]}
    box = [35, 71, -25, 45]                                                  # Europe

    def values(score):
        return np.array([np.nan if v is None else v
                         for v in score["instruments"]["surface_temperature"]["values_per_beat"]])

    ref = client.post("/api/compose", json={**base, "bbox": box}).json()["score"]
    cells = _cells_in_box(grid, box)
    r = client.post("/api/compose", json={**base, "cells": cells})
    assert r.status_code == 200, r.text
    s = r.json()["score"]
    assert s["zone"] == "custom" and s["cells"] == len(cells) and s["n_bars"] == 7
    assert np.allclose(values(s), values(ref), equal_nan=True)               # same cells -> same music
    assert s["bbox"] == [35.5, 70.5, -24.5, 44.5]                            # box around the cells (cell centres)
    assert r.json()["request"]["n_cells"] == len(cells) and "cells" not in r.json()["request"]
    # the same cells in another order are the same piece (cache hit)
    again = client.post("/api/compose", json={**base, "cells": cells[::-1]}).json()
    assert again["cached"] and again["id"] == r.json()["id"]
    # cells win over zone and bbox
    s = client.post("/api/compose", json={**base, "zone": "india", "bbox": [0, 10, 0, 10], "cells": cells}).json()["score"]
    assert np.allclose(values(s), values(ref), equal_nan=True)
    # any shape of area: half of the cells is a different piece
    half = client.post("/api/compose", json={**base, "cells": [c for c in cells if c[1] < 180]}).json()["score"]
    assert not np.allclose(values(half), values(ref), equal_nan=True)
    # positions picked on an array twice as fine, and on one ten times coarser, land on the same area
    fine = [[2 * a + i, 2 * b + j] for a, b in cells for i in (0, 1) for j in (0, 1)]
    s = client.post("/api/compose", json={**base, "cells": fine, "cells_grid": [2 * grid[0], 2 * grid[1]]}).json()["score"]
    assert s["cells"] == len(cells) and np.allclose(values(s), values(ref), equal_nan=True)
    s = client.post("/api/compose", json={**base, "cells": [[4, 18]], "cells_grid": [18, 36]}).json()["score"]
    assert s["cells"] == 100 and s["bbox"] == [40.5, 49.5, 0.5, 9.5]         # one 10-degree cell = 100 one-degree cells
    # a single small position on a fine array still selects the cell it falls in
    s = client.post("/api/compose", json={**base, "cells": [[172, 748]], "cells_grid": [720, 1440]}).json()["score"]
    assert s["cells"] == 1 and s["bbox"] == [46.5, 46.5, 7.5, 7.5]


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
    assert client.post("/api/compose", json={"cells": []}).status_code == 422
    assert client.post("/api/compose", json={"cells": [[1]]}).status_code == 422
    assert client.post("/api/compose", json={"cells": [[-1, 2]]}).status_code == 422
    assert client.post("/api/compose", json={"cells": [[5, 5]], "cells_grid": [4, 4]}).status_code == 422
    assert client.post("/api/compose", json={"cells": [[99999, 0]], "audio": False}).status_code == 400   # outside the array
    assert client.post("/api/compose", json={"dataset": "nope"}).status_code == 404
    assert client.post("/api/compose", json={"zone": "india", "variables": ["unicorns"]}).status_code == 400
    assert client.post("/api/compose", json={"zone": "india", "start": "1990", "end": "1991"}).status_code == 400
    assert client.get("/api/files/abc/../../etc/passwd").status_code == 404


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok ", name)
