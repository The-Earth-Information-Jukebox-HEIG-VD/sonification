"""
Earth Information Jukebox: sonification backend (FastAPI).

The frontend picks a dataset, a zone (preset, list of array positions, or lat/lon box), a time window and a few musical
options; the backend composes the orchestra score and returns:
  * the score as JSON (bars, chords, instruments, notes, data values) for visualisation / playback
  * URLs to the MIDI file, per-instrument MIDI stems, and the rendered audio (MP3/WAV)

Run:   uvicorn backend.app:app --reload --port 8000        (from the repo root)
Docs:  http://localhost:8000/docs  (interactive OpenAPI)

Config (environment variables):
  JUKEBOX_DATA_DIR    folder with intensity maps (*.npz + *.json)   default: simulation/output
  JUKEBOX_CACHE_DIR   where compositions are written / cached        default: backend/cache
  JUKEBOX_BUILT_DIR   datasets built by backend/prepare_data.py      default: <cache>/_datasets
  JUKEBOX_MERGE_DATASETS  1 = each variable from its own dataset, lined up by date   default: 0
  JUKEBOX_FULL_API    0 = only the frontend's two calls (+ health) are served      default: 1
  JUKEBOX_CHUNK_SECONDS   length of one piece returned by POST /api/sonify         default: 30
  JUKEBOX_CORS        comma-separated allowed origins                default: *
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.routing import NoMatchFound
from pydantic import AliasChoices, BaseModel, Field, ValidationError, field_validator, model_validator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "music"))
import orchestra  # noqa: E402
from zone_to_midi import ZONES  # noqa: E402

DATA_DIR = Path(os.environ.get("JUKEBOX_DATA_DIR", ROOT / "simulation" / "output"))
CACHE_DIR = Path(os.environ.get("JUKEBOX_CACHE_DIR", ROOT / "backend" / "cache"))
# datasets converted from the team's raw folders by backend/prepare_data.py (the data folder can be read-only)
BUILT_DIR = Path(os.environ.get("JUKEBOX_BUILT_DIR") or CACHE_DIR / "_datasets")
CORS = [o.strip() for o in os.environ.get("JUKEBOX_CORS", "*").split(",")]
API_VERSION = "2.1"
COMPOSITION_ID = re.compile(r"[0-9a-f]{16}")         # what cache_key() produces
# 1 = a request that names no dataset takes EACH variable from its own best dataset and the composer lines
# them up by date (temperature since 1880 with precipitation joining in 2000 ...). The Docker image sets it:
# there every dataset holds one variable. 0 (default) = one dataset per request, as before.
MERGE_DATASETS = os.environ.get("JUKEBOX_MERGE_DATASETS", "0").lower() not in ("", "0", "false", "no")
# 0 = only what the frontend uses is served: GET /api/instruments, POST /api/sonify (and /api/health).
# The Docker image sets it. 1 (default) = also the engine's own endpoints, for development and tests.
FULL_API = os.environ.get("JUKEBOX_FULL_API", "1").lower() not in ("", "0", "false", "no")
CHUNK_SECONDS = float(os.environ.get("JUKEBOX_CHUNK_SECONDS", "30"))     # length of one piece of POST /api/sonify
FRONTEND_PATHS = ("/api/health", "/api/instruments", "/api/sonify", "/api/zones/zone-data")

app = FastAPI(title="Earth Information Jukebox: sonification API", version=API_VERSION,
              description="Turns NASA EIC intensity maps into an orchestra (MIDI + audio).")
app.add_middleware(CORSMiddleware, allow_origins=CORS, allow_methods=["*"], allow_headers=["*"])


# ----------------------------------------------------------------------------
# Datasets: groups of maps "<prefix>_<variable>.npz" sharing one time axis
# ----------------------------------------------------------------------------
_datasets_cache: dict = {"mtime": None, "data": {}}


def scan_datasets() -> dict:
    files = [f for d in dict.fromkeys((DATA_DIR, BUILT_DIR)) if d.exists() for f in sorted(d.glob("*.npz"))]
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
        g["variables"][var]["real"] = bool(meta.get("real", False))
        g["variables"][var]["anomaly"] = bool(meta.get("anomaly", False))    # e.g. temperature in degC vs a normal
        g["resolution_deg"] = meta.get("resolution_deg")
    for g in groups.values():
        g["real_variables"] = sorted(v for v, info in g["variables"].items() if info.get("real"))
        g["simulated"] = not g["real_variables"]
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
    # prefer the dataset with the most REAL variables (then the most variables, then the most frames)
    return max(ds, key=lambda k: (len(ds[k].get("real_variables", [])), len(ds[k]["variables"]), ds[k]["frames"]))


def pick_dataset(variables=None, start=None) -> Optional[str]:
    """The dataset for a request that names none. It must have every requested variable; among those,
    prefer one whose dates cover `start` (a date as text), then the most REAL variables of the requested ones,
    then the LONGEST series (a request for temperature alone gets 1880 -> today, not the few years a
    many-variable dataset covers), then the most variables.
    When no variable is requested (the whole orchestra): the most real variables, then the most variables,
    then the most frames; with no date either, this is default_dataset()."""
    ds = scan_datasets()
    want = set(variables or [])
    candidates = [k for k, g in ds.items() if want <= set(g["variables"])]
    if not candidates:
        return None

    def rank(k):
        g = ds[k]
        covers = True
        if isinstance(start, str) and g.get("time_start"):
            n = len(start)
            covers = g["time_start"][:n] <= start <= g["time_end"][:n]
        real = set(g.get("real_variables", []))
        if want:
            return covers, len(want & real), g["frames"], len(g["variables"])
        return covers, len(real), len(g["variables"]), g["frames"]
    return max(candidates, key=rank)


def merged_sources(variables=None, start=None) -> Optional[dict]:
    """{variable: dataset id}: every requested variable (or, with none requested, every variable the server
    has) from its own best dataset, see pick_dataset(). None when a requested variable exists nowhere."""
    ds = scan_datasets()
    known = {v for g in ds.values() for v in g["variables"]}
    wanted = list(variables) if variables else [v for v in orchestra.ORCHESTRA if v in known]
    sources = {v: pick_dataset([v], start) for v in wanted}
    return None if not sources or any(k is None for k in sources.values()) else sources


# ----------------------------------------------------------------------------
# Request / response models
# ----------------------------------------------------------------------------
class ComposeRequest(BaseModel):
    dataset: Optional[str] = Field(None, description="dataset id from GET /api/datasets (default: best available)")
    zone: Optional[str] = Field("global", description="preset zone name (GET /api/options); ignored if bbox given")
    bbox: Optional[list[float]] = Field(None, description="[lat_min, lat_max, lon_min, lon_max]; lon_min > lon_max crosses the dateline")
    cells: Optional[list[list[int]]] = Field(None, description="custom area of any shape: positions [[row, col], ...] on the data "
                                             "array (row 0 = north, col 0 = 180°W); overrides zone and bbox")
    cells_grid: Optional[list[int]] = Field(None, description="[rows, cols] of the array the positions refer to, when it is not "
                                            "the dataset grid (GET /api/datasets -> variables[v].grid); the selection is rescaled")
    surface: Literal["all", "land", "ocean"] = "all"
    start: Optional[str | int] = Field(None, description="first frame: date as text ('2024-06', '2024') or frame index (number)")
    end: Optional[str | int] = Field(None, description="last frame: date as text or frame index")
    year: Optional[int] = Field(None, ge=1800, le=2200, description="start date as numbers: year (instead of start)")
    month: Optional[int] = Field(None, ge=1, le=12, description="start date as numbers: month 1-12 (needs year)")
    duration_s: Optional[float] = Field(None, gt=0, le=600, description="seconds of music from the start date (instead of end); "
                                        "exact in data style, the tempo is nudged to fit")
    variables: Optional[list[str]] = Field(None, description="subset of variables / instruments (default: all)")
    palette: str = Field("orchestra", description="'orchestra' (default), 'auto' (regional instruments) or a palette name")
    key: str = Field("C", description="tonic, e.g. C, D, F#, Bb")
    scale: Optional[Literal["major", "minor", "dorian", "lydian", "harmonic_minor"]] = None
    bpm: float = Field(76, ge=30, le=200)
    frames_per_bar: Optional[float] = Field(None, gt=0, le=60, description="data frames per bar; default = dataset hint "
                                            "(daily data: 7 = one bar per week; monthly: 1)")
    mapping: Literal["hybrid", "relative", "absolute"] = "hybrid"
    pitch_range: Literal["window", "series"] = Field("window", description="lowest/highest note = lowest/highest value "
                                                     "of this piece (window) or of the whole series of the area (series)")
    loop: bool = False
    audio: bool = Field(True, description="render audio with sampled instruments")
    audio_format: Literal["mp3", "wav"] = "mp3"
    reverb: float = Field(0.28, ge=0, le=0.8)
    audio_engine: Literal["auto", "vsco", "sf2"] = Field("auto", description="auto: real orchestral samples "
                                                         "(VSCO 2 CE) when installed, otherwise the SoundFont")
    style: Literal["data", "musical"] = Field("data", description="data (default): every note follows the data "
                                              "(pitch = value, loudness/density = amount); musical: orchestral arrangement")
    quantize: Literal["scale", "pentatonic", "chromatic"] = Field("scale", description="data style pitch grid; chromatic = finest")
    markers: bool = Field(True, description="data style: triangle on unusually hot months, woodblock on cold ones")
    intro: bool = Field(True, description="musical style: 'meet the orchestra' intro")
    tension: bool = Field(True, description="musical style: anomalies -> minor mode, dissonance, agitation")
    climax: bool = Field(True, description="musical style: highlight the most anomalous moment")
    humanize: float = Field(0.5, ge=0, le=1, description="musical style: phrase dynamics + human timing")
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

    @field_validator("start", "end")
    @classmethod
    def _date(cls, v):
        """'2024-3' -> '2024-03' (dates are compared as text, so months and days need two digits)."""
        if isinstance(v, str):
            m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})(?:-(\d{1,2}))?\s*", v)
            if m:
                v = "-".join([m.group(1)] + [f"{int(g):02d}" for g in m.groups()[1:] if g])
        return v

    @field_validator("cells")
    @classmethod
    def _cells(cls, v):
        if v is not None:
            if not v:
                raise ValueError("cells is empty: send at least one [row, col] position")
            if any(len(p) != 2 or p[0] < 0 or p[1] < 0 for p in v):
                raise ValueError("every cell must be [row, col] with row >= 0 and col >= 0")
        return v

    @field_validator("cells_grid")
    @classmethod
    def _cells_grid(cls, v):
        if v is not None and (len(v) != 2 or v[0] < 1 or v[1] < 1):
            raise ValueError("cells_grid needs 2 positive numbers: [rows, cols]")
        return v

    @model_validator(mode="after")
    def _zone(self):
        if self.bbox is None and self.cells is None and (self.zone or "global") not in ZONES:
            raise ValueError(f"unknown zone '{self.zone}'")
        if self.cells is not None and self.cells_grid is not None:
            rows, cols = self.cells_grid
            if any(p[0] >= rows or p[1] >= cols for p in self.cells):
                raise ValueError(f"cells must be inside cells_grid ({rows} x {cols})")
        if self.month is not None and self.year is None:
            raise ValueError("month needs year")
        if self.year is not None:
            if self.start is not None:
                raise ValueError("send the start date either as start or as year/month, not both")
            self.start = f"{self.year:04d}" + (f"-{self.month:02d}" if self.month else "")
        return self


def frame_or_date(v):
    """Numbers and short digit strings ('12') are frame indices; a 4-digit string ('2024') is a year."""
    if isinstance(v, str) and v.isdigit() and len(v) != 4:
        return int(v)
    return v


def cache_key(req: ComposeRequest, dataset_id: str, data_stamp) -> str:
    payload = req.model_dump(exclude={"include_notes"})
    payload["dataset"] = dataset_id
    payload["year"] = payload["month"] = None                    # already folded into start
    if req.cells is not None:
        payload["zone"] = payload["bbox"] = None
        payload["cells"] = sorted(set(map(tuple, req.cells)))   # same area in another order = same piece
    else:
        payload["cells_grid"] = None
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
    try:
        return str(request.url_for("get_file", comp_id=comp_id, file_path=rel))
    except NoMatchFound:               # JUKEBOX_FULL_API=0: files are not served, the audio travels in the answer
        return None


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
    import sampler
    return {"status": "ok", "version": API_VERSION, "datasets": len(ds), "data_dir": str(DATA_DIR),
            "built_dir": str(BUILT_DIR), "preparing_data": (BUILT_DIR / ".preparing").exists(),
            "merge_datasets": MERGE_DATASETS, "full_api": FULL_API,
            "audio_rendering": audio_ok, "mp3_encoding": bool(shutil.which("ffmpeg")),
            "orchestral_samples": sampler.vsco_available()}


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
    sources = merged_sources(req.variables, frame_or_date(req.start)) if MERGE_DATASETS and not req.dataset else None
    if sources and len(set(sources.values())) > 1:
        # each variable from its own dataset; the composer lines them up by date
        files = [f for v, k in sources.items() for f in ds[k]["files"] if f.stem == f"{k}_{v}"]
        ds_id, only = " + ".join(sorted(set(sources.values()))), list(sources)
    else:
        sources = None
        ds_id = req.dataset or pick_dataset(req.variables, frame_or_date(req.start)) or default_dataset()
        if not ds_id or ds_id not in ds:
            raise HTTPException(404, f"dataset '{req.dataset}' not found; see GET /api/datasets")
        group = ds[ds_id]
        if req.variables:
            unknown = set(req.variables) - set(group["variables"])
            if unknown:
                raise HTTPException(400, f"variables not in dataset: {sorted(unknown)}")
        files, only = group["files"], req.variables
    stamp = [(f.name, f.stat().st_mtime) for f in files]
    key = cache_key(req, ds_id, stamp)
    folder = CACHE_DIR / key
    t0 = time.perf_counter()
    with lock_for(key):
        done = (folder / "score.json").exists() and (not req.audio or any(
            (folder / f"score.{e}").exists() for e in ("mp3", "wav")))
        if done:
            return build_response(request, key, req.include_notes, True, time.perf_counter() - t0)
        folder.mkdir(parents=True, exist_ok=True)
        start, end = frame_or_date(req.start), frame_or_date(req.end)
        try:
            orchestra.compose(
                [str(f) for f in files], zone=None if (req.bbox or req.cells) else req.zone,
                bbox=None if req.cells else req.bbox, cells=req.cells, cells_grid=req.cells_grid if req.cells else None,
                duration_s=req.duration_s, pitch_range=req.pitch_range,
                surface=req.surface, start=start, end=end, loop=req.loop, mapping=req.mapping,
                key=req.key, scale=req.scale, bpm=req.bpm, palette=req.palette, only=only,
                frames_per_bar=req.frames_per_bar,
                out_dir=folder, name="score", preview=req.score_image, render=req.audio,
                reverb=req.reverb, audio_format=req.audio_format,
                intro=req.intro, tension=req.tension, climax=req.climax, humanize=req.humanize,
                style=req.style, quantize=req.quantize, markers=req.markers, audio_engine=req.audio_engine)
        except orchestra.ComposeError as e:
            raise HTTPException(400, str(e))
        (folder / "score_preview.wav").unlink(missing_ok=True)  # rough synth preview not needed here
        echo = {**req.model_dump(exclude={"cells"}), "dataset": ds_id,      # the positions are not echoed back,
                "n_cells": len(req.cells) if req.cells else None,             # only how many were sent
                "datasets": sources}       # {variable: dataset} when the variables come from several datasets
        (folder / "request.json").write_text(json.dumps(echo, default=str))
        return build_response(request, key, req.include_notes, False, time.perf_counter() - t0)


# ----------------------------------------------------------------------------
# Frontend contract: selected points + one time period -> a chunk of audio inside the JSON.
# The frontend plays the chunk, then asks again with `next_time_period`.
# ----------------------------------------------------------------------------
VARIABLE_ALIASES = {"temperature": "surface_temperature", "temp": "surface_temperature",
                    "rain": "precipitation", "precip": "precipitation", "dust": "aerosol_dust",
                    "smoke": "aerosol_smoke", "fire": "aerosol_smoke", "carbon_dioxide": "co2", "snow": "snowfall",
                    # the webapp's own ids (webapp/src/data/tracks.ts)
                    "night_lights": "nightlights", "co2_atmosphere": "co2"}
POINT_GRID = (1800, 3600)      # lat/lng points are placed on a 0.1 degree grid, then rescaled onto each data map
SHORT_IDS = {"surface_temperature": "temperature", "aerosol_dust": "dust", "aerosol_smoke": "smoke",
             "snowfall": "snow", "nightlights": "night_lights"}                    # variable -> dataset_id
ROLE_LABELS = {"timpani": "percussion"}


@app.get("/api/instruments")
def instruments():
    """The datasets the frontend can ask for and the instrument that plays each one."""
    ds = scan_datasets()
    rows = []
    for var, cfg in orchestra.ORCHESTRA.items():
        holders = [g for g in ds.values() if var in g["variables"]]
        if not holders:
            continue                                              # no data for this instrument on this server
        label, _, instrument = cfg["name"].partition(" - ")
        best = ds[pick_dataset([var])]["variables"][var]
        times = sorted(x for g in holders for x in g.get("times") or [])
        n = 7 if all(len({x[:7] for x in g.get("times") or []}) == len(g.get("times") or []) for g in holders) else 10
        rows.append({"dataset_id": SHORT_IDS.get(var, var), "name": label, "instrument": instrument,
                     "units": ("degC anomaly" if best.get("anomaly") else best.get("units")),
                     "first_time_period": times[0][:n] if times else None,
                     "last_time_period": times[-1][:n] if times else None,
                     # what the webapp's InstrumentInfo declares (webapp/src/services/zoneApi.ts)
                     "midi_program": cfg["program"], "pitch_range": [cfg["low"], cfg["high"]],
                     "role": ROLE_LABELS.get(cfg["role"], cfg["role"]), "always_playing": bool(cfg.get("always_on"))})
    return {"palette": "orchestra", "by_dataset": {r["dataset_id"]: r["instrument"] for r in rows}, "instruments": rows}


class SonifyPoint(BaseModel):
    lat: float = Field(ge=-90, le=90, validation_alias=AliasChoices("lat", "latitude"))
    lng: float = Field(ge=-180, le=360, validation_alias=AliasChoices("lng", "lon", "longitude"))
    x: Optional[float] = Field(None, description="position on the frontend's array; accepted, not used")
    y: Optional[float] = None


class SonifyRequest(BaseModel):
    """What the frontend sends. Nothing else is needed."""
    zone_id: Optional[str] = Field(None, description="the frontend's id of the area; echoed back")
    zone_name: Optional[str] = None
    dataset_id: Optional[str | list[str]] = Field(None, description="a dataset_id of GET /api/instruments, or a list of "
                                                  "them to hear several instruments together")
    points: Optional[list[SonifyPoint]] = Field(None, description="the selected points: [{lat, lng, x, y}, ...]")
    time_period: Optional[str | int] = Field(None, description="where the music starts: '1880-01'")
    aggregation: Literal["mean"] = Field("mean", description="how the points are combined: the area-weighted mean")


class SonifyDebugRequest(SonifyRequest):
    """Development only (POST /api/sonify/debug): the same request with the engine's knobs."""
    duration_s: float = Field(CHUNK_SECONDS, gt=0, le=600)
    audio_format: Literal["mp3", "wav"] = "mp3"
    embed_audio: bool = True
    options: Optional[dict] = Field(None, description="any field of POST /api/compose (style, bpm, key, dataset, ...)")


