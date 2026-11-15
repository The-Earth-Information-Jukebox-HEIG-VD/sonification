"""
Composition helpers: anomalies -> tension, tempo map, phrase dynamics, humanisation.
Pure functions (numpy only), used by orchestra.compose().
"""
from __future__ import annotations

from datetime import datetime

import numpy as np

# How much each variable's anomaly contributes to musical tension, and in which direction.
#   "abs": both too high and too low are tense (temperature, rain)
#   "pos": only above normal is tense (smoke, lightning, dust, CO2)
#   "neg": only below normal is tense (sea ice: less ice than the season's normal)
TENSION_WEIGHTS = {
    "sea_ice": (0.5, "neg"),
    "surface_temperature": (1.0, "abs"),
    "precipitation": (0.5, "abs"),
    "snowfall": (0.4, "pos"),
    "aerosol_smoke": (0.7, "pos"),
    "fires": (0.7, "pos"),
    "lightning": (0.5, "pos"),
    "aerosol_dust": (0.5, "pos"),
    "co2": (0.0, "pos"),            # CO2 rise is already heard in the bass / chord ladder
}


# ----------------------------------------------------------------------------
# Anomalies: departure from the seasonal "normal"
# ----------------------------------------------------------------------------
def frames_per_year(times, default=12.0) -> float:
    try:
        t0 = datetime.fromisoformat(str(times[0]).replace("Z", ""))
        t1 = datetime.fromisoformat(str(times[-1]).replace("Z", ""))
        dt = (t1 - t0).total_seconds() / 86400 / max(len(times) - 1, 1)
        return 365.25 / dt if dt > 0 else default
    except Exception:
        return default


SKEWED = {"precipitation", "snowfall", "lightning", "aerosol_smoke", "aerosol_dust", "fires"}   # analysed in log space


def seasonal_anomaly(series: np.ndarray, times=None, reference_years: float = 3.0, log: bool = False,
                     already_anomaly: bool = False):
    """Anomaly = value - seasonal normal. The normal is a mean + annual harmonic fitted on a reference
    period (the first `reference_years`, or the whole series if shorter), like climatologists use a
    baseline period. Returns (anomaly, z_score, baseline).
    already_anomaly: the data are anomalies already (e.g. degC away from a 30-year normal): the normal is 0
    and z = value / robust spread of the whole series, so a record month stays a record in any window."""
    x = np.asarray(series, dtype=float)
    if already_anomaly:
        ok = x[np.isfinite(x)]
        scale = 1.4826 * np.median(np.abs(ok - np.median(ok))) if len(ok) > 3 else 0.0
        scale = max(scale, 0.5 * float(np.std(ok))) if len(ok) > 3 else scale
        scale = scale if np.isfinite(scale) and scale > 1e-9 else 1.0
        return x, np.clip(x / scale, -4, 4), np.zeros(len(x))
    if log:                                   # skewed quantities (rain, lightning...): log scale
        x = np.log1p(np.clip(x, 0, None) * 10)
    n = len(x)
    P = frames_per_year(times) if times is not None else 12.0
    t = np.arange(n)
    n_ref = int(round(P * reference_years)) if n >= P * (reference_years + 1) else n
    X = np.stack([np.ones(n), np.cos(2 * np.pi * t / P), np.sin(2 * np.pi * t / P)], axis=1)
    ok = np.isfinite(x[:n_ref])
    if ok.sum() >= 4 and n >= P * 0.75:
        coef, *_ = np.linalg.lstsq(X[:n_ref][ok], x[:n_ref][ok], rcond=None)
        baseline = X @ coef
    else:                                     # < 1 year of data: compare to the mean
        baseline = np.full(n, np.nanmean(x))
    anom = x - baseline
    ref = anom[:n_ref][np.isfinite(anom[:n_ref])]
    if len(ref) > 3:                          # robust spread (median absolute deviation)
        scale = 1.4826 * np.median(np.abs(ref - np.median(ref)))
        scale = max(scale, 0.5 * np.std(ref))
    else:
        scale = np.nanstd(anom)
    scale = scale if np.isfinite(scale) and scale > 1e-9 else 1.0
    return anom, np.clip(anom / scale, -4, 4), baseline


