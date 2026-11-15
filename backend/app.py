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
  JUKEBOX_FULL_API    0 = only the frontend's calls (FRONTEND_PATHS) are served  default: 1
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
import zlib
from collections import OrderedDict
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
import groove  # noqa: E402
import orchestra  # noqa: E402
import eras as genres_mod  # noqa: E402
import soundscape  # noqa: E402
from zone_to_midi import ZONES  # noqa: E402

DATA_DIR = Path(os.environ.get("JUKEBOX_DATA_DIR", ROOT / "simulation" / "output"))
CACHE_DIR = Path(os.environ.get("JUKEBOX_CACHE_DIR", ROOT / "backend" / "cache"))
# datasets converted from the team's raw folders by backend/prepare_data.py (the data folder can be read-only)
BUILT_DIR = Path(os.environ.get("JUKEBOX_BUILT_DIR") or CACHE_DIR / "_datasets")
CORS = [o.strip() for o in os.environ.get("JUKEBOX_CORS", "*").split(",")]
API_VERSION = "2.9"
COMPOSITION_ID = re.compile(r"[0-9a-f]{16}")         # what cache_key() produces
# 1 = a request that names no dataset takes EACH variable from its own best dataset and the composer lines
# them up by date (temperature since 1880 with precipitation joining in 2000 ...). The Docker image sets it:
# there every dataset holds one variable. 0 (default) = one dataset per request, as before.
MERGE_DATASETS = os.environ.get("JUKEBOX_MERGE_DATASETS", "0").lower() not in ("", "0", "false", "no")
# 0 = only what the frontend uses is served (FRONTEND_PATHS): /api/instruments, /api/genres, /api/sonify, /api/zones/zone-data, /api/zones/series, /api/zones/groove (and /api/health).
# The Docker image sets it. 1 (default) = also the engine's own endpoints, for development and tests.
FULL_API = os.environ.get("JUKEBOX_FULL_API", "1").lower() not in ("", "0", "false", "no")
CHUNK_SECONDS = float(os.environ.get("JUKEBOX_CHUNK_SECONDS", "30"))     # length of one piece of POST /api/sonify
FRONTEND_PATHS = ("/api/health", "/api/instruments", "/api/genres", "/api/sonify", "/api/zones/zone-data",
                  "/api/zones/series", "/api/zones/groove")

app = FastAPI(title="Earth Information Jukebox: sonification API", version=API_VERSION,
              description="Turns NASA EIC intensity maps into an orchestra (MIDI + audio).")
app.add_middleware(CORSMiddleware, allow_origins=CORS, allow_methods=["*"], allow_headers=["*"])


# ----------------------------------------------------------------------------
# Datasets: groups of maps "<prefix>_<variable>.npz" sharing one time axis
# ----------------------------------------------------------------------------
_datasets_cache: dict = {"mtime": None, "data": {}}


def npz_shape(z, name: str) -> list[int]:
    """Shape of one array of an open .npz, read from its .npy header only: z[name].shape would load (and
    inflate) the whole array, ~360 MB for the deployed datasets on every rescan."""
    with z.zip.open(f"{name}.npy") as fh:
        major, _ = np.lib.format.read_magic(fh)
        read = np.lib.format.read_array_header_1_0 if major == 1 else np.lib.format.read_array_header_2_0
        return list(read(fh)[0])


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
        g = groups.setdefault(prefix, {"id": prefix, "variables": {}, "files": [], "file_of": {}})
        with np.load(f) as z:
            times = [str(t) for t in z["time"]] if "time" in z.files else []
            shape = npz_shape(z, "data")
        g["files"].append(f)
        g["file_of"][var] = f
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
    return {k: v for k, v in g.items() if k not in ("files", "file_of")}


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
    bpm: Optional[float] = Field(None, ge=30, le=200, description="tempo; default 76, or the usual tempo of the genre")
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
    genre: str = Field("orchestra", description="musical genre of the whole piece (GET /api/instruments -> genres): "
                       "orchestra (default), ragtime, swing, rocknroll, funk, synthpop, house, trap, or realistic "
                       "(recordings: rain, fire, thunder ...; the temperature is a heartbeat)")
    tempo_from_temperature: bool = Field(True, description="data style: each bar's tempo follows the temperature "
                                         "(x0.75 cold .. x1.25 hot; realistic mode: a heartbeat of 60-130 bpm); with "
                                         "duration_s the piece still lasts duration_s. false = one fixed tempo")
    temperature_sound: Literal["heartbeat", "clock"] = Field("heartbeat", description="realistic mode: the temperature "
                                                             "as a heartbeat or a ticking clock (its rate = the tempo)")
    eras: bool = Field(False, description="fun mode, music through the ages: each bar is played in the style of its date, "
                       "Romantic orchestra before 1910, ragtime, swing, rock'n'roll, funk & disco, synth-pop, house, "
                       "modern pop / trap from 2010 (data style only)")
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
            "orchestral_samples": sampler.vsco_available(),
            "realistic_sounds": soundscape.ambience_available()}


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
                style=req.style, quantize=req.quantize, markers=req.markers, audio_engine=req.audio_engine,
                eras=req.eras, genre=req.genre, tempo_from_temperature=req.tempo_from_temperature,
                temperature_sound=req.temperature_sound)
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
                    "smoke": "aerosol_smoke", "fire": "fires", "wildfires": "fires", "carbon_dioxide": "co2", "snow": "snowfall",
                    # the webapp's own ids (webapp/src/data/tracks.ts)
                    "night_lights": "nightlights", "co2_atmosphere": "co2"}