SONIFY_FIELDS = ("zone_id", "dataset_id", "time_period", "next_time_period", "audio_data", "value", "point_count")


def make_chunk(req: SonifyRequest, request: Request, duration_s: float = CHUNK_SECONDS, audio_format: str = "mp3",
               embed_audio: bool = True, options: Optional[dict] = None) -> dict:
    """One chunk of music for the selected points, starting at req.time_period. Returns everything known about
    it; POST /api/sonify keeps SONIFY_FIELDS."""
    opts = dict(options or {})
    ds = scan_datasets()
    if not ds:
        raise HTTPException(404, "no dataset on the server")
    known = set().union(*(g["variables"] for g in ds.values()))
    # which instruments
    wanted = req.dataset_id if isinstance(req.dataset_id, list) else ([req.dataset_id] if req.dataset_id else [])
    variables = []
    for d in wanted:
        k = VARIABLE_ALIASES.get(d.strip().lower(), d.strip().lower())
        if k in ("all", "orchestra"):
            variables = []
            break
        if k not in known:
            ids = sorted(SHORT_IDS.get(v, v) for v in known)
            raise HTTPException(400, f"unknown dataset_id '{d}'; available: {ids} (see GET /api/instruments)")
        variables.append(k)
    # which area
    if req.points:
        gh, gw = POINT_GRID
        cells = sorted({(min(int((90 - p.lat) * gh / 180), gh - 1), min(int(((p.lng + 180) % 360) * gw / 360), gw - 1))
                        for p in req.points})
        area = {"cells": [list(c) for c in cells], "cells_grid": list(POINT_GRID)}
    elif (req.zone_id or "").lower() in ZONES:
        area = {"zone": req.zone_id.lower()}
    else:
        raise HTTPException(422, "points is empty: send the selected points [{lat, lng}, ...]")
    # pitch_range "series": a long series is played chunk after chunk, so a value must be the same note in each
    body = {"pitch_range": "series", **opts, **area, "variables": variables or None, "duration_s": duration_s,
            "audio": True, "audio_format": audio_format, "include_notes": False}
    if req.time_period is not None:
        body["start"] = str(req.time_period)
    try:
        creq = ComposeRequest(**body)
    except ValidationError as e:
        raise HTTPException(422, [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()])
    if creq.dataset is None and not MERGE_DATASETS:   # the dataset that has these variables and, if possible, covers the date
        creq.dataset = pick_dataset(variables, frame_or_date(creq.start))
        if creq.dataset is None:
            where = {v: sorted(k for k, g in ds.items() if v in g["variables"]) for v in variables}
            raise HTTPException(400, f"no single dataset has all of {variables}; they are in {where}")
    out = compose(creq, request)
    used = out["request"].get("datasets") or {}         # {variable: dataset} when several datasets were merged
    group = ds.get(out["request"]["dataset"], {})
    score, folder = out["score"], CACHE_DIR / out["id"]
    audio = next((folder / f"score.{e}" for e in ("mp3", "wav") if (folder / f"score.{e}").exists()), None)
    if audio is None:
        raise HTTPException(503, "audio rendering is not available on the server (see GET /api/health)")
    # the period right after this chunk, in the same form as time_period ('2024-03', or a day for daily data)
    times = sorted({x for k in set(used.values()) for x in ds[k].get("times") or []}) if used \
        else (group.get("times") or [])
    n = 7 if len({t[:7] for t in times}) == len(times) else 10
    end_time = score.get("time_last_frame") or score["time_end"]       # the last frame played, not the start of the last bar
    last = times.index(end_time) if end_time in times else None
    nxt = times[last + 1][:n] if last is not None and last + 1 < len(times) else None
    first = {v: (i["values_per_beat"][0] if i.get("values_per_beat") else None, i.get("units"))
             for v, i in score["instruments"].items() if v != "anomaly_markers"}
    # the main variable: the first one asked for that is actually in the piece (a variable with no data in
    # this period is left out by the composer)
    main = next((v for v in variables if v in first), None) or next(iter(first), None)
    info = ds.get(used.get(main) or out["request"]["dataset"], {}).get("variables", {}).get(main, {}) if main else {}
    return {"zone_id": req.zone_id, "zone_name": req.zone_name, "dataset_id": req.dataset_id,
            "time_period": score["time_start"][:n] if score["time_start"] else None,
            "next_time_period": nxt,
            "audio_data": base64.b64encode(audio.read_bytes()).decode("ascii") if embed_audio else None,
            "audio_format": audio.suffix[1:], "audio_url": out["files"]["audio"],
            "value": first[main][0] if main else None, "units": first[main][1] if main else None,
            "values": {v: x[0] for v, x in first.items()},
            "point_count": len(req.points or []), "cell_count": score.get("cells"),
            "aggregation": req.aggregation, "time_end": end_time[:n] if end_time else None,
            "duration_s": score["duration_s"], "composition_id": out["id"], "cached": out["cached"],
            # which dataset of GET /api/datasets was played (the main variable's, when several were merged)
            "source_dataset": used.get(main) or out["request"]["dataset"],
            # {variable: dataset} when several were merged; only the variables that are in the piece
            "source_datasets": {v: k for v, k in used.items() if v in first} or None,
            "real_data": bool(info.get("real")),
            "anomaly": bool(info.get("anomaly"))}                 # value = degC vs normal


