"""Musical genres, and music through the ages.

Two ways to use the same table of styles:
  * genre (`compose(..., genre="rocknroll")`): ONE style for the whole piece, chosen by the listener. The
    instrument set stays the same from the first bar to the last, so the instruments are easy to recognise.
  * eras (fun mode, `compose(..., eras=True)`): the date of each bar picks the style of its time.

The date of each bar picks the musical style of its time: a Romantic orchestra before 1910, then ragtime, swing,
rock'n'roll, 70s funk & disco, 80s synth-pop, house, and modern pop / trap after 2010. Listening along a long
timeline (1880 -> 2026) you hear music history go by while the data keeps playing.

What stays the data (as in the default data style):
  * pitch = value: every chord root, bass note and melody note is the one the data style chose, on the beat it
    chose it. A style only changes HOW the note is played: the instrument, the rhythm that repeats it (stride
    piano, walking bass, funk clavinet, house off-beats...), and swing.
  * loudness = amount: the velocities come from the data notes.
What is added for the fun:
  * a drum kit for each era (brushes, jazz ride, rock backbeat, disco, drum machine, 808), louder and busier when
    the temperature (or, without it, the instruments on average) is high: hot years hit harder.
  * a cymbal crash and a short fill where one era hands over to the next.
  * walking bass and stride piano add passing notes between the data notes (beats 2-4 of a bar, the left hand).

The Romantic orchestra keeps the instruments of the normal orchestra (VSCO samples when installed). The other
eras play on separate MIDI channels with program changes at each era boundary (General MIDI sounds of the
GeneralUser GS SoundFont, drum kits included).
"""
from __future__ import annotations

import numpy as np

# ----------------------------------------------------------------------------
# The eras. `start` = first year. Programs: General MIDI (0-based), drum kits: GS kits on channel 10.
#   harmony : (style, program) for the always-on chords (temperature)
#   bass    : (style, program) for the bass (CO2)
#   melody / swell / timpani : program for that role; per-variable overrides in `vars`
#   swing   : where the off-beat eighth falls (0.5 straight, 0.667 triplet swing)
#   gain    : velocity factor of the era (GM sounds differ a lot in loudness: a brush kit and a honky-tonk piano
#             are about 12 dB under a house kit and synths; measured on the rendered audio, a slight rise is kept)
# A program None keeps the orchestra's instrument.
# ----------------------------------------------------------------------------
ERAS = [
    dict(key="orchestra", name="Orchestra", era_name="Romantic orchestra", start=-9999, kit=None, swing=0.5, bpm=76,
         harmony=("keep", None), bass=("keep", None), melody=None, swell=None, timpani=None, vars={}),
    dict(key="ragtime", gain=1.5, name="Ragtime & early jazz", start=1910, bpm=96, kit=40, swing=0.5, drums="ragtime",
         harmony=("stride", 3), bass=("two_beat", 58), melody=71, swell=57, timpani=47,
         vars={"snowfall": 8, "aerosol_dust": 72, "aerosol_smoke": 56}),
    dict(key="swing", gain=1.12, name="Swing & big band", start=1930, bpm=126, kit=32, swing=0.667, drums="swing",
         harmony=("comp4", 26), bass=("walking", 32), melody=56, swell=61, timpani=47,
         vars={"snowfall": 11, "aerosol_dust": 65, "aerosol_smoke": 59}),
    dict(key="rocknroll", gain=1.0, name="Rock'n'roll", start=1950, bpm=150, kit=0, swing=0.62, drums="rocknroll",
         harmony=("eighths", 27), bass=("eighths", 33), melody=66, swell=61, timpani=117,
         vars={"snowfall": 11, "aerosol_dust": 22, "aerosol_smoke": 67}),
    dict(key="funk", gain=0.92, name="Funk & disco", era_name="Rock, funk & disco", start=1965, bpm=112, kit=16, swing=0.5, drums="disco",
         harmony=("funk", 7), bass=("octaves", 33), melody=29, swell=16, timpani=117,
         vars={"snowfall": 11, "aerosol_dust": 73, "aerosol_smoke": 61}),
    dict(key="synthpop", gain=0.88, name="Synth-pop", start=1980, bpm=116, kit=24, swing=0.5, drums="synthpop",
         harmony=("pad", 90), bass=("eighths", 38), melody=80, swell=62, timpani=118,
         vars={"snowfall": 98, "aerosol_dust": 83, "aerosol_smoke": 62}),
    dict(key="house", gain=0.76, name="House & electronic", start=1995, bpm=124, kit=26, swing=0.5, drums="house",
         harmony=("offbeat", 1), bass=("offbeat", 39), melody=81, swell=90, timpani=118,
         vars={"snowfall": 108, "aerosol_dust": 80, "aerosol_smoke": 63}),
    dict(key="trap", gain=0.88, name="Modern pop & trap", start=2010, bpm=140, kit=25, swing=0.5, drums="trap",
         harmony=("arp", 4), bass=("808", 38), melody=87, swell=95, timpani=118,
         vars={"snowfall": 10, "aerosol_dust": 75, "aerosol_smoke": 63}),
]
KEEP_VARS = {"nightlights"}        # already an electronic pad: the same sound in every era
ERA_BY_KEY = {e["key"]: e for e in ERAS}
GENRES = ERA_BY_KEY                # genre id -> style; "orchestra" = the normal orchestra (no change)
GENRE_ALIASES = {"classical": "orchestra", "classic": "orchestra", "jazz": "swing", "bigband": "swing",
                 "rock": "rocknroll", "rock'n'roll": "rocknroll", "rock_n_roll": "rocknroll", "disco": "funk",
                 "seventies": "funk", "70s": "funk", "80s": "synthpop", "synth": "synthpop", "electro": "house",
                 "electronic": "house", "edm": "house", "pop": "trap", "hiphop": "trap", "modern": "trap"}


