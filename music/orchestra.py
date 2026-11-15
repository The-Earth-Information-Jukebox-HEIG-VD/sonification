#!/usr/bin/env python3
"""
Earth Orchestra: several intensity maps (one per variable) -> one coherent multi-track score.

Arranged "like a conductor":

  (Temperature is played by the piano: a wide range and re-struck notes make it the most precise line.)
  ALWAYS ON = FOUNDATION, SOMETIMES = MELODY. Variables present all the time (temperature, CO2) form
  the harmonic bed; variables that only appear sometimes (rain, dust, smoke, lightning) carry the
  melodies when they happen, so each melody entering is itself information.

    voice                 variable             instrument              data style / musical style
    -------------------   ------------------   ---------------------   ------------------------------------
    melody   G4..G6       precipitation        Violins                 pitch = value, tremolo when heavy / gated legato line
    melody   G4..E6 (8va) snowfall             Glockenspiel            pitch = value while it snows
    melody   C5..A6       aerosol_dust         Flute                   pitch = value while dust is present
    melody   F3..D5       aerosol_smoke        French horns            pitch = value while smoke is present
    timpani  E2..G3       lightning            Timpani                 hits per beat = flash density, pitch = value
    swell    E2..C4       fires                Trombones               held, louder with more fires
    pad      D4..A5       nightlights          Synth pad               a note per year, fading slowly over the year
    harmony  C3..C5       surface_temperature  Piano (chords)          chord every beat, root = value / comping
    bass     E2..D4       co2                  Contrabass              pitch = value, re-bowed every bar / one root per bar

  TIME = BARS. One frame = one bar (4 beats). One chord every 2 bars, changing only on bar lines:
  CO2 (the slow foundation) climbs a TENSION LADDER I -> vi -> IV -> ii -> V (with hysteresis) and
  the contrabass plays that root; every instrument
  plays only tones of that chord (the melody may use scale passing tones on weak beats).
  Activity (on/off) and density levels are decided PER BAR with hysteresis, so instruments
  enter and leave cleanly instead of flickering beat by beat.

Usage
  python music/orchestra.py simulation/output/sim_both_24f_1deg_ --zone india --preview
  python music/orchestra.py simulation/output/sim_both_24f_1deg_ --zone sahara --key D --scale dorian
  python music/orchestra.py simulation/output/sim_both_24f_1deg_ --zone arctic --only surface_temperature,co2
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from midi_writer import MidiFile  # noqa: E402
from zone_to_midi import (ZONES, SCALES, load_map, note_name, parse_key,  # noqa: E402
                          scale_notes, zone_mask, cells_mask, zone_series)
import composition as comp  # noqa: E402

SEVEN_NOTE_SCALES = [k for k, v in SCALES.items() if len(v) == 7]

# ----------------------------------------------------------------------------
# The arrangement (score order: high -> low). This table IS the orchestration.
#   low/high : register (MIDI notes)        gain  : absolute intensity x gain = presence
#   gate     : presence needed to enter     levels: presence thresholds for denser rhythm
#   level    : mix level (scales velocity)  pan   : 0 left .. 127 right (orchestra seating)
# ----------------------------------------------------------------------------
LEVEL_CO2 = 1.15         # mix level of the CO2 bass (see its entry)
ORCHESTRA = {
    # always present -> the foundation: harmony (piano chords) and bass
    "surface_temperature": dict(name="Temperature - Piano (chords)", role="harmony", program=0, channel=0,
                                low=48, high=72, gain=1.0, gate=0.0, levels=[0.45, 0.75], level=0.65, pan=64,
                                always_on=True),
    # an octave higher than a real contrabass part (E2..D4, not E1..D3), re-bowed on every bar line and
    # louder: below about 100 Hz laptop and phone speakers play almost nothing, and a note held for many
    # bars fades into the background
    "co2":                 dict(name="CO2 - Contrabass", role="bass", program=43, channel=5,
                                low=40, high=62, gain=1.0, gate=0.0, levels=[], level=LEVEL_CO2, pan=78,
                                always_on=True, pulse="bar"),
    # present only sometimes -> they carry the melodies when they happen
    "precipitation":       dict(name="Rain - Violins (melody)", role="melody", program=48, channel=1,
                                low=67, high=91, gain=2.5, gate=0.10, levels=[0.35, 0.65], level=0.90, pan=36,
                                tremolo=4),          # heavy rain -> up to 4 notes per beat (tremolo)
    "aerosol_dust":        dict(name="Dust - Flute (melody)", role="melody", program=73, channel=2,
                                low=72, high=93, gain=4.0, gate=0.12, levels=[0.45, 0.75], level=0.85, pan=24),
    "aerosol_smoke":       dict(name="Smoke - French Horns (melody)", role="melody", program=60, channel=3,
                                low=53, high=74, gain=4.0, gate=0.12, levels=[0.60], level=0.85, pan=96),
    "snowfall":            dict(name="Snow - Glockenspiel (melody)", role="melody", program=9, channel=6,
                                low=67, high=88, gain=8.0, gate=0.06, levels=[0.35, 0.65], level=1.0, pan=16,
                                restrike=True),   # sounds an octave higher; percussive -> re-struck every beat
    # lightning -> thunder: timpani, low; hits per beat = flash density. (Was a xylophone, too close to the
    # glockenspiel of the snow.)
    "lightning":           dict(name="Lightning - Timpani", role="timpani", program=47, channel=4,
                                low=40, high=55, gain=3.0, gate=0.08, levels=[0.35, 0.60, 0.85], level=0.85, pan=104),
    # fires (days of the month with a fire): trombones that swell and hold, louder with more fires, below
    # the piano's chords instead of a melody inside them
    "fires":               dict(name="Fires - Trombones", role="swell", program=57, channel=8,
                                low=40, high=60, gain=3.0, gate=0.08, levels=[0.45, 0.75], level=0.85, pan=88),
    # night lights (human-made, yearly: one value per year, held for its 12 months): a soft synthesizer pad,
    # the only electronic sound. It starts at full level when the value changes (each January) and fades slowly,
    # over the whole year, to a soft background (`fade_floor` of the full level) reached just before the next change:
    # leaves room: a drone for the whole year became tiring. `smooth` False: the yearly steps are not blurred.
    # level 0.60: about as loud as the CO2 bass, 2 dB under the piano while it sounds (0.30 was 8 dB under: lost).
    "nightlights":         dict(name="Night lights - Synth pad", role="swell", program=89, channel=7,
                                low=62, high=81, gain=2.0, gate=0.03, levels=[0.35, 0.65], level=0.60, pan=52,
                                smooth=False, fade=True, fade_floor=0.75),    # 0.75: ends about 10 dB under the piano
}
# ----------------------------------------------------------------------------
# Regional palettes (fun!): same roles and registers, local instruments.
# Each entry: variable -> (GM program, label[, level multiplier]). Missing variables keep the orchestra.
# GM numbers match the GeneralUser GS SoundFont presets.
# ----------------------------------------------------------------------------
T, R, D, S, L, C = "surface_temperature", "precipitation", "aerosol_dust", "aerosol_smoke", "lightning", "co2"
PALETTES = {
    "orchestra": {},
    "alps": {        # Swiss/Alpine folk: Laendler clarinet, Appenzell Hackbrett, Schwyzeroergeli, alphorn, bells
        T: (71, "Clarinet"), R: (15, "Hackbrett (dulcimer)"), D: (21, "Schwyzeroergeli (accordion)"),
        S: (60, "Alphorn (horns)"), L: (14, "Cowbells & church bells", 0.7), C: (43, "Bassgeige (double bass)")},
    "india": {       # Hindustani colours: sitar, santoor, harmonium, shehnai, tabla, drone
        T: (104, "Sitar"), R: (15, "Santoor (dulcimer)"), D: (20, "Harmonium (reed organ)"),
        S: (111, "Shehnai", 0.8), L: (117, "Tabla (melodic tom)"), C: (42, "Drone (cello, tanpura-like)")},
    "japan": {       # shakuhachi, koto, shamisen, taiko
        T: (77, "Shakuhachi"), R: (107, "Koto"), D: (106, "Shamisen"),
        S: (92, "Sho-like (bowed glass)", 0.8), L: (116, "Taiko"), C: (43, "Double bass")},
    "china": {       # dizi, guzheng, pipa, sheng, gong
        T: (73, "Dizi (flute)"), R: (107, "Guzheng (koto)"), D: (106, "Pipa (shamisen)"),
        S: (22, "Sheng (harmonica)", 0.7), L: (14, "Gong & bells (tubular bells)", 0.7), C: (43, "Double bass")},
    "north_africa": {  # Sahara: ney, oud, desert-blues guitar, mizmar, darbuka
        T: (75, "Ney (pan flute)"), R: (24, "Oud (nylon guitar)"), D: (25, "Desert blues guitar (steel)"),
        S: (111, "Mizmar (shenai)", 0.8), L: (117, "Darbuka (melodic tom)"), C: (32, "Bass")},
    "west_africa": {   # balafon, kora, guitar, djembe
        T: (12, "Balafon (marimba)"), R: (46, "Kora (harp)"), D: (24, "Guitar"),
        S: (60, "Horns"), L: (117, "Djembe (melodic tom)"), C: (32, "Bass")},
    "celtic": {      # fiddle, harp, accordion, bagpipe drones, bodhran
        T: (110, "Fiddle"), R: (46, "Celtic harp"), D: (21, "Accordion"),
        S: (109, "Bagpipe drones", 0.6), L: (117, "Bodhran (melodic tom)"), C: (42, "Cello")},
    "americana": {   # USA: fiddle, banjo, steel guitar, harmonica
        T: (110, "Fiddle"), R: (105, "Banjo"), D: (25, "Acoustic steel guitar"),
        S: (22, "Blues harmonica", 0.7), L: (116, "Thunder drum (taiko)"), C: (32, "Upright bass")},
    "andes": {       # quena/pan flute, charango, guitar, ocarina, bombo
        T: (75, "Pan flute (siku)"), R: (105, "Charango (banjo)"), D: (24, "Guitar"),
        S: (79, "Ocarina"), L: (116, "Bombo (taiko)"), C: (32, "Bass")},
    "tropical": {    # Amazon/Brazil: flute, marimba, nylon guitar (bossa), brass, wood block
        T: (73, "Flute"), R: (12, "Marimba"), D: (24, "Nylon guitar (bossa)"),
        S: (61, "Brass section"), L: (115, "Wood block / percussion"), C: (32, "Acoustic bass")},
    "caribbean": {   # steel pan band
        T: (114, "Steel pan"), R: (12, "Marimba"), D: (24, "Guitar"),
        S: (61, "Brass section"), L: (115, "Claves (wood block)"), C: (32, "Bass")},
    "pacific": {     # Hawaii / Pacific islands: lap steel, ukulele, log drums
        T: (25, "Hawaiian steel guitar"), R: (24, "Ukulele (nylon guitar)"), D: (12, "Marimba"),
        S: (75, "Pan flute"), L: (114, "Steel drums"), C: (32, "Bass")},
    "russia": {      # Siberia: bayan, balalaika, strings, tuba, Orthodox bells
        T: (21, "Bayan (accordion)"), R: (105, "Balalaika (banjo)"), D: (48, "Strings"),
        S: (58, "Tuba"), L: (14, "Orthodox bells", 0.7), C: (43, "Double bass")},
    "ice": {         # Arctic/Antarctica: celesta, glockenspiel, bowed glass, horns, bells
        T: (8, "Celesta"), R: (9, "Glockenspiel", 0.8), D: (92, "Bowed glass"),
        S: (60, "French horns"), L: (14, "Tubular bells", 0.7), C: (43, "Double bass")},
    "australia": {   # whistle, kalimba/clapsticks, guitar, didgeridoo-like drone
        T: (78, "Whistle"), R: (108, "Clapsticks (kalimba)"), D: (24, "Guitar"),
        S: (70, "Didgeridoo-like (bassoon)"), L: (115, "Wood block"), C: (43, "Double bass")},
}
PALETTE_SCALE = {   # default mode per palette (only used when --scale is not given)
    "alps": "major", "india": "harmonic_minor", "japan": "minor", "china": "major",
    "north_africa": "harmonic_minor", "west_africa": "major", "celtic": "dorian", "americana": "major",
    "andes": "minor", "tropical": "lydian", "caribbean": "major", "pacific": "major",
    "russia": "minor", "ice": "lydian", "australia": "dorian",
}
ZONE_PALETTE = {
    "switzerland": "alps", "alps": "alps", "europe": "orchestra", "india": "india", "japan": "japan",
    "china": "china", "sahara": "north_africa", "west_africa": "west_africa", "north_atlantic": "celtic",
    "scotland_ireland": "celtic", "usa": "americana", "andes": "andes", "amazon": "tropical",
    "caribbean": "caribbean", "nino34": "pacific", "pacific_center": "pacific", "siberia": "russia",
    "arctic": "ice", "antarctica": "ice", "australia": "australia",
}


def apply_palette(name):
    """Return a copy of ORCHESTRA with the palette's instruments swapped in."""
    pal = PALETTES[name]
    orch = {}
    for var, cfg in ORCHESTRA.items():
        cfg = dict(cfg)
        if var in pal:
            prog, label, *lv = pal[var]
            cfg["program"] = prog
            cfg["name"] = f"{cfg['name'].split(' - ')[0]} - {label}"
            if lv:
                cfg["level"] = cfg["level"] * lv[0]
        orch[var] = cfg
    return orch


