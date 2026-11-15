#!/usr/bin/env python3
"""
Render any of our MIDI files (orchestra score or stems) to a good-sounding WAV with a real
sampled SoundFont instead of the rough built-in preview synth.

  SoundFont : GeneralUser GS (S. Christian Collins), free for music creation, private or
              commercial (see soundfonts/README.md). Downloaded automatically on first use.
  Synth     : tinysoundfont (pip install tinysoundfont --no-deps)
  Room      : synthetic concert-hall reverb (an orchestra sounds fake without a room)

Usage
  python music/render_audio.py music/output/orchestra_india_all.mid
  python music/render_audio.py music/output/orchestra_india_all.mid --reverb 0.35 --out india.wav
  python music/render_audio.py SCORE.mid --sf2 path/to/Other.sf2        # try another SoundFont
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
import sys as _sys
_sys.path.insert(0, str(HERE))
SF_DIR = HERE.parent / "soundfonts"
DEFAULT_SF2 = SF_DIR / "GeneralUser-GS.sf2"
GUGS_REPO = "https://github.com/mrbumpy409/GeneralUser-GS.git"


def ensure_soundfont(path: Path) -> Path:
    if path.exists():
        return path
    if path != DEFAULT_SF2:
        raise SystemExit(f"SoundFont not found: {path}")
    print("Downloading GeneralUser GS (~32 MB) ...")
    tmp = SF_DIR / "_gugs"
    SF_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--depth", "1", GUGS_REPO, str(tmp)], check=True)
    (tmp / "GeneralUser-GS.sf2").rename(DEFAULT_SF2)
    lic = tmp / "documentation" / "LICENSE.txt"
    if lic.exists():
        lic.replace(SF_DIR / "GeneralUser-GS_LICENSE.txt")      # replace: rename fails on Windows if it exists
    _rmtree(tmp)                                                 # no "rm" on Windows
    return DEFAULT_SF2


def _rmtree(folder: Path):
    """Delete a folder, including the read-only files git leaves on Windows."""
    def force(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    kw = {"onexc": force} if _sys.version_info >= (3, 12) else {"onerror": force}
    shutil.rmtree(folder, **kw)


def hall_ir(sr: int, seconds=2.4, predelay=0.02, seed=1):
    """Stereo synthetic hall impulse response: early reflections + decaying diffuse tail,
    darker over time (high frequencies die first, like a real hall)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    ir = np.zeros((n, 2))
    for ch in range(2):
        noise = rng.standard_normal(n)
        # progressive low-pass: blend noise with increasingly smoothed versions
        smooth1 = np.convolve(noise, np.ones(4) / 4, "same")
        smooth2 = np.convolve(noise, np.ones(16) / 16, "same")
        mix = np.where(t < 0.3, noise, np.where(t < 1.0, smooth1, smooth2))
        tail = mix * np.exp(-t * 6.9 / seconds)          # -60 dB at `seconds`
        tail[: int(predelay * sr)] = 0
        for d, g in ((0.011, 0.5), (0.017, 0.4), (0.023 + 0.004 * ch, 0.35), (0.031, 0.3)):
            tail[int((predelay + d) * sr)] += g        # early reflections
        ir[:, ch] = tail
    return ir / np.sqrt((ir ** 2).sum(axis=0, keepdims=True))


def fft_convolve(x, h):
    n = len(x) + len(h) - 1
    nfft = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(h, nfft), nfft)[:n]


@lru_cache(maxsize=2)           # one entry is ~34 MB (2^20 complex x 2 channels); a 30 s chunk uses one
def _hall_spectrum(sr: int, nfft: int) -> np.ndarray:
    """rfft of hall_ir(sr) padded to nfft, both channels (read-only). Every chunk has about the same length,
    so the hall and its spectrum are computed once instead of once per render."""
    spec = np.fft.rfft(hall_ir(sr), nfft, axis=0)
    spec.flags.writeable = False
    return spec


def hall_reverb(dry: np.ndarray, sr: int) -> np.ndarray:
    """dry (n, 2) convolved with the stereo hall, cut to n samples (the same as fft_convolve per channel)."""
    n = len(dry) + int(2.4 * sr) - 1                      # len(hall_ir(sr)) = int(2.4 * sr)
    nfft = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(dry, nfft, axis=0) * _hall_spectrum(sr, nfft), nfft, axis=0)[: len(dry)]