def genre_key(name):
    """'Rock'n'roll', 'jazz', 'house' ... -> the genre id, or None if unknown."""
    k = str(name or "orchestra").strip().lower().replace(" ", "").replace("-", "")
    k = GENRE_ALIASES.get(k, k)
    return k if k in GENRES else None


# General MIDI names of the programs used above (for the instrument list of a genre)
GM_NAMES = {0: "Grand piano", 1: "Bright piano", 3: "Honky-tonk piano", 4: "Electric piano", 7: "Clavinet",
            8: "Celesta", 10: "Music box", 11: "Vibraphone", 16: "Hammond organ", 22: "Harmonica", 26: "Jazz guitar",
            27: "Clean electric guitar", 29: "Overdriven guitar", 32: "Upright bass", 33: "Electric bass",
            38: "Synth bass", 39: "Synth bass", 47: "Timpani", 56: "Trumpet", 57: "Trombone", 58: "Tuba",
            59: "Muted trumpet", 61: "Brass section", 62: "Synth brass", 63: "Synth brass", 65: "Alto sax",
            66: "Tenor sax", 67: "Baritone sax", 71: "Clarinet", 72: "Piccolo", 73: "Flute", 75: "Pan flute",
            80: "Square lead synth", 81: "Saw lead synth", 83: "Chiff lead synth", 87: "Bass & lead synth",
            89: "Synth pad", 90: "Polysynth pad", 95: "Sweep pad", 98: "Crystal synth", 108: "Kalimba",
            117: "Melodic toms", 118: "Synth drum"}
STYLE_NAMES = {"stride": "stride", "comp4": "comping", "eighths": "eighth notes", "funk": "funky stabs",
               "pad": "pad", "offbeat": "off-beats", "arp": "arpeggios", "two_beat": "oom-pah",
               "walking": "walking", "octaves": "octaves", "808": "808"}


def instrument_name(genre, var, role, orig_name, orig_program):
    """Display name of the instrument of `var` in `genre`, e.g. 'Clean electric guitar (eighth notes)'."""
    g = GENRES[genre]
    prog = program_for(g, var, role, orig_program)
    if prog is None:
        return orig_name
    style = g["harmony"][0] if role == "harmony" else (g["bass"][0] if role == "bass" else None)
    return GM_NAMES.get(prog, f"GM {prog}") + (f" ({STYLE_NAMES[style]})" if style in STYLE_NAMES else "")
DRUMS_CFG = dict(name="Era drums", role="drums", program=0, channel=9, low=35, high=81, gain=1.0, gate=0.0,
                 levels=[], level=0.8, pan=64)

# GM drum notes
KICK, SIDE, SNARE, CLAP, ESNARE, CHH, PHH, OHH, CRASH, RIDE = 36, 37, 38, 39, 40, 42, 44, 46, 49, 51


def era_of_year(year):
    era = ERAS[0]
    for e in ERAS:
        if year is not None and year >= e["start"]:
            era = e
    return era


def program_for(era, var, role, orig):
    """GM program of `var` (role `role`, orchestra program `orig`) in `era`; None = keep the orchestra's."""
    if era["key"] == "orchestra" or var in KEEP_VARS:
        return None
    if var in era["vars"]:
        return era["vars"][var]
    if role == "harmony":
        return era["harmony"][1]
    if role == "bass":
        return era["bass"][1]
    if role in ("melody", "rhythm"):
        return era["melody"]
    if role == "swell":
        return era["swell"]
    if role == "timpani":
        return era["timpani"]
    return orig