@app.post("/api/sonify")
def sonify(req: SonifyRequest, request: Request):
    """The frontend's call: selected points + one time period in; CHUNK_SECONDS of MP3 (base64) out, with the
    time period to ask for next."""
    chunk = make_chunk(req, request)
    return {k: chunk[k] for k in SONIFY_FIELDS}


class ZoneDataRequest(SonifyRequest):
    """The same request as the webapp sends it (webapp/src/services/zoneApi.ts): a list of datasets."""
    dataset_ids: Optional[list[str]] = Field(None, description="dataset ids to combine into one piece")


@app.post("/api/zones/zone-data")
def zone_data(req: ZoneDataRequest, request: Request):
    """POST /api/sonify under the path and with the field names of the webapp's code: `dataset_ids` in,
    `dataset_ids` + `values` out. Datasets the server has no data for are left out, not an error, as long as
    one remains; the answer says which ones are in the piece."""
    sent = list(req.dataset_ids or ([req.dataset_id] if isinstance(req.dataset_id, str) else req.dataset_id or []))
    known = {v for g in scan_datasets().values() for v in g["variables"]}
    variable_of = {d: VARIABLE_ALIASES.get(d.strip().lower(), d.strip().lower()) for d in sent}
    kept = [d for d in sent if variable_of[d] in known]
    if sent and not kept:
        ids = sorted(SHORT_IDS.get(v, v) for v in known)
        raise HTTPException(400, f"none of {sent} is available; available: {ids} (see GET /api/instruments)")
    chunk = make_chunk(req.model_copy(update={"dataset_id": kept or None}), request)
    sent_id = {variable_of[d]: d for d in reversed(kept)}                 # variable -> the id the webapp used
    values = {sent_id.get(v, SHORT_IDS.get(v, v)): x for v, x in chunk["values"].items() if x is not None}
    return {"zone_id": chunk["zone_id"], "dataset_ids": [sent_id.get(v, SHORT_IDS.get(v, v)) for v in chunk["values"]],
            "time_period": chunk["time_period"], "next_time_period": chunk["next_time_period"],
            "audio_data": chunk["audio_data"], "values": values, "point_count": chunk["point_count"]}