def tension_curve(bar_z: dict, smooth: float = 0.5) -> np.ndarray:
    """Per-bar tension 0..1 from the variables' anomaly z-scores (strongest contributor wins)."""
    n = len(next(iter(bar_z.values())))
    raw = np.zeros(n)
    for var, z in bar_z.items():
        w, mode = TENSION_WEIGHTS.get(var, (0.3, "abs"))
        zz = np.nan_to_num(np.abs(z) if mode == "abs" else np.clip(-z if mode == "neg" else z, 0, None))
        raw = np.maximum(raw, w * zz)
    t = np.clip((raw - 0.5) / 2.0, 0, 1)      # |z| < 0.5 = normal; |z| >= 2.5 = maximum tension
    out = np.zeros(n)                          # smooth so the music doesn't flicker
    for i in range(n):
        out[i] = t[i] if i == 0 else max(t[i], smooth * out[i - 1] + (1 - smooth) * t[i])
    return out


def modes_with_hysteresis(tension, enter=0.5, leave=0.35):
    """'major' normally, 'minor' while tension is high (enter at `enter`, leave below `leave`)."""
    out, dark = [], False
    for t in tension:
        dark = t >= (leave if dark else enter)
        out.append("minor" if dark else "major")
    return out


# ----------------------------------------------------------------------------
# Tempo map (beats -> seconds)
# ----------------------------------------------------------------------------
def tempo_map(bpm: float, total_beats: float, outro_start: float | None = None, outro_beats: float = 8,
              slowdown: float = 0.7):
    """[(beat, bpm), ...]: steady tempo, then a ritardando over the outro down to bpm*slowdown."""
    pts = [(0.0, bpm)]
    if outro_start is not None and outro_beats > 0:
        steps = int(outro_beats)
        for k in range(steps):
            pts.append((outro_start + k, bpm * (1 - (1 - slowdown) * (k + 1) / steps)))
    return pts


def beat_to_sec(beat: float, tmap) -> float:
    sec, last_b, last_bpm = 0.0, tmap[0][0], tmap[0][1]
    for b, bpm in tmap[1:]:
        if beat <= b:
            break
        sec += (b - last_b) * 60 / last_bpm
        last_b, last_bpm = b, bpm
    return sec + (beat - last_b) * 60 / last_bpm


# ----------------------------------------------------------------------------
# Expression
# ----------------------------------------------------------------------------
def phrase_envelope(beat: float, bpb: int, phrase_bars: int = 4, offset_beats: float = 0.0) -> float:
    """0..1 hairpin over each phrase: grows towards the 3rd bar, relaxes at the end."""
    L = bpb * phrase_bars
    pos = ((beat - offset_beats) % L) / L
    return float(np.sin(np.pi * min(pos / 0.7, 1.0) * 0.5) if pos < 0.7 else np.cos(np.pi * (pos - 0.7) / 0.6))


def apply_expression(parts, bpb, rng, tension_at, phrase_offset=0.0, humanize=0.5, skip_roles=()):
    """Velocity hairpins per phrase, louder when tense, small timing/velocity humanisation.
    tension_at(beat) -> 0..1. Notes are (pitch, start, dur, vel)."""
    for p in parts:
        role = p["cfg"]["role"]
        out = []
        for pitch, st, du, vel in p["notes"]:
            env = phrase_envelope(st, bpb, 4, phrase_offset)
            ten = tension_at(st)
            v = vel * (0.88 + 0.18 * env) * (0.9 + 0.25 * ten)
            on_beat = abs(st - round(st)) < 1e-6
            if humanize > 0 and role not in skip_roles:
                jitter = rng.normal(0, 0.012 if on_beat else 0.02) * humanize      # in beats (~10-15 ms)
                st = max(0.0, st + jitter)
                v += rng.normal(0, 4) * humanize
            out.append((pitch, st, du, int(np.clip(v, 1, 127))))
        p["notes"] = out


def expression_cc(total_beats, bpb, phrase_offset, tension_at, step=0.5):
    """CC11 curve for sustained parts: swell inside each phrase, fuller when tense."""
    pts = []
    b = 0.0
    while b <= total_beats:
        env = phrase_envelope(b, bpb, 4, phrase_offset)
        pts.append((11, 78 + 40 * env + 9 * tension_at(b), b))
        b += step
    return pts
