"""
Vinyl mode: the needle moves through SPACE at one fixed date.

A groove is an ordered list of places (the path the needle crosses). Each place is read as the mean of the map
over a small disc around it (the needle's width), at one date: a month ('2018-07', the mean of its frames) or a
whole year ('2018', the mean of its months). The result is one value per place and per variable: a series in
space, which the composer plays like a series in time (one place = one bar, see orchestra.compose(series_in=...)).

Reading a date means decompressing the map's whole history once; the map of that date is then kept in memory,
so a vinyl session (many requests at the same date) reads each map only once.
"""
from __future__ import annotations

import os
import threading
import warnings
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import numpy as np

from zone_to_midi import load_map

PERIOD_CACHE_SIZE = 64          # (map, date) pairs kept: one 1 deg map is 260 kB
_period_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
_period_lock = threading.Lock()


def _frames_of(times, period: str) -> np.ndarray:
    """Indices of the frames whose date starts with `period` ('2018-07' -> that month, '2018' -> that year)."""
    return np.array([i for i, t in enumerate(times) if str(t).startswith(period)], dtype=np.int64)


def period_map(path, period: str) -> Optional[tuple]:
    """(map [lat, lon] at `period`, lat, lon, meta) for one map file, or None when the file has no frame at that
    date. Several frames (a year of months, a month of days) are averaged, NaN ignored. Cached per (file, date)."""
    p = Path(path)
    st = os.stat(p)
    key = (os.path.realpath(p), st.st_mtime_ns, st.st_size, period)
    with _period_lock:
        hit = _period_cache.get(key)
        if hit is not None:
            _period_cache.move_to_end(key)
            return hit
    z, meta = load_map(str(p))
    with z:
        if "time" not in z.files:
            return None
        idx = _frames_of(z["time"], period)
        if not len(idx):
            return None
        data = z["data"]
        with warnings.catch_warnings():                       # all-NaN cells (no data there) stay NaN
            warnings.simplefilter("ignore", RuntimeWarning)
            field = np.nanmean(data[idx].astype(np.float64), axis=0)
        out = (field, np.asarray(z["lat"], dtype=np.float64), np.asarray(z["lon"], dtype=np.float64), meta)
    with _period_lock:
        _period_cache[key] = out
        while len(_period_cache) > PERIOD_CACHE_SIZE:
            _period_cache.popitem(last=False)
    return out


def disc_mean(field: np.ndarray, lat: np.ndarray, lon: np.ndarray, lat0: float, lng0: float,
              radius_deg: float) -> float:
    """Mean of `field` over the cells whose centre is within `radius_deg` of (lat0, lng0): cos-latitude
    weighted, NaN ignored, longitudes wrapping at the dateline. A disc narrower than the grid reads the nearest
    cell. NaN when there is no data under the needle."""
    dlat = lat - lat0
    dlon = (lon - lng0 + 180.0) % 360.0 - 180.0              # shortest way round
    near_rows = np.abs(dlat) <= radius_deg
    d2 = dlat[near_rows, None] ** 2 + (dlon[None, :] * np.cos(np.deg2rad(lat0))) ** 2
    rows, cols = np.nonzero(d2 <= radius_deg ** 2)
    rows = np.flatnonzero(near_rows)[rows]
    if not len(rows):                                         # needle thinner than a cell: the nearest cell
        rows, cols = np.array([np.argmin(np.abs(dlat))]), np.array([np.argmin(np.abs(dlon))])
    v = field[rows, cols]
    w = np.cos(np.deg2rad(lat[rows]))
    ok = np.isfinite(v)
    if not ok.any():
        return float("nan")
    return float(np.sum(v[ok] * w[ok]) / np.sum(w[ok]))


def groove_series(files: dict, period: str, path: list[tuple[float, float]], radius_deg: float) -> dict:
    """{variable: (values [len(path)], meta)} for the maps `files` = {variable: path of its map}.
    A variable with no frame at `period`, or no data anywhere along the path, is left out."""
    out = {}
    for var, f in files.items():
        got = period_map(f, period)
        if got is None:
            continue
        field, lat, lon, meta = got
        vals = np.array([disc_mean(field, lat, lon, la, lo, radius_deg) for la, lo in path])
        if np.isfinite(vals).any():
            out[var] = (vals, meta)
    return out
