"""Vinyl mode (POST /api/zones/groove):  python -m pytest backend/test_groove.py -q"""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("JUKEBOX_CACHE_DIR", tempfile.mkdtemp(prefix="jukebox_cache_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.app import app  # noqa: E402
import groove  # noqa: E402
import orchestra  # noqa: E402

client = TestClient(app)


def _fake_map(folder: Path, values_by_month: dict, res: float = 1.0, var="precipitation", vmin=0.0, vmax=20.0):
    """A small map file like the built datasets: data [T, lat, lon], times 'YYYY-MM-01', a JSON sidecar."""
    lat = np.arange(90 - res / 2, -90, -res)
    lon = np.arange(-180 + res / 2, 180, res)
    times = sorted(values_by_month)
    data = np.stack([values_by_month[t](lat[:, None], lon[None, :]) * np.ones((len(lat), len(lon)))
                     for t in times]).astype(np.float32)
    path = folder / f"fake_{var}.npz"
    np.savez_compressed(path, data=data, lat=lat, lon=lon, time=np.array([f"{t}-01" for t in times]))
    path.with_suffix(".json").write_text(json.dumps({"variable": var, "units": "mm/day", "vmin": vmin, "vmax": vmax}))
    return path


def test_disc_mean_reads_the_needle():
    lat = np.arange(89.5, -90, -1.0)
    lon = np.arange(-179.5, 180, 1.0)
    field = np.broadcast_to(lon[None, :], (len(lat), len(lon))).astype(np.float64).copy()   # value = longitude
    assert abs(groove.disc_mean(field, lat, lon, 0.0, 20.0, 2.0) - 20.0) < 0.6
    # across the dateline the disc takes cells on both sides (about -179.5 and +179.5)
    both = groove.disc_mean(np.abs(field), lat, lon, 0.0, 180.0, 2.0)
    assert 178 < both < 180
    # a needle thinner than a cell reads the nearest cell
    assert groove.disc_mean(field, lat, lon, 10.2, 33.3, 0.1) == 33.5
    # no data under the needle: NaN
    field[:, 100:140] = np.nan
    assert np.isnan(groove.disc_mean(field, lat, lon, 0.0, -50.0, 1.0))


def test_period_map_month_and_year(tmp_path):
    months = {f"2018-{m:02d}": (lambda m: lambda la, lo: m + 0 * la)(m) for m in range(1, 13)}
    f = _fake_map(tmp_path, months)
    field, *_ = groove.period_map(f, "2018-07")
    assert np.allclose(field, 7)
    year, *_ = groove.period_map(f, "2018")
    assert np.allclose(year, 6.5)                    # the mean of the 12 months
    assert groove.period_map(f, "1999") is None


def test_groove_series_one_value_per_place(tmp_path):
    f = _fake_map(tmp_path, {"2018-07": lambda la, lo: np.clip(lo / 10, 0, None)})   # rain grows eastwards
    out = groove.groove_series({"precipitation": f}, "2018-07", [(0, 0), (0, 50), (0, 100)], 1.5)
    vals, meta = out["precipitation"]
    assert len(vals) == 3 and vals[0] < vals[1] < vals[2]
    assert meta["vmax"] == 20.0
    assert groove.groove_series({"precipitation": f}, "2001-01", [(0, 0)], 1.5) == {}


def _first_beat_pitches(doc, var):
    """Pitches of the first beat: the value of the first place exactly (later beats glide towards the next one)."""
    return sorted({n["pitch"] for n in doc["instruments"][var]["notes"] if n["start"] < 1})


def test_global_scale_same_value_same_note(tmp_path):
    """In vinyl mode a value is the same note wherever the needle is (dataset range), not the piece's own range."""
    meta = {"variable": "precipitation", "units": "mm/day", "vmin": 0.0, "vmax": 20.0}
    pieces = {}
    for name, vals in {"dry_then_wet": [8.0, 1.0, 15.0, 19.0], "flat": [8.0, 8.5, 8.0, 8.5]}.items():
        out = orchestra.compose([], bbox=(-1, 1, 0, 3), series_in={"precipitation": (np.array(vals), meta)},
                                pitch_range="series", frames_per_bar=1, bpm=80, out_dir=tmp_path / name, name="score")
        pieces[name] = out["doc"]
        assert out["doc"]["n_bars"] == 4                  # one place = one bar
    first = _first_beat_pitches(pieces["dry_then_wet"], "precipitation")
    assert first and first == _first_beat_pitches(pieces["flat"], "precipitation")


def test_one_point_per_beat(tmp_path):
    """frames_per_bar = 4: every point is one beat, the notes follow each point (not one value per bar)."""
    meta = {"variable": "precipitation", "units": "mm/day", "vmin": 0.0, "vmax": 20.0}
    vals = np.array([2.0, 18.0, 2.0, 18.0, 2.0, 18.0, 2.0, 18.0])          # dry, wet, dry, wet ... beat by beat
    doc = orchestra.compose([], bbox=(-1, 1, 0, 3), series_in={"precipitation": (vals, meta)}, pitch_range="series",
                            frames_per_bar=4, bpm=80, out_dir=tmp_path, name="score")["doc"]
    assert doc["n_bars"] == 2 and abs(doc["duration_s"] - 6.0) < 0.1      # 8 points = 2 bars of 3 s
    beat_values = doc["instruments"]["precipitation"]["values_per_beat"]
    assert beat_values == [2.0, 18.0] * 4


def _instrument(dataset_id):
    return next((i for i in client.get("/api/instruments").json()["instruments"] if i["dataset_id"] == dataset_id), None)


def test_groove_endpoint():
    temp = _instrument("temperature")
    assert temp, "no temperature dataset on this server"
    # a month inside the data
    year = int(temp["last_time_period"][:4]) - 1
    month = f"{year}-07" if len(temp["last_time_period"]) > 4 else str(year)
    path = [{"lat": 46.2, "lng": 6.1 + 2.5 * k} for k in range(4)]          # Geneva, eastwards
    body = {"zone_id": "groove-1", "dataset_ids": ["temperature"], "time_period": month, "path": path, "radius_deg": 1.5}
    r = client.post("/api/zones/groove", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["zone_id"] == "groove-1" and j["time_period"] == month and j["dataset_ids"] == ["temperature"]
    assert j["bar_seconds"] == 3.0 and abs(j["duration_s"] - 12.0) < 0.2       # 4 places = 4 bars of 3 s
    assert [(s["lat"], s["lng"]) for s in j["stops"]] == [(46.0, 6.0), (46.0, 8.5), (46.0, 11.0), (46.0, 13.5)]
    assert all(set(s["values"]) == {"temperature"} for s in j["stops"])
    audio = base64.b64decode(j["audio_data"])
    assert len(audio) > 5000 and (audio[:3] == b"ID3" or audio[0] == 0xFF or audio[:4] == b"RIFF")
    # the same places (after snapping) come from the cache: the same piece
    nudged = {**body, "path": [{"lat": p["lat"] + 0.04, "lng": p["lng"] - 0.04} for p in path]}
    assert client.post("/api/zones/groove", json=nudged).json()["audio_data"] == j["audio_data"]
    assert j["points_per_bar"] == 1 and j["stop_seconds"] == 3.0
    # a whole year instead of a month
    y = client.post("/api/zones/groove", json={**body, "time_period": str(year)})
    assert y.status_code == 200, y.text
    assert y.json()["time_period"] == str(year)
    # one point per beat: 8 points = 2 bars = 6 s, each point 0.75 s
    beats = {**body, "points_per_bar": 4, "path": [{"lat": 46.2, "lng": 6.1 + 1.0 * k} for k in range(8)]}
    b = client.post("/api/zones/groove", json=beats)
    assert b.status_code == 200, b.text
    jb = b.json()
    assert jb["points_per_bar"] == 4 and jb["stop_seconds"] == 0.75 and len(jb["stops"]) == 8
    assert abs(jb["duration_s"] - 6.0) < 0.2


def test_groove_errors():
    path = [{"lat": 0, "lng": 0}]
    post = lambda **kw: client.post("/api/zones/groove", json={"time_period": "2018-07", "path": path, **kw})  # noqa: E731
    assert post(time_period="July 2018").status_code == 422
    assert post(time_period="2018-13").status_code == 422
    assert post(path=[]).status_code == 422
    assert post(path=[{"lat": 0, "lng": k} for k in range(21)]).status_code == 422       # 20 bars at most
    assert post(path=[{"lat": 0, "lng": k} for k in range(84)], points_per_bar=4).status_code == 422   # 80 points
    assert post(path=[{"lat": 0, "lng": k} for k in range(6)], points_per_bar=4).status_code == 422    # whole bars
    assert post(points_per_bar=3).status_code == 422
    assert post(radius_deg=50).status_code == 422
    assert post(dataset_ids=["unicorns"]).status_code == 400
    assert post(time_period="1700").status_code == 400                                     # no data at that date


def test_groove_genre():
    """`genre` (aliases ok) changes the music, is echoed only when sent, keeps the fixed 3 s bars; unknown -> 400."""
    temp = _instrument("temperature")
    assert temp, "no temperature dataset on this server"
    year = int(temp["last_time_period"][:4]) - 1
    month = f"{year}-07" if len(temp["last_time_period"]) > 4 else str(year)
    path = [{"lat": 46.2, "lng": 6.1 + 2.5 * k} for k in range(4)]
    body = {"dataset_ids": ["temperature"], "time_period": month, "path": path}
    plain = client.post("/api/zones/groove", json=body).json()
    assert "genre" not in plain
    funk = client.post("/api/zones/groove", json={**body, "genre": "funk"})
    assert funk.status_code == 200, funk.text
    f = funk.json()
    assert f["genre"] == "funk" and f["audio_data"] != plain["audio_data"]
    assert f["bar_seconds"] == 3.0 and abs(f["duration_s"] - 12.0) < 0.2         # the tempo stays fixed
    alias = client.post("/api/zones/groove", json={**body, "genre": "jazz"}).json()
    assert alias["genre"] == "swing" and alias["audio_data"] != f["audio_data"]  # another genre, another cache key
    assert client.post("/api/zones/groove", json={**body, "genre": "funk"}).json()["audio_data"] == f["audio_data"]
    real = client.post("/api/zones/groove", json={**body, "genre": "realistic"})
    assert real.status_code == 200, real.text
    assert real.json()["genre"] == "realistic" and abs(real.json()["duration_s"] - 12.0) < 0.2
    assert client.post("/api/zones/groove", json={**body, "genre": "polka"}).status_code == 400