def _swing(t, ratio):
    """Move an off-beat eighth (x.5) to x.ratio; everything else stays."""
    f = t - np.floor(t)
    return np.floor(t) + ratio if abs(f - 0.5) < 1e-6 else t


# ---------------------------------------------------------------- harmony (always-on chords)
def _chords_by_beat(notes):
    by = {}
    for p, s, d, v in notes:
        by.setdefault(round(s, 4), []).append((p, d, v))
    return {s: (sorted(x[0] for x in ch), max(x[2] for x in ch), max(x[1] for x in ch)) for s, ch in by.items()}


def _scale_step(p, steps, sl):
    if p in sl:
        i = sl.index(p) + steps
        if 0 <= i < len(sl):
            return sl[i]
    return p + {2: 4, 4: 7, 6: 10, -1: -1}.get(steps, 0)


def harmony_notes(style, notes, bpb, sl, swing):
    """Re-play the data chords (root = value, one per beat) in an era's comping style."""
    out = []
    for s, (ch, v, d) in sorted(_chords_by_beat(notes).items()):
        root, k = ch[0], int(round(s)) % bpb
        if style == "stride":                     # left hand low on 1 and 3, chord on 2 and 4
            if k % 2 == 0:
                out += [(root - 12, s, 0.45, v * 1.05), (root - 24 if root - 24 >= 28 else root - 12, s, 0.45, v * 0.8)]
            else:
                out += [(q, s, 0.4, v * 0.9) for q in ch]
        elif style == "comp4":                    # Freddie Green guitar: short chord on every beat, 7th added
            seventh = _scale_step(root, 6, sl)
            acc = 1.0 if k % 2 else 0.85
            out += [(q, s, 0.35, v * acc) for q in ch + [seventh]]
        elif style == "eighths":                  # rock'n'roll rhythm guitar: chord on each eighth
            for off, a in ((0.0, 1.0), (0.5, 0.8)):
                out += [(q, _swing(s + off, swing), 0.3, v * a) for q in ch]
        elif style == "funk":                     # clavinet: syncopated stabs, the same pattern every bar
            for off in {0: (0.0,), 1: (0.5,), 2: (0.0, 0.75), 3: (0.5,)}.get(k, (0.0,)):
                out += [(q, s + off, 0.22, v * (1.0 if off == 0 else 0.9)) for q in ch]
        elif style == "pad":                      # synth pad: held, legato, re-struck when the chord changes
            out += [(q, s, 1.0, v * 0.85) for q in ch]
        elif style == "offbeat":                  # house piano stabs on the off-beats
            out += [(q, s + 0.5, 0.3, v) for q in ch]
        elif style == "arp":                      # modern pluck/keys: eighth-note arpeggio of the chord
            tones = ch + [root + 12]
            for h in (0, 1):
                out.append((tones[(2 * k + h) % len(tones)], s + 0.5 * h, 0.45, v * (1.0 if h == 0 else 0.85)))
        else:
            out += [(q, s, d, v) for q in ch]
    return _merge_pad(out) if style == "pad" else out


def _merge_pad(notes):
    """Same pitch on consecutive beats -> one held note."""
    notes = sorted(notes, key=lambda n: (n[0], n[1]))
    out = []
    for p, s, d, v in notes:
        if out and out[-1][0] == p and abs(out[-1][1] + out[-1][2] - s) < 1e-6:
            out[-1] = (p, out[-1][1], out[-1][2] + d, out[-1][3])
        else:
            out.append((p, s, d, v))
    return [(p, s, d * 0.98, v) for p, s, d, v in out]


# ---------------------------------------------------------------- bass
def _pitch_per_beat(notes, beats):
    """The data note sounding at each beat (None = silent)."""
    at = [None] * len(beats)
    for p, s, d, v in notes:
        for j, b in enumerate(beats):
            if s - 1e-6 <= b < s + max(d / 0.95, 1.0) - 1e-6 and at[j] is None:
                at[j] = (p, v)
    return at