def render(midi_path, sf2: Path, sr=44100, reverb=0.28, gain_db=-6.0, tail_s=3.0, engine="auto"):
    """engine: 'auto' (VSCO samples where available + SoundFont for the rest), 'vsco', or 'sf2'."""
    import tinysoundfont as tsf

    events = sorted(tsf.midi.load(str(midi_path)), key=lambda e: e.t)
    end_t = (events[-1].t if events else 0) + tail_s
    n_out = int(end_t * sr) + sr

    # which channels go to the VSCO sampler?
    vsco_ch, programs = {}, {}
    use_vsco = False
    if engine in ("auto", "vsco"):
        import sampler
        use_vsco = sampler.vsco_available() or (engine == "vsco" and sampler.ensure_vsco())
    changes = {}
    for ev in events:
        if type(ev.action).__name__ == "ProgramChange":
            programs.setdefault(ev.channel, ev.action.program)
            changes.setdefault(ev.channel, set()).add(ev.action.program)
    if use_vsco:      # a channel that switches instrument (eras mode) stays on the SoundFont
        vsco_ch = {ch: p for ch, p in programs.items()
                   if ch != 9 and p in sampler.GM_TO_VSCO and len(changes.get(ch, ())) == 1}

    # ---- SoundFont part (every channel not handled by VSCO)
    synth = tsf.Synth(gain=gain_db, samplerate=sr)
    sfid = synth.sfload(str(sf2))
    for ch in range(16):
        synth.program_select(ch, sfid, 128 if ch == 9 else 0, 0, is_drums=(ch == 9))
    out = np.zeros((n_out, 2), np.float32)
    pos = 0
    for ev in events:
        if ev.channel in vsco_ch:
            continue
        target = int(ev.t * sr)
        if target > pos:
            out[pos:target] = np.frombuffer(synth.generate(target - pos), dtype=np.float32).reshape(-1, 2)
            pos = target
        a = ev.action
        kind = type(a).__name__
        if kind == "NoteOn":
            synth.noteon(ev.channel, a.key, a.velocity) if a.velocity > 0 else synth.noteoff(ev.channel, a.key)
        elif kind == "NoteOff":
            synth.noteoff(ev.channel, a.key)
        elif kind == "ProgramChange":
            synth.program_select(ev.channel, sfid, 128 if ev.channel == 9 else 0, a.program,
                                 is_drums=(ev.channel == 9))
        elif kind == "ControlChange":
            synth.control_change(ev.channel, a.control, a.control_value)
        elif kind == "PitchBend":
            synth.pitchbend(ev.channel, a.pitch_bend)
    if pos < len(out):
        out[pos:] = np.frombuffer(synth.generate(len(out) - pos), dtype=np.float32).reshape(-1, 2)

    # ---- VSCO part: real orchestral recordings, each part loudness-matched to its SoundFont rendering
    def sf2_channel_rms(ch):
        syn = tsf.Synth(gain=gain_db, samplerate=sr)
        sid = syn.sfload(str(sf2))
        syn.program_select(ch, sid, 0, 0)
        buf_, p_ = np.zeros((n_out, 2), np.float32), 0
        for ev in events:
            if ev.channel != ch:
                continue
            tg = int(ev.t * sr)
            if tg > p_:
                buf_[p_:tg] = np.frombuffer(syn.generate(tg - p_), dtype=np.float32).reshape(-1, 2); p_ = tg
            a_, k_ = ev.action, type(ev.action).__name__
            if k_ == "NoteOn":
                syn.noteon(ch, a_.key, a_.velocity) if a_.velocity > 0 else syn.noteoff(ch, a_.key)
            elif k_ == "NoteOff":
                syn.noteoff(ch, a_.key)
            elif k_ == "ProgramChange":
                syn.program_select(ch, sid, 0, a_.program)
            elif k_ == "ControlChange":
                syn.control_change(ch, a_.control, a_.control_value)
        if p_ < n_out:
            buf_[p_:] = np.frombuffer(syn.generate(n_out - p_), dtype=np.float32).reshape(-1, 2)
        return float(np.sqrt((buf_ ** 2).mean()))

    for ch, prog in vsco_ch.items():
        sfz = sampler.GM_TO_VSCO[prog]
        alt = sampler.SHORT_NOTE_ALT.get(prog)
        gain = 1.0
        cc = {7: [(0.0, 100)], 10: [(0.0, 64)], 11: [(0.0, 127)]}
        on, notes = {}, []
        for ev in events:
            if ev.channel != ch:
                continue
            a, kind = ev.action, type(ev.action).__name__
            if kind == "ControlChange" and a.control in cc:
                cc[a.control].append((ev.t, a.control_value))
            elif kind == "NoteOn" and a.velocity > 0:
                on.setdefault(a.key, []).append((ev.t, a.velocity))
            elif kind in ("NoteOff", "NoteOn"):
                if on.get(a.key):
                    t0, v = on[a.key].pop(0)
                    notes.append((t0, ev.t - t0, a.key, v))
        buf = np.zeros((n_out, 2), np.float32)
        for t0, dur, key, vel in notes:
            name = alt[0] if (alt and dur < alt[1]) else sfz
            y = sampler.render_note(name, key, vel, dur, sr) * gain
            i0 = int(t0 * sr)
            i1 = min(n_out, i0 + len(y))
            buf[i0:i1] += y[: i1 - i0]
        tt = np.arange(n_out) / sr
        for ctl in (7, 11):                                   # volume and expression curves
            pts = sorted(cc[ctl])
            curve = np.interp(tt, [p[0] for p in pts], [p[1] / 127 for p in pts])
            buf *= (curve ** (2 if ctl == 7 else 1.5))[:, None]
        pan = cc[10][-1][1] / 127 if len(cc[10]) > 1 else 0.5
        buf[:, 0] *= min(1.0, 2 * (1 - pan)); buf[:, 1] *= min(1.0, 2 * pan)
        ref, mine = sf2_channel_rms(ch), float(np.sqrt((buf ** 2).mean()))
        if ref > 1e-7 and mine > 1e-7:
            buf *= ref / mine                                 # same loudness as the designed GM mix
        out += buf

    dry = out.astype(np.float64)
    if reverb > 0:
        wet = hall_reverb(dry, sr)
        mixed = (1 - reverb) * dry + reverb * wet * 1.4
    else:
        mixed = dry
    nz = np.where(np.abs(mixed).max(axis=1) > 1e-4)[0]
    mixed = mixed[: (nz[-1] + 1 if len(nz) else len(mixed))]
    mixed *= 0.89 / (np.abs(mixed).max() + 1e-9)
    return mixed, sr