POINT_GRID = (1800, 3600)      # lat/lng points are placed on a 0.1 degree grid, then rescaled onto each data map
SHORT_IDS = {"surface_temperature": "temperature", "aerosol_dust": "dust", "aerosol_smoke": "smoke",
             "snowfall": "snow", "nightlights": "night_lights"}                    # variable -> dataset_id
ROLE_LABELS = {"timpani": "percussion"}
MAX_POINTS = 20000             # points of one POST /api/zones/series request


def display_units(info: dict) -> Optional[str]:
    """The units the frontend shows for a variable of scan_datasets() (GET /api/instruments, /api/zones/series)."""
    return "degC anomaly" if info.get("anomaly") else info.get("units")


def points_to_cells(points) -> list[list[int]]:
    """Selected lat/lng points -> the distinct [row, col] cells of the 0.1 degree POINT_GRID they fall in, sorted."""
    gh, gw = POINT_GRID
    lat = np.fromiter((p.lat for p in points), dtype=np.float64, count=len(points))
    lng = np.fromiter((p.lng for p in points), dtype=np.float64, count=len(points))
    rows = np.minimum(((90 - lat) * gh / 180).astype(np.int64), gh - 1)          # lat <= 90: never negative
    cols = np.minimum((((lng + 180) % 360) * gw / 360).astype(np.int64), gw - 1)
    flat = np.unique(rows * gw + cols)                                            # distinct, sorted by (row, col)
    return np.stack([flat // gw, flat % gw], axis=1).tolist()


MASK_BYTES = POINT_GRID[0] * POINT_GRID[1] // 8      # the POINT_GRID as bits: 810 000 bytes before compression


def mask_to_cells(mask: str) -> list[list[int]]:
    """`mask` of a request -> the [row, col] cells of POINT_GRID it selects, sorted (same as points_to_cells).

    mask = base64( zlib( packbits( POINT_GRID as booleans, row-major, row 0 = 90°N, col 0 = 180°W ) ) ).
    The webapp marks the 0.1° cell of each of its points: the same cells as sending the points, in a few kB
    instead of megabytes for an ocean (the browser's CompressionStream("deflate") writes this zlib format)."""
    try:
        packed = base64.b64decode(mask, validate=True)
        z = zlib.decompressobj()
        bits = z.decompress(packed, MASK_BYTES + 1)                # never inflates more than the grid
    except (ValueError, zlib.error) as e:
        raise HTTPException(422, f"mask is not base64 of zlib data: {e}")
    if len(bits) != MASK_BYTES or not z.eof:
        raise HTTPException(422, f"mask must hold exactly {POINT_GRID[0]} x {POINT_GRID[1]} bits "
                                 f"({MASK_BYTES} bytes once inflated), got {len(bits)} bytes")
    cells = np.argwhere(np.unpackbits(np.frombuffer(bits, dtype=np.uint8)).reshape(POINT_GRID))
    if not len(cells):
        raise HTTPException(422, "mask selects no cell")
    return cells.tolist()


def request_cells(req) -> Optional[list[list[int]]]:
    """The POINT_GRID cells of a request's area, from `mask` (preferred) or `points`; None when it has neither."""
    if getattr(req, "mask", None):
        return mask_to_cells(req.mask)
    if req.points:
        return points_to_cells(req.points)
    return None


GENRE_INFO = {   # id -> what the frontend shows in its genre menu
    "orchestra": "The orchestra: piano chords, contrabass, violins, glockenspiel, timpani, trombones",
    "ragtime": "Honky-tonk stride piano, tuba, clarinet, brushes",
    "swing": "Jazz guitar, walking bass, trumpet, vibraphone, ride cymbal, swung eighths",
    "rocknroll": "Electric guitar, electric bass, tenor sax, backbeat drums",
    "funk": "Clavinet, octave bass, overdriven guitar, Hammond organ, disco drums",
    "synthpop": "Polysynth, synth bass, square lead, synth brass, drum machine",
    "house": "Off-beat piano and bass, saw lead, four-on-the-floor",
    "trap": "Electric-piano arpeggios, 808 bass, synth lead, hi-hat rolls",
    "realistic": "No instruments: the sound of each phenomenon (rain, fire, thunder, traffic ...), the temperature as a heartbeat",
}


def resolve_genre(genre: Optional[str]) -> str:
    g = (genre or "orchestra").strip().lower()
    if g in ("realistic", "realiste", "réaliste", "nature"):
        return "realistic"
    k = genres_mod.genre_key(g)
    if k is None:
        raise HTTPException(400, f"unknown genre '{genre}'; available: {list(GENRE_INFO)}")
    return k


def genres_list() -> list:
    return [{"id": k, "name": genres_mod.GENRES[k]["name"] if k in genres_mod.GENRES else "Realistic sounds",
             "bpm": genres_mod.GENRES[k]["bpm"] if k in genres_mod.GENRES else 76, "description": d}
            for k, d in GENRE_INFO.items()]


def instrument_for(genre: str, var: str, cfg: dict) -> str:
    """The instrument (or sound) that plays `var` in `genre`."""
    orig = cfg["name"].partition(" - ")[2]
    if genre == "realistic":
        return soundscape.SOUND_NAMES.get(var, orig)
    if genre == "orchestra":
        return orig
    return genres_mod.instrument_name(genre, var, cfg["role"], orig, cfg["program"])


@app.get("/api/instruments")
def instruments(genre: Optional[str] = None):
    """The datasets the frontend can ask for and the instrument that plays each one (in `genre`, default the
    orchestra), with the list of genres."""
    gkey = resolve_genre(genre)
    ds = scan_datasets()
    rows = []
    for var, cfg in orchestra.ORCHESTRA.items():
        holders = [g for g in ds.values() if var in g["variables"]]
        if not holders:
            continue                                              # no data for this instrument on this server
        label, _, instrument = cfg["name"].partition(" - ")
        instrument = instrument_for(gkey, var, cfg)
        best = ds[pick_dataset([var])]["variables"][var]
        times = sorted(x for g in holders for x in g.get("times") or [])
        n = 7 if all(len({x[:7] for x in g.get("times") or []}) == len(g.get("times") or []) for g in holders) else 10
        rows.append({"dataset_id": SHORT_IDS.get(var, var), "name": label, "instrument": instrument,
                     "units": display_units(best),
                     "first_time_period": times[0][:n] if times else None,
                     "last_time_period": times[-1][:n] if times else None,
                     # what the webapp's InstrumentInfo declares (webapp/src/services/sonificationApi.ts)
                     "midi_program": cfg["program"], "pitch_range": [cfg["low"], cfg["high"]],
                     "role": ROLE_LABELS.get(cfg["role"], cfg["role"]), "always_playing": bool(cfg.get("always_on"))})
    out = {"palette": "orchestra", "by_dataset": {r["dataset_id"]: r["instrument"] for r in rows}, "instruments": rows}
    return {**out, "genre": gkey} if genre else out          # without ?genre= the answer is unchanged


@app.get("/api/genres")
def genres():
    """The genres the frontend can offer (send one as `genre` in POST /api/sonify or /api/zones/zone-data)."""
    return {"default": "orchestra", "genres": genres_list()}


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
    mask: Optional[str] = Field(None, description="the selected area as a compressed bitmap of the 0.1 degree grid, "
                                "instead of points (see mask_to_cells / FRONTEND_API.md)")
    time_period: Optional[str | int] = Field(None, description="where the music starts: '1880-01'")
    aggregation: Literal["mean"] = Field("mean", description="how the points are combined: the area-weighted mean")
    genre: Optional[str] = Field(None, description="optional musical genre for the whole piece: orchestra (default), "
                                 "ragtime, swing, rocknroll, funk, synthpop, house, trap, or realistic (sounds of rain, "
                                 "fire, thunder ...). See GET /api/instruments -> genres. The answer then has `genre`")
    eras: bool = Field(False, description="optional fun mode, music through the ages: the music takes the style of "
                       "the date (Romantic orchestra in 1880 ... modern pop / trap in 2026). The answer then has `era`")


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
    cells = request_cells(req)
    if cells is not None:
        area = {"cells": cells, "cells_grid": list(POINT_GRID)}
    elif (req.zone_id or "").lower() in ZONES:
        area = {"zone": req.zone_id.lower()}
    else:
        raise HTTPException(422, "points is empty: send the selected points [{lat, lng}, ...] or a mask")
    # pitch_range "series": a long series is played chunk after chunk, so a value must be the same note in each
    if req.eras:
        opts["eras"] = True
    if req.genre:
        opts["genre"] = resolve_genre(req.genre)
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
             for v, i in score["instruments"].items() if v not in ("anomaly_markers", "era_drums")}
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
            "point_count": len(req.points) if req.points else len(cells or []), "cell_count": score.get("cells"),
            "aggregation": req.aggregation, "time_end": end_time[:n] if end_time else None,
            "duration_s": score["duration_s"], "composition_id": out["id"], "cached": out["cached"],
            # which dataset of GET /api/datasets was played (the main variable's, when several were merged)
            "source_dataset": used.get(main) or out["request"]["dataset"],
            # {variable: dataset} when several were merged; only the variables that are in the piece
            "source_datasets": {v: k for v, k in used.items() if v in first} or None,
            "real_data": bool(info.get("real")),
            "anomaly": bool(info.get("anomaly")),                 # value = degC vs normal
            # eras mode: the musical era at the start of the chunk, and every era change inside it
            "genre": score.get("genre", "orchestra"),
            "era": score["eras"][0]["name"] if score.get("eras") else None,
            "eras": [{"name": e["name"], "time_period": (e["time"] or "")[:n] or None, "start_s": e["start_s"]}
                     for e in score["eras"]] if score.get("eras") else None}


