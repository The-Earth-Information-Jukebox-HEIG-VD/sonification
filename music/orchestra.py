#!/usr/bin/env python3
"""
Earth Orchestra: several intensity maps (one per variable) -> one coherent multi-track score.

Arranged "like a conductor":

  REGISTER = ROLE. The more continuous and important a variable, the higher and more melodic;
  the slower and more cumulative, the lower and more foundational; punctual events = rhythm.

    voice (high -> low)   variable             instrument              role / rhythmic value
    -------------------   ------------------   ---------------------   ------------------------------------
    melody   G4..G6       surface_temperature  Violins                 one legato line, quarters/halves, tied
    rhythm   C4..A5       precipitation        Pizzicato strings       arpeggio: half -> quarter -> 8th notes
    harmony  C3..G4       aerosol_dust         Piano                   chord, whole -> half -> quarter notes
    swell    F2..C4       aerosol_smoke        Trombones               held fifths + crescendo, sus4 when thick
    timpani  E2..G3       lightning            Timpani (tuned)         beats 1 -> 1&3 -> all beats -> roll
    bass     E1..D3       co2                  Contrabass              one root per bar (= the harmony)

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
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from midi_writer import MidiFile  # noqa: E402
from zone_to_midi import (ZONES, SCALES, load_map, note_name, parse_key,  # noqa: E402
                          scale_notes, zone_mask, zone_series)

SEVEN_NOTE_SCALES = [k for k, v in SCALES.items() if len(v) == 7]

# ----------------------------------------------------------------------------
# The arrangement (score order: high -> low). This table IS the orchestration.
#   low/high : register (MIDI notes)        gain  : absolute intensity x gain = presence
#   gate     : presence needed to enter     levels: presence thresholds for denser rhythm
#   level    : mix level (scales velocity)  pan   : 0 left .. 127 right (orchestra seating)
# ----------------------------------------------------------------------------
ORCHESTRA = {
    "surface_temperature": dict(name="Temperature - Violins", role="melody", program=49, channel=0,
                                low=67, high=91, gain=1.0, gate=0.0, levels=[], level=0.95, pan=40),
    "precipitation":       dict(name="Rain - Pizzicato Strings", role="rhythm", program=45, channel=1,
                                low=60, high=81, gain=2.5, gate=0.10, levels=[0.35, 0.65], level=0.70, pan=88),
    "aerosol_dust":        dict(name="Dust - Piano", role="harmony", program=0, channel=2,
                                low=48, high=67, gain=4.0, gate=0.12, levels=[0.45, 0.75], level=0.70, pan=60),
    "aerosol_smoke":       dict(name="Smoke - Trombones", role="swell", program=57, channel=3,
                                low=41, high=60, gain=4.0, gate=0.12, levels=[0.60], level=0.75, pan=96),
    "lightning":           dict(name="Lightning - Timpani", role="timpani", program=47, channel=4,
                                low=40, high=55, gain=3.0, gate=0.08, levels=[0.35, 0.60, 0.85], level=0.85, pan=70),
    "co2":                 dict(name="CO2 - Contrabass", role="bass", program=43, channel=5,
                                low=28, high=50, gain=1.0, gate=0.0, levels=[], level=0.90, pan=78),
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


HARMONY_PRIORITY = ["co2", "surface_temperature"]   # who chooses the chord of each bar
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
    """Value of a per-frame series at every beat (time measured in the reference's frames)."""
    n = len(series)
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


def rel_norm(v):
    f = v[np.isfinite(v)]
    if not len(f):
        return np.full_like(v, np.nan)
    lo, hi = f.min(), f.max()
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


def chord_on(root, sl):
    i = sl.index(root)
    triad = [sl[i], sl[i + 2], sl[i + 4]]
    q = QUALITY.get((triad[1] - triad[0], triad[2] - triad[0]), "")
    return {"root": root, "root_pc": root % 12, "triad": triad, "pcs": sorted({m % 12 for m in triad}),
            "name": note_name(root)[:-1].rstrip("-0123456789") + q}


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
    scale tones. Repeated pitches are tied; every 4th bar ends with a short breath."""
    lo, hi, bpb = cfg["low"], cfg["high"], c["bpb"]
    scale_in = [m for m in c["sl"] if lo <= m <= hi]
    notes, prev = [], None
    for b, h in enumerate(c["harmony"]):
        if h is None:
            prev = None; continue
        bar = []
        for k in range(bpb):
            i = b * bpb + k
            nv = c["norm"][i]
            if not np.isfinite(nv):
                continue
            strong = k % 2 == 0
            cand = tones(h["pcs"], lo, hi) if strong else scale_in
            p = nearest(lo + nv * (hi - lo), cand, prev, smooth=0.35)
            vel = cfg["level"] * (62 + 40 * nv + (8 if k == 0 else 0))
            if bar and bar[-1][0] == p and bar[-1][1] + bar[-1][2] == i:
                bar[-1][2] += 1                       # tie repeated note
            else:
                bar.append([p, i, 1, vel])
            prev = p
        if bar and (b + 1) % 4 == 0:                  # phrase breath every 4 bars
            bar[-1][2] = max(0.5, bar[-1][2] - 0.35)
        notes += [(p, s, d * 0.97, v) for p, s, d, v in bar]
    return notes, []


def role_bass(cfg, c):
    """Contrabass: the chord root, one whole note per bar; doubled an octave down when high."""
    lo, hi, bpb = cfg["low"], cfg["high"], c["bpb"]
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
    for b, h in enumerate(c["harmony"]):
        lvl = c["levels"][b]
        if h is None or lvl == 0:
            continue
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


ROLES = {"melody": role_melody, "bass": role_bass, "harmony": role_harmony,
         "rhythm": role_rhythm, "swell": role_swell, "timpani": role_timpani}


def ending(parts, n_bars, bpb, key_pc, scale):
    """Final cadence: two bars on the tonic chord for melody, harmony and bass."""
    sl = scale_notes(key_pc, scale)
    tonic = chord_on(next(m for m in sl if m >= 36 and m % 12 == key_pc), sl)
    t0 = n_bars * bpb
    for p in parts:
        cfg, role = p["cfg"], p["cfg"]["role"]
        last = [n[0] for n in p["notes"][-4:]] or [(cfg["low"] + cfg["high"]) // 2]
        if role == "melody":
            q = nearest(last[-1], [m for m in range(cfg["low"], cfg["high"] + 1) if m % 12 == key_pc])
            p["notes"].append((q, t0, 2 * bpb, cfg["level"] * 80))
        elif role == "bass":
            q = nearest(last[-1], [m for m in range(cfg["low"], cfg["high"] + 1) if m % 12 == key_pc])
            p["notes"].append((q, t0, 2 * bpb, cfg["level"] * 85))
        elif role == "harmony":
            for q in tones(tonic["pcs"], cfg["low"], cfg["high"])[:4]:
                p["notes"].append((q, t0, 2 * bpb, cfg["level"] * 60))
    return t0 + 2 * bpb


# ----------------------------------------------------------------------------
# Preview: score lanes + stereo synth
# ----------------------------------------------------------------------------
COLORS = {"melody": "#d1495b", "rhythm": "#00798c", "harmony": "#8f6593",
          "swell": "#edae49", "timpani": "#3d3d3d", "bass": "#30638e"}


def score_png(parts, harmony, total_beats, bpb, path, title):
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
        ax2.plot(np.arange(len(p["pres"])) + 0.5, p["pres"], color=col, lw=1, alpha=0.5)
        ax2.set_ylim(0, 1.05); ax2.set_yticks([])
        ax.set_yticks([])
        ax.set_ylabel(p["cfg"]["name"].replace(" - ", "\n"), rotation=0, ha="right", va="center", fontsize=8)
        if li == 0:
            for b, h in enumerate(harmony):
                if h is not None:
                    ax.text(b * bpb + 0.1, max(ps) + 1.2, h["name"], fontsize=6, color="#555", va="bottom")
    axes[0, 0].set_title(title)
    axes[-1, 0].set_xlabel("beats  (thin lines = bar lines; curve = presence of the phenomenon)")
    axes[-1, 0].set_xlim(0, total_beats)
    fig.tight_layout(); fig.savefig(path, dpi=100); plt.close(fig)


def synth(role, pitch, dur, vel, sr):
    a = vel / 127
    f = 440 * 2 ** ((pitch - 69) / 12)
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


def _time_window(times, start, end):
    """Indices of frames within [start, end]; start/end are ISO dates (prefix ok) or frame indices."""
    n = len(times) if times is not None else None
    if start is None and end is None:
        return None
    if isinstance(start, int) or isinstance(end, int) or times is None:
        lo = int(start or 0); hi = int(end if end is not None else (n or 10**9) - 1)
        return slice(lo, hi + 1)
    ts = [str(t) for t in times]
    idx = [i for i, t in enumerate(ts) if (start is None or t >= str(start)) and (end is None or t[:len(str(end))] <= str(end))]
    if not idx:
        raise ComposeError(f"no frames between {start} and {end}")
    return slice(idx[0], idx[-1] + 1)


def compose(maps, zone="global", bbox=None, surface="all", start=None, end=None, loop=False,
            mapping="hybrid", key="C", scale=None, harmony="auto", chord_bars=2, bpm=76.0,
            beats_per_bar=4, frames_per_bar=1.0, add_ending=True, palette="orchestra",
            only=None, exclude=None, seed=7, out_dir=None, name=None,
            preview=False, render=False, reverb=0.28, audio_format="wav", verbose=False):
    """Data maps -> score. Writes MIDI (+ stems, JSON, optional PNG/audio) to out_dir and returns
    a dict with the score document, file paths and stats."""
    log = print if verbose else (lambda *a, **k: None)
    pal_name = resolve_palette(palette, zone, bbox)
    orch = apply_palette(pal_name)
    scale = scale or PALETTE_SCALE.get(pal_name, "major")
    if scale not in SEVEN_NOTE_SCALES:
        raise ComposeError(f"scale must be one of {SEVEN_NOTE_SCALES}")
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
    series = {}
    files = collect_maps(maps if isinstance(maps, (list, tuple)) else [maps])
    if not files:
        raise ComposeError("no map files found")
    for f in files:
        z, meta = load_map(str(f))
        var = meta.get("variable")
        if var not in orch or (only and var not in only) or var in excl:
            continue
        land = z["land_fraction"] if "land_fraction" in z.files else None
        try:
            mask = zone_mask(z["lat"], z["lon"], bbox, land, surface)
        except ValueError as e:
            raise ComposeError(str(e))
        times = z["time"] if "time" in z.files else None
        m, _ = zone_series(z["data"].astype(np.float32), z["lat"], mask)
        win = _time_window(times, start, end)
        if win is not None:
            m = m[win]; times = times[win] if times is not None else None
        if len(m) < 1 or not np.isfinite(m).any():
            continue
        series[var] = dict(mean=m, meta=meta, times=times)
    if not series:
        raise ComposeError("no usable data for this zone / selection")

    ref_var = next((v for v in HARMONY_PRIORITY if v in series), next(iter(series)))
    n_ref = len(series[ref_var]["mean"])
    bpb, fpb = int(beats_per_bar), float(frames_per_bar)
    n_bars = max(1, int(np.ceil(n_ref / fpb)))
    n_beats = n_bars * bpb
    sl = scale_notes(key_pc, scale)
    rng = np.random.default_rng(seed)

    for var, d in series.items():
        cfg = orch[var]
        vals = sample_beats(d["mean"], n_ref, n_beats, fpb, bpb, loop, cfg["role"] in SMOOTH_ROLES)
        vmin, vmax = d["meta"].get("vmin", np.nanmin(vals)), d["meta"].get("vmax", np.nanmax(vals))
        absn = np.clip((vals - vmin) / ((vmax - vmin) or 1), 0, 1)
        reln = rel_norm(vals)
        d["values"] = vals
        d["norm"] = absn if mapping == "absolute" else reln
        pres = np.clip((reln if mapping == "relative" else absn) * cfg["gain"], 0, 1)
        if cfg["role"] in ("melody", "bass"):
            pres = np.where(np.isfinite(vals), np.maximum(pres, 0.35), np.nan)
        d["pres"] = pres
        d["bar_norm"], d["bar_pres"] = per_bar(d["norm"], bpb), per_bar(pres, bpb)
        d["levels"] = bar_levels(d["bar_pres"], cfg["gate"], cfg["levels"])

    hsrc = None if harmony == "progression" else (
        harmony if harmony != "auto" else next((v for v in HARMONY_PRIORITY if v in series), None))
    if hsrc and hsrc in series:
        chords = harmony_from(series[hsrc]["bar_norm"], key_pc, scale, chord_bars)
    else:
        hsrc = None
        chords = harmony_progression(n_bars, key_pc, scale)

    parts = []
    for var, cfg in orch.items():
        if var not in series:
            continue
        d = series[var]
        ctx = dict(harmony=chords, norm=d["norm"], pres=d["pres"], bar_norm=d["bar_norm"],
                   bar_pres=d["bar_pres"], levels=d["levels"], bpb=bpb, sl=sl, rng=rng)
        notes, ccs = ROLES[cfg["role"]](cfg, ctx)
        notes = [(int(p), float(s_), float(du), int(np.clip(v, 1, 127))) for p, s_, du, v in notes]
        parts.append(dict(var=var, cfg=cfg, notes=notes, ccs=ccs, pres=np.nan_to_num(d["pres"])))
    total_beats = ending(parts, n_bars, bpb, key_pc, scale) if add_ending else n_beats

    # ---- files
    out = Path(out_dir) if out_dir else Path(__file__).resolve().parent / "output"
    out.mkdir(parents=True, exist_ok=True)
    name = name or (f"orchestra_{zone_label}_{surface}" + ("" if pal_name == "orchestra" else f"_{pal_name}"))
    stems_dir = out / f"{name}_stems"
    stems_dir.mkdir(exist_ok=True)
    paths = {"midi": out / f"{name}.mid", "json": out / f"{name}.json", "stems": {}}
    score = MidiFile(bpm=bpm, time_sig=(bpb, 4))
    for p in parts:
        cfg = p["cfg"]
        stem = MidiFile(bpm=bpm, time_sig=(bpb, 4))
        for mf in (score, stem):
            t = mf.add_track(cfg["name"], cfg["program"], cfg["channel"])
            t.cc(7, 100); t.cc(10, cfg["pan"]); t.cc(11, 127 if not p["ccs"] else 0)
            for cnum, v, b in p["ccs"]:
                t.cc(cnum, v, b)
            for pitch, st, du, vel in p["notes"]:
                t.note(pitch, st, du, vel)
        stem.save(stems_dir / f"{p['var']}.mid")
        paths["stems"][p["var"]] = stems_dir / f"{p['var']}.mid"
    marker_track = score.add_track("Chords (markers)", 0, 15)
    for b, h in enumerate(chords):
        if h is not None:
            marker_track.marker(h["name"], b * bpb)
    score.save(paths["midi"])

    times = series[ref_var]["times"]
    def bar_time(b):
        return None if times is None else str(times[min(int(b * fpb), len(times) - 1)])
    grid = np.zeros(int(total_beats * 4) + 8)
    for p in parts:
        for pitch, st, du, vel in p["notes"]:
            grid[int(st * 4): int((st + du) * 4)] += 1
    doc = {
        "zone": zone_label, "bbox": list(bbox), "surface": surface, "palette": pal_name,
        "key": key, "scale": scale, "bpm": bpm, "beats_per_bar": bpb, "n_bars": n_bars,
        "total_beats": total_beats, "duration_s": round(total_beats * 60 / bpm, 2),
        "time_start": bar_time(0), "time_end": bar_time(n_bars - 1),
        "harmony_source": hsrc or "progression",
        "bars": [{"index": b, "start_beat": b * bpb, "time": bar_time(b),
                  "chord": None if h is None else h["name"]} for b, h in enumerate(chords)],
        "instruments": {p["var"]: {
            "name": p["cfg"]["name"], "role": p["cfg"]["role"], "program": p["cfg"]["program"],
            "channel": p["cfg"]["channel"], "pan": p["cfg"]["pan"], "register": [p["cfg"]["low"], p["cfg"]["high"]],
            "units": series[p["var"]]["meta"].get("units"),
            "values_per_beat": [None if not np.isfinite(x) else round(float(x), 4) for x in series[p["var"]]["values"]],
            "presence_per_beat": [round(float(x), 3) for x in p["pres"]],
            "levels_per_bar": [int(x) for x in series[p["var"]]["levels"]],
            "bars_playing": int(sum(l > 0 for l in series[p["var"]]["levels"])),
            "notes": [{"pitch": n[0], "start": n[1], "duration": n[2], "velocity": n[3]} for n in p["notes"]],
        } for p in parts},
        "stats": {"notes_total": sum(len(p["notes"]) for p in parts),
                  "polyphony_mean": round(float(grid[grid > 0].mean()), 2) if (grid > 0).any() else 0,
                  "polyphony_max": int(grid.max())},
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
    log(f"wrote {paths['midi']}  (+ stems/, .json)")

    if preview:
        paths["score_png"] = out / f"{name}_score.png"
        paths["preview_wav"] = out / f"{name}_preview.wav"
        score_png(parts, chords, total_beats, bpb, paths["score_png"],
                  f"Earth Orchestra - {zone_label} ({surface}) - {pal_name} palette - {key} {scale}")
        render_wav(parts, paths["preview_wav"], bpm, total_beats)
        log(f"wrote {name}_score.png and {name}_preview.wav")
    if render:
        import render_audio
        sf2 = render_audio.ensure_soundfont(render_audio.DEFAULT_SF2)
        x, sr = render_audio.render(paths["midi"], sf2, reverb=reverb)
        wav = out / f"{name}.wav"
        render_audio.save_wav(x, sr, wav)
        paths["audio"] = wav
        if audio_format == "mp3":
            mp3 = render_audio.to_mp3(wav)
            if mp3:
                wav.unlink()
                paths["audio"] = mp3
        log(f"wrote {paths['audio'].name}  (GeneralUser GS, {len(x) / sr:.0f} s)")
    return {"name": name, "doc": doc, "paths": paths}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("maps", nargs="*", help=".npz files, a folder, or a prefix like output/sim_both_24f_1deg_")
    ap.add_argument("--zone", default="global")
    ap.add_argument("--bbox", help="lat_min,lat_max,lon_min,lon_max")
    ap.add_argument("--surface", choices=["all", "land", "ocean"], default="all")
    ap.add_argument("--start", help="first frame: ISO date (e.g. 2024-06) or frame index")
    ap.add_argument("--end", help="last frame: ISO date or frame index")
    ap.add_argument("--loop", action="store_true", help="cyclic data (e.g. 12 months)")
    ap.add_argument("--mapping", choices=["hybrid", "relative", "absolute"], default="hybrid",
                    help="hybrid: melodic contour from zone-relative values, activity from absolute amounts")
    ap.add_argument("--key", default="C")
    ap.add_argument("--scale", choices=SEVEN_NOTE_SCALES, default=None,
                    help="default: major (or the palette's mode, e.g. dorian for celtic)")
    ap.add_argument("--harmony", choices=["auto", "co2", "surface_temperature", "progression"], default="auto",
                    help="which variable picks the chord of each bar (auto: CO2, else temperature)")
    ap.add_argument("--chord-bars", type=int, default=2, help="harmonic rhythm: bars per chord")
    ap.add_argument("--bpm", type=float, default=76)
    ap.add_argument("--beats-per-bar", type=int, default=4)
    ap.add_argument("--frames-per-bar", type=float, default=1.0, help="1 = one frame per bar; 0.5 = two bars per frame")
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
        compose(a.maps, zone=None if a.bbox else a.zone,
                bbox=[float(x) for x in a.bbox.split(",")] if a.bbox else None,
                surface=a.surface, start=num_or_str(a.start), end=num_or_str(a.end), loop=a.loop,
                mapping=a.mapping, key=a.key, scale=a.scale, harmony=a.harmony, chord_bars=a.chord_bars,
                bpm=a.bpm, beats_per_bar=a.beats_per_bar, frames_per_bar=a.frames_per_bar,
                add_ending=not a.no_ending, palette=a.palette,
                only=a.only.split(",") if a.only else None, exclude=a.exclude.split(",") if a.exclude else None,
                seed=a.seed, out_dir=a.out, name=a.name, preview=a.preview, render=a.render,
                reverb=a.reverb, audio_format="mp3" if a.mp3 else "wav", verbose=True)
    except ComposeError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