HARMONY_PRIORITY = ["co2", "surface_temperature"]
LEAD_VAR = "surface_temperature"   # its anomaly breaks ties when choosing the climax
SMOOTH_ROLES = {"melody", "bass", "harmony", "swell", "rhythm"}  # timpani holds per frame (spiky)


# ----------------------------------------------------------------------------
# Inputs and time grid
# ----------------------------------------------------------------------------
def collect_maps(paths):
    files = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            files += sorted(pp.glob("*.npz"))
        elif pp.exists():
            files.append(pp)
        else:
            files += [Path(f) for f in sorted(glob.glob(p + "*.npz"))] or [Path(f) for f in sorted(glob.glob(p))]
    out = []
    for f in files:
        if f.suffix == ".npz" and f not in out:
            out.append(f)
    return out


def sample_beats(series, n_ref, n_beats, fpb, bpb, loop, smooth):
    """Value of a per-frame series at every beat (time measured in the reference's frames).
    When a beat covers several frames (e.g. daily data, one bar per week), the beat gets the MEAN of
    its frames, so no day is skipped."""
    n = len(series)
    step = fpb / bpb * (n / n_ref)                               # frames of this series per beat
    if step > 1.0 + 1e-9 and not loop:
        out = np.full(n_beats, np.nan)
        x = np.asarray(series, dtype=float)
        for k in range(n_beats):
            a, b = int(round(k * step)), int(round((k + 1) * step))
            seg = x[a:max(b, a + 1)]
            seg = seg[np.isfinite(seg)]
            if len(seg):
                out[k] = seg.mean()
        return out
    pos = np.arange(n_beats) * fpb / bpb                       # in reference frames
    if loop:
        pos = (pos * n / n_ref) % n
        xs, ys = np.arange(n + 1), np.append(series, series[0])
    else:
        pos = np.clip(pos * n / n_ref, 0, n - 1)
        xs, ys = np.arange(n), series
    if smooth:
        return np.interp(pos, xs, ys)
    return ys[np.clip(np.floor(pos + 1e-9).astype(int), 0, len(ys) - 1)]


def rel_norm(v, span=None):
    """Values -> 0..1 relative to their own lowest and highest, or to a given (lowest, highest) span."""
    f = v[np.isfinite(v)]
    if not len(f):
        return np.full_like(v, np.nan)
    lo, hi = f.min(), f.max()
    if span is not None and np.isfinite(span[0]) and np.isfinite(span[1]):
        lo, hi = float(span[0]), float(span[1])
    if hi - lo < 1e-9:
        return np.where(np.isfinite(v), 0.5, np.nan)
    return np.clip((v - lo) / (hi - lo), 0, 1)


def per_bar(x, bpb):
    x = x.reshape(-1, bpb)
    out = np.full(len(x), np.nan)
    for i, row in enumerate(x):
        r = row[np.isfinite(row)]
        if len(r):
            out[i] = r.mean()
    return out


def bar_levels(bar_pres, gate, thresholds):
    """0 = rest, 1.. = density level. Hysteresis: enter at gate, leave below 0.7*gate."""
    active, out = False, []
    for p in bar_pres:
        if not np.isfinite(p):
            active = False; out.append(0); continue
        active = p >= (0.7 * gate if active else gate)
        out.append(0 if not active else 1 + sum(p >= t for t in thresholds))
    return out


# ----------------------------------------------------------------------------
# Harmony: one chord per bar
# ----------------------------------------------------------------------------
QUALITY = {(4, 7): "", (3, 7): "m", (3, 6): "dim", (4, 8): "aug"}
CHORD_ROOT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def chord_on(root, sl):
    i = sl.index(root)
    triad = [sl[i], sl[i + 2], sl[i + 4]]
    q = QUALITY.get((triad[1] - triad[0], triad[2] - triad[0]), "")
    return {"root": root, "root_pc": root % 12, "triad": triad, "pcs": sorted({m % 12 for m in triad}),
            "name": CHORD_ROOT_NAMES[root % 12] + q}


# Chords ordered by harmonic tension (scale degrees, 0 = tonic): rest -> tension.
# Low value = home (I), high value = dominant (V). Minor scales use the same degrees (i, VI, iv, ii°->iv, v).
TENSION_LADDER = [0, 5, 3, 1, 4]          # I, vi, IV, ii, V
LADDERS = {                               # per mode, never a diminished/augmented chord
    "major":          [0, 5, 3, 1, 4],    # I  vi  IV  ii  V
    "minor":          [0, 5, 3, 6, 4],    # i  VI  iv  VII v
    "harmonic_minor": [0, 5, 3, 4],       # i  VI  iv  V
    "dorian":         [0, 6, 2, 3, 4],    # i  VII III IV  v
    "lydian":         [0, 5, 2, 1, 4],    # I  vi  iii II  V
}


def harmony_from(bar_norm, key_pc, scale, chord_bars=2, hysteresis=0.6):
    """A slow variable climbs the tension ladder. One chord per `chord_bars` bars; the chord only
    changes when the value moves clearly (hysteresis), so small wiggles don't make it jump."""
    sl = scale_notes(key_pc, scale)
    i0 = next(i for i, m in enumerate(sl) if m >= 36 and m % 12 == key_pc)
    ladder = LADDERS.get(scale, TENSION_LADDER)
    out, cur = [], None
    for b0 in range(0, len(bar_norm), chord_bars):
        block = bar_norm[b0:b0 + chord_bars]
        block = block[np.isfinite(block)]
        if not len(block):
            out += [None] * min(chord_bars, len(bar_norm) - b0); cur = None; continue
        x = float(block.mean()) * (len(ladder) - 1)
        if cur is None or abs(x - cur) > hysteresis:
            cur = int(round(x))
        ch = chord_on(sl[i0 + ladder[cur]], sl)
        out += [ch] * min(chord_bars, len(bar_norm) - b0)
    return out