def bass_notes(style, notes, beats, bpb, sl, swing):
    """beats: the integer beats of this era (in order). Returns the bass re-played in the era's style."""
    if style == "keep":
        return list(notes)
    at = _pitch_per_beat(notes, beats)
    out = []
    for j, b in enumerate(beats):
        if at[j] is None:
            continue
        p, v = at[j]
        k = b % bpb
        if style == "two_beat":                       # tuba: oom on 1 and 3
            if k % 2 == 0:
                out.append((p, b, 0.9, v))
        elif style == "walking":                      # data note on 1, then up the chord, then lead to the next bar
            nxt = next((at[i][0] for i in range(j + 1, len(beats)) if at[i] is not None and beats[i] % bpb == 0), p)
            q = {0: p, 1: _scale_step(p, 2, sl), 2: _scale_step(p, 4, sl)}.get(k, nxt + (1 if nxt <= p else -1))
            out.append((q, b, 0.9, v * (1.0 if k == 0 else 0.85)))
        elif style == "eighths":
            out += [(p, b, 0.42, v), (p, _swing(b + 0.5, swing), 0.42, v * 0.85)]
        elif style == "octaves":                      # disco octave bass
            out += [(p, b, 0.42, v), (p + 12 if p + 12 <= 72 else p, b + 0.5, 0.42, v * 0.9)]
        elif style == "offbeat":                      # house: bass on the off-beat
            out.append((p, b + 0.5, 0.42, v))
        elif style == "808":                          # one long 808 per bar, again when the value changes
            if k == 0 or (at[j - 1] is not None and at[j - 1][0] != p) or at[j - 1] is None:
                end = next((beats[i] for i in range(j + 1, len(beats))
                            if beats[i] % bpb == 0 or at[i] is None or at[i][0] != p), b + 1)
                out.append((p, b, (end - b) * 0.95, v * 1.05))
        else:
            out.append((p, b, 0.9, v))
    return out


# ---------------------------------------------------------------- drums
def drum_pattern(kind, energy):
    """[(note, offset in the bar (beats), velocity factor)] for one 4-beat bar."""
    e = float(np.clip(energy, 0, 1))
    if kind == "ragtime":
        return [(KICK, 0, .55), (KICK, 2, .5), (SNARE, 1, .5), (SNARE, 3, .55)]
    if kind == "swing":
        return [(RIDE, 0, .6), (RIDE, 1, .7), (RIDE, 1.5, .45), (RIDE, 2, .6), (RIDE, 3, .7), (RIDE, 3.5, .45),
                (PHH, 1, .55), (PHH, 3, .55)] + [(KICK, b, .3) for b in range(4)]
    if kind == "rocknroll":
        return ([(KICK, 0, .8), (KICK, 2, .8), (KICK, 2.5, .5), (SNARE, 1, .85), (SNARE, 3, .85)]
                + [(CHH, x / 2, .5 if x % 2 else .6) for x in range(8)])
    if kind == "disco":
        return ([(KICK, b, .85) for b in range(4)] + [(SNARE, 1, .8), (SNARE, 3, .8)]
                + [(OHH, b + .5, .6) for b in range(4)]
                + ([(CHH, b + q, .35) for b in range(4) for q in (.25, .75)] if e > 0.4 else []))
    if kind == "synthpop":
        return ([(KICK, 0, .9), (KICK, 2, .85), (KICK, 2.5, .6), (ESNARE, 1, 1.0), (ESNARE, 3, 1.0), (CLAP, 3, .7)]
                + [(CHH, x / 2, .5) for x in range(8)])
    if kind == "house":
        return ([(KICK, b, .95) for b in range(4)] + [(CLAP, 1, .8), (CLAP, 3, .8)]
                + [(OHH, b + .5, .65) for b in range(4)] + [(CHH, b + q, .35) for b in range(4) for q in (.25, .75)])
    if kind == "trap":                    # half-time snare on 3, hi-hat rolls when it is hot
        hats = [(CHH, x / 2, .55) for x in range(6)]
        hats += [(CHH, 3 + i / 6, .45 + .05 * i) for i in range(6)] if e > 0.55 else [(CHH, 3, .55), (CHH, 3.5, .5)]
        if e > 0.75:
            hats += [(CHH, x / 2 + .25, .35) for x in range(6)]
        return [(KICK, 0, .95), (KICK, 2.5, .8), (KICK, 3.75, .6), (CLAP, 2, .95), (SNARE, 2, .7)] + hats
    return []