@app.post("/api/sonify")
def sonify(req: SonifyRequest, request: Request):
    """The frontend's call: selected points + one time period in; CHUNK_SECONDS of MP3 (base64) out, with the
    time period to ask for next."""
    chunk = make_chunk(req, request)
    return {k: chunk[k] for k in SONIFY_FIELDS + (("era", "eras") if req.eras else ()) + (("genre",) if req.genre else ())}


class ZoneDataRequest(SonifyRequest):
    """The same request as the webapp sends it (webapp/src/services/sonificationApi.ts): a list of datasets."""
    dataset_ids: Optional[list[str]] = Field(None, description="dataset ids to combine into one piece")


def known_dataset_ids(sent: list[str]) -> tuple[dict, list[str]]:
    """{id: variable} for the ids the webapp sent, and the ids the server has data for (in the order sent).
    400 when some were sent and none is known."""
    known = {v for g in scan_datasets().values() for v in g["variables"]}
    variable_of = {d: VARIABLE_ALIASES.get(d.strip().lower(), d.strip().lower()) for d in sent}
    kept = [d for d in sent if variable_of[d] in known]
    if sent and not kept:
        ids = sorted(SHORT_IDS.get(v, v) for v in known)
        raise HTTPException(400, f"none of {sent} is available; available: {ids} (see GET /api/instruments)")
    return variable_of, kept


