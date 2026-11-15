"""Realistic mode (`compose(..., genre="realistic")`): each variable is heard as the sound of what it is.

    rain        -> a rain recording, plus a downpour layer when it pours           loudness = amount
    snowfall    -> soft wind, high and airy                                         loudness = amount
    fires       -> a campfire, plus a fireplace and a low roar for big fires        loudness / size = amount
    lightning   -> real thunderclaps (5 recordings), as many as the flashes          claps per beat = activity
    temperature -> a heartbeat, "the pulse of the planet": one beat per musical beat, and the tempo follows the
                   temperature (60 bpm, a heart at rest, when coldest -> 130 bpm when hottest; set by
                   orchestra.compose); louder and harder when hotter (synthesised) rate / loudness = value
                   (temperature_sound="clock": a real ticking clock instead, one tick per beat)
    CO2         -> city traffic                                                     loudness = level
    night lights-> the 50 Hz hum of street lamps (synthesised)                      loudness = brightness
    dust        -> desert wind;  smoke -> a smouldering fire

The recordings are free (CC0, public domain, CC BY), downloaded once from GitHub at pinned commits and prepared
here (cut, loops made seamless, levels matched): `python music/soundscape.py --download`. Credits in
soundfonts/ambience/CREDITS.md (written by the download). Nothing but numpy, soundfile and ffmpeg is needed.
"""
from __future__ import annotations

import argparse
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
AMBIENCE_DIR = HERE.parent / "soundfonts" / "ambience"
SR = 44100

BLANKET = "https://raw.githubusercontent.com/rafaelmardojai/blanket/2eaca9ba5a1b624adf90b6ccee1008efc46f0273/data/resources/sounds/"
NOIXER = "https://raw.githubusercontent.com/abetusk/noixer/24919a4b33e3604516482832d983e2b1770ef402/assets/"
OGA = "https://opengameart.org/sites/default/files/"
# downloaded file -> (url, credit, license)
SOURCES = {
    "blanket_rain.ogg": (BLANKET + "rain.ogg", "Rain by alex36917 (freesound.org/s/524605), edited by Porrumentzio for Blanket", "CC BY"),
    "blanket_wind.ogg": (BLANKET + "wind.ogg", "Wind by felix.blume (freesound.org/s/217506), edited by Porrumentzio for Blanket", "CC0"),
    "blanket_fireplace.ogg": (BLANKET + "fireplace.ogg", "Fireplace by ezwa (soundbible.com/1543)", "Public domain"),
    "blanket_city.ogg": (BLANKET + "city.ogg", "City by gezortenplotz (freesound.org/s/44796), edited by Porrumentzio for Blanket", "CC BY"),
    "noixer_rain_thunder.mp3": (NOIXER + "156049__ali-k__spring-night-rain2-evosmos-salonika-03-15-15-05.mp3",
                                "Spring night rain 2 by ali@k (freesound.org/s/156049), via noixer", "CC0"),
    "noixer_campfire.mp3": (NOIXER + "433783__cagancelik__campfire.mp3", "Campfire by cagancelik (freesound.org/s/433783), via noixer", "CC0"),
    "oga_ticks.zip": (OGA + "ticks.zip", "Ticking clock (single ticks) by bart (opengameart.org/content/ticking-clock)", "CC0"),
    "oga_ticking_clock.wav": (OGA + "ticking_clock.wav", "Ticking clock (loop) by bart (opengameart.org/content/ticking-clock)", "CC0"),
}
# prepared clip -> (source, start s, end s, low-pass Hz, kind). kind "loop": made seamless, RMS -20 dBFS;
# "hit": faded in/out, peak -3 dBFS. The thunderclaps are the 5 loudest of the CC0 night storm (25 dB over the rain).
CLIPS = {
    "rain_light": ("blanket_rain.ogg", 0, None, None, "loop"),
    "rain_heavy": ("noixer_rain_thunder.mp3", 162, 190, None, "loop"),       # a stretch with no thunder in it
    "thunder_1": ("noixer_rain_thunder.mp3", 1.8, 9.2, 3500, "hit"),
    "thunder_2": ("noixer_rain_thunder.mp3", 109.9, 117.3, 3500, "hit"),
    "thunder_3": ("noixer_rain_thunder.mp3", 445.1, 452.5, 3500, "hit"),
    "thunder_4": ("noixer_rain_thunder.mp3", 325.2, 332.6, 3500, "hit"),
    "thunder_5": ("noixer_rain_thunder.mp3", 281.8, 289.2, 3500, "hit"),
    "fire_camp": ("noixer_campfire.mp3", 0, None, None, "loop"),
    "fire_place": ("blanket_fireplace.ogg", 0, None, None, "loop"),
    "wind": ("blanket_wind.ogg", 0, None, None, "loop"),
    "city": ("blanket_city.ogg", 0, None, None, "loop"),
    # a real clock: its 4 single ticks + 7 ticks cut from its loop (one per second there), for variety
    **{f"tick_{k}": (f"oga_ticks.zip#ticking clock - tick{k}.wav", 0, None, None, "tick") for k in range(1, 5)},
    **{f"tick_{5 + k}": ("oga_ticking_clock.wav", t - 0.03, t + 0.3, None, "tick")
       for k, t in enumerate([0.153, 1.157, 2.154, 3.154, 5.168, 6.156, 7.156])},
}
# stretches cut out of a recording before it is looped (start s, end s): a car horn in the city recording
CUTS = {"city": [(14.9, 16.7)]}
AMBIENCE_VERSION = "2"     # bump when the preparation changes: installed clips of another version are prepared again
THUNDERS = [k for k in CLIPS if k.startswith("thunder")]
TICKS = [k for k in CLIPS if k.startswith("tick_")]

