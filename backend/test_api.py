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
_ds = {d["id"]: d for d in client.get("/api/datasets").json()["datasets"]}
MONTHLY = next((k for k in _ds if k.startswith("real_20") and _ds[k]["frames"] > 100), None) or "sim_both_24f_1deg"
DAILY = next((k for k in _ds if k.startswith("real_daily")), None)
# the best dataset that has the whole orchestra (the default one can hold a single real variable, e.g. real_monthly_*)
FULL = max((k for k in _ds if {"surface_temperature", "co2"} <= set(_ds[k]["variables"])),
           key=lambda k: (len(_ds[k]["real_variables"]), len(_ds[k]["variables"]), _ds[k]["frames"]), default=None)
REAL_MONTHLY = next((k for k in _ds if k.startswith("real_monthly")), None)


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
    body = {"zone": "india", "start": "2024-05", "end": "2024-09", "audio": True, "audio_format": "mp3",
            **({"dataset": FULL} if FULL else {})}
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


def test_single_date_and_duration():
    ds = {d["id"]: d for d in client.get("/api/datasets").json()["datasets"]}
    if "sim_both_24f_1deg" not in ds:
        print("skip: no simulated dataset (python simulation/simulate_variables.py ...)"); return
    base = {"dataset": "sim_both_24f_1deg", "zone": "europe", "audio": False, "variables": ["surface_temperature"]}
    # one date as numbers + a length in seconds: 30 s from March 2024
    r = client.post("/api/compose", json={**base, "year": 2024, "month": 3, "duration_s": 30})
    assert r.status_code == 200, r.text
    s = r.json()["score"]
    assert s["time_start"].startswith("2024-03") and s["time_end"].startswith("2024-12")
    assert s["n_bars"] == 10 and s["bpm"] == 80 and abs(s["duration_s"] - 30) < 0.05
    assert r.json()["request"]["start"] == "2024-03"
    # the same date as text, with or without the leading zero, is the same piece
    for start in ("2024-03", "2024-3"):
        again = client.post("/api/compose", json={**base, "start": start, "duration_s": 30}).json()
        assert again["cached"] and again["id"] == r.json()["id"]
    # a year alone, as text, is a date (a number is a frame index)
    s = client.post("/api/compose", json={**base, "start": "2024", "end": "2024"}).json()["score"]
    assert s["n_bars"] == 12 and s["time_start"].startswith("2024-01") and s["time_end"].startswith("2024-12")
    s = client.post("/api/compose", json={**base, "year": 2025, "duration_s": 15}).json()["score"]
    assert s["time_start"].startswith("2025-01") and s["n_bars"] == 5 and abs(s["duration_s"] - 15) < 0.05
    # the length counts bars: with 2 frames per bar, the same 30 s covers twice as many months
    s = client.post("/api/compose", json={**base, "year": 2024, "month": 3, "duration_s": 30, "frames_per_bar": 2}).json()["score"]
    assert s["n_bars"] == 10 and s["time_last_frame"].startswith("2025-10") and abs(s["duration_s"] - 30) < 0.05
    assert s["time_end"].startswith("2025-09")                              # time_end = start of the last bar
    # an end date still stops the piece earlier
    s = client.post("/api/compose", json={**base, "start": "2024-03", "end": "2024-05", "duration_s": 30}).json()["score"]
    assert s["n_bars"] == 3
    # close to the end of the data there are fewer months left: the piece is shorter, the tempo is the same
    s = client.post("/api/compose", json={**base, "year": 2025, "month": 10, "duration_s": 30}).json()["score"]
    assert s["n_bars"] == 3 and s["bpm"] == 80 and abs(s["duration_s"] - 9) < 0.05
    assert client.post("/api/compose", json={**base, "month": 3}).status_code == 422                       # month needs year
    assert client.post("/api/compose", json={**base, "year": 2024, "start": "2024-03"}).status_code == 422
    assert client.post("/api/compose", json={**base, "year": 2024, "duration_s": 0}).status_code == 422
    assert client.post("/api/compose", json={**base, "year": 2090, "duration_s": 30}).status_code == 400   # after the data
    # a date before the data starts at the first frame (score.time_start says where)
    s = client.post("/api/compose", json={**base, "year": 1990, "duration_s": 30}).json()["score"]
    assert s["time_start"].startswith("2024-01") and s["n_bars"] == 10