@app.post("/api/zones/zone-data")
def zone_data(req: ZoneDataRequest, request: Request):
    """POST /api/sonify under the path and with the field names of the webapp's code: `dataset_ids` in,
    `dataset_ids` + `values` out. Datasets the server has no data for are left out, not an error, as long as
    one remains; the answer says which ones are in the piece."""
    sent = list(req.dataset_ids or ([req.dataset_id] if isinstance(req.dataset_id, str) else req.dataset_id or []))
    variable_of, kept = known_dataset_ids(sent)
    chunk = make_chunk(req.model_copy(update={"dataset_id": kept or None}), request)
    sent_id = {variable_of[d]: d for d in reversed(kept)}                 # variable -> the id the webapp used
    values = {sent_id.get(v, SHORT_IDS.get(v, v)): x for v, x in chunk["values"].items() if x is not None}
    return {"zone_id": chunk["zone_id"], "dataset_ids": [sent_id.get(v, SHORT_IDS.get(v, v)) for v in chunk["values"]],
            "time_period": chunk["time_period"], "next_time_period": chunk["next_time_period"],
            "audio_data": chunk["audio_data"], "values": values, "point_count": chunk["point_count"],
            **({"era": chunk["era"], "eras": chunk["eras"]} if req.eras else {}),
            **({"genre": chunk["genre"]} if req.genre else {})}


