"""
Earth Information Jukebox: sonification backend (FastAPI).

The frontend picks a dataset, a zone (preset or lat/lon box), a time window and a few musical
options; the backend composes the orchestra score and returns:
  * the score as JSON (bars, chords, instruments, notes, data values) for visualisation / playback
  * URLs to the MIDI file, per-instrument MIDI stems, and the rendered audio (MP3/WAV)

Run:   uvicorn backend.app:app --reload --port 8000        (from the repo root)
Docs:  http://localhost:8000/docs  (interactive OpenAPI)

Config (environment variables):
  JUKEBOX_DATA_DIR    folder with intensity maps (*.npz + *.json)   default: simulation/output
  JUKEBOX_CACHE_DIR   where compositions are written / cached        default: backend/cache
  JUKEBOX_CORS        comma-separated allowed origins                default: *
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "music"))
import orchestra  # noqa: E402
from zone_to_midi import ZONES  # noqa: E402

DATA_DIR = Path(os.environ.get("JUKEBOX_DATA_DIR", ROOT / "simulation" / "output"))
CACHE_DIR = Path(os.environ.get("JUKEBOX_CACHE_DIR", ROOT / "backend" / "cache"))
CORS = [o.strip() for o in os.environ.get("JUKEBOX_CORS", "*").split(",")]
API_VERSION = "1.0"

app = FastAPI(title="Earth Information Jukebox: sonification API", version=API_VERSION,
              description="Turns NASA EIC intensity maps into an orchestra (MIDI + audio).")
app.add_middleware(CORSMiddleware, allow_origins=CORS, allow_methods=["*"], allow_headers=["*"])


# ----------------------------------------------------------------------------
# Datasets: groups of maps "<prefix>_<variable>.npz" sharing one time axis
# ----------------------------------------------------------------------------
_datasets_cache: dict = {"mtime": None, "data": {}}


def scan_datasets() -> dict:
    files = sorted(DATA_DIR.glob("*.npz")) if DATA_DIR.exists() else []
    stamp = tuple((f.name, f.stat().st_mtime) for f in files)
    if _datasets_cache["mtime"] == stamp:
        return _datasets_cache["data"]
    groups: dict = {}
    for f in files:
        meta_path = f.with_suffix(".json")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        var = meta.get("variable")
        if not var:
            continue
        prefix = f.stem[: -len(var) - 1] if f.stem.endswith("_" + var) else f.stem
        g = groups.setdefault(prefix, {"id": prefix, "variables": {}, "files": []})
        with np.load(f) as z:
            times = [str(t) for t in z["time"]] if "time" in z.files else []
            shape = list(z["data"].shape)
        g["files"].append(f)
        g["variables"][var] = {"units": meta.get("units"), "vmin": meta.get("vmin"), "vmax": meta.get("vmax"),
                               "frames": shape[0], "grid": shape[1:], "source": meta.get("source"),
                               "instrument": orchestra.ORCHESTRA.get(var, {}).get("name")}
        g.setdefault("times", times)
        g["source"] = meta.get("source", "")
        g["simulated"] = "SIMULATED" in str(meta.get("source", "")).upper()
        g["resolution_deg"] = meta.get("resolution_deg")
    for g in groups.values():
        t = g.get("times") or []
        g["frames"] = len(t)
        g["time_start"], g["time_end"] = (t[0], t[-1]) if t else (None, None)
    _datasets_cache.update(mtime=stamp, data=groups)
    return groups


def public_dataset(g: dict) -> dict:
    return {k: v for k, v in g.items() if k not in ("files",)}


def default_dataset() -> Optional[str]:
    ds = scan_datasets()
    if not ds:
        return None
    real = [k for k, g in ds.items() if not g["simulated"]]
    return (real or sorted(ds, key=lambda k: -len(ds[k]["variables"])))[0]


# ----------------------------------------------------------------------------
# Request / response models
# ----------------------------------------------------------------------------
class ComposeRequest(BaseModel):
    dataset: Optional[str] = Field(None, description="dataset id from GET /api/datasets (default: best available)")
    zone: Optional[str] = Field("global", description="preset zone name (GET /api/options); ignored if bbox given")
    bbox: Optional[list[float]] = Field(None, description="[lat_min, lat_max, lon_min, lon_max]; lon_min > lon_max crosses the dateline")
    surface: Literal["all", "land", "ocean"] = "all"
    start: Optional[str | int] = Field(None, description="first frame: ISO date prefix ('2024-06') or frame index")
    end: Optional[str | int] = Field(None, description="last frame: ISO date prefix or frame index")
    variables: Optional[list[str]] = Field(None, description="subset of variables / instruments (default: all)")
    palette: str = Field("orchestra", description="'orchestra' (default), 'auto' (regional instruments) or a palette name")
    key: str = Field("C", description="tonic, e.g. C, D, F#, Bb")
    scale: Optional[Literal["major", "minor", "dorian", "lydian", "harmonic_minor"]] = None
    bpm: float = Field(76, ge=30, le=200)
    mapping: Literal["hybrid", "relative", "absolute"] = "hybrid"
    loop: bool = False
    audio: bool = Field(True, description="render audio with sampled instruments")
    audio_format: Literal["mp3", "wav"] = "mp3"
    reverb: float = Field(0.28, ge=0, le=0.8)
    include_notes: bool = Field(True, description="include every note in the response (set false for a lighter payload)")
    score_image: bool = Field(False, description="also produce a PNG of the score")

    @field_validator("bbox")
    @classmethod
    def _bbox(cls, v):
        if v is not None:
            if len(v) != 4:
                raise ValueError("bbox needs 4 numbers: [lat_min, lat_max, lon_min, lon_max]")
            if not (-90 <= v[0] < v[1] <= 90):
                raise ValueError("latitudes must satisfy -90 <= lat_min < lat_max <= 90")
            if not all(-180 <= x <= 180 for x in v[2:]):
                raise ValueError("longitudes must be within -180..180")
        return v

    @model_validator(mode="after")
    def _zone(self):
        if self.bbox is None and (self.zone or "global") not in ZONES:
            raise ValueError(f"unknown zone '{self.zone}'")
        return self


def cache_key(req: ComposeRequest, dataset_id: str, data_stamp) -> str:
    payload = req.model_dump(exclude={"include_notes"})
    payload["dataset"] = dataset_id
    if req.bbox is not None:
        payload["zone"] = None
    payload["_data"] = data_stamp
    payload["_v"] = API_VERSION
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def file_url(request: Request, comp_id: str, path: Path) -> str:
    rel = path.relative_to(CACHE_DIR / comp_id).as_posix()
    return str(request.url_for("get_file", comp_id=comp_id, file_path=rel))


def build_response(request: Request, comp_id: str, include_notes: bool, cached: bool, elapsed: float) -> dict:
    folder = CACHE_DIR / comp_id
    meta = json.loads((folder / "request.json").read_text())
    score = json.loads((folder / "score.json").read_text())
    if not include_notes:
        for inst in score["instruments"].values():
            inst.pop("notes", None)
    files = {"midi": file_url(request, comp_id, folder / "score.mid"),
             "score_json": file_url(request, comp_id, folder / "score.json"),
             "stems": {p.stem: file_url(request, comp_id, p) for p in sorted((folder / "score_stems").glob("*.mid"))},
             "audio": None, "score_image": None}
    for ext in ("mp3", "wav"):
        if (folder / f"score.{ext}").exists():
            files["audio"] = file_url(request, comp_id, folder / f"score.{ext}")
    if (folder / "score_score.png").exists():
        files["score_image"] = file_url(request, comp_id, folder / "score_score.png")
    return {"id": comp_id, "cached": cached, "elapsed_s": round(elapsed, 3), "request": meta,
            "files": files, "score": score}


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@app.get("/api/health")
def health():
    ds = scan_datasets()
    try:
        import tinysoundfont  # noqa: F401
        audio_ok = True
    except ImportError:
        audio_ok = False
    import shutil
    return {"status": "ok", "version": API_VERSION, "datasets": len(ds), "data_dir": str(DATA_DIR),
            "audio_rendering": audio_ok, "mp3_encoding": bool(shutil.which("ffmpeg"))}


@app.get("/api/options")
def options():
    """Everything the UI needs to build its menus."""
    orch = orchestra.ORCHESTRA
    return {
        "zones": [{"id": z, "bbox": list(b), "regional_palette": orchestra.ZONE_PALETTE.get(z, "orchestra")}
                  for z, b in ZONES.items()],
        "palettes": [{"id": n, "default_scale": orchestra.PALETTE_SCALE.get(n, "major"),
                      "zones": [z for z, p in orchestra.ZONE_PALETTE.items() if p == n],
                      "instruments": {v: (pal[v][1] if v in pal else orch[v]["name"].split(" - ")[1]) for v in orch}}
                     for n, pal in orchestra.PALETTES.items()],
        "orchestra": {v: {"name": c["name"], "role": c["role"], "program": c["program"],
                          "register": [c["low"], c["high"]]} for v, c in orch.items()},
        "scales": orchestra.SEVEN_NOTE_SCALES,
        "keys": ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"],
        "surfaces": ["all", "land", "ocean"],
        "mappings": ["hybrid", "relative", "absolute"],
        "defaults": ComposeRequest().model_dump(),
    }


@app.get("/api/datasets")
def datasets():
    ds = scan_datasets()
    return {"default": default_dataset(), "datasets": [public_dataset(g) for g in ds.values()]}


@app.post("/api/compose")
def compose(req: ComposeRequest, request: Request):
    ds = scan_datasets()
    ds_id = req.dataset or default_dataset()
    if not ds_id or ds_id not in ds:
        raise HTTPException(404, f"dataset '{req.dataset}' not found; see GET /api/datasets")
    group = ds[ds_id]
    if req.variables:
        unknown = set(req.variables) - set(group["variables"])
        if unknown:
            raise HTTPException(400, f"variables not in dataset: {sorted(unknown)}")
    stamp = [(f.name, f.stat().st_mtime) for f in group["files"]]
    key = cache_key(req, ds_id, stamp)
    folder = CACHE_DIR / key
    t0 = time.perf_counter()
    with lock_for(key):
        done = (folder / "score.json").exists() and (not req.audio or any(
            (folder / f"score.{e}").exists() for e in ("mp3", "wav")))
        if done:
            return build_response(request, key, req.include_notes, True, time.perf_counter() - t0)
        folder.mkdir(parents=True, exist_ok=True)
        start = int(req.start) if isinstance(req.start, str) and req.start.isdigit() else req.start
        end = int(req.end) if isinstance(req.end, str) and req.end.isdigit() else req.end
        try:
            orchestra.compose(
                [str(f) for f in group["files"]], zone=None if req.bbox else req.zone, bbox=req.bbox,
                surface=req.surface, start=start, end=end, loop=req.loop, mapping=req.mapping,
                key=req.key, scale=req.scale, bpm=req.bpm, palette=req.palette, only=req.variables,
                out_dir=folder, name="score", preview=req.score_image, render=req.audio,
                reverb=req.reverb, audio_format=req.audio_format)
        except orchestra.ComposeError as e:
            raise HTTPException(400, str(e))
        (folder / "score_preview.wav").unlink(missing_ok=True)  # rough synth preview not needed here
        (folder / "request.json").write_text(json.dumps({**req.model_dump(), "dataset": ds_id}, default=str))
        return build_response(request, key, req.include_notes, False, time.perf_counter() - t0)


@app.get("/api/compositions/{comp_id}")
def get_composition(comp_id: str, request: Request, include_notes: bool = True):
    if not (CACHE_DIR / comp_id / "score.json").exists() or "/" in comp_id or ".." in comp_id:
        raise HTTPException(404, "composition not found")
    return build_response(request, comp_id, include_notes, True, 0.0)


@app.get("/api/files/{comp_id}/{file_path:path}", name="get_file")
def get_file(comp_id: str, file_path: str):
    base = (CACHE_DIR / comp_id).resolve()
    target = (base / file_path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "file not found")
    media = {".mid": "audio/midi", ".mp3": "audio/mpeg", ".wav": "audio/wav",
             ".json": "application/json", ".png": "image/png"}.get(target.suffix, "application/octet-stream")
    return FileResponse(target, media_type=media, filename=target.name)
