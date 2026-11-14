"""
Minimal SFZ sampler (numpy + soundfile) for the VSCO 2 Community Edition orchestral samples.

Supports the opcodes VSCO 2 CE uses: default_path, sample, lokey/hikey, pitch_keycenter,
lovel/hivel, volume, ampeg_attack, ampeg_release (with <control>/<global>/<group>/<region>
inheritance). Notes are pitch-shifted from the nearest sample, enveloped, and, if a note
is longer than its recording, looped with a crossfade.

Samples: VSCO 2 Community Edition by Versilian Studios, CC0 (public domain):
         https://github.com/sgossner/VSCO-2-CE  (branch SFZ). Downloaded by ensure_vsco().
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
VSCO_DIR = ROOT / "soundfonts" / "vsco"
VSCO_REPO = "https://github.com/sgossner/VSCO-2-CE.git"

# GM program -> VSCO instrument (sfz file). Short notes on violins use the real tremolo samples.
GM_TO_VSCO = {
    0: "UprightPiano.sfz",          # temperature chords
    48: "ViolinEnsSusVib.sfz",      # rain melody (violin section)
    43: "ContrabassSusVB.sfz",      # CO2
    73: "FluteSusVib.sfz",          # dust melody
    60: "FHornSus.sfz",             # smoke melody
    13: "Xylophone.sfz",            # lightning
    9: "Glockenspiel.sfz",          # snow
}
SHORT_NOTE_ALT = {48: ("ViolinEnsTrem.sfz", 0.35)}   # notes shorter than 0.35 s -> tremolo samples
TUNE_CENTS = {"Xylophone.sfz": -15, "Glockenspiel.sfz": -15}   # measured: these recordings are ~15 cents sharp
SUSTAINED = {"ViolinEnsSusVib.sfz", "ViolinEnsTrem.sfz", "ContrabassSusVB.sfz", "FluteSusVib.sfz", "FHornSus.sfz"}


# ----------------------------------------------------------------------------
# Download (sparse: only the samples our instruments reference, ~440 MB)
# ----------------------------------------------------------------------------
def _needed_sfz():
    return set(GM_TO_VSCO.values()) | {SHORT_NOTE_ALT[48][0]}


def vsco_available() -> bool:
    """True when every instrument definition AND every sample it references is on disk."""
    try:
        for f in _needed_sfz():
            if not (VSCO_DIR / f).exists():
                return False
            if not all(r["path"].exists() for r in instrument(f).regions):
                return False
        return True
    except Exception:
        return False


def ensure_vsco(verbose=True) -> bool:
    """Sparse-clone VSCO-2-CE (SFZ branch) with only the needed samples. Returns True if available."""
    if vsco_available():
        return True
    try:
        VSCO_DIR.parent.mkdir(parents=True, exist_ok=True)
        if not (VSCO_DIR / ".git").exists():
            if verbose:
                print("downloading VSCO 2 CE instrument list ...")
            subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", "--depth", "1",
                            "-b", "SFZ", VSCO_REPO, str(VSCO_DIR)], check=True)
        subprocess.run(["git", "-C", str(VSCO_DIR), "sparse-checkout", "set", "--no-cone", "/*.sfz"], check=True)
        subprocess.run(["git", "-C", str(VSCO_DIR), "checkout", "-q"], check=True)
        files = _needed_sfz()
        instrument.cache_clear()
        paths = ["/*.sfz"]
        for f in files:
            inst = SfzInstrument(VSCO_DIR / f, load=False)
            paths += ["/" + str(r["path"].relative_to(VSCO_DIR)).replace(" ", "\\ ") for r in inst.regions]
        if verbose:
            print(f"downloading {len(paths) - 1} VSCO samples (~440 MB, once) ...")
        subprocess.run(["git", "-C", str(VSCO_DIR), "sparse-checkout", "set", "--no-cone", "--stdin"],
                       input="\n".join(paths), text=True, check=True)
        subprocess.run(["git", "-C", str(VSCO_DIR), "checkout", "-q"], check=True)
        instrument.cache_clear()
        return vsco_available()
    except Exception as e:  # network / git missing -> caller falls back to the SoundFont
        if verbose:
            print(f"VSCO download failed ({e}); using the SoundFont instead")
        return False


# ----------------------------------------------------------------------------
# SFZ parsing
# ----------------------------------------------------------------------------
_TOKEN = re.compile(r"<(\w+)>|(\w+)=([^\n<]*?)(?=\s+\w+=|\s*<|\s*$|\s*//)", re.M)


class SfzInstrument:
    def __init__(self, sfz_path: Path, load=True):
        self.path = Path(sfz_path)
        self.regions = self._parse(self.path.read_text(errors="ignore"))
        self.name = self.path.stem

    def _parse(self, text):
        text = re.sub(r"//.*", "", text)
        control, glob, group, region, regions, level = {}, {}, {}, None, [], None
        for m in _TOKEN.finditer(text):
            if m.group(1):
                level = m.group(1)
                if region is not None:
                    regions.append(region)
                region = {} if level == "region" else None
                if level == "group":
                    group = {}
                continue
            k, v = m.group(2), m.group(3).strip()
            target = {"control": control, "global": glob, "group": group}.get(level, region)
            if target is not None:
                target[k] = v
        if region is not None:
            regions.append(region)
        base = self.path.parent / control.get("default_path", "").replace("\\", "/")
        out = []
        for r in regions:
            o = {**glob, **group, **r}
            if "sample" not in o:
                continue
            key = int(o.get("key", -1))
            out.append({
                "path": base / o["sample"].replace("\\", "/"),
                "lokey": int(o.get("lokey", key if key >= 0 else 0)),
                "hikey": int(o.get("hikey", key if key >= 0 else 127)),
                "center": int(o.get("pitch_keycenter", key if key >= 0 else 60)),
                "lovel": int(o.get("lovel", 0)), "hivel": int(o.get("hivel", 127)),
                "volume": float(o.get("volume", 0)),
                "attack": float(o.get("ampeg_attack", 0.001)),
                "release": float(o.get("ampeg_release", 0.5)),
            })
        return out

    def region_for(self, key, vel):
        hits = [r for r in self.regions if r["lokey"] <= key <= r["hikey"] and r["lovel"] <= vel <= r["hivel"]]
        if not hits:
            hits = [r for r in self.regions if r["lokey"] <= key <= r["hikey"]] or \
                   sorted(self.regions, key=lambda r: min(abs(key - r["lokey"]), abs(key - r["hikey"])))[:1]
        return hits[0]


@lru_cache(maxsize=512)
def _load(path: str, sr: int):
    import soundfile as sf
    x, file_sr = sf.read(path, dtype="float32", always_2d=True)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    if file_sr != sr:                                     # simple resample to the output rate
        n = int(len(x) * sr / file_sr)
        t = np.linspace(0, len(x) - 1, n)
        x = np.stack([np.interp(t, np.arange(len(x)), x[:, c]) for c in range(2)], axis=1).astype(np.float32)
    return x


@lru_cache(maxsize=32)
def instrument(sfz_name: str) -> SfzInstrument:
    return SfzInstrument(VSCO_DIR / sfz_name)


def _extend(x, needed, sr, xfade_s=0.25):
    """Loop the steady middle of a sustained recording (crossfaded) until it is `needed` samples long."""
    if len(x) >= needed:
        return x
    a, b = int(len(x) * 0.35), int(len(x) * 0.85)
    loop = x[a:b]
    xf = min(int(xfade_s * sr), len(loop) // 3)
    fade = np.linspace(0, 1, xf, dtype=np.float32)[:, None]
    out = x[:b].copy()
    while len(out) < needed:
        head = out[-xf:] * (1 - fade) + loop[:xf] * fade
        out = np.concatenate([out[:-xf], head, loop[xf:]])
    return out


def render_note(sfz_name, key, vel, dur_s, sr=44100):
    inst = instrument(sfz_name)
    r = inst.region_for(key, vel)
    x = _load(str(r["path"]), sr)
    ratio = 2 ** ((key - r["center"]) / 12 + TUNE_CENTS.get(sfz_name, 0) / 1200)
    sustained = sfz_name in SUSTAINED
    release = min(r["release"], 1.2) if sustained else min(r["release"], 2.5)
    out_len = int((dur_s + release) * sr) if sustained else min(int((dur_s + release) * sr), int(len(x) / ratio))
    if sustained:
        x = _extend(x, int(out_len * ratio) + 2, sr)
    pos = np.arange(out_len) * ratio
    pos = pos[pos < len(x) - 1]
    i0 = pos.astype(int)
    frac = (pos - i0)[:, None].astype(np.float32)
    y = x[i0] * (1 - frac) + x[i0 + 1] * frac                 # linear-interpolated pitch shift
    t = np.arange(len(y)) / sr
    env = np.minimum(1.0, t / max(r["attack"], 0.003))
    tail = t > dur_s
    env[tail] *= np.exp(-(t[tail] - dur_s) / max(release / 4, 0.02))   # release
    gain = (vel / 127) ** 1.6 * 10 ** (r["volume"] / 20)
    return (y * (env * gain)[:, None]).astype(np.float32)


if __name__ == "__main__":
    import sys
    if "--download" in sys.argv:
        ok = ensure_vsco()
        print("VSCO 2 CE samples ready" if ok else "VSCO download failed")
    else:
        print(f"VSCO available: {vsco_available()}  (python music/sampler.py --download)")