# ----------------------------------------------------------------------------
# Frontend contract, vinyl mode: the needle moves through SPACE at one fixed date.
# The frontend sends the stretch of path the needle crosses next; one point = one bar, or one beat.
# ----------------------------------------------------------------------------
GROOVE_BAR_SECONDS = 3.0       # the same bar as the normal mode (30 s = 10 bars), whatever points_per_bar: pieces join
GROOVE_SNAP_DEG = 0.5          # places are snapped to this grid, so listeners crossing the same places share the cache
GROOVE_MAX_BARS = 20           # bars of one request (20 x 3 s = 60 s of music)
GROOVE_MAX_STOPS = GROOVE_MAX_BARS * 4      # points of one request at 4 points per bar
GROOVE_VERSION = 2             # bump when the groove music changes, so cached pieces are not reused


def _snap(v: float) -> float:
    return round(round(v / GROOVE_SNAP_DEG) * GROOVE_SNAP_DEG, 4)


class GrooveRequest(BaseModel):
    zone_id: Optional[str] = Field(None, description="the frontend's id of this stretch or record; echoed back")
    dataset_ids: Optional[list[str]] = Field(None, description="dataset ids of GET /api/instruments (default: all)")
    time_period: str = Field(description="the fixed date: '2018-07' (that month) or '2018' (the mean of the year)")
    path: list[SonifyPoint] = Field(min_length=1, max_length=GROOVE_MAX_STOPS,
                                    description="the places the needle crosses, in order: [{lat, lng}, ...]; "
                                    "a multiple of points_per_bar, at most "
                                    f"{GROOVE_MAX_BARS} bars of {GROOVE_BAR_SECONDS:g} s")
    points_per_bar: Literal[1, 2, 4] = Field(1, description="points of `path` per bar: 4 = one point per beat "
                                             f"({GROOVE_BAR_SECONDS / 4:g} s each), 40 points for 30 s")
    radius_deg: float = Field(1.5, ge=0.1, le=10, description="how wide the needle reads: each place is the mean "
                              "over a disc of this radius (degrees)")

    @field_validator("time_period")
    @classmethod
    def _period(cls, v):
        m = re.fullmatch(r"\s*(\d{4})(?:-(\d{1,2}))?\s*", str(v))
        if not m or (m.group(2) and not 1 <= int(m.group(2)) <= 12):
            raise ValueError("time_period must be a month 'YYYY-MM' or a year 'YYYY'")
        return m.group(1) + (f"-{int(m.group(2)):02d}" if m.group(2) else "")

    @model_validator(mode="after")
    def _whole_bars(self):
        n, k = len(self.path), self.points_per_bar
        if n % k:
            raise ValueError(f"path has {n} points: send a multiple of points_per_bar ({k}), whole bars")
        if n // k > GROOVE_MAX_BARS:
            raise ValueError(f"path has {n // k} bars: at most {GROOVE_MAX_BARS} "
                             f"({GROOVE_MAX_BARS * GROOVE_BAR_SECONDS:g} s)")
        return self