def to_mp3(wav_path, bitrate="192k"):
    """Encode WAV -> MP3 with ffmpeg (fast preset). Returns the MP3 path, or None if ffmpeg is missing."""
    import shutil
    if not shutil.which("ffmpeg"):
        return None
    mp3 = Path(wav_path).with_suffix(".mp3")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame",
                    "-b:a", bitrate, "-compression_level", "9", str(mp3)], check=True)
    return mp3


def save_wav(x, sr, path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("midi", nargs="*")
    ap.add_argument("--download", action="store_true",
                    help="only fetch the SoundFont (~32 MB) and exit; do it once before starting the server, "
                         "otherwise the first audio request has to wait for it")
    ap.add_argument("--sf2", default=str(DEFAULT_SF2))
    ap.add_argument("--reverb", type=float, default=0.28, help="0 = dry, 0.5 = very wet")
    ap.add_argument("--sr", type=int, default=44100)
    ap.add_argument("--out", help="output WAV (single input only)")
    ap.add_argument("--engine", choices=["auto", "vsco", "sf2"], default="auto",
                    help="auto: VSCO 2 CE orchestral samples where available, SoundFont for the rest")
    a = ap.parse_args()
    if not a.midi and not a.download:
        ap.error("give MIDI files to render, or --download")
    sf2 = ensure_soundfont(Path(a.sf2))
    if a.download:
        print(f"SoundFont ready: {sf2}")
    for m in a.midi:
        x, sr = render(m, sf2, a.sr, a.reverb, engine=a.engine)
        out = Path(a.out) if a.out and len(a.midi) == 1 else Path(m).with_suffix(".wav")
        save_wav(x, sr, out)
        print(f"{m} -> {out}  ({len(x) / sr:.1f} s)")


if __name__ == "__main__":
    main()
