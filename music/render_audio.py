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
import subprocess
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
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
        lic.rename(SF_DIR / "GeneralUser-GS_LICENSE.txt")
    subprocess.run(["rm", "-rf", str(tmp)])
    return DEFAULT_SF2


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


def render(midi_path, sf2: Path, sr=44100, reverb=0.28, gain_db=-6.0, tail_s=3.0):
    import tinysoundfont as tsf

    synth = tsf.Synth(gain=gain_db, samplerate=sr)
    sfid = synth.sfload(str(sf2))
    events = sorted(tsf.midi.load(str(midi_path)), key=lambda e: e.t)
    # every channel starts on piano so notes before any program change still sound
    for ch in range(16):
        synth.program_select(ch, sfid, 128 if ch == 9 else 0, 0, is_drums=(ch == 9))

    end_t = (events[-1].t if events else 0) + tail_s
    out = np.zeros((int(end_t * sr) + sr, 2), np.float32)
    pos = 0
    for ev in events:
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

    dry = out.astype(np.float64)
    if reverb > 0:
        ir = hall_ir(sr)
        wet = np.stack([fft_convolve(dry[:, c], ir[:, c])[: len(dry)] for c in range(2)], axis=1)
        mixed = (1 - reverb) * dry + reverb * wet * 1.4
    else:
        mixed = dry
    # trim trailing silence, normalise to -1 dBFS
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
    ap.add_argument("midi", nargs="+")
    ap.add_argument("--sf2", default=str(DEFAULT_SF2))
    ap.add_argument("--reverb", type=float, default=0.28, help="0 = dry, 0.5 = very wet")
    ap.add_argument("--sr", type=int, default=44100)
    ap.add_argument("--out", help="output WAV (single input only)")
    a = ap.parse_args()
    sf2 = ensure_soundfont(Path(a.sf2))
    for m in a.midi:
        x, sr = render(m, sf2, a.sr, a.reverb)
        out = Path(a.out) if a.out and len(a.midi) == 1 else Path(m).with_suffix(".wav")
        save_wav(x, sr, out)
        print(f"{m} -> {out}  ({len(x) / sr:.1f} s)")


if __name__ == "__main__":
    main()