@app.post("/api/zones/groove")
def zone_groove(req: GrooveRequest, request: Request):
    """Vinyl mode: the music of a path in space at one date. Every point of `path` is one bar, or one beat with
    points_per_bar = 4 (half a bar with 2); the values are
    read on the needle (a disc of `radius_deg` around the place) and played on each dataset's GLOBAL scale, so
    a value is the same note wherever the needle is. Pieces have a fixed bar length (`bar_seconds`) and key,
    so consecutive stretches of the same groove join without a jump."""
    sent = list(req.dataset_ids or [])
    variable_of, kept = known_dataset_ids(sent)
    variables = [variable_of[d] for d in kept] or None
    sources = merged_sources(variables, req.time_period)
    if not sources:
        raise HTTPException(404, "no dataset on the server")
    ds = scan_datasets()
    files = {v: ds[k]["file_of"][v] for v, k in sources.items()}
    stops = [(max(-90.0, min(90.0, _snap(p.lat))), _snap((p.lng + 180) % 360 - 180)) for p in req.path]
    radius = round(req.radius_deg, 2)
    stamp = sorted((v, f.name, f.stat().st_mtime) for v, f in files.items())
    key = hashlib.sha1(json.dumps({"groove": GROOVE_VERSION, "period": req.time_period, "stops": stops,
                                   "radius": radius, "ppb": req.points_per_bar, "_data": stamp,
                                   "_v": API_VERSION}).encode()).hexdigest()[:16]
    folder = CACHE_DIR / key
    with lock_for(key):
        audio = next((folder / f"score.{e}" for e in ("mp3", "wav") if (folder / f"score.{e}").exists()), None)
        if not (audio and (folder / "score.json").exists() and (folder / "groove.json").exists()):
            series = groove.groove_series(files, req.time_period, stops, radius)
            if not series:
                raise HTTPException(400, f"no data at {req.time_period} along this path "
                                         f"for {sent or 'any dataset'} (see GET /api/instruments for the dates)")
            folder.mkdir(parents=True, exist_ok=True)
            lats, lngs = [s[0] for s in stops], [s[1] for s in stops]
            la0, la1 = max(-90.0, min(lats) - radius), min(90.0, max(lats) + radius)
            bbox = (la0, la1 if la1 > la0 else la0 + 1e-3, max(-180.0, min(lngs) - radius), min(180.0, max(lngs) + radius))
            try:
                orchestra.compose([], bbox=bbox, series_in=series, pitch_range="series",
                                  frames_per_bar=req.points_per_bar,
                                  bpm=60.0 * 4 / GROOVE_BAR_SECONDS, out_dir=folder, name="score",
                                  render=True, audio_format="mp3", audio_engine="auto")
            except orchestra.ComposeError as e:
                raise HTTPException(400, str(e))
            (folder / "score_preview.wav").unlink(missing_ok=True)
            (folder / "groove.json").write_text(json.dumps(
                {v: [None if not np.isfinite(x) else round(float(x), 4) for x in vals] for v, (vals, _) in series.items()}))
            audio = next((folder / f"score.{e}" for e in ("mp3", "wav") if (folder / f"score.{e}").exists()), None)
        if audio is None:
            raise HTTPException(503, "audio rendering is not available on the server (see GET /api/health)")
        score = json.loads((folder / "score.json").read_text())
        values = json.loads((folder / "groove.json").read_text())
    sent_id = {variable_of[d]: d for d in reversed(kept)}                 # variable -> the id the webapp used
    id_of = lambda v: sent_id.get(v, SHORT_IDS.get(v, v))  # noqa: E731
    playing = [v for v in score["instruments"] if v in values]
    return {"zone_id": req.zone_id, "time_period": req.time_period,
            "dataset_ids": [id_of(v) for v in playing],
            "audio_data": base64.b64encode(audio.read_bytes()).decode("ascii"),
            "duration_s": score["duration_s"], "bar_seconds": GROOVE_BAR_SECONDS,
            "points_per_bar": req.points_per_bar, "stop_seconds": GROOVE_BAR_SECONDS / req.points_per_bar,
            "radius_deg": radius,
            "stops": [{"lat": la, "lng": lo, "values": {id_of(v): values[v][i] for v in playing}}
                      for i, (la, lo) in enumerate(stops)]}


# ----------------------------------------------------------------------------
# Frontend contract: the whole monthly series of the selected points, for a chart (no audio).
# ----------------------------------------------------------------------------
class ZoneSeriesRequest(BaseModel):
    zone_id: Optional[str] = Field(None, description="the frontend's id of the area; echoed back")
    zone_name: Optional[str] = None
    dataset_ids: list[str] = Field(min_length=1, description="dataset ids of GET /api/instruments")
    points: Optional[list[SonifyPoint]] = Field(None, min_length=1, max_length=MAX_POINTS,
                                                description="the selected points [{lat, lng}, ...]")
    mask: Optional[str] = Field(None, description="the selected area as a compressed bitmap, instead of points "
                                "(no size limit: see POST /api/zones/zone-data)")
    aggregation: Literal["mean"] = Field("mean", description="how the points are combined: the area-weighted mean")

    @model_validator(mode="after")
    def _area_given(self):
        if not self.points and not self.mask:
            raise ValueError("send the selected points [{lat, lng}, ...] or a mask")
        return self


