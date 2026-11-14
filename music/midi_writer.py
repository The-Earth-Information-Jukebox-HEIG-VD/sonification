"""
Minimal dependency-free Standard MIDI File writer (format 1).

    from midi_writer import MidiFile
    m = MidiFile(bpm=90)
    t = m.add_track("Temperature", program=0, channel=0)   # GM 0 = Acoustic Grand Piano
    t.note(pitch=60, start_beat=0, dur_beats=1, velocity=90)
    m.save("out.mid")
"""
from __future__ import annotations

import struct

TPQ = 480  # ticks per quarter note


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


class Track:
    def __init__(self, name: str, program: int = 0, channel: int = 0):
        self.name, self.program, self.channel = name, program, channel
        self.events: list[tuple[int, int, bytes]] = []  # (tick, order, data)

    def note(self, pitch: int, start_beat: float, dur_beats: float, velocity: int = 80):
        pitch = max(0, min(127, int(pitch)))
        velocity = max(1, min(127, int(velocity)))
        on = int(round(start_beat * TPQ))
        off = max(on + 1, int(round((start_beat + dur_beats) * TPQ)))
        ch = self.channel & 0x0F
        self.events.append((on, 1, bytes([0x90 | ch, pitch, velocity])))
        self.events.append((off, 0, bytes([0x80 | ch, pitch, 0])))  # offs before ons at same tick

    def cc(self, controller: int, value: int, beat: float = 0.0):
        """Control change, e.g. cc(10, 32) pan left-ish, cc(7, 100) volume."""
        ch = self.channel & 0x0F
        self.events.append((int(round(beat * TPQ)), 0,
                            bytes([0xB0 | ch, controller & 0x7F, max(0, min(127, int(value)))])))

    def marker(self, text: str, beat: float):
        b = text.encode("utf-8")
        self.events.append((int(round(beat * TPQ)), 2, b"\xff\x06" + _vlq(len(b)) + b))

    def to_bytes(self) -> bytes:
        name = self.name.encode("utf-8")
        data = bytearray()
        data += b"\x00\xff\x03" + _vlq(len(name)) + name
        data += b"\x00" + bytes([0xC0 | (self.channel & 0x0F), self.program & 0x7F])
        last = 0
        for tick, _, ev in sorted(self.events, key=lambda e: (e[0], e[1])):
            data += _vlq(tick - last) + ev
            last = tick
        data += b"\x00\xff\x2f\x00"
        return b"MTrk" + struct.pack(">I", len(data)) + bytes(data)


class MidiFile:
    def __init__(self, bpm: float = 90, time_sig=(4, 4)):
        self.bpm, self.time_sig = bpm, time_sig
        self.tracks: list[Track] = []
        self.tempo_changes: list[tuple[float, float]] = []   # (beat, bpm) after the initial tempo

    def add_track(self, name: str, program: int = 0, channel: int = 0) -> Track:
        t = Track(name, program, channel)
        self.tracks.append(t)
        return t

    def tempo(self, beat: float, bpm: float):
        """Tempo change at `beat` (e.g. a ritardando = several decreasing changes)."""
        self.tempo_changes.append((beat, bpm))

    def save(self, path):
        tempo = int(60_000_000 / self.bpm)
        num, den = self.time_sig
        cond = bytearray(b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big"))
        cond += b"\x00\xff\x58\x04" + bytes([num, den.bit_length() - 1, 24, 8])
        last = 0
        for beat, bpm in sorted(self.tempo_changes):
            tick = int(round(beat * TPQ))
            cond += _vlq(tick - last) + b"\xff\x51\x03" + int(60_000_000 / bpm).to_bytes(3, "big")
            last = tick
        cond += b"\x00\xff\x2f\x00"
        chunks = [b"MTrk" + struct.pack(">I", len(cond)) + bytes(cond)]
        chunks += [t.to_bytes() for t in self.tracks]
        header = b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), TPQ)
        with open(path, "wb") as f:
            f.write(header + b"".join(chunks))