def test_instruments_table():
    j = client.get("/api/instruments").json()
    assert j["palette"] == "orchestra" and len(j["instruments"]) == len(j["by_dataset"]) >= 6
    assert {"temperature", "co2", "precipitation", "dust", "smoke", "lightning"} <= set(j["by_dataset"])
    t = next(r for r in j["instruments"] if r["dataset_id"] == "temperature")
    assert t["variable"] == "surface_temperature" and t["label"] == "Temperature" and t["instrument"] == j["by_dataset"]["temperature"]
    assert t["always_playing"] and t["available"] and t["units"] == "degC" and "temp" in t["also_accepted"]
    # every dataset_id of the table is accepted by /api/sonify
    for r in j["instruments"]:
        if not r["available"]:                                   # e.g. snowfall is only in the daily dataset
            continue
        ok = client.post("/api/sonify", json={"zone_id": "europe", "dataset_id": r["dataset_id"], "embed_audio": False,
                                              "duration_s": 6})
        assert ok.status_code == 200 and list(ok.json()["values"]) == [r["variable"]], ok.text
    # regional instruments
    jp = client.get("/api/instruments?palette=auto&zone=japan").json()
    assert jp["palette"] == "japan" and jp["by_dataset"] != j["by_dataset"]
    assert client.get("/api/instruments?palette=nope").status_code == 400