class _LRU:
    """A small thread-safe least-recently-used cache."""

    def __init__(self, size: int):
        self.size, self.data, self.lock = size, OrderedDict(), threading.Lock()
        self.hits = self.misses = 0

    def get(self, key):
        with self.lock:
            if key in self.data:
                self.data.move_to_end(key)
                self.hits += 1
                return self.data[key]
            self.misses += 1
            return None

    def put(self, key, value):
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)


SERIES_CACHE = _LRU(256)       # (sha1 of the cells, dataset, variable, file mtime) -> one entry of "series"


def _month_index(t: str) -> int:
    return int(t[:4]) * 12 + int(t[5:7]) - 1


def monthly_values(mean, times) -> tuple[str, list]:
    """Per-frame values -> ('YYYY-MM' of the first month, one value per month on a contiguous monthly axis).
    Frames of the same month (daily data) are averaged; a month with no finite value is None."""
    months = [str(t)[:7] for t in times]
    i0 = min(map(_month_index, months))
    n = max(map(_month_index, months)) - i0 + 1
    sums, counts = np.zeros(n), np.zeros(n)
    for m, v in zip(months, np.asarray(mean, dtype=np.float64)):
        if np.isfinite(v):
            sums[_month_index(m) - i0] += v
            counts[_month_index(m) - i0] += 1
    start = f"{i0 // 12:04d}-{i0 % 12 + 1:02d}"
    return start, [round(float(s / c), 4) if c else None for s, c in zip(sums, counts)]


def area_monthly_series(cells: list, digest: str, ds_id: str, var: str) -> Optional[dict]:
    """One entry of the "series" of POST /api/zones/series (cached). None when the dataset has no time axis."""
    g = scan_datasets()[ds_id]
    path = g["file_of"][var]
    key = (digest, ds_id, var, path.stat().st_mtime)
    hit = SERIES_CACHE.get(key)
    if hit is not None:
        return hit
    z, meta = orchestra.load_map(str(path))
    with z:
        if "time" not in z.files or not len(z["time"]):
            return None
        mean, _, times, _ = orchestra.area_series((z, meta), cells, POINT_GRID)
        start, values = monthly_values(mean, times)
    info = g["variables"][var]
    entry = {"units": display_units(info), "anomaly": bool(info.get("anomaly")), "start": start,
             # one map per year (night lights), repeated for its 12 months
             "yearly": meta.get("source_time_step") == "1 year" or var == "nightlights",
             "values": values}
    SERIES_CACHE.put(key, entry)
    return entry


def series_dataset(var: str) -> Optional[str]:
    """The dataset a chart of `var` is drawn from: the one covering the most MONTHS (a monthly series since
    2000 rather than a daily one of a single year), real data first. With one dataset per variable (the
    deployed server) this is the dataset POST /api/zones/zone-data plays."""
    ds = scan_datasets()
    holders = [k for k, g in ds.items() if var in g["variables"]]
    return max(holders, key=lambda k: (bool(ds[k]["variables"][var].get("real")),
                                       len({str(t)[:7] for t in ds[k].get("times") or []}), ds[k]["frames"]),
               default=None)


@app.post("/api/zones/series")
def zone_series(req: ZoneSeriesRequest):
    """The monthly series of each requested dataset over the selected points (the same area-weighted mean as
    the `values` of POST /api/zones/zone-data), for a chart. Unknown ids are listed in `missing`."""
    sent = list(dict.fromkeys(req.dataset_ids))
    variable_of, kept = known_dataset_ids(sent)
    cells = request_cells(req)
    digest = hashlib.sha1(json.dumps(cells).encode()).hexdigest()
    series, missing = {}, [d for d in sent if d not in kept]
    for d in kept:
        var = variable_of[d]
        entry = area_monthly_series(cells, digest, series_dataset(var), var)
        if entry is None:
            missing.append(d)
        else:
            series[d] = entry
    return {"zone_id": req.zone_id, "step": "month", "point_count": len(req.points) if req.points else len(cells),
            "series": series,
            "missing": missing}


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