def apply(parts, bar_keys, bpb, sl, n_bars, used_channels, era_names=False):
    """Turn the data parts into the music of each bar's style (in place): bar_keys[b] = the genre id of bar b
    (the same id everywhere for a genre; era_of_year(...)["key"] per bar for the eras mode). Adds the drum part.
    Returns (segments [{key, name, start_bar, bars}], bar_keys)."""
    if bpb != 4:
        raise ValueError("genres need 4 beats per bar")
    bar_era = [GENRES[k] for k in bar_keys]
    segments = []
    for b, e in enumerate(bar_era):
        if not segments or segments[-1]["key"] != e["key"]:
            segments.append({"key": e["key"], "name": e.get("era_name", e["name"]) if era_names else e["name"],
                             "start_bar": b, "bars": 0})
        segments[-1]["bars"] += 1

    # a free channel for each part that changes sound (the orchestra keeps its own for the Romantic bars)
    free = [c for c in list(range(10, 15)) + list(range(0, 9)) if c not in used_channels]
    for p in parts:
        cfg, var, role = p["cfg"], p["var"], p["cfg"]["role"]
        orch_notes, era_notes, progs = [], [], []
        for seg in segments:
            era = ERA_BY_KEY[seg["key"]]
            b0, b1 = seg["start_bar"] * bpb, (seg["start_bar"] + seg["bars"]) * bpb
            seg_notes = [n for n in p["notes"] if b0 - 1e-6 <= n[1] < b1 - 1e-6]
            prog = program_for(era, var, role, cfg["program"])
            if prog is None:
                orch_notes += seg_notes
                continue
            if role == "harmony":
                new = harmony_notes(era["harmony"][0], seg_notes, bpb, sl, era["swing"])
            elif role == "bass":
                new = bass_notes(era["bass"][0], seg_notes, list(range(b0, b1)), bpb, sl, era["swing"])
            else:
                new = [(q, _swing(s, era["swing"]), d, v) for q, s, d, v in seg_notes]
            g = era.get("gain", 1.0)
            era_notes += [(q, s_, d, v * g) for q, s_, d, v in new if s_ < b1 - 1e-6]
            progs.append((b0, prog))
        p["notes"] = orch_notes + era_notes
        if not progs:
            continue
        ch = free.pop(0) if free else cfg["channel"]
        tracks = []
        if orch_notes or ch == cfg["channel"]:
            tracks.append(dict(name=cfg["name"], channel=cfg["channel"], programs=[(0, cfg["program"])], notes=orch_notes))
        if ch == cfg["channel"]:          # no channel left: the same channel switches sound at the boundaries
            tracks[0]["programs"] += [(b, pr) for b, pr in progs]
            tracks[0]["notes"] = orch_notes + era_notes
        else:
            tracks.append(dict(name=cfg["name"].split(" - ")[0] + " - eras", channel=ch, programs=progs, notes=era_notes))
        p["era_tracks"] = tracks

    # ---- drums: one kit per era, energy = how hot (or how loud the data is) in that bar
    harm = next((p for p in parts if p["cfg"]["role"] == "harmony"), None)
    src = [harm] if harm is not None else parts
    energy = []
    for b in range(n_bars):
        vals = [float(np.mean(p["pres"][b * bpb:(b + 1) * bpb])) for p in src if len(p["pres"]) >= (b + 1) * bpb]
        energy.append(float(np.clip((np.mean(vals) - 0.35) / 0.4, 0, 1)) if vals else 0.5)
    dnotes, kits = [], []
    for b in range(n_bars):
        era = bar_era[b]
        if era.get("kit") is None:
            continue
        if not kits or kits[-1][1] != era["kit"]:
            kits.append((b * bpb, era["kit"]))
        scale = (0.55 + 0.45 * energy[b]) * era.get("gain", 1.0)
        for note, off, f in drum_pattern(era["drums"], energy[b]):
            dnotes.append((note, b * bpb + _swing(off, era["swing"]), 0.25, 105 * f * scale))
        if b > 0 and bar_era[b - 1]["key"] != era["key"]:
            dnotes.append((CRASH, b * bpb, 1.5, 110))
        if b + 1 < n_bars and bar_era[b + 1]["key"] != era["key"] and bar_era[b + 1].get("kit") is not None:
            dnotes = [n for n in dnotes if not (n[1] >= b * bpb + 3 and n[0] in (SNARE, CLAP, ESNARE, CHH, OHH, RIDE))]
            dnotes += [(SNARE, b * bpb + 3 + i / 4, 0.2, 60 + 12 * i) for i in range(4)]       # a fill into the next era
    if dnotes:
        pres = np.repeat(np.asarray(energy), bpb)
        parts.append(dict(var="era_drums", cfg=dict(DRUMS_CFG, program=kits[0][1]), notes=dnotes, ccs=[], pres=pres,
                          era_tracks=[dict(name="Era drums", channel=9, programs=kits, notes=dnotes)]))
    return segments, [e["key"] for e in bar_era]