def test_sonify_frontend_contract():
    """The frontend's own request/response shape: points + one time period -> audio inside the JSON."""
    import base64
    body = {"zone_id": "france", "zone_name": "France", "dataset_id": "temperature",
            "points": [{"lat": 48.8566, "lng": 2.3522, "x": 512, "y": 234},
                       {"lat": 48.1173, "lng": -1.6778, "x": 508, "y": 236},
                       {"lat": 43.2965, "lng": 5.3698, "x": 520, "y": 248}],
            "time_period": "2024-03", "aggregation": "mean"}
    r = client.post("/api/sonify", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["zone_id"] == "france" and j["dataset_id"] == "temperature" and j["point_count"] == 3
    assert j["time_period"] == "2024-03" and j["next_time_period"] == "2025-01"      # 30 s = 10 monthly bars
    assert isinstance(j["value"], float) and j["units"] == "degC" and list(j["values"]) == ["surface_temperature"]
    assert abs(j["duration_s"] - 30) < 0.05 and j["cell_count"] >= 1
    audio = base64.b64decode(j["audio_data"])
    assert len(audio) > 10000 and (audio[:4] == b"RIFF" if j["audio_format"] == "wav" else audio[:3] in (b"ID3", b"\xff\xfb\x90")
                                   or audio[0] == 0xFF)
    assert client.get(j["audio_url"]).content == audio
    # the frontend plays the chunk, then asks for the next one
    j2 = client.post("/api/sonify", json={**body, "time_period": j["next_time_period"], "embed_audio": False}).json()
    assert j2["time_period"] == "2025-01" and j2["audio_data"] is None and j2["audio_url"]
    # no dataset_id = the whole orchestra; lon is accepted for lng; a preset zone needs no points
    j3 = client.post("/api/sonify", json={"zone_id": "india", "time_period": "2024-03", "embed_audio": False,
                                          "options": {"bpm": 96, **({"dataset": FULL} if FULL else {})}}).json()
    assert len(j3["values"]) > 1 and j3["point_count"] == 0 and j3["cell_count"] is None
    pts = [{"lat": 46.5, "lon": 6.6}]
    assert client.post("/api/sonify", json={"points": pts, "dataset_id": ["co2", "rain"], "embed_audio": False}).status_code == 200
    assert client.post("/api/sonify", json={"points": pts, "aggregation": "max"}).status_code == 422
    assert client.post("/api/sonify", json={"zone_id": "atlantis"}).status_code == 422
    assert client.post("/api/sonify", json={"points": pts, "dataset_id": "unicorns"}).status_code == 400
    assert client.post("/api/sonify", json={"points": [{"lat": 99, "lng": 0}]}).status_code == 422


def test_real_dataset_tension_and_form():
    ds = client.get("/api/datasets").json()
    real = [d for d in ds["datasets"] if d["id"].startswith("real_2015")]
    if not real:
        print("skip: no real monthly dataset (python simulation/build_realistic_dataset.py)"); return
    best = max(ds["datasets"], key=lambda d: (len(d["real_variables"]), len(d["variables"]), d["frames"]))
    assert ds["default"] == best["id"]
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
    monthly = [d["id"] for d in client.get("/api/datasets").json()["datasets"] if d["id"].startswith("real_2015")]
    r = client.post("/api/compose", json={"zone": "switzerland", "start": "2022-01", "end": "2024-12", "audio": False,
                                          **({"dataset": monthly[0]} if monthly else {"dataset": FULL} if FULL else {})})
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


def test_real_monthly_precipitation():
    """The team's real monthly precipitation 2000-2026 (python simulation/build_monthly_dataset.py --precip DIR)."""
    if not REAL_MONTHLY:
        print("skip: no real monthly dataset (python simulation/build_monthly_dataset.py --precip DIR)"); return
    d = _ds[REAL_MONTHLY]
    assert d["real_variables"] == ["precipitation"] and d["frames"] >= 300 and d["time_start"].startswith("2000-06")
    # a request for precipitation at a date only this dataset covers finds it by itself
    pts = [{"lat": 46.5, "lng": 6.6}, {"lat": 47.4, "lng": 8.5}]
    j = client.post("/api/sonify", json={"zone_id": "ch", "dataset_id": "precipitation", "points": pts,
                                         "time_period": "2003-05", "embed_audio": False}).json()
    assert j["source_dataset"] == REAL_MONTHLY and j["real_data"] and j["units"] == "mm/day"
    assert j["time_period"] == "2003-05" and j["next_time_period"] == "2004-03" and abs(j["duration_s"] - 30) < 0.05
    # the monsoon: India is far wetter in July than in January
    s = client.post("/api/compose", json={"dataset": REAL_MONTHLY, "zone": "india", "start": "2019-01", "end": "2019-12",
                                          "audio": False}).json()["score"]
    v = s["instruments"]["precipitation"]["values_per_beat"]
    assert s["n_bars"] == 12 and v[6 * 4] > 5 * v[0] > 0
    # a variable this dataset does not have is served by another one
    if FULL:
        t = client.post("/api/sonify", json={"zone_id": "europe", "dataset_id": "temperature", "embed_audio": False,
                                             "duration_s": 6}).json()
        assert t["source_dataset"] != REAL_MONTHLY and t["units"] == "degC"


def test_real_daily_precipitation():
    if not DAILY:
        print("skip: no daily dataset (python simulation/build_daily_dataset.py --precip FILE)"); return
    assert client.get("/api/datasets").json()["default"] == DAILY        # most real variables
    r = client.post("/api/compose", json={"dataset": DAILY, "bbox": [38.5, 40.5, -1.5, 0.5], "audio": False})
    assert r.status_code == 200, r.text
    s = r.json()["score"]
    assert s["frames_per_bar"] == 7 and s["n_bars"] == 53                  # one bar per week
    rain = s["instruments"]["precipitation"]
    vals = [v or 0 for v in rain["values_per_beat"]]
    wettest = max(range(len(vals)), key=vals.__getitem__)
    assert s["bars"][wettest // s["beats_per_bar"]]["time"].startswith("2024-10"), "Valencia flood (Oct 2024)"
    assert any(int(n["start"]) == wettest for n in rain["notes"])
    ch = client.post("/api/compose", json={"dataset": DAILY, "zone": "switzerland", "audio": False}).json()["score"]
    snow_bars = {int(n["start"]) // 4 for n in ch["instruments"]["snowfall"]["notes"]}
    months = {ch["bars"][b]["time"][5:7] for b in snow_bars}
    assert months & {"01", "02", "12"} and not months & {"07", "08"}        # snow in winter, not summer


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
    assert client.post("/api/compose", json={"dataset": MONTHLY, "zone": "india", "start": "1990", "end": "1991"}).status_code == 400
    assert client.get("/api/files/abc/../../etc/passwd").status_code == 404


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok ", name)
