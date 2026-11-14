#!/usr/bin/env python3
"""
Zone -> time series -> chords -> MIDI   (Earth Information Jukebox, music side)

Pipeline
  1. Load an intensity map (.npz + .json, see simulation/INTENSITY_MAP_SPEC.md)
  2. Select a zone (preset name or lat/lon box; optional land/ocean only)
  3. Area-weighted mean per frame  -> time series (one value per frame, NaN-safe)
  4. Resample to the number of musical steps (smooth interpolation between frames)
  5. Map each value to a pitch quantized to a musical scale
  6. Chordify: build a chord *in the key* on that scale degree
     (the data note stays the top voice, so the ear follows the data)
  7. Write one MIDI track per variable/instrument (+ JSON/CSV for the web player,
     optional piano-roll PNG and WAV preview)

Examples
  python music/zone_to_midi.py simulation/output/temperature_sim_none_12f_1deg.npz --zone europe
  python music/zone_to_midi.py MAP.npz --zone arctic --steps-per-frame 4 --scale minor --preview
  python music/zone_to_midi.py MAP.npz --bbox 45.8,47.8,5.9,10.5 --surface land     # Switzerland
  python music/zone_to_midi.py MAP.npz --list-zones

Mapping modes
  --mapping relative : zone's own min..max stretched over the pitch range (most melodic)
  --mapping absolute : fixed vmin..vmax from metadata (same °C = same note in every zone;
                       good for comparing zones / building the orchestra)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from midi_writer import MidiFile  # noqa: E402

# ----------------------------------------------------------------------------
# Zones: (lat_min, lat_max, lon_min, lon_max). lon_min > lon_max = crosses dateline
# ----------------------------------------------------------------------------
ZONES = {
    "global":         (-90, 90, -180, 180),
    "northern_hemi":  (0, 90, -180, 180),
    "southern_hemi":  (-90, 0, -180, 180),
    "tropics":        (-23.5, 23.5, -180, 180),
    "arctic":         (66.5, 90, -180, 180),
    "antarctica":     (-90, -60, -180, 180),
    "europe":         (35, 71, -25, 45),
    "switzerland":    (45.8, 47.8, 5.9, 10.5),
    "sahara":         (15, 32, -15, 35),
    "siberia":        (50, 72, 60, 180),
    "india":          (8, 35, 68, 90),
    "usa":            (25, 50, -125, -66),
    "amazon":         (-15, 5, -75, -45),
    "australia":      (-40, -10, 112, 155),
    "north_atlantic": (40, 65, -60, 0),
    "nino34":         (-5, 5, -170, -120),   # El Niño index region
    "pacific_center": (-30, 30, 160, -120),  # crosses the dateline
    "alps":           (44, 48.5, 5, 16),
    "japan":          (30, 46, 129, 146),
    "china":          (20, 45, 98, 123),
    "andes":          (-35, 5, -80, -60),
    "west_africa":    (4, 20, -18, 15),
    "caribbean":      (10, 25, -85, -60),
    "scotland_ireland": (51, 59, -11, -1),
}

SCALES = {
    "major":            [0, 2, 4, 5, 7, 9, 11],
    "minor":            [0, 2, 3, 5, 7, 8, 10],
    "dorian":           [0, 2, 3, 5, 7, 9, 10],
    "lydian":           [0, 2, 4, 6, 7, 9, 11],
    "pentatonic":       [0, 2, 4, 7, 9],
    "minor_pentatonic": [0, 3, 5, 7, 10],
    "harmonic_minor":   [0, 2, 3, 5, 7, 8, 11],
}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# One instrument per variable (General MIDI program, channel). Extend for the orchestra.
INSTRUMENTS = {   # same instruments as orchestra.py
    "surface_temperature": {"program": 0, "channel": 0, "name": "Temperature - Piano"},
    "temperature":         {"program": 0, "channel": 0, "name": "Temperature - Piano"},
    "precipitation":       {"program": 48, "channel": 1, "name": "Rain - Violins"},
    "aerosol_dust":        {"program": 73, "channel": 2, "name": "Dust - Flute"},
    "aerosol_smoke":       {"program": 60, "channel": 3, "name": "Smoke - French Horns"},
    "lightning":           {"program": 13, "channel": 4, "name": "Lightning - Xylophone"},
    "snowfall":            {"program": 9,  "channel": 6, "name": "Snow - Glockenspiel"},
    "co2":                 {"program": 43, "channel": 5, "name": "CO2 - Contrabass"},
}


def note_name(m: int) -> str:
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def parse_key(key: str) -> int:
    """'C' -> 0, 'F#' -> 6, 'Bb' -> 10"""
    key = key.strip()
    pc = NOTE_NAMES.index(key[0].upper())
    if key[1:] == "#":
        pc += 1
    elif key[1:] == "b":
        pc -= 1
    return pc % 12


# ----------------------------------------------------------------------------
# 1-3. Load + zone mean
# ----------------------------------------------------------------------------
def load_map(path: str):
    p = Path(path)
    z = np.load(p, allow_pickle=False)
    meta_path = p.with_suffix(".json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return z, meta


def zone_mask(lat, lon, bbox, land_fraction=None, surface="all"):
    la0, la1, lo0, lo1 = bbox
    lat_ok = (lat >= la0) & (lat <= la1)
    lon_ok = (lon >= lo0) & (lon <= lo1) if lo0 <= lo1 else (lon >= lo0) | (lon <= lo1)
    mask = lat_ok[:, None] & lon_ok[None, :]
    if surface != "all":
        if land_fraction is None:
            raise ValueError("map has no land_fraction; use --surface all")
        mask &= (land_fraction >= 0.5) if surface == "land" else (land_fraction < 0.5)
    if not mask.any():
        raise ValueError(f"zone {bbox} ({surface}) selects no grid cells")
    return mask


def cells_mask(shape, cells, land_fraction=None, surface="all", grid=None):
    """Mask from a list of positions on the data array instead of a lat/lon box.

    cells : [[row, col], ...] or an (N, 2) integer array (e.g. np.load("positions.npy")).
            row 0 = top of the array (north), col 0 = left edge (180°W), like the maps.
    grid  : (rows, cols) of the array the positions refer to, when it is not this map's own
            shape (e.g. positions picked on a 720 x 1440 picture, data on 180 x 360). The
            selection is then rescaled onto this map.
    """
    c = np.asarray(cells)
    if c.ndim != 2 or c.shape[1] != 2 or len(c) == 0:
        raise ValueError("cells must be a non-empty list of [row, col] positions")
    if not np.issubdtype(c.dtype, np.integer):
        if not (np.issubdtype(c.dtype, np.number) and np.all(c == np.floor(c))):
            raise ValueError("cell positions must be whole numbers [row, col]")
        c = c.astype(np.int64)
    h, w = int(shape[0]), int(shape[1])
    gh, gw = (int(grid[0]), int(grid[1])) if grid is not None else (h, w)
    if gh < 1 or gw < 1:
        raise ValueError("cells_grid must be [rows, cols], both positive")
    r, k = c[:, 0], c[:, 1]
    if r.min() < 0 or k.min() < 0 or r.max() >= gh or k.max() >= gw:
        raise ValueError(f"cell positions must be inside the {gh} x {gw} array "
                         f"(row 0..{gh - 1}, col 0..{gw - 1}); got rows {r.min()}..{r.max()}, cols {k.min()}..{k.max()}")
    if (gh, gw) == (h, w):
        mask = np.zeros((h, w), dtype=bool)
        mask[r, k] = True
    else:
        src = np.zeros((gh, gw), dtype=bool)
        src[r, k] = True
        # every cell of this map looks up the source cell under its centre (covers a finer map) ...
        rows = np.minimum(((np.arange(h) + 0.5) * gh / h).astype(int), gh - 1)
        cols = np.minimum(((np.arange(w) + 0.5) * gw / w).astype(int), gw - 1)
        mask = src[rows][:, cols]
        # ... and every position marks the cell it falls in (so a small selection survives a coarser map)
        mask[np.minimum(((r + 0.5) * h / gh).astype(int), h - 1),
             np.minimum(((k + 0.5) * w / gw).astype(int), w - 1)] = True
    if surface != "all":
        if land_fraction is None:
            raise ValueError("map has no land_fraction; use --surface all")
        mask &= (land_fraction >= 0.5) if surface == "land" else (land_fraction < 0.5)
    if not mask.any():
        raise ValueError(f"the selected cells ({surface}) contain no grid cells of this map")
    return mask


def zone_series(field, lat, mask):
    """Area-weighted (cos lat) mean and spatial std per frame, ignoring NaN."""
    w = np.cos(np.deg2rad(lat))[:, None] * np.ones(field.shape[1:])
    w = np.where(mask, w, 0.0)
    means, stds = [], []
    for f in field:
        ww = np.where(np.isnan(f), 0.0, w)
        s = ww.sum()
        if s == 0:
            means.append(np.nan); stds.append(np.nan); continue
        m = np.nansum(f * ww) / s
        means.append(m)
        stds.append(np.sqrt(np.nansum(ww * (f - m) ** 2) / s))
    return np.array(means), np.array(stds)


# ----------------------------------------------------------------------------
# 4. Resample (frames -> musical steps)
# ----------------------------------------------------------------------------
def resample(series, steps_per_frame: int, loop=False):
    """Smooth (Catmull-Rom) interpolation between frames. NaN kept as NaN."""
    n = len(series)
    if steps_per_frame <= 1 or n < 2:
        return series.copy()
    out = []
    last = n if loop else n - 1
    for i in range(last):
        p0 = series[(i - 1) % n] if (loop or i > 0) else series[i]
        p1, p2 = series[i], series[(i + 1) % n]
        p3 = series[(i + 2) % n] if (loop or i + 2 < n) else p2
        for k in range(steps_per_frame):
            t = k / steps_per_frame
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t**2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t**3))
    if not loop:
        out.append(series[-1])
    return np.array(out)


# ----------------------------------------------------------------------------
# 5-6. Pitch mapping + chordify
# ----------------------------------------------------------------------------
def scale_notes(key_pc: int, scale: str, low=21, high=108):
    iv = SCALES[scale]
    return [m for m in range(low, high + 1) if (m - key_pc) % 12 in iv]


def normalize(values, mode, vmin=None, vmax=None, invert=False):
    if mode == "absolute":
        lo, hi = vmin, vmax
    else:
        lo, hi = np.nanmin(values), np.nanmax(values)
    rng = hi - lo if hi > lo else 1.0
    n = np.clip((values - lo) / rng, 0, 1)
    return (1 - n) if invert else n, (lo, hi)


def build_chord(scale_list, idx, kind, voice="top", tension=0):
    """Diatonic chord on a scale index. kind: none|power|triad|seventh|ninth|auto.
    voice='top': data note is the highest voice (chord stacked below it).
    voice='root': data note is the root (chord stacked above it)."""
    if kind == "none":
        steps = [0]
    elif kind == "power":
        steps = [0, 4]                       # root + fifth (in diatonic steps)
    else:
        steps = [0, 2, 4]                    # triad: root, third, fifth
        extra = {"seventh": 1, "ninth": 2}.get(kind, tension if kind == "auto" else 0)
        if extra >= 1: steps.append(6)       # 7th
        if extra >= 2: steps.append(8)       # 9th
    if voice == "top":
        top = max(steps)
        steps = [s - top for s in steps]     # stack downward from the data note
    chord = []
    for s in steps:
        j = idx + s
        if 0 <= j < len(scale_list):
            chord.append(scale_list[j])
    return sorted(set(chord))


def series_to_events(values, stds, args, meta):
    vmin = meta.get("vmin", np.nanmin(values))
    vmax = meta.get("vmax", np.nanmax(values))
    norm, (lo, hi) = normalize(values, args.mapping, vmin, vmax, args.invert)

    key_pc = parse_key(args.key)
    sl = scale_notes(key_pc, args.scale)
    lo_i = next(i for i, m in enumerate(sl) if m >= args.low)
    hi_i = max(i for i, m in enumerate(sl) if m <= args.high)

    # spatial variability -> velocity (heterogeneous zone = louder/more intense)
    sv = stds[np.isfinite(stds)]
    s_lo, s_hi = (sv.min(), sv.max()) if len(sv) else (0, 1)

    events, prev_n = [], None
    for i, (v, n) in enumerate(zip(values, norm)):
        if not np.isfinite(v):
            events.append({"step": i, "value": None, "notes": [], "velocity": 0})
            prev_n = None
            continue
        idx = int(round(lo_i + n * (hi_i - lo_i)))
        # tension from rate of change (only used with --chord auto)
        d = abs(n - prev_n) if prev_n is not None else 0.0
        tension = 2 if d > args.tension_threshold * 2 else (1 if d > args.tension_threshold else 0)
        prev_n = n
        notes = build_chord(sl, idx, args.chord, args.voice, tension)
        if args.bass:
            notes = sorted(set(notes + [sl[idx] - 12 * args.bass_octaves]))
        if args.velocity == "variability" and np.isfinite(stds[i]) and s_hi > s_lo:
            vel = int(55 + 60 * (stds[i] - s_lo) / (s_hi - s_lo))
        elif args.velocity == "value":
            vel = int(50 + 70 * n)
        else:
            vel = 85
        events.append({"step": i, "value": float(v), "norm": float(n), "melody": sl[idx],
                       "melody_name": note_name(sl[idx]), "notes": notes, "velocity": vel,
                       "tension": tension})
    return events, (float(lo), float(hi))


# ----------------------------------------------------------------------------
# 7. Output
# ----------------------------------------------------------------------------
def write_midi(events, path, args, instrument, step_times):
    m = MidiFile(bpm=args.bpm)
    t = m.add_track(instrument["name"], instrument["program"], instrument["channel"])
    beat = args.beats_per_step
    i = 0
    while i < len(events):
        ev = events[i]
        if not ev["notes"]:
            i += 1; continue
        # merge identical consecutive chords into one sustained chord
        j = i + 1
        if args.sustain_repeats:
            while j < len(events) and events[j]["notes"] == ev["notes"]:
                j += 1
        dur = (j - i) * beat
        start = i * beat
        if args.style == "block":
            for p in ev["notes"]:
                t.note(p, start, dur * args.legato, ev["velocity"])
        elif args.style == "arpeggio":
            k, nn = 0, len(ev["notes"])
            sub = beat / max(nn, 1)
            pos = start
            while pos < start + dur - 1e-9:
                p = ev["notes"][k % nn] if (k // nn) % 2 == 0 else ev["notes"][nn - 1 - k % nn]
                t.note(p, pos, sub * 0.95, ev["velocity"] - (0 if k % nn == 0 else 10))
                pos += sub; k += 1
        elif args.style == "broken":  # bass on beat, chord on the off-beat
            low, rest = ev["notes"][0], ev["notes"][1:] or ev["notes"]
            pos = start
            while pos < start + dur - 1e-9:
                t.note(low, pos, beat * 0.5, ev["velocity"])
                for p in rest:
                    t.note(p, pos + beat * 0.5, beat * 0.45, ev["velocity"] - 12)
                pos += beat
        if step_times is not None and args.markers:
            t.marker(str(step_times[i])[:10], start)
        i = j
    m.save(path)


def write_piano_roll(events, values, path, title, beat):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [1, 2]})
    x = np.arange(len(values)) * beat
    ax1.plot(x, values, color="#d1495b", lw=2)
    ax1.set_ylabel("zone mean"); ax1.set_title(title); ax1.grid(alpha=0.3)
    for ev in events:
        for p in ev["notes"]:
            is_mel = p == ev.get("melody")
            ax2.add_patch(plt.Rectangle((ev["step"] * beat, p - 0.4), beat * 0.95, 0.8,
                                        color="#d1495b" if is_mel else "#30638e",
                                        alpha=0.4 + 0.6 * ev["velocity"] / 127))
    allp = [p for ev in events for p in ev["notes"]] or [60]
    ax2.set_ylim(min(allp) - 2, max(allp) + 2); ax2.set_xlim(0, len(values) * beat)
    ticks = [p for p in range(min(allp) - 2, max(allp) + 3) if p % 12 == 0]
    ax2.set_yticks(ticks); ax2.set_yticklabels([note_name(p) for p in ticks])
    ax2.set_xlabel("beats"); ax2.set_ylabel("pitch (red = data note)"); ax2.grid(alpha=0.2)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def render_wav(events, path, args, sr=22050):
    """Quick piano-ish additive synth preview (not a real piano, just to hear the shape)."""
    import wave
    spb = 60 / args.bpm
    dur_step = args.beats_per_step * spb
    total = int((len(events) * dur_step + 2.5) * sr)
    out = np.zeros(total)
    for ev in events:
        if not ev["notes"]:
            continue
        start = int(ev["step"] * dur_step * sr)
        L = int((dur_step * args.legato + 1.2) * sr)
        tt = np.arange(L) / sr
        for p in ev["notes"]:
            f = 440 * 2 ** ((p - 69) / 12)
            env = np.exp(-tt * (2.0 + f / 600)) * (1 - np.exp(-tt * 200))
            tone = sum(a * np.sin(2 * np.pi * f * h * tt) for h, a in ((1, 1), (2, .45), (3, .2), (4, .1)))
            seg = tone * env * ev["velocity"] / 127 / max(len(ev["notes"]), 1)
            e = min(total, start + L)
            out[start:e] += seg[: e - start]
    out = out / (np.abs(out).max() + 1e-9) * 0.85
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((out * 32767).astype(np.int16).tobytes())


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("map", nargs="?", help="intensity map .npz")
    ap.add_argument("--list-zones", action="store_true")
    g = ap.add_argument_group("zone")
    g.add_argument("--zone", default="global", help="preset zone name (see --list-zones)")
    g.add_argument("--bbox", help="custom box 'lat_min,lat_max,lon_min,lon_max' (overrides --zone)")
    g.add_argument("--surface", choices=["all", "land", "ocean"], default="all")
    g.add_argument("--field", choices=["data", "intensity"], default="data",
                   help="average physical values (default) or the 0..1 intensity")
    g = ap.add_argument_group("time")
    g.add_argument("--steps-per-frame", type=int, default=4, help="musical steps between two frames")
    g.add_argument("--loop", action="store_true", help="series is cyclic (e.g. 12 months) - wrap interpolation")
    g = ap.add_argument_group("pitch")
    g.add_argument("--mapping", choices=["relative", "absolute"], default="relative")
    g.add_argument("--key", default="C")
    g.add_argument("--scale", choices=list(SCALES), default="major")
    g.add_argument("--low", type=int, default=55, help="lowest data note (MIDI, 55 = G3)")
    g.add_argument("--high", type=int, default=84, help="highest data note (MIDI, 84 = C6)")
    g.add_argument("--invert", action="store_true", help="higher value = lower pitch")
    g = ap.add_argument_group("chords")
    g.add_argument("--chord", choices=["none", "power", "triad", "seventh", "ninth", "auto"], default="auto",
                   help="auto = triad, adds 7th/9th when the value changes fast (tension)")
    g.add_argument("--voice", choices=["top", "root"], default="top", help="data note is top voice or chord root")
    g.add_argument("--tension-threshold", type=float, default=0.08,
                   help="normalized change per step that triggers a 7th (2x -> 9th)")
    g.add_argument("--bass", action="store_true", help="add the data note an octave (or more) below")
    g.add_argument("--bass-octaves", type=int, default=2)
    g = ap.add_argument_group("rhythm / dynamics")
    g.add_argument("--bpm", type=float, default=90)
    g.add_argument("--beats-per-step", type=float, default=1.0)
    g.add_argument("--style", choices=["block", "arpeggio", "broken"], default="block")
    g.add_argument("--legato", type=float, default=0.95)
    g.add_argument("--sustain-repeats", action="store_true", help="hold repeated chords instead of re-striking")
    g.add_argument("--velocity", choices=["variability", "value", "fixed"], default="variability",
                   help="variability = spatial std inside the zone drives loudness")
    g.add_argument("--markers", action="store_true", help="write frame dates as MIDI markers")
    g = ap.add_argument_group("output")
    g.add_argument("--out", default=None, help="output folder (default: music/output)")
    g.add_argument("--name", default=None)
    g.add_argument("--preview", action="store_true", help="also write piano-roll PNG + WAV preview")
    args = ap.parse_args()

    if args.list_zones:
        for k, v in ZONES.items():
            print(f"{k:16s} lat {v[0]:>6}..{v[1]:<6} lon {v[2]:>5}..{v[3]}")
        return
    if not args.map:
        ap.error("map path required")

    z, meta = load_map(args.map)
    field = z[args.field].astype(np.float32)
    lat, lon = z["lat"], z["lon"]
    times = z["time"] if "time" in z.files else None
    land = z["land_fraction"] if "land_fraction" in z.files else None
    if args.field == "intensity":
        meta = {**meta, "vmin": 0.0, "vmax": 1.0}

    bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else ZONES[args.zone]
    zone_label = "custom" if args.bbox else args.zone
    mask = zone_mask(lat, lon, bbox, land, args.surface)
    means, stds = zone_series(field, lat, mask)

    values = resample(means, args.steps_per_frame, args.loop)
    stds_r = resample(stds, args.steps_per_frame, args.loop)
    if times is not None:
        idx = np.minimum(np.arange(len(values)) // max(args.steps_per_frame, 1), len(times) - 1)
        step_times = times[idx]
    else:
        step_times = None

    events, (lo, hi) = series_to_events(values, stds_r, args, meta)

    var = meta.get("variable", "temperature")
    inst = INSTRUMENTS.get(var, INSTRUMENTS["temperature"])
    out = Path(args.out) if args.out else Path(__file__).resolve().parent / "output"
    out.mkdir(parents=True, exist_ok=True)
    name = args.name or f"{var}_{zone_label}_{args.surface}"
    write_midi(events, out / f"{name}.mid", args, inst, step_times)

    units = meta.get("units", "")
    info = {
        "variable": var, "units": units, "instrument": inst, "zone": zone_label, "bbox": bbox,
        "surface": args.surface, "cells": int(mask.sum()), "mapping": args.mapping,
        "range_mapped": [lo, hi], "key": args.key, "scale": args.scale, "chord": args.chord,
        "bpm": args.bpm, "beats_per_step": args.beats_per_step,
        "frame_means": [None if not np.isfinite(v) else round(float(v), 3) for v in means],
        "steps": [{**e, "time": (str(step_times[e["step"]]) if step_times is not None else None)} for e in events],
    }
    (out / f"{name}.json").write_text(json.dumps(info, indent=1))
    with open(out / f"{name}.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["step", "time", "value", "melody", "chord"])
        for e in events:
            wr.writerow([e["step"], step_times[e["step"]] if step_times is not None else "",
                         e["value"], e.get("melody_name", ""),
                         " ".join(note_name(p) for p in e["notes"])])

    print(f"zone {zone_label} {bbox} [{args.surface}] {int(mask.sum())} cells | "
          f"{len(means)} frames -> {len(values)} steps")
    print(f"series {np.nanmin(means):.2f}..{np.nanmax(means):.2f} {units}  mapped range {lo:.2f}..{hi:.2f}")
    print("melody: " + " ".join(e.get("melody_name", "-") for e in events[:24]) + (" ..." if len(events) > 24 else ""))
    print(f"wrote {out / (name + '.mid')} (+ .json, .csv)")
    if args.preview:
        write_piano_roll(events, values, out / f"{name}_pianoroll.png",
                         f"{var} - {zone_label} ({args.surface}) - {args.key} {args.scale}, chord={args.chord}",
                         args.beats_per_step)
        render_wav(events, out / f"{name}_preview.wav", args)
        print(f"wrote {name}_pianoroll.png and {name}_preview.wav")


if __name__ == "__main__":
    main()