# what each variable sounds like (for the score / API instrument list)
SOUND_NAMES = {
    "precipitation": "Rain sounds", "snowfall": "Snowy wind", "fires": "Crackling fire", "lightning": "Thunder",
    "surface_temperature": "Heartbeat (faster when warmer)", "co2": "City traffic",
    "nightlights": "Street-lamp hum", "aerosol_dust": "Desert wind", "aerosol_smoke": "Smouldering fire",
}


# ---------------------------------------------------------------- download + preparation
def _resample(x: np.ndarray, sr: int) -> np.ndarray:
    """FFT resampling to 44.1 kHz (band-limited, per channel)."""
    if sr == SR:
        return x
    n_out = int(round(len(x) * SR / sr))
    X = np.fft.rfft(x, axis=0)
    keep = min(X.shape[0], n_out // 2 + 1)
    Y = np.zeros((n_out // 2 + 1, x.shape[1]), complex)
    Y[:keep] = X[:keep]
    return (np.fft.irfft(Y, n_out, axis=0) * (n_out / len(x))).astype(np.float32)


def _decode(path: Path) -> np.ndarray:
    """An .ogg / .mp3 file -> float32 [n, 2] at 44.1 kHz: soundfile (libsndfile reads Vorbis and MP3), else ffmpeg."""
    try:
        import soundfile as sf
        x, sr = sf.read(path, dtype="float32", always_2d=True)
        x = np.repeat(x, 2, 1) if x.shape[1] == 1 else x[:, :2]
        return _resample(x, sr)
    except Exception:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def _fft_filter(x: np.ndarray, lo=None, hi=None) -> np.ndarray:
    """Zero-phase band filter with smooth (half-octave) edges, per channel."""
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    g = np.ones_like(f)
    if lo:
        g *= np.clip((np.log2(np.maximum(f, 1)) - np.log2(lo) + 0.5), 0, 1)
    if hi:
        g *= np.clip((np.log2(hi) - np.log2(np.maximum(f, 1)) + 0.5), 0, 1)
    return np.stack([np.fft.irfft(np.fft.rfft(x[:, c]) * g, n) for c in range(x.shape[1])], 1).astype(np.float32)


def _seamless(x: np.ndarray, cf_s=1.0) -> np.ndarray:
    """Loop without a click: the last cf_s seconds are cross-faded into the start."""
    cf = min(int(cf_s * SR), len(x) // 4)
    w = np.linspace(0, 1, cf, dtype=np.float32)[:, None]
    out = x[:-cf].copy()
    out[:cf] = x[:cf] * w + x[-cf:] * (1 - w)
    return out


def ambience_available(folder: Path = AMBIENCE_DIR) -> bool:
    version = folder / "VERSION"
    return (version.exists() and version.read_text().strip() == AMBIENCE_VERSION
            and all((folder / f"{k}.flac").exists() for k in CLIPS))


def ensure_ambience(folder: Path = AMBIENCE_DIR, verbose=True) -> bool:
    """Download the recordings (about 15 MB) and prepare the clips once. Returns True when they are ready."""
    import soundfile as sf
    if ambience_available(folder):
        return True
    folder.mkdir(parents=True, exist_ok=True)
    raw_dir = folder / "_raw"
    raw_dir.mkdir(exist_ok=True)
    for name, (url, _, _) in SOURCES.items():
        if not (raw_dir / name).exists():
            if verbose:
                print(f"downloading {name} ...", flush=True)
            urllib.request.urlretrieve(url, raw_dir / name)
    decoded = {}
    for clip, (src, t0, t1, lowpass, kind) in CLIPS.items():
        if src not in decoded:
            if "#" in src:                         # a file inside a downloaded zip
                import zipfile
                archive, member = src.split("#", 1)
                target = raw_dir / f"_{member}"
                target.write_bytes(zipfile.ZipFile(raw_dir / archive).read(member))
                decoded[src] = _decode(target)
            else:
                decoded[src] = _decode(raw_dir / src)
        x = decoded[src][int(t0 * SR): None if t1 is None else int(t1 * SR)]
        for c0, c1 in sorted(CUTS.get(clip, []), reverse=True):     # remove a stretch, cross-fade the two sides
            cf = int(0.3 * SR)
            a, b = x[:int(c0 * SR)], x[int(c1 * SR):]
            w = np.linspace(0, 1, cf, dtype=np.float32)[:, None]
            x = np.concatenate([a[:-cf], a[-cf:] * (1 - w) + b[:cf] * w, b[cf:]])
        if lowpass:
            x = _fft_filter(x, hi=lowpass)
        if kind == "loop":
            x = _seamless(x)
            x *= 10 ** (-20 / 20) / (np.sqrt(np.mean(x ** 2)) + 1e-9)
        elif kind == "tick":
            x = x.copy()
            fi, fo = int(0.002 * SR), min(int(0.04 * SR), len(x) // 3)
            x[:fi] *= np.linspace(0, 1, fi)[:, None]
            x[-fo:] *= np.linspace(1, 0, fo)[:, None]
            x *= 10 ** (-3 / 20) / (np.abs(x).max() + 1e-9)
        else:
            fade_in, fade_out = int(0.12 * SR), int(1.8 * SR)
            x[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
            x[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None] ** 2
            x *= 10 ** (-3 / 20) / (np.abs(x).max() + 1e-9)
        sf.write(folder / f"{clip}.flac", np.clip(x, -1, 1), SR, subtype="PCM_16")
    lines = ["# Realistic-mode recordings", "", "Downloaded and prepared by `python music/soundscape.py --download`.", "",
             "| file | credit | license |", "|---|---|---|"]
    lines += [f"| {name} | {credit} | {lic} |" for name, (_, credit, lic) in SOURCES.items()]
    lines += ["", "CC BY sounds: credit the authors above wherever the audio is published."]
    (folder / "CREDITS.md").write_text("\n".join(lines) + "\n")
    (folder / "VERSION").write_text(AMBIENCE_VERSION + "\n")
    for f in raw_dir.iterdir():                    # keep only the prepared clips
        f.unlink()
    raw_dir.rmdir()
    return ambience_available(folder)


# ---------------------------------------------------------------- playback helpers
_CACHE: dict = {}


def clip(name: str, lo=None, hi=None) -> np.ndarray:
    key = (name, lo, hi)
    if key not in _CACHE:
        import soundfile as sf
        x, sr = sf.read(AMBIENCE_DIR / f"{name}.flac", dtype="float32", always_2d=True)
        if x.shape[1] == 1:
            x = np.repeat(x, 2, 1)
        _CACHE[key] = _fft_filter(x, lo, hi) if (lo or hi) else x
    return _CACHE[key]


def loop(x: np.ndarray, n: int, rng, rate=None) -> np.ndarray:
    """n samples of the looped clip from a random point. rate: playback speed per sample (array or None):
    faster = higher and quicker, like a tape."""
    L, start = len(x), rng.integers(0, len(x))
    if rate is None:
        return x[(start + np.arange(n)) % L]
    pos = start + np.cumsum(rate)
    i0 = np.floor(pos).astype(np.int64)
    frac = (pos - i0)[:, None].astype(np.float32)
    return x[i0 % L] * (1 - frac) + x[(i0 + 1) % L] * frac


def envelope(per_beat, beat_times, n):
    """Values per beat -> one value per sample, linear between beat centres (NaN = 0)."""
    v = np.nan_to_num(np.asarray(per_beat, dtype=float), nan=0.0)
    if len(v) == 0:
        return np.zeros(n, np.float32)
    centres = (np.asarray(beat_times[:-1]) + np.asarray(beat_times[1:])) / 2
    return np.interp(np.arange(n) / SR, centres, v).astype(np.float32)


def hum(n, rng):
    """Mains hum of street lamps: 50 Hz and its harmonics, a slight buzz and flicker."""
    t = np.arange(n) / SR
    y = sum(a * np.sin(2 * np.pi * 50 * k * t + rng.uniform(0, 6.3)) for k, a in
            enumerate([1, .55, .5, .3, .25, .14, .1, .08, .05, .04], start=1))
    y = y + 0.04 * np.sign(np.sin(2 * np.pi * 100 * t)) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.13 * t))
    y = y / np.abs(y).max() * 10 ** (-14 / 20)
    return np.stack([y, y * 0.97], 1).astype(np.float32)


def _thump(f_start, f_end, dur, drive=2.5):
    """One heart sound: a low sine gliding down (the 'lub' or the 'dub'), soft-clipped so that its harmonics
    are heard on laptop and phone speakers too, plus a muffled thud."""
    t = np.arange(int(dur * SR)) / SR
    f = f_end + (f_start - f_end) * np.exp(-t / 0.03)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * (1 - np.exp(-t / 0.004)) * np.exp(-t / (dur / 4))
    y = np.tanh(drive * y) / np.tanh(drive)
    noise = np.random.default_rng(3).standard_normal(len(t)) * np.exp(-t / 0.02)
    noise = np.fft.irfft(np.fft.rfft(noise) * (np.fft.rfftfreq(len(t), 1 / SR) < 300), len(t))
    y = y + 0.6 * noise / (np.abs(noise).max() + 1e-9)
    return (y / np.abs(y).max()).astype(np.float32)


HEART = {}


def heartbeat(beat_times, values, n):
    """A 'lub-dub' at the start of every beat. values: 0..1 per beat (NaN = no data = silent): louder and a
    little harder when hotter. The 'dub' follows the 'lub' a little sooner when the heart beats faster."""
    if not HEART:
        HEART.update(lub=_thump(95, 48, 0.22, drive=5.0), dub=_thump(120, 60, 0.15, drive=4.0))   # strong harmonics: heard on small speakers
    out = np.zeros(n, np.float32)
    for b in range(len(beat_times) - 1):
        v = values[b] if b < len(values) else np.nan
        if not np.isfinite(v):
            continue
        period = beat_times[b + 1] - beat_times[b]
        a = 0.45 + 0.55 * float(v)              # always clearly there: the pulse leads the mix
        for grain, at, g in ((HEART["lub"], 0.0, 1.0), (HEART["dub"], min(0.3, 0.36 * period), 0.65)):
            i = int((beat_times[b] + at) * SR)
            seg = grain[: max(0, n - i)]
            out[i:i + len(seg)] += seg * a * g
    out *= 10 ** (3 / 20)
    return np.stack([out, out], 1)


def clock(beat_times, values, n, rng):
    """A real ticking clock (recorded ticks): one tick per beat, a different recorded tick each time, the odd and
    even beats from two sets of ticks so it goes tick-tock; louder when hotter. values as heartbeat()."""
    grains = [clip(k) for k in TICKS]
    sets = (grains[0::2], grains[1::2])
    out = np.zeros((n, 2), np.float32)
    for b in range(len(beat_times) - 1):
        v = values[b] if b < len(values) else np.nan
        if not np.isfinite(v):
            continue
        group = sets[b % 2]
        g = group[rng.integers(len(group))]
        i = int(beat_times[b] * SR)
        seg = g[: max(0, n - i)] * (0.3 + 0.7 * float(v)) * 10 ** (-6 / 20)
        out[i:i + len(seg)] += seg
    return out


def roar(n, rng):
    """The low roar of a big fire: brown noise below 250 Hz with a flickering level."""
    x = np.cumsum(rng.standard_normal((n, 2)), 0).astype(np.float32)
    x = _fft_filter(x, lo=25, hi=250)
    flick = 1 + 0.5 * _fft_filter(rng.standard_normal((n, 1)).astype(np.float32), hi=3)[:, 0] * 20
    x *= np.clip(flick, 0.2, 2)[:, None]
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-9) * 10 ** (-20 / 20)


# ---------------------------------------------------------------- the mix
def render(layers, beat_times, total_s, seed=7, tail_s=1.0, temperature_sound="heartbeat"):
    """layers: [{var, role, norm: per-beat 0..1 value, pres: per-beat 0..1 amount, gate}], beat_times: start of each
    beat in seconds (+ the end). Returns (stereo float32, SR)."""
    rng = np.random.default_rng(seed)
    n = int((total_s + tail_s) * SR)
    mix = np.zeros((n, 2), np.float32)
    heart = np.zeros((n, 2), np.float32)          # the temperature pulse, mixed on top (see the ducking below)
    for L in layers:
        var, gate = L["var"], L.get("gate", 0.0)
        pres = np.where(np.nan_to_num(L["pres"]) >= max(gate, 1e-6), np.nan_to_num(L["pres"]), 0.0)
        p = envelope(pres, beat_times, n)
        v = envelope(L["norm"], beat_times, n)
        on = envelope(np.isfinite(np.asarray(L["norm"], dtype=float)).astype(float), beat_times, n)   # data exists
        if var == "precipitation":
            heavy = np.clip((p - 0.45) / 0.4, 0, 1) ** 1.2
            mix += loop(clip("rain_light"), n, rng) * (p ** 0.8)[:, None] * 0.32
            mix += loop(clip("rain_heavy"), n, rng) * heavy[:, None] * 0.2
        elif var == "snowfall":
            mix += loop(clip("wind", lo=500), n, rng) * (p ** 0.8)[:, None] * 5.0      # the high part of the wind: airy
        elif var in ("fires", "aerosol_smoke"):
            k = 1.0 if var == "fires" else 0.55
            mix += loop(clip("fire_camp"), n, rng) * (p ** 0.7)[:, None] * k
            mix += loop(clip("fire_place"), n, rng) * np.clip((p - 0.3) / 0.4, 0, 1)[:, None] * 0.8 * k
            if var == "fires":
                mix += roar(n, rng) * np.clip((p - 0.6) / 0.4, 0, 1)[:, None] * 0.9
        elif var == "lightning":
            for b, pr in enumerate(pres):          # a clap every few seconds, more often with more lightning
                hits = rng.poisson(0.12 + 0.45 * pr) if pr > 0 else 0
                for _ in range(hits):
                    t = beat_times[b] + rng.uniform(0, beat_times[b + 1] - beat_times[b])
                    x = clip(THUNDERS[rng.integers(len(THUNDERS))])
                    i = int(t * SR)
                    seg = x[: max(0, n - i)]
                    pan = rng.uniform(0.6, 1.0)
                    g = (0.45 + 0.55 * pr) * rng.uniform(0.6, 1.0) * 0.6
                    side = rng.integers(2)
                    mix[i:i + len(seg), side] += seg[:, side] * g
                    mix[i:i + len(seg), 1 - side] += seg[:, 1 - side] * g * pan
        elif var == "surface_temperature":
            # the heartbeat beats on every beat: its speed is the tempo, which follows the temperature
            vals = np.asarray(L["norm"], dtype=float)
            heart += clock(beat_times, vals, n, rng) if temperature_sound == "clock" else heartbeat(beat_times, vals, n)
        elif var == "co2":
            mix += loop(clip("city"), n, rng) * ((0.15 + 0.85 * v) * on)[:, None] * 0.55
        elif var == "nightlights":
            mix += hum(n, rng) * p[:, None] * 0.35
        elif var == "aerosol_dust":
            mix += loop(clip("wind", lo=250, hi=4000), n, rng) * (p ** 0.8)[:, None] * 0.9
    if np.any(heart):
        # ducking ("pumping"): on every beat the other sounds dip for a moment, so the pulse always comes through
        # and the whole soundscape breathes with it: the urgency is felt, not only heard
        env = np.abs(heart).max(axis=1)
        k = int(0.12 * SR)
        env = np.convolve(env, np.ones(k) / k, "same")
        env = env / (env.max() + 1e-9)
        mix *= (1 - 0.55 * env)[:, None]
        mix += heart
    fade = int(0.05 * SR)                          # no click where two chunks meet
    mix[:fade] *= np.linspace(0, 1, fade)[:, None]
    mix[-int(tail_s * SR):] *= np.linspace(1, 0, int(tail_s * SR))[:, None]
    return (np.tanh(mix * 1.2) / 1.2 * 0.95).astype(np.float32), SR     # gentle limiter, fixed level across chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", action="store_true", help="download and prepare the recordings (once)")
    a = ap.parse_args()
    if a.download:
        print("ready" if ensure_ambience() else "failed", "->", AMBIENCE_DIR)


if __name__ == "__main__":
    main()