@app.post("/api/sonify/debug")
def sonify_debug(req: SonifyDebugRequest, request: Request):
    """Development only: the same chunk with every detail (datasets used, values of each instrument, ...)."""
    return make_chunk(req, request, req.duration_s, req.audio_format, req.embed_audio, req.options)


@app.get("/api/compositions/{comp_id}")
def get_composition(comp_id: str, request: Request, include_notes: bool = True):
    if not COMPOSITION_ID.fullmatch(comp_id) or not (CACHE_DIR / comp_id / "score.json").exists():
        raise HTTPException(404, "composition not found")
    return build_response(request, comp_id, include_notes, True, 0.0)


@app.get("/api/files/{comp_id}/{file_path:path}", name="get_file")
def get_file(comp_id: str, file_path: str):
    if not COMPOSITION_ID.fullmatch(comp_id):          # only compositions, nothing else kept in the cache folder
        raise HTTPException(404, "file not found")
    base = (CACHE_DIR / comp_id).resolve()
    target = (base / file_path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "file not found")
    media = {".mid": "audio/midi", ".mp3": "audio/mpeg", ".wav": "audio/wav",
             ".json": "application/json", ".png": "image/png"}.get(target.suffix, "application/octet-stream")
    return FileResponse(target, media_type=media, filename=target.name)


if not FULL_API:        # serve only what the frontend uses; everything else stays available as Python functions
    app.router.routes = [r for r in app.router.routes
                         if not getattr(r, "path", "").startswith("/api/") or r.path in FRONTEND_PATHS]