def harmony_progression(n_bars, key_pc, scale):
    """No usable leader: I - vi - IV - V."""
    sl = scale_notes(key_pc, scale)
    i0 = next(i for i, m in enumerate(sl) if m >= 36 and m % 12 == key_pc)
    return [chord_on(sl[i0 + [0, 5, 3, 4][(b // 2) % 4]], sl) for b in range(n_bars)]


MAJOR_LIKE = {"major", "lydian"}


def darken(chords, tension, modes, key_pc, scale):
    """Tension -> harmony. In 'minor' bars a major-mode piece borrows the parallel-minor chords
    (I->i, IV->iv, vi->bVI, ii->iv, V stays major = harmonic minor) and the melody uses that scale.
    Very tense bars (>= 0.75) add the 7th. Returns (chords, per-bar scale lists)."""
    sl = scale_notes(key_pc, scale)
    nat, harm = scale_notes(key_pc, "minor"), scale_notes(key_pc, "harmonic_minor")
    iv = SCALES[scale]
    out, scales = [], []
    for h, t, mode in zip(chords, tension, modes):
        if h is None:
            out.append(None); scales.append(sl); continue
        ch, scl = h, sl
        if mode == "minor" and scale in MAJOR_LIKE:
            pc = (h["root"] - key_pc) % 12
            deg = iv.index(pc) if pc in iv else 0
            deg = 3 if deg == 1 else deg                     # ii -> iv (avoids ii dim)
            src = harm if deg == 4 else nat
            target_pc = (key_pc + SCALES["harmonic_minor" if deg == 4 else "minor"][deg]) % 12
            root = min((m for m in src if m % 12 == target_pc), key=lambda m: abs(m - h["root"]))
            ch, scl = chord_on(root, src), src
        if t >= 0.75 and ch["root"] in scl:
            i = scl.index(ch["root"])
            sev = scl[i + 6]
            ch = {**ch, "pcs": sorted(set(ch["pcs"]) | {sev % 12}), "name": ch["name"] + "7"}
        out.append(ch); scales.append(scl)
    return out, scales


def tones(pcs, low, high):
    return [m for m in range(low, high + 1) if m % 12 in pcs]


def nearest(target, cand, prev=None, smooth=0.0):
    if not cand:
        return None
    return min(cand, key=lambda m: abs(m - target) + (smooth * abs(m - prev) if prev is not None else 0))


# ----------------------------------------------------------------------------
# Roles. Each returns (notes, ccs); a note = (pitch, start_beat, dur_beats, velocity)
# ----------------------------------------------------------------------------
def role_melody(cfg, c):
    """Violins: one legato line. Strong beats (1, 3) = chord tones; weak beats may pass through
    scale tones. Repeated pitches are tied; every 4th bar ends with a short breath.
    Tense bars (anomaly): restless eighth-note motion (note + upper neighbour), shorter articulation."""
    lo, hi, bpb = cfg["low"], cfg["high"], c["bpb"]
    bar_scales, tens = c.get("bar_scale"), c.get("tension")
    notes, prev = [], None
    for b, h in enumerate(c["harmony"]):
        if h is None or (cfg.get("gate", 0) > 0 and c["levels"][b] == 0):
            prev = None; continue
        scale_in = [m for m in (bar_scales[b] if bar_scales else c["sl"]) if lo <= m <= hi]
        agitated = tens is not None and tens[b] >= 0.6
        bar = []
        for k in range(bpb):
            i = b * bpb + k
            nv = c["norm"][i]
            if not np.isfinite(nv):
                continue
            cand = tones(h["pcs"], lo, hi) if k % 2 == 0 else scale_in
            p = nearest(lo + nv * (hi - lo), cand, prev, smooth=0.35)
            vel = cfg["level"] * (62 + 40 * nv + (8 if k == 0 else 0))
            if agitated:
                j = scale_in.index(p) if p in scale_in else None
                nb = scale_in[j + 1] if j is not None and j + 1 < len(scale_in) else p
                bar.append([p, i, 0.5, vel, 0.8])
                bar.append([nb, i + 0.5, 0.5, vel * 0.85, 0.8])
            elif bar and bar[-1][0] == p and bar[-1][1] + bar[-1][2] == i and bar[-1][4] > 0.9:
                bar[-1][2] += 1                       # tie repeated note
            else:
                bar.append([p, i, 1, vel, 0.97])
            prev = p
        if bar and (b + 1) % 4 == 0 and not agitated:  # phrase breath every 4 bars
            bar[-1][2] = max(0.5, bar[-1][2] - 0.35)
        notes += [(p, s_, d * leg, v) for p, s_, d, v, leg in bar]
    return notes, []

def role_bass(cfg, c):
    """Contrabass: the chord root, one whole note per bar; doubled an octave down when high.
    Tense bars: driving eighth-note ostinato on the root (film-score agitation)."""
    lo, hi, bpb = cfg["low"], cfg["high"], c["bpb"]
    tens = c.get("tension")
    notes, prev = [], None
    for b, h in enumerate(c["harmony"]):
        if h is None or not np.isfinite(c["bar_norm"][b]):
            prev = None; continue
        # register follows CO2, but like a real bassist: smallest leap from the previous note
        p = nearest(lo + c["bar_norm"][b] * (hi - lo), [m for m in range(lo, hi + 1) if m % 12 == h["root_pc"]],
                    prev, smooth=1.2)
        prev = p
        pres = c["bar_pres"][b]
        vel = cfg["level"] * (58 + 45 * pres)
        if tens is not None and tens[b] >= 0.6:
            for j in range(bpb * 2):
                q = p + 12 if (j % 4 == 2 and p + 12 <= 55) else p
                notes.append((q, b * bpb + j * 0.5, 0.42, vel * (1.0 if j % 2 == 0 else 0.78)))
            continue
        notes.append((p, b * bpb, bpb * 0.98, vel))
        if pres > 0.75 and p - 12 >= 28:
            notes.append((p - 12, b * bpb, bpb * 0.98, vel * 0.85))
    return notes, []

def role_harmony(cfg, c):
    """Piano: the bar's chord, voice-led (each voicing moves as little as possible).
    Density by level: 1 = whole note, 2 = half notes, 3 = quarter notes."""
    lo, hi, bpb = cfg["low"], cfg["high"], c["bpb"]
    rhythms = {1: [(0, bpb, 1.0)], 2: [(0, bpb / 2, 1.0), (bpb / 2, bpb / 2, 0.85)],
               3: [(k, 1, 1.0 if k % 2 == 0 else 0.75) for k in range(bpb)]}
    notes, prev = [], None
    tens = c.get("tension")
    for b, h in enumerate(c["harmony"]):
        lvl = c["levels"][b]
        if h is None or lvl == 0:
            continue
        if tens is not None and tens[b] >= 0.6:
            lvl = min(lvl + 1, 3)                      # tense: the piano pulses faster
        k = 3 if c["bar_pres"][b] < 0.5 else 4
        ts = tones(h["pcs"], lo, hi)
        windows = [ts[j:j + k] for j in range(len(ts) - k + 1)] or [ts]
        if prev:
            voicing = min(windows, key=lambda w: sum(min(abs(x - p) for p in prev) for x in w))
        else:
            target = lo + c["bar_norm"][b] * (hi - lo)
            voicing = min(windows, key=lambda w: abs(np.mean(w) - target))
        prev = voicing
        base = cfg["level"] * (40 + 75 * c["bar_pres"][b])
        for off, dur, acc in rhythms[min(lvl, 3)]:
            for p in voicing:
                notes.append((p, b * bpb + off, dur * 0.95, base * acc))
    return notes, []


def role_rhythm(cfg, c):
    """Pizzicato: chord-tone arpeggio rising then falling. Level 1 = half notes, 2 = quarters,
    3 = eighth notes. Short plucks, so nothing piles up."""
    lo, hi, bpb = cfg["low"], cfg["high"], c["bpb"]
    per_bar = {1: bpb // 2, 2: bpb, 3: bpb * 2}
    wave = [0, 1, 2, 3, 4, 3, 2, 1]
    notes = []
    for b, h in enumerate(c["harmony"]):
        lvl = c["levels"][b]
        if h is None or lvl == 0:
            continue
        ts = tones(h["pcs"], lo, hi)
        if not ts:
            continue
        n = per_bar[min(lvl, 3)]
        start = min(int(round(c["bar_norm"][b] * (len(ts) - 1))), max(len(ts) - 5, 0))
        sub = bpb / n
        for j in range(n):
            off = wave[(j * len(wave) // n) % len(wave)] if n < len(wave) else wave[j % len(wave)]
            p = ts[min(start + off, len(ts) - 1)]
            beat_i = b * bpb + int(j * sub)
            pres = c["pres"][beat_i] if np.isfinite(c["pres"][beat_i]) else c["bar_pres"][b]
            acc = 1.0 if (j * sub) % 1 == 0 else 0.8
            notes.append((p, b * bpb + j * sub, sub * 0.45, cfg["level"] * (50 + 60 * pres) * acc))
    return notes, []


def role_swell(cfg, c):
    """Trombones: held open fifth on the chord root; crescendo follows the smoke (CC11).
    Thick smoke (level 2) adds the 4th = an unresolved suspension: urgency."""
    lo, hi, bpb, sl = cfg["low"], cfg["high"], c["bpb"], c["sl"]
    held, ccs, prev = [], [], None
    for b, h in enumerate(c["harmony"]):
        lvl = c["levels"][b]
        if h is None or lvl == 0:
            ccs.append((11, 0, b * bpb)); prev = None; continue
        target = (lo + hi - 7) / 2 if prev is None else prev
        r = nearest(target, [m for m in range(lo, hi - 6) if m % 12 == h["root_pc"]])
        if r is None:
            continue
        prev = r
        chord = [r, r + 7]
        if lvl >= 2 and r in sl:
            chord.append(sl[sl.index(r) + 3])
        held.append((tuple(sorted(chord)), b))
        for k in range(bpb):
            p = c["pres"][b * bpb + k]
            ccs.append((11, 30 + 97 * (p if np.isfinite(p) else 0), b * bpb + k))
    notes, i = [], 0
    while i < len(held):                       # tie identical chords across consecutive bars
        ch, b0 = held[i]
        j = i + 1
        while j < len(held) and held[j][0] == ch and held[j][1] == held[j - 1][1] + 1:
            j += 1
        dur = (held[j - 1][1] - b0 + 1) * bpb
        notes += [(p, b0 * bpb, dur * 0.97, cfg["level"] * 100) for p in ch]
        i = j
    return notes, ccs


def role_timpani(cfg, c):
    """Timpani tuned to the chord root and fifth. Level 1 = beat 1, 2 = beats 1 & 3,
    3 = every beat, 4 = every beat + a roll into the next bar. New storm = accented downbeat."""
    lo, hi, bpb = cfg["low"], cfg["high"], c["bpb"]
    notes, prev_lvl = [], 0
    for b, h in enumerate(c["harmony"]):
        lvl = c["levels"][b]
        if h is None or lvl == 0:
            prev_lvl = 0; continue
        r = nearest((lo + hi) / 2, [m for m in range(lo, hi + 1) if m % 12 == h["root_pc"]])
        f = r + 7 if r + 7 <= hi else r - 5
        hits = {1: [(0, r)], 2: [(0, r), (2, f)], 3: [(k, r if k % 2 == 0 else f) for k in range(bpb)]}
        pattern = hits[min(lvl, 3)]
        for k, p in pattern:
            pres = c["pres"][b * bpb + int(k)]
            pres = pres if np.isfinite(pres) else c["bar_pres"][b]
            vel = cfg["level"] * (52 + 70 * pres)
            if k == 0 and lvl >= 3 and prev_lvl < 3:
                vel = 127                                  # storm arrives
            notes.append((p, b * bpb + k, 0.9, vel))
        if lvl >= 4:                                       # roll on the last beat
            for j in range(4):
                notes.append((r, b * bpb + bpb - 1 + j / 4, 0.24, cfg["level"] * (70 + 12 * j)))
        prev_lvl = lvl
    return notes, []


# ----------------------------------------------------------------------------
# DATA-FAITHFUL roles (style="data", the default): every note comes from the data.
#   pitch    = the variable's value, mapped linearly onto the quantisation grid of the register
#   loudness = how much of the phenomenon is present (rain, dust, smoke, lightning)
#   density  = amount, for event-like variables (rain plucks, lightning hits) - deterministic
# No chord snapping, no smoothing, no invented notes.
# ----------------------------------------------------------------------------
def _grid(cfg, c):
    return c["qnotes"](cfg["low"], cfg["high"])


def _pitch(nv, grid):
    return grid[int(round(float(np.clip(nv, 0, 1)) * (len(grid) - 1)))]


def _data_line(cfg, c, sustain=True, gate=False, vel_from_presence=False):
    grid, notes, cur = _grid(cfg, c), [], None
    for i, nv in enumerate(c["norm"]):
        pr = c["pres"][i]
        if not np.isfinite(nv) or (gate and (not np.isfinite(pr) or pr < cfg["gate"])):
            cur = None; continue
        p = _pitch(nv, grid)
        vel = cfg["level"] * (45 + 80 * pr) if vel_from_presence else cfg["level"] * 92
        if sustain and cur is not None and cur[0] == p and cur[1] + cur[2] == i:
            cur[2] += 1                                   # same value -> held note
            continue
        cur = [p, i, 1, vel]
        notes.append(cur)
    return [(p, s_, d * 0.95, v) for p, s_, d, v in notes]


def data_chords(cfg, c):
    """Temperature (always on): a piano chord on every beat. Its ROOT = the value (linear on the scale
    grid of the register); the third and fifth are the next scale tones, so the harmony itself moves
    with the data. Hotter (absolute) -> a little louder; accent on each bar's downbeat."""
    grid, notes = _grid(cfg, c), []
    full = c["qnotes"](cfg["low"], cfg["high"] + 12)
    for i, nv in enumerate(c["norm"]):
        if not np.isfinite(nv):
            continue
        root = _pitch(nv, grid)
        j = full.index(root) if root in full else None
        chord = [root] + ([full[j + 2], full[j + 4]] if j is not None and j + 4 < len(full) else [root + 4, root + 7])
        pr = c["pres"][i] if np.isfinite(c["pres"][i]) else 0.5
        vel = cfg["level"] * (62 + 30 * pr) * (1.0 if i % c["bpb"] == 0 else 0.82)
        for q in chord:
            notes.append((q, i, 0.9, vel))
    return notes, []


def data_melody_intermittent(cfg, c):
    """Rain / dust / smoke: a melody that only plays while the phenomenon is present.
    pitch = value, loudness = amount; with `tremolo`, heavy amounts repeat the note faster."""
    grid, notes, cur = _grid(cfg, c), [], None
    trem = int(cfg.get("tremolo", 1))
    for i, nv in enumerate(c["norm"]):
        pr = c["pres"][i]
        if not np.isfinite(nv) or not np.isfinite(pr) or pr < cfg["gate"]:
            cur = None; continue
        p = _pitch(nv, grid)
        vel = cfg["level"] * (68 + 59 * pr)        # present = clearly audible; louder with more
        n = 1 + int(round(pr * (trem - 1))) if trem > 1 else 1
        if n > 1:
            for k in range(n):
                notes.append([p, i + k / n, 0.95 / n, vel * (1.0 if k == 0 else 0.85)])
            cur = None; continue
        if not cfg.get("restrike") and cur is not None and cur[0] == p and cur[1] + cur[2] >= i - 1e-9:
            cur[2] += 1; continue
        cur = [p, i, 1, vel]
        notes.append(cur)
    return [(p, s_, d * (0.95 if d >= 1 else 1.0), v) for p, s_, d, v in notes], []


def data_melody(cfg, c):
    """Temperature: the lead data line. `steps_per_beat` samples per beat (values interpolated between
    beats), re-struck every step when `restrike` (piano: you keep hearing the current value)."""
    k = int(cfg.get("steps_per_beat", 1))
    if k <= 1:
        return _data_line(cfg, c, sustain=not cfg.get("restrike")), []
    grid, notes = _grid(cfg, c), []
    norm = np.asarray(c["norm"], dtype=float)
    n = len(norm)
    pos = np.arange(n * k) / k
    fine = np.interp(pos, np.arange(n), np.where(np.isfinite(norm), norm, np.nan))
    for j, nv in enumerate(fine):
        if not np.isfinite(nv):
            continue
        p = _pitch(nv, grid)
        accent = 1.0 if j % (k * 2) == 0 else (0.9 if j % k == 0 else 0.8)   # light pulse on beats
        if not cfg.get("restrike") and notes and notes[-1][0] == p and notes[-1][1] + notes[-1][2] >= pos[j] - 1e-9:
            notes[-1] = (p, notes[-1][1], notes[-1][2] + 1 / k, notes[-1][3]); continue
        notes.append((p, pos[j], 0.95 / k, cfg["level"] * 92 * accent))
    return notes, []


def data_bass(cfg, c):          # CO2: same, low register
    notes = _data_line(cfg, c, sustain=True)
    if cfg.get("pulse") != "bar":
        return notes, []
    out, bpb = [], c["bpb"]                     # pulse="bar": a held note is re-bowed on every bar line
    for p, s_, d, v in notes:
        end = s_ + d / 0.95
        while s_ < end - 1e-9:
            nxt = min(end, (int(s_ // bpb) + 1) * bpb)
            out.append((p, s_, (nxt - s_) * 0.95, v))
            s_ = nxt
    return out, []


def data_harmony(cfg, c):       # dust: re-struck every beat while present, louder = more dust
    return _data_line(cfg, c, sustain=False, gate=True, vel_from_presence=True), []


def data_swell(cfg, c):         # smoke: held line, CC11 expression = amount of smoke
    if cfg.get("fade"):
        return _data_pad(cfg, c)
    notes = _data_line(cfg, c, sustain=True, gate=True, vel_from_presence=True)
    ccs = [(11, 25 + 102 * (p if np.isfinite(p) else 0), i) for i, p in enumerate(c["pres"])]
    return notes, ccs


def _data_pad(cfg, c):
    """A note each time the value CHANGES (yearly data: each January), held until the next change. It starts
    at full level and fades slowly over the whole note (the whole year) to `fade_floor` of it (CC11 expression):
    the new value is stated clearly, then stays as a softer background. pitch = value, loudness = amount.
    A piece that starts in the middle of a year fades over the months left until January."""
    grid, notes, ccs = _grid(cfg, c), [], []
    floor = float(cfg.get("fade_floor", 0.75))
    norm, pres = c["norm"], c["pres"]
    starts = [i for i, nv in enumerate(norm)
              if np.isfinite(nv) and np.isfinite(pres[i]) and pres[i] >= cfg["gate"]
              and (i == 0 or not np.isfinite(norm[i - 1]) or nv != norm[i - 1])]
    for k, i in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(norm)
        j = i
        while j < end and np.isfinite(norm[j]):           # stop at the first beat without data
            j += 1
        notes.append((_pitch(norm[i], grid), i, (j - i) * 0.98, cfg["level"] * (55 + 60 * pres[i])))
        steps = max(1, (j - i) * 4)                       # expression every quarter beat: a smooth fade
        for s in range(steps):
            g = floor + (1 - floor) * (1 - s / steps)
            ccs.append((11, round(127 * g), i + s / 4))
    return notes, ccs


def data_rhythm(cfg, c):        # rain: plucks per beat = amount of rain, pitch = value
    grid, notes = _grid(cfg, c), []
    for i, nv in enumerate(c["norm"]):
        pr = c["pres"][i]
        if not np.isfinite(nv) or not np.isfinite(pr) or pr < cfg["gate"]:
            continue
        n = max(1, int(round(pr * cfg.get("max_notes", 8))))
        p = _pitch(nv, grid)
        for j in range(n):
            notes.append((p, i + j / n, 0.9 / n, cfg["level"] * (45 + 80 * pr)))
    return notes, []


def data_timpani(cfg, c):       # lightning: hits per beat = flash density, pitch = value
    grid, notes = _grid(cfg, c), []
    for i, nv in enumerate(c["norm"]):
        pr = c["pres"][i]
        if not np.isfinite(nv) or not np.isfinite(pr) or pr < cfg["gate"]:
            continue
        n = max(1, int(round(pr * cfg.get("max_hits", 4))))
        p = _pitch(nv, grid)
        for j in range(n):
            notes.append((p, i + j / n, 0.9 / n, cfg["level"] * (50 + 77 * pr)))
    return notes, []


DATA_ROLES = {"melody": data_melody_intermittent, "bass": data_bass, "harmony": data_chords,
              "rhythm": data_rhythm, "swell": data_swell, "timpani": data_timpani}

MARKER_CFG = dict(name="Anomaly markers - Triangle (hot) / Woodblock (cold)", role="markers", program=0,
                  channel=9, low=76, high=81, gain=1.0, gate=0.0, levels=[], level=0.8, pan=64)
MARKER_HOT, MARKER_COLD = 81, 77      # GM drums: open triangle, low wood block


ROLES = {"melody": role_melody, "bass": role_bass, "harmony": role_harmony,
         "rhythm": role_rhythm, "swell": role_swell, "timpani": role_timpani}


def ending(parts, n_bars, bpb, key_pc, scale):
    """Final cadence: two bars on the tonic chord for melody, harmony and bass."""
    sl = scale_notes(key_pc, scale)
    tonic = chord_on(next(m for m in sl if m >= 36 and m % 12 == key_pc), sl)
    t0 = n_bars * bpb
    for p in parts:
        cfg, role = p["cfg"], p["cfg"]["role"]
        if not p["notes"]:
            continue                                     # instruments that never played stay silent
        last = [n[0] for n in p["notes"][-4:]]
        if role == "melody":
            q = nearest(last[-1], [m for m in range(cfg["low"], cfg["high"] + 1) if m % 12 == key_pc])
            p["notes"].append((q, t0, 2 * bpb, cfg["level"] * 80))
        elif role == "bass":
            q = nearest(last[-1], [m for m in range(cfg["low"], cfg["high"] + 1) if m % 12 == key_pc])
            p["notes"].append((q, t0, 2 * bpb, cfg["level"] * 85))
        elif role == "harmony" and p["notes"]:          # only if the piano actually played
            for q in tones(tonic["pcs"], cfg["low"], cfg["high"])[:4]:
                p["notes"].append((q, t0, 2 * bpb, cfg["level"] * 60))
    return t0 + 2 * bpb


# ----------------------------------------------------------------------------
# Preview: score lanes + stereo synth
# ----------------------------------------------------------------------------
COLORS = {"drums": "#6c757d", "markers": "#e07a5f", "melody": "#d1495b", "rhythm": "#00798c", "harmony": "#8f6593",
          "swell": "#edae49", "timpani": "#3d3d3d", "bass": "#30638e"}


def score_png(parts, harmony, total_beats, bpb, path, title, intro_beats=0, tension=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(parts), 1, figsize=(14, 1.7 * len(parts) + 0.8), sharex=True, squeeze=False)
    for li, (ax, p) in enumerate(zip(axes[:, 0], parts)):
        col = COLORS[p["cfg"]["role"]]
        for pitch, st, du, vel in p["notes"]:
            ax.add_patch(plt.Rectangle((st, pitch - 0.4), du, 0.8, color=col, alpha=0.3 + 0.7 * min(vel, 127) / 127))
        ps = [n[0] for n in p["notes"]] or [60]
        ax.set_ylim(min(ps) - 2, max(ps) + 2)
        for x in range(0, int(total_beats) + 1, bpb):
            ax.axvline(x, color="#999", lw=0.4, alpha=0.5)
        ax2 = ax.twinx()
        ax2.plot(np.arange(len(p["pres"])) + 0.5 + intro_beats, p["pres"], color=col, lw=1, alpha=0.5)
        if tension is not None:                       # red shading = anomaly tension
            for b, tv in enumerate(tension):
                if tv > 0.05:
                    ax.axvspan(intro_beats + b * bpb, intro_beats + (b + 1) * bpb, color="#d1495b", alpha=0.18 * tv, lw=0)
        if intro_beats:
            ax.axvspan(0, intro_beats, color="#999", alpha=0.08, lw=0)
        ax2.set_ylim(0, 1.05); ax2.set_yticks([])
        ax.set_yticks([])
        ax.set_ylabel(p["cfg"]["name"].replace(" - ", "\n"), rotation=0, ha="right", va="center", fontsize=8)
        if li == 0:
            for b, h in enumerate(harmony):
                if h is not None:
                    ax.text(intro_beats + b * bpb + 0.1, max(ps) + 1.2, h["name"], fontsize=6, color="#555", va="bottom")
    axes[0, 0].set_title(title)
    axes[-1, 0].set_xlabel("beats  (grey = intro; red shading = anomaly tension; curve = presence of the phenomenon)")
    axes[-1, 0].set_xlim(0, total_beats)
    fig.tight_layout(); fig.savefig(path, dpi=100); plt.close(fig)


def synth(role, pitch, dur, vel, sr):
    a = vel / 127
    f = 440 * 2 ** ((pitch - 69) / 12)
    if role == "drums":                       # preview only: a short noise burst
        L = int(0.12 * sr)
        return a * 0.3 * np.random.default_rng(pitch).standard_normal(L) * np.exp(-np.arange(L) / sr * 40)
    if role == "markers":
        L = int(0.8 * sr); t = np.arange(L) / sr
        f = 2600 if pitch == MARKER_HOT else 700
        return a * 0.6 * np.sin(2 * np.pi * f * t) * np.exp(-t * (4 if pitch == MARKER_HOT else 25))
    rel = {"melody": 0.3, "bass": 0.35, "harmony": 0.6, "rhythm": 0.25, "swell": 0.3, "timpani": 1.6}[role]
    L = int((dur + rel) * sr)
    t = np.arange(L) / sr

    def env(att, release):
        e = np.minimum(1, t / att)
        tail = t > dur
        e[tail] *= np.exp(-(t[tail] - dur) / (release / 4))
        return e

    if role == "melody":
        # Violin section. The phase is INTEGRATED from the instantaneous frequency, so the vibrato
        # stays a gentle ±0.25 % wobble (multiplying f by t made the pitch drift more and more).
        # Vibrato fades in after the attack, like a player; 3 players detuned by only ±2 cents.
        tone = np.zeros(L)
        for d, ph0, rate in ((0.9988, 0.0, 5.3), (1.0, 1.3, 5.6), (1.0012, 2.1, 5.9)):
            depth = 0.0025 * np.clip((t - 0.25) / 0.4, 0, 1)
            inst_f = f * d * (1 + depth * np.sin(2 * np.pi * rate * t + ph0))
            phase = 2 * np.pi * np.cumsum(inst_f) / sr
            tone += sum((0.55 ** h) * np.sin(h * phase) for h in range(1, 7))  # soft, rounded bowed tone
        tone /= 3
        return a * 0.8 * tone * env(0.14, rel)
    if role == "bass":         # contrabass: dark bowed
        tone = sum((0.6 ** h) * np.sin(2 * np.pi * f * h * t) for h in range(1, 7))
        return a * 1.3 * tone * env(0.07, rel)
    if role == "harmony":      # piano
        tone = sum(w * np.sin(2 * np.pi * f * h * t) for h, w in ((1, 1), (2, .4), (3, .18), (4, .08)))
        return a * 0.7 * tone * np.exp(-t * (1.2 + f / 900)) * env(0.004, rel)
    if role == "rhythm":       # pizzicato pluck
        tone = sum(w * np.sin(2 * np.pi * f * h * t) for h, w in ((1, 1), (2, .45), (3, .2)))
        return a * 0.8 * tone * np.exp(-t * 11) * np.minimum(1, t / 0.002)
    if role == "swell":        # trombones
        tone = sum(w * np.sin(2 * np.pi * f * h * t) for h, w in ((1, 1), (2, .8), (3, .55), (4, .35), (5, .2)))
        return a * 0.55 * tone * env(0.18, rel)
    # timpani: inharmonic partials + soft thump
    tone = sum(w * np.sin(2 * np.pi * f * r * t) for r, w in ((1, 1), (1.5, .5), (1.98, .3), (2.44, .15)))
    thump = np.random.default_rng(pitch).standard_normal(L) * np.exp(-t * 60) * 0.3
    return a * 1.2 * (tone * np.exp(-t * 2.8) + thump) * np.minimum(1, t / 0.003)


def render_wav(parts, path, bpm, total_beats, sr=22050):
    import wave
    spb = 60 / bpm
    N = int((total_beats * spb + 3) * sr)
    mix = np.zeros((N, 2))
    for p in parts:
        role, pan = p["cfg"]["role"], p["cfg"]["pan"] / 127
        tr = np.zeros(N)
        for pitch, st, du, vel in p["notes"]:
            s = synth(role, pitch, du * spb, vel, sr)
            i0 = int(st * spb * sr); i1 = min(N, i0 + len(s))
            tr[i0:i1] += s[: i1 - i0]
        if p["ccs"]:
            xs = np.array([cc[2] for cc in p["ccs"]]) * spb
            ys = np.array([cc[1] for cc in p["ccs"]]) / 127
            tr *= np.interp(np.arange(N) / sr, xs, ys, right=ys[-1])
        mix[:, 0] += tr * np.cos(pan * np.pi / 2)
        mix[:, 1] += tr * np.sin(pan * np.pi / 2)
    mix = mix / (np.abs(mix).max() + 1e-9) * 0.89
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())


# ----------------------------------------------------------------------------
# Public entry point (used by the CLI below and by the backend API)
# ----------------------------------------------------------------------------
class ComposeError(ValueError):
    """Bad request (unknown zone, no data in the zone, ...). The API maps it to HTTP 400."""


def resolve_palette(palette, zone=None, bbox=None):
    name = palette or "orchestra"
    if name == "auto":
        name = ZONE_PALETTE.get("custom" if bbox else (zone or "global"), "orchestra")
    if name not in PALETTES:
        raise ComposeError(f"unknown palette '{palette}'")
    return name


def _time_window(times, start, end, count=None):
    """Indices of frames within [start, end]; start/end are ISO dates (prefix ok) or frame indices.
    count: keep at most this many frames from the first one (a piece of a given length)."""
    n = len(times) if times is not None else None
    if start is None and end is None:
        return None if count is None else slice(0, count)
    if isinstance(start, int) or isinstance(end, int) or times is None:
        lo = int(start or 0); hi = int(end if end is not None else (n or 10**9) - 1)
    else:
        ts = [str(t) for t in times]
        idx = [i for i, t in enumerate(ts) if (start is None or t >= str(start)) and (end is None or t[:len(str(end))] <= str(end))]
        if not idx:
            raise ComposeError(f"no frames between {start} and {end}")
        lo, hi = idx[0], idx[-1]
    if count is not None:
        hi = min(hi, lo + count - 1)
    return slice(lo, hi + 1)


AREA_CACHE_SIZE = 512          # areas x variables kept: one entry is one float per frame (~14 kB for 1880 -> 2025)
_area_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_area_lock = threading.Lock()


def _area_key(z, mask):
    """(file, its version, the mask) or None when `z` does not come from a file (then nothing is cached)."""
    path = getattr(getattr(z, "zip", None), "filename", None)
    if not path:
        return None
    st = os.stat(path)
    return (os.path.realpath(path), st.st_mtime_ns, st.st_size, mask.shape,
            hashlib.sha1(np.packbits(mask)).hexdigest())


def area_mean(z, mask):
    """Mean of the map over `mask`, per frame: cos-latitude weighted, NaN ignored (zone_to_midi.zone_series).
    The ONE way an area is averaged: the music (compose) and the chart (POST /api/zones/series) both use it.

    Cached in memory per (file, mask): every 30 s chunk of the same zone needs the same full series (the anomaly
    is computed against the whole series), and reading it means decompressing the map's whole history. The
    returned array is read-only; a rebuilt file (new mtime/size) is a new key."""
    key = _area_key(z, mask)
    if key is not None:
        with _area_lock:
            hit = _area_cache.get(key)
            if hit is not None:
                _area_cache.move_to_end(key)
                return hit
    m, _ = zone_series(z["data"], z["lat"], mask)
    m.flags.writeable = False
    if key is not None:
        with _area_lock:
            _area_cache[key] = m
            while len(_area_cache) > AREA_CACHE_SIZE:
                _area_cache.popitem(last=False)
    return m


def area_series(src, cells, cells_grid=None, surface="all"):
    """Mean per frame of one map over an area given as cells: load_map -> cells_mask -> area_mean.

    src        : path of a map (.npz) or an already loaded (z, meta) pair from load_map
    cells      : [[row, col], ...] on the data array, or on `cells_grid` when it is given (rescaled)
    Returns (mean [T], mask [H, W], times [T] or None, meta). Raises ValueError for an invalid selection."""
    z, meta = load_map(str(src)) if isinstance(src, (str, Path)) else src
    land = z["land_fraction"] if "land_fraction" in z.files else None
    mask = cells_mask((len(z["lat"]), len(z["lon"])), cells, land, surface, grid=cells_grid)
    times = z["time"] if "time" in z.files else None
    return area_mean(z, mask), mask, times, meta


def _align_by_date(full: dict):
    """Put the variables on ONE time axis when their maps come from different datasets.

    Each variable can come from its own dataset, with its own first and last date (temperature since 1880,
    precipitation since 2000, lightning 2017-2023 ...). The common axis is every date any of them has; where
    a variable's own series does not reach, it gets NaN, which the instruments already treat as "no data":
    they stay silent there. Maps that already share their dates (one dataset) are left untouched.

    Returns the frames-per-bar hint to use for the merged axis (None = no opinion), and edits `full` in place."""
    lists = {v: (None if d["times"] is None else [str(x) for x in d["times"]]) for v, d in full.items()}
    if len(full) < 2 or any(ts is None for ts in lists.values()) or len({tuple(ts) for ts in lists.values()}) == 1:
        return None
    axis = sorted(set().union(*lists.values()))          # ISO dates sort chronologically
    where = {x: i for i, x in enumerate(axis)}
    for v, ts in lists.items():
        own = [where[x] for x in ts]
        if own != list(range(own[0], own[0] + len(own))):
            raise ComposeError(f"the maps do not share a time step ('{v}' falls between the dates of the others); "
                               f"take them from datasets with the same step, or from one dataset")
        d = full[v]
        for key in ("mean", "z"):
            out = np.full(len(axis), np.nan)
            out[own[0]: own[0] + len(own)] = d[key]
            d[key] = out
        d["times"] = np.array(axis)
    hints = {float(d["meta"]["frames_per_bar"]) for d in full.values() if d["meta"].get("frames_per_bar")}
    return hints.pop() if len(hints) == 1 else None     # only when the datasets agree on one


def compose(maps, zone="global", bbox=None, surface="all", start=None, end=None, loop=False,
            mapping="hybrid", key="C", scale=None, harmony="auto", chord_bars=2, bpm=None,
            beats_per_bar=4, frames_per_bar=None, add_ending=True, palette="orchestra",
            only=None, exclude=None, seed=7, out_dir=None, name=None,
            preview=False, render=False, reverb=0.28, audio_format="wav", verbose=False,
            intro=True, tension=True, climax=True, humanize=0.5,
            style="data", quantize="scale", markers=True, marker_threshold=1.5, audio_engine="auto",
            cells=None, cells_grid=None, duration_s=None, pitch_range="window", series_in=None, eras=False,
            genre="orchestra", tempo_from_temperature=True, temperature_sound="heartbeat"):
    """Data maps -> score. Writes MIDI (+ stems, JSON, optional PNG/audio) to out_dir and returns
    a dict with the score document, file paths and stats.

    The area is a preset `zone`, a lat/lon `bbox`, or `cells`: a list of [row, col] positions on
    the data array (any shape of area). `cells` wins over `bbox`, which wins over `zone`.

    `maps` can mix datasets (one map per variable): the variables are then lined up by date, and an
    instrument is silent for the dates its own data does not cover (see _align_by_date).

    pitch_range: what "low" and "high" mean for the pitch of an instrument (mapping hybrid / relative).
    "window" (default): the lowest and highest value of the piece itself, so every piece uses the whole
    register. "series": the lowest and highest value of the WHOLE series of this area, so the same value
    is the same note in every piece cut from that series. Needed when a long series is played in chunks,
    and for yearly data, which is constant inside a short piece.

    duration_s: play this many seconds of data starting at `start` (instead of giving `end`).
    As many bars as fit at about `bpm` are taken, then the tempo is nudged so that they last
    exactly duration_s. Near the end of the dataset, where fewer frames are left, the piece is shorter.

    series_in: {variable: (values, meta)} played instead of reading `maps` (vinyl mode, music/groove.py:
    one value per PLACE along the needle's path, at one date). The pitch scale of each variable is then its
    dataset's global range (meta vmin / vmax), so the same value is the same note wherever the needle is.
    `bbox` should be given (the box around the path); start / end / duration_s do not apply.

    eras (fun mode, data style only): the date of each bar picks the music of its time, from a Romantic
    orchestra before 1910 to modern pop / trap after 2010 (see eras.py). Pitch and loudness stay the data's;
    instruments, comping rhythm, swing and a drum kit change with the era. No anomaly markers in this mode.

    genre: the musical style of the whole piece (data style only): "orchestra" (default), "ragtime", "swing",
    "rocknroll", "funk", "synthpop", "house", "trap" (eras.py: one instrument set and groove from the first bar to
    the last), or "realistic": every variable is heard as the sound of what it is (rain, fire, thunder, a heartbeat
    ...; soundscape.py). bpm None = the genre's usual tempo (76 for the orchestra and the realistic mode).

    tempo_from_temperature (data style, default on): each bar's tempo follows the temperature of that bar, scaled on
    the whole series of the area (5th..95th percentile; the same temperature = the same tempo in every piece):
    bpm x 0.75 (cold) to x 1.25 (hot); in the realistic mode a heartbeat from 60 to 130 bpm. With duration_s, the number of bars is
    chosen with these tempos so that the piece still lasts duration_s (a hot chunk covers more months).

    temperature_sound (realistic mode): "heartbeat" (default) or "clock" (a ticking clock, tick-tock on each beat)."""
    log = print if verbose else (lambda *a, **k: None)
    if style not in ("data", "musical"):
        raise ComposeError("style must be 'data' or 'musical'")
    if quantize not in ("scale", "pentatonic", "chromatic"):
        raise ComposeError("quantize must be 'scale', 'pentatonic' or 'chromatic'")
    if pitch_range not in ("window", "series"):
        raise ComposeError("pitch_range must be 'window' or 'series'")
    musical = style == "musical"
    import eras as era_mod
    gkey = "realistic" if str(genre or "").strip().lower() in ("realistic", "realiste", "réaliste", "nature") \
        else era_mod.genre_key(genre)
    if gkey is None:
        raise ComposeError(f"unknown genre '{genre}'; available: {', '.join(list(era_mod.GENRES) + ['realistic'])}")
    if (eras or gkey != "orchestra") and musical:
        raise ComposeError("eras and genres work with style 'data' (the default)")
    if eras and gkey != "orchestra":
        raise ComposeError("choose either eras (the style follows the date) or a genre (one style), not both")
    if bpm is None:
        bpm = float(era_mod.GENRES[gkey]["bpm"]) if gkey in era_mod.GENRES else 76.0
    if eras or gkey != "orchestra":
        markers = False          # a drum kit takes the percussion channel; the realistic mode is sounds only
    if not musical:      # data-faithful: nothing that isn't in the data
        intro, climax, humanize, add_ending = False, False, None, False
    bars_wanted = None
    base_bpm = bpm
    if duration_s is not None:
        if not duration_s > 0:
            raise ComposeError("duration_s must be a positive number of seconds")
        bars_wanted = max(1, int(round(duration_s * bpm / (60.0 * beats_per_bar))))
        bpm = round(bars_wanted * beats_per_bar * 60.0 / duration_s, 3)     # these bars last exactly duration_s
    use_cells = cells is not None
    pal_name = resolve_palette(palette, zone, use_cells or bbox)
    orch = apply_palette(pal_name)
    scale = scale or PALETTE_SCALE.get(pal_name, "major")
    if scale not in SEVEN_NOTE_SCALES:
        raise ComposeError(f"scale must be one of {SEVEN_NOTE_SCALES}")
    n_cells = None
    if use_cells:
        bbox, zone_label = None, "custom"     # bbox is reported below as the box around the cells
    else:
        if bbox is None and zone not in ZONES:
            raise ComposeError(f"unknown zone '{zone}'")
        bbox = tuple(float(x) for x in bbox) if bbox is not None else ZONES[zone]
        if len(bbox) != 4 or not (-90 <= bbox[0] < bbox[1] <= 90) or not all(-180 <= x <= 180 for x in bbox[2:]):
            raise ComposeError("bbox must be [lat_min, lat_max, lon_min, lon_max] with lat_min < lat_max")
        zone_label = "custom" if zone is None or zone == "custom" or (zone not in ZONES) else zone
    only = set(only) if only else None
    excl = set(exclude or [])
    try:
        key_pc = parse_key(key)
    except (ValueError, IndexError):
        raise ComposeError(f"invalid key '{key}'")

    # ---- zone mean per frame for every variable
    series, full = {}, {}
    if series_in is not None:              # vinyl mode: values along a path in space, given by the caller
        for var, (vals, meta) in series_in.items():
            if var not in orch or (only and var not in only) or var in excl:
                continue
            m = np.asarray(vals, dtype=np.float64)
            lo, hi = meta.get("vmin"), meta.get("vmax")
            span = (float(lo), float(hi)) if lo is not None and hi is not None and hi > lo else None
            full[var] = dict(mean=m, z=np.zeros(len(m)), meta=meta, times=None, file=None, span=span)
    files = [] if series_in is not None else collect_maps(maps if isinstance(maps, (list, tuple)) else [maps])
    if series_in is None and not files:
        raise ComposeError("no map files found")
    for f in files:
        z, meta = load_map(str(f))
        var = meta.get("variable")
        if var not in orch or (only and var not in only) or var in excl:
            continue
        land = z["land_fraction"] if "land_fraction" in z.files else None
        try:
            if use_cells:
                m, mask, times, _ = area_series((z, meta), cells, cells_grid, surface)
                if bbox is None:
                    la, lo = z["lat"][mask.any(axis=1)], z["lon"][mask.any(axis=0)]
                    bbox = (float(la.min()), float(la.max()), float(lo.min()), float(lo.max()))
                    n_cells = int(mask.sum())
            else:
                mask = zone_mask(z["lat"], z["lon"], bbox, land, surface)
                times = z["time"] if "time" in z.files else None
                m = area_mean(z, mask)
        except ValueError as e:
            raise ComposeError(str(e))
        # anomaly vs the seasonal normal, computed on the FULL series before any time window
        _, zsc, _ = comp.seasonal_anomaly(m, times, log=var in comp.SKEWED,
                                          already_anomaly=bool(meta.get("anomaly")))
        if var in full:
            raise ComposeError(f"two maps for '{var}' ({full[var]['file']} and {Path(f).name}): give one per variable")
        full[var] = dict(mean=m, z=zsc, meta=meta, times=times, file=Path(f).name)
    for d in full.values():                # lowest and highest value of the WHOLE series of this area
        if d.get("span") is not None:      # vinyl mode: the dataset's global range, set above
            continue
        ok = np.isfinite(d["mean"])
        d["span"] = (float(d["mean"][ok].min()), float(d["mean"][ok].max())) if ok.any() else (np.nan, np.nan)
    hint = _align_by_date(full)            # maps from different datasets -> one time axis (no-op for one dataset)

    # ---- the temperature sets the tempo: the hotter the bar, the faster (data style)
    temp_tempo = None
    # (not in vinyl mode: there the values are places along a path, at one date, on Teo's fixed grid of points per bar)
    if tempo_from_temperature and series_in is None and not musical and LEAD_VAR in full \
            and np.isfinite(full[LEAD_VAR]["span"][0]):
        dT = full[LEAD_VAR]
        # robust scale: the 5th..95th percentile of the whole series of the area (a few extreme months would
        # otherwise squeeze every ordinary month into the middle tempos)
        lo_, hi_ = (float(q) for q in np.nanpercentile(dT["mean"], [5, 95]))

        def bar_bpm(seg, lo_=lo_, hi_=hi_):
            ok = seg[np.isfinite(seg)] if len(seg) else seg
            x = float(np.clip((ok.mean() - lo_) / ((hi_ - lo_) or 1), 0, 1)) if len(ok) else 0.5
            return 60.0 + 70.0 * x if gkey == "realistic" else base_bpm * (0.75 + 0.5 * x)
        fpb_t = float(frames_per_bar or hint or dT["meta"].get("frames_per_bar", 1.0))
        temp_tempo = dict(bar_bpm=bar_bpm, scale=1.0)
        if bars_wanted is not None:        # as many bars as fit in duration_s at THEIR tempo
            win0 = _time_window(dT["times"], start, end, None)
            i0, i1 = (0, len(dT["mean"])) if win0 is None else (win0.start, win0.stop)
            total, n = 0.0, 0
            while i0 + n * fpb_t < i1 - 1e-9:
                a, b = int(round(i0 + n * fpb_t)), int(round(i0 + (n + 1) * fpb_t))
                d_ = int(beats_per_bar) * 60.0 / bar_bpm(dT["mean"][a:max(b, a + 1)])
                if n > 0 and total + d_ / 2 > duration_s:
                    break
                total, n = total + d_, n + 1
            bars_wanted = n
            temp_tempo["scale"] = total / duration_s if total < duration_s * 2 else 1.0
    for var, d in full.items():
        m, zsc, meta, times = d["mean"], d["z"], d["meta"], d["times"]
        max_frames = None
        if bars_wanted is not None:        # frames per bar: the request, else the dataset hint (daily data: 7)
            max_frames = max(1, int(round(bars_wanted * float(frames_per_bar or hint or meta.get("frames_per_bar", 1.0)))))
        win = _time_window(times, start, end, max_frames)
        if win is not None:
            m, zsc = m[win], zsc[win]
            times = times[win] if times is not None else None
        if len(m) < 1 or not np.isfinite(m).any():
            continue                       # nothing of this variable in the window: its instrument stays out
        series[var] = dict(mean=m, z=zsc, meta=meta, times=times, span=d["span"])
    if not series:
        raise ComposeError("no usable data for this zone / selection")

    ref_var = next((v for v in HARMONY_PRIORITY if v in series), next(iter(series)))
    n_ref = len(series[ref_var]["mean"])
    bpb = int(beats_per_bar)
    fpb = float(frames_per_bar if frames_per_bar else hint or series[ref_var]["meta"].get("frames_per_bar", 1.0))
    n_bars = max(1, int(np.ceil(n_ref / fpb)))
    n_beats = n_bars * bpb
    sl = scale_notes(key_pc, scale)
    rng = np.random.default_rng(seed)
    bar_tempi = None
    if temp_tempo is not None and LEAD_VAR in series:   # one tempo per bar, from the temperature of the bar
        mT = series[LEAD_VAR]["mean"]
        bar_tempi = [round(temp_tempo["bar_bpm"](mT[int(round(b * fpb)):max(int(round((b + 1) * fpb)),
                                                                          int(round(b * fpb)) + 1)])
                           * temp_tempo["scale"], 3) for b in range(n_bars)]
        bpm = round(base_bpm * temp_tempo["scale"], 3) if gkey != "realistic" else round(float(np.median(bar_tempi)), 3)

    for var, d in series.items():
        cfg = orch[var]
        smooth = cfg.get("smooth", cfg["role"] in SMOOTH_ROLES)     # an instrument can opt out (yearly steps)
        vals = sample_beats(d["mean"], n_ref, n_beats, fpb, bpb, loop, smooth)
        vmin, vmax = d["meta"].get("vmin", np.nanmin(vals)), d["meta"].get("vmax", np.nanmax(vals))
        absn = np.clip((vals - vmin) / ((vmax - vmin) or 1), 0, 1)
        reln = rel_norm(vals, d.get("span") if pitch_range == "series" else None)
        d["values"] = vals
        d["norm"] = absn if mapping == "absolute" else reln
        pres = np.clip((reln if mapping == "relative" else absn) * cfg["gain"], 0, 1)
        if cfg.get("always_on"):
            pres = np.where(np.isfinite(vals), np.maximum(pres, 0.35), np.nan)
        d["pres"] = pres
        d["bar_norm"], d["bar_pres"] = per_bar(d["norm"], bpb), per_bar(pres, bpb)
        d["levels"] = bar_levels(d["bar_pres"], cfg["gate"], cfg["levels"])
        d["bar_z"] = per_bar(sample_beats(d["z"], n_ref, n_beats, fpb, bpb, loop, smooth), bpb)

    # ---- tension (anomalies) -> harmony colour
    tens_info = comp.tension_curve({v: d["bar_z"] for v, d in series.items()})
    use_tension = tension and musical
    tens = tens_info if use_tension else np.zeros(n_bars)
    modes = comp.modes_with_hysteresis(tens) if use_tension else (["major"] * n_bars if musical else [None] * n_bars)
    hsrc = None if harmony == "progression" else (
        harmony if harmony != "auto" else next((v for v in HARMONY_PRIORITY if v in series), None))
    if hsrc and hsrc in series:
        chords = harmony_from(series[hsrc]["bar_norm"], key_pc, scale, chord_bars)
    else:
        hsrc = None
        chords = harmony_progression(n_bars, key_pc, scale)
    chords, bar_scales = darken(chords, tens, modes, key_pc, scale) if use_tension else (chords, None)
    if not musical:
        chords = [None] * n_bars       # no harmony in data style: voices follow their own data

    if quantize == "chromatic":
        qnotes = lambda lo_, hi_: list(range(lo_, hi_ + 1))  # noqa: E731
    elif quantize == "pentatonic":
        pent = scale_notes(key_pc, "minor_pentatonic" if scale in ("minor", "harmonic_minor", "dorian") else "pentatonic")
        qnotes = lambda lo_, hi_: [m for m in pent if lo_ <= m <= hi_]  # noqa: E731
    else:
        qnotes = lambda lo_, hi_: [m for m in sl if lo_ <= m <= hi_]  # noqa: E731

    # ---- arrange the data section (beat 0 = first data bar)
    parts = []
    for var, cfg in orch.items():
        if var not in series:
            continue
        d = series[var]
        ctx = dict(harmony=chords, norm=d["norm"], pres=d["pres"], bar_norm=d["bar_norm"],
                   bar_pres=d["bar_pres"], levels=d["levels"], bpb=bpb, sl=sl, rng=rng,
                   tension=tens, bar_scale=bar_scales, qnotes=qnotes)
        notes, ccs = (ROLES if musical else DATA_ROLES)[cfg["role"]](cfg, ctx)
        parts.append(dict(var=var, cfg=cfg, notes=list(notes), ccs=list(ccs), pres=np.nan_to_num(d["pres"])))

    # ---- anomaly markers (data style): triangle = unusually hot month, woodblock = unusually cold
    marker_var = LEAD_VAR if LEAD_VAR in series else None
    # not when the temperature is already an anomaly: the piano itself plays the anomaly, a second track would repeat it
    if marker_var and series[marker_var]["meta"].get("anomaly"):
        marker_var = None
    if markers and not musical and marker_var:
        zb = series[marker_var]["bar_z"]
        mnotes, mlev = [], []
        for b, zz in enumerate(zb):
            hit = np.isfinite(zz) and abs(zz) >= marker_threshold
            mlev.append(1 if hit else 0)
            if hit:
                vel = 70 + 57 * min(1.0, (abs(zz) - marker_threshold) / 1.5)
                mnotes.append((MARKER_HOT if zz > 0 else MARKER_COLD, b * bpb, 1.0, vel))
        zbeat = np.repeat(np.nan_to_num(zb), bpb)
        series["anomaly_markers"] = dict(values=zbeat, bar_z=zb, levels=mlev,
                                         meta={"units": "z (temperature anomaly)", "source": "derived from temperature"})
        parts.append(dict(var="anomaly_markers", cfg=dict(MARKER_CFG), notes=mnotes, ccs=[],
                          pres=np.clip(np.abs(zbeat) / 3, 0, 1)))

    # ---- music through the ages: each bar played in the style of its date
    era_segments, bar_eras = None, None
    if eras or gkey not in ("orchestra", "realistic"):
        if eras:
            times_ = series[ref_var]["times"]
            if times_ is None:
                raise ComposeError("eras needs dated data (the maps have no time axis)")
            keys = [era_mod.era_of_year(int(str(times_[min(int(b * fpb), len(times_) - 1)])[:4]))["key"]
                    for b in range(n_bars)]
        else:                    # one genre for the whole piece: the names say which instrument plays what
            keys = [gkey] * n_bars
            for p in parts:
                label = p["cfg"]["name"].split(" - ")[0]
                p["cfg"] = dict(p["cfg"], name=f"{label} - " + era_mod.instrument_name(
                    gkey, p["var"], p["cfg"]["role"], p["cfg"]["name"].partition(" - ")[2], p["cfg"]["program"]))
        era_segments, bar_eras = era_mod.apply(parts, keys, bpb, sl, n_bars, {p["cfg"]["channel"] for p in parts},
                                               era_names=eras)
        if not eras:
            bar_eras = None
        if any(p["var"] == "era_drums" for p in parts):
            series["era_drums"] = dict(values=np.full(n_bars * bpb, np.nan), bar_z=np.full(n_bars, np.nan),
                                       levels=[1] * n_bars, meta={"units": None, "source": "the drum kit of the style"})
    if temperature_sound not in ("heartbeat", "clock"):
        raise ComposeError("temperature_sound must be 'heartbeat' or 'clock'")
    if gkey == "realistic":
        import soundscape
        for p in parts:
            label = p["cfg"]["name"].split(" - ")[0]
            sound = "Ticking clock (faster when warmer)" if (p["var"] == LEAD_VAR and temperature_sound == "clock") \
                else soundscape.SOUND_NAMES.get(p["var"], "(silent)")
            p["cfg"] = dict(p["cfg"], name=f"{label} - {sound}")

    # ---- climax: the most anomalous bar
    tz = np.nan_to_num(np.abs(series[LEAD_VAR]["bar_z"])) if LEAD_VAR in series else np.zeros(n_bars)
    climax_bar = int(np.argmax(tens + 0.01 * tz)) if (climax and use_tension and tens.max() >= 0.4) else None
    if climax_bar is not None:
        c0, c1 = climax_bar * bpb, (climax_bar + 2) * bpb
        for p in parts:
            boosted = [(pt, st, du, v * 1.15 if c0 <= st < c1 else v) for pt, st, du, v in p["notes"]]
            if p["cfg"]["role"] == "melody":      # violins doubled an octave up
                boosted += [(pt + 12, st, du, v * 0.85) for pt, st, du, v in p["notes"] if c0 <= st < c1 and pt + 12 <= 100]
            if p["cfg"]["role"] == "timpani" and climax_bar > 0 and chords[climax_bar]:
                lo_, hi_ = p["cfg"]["low"], p["cfg"]["high"]
                r = nearest((lo_ + hi_) / 2, [m for m in range(lo_, hi_ + 1) if m % 12 == chords[climax_bar]["root_pc"]])
                boosted += [(r, c0 - 1 + j / 8, 0.12, 60 + 8 * j) for j in range(8)]   # roll into the climax
                boosted.append((r, c0, 1.5, 127))
            p["notes"] = boosted

    # ---- outro: final cadence on the tonic (data-relative), slowed down by the tempo map
    end_data = n_beats
    total_data = ending(parts, n_bars, bpb, key_pc, scale) if add_ending else n_beats

    # ---- intro: "meet the orchestra" (instruments enter one by one)
    intro_beats, entries = 0, []
    if intro:
        order = ["bass", "harmony", "timpani", "swell", "rhythm", "melody"]
        active = [p for p in parts if p["cfg"].get("always_on") or max(series[p["var"]]["levels"]) > 0]
        active.sort(key=lambda p: order.index(p["cfg"]["role"]))
        n_in = len(active) + 1
        i0 = next(i for i, m in enumerate(sl) if m >= 36 and m % 12 == key_pc)
        prog = [0, 3, 5, 4]                                   # I IV vi V ... then V into the data
        intro_chords = [chord_on(sl[i0 + prog[b % 4]], sl) for b in range(n_in - 1)] + [chord_on(sl[i0 + 4], sl)]
        nb = n_in * bpb
        for k, p in enumerate(active):
            cfg = p["cfg"]
            norm = np.linspace(0.25, 0.6, nb) if cfg["role"] == "melody" else np.full(nb, 0.45)
            norm[: k * bpb] = np.nan
            pres = np.where(np.isfinite(norm), 0.55, 0.0)
            ctx_i = dict(harmony=intro_chords, norm=norm, pres=pres, bar_norm=per_bar(norm, bpb),
                         bar_pres=per_bar(pres, bpb), levels=[0] * k + [1] * (n_in - k), bpb=bpb, sl=sl,
                         rng=rng, tension=np.zeros(n_in), bar_scale=None)
            inotes, iccs = ROLES[cfg["role"]](cfg, ctx_i)
            p["intro_notes"], p["intro_ccs"] = list(inotes), list(iccs)
            entries.append({"bar": k, "variable": p["var"], "instrument": cfg["name"], "role": cfg["role"],
                            "units": series[p["var"]]["meta"].get("units")})
        intro_beats = nb
    for p in parts:                                          # shift the data section after the intro
        p["notes"] = p.pop("intro_notes", []) + [(pt, st + intro_beats, du, v) for pt, st, du, v in p["notes"]]
        p["ccs"] = p.pop("intro_ccs", []) + [(cn, cv, cb + intro_beats) for cn, cv, cb in p["ccs"]]
    total_beats = intro_beats + total_data
    outro_start = intro_beats + end_data if add_ending else None

    # ---- expression: phrase hairpins, tension dynamics, humanisation, CC11 swells
    def tension_at(beat):
        b_ = int((beat - intro_beats) // bpb)
        return 0.15 if beat < intro_beats else (float(tens[b_]) if 0 <= b_ < n_bars else 0.0)
    if humanize is not None:
        comp.apply_expression(parts, bpb, rng, tension_at, phrase_offset=intro_beats, humanize=humanize)
        for p in parts:
            if p["cfg"]["role"] in ("melody", "bass") and p["var"] != "aerosol_smoke":
                p["ccs"] += comp.expression_cc(total_beats, bpb, intro_beats, tension_at)
    for p in parts:
        p["notes"] = sorted((int(pt), float(st), float(du), int(np.clip(v, 1, 127))) for pt, st, du, v in p["notes"])
        p["ccs"] = sorted(p["ccs"], key=lambda c_: c_[2])
    if bar_tempi is not None:
        tmap = [(float(intro_beats + b * bpb), t) for b, t in enumerate(bar_tempi)
                if b == 0 or t != bar_tempi[b - 1]]
    else:
        tmap = comp.tempo_map(bpm, total_beats, outro_start)
    b2s = lambda b: comp.beat_to_sec(b, tmap)  # noqa: E731

    # ---- files
    out = Path(out_dir) if out_dir else Path(__file__).resolve().parent / "output"
    out.mkdir(parents=True, exist_ok=True)
    name = name or (f"orchestra_{zone_label}_{surface}" + ("" if pal_name == "orchestra" else f"_{pal_name}"))
    stems_dir = out / f"{name}_stems"
    stems_dir.mkdir(exist_ok=True)
    paths = {"midi": out / f"{name}.mid", "json": out / f"{name}.json", "stems": {}}
    score = MidiFile(bpm=tmap[0][1], time_sig=(bpb, 4))
    for p in parts:
        cfg = p["cfg"]
        stem = MidiFile(bpm=tmap[0][1], time_sig=(bpb, 4))
        for mf in (score, stem):
            for tb, tbpm in tmap[1:]:
                mf.tempo(tb, tbpm)
            for tr in p.get("era_tracks") or [dict(name=cfg["name"], channel=cfg["channel"],
                                                       programs=[(0, cfg["program"])], notes=p["notes"])]:
                t = mf.add_track(tr["name"], tr["programs"][0][1], tr["channel"])
                t.cc(7, 100); t.cc(10, cfg["pan"]); t.cc(11, 127 if not p["ccs"] else 0)
                for b, prog in tr["programs"][1:]:
                    t.program_change(prog, b)
                for cnum, v, b in p["ccs"]:
                    t.cc(cnum, v, b)
                for pitch, st, du, vel in tr["notes"]:
                    t.note(int(pitch), float(st), float(du), int(np.clip(vel, 1, 127)))
        stem.save(stems_dir / f"{p['var']}.mid")
        paths["stems"][p["var"]] = stems_dir / f"{p['var']}.mid"
    marker_track = score.add_track("Sections & chords (markers)", 0, 15)
    for e in entries:
        marker_track.marker(f"enters: {e['instrument']}", e["bar"] * bpb)
    for b, h in enumerate(chords):
        if h is not None:
            marker_track.marker(h["name"] + ("  [CLIMAX]" if b == climax_bar else ""), intro_beats + b * bpb)
    score.save(paths["midi"])

    times = series[ref_var]["times"]
    def bar_time(b):
        return None if times is None else str(times[min(int(b * fpb), len(times) - 1)])
    grid = np.zeros(int(total_beats * 4) + 8)
    for p in parts:
        for pitch, st, du, vel in p["notes"]:
            grid[int(st * 4): int((st + du) * 4)] += 1
    intro_bars = intro_beats // bpb
    sections = []
    if intro:
        sections.append({"name": "intro", "start_bar": 0, "bars": intro_bars, "start_s": 0.0,
                         "entries": [{**e, "start_s": round(b2s(e["bar"] * bpb), 3)} for e in entries]})
    sections.append({"name": "data", "start_bar": intro_bars, "bars": n_bars, "start_s": round(b2s(intro_beats), 3)})
    if climax_bar is not None:
        sections.append({"name": "climax", "start_bar": intro_bars + climax_bar, "bars": 2,
                         "start_s": round(b2s(intro_beats + climax_bar * bpb), 3), "time": bar_time(climax_bar),
                         "tension": round(float(tens[climax_bar]), 3)})
    if add_ending:
        sections.append({"name": "outro", "start_bar": intro_bars + n_bars, "bars": (total_data - end_data) // bpb,
                         "start_s": round(b2s(outro_start), 3)})
    doc = {
        "style": style, "quantize": quantize,
        "zone": zone_label, "bbox": list(bbox), "cells": n_cells, "surface": surface, "palette": pal_name,
        "key": key, "scale": scale, "bpm": bpm, "beats_per_bar": bpb, "frames_per_bar": fpb,
        "n_bars": n_bars, "intro_bars": intro_bars, "total_beats": total_beats,
        "duration_s": round(b2s(total_beats), 2),
        "tempo_map": [{"beat": tb, "bpm": round(tp, 2)} for tb, tp in tmap],
        "time_start": bar_time(0), "time_end": bar_time(n_bars - 1),
        "time_last_frame": None if times is None else str(times[-1]),     # time_end is the START of the last bar
        "harmony_source": hsrc or "progression",
        "sections": sections,
        "bars": [{"index": b, "start_beat": intro_beats + b * bpb, "start_s": round(b2s(intro_beats + b * bpb), 3),
                  "time": bar_time(b), "chord": None if h is None else h["name"],
                  "tension": round(float(tens_info[b]), 3), "mode": modes[b], "climax": b == climax_bar,
                  **({"era": bar_eras[b]} if bar_eras else {})}
                 for b, h in enumerate(chords)],
        # eras mode: [{key, name, start_bar, bars, start_s, time}], the musical era of each stretch of the piece
        "genre": gkey,
        "tempo_from_temperature": bar_tempi is not None,
        "tempo_range": [min(t for _, t in tmap), max(t for _, t in tmap)],
        "eras": None if not (eras and era_segments) else [
            {**sg, "start_s": round(b2s(intro_beats + sg["start_bar"] * bpb), 3), "time": bar_time(sg["start_bar"])}
            for sg in era_segments],
        "instruments": {p["var"]: {
            "name": p["cfg"]["name"], "role": p["cfg"]["role"], "program": p["cfg"]["program"],
            "channel": p["cfg"]["channel"], "pan": p["cfg"]["pan"], "register": [p["cfg"]["low"], p["cfg"]["high"]],
            "units": series[p["var"]]["meta"].get("units"), "source": series[p["var"]]["meta"].get("source"),
            "anomaly": bool(series[p["var"]]["meta"].get("anomaly", False)),      # values are anomalies, not levels
            "values_per_beat": [None if not np.isfinite(x) else round(float(x), 4) for x in series[p["var"]]["values"]],
            "presence_per_beat": [round(float(x), 3) for x in p["pres"]],
            "pitch_range": [min((n[0] for n in p["notes"]), default=None), max((n[0] for n in p["notes"]), default=None)],
            "anomaly_z_per_bar": [None if not np.isfinite(x) else round(float(x), 2) for x in series[p["var"]]["bar_z"]],
            "levels_per_bar": [int(x) for x in series[p["var"]]["levels"]],
            "bars_playing": int(sum(l > 0 for l in series[p["var"]]["levels"])),
            "notes": [{"pitch": n[0], "start": round(n[1], 4), "duration": round(n[2], 4), "velocity": n[3],
                       "time_s": round(b2s(n[1]), 4), "duration_s": round(b2s(n[1] + n[2]) - b2s(n[1]), 4)}
                      for n in p["notes"]],
        } for p in parts},
        "stats": {"notes_total": sum(len(p["notes"]) for p in parts),
                  "polyphony_mean": round(float(grid[grid > 0].mean()), 2) if (grid > 0).any() else 0,
                  "polyphony_max": int(grid.max()),
                  "tense_bars": int(sum(t_ >= 0.5 for t_ in tens_info)), "max_tension": round(float(tens_info.max()), 3)},
    }
    paths["json"].write_text(json.dumps(doc))

    log(f"palette: {pal_name}")
    log(f"zone {zone_label} [{surface}]  {n_ref} frames -> {n_bars} bars of {bpb}  "
        f"key {key} {scale}  harmony from {hsrc or 'progression'}")
    log("chords: " + " ".join(h["name"] if h else "-" for h in chords[:24]) + (" ..." if n_bars > 24 else ""))
    for p in parts:
        lv = series[p["var"]]["levels"]
        log(f"  {p['cfg']['name']:26s} notes {len(p['notes']):4d}  bars playing {sum(l > 0 for l in lv):3d}/{n_bars}"
            f"  levels {''.join(str(min(l, 9)) for l in lv[:32])}")
    log(f"simultaneous notes: mean {doc['stats']['polyphony_mean']}, max {doc['stats']['polyphony_max']}")
    log(f"style: {style} (quantize={quantize})")
    log("tension: " + "".join(" .:-=+*#%@"[min(9, int(t_ * 9.99))] for t_ in tens_info[:96]) +
        f"   (max {tens_info.max():.2f}, {doc['stats']['tense_bars']} tense bars)")
    log("form: " + " | ".join(f"{s_['name']} @ {s_['start_s']}s" for s_ in sections) + f" | end {doc['duration_s']}s")
    log(f"wrote {paths['midi']}  (+ stems/, .json)")

    if preview:
        paths["score_png"] = out / f"{name}_score.png"
        paths["preview_wav"] = out / f"{name}_preview.wav"
        score_png(parts, chords, total_beats, bpb, paths["score_png"],
                  f"Earth Orchestra - {zone_label} ({surface}) - {style} style - {pal_name} palette - {key} {scale}",
                  intro_beats=intro_beats, tension=tens_info)
        render_wav(parts, paths["preview_wav"], bpm, total_beats)
        log(f"wrote {name}_score.png and {name}_preview.wav")
    if render:
        import render_audio
        if gkey == "realistic":           # recordings of rain, fire, thunder ... instead of instruments
            import soundscape
            if not soundscape.ensure_ambience(verbose=verbose):
                raise ComposeError("the realistic sounds could not be downloaded (python music/soundscape.py --download)")
            layers = [dict(var=p["var"], role=p["cfg"]["role"], norm=series[p["var"]]["norm"],
                           pres=series[p["var"]]["pres"], gate=p["cfg"]["gate"]) for p in parts]
            x, sr = soundscape.render(layers, [b2s(b) for b in range(total_beats + 1)], b2s(total_beats),
                                      temperature_sound=temperature_sound)
        else:
            sf2 = render_audio.ensure_soundfont(render_audio.DEFAULT_SF2)
            x, sr = render_audio.render(paths["midi"], sf2, reverb=reverb, engine=audio_engine)
        wav = out / f"{name}.wav"
        render_audio.save_wav(x, sr, wav)
        paths["audio"] = wav
        if audio_format == "mp3":
            mp3 = render_audio.to_mp3(wav)
            if mp3:
                wav.unlink()
                paths["audio"] = mp3
        import sampler
        eng = "VSCO 2 CE orchestral samples + GeneralUser GS" if (audio_engine != "sf2" and sampler.vsco_available()) else "GeneralUser GS"
        eng = "realistic recordings" if gkey == "realistic" else eng
        log(f"wrote {paths['audio'].name}  ({eng}, {len(x) / sr:.0f} s)")
    return {"name": name, "doc": doc, "paths": paths}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("maps", nargs="*", help=".npz files, a folder, or a prefix like output/sim_both_24f_1deg_")
    ap.add_argument("--zone", default="global")
    ap.add_argument("--bbox", help="lat_min,lat_max,lon_min,lon_max")
    ap.add_argument("--cells", help=".npy file with an (N, 2) integer array of [row, col] positions on the data "
                                    "array (overrides --zone and --bbox)")
    ap.add_argument("--cells-grid", help="rows,cols of the array the positions refer to, if not the data grid")
    ap.add_argument("--surface", choices=["all", "land", "ocean"], default="all")
    ap.add_argument("--start", help="first frame: ISO date (e.g. 2024-06) or frame index")
    ap.add_argument("--end", help="last frame: ISO date or frame index")
    ap.add_argument("--duration", type=float, help="seconds of music from --start (instead of --end); the tempo is "
                                                   "nudged so the bars that fit last exactly this long")
    ap.add_argument("--loop", action="store_true", help="cyclic data (e.g. 12 months)")
    ap.add_argument("--mapping", choices=["hybrid", "relative", "absolute"], default="hybrid",
                    help="hybrid: melodic contour from zone-relative values, activity from absolute amounts")
    ap.add_argument("--key", default="C")
    ap.add_argument("--scale", choices=SEVEN_NOTE_SCALES, default=None,
                    help="default: major (or the palette's mode, e.g. dorian for celtic)")
    ap.add_argument("--harmony", choices=["auto", "co2", "surface_temperature", "progression"], default="auto",
                    help="which variable picks the chord of each bar (auto: CO2, else temperature)")
    ap.add_argument("--chord-bars", type=int, default=2, help="harmonic rhythm: bars per chord")
    ap.add_argument("--bpm", type=float, default=None, help="tempo (default: 76, or the usual tempo of --genre)")
    ap.add_argument("--beats-per-bar", type=int, default=4)
    ap.add_argument("--frames-per-bar", type=float, default=None,
                    help="data frames per bar (default: dataset hint, e.g. 7 for daily data = one bar per week; else 1)")
    ap.add_argument("--no-ending", action="store_true")
    ap.add_argument("--palette", default="orchestra",
                    help="instrument set: orchestra (default), auto (regional instruments for the zone), "
                         "or a name from --list-palettes")
    ap.add_argument("--list-palettes", action="store_true")
    ap.add_argument("--only"); ap.add_argument("--exclude")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out"); ap.add_argument("--name")
    ap.add_argument("--preview", action="store_true", help="score PNG + rough synth WAV preview")
    ap.add_argument("--render", action="store_true",
                    help="high-quality audio with the GeneralUser GS SoundFont + hall reverb (render_audio.py)")
    ap.add_argument("--mp3", action="store_true", help="with --render: write MP3 instead of WAV (needs ffmpeg)")
    ap.add_argument("--reverb", type=float, default=0.28)
    ap.add_argument("--style", choices=["data", "musical"], default="data",
                    help="data (default): every note follows the data; musical: orchestral arrangement")
    ap.add_argument("--quantize", choices=["scale", "pentatonic", "chromatic"], default="scale",
                    help="pitch grid in data style (chromatic = finest resolution)")
    ap.add_argument("--no-markers", action="store_true", help="data style: no anomaly triangle/woodblock")
    ap.add_argument("--genre", default="orchestra", help="orchestra (default), ragtime, swing, rocknroll, funk, synthpop, "
                                                          "house, trap, or realistic (recordings of rain, fire, thunder ...)")
    ap.add_argument("--fixed-tempo", action="store_true", help="the temperature does not change the tempo")
    ap.add_argument("--temperature-sound", default="heartbeat", choices=["heartbeat", "clock"],
                    help="realistic mode: the temperature as a heartbeat (default) or a ticking clock")
    ap.add_argument("--eras", action="store_true", help="fun: music through the ages, each bar in the style of its date "
                                                        "(Romantic orchestra < 1910 ... modern pop / trap >= 2010)")
    ap.add_argument("--engine", choices=["auto", "vsco", "sf2"], default="auto",
                    help="audio: auto = VSCO 2 CE samples if downloaded (python music/sampler.py --download), else SoundFont")
    a = ap.parse_args()

    if a.list_palettes:
        for n, pal in PALETTES.items():
            zones = [z for z, p in ZONE_PALETTE.items() if p == n]
            print(f"{n:13s} zones: {', '.join(zones) or '-':38s} " +
                  ", ".join(v[1] for v in pal.values()) if pal else f"{n:13s} the classical orchestra (default)")
        return
    if not a.maps:
        ap.error("map files required")
    def num_or_str(x):
        return int(x) if x is not None and x.isdigit() else x
    try:
        compose(a.maps, zone=None if (a.bbox or a.cells) else a.zone,
                bbox=[float(x) for x in a.bbox.split(",")] if a.bbox else None,
                cells=np.load(a.cells, allow_pickle=False) if a.cells else None,
                cells_grid=[int(x) for x in a.cells_grid.split(",")] if a.cells_grid else None,
                duration_s=a.duration,
                surface=a.surface, start=num_or_str(a.start), end=num_or_str(a.end), loop=a.loop,
                mapping=a.mapping, key=a.key, scale=a.scale, harmony=a.harmony, chord_bars=a.chord_bars,
                bpm=a.bpm, beats_per_bar=a.beats_per_bar, frames_per_bar=a.frames_per_bar,
                add_ending=not a.no_ending, palette=a.palette,
                only=a.only.split(",") if a.only else None, exclude=a.exclude.split(",") if a.exclude else None,
                seed=a.seed, out_dir=a.out, name=a.name, preview=a.preview, render=a.render,
                reverb=a.reverb, audio_format="mp3" if a.mp3 else "wav", verbose=True,
                style=a.style, quantize=a.quantize, markers=not a.no_markers, audio_engine=a.engine, eras=a.eras,
                genre=a.genre, tempo_from_temperature=not a.fixed_tempo, temperature_sound=a.temperature_sound)
    except ComposeError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
