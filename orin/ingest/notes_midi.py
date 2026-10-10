"""MIDI -> IR, read with mido (spec 7.14).

One melody line is kept: the track and channel with the most notes (drums on channel 10
are passed over), and the highest note wherever notes sound together. Pitch bend becomes
cents (a range of 2 semitones unless the file sets another through RPN 0), velocity is
kept, and seconds follow the file's tempo map. Time is counted in sixteenths (4 pulses
per quarter note).
"""
from __future__ import annotations

import bisect
import io
from dataclasses import dataclass
from typing import Optional

from ..ir.models import IR, Cycle, IRWarning, Mode
from .notes import NotesRequest, RawNote, build_ir, choose_tonic, fail, meter_cycle, monophonic

SUBDIVISION = 4  # pulses per quarter note
DEFAULT_TEMPO_US = 500_000  # MIDI's own default: 120 quarter notes per minute
DEFAULT_BEND_RANGE = 2.0  # semitones
DRUM_CHANNEL = 9
_BEND_INSIDE_CENTS = 10.0


@dataclass
class _Open:
    onset: int  # ticks
    note: int
    velocity: int
    bend_cents: float
    bent_at: Optional[int] = None  # tick of the first bend that moved the pitch after the note began


class _TempoMap:
    def __init__(self, changes: list[tuple[int, int]], ticks_per_beat: int):
        merged: dict[int, int] = {0: DEFAULT_TEMPO_US}
        for tick, tempo in sorted(changes):
            merged[tick] = tempo
        self.ticks = sorted(merged)
        self.tempos = [merged[t] for t in self.ticks]
        self.ticks_per_beat = ticks_per_beat
        self.seconds = [0.0]
        for i in range(1, len(self.ticks)):
            span = self.ticks[i] - self.ticks[i - 1]
            self.seconds.append(self.seconds[-1] + span * self.tempos[i - 1] / 1e6 / ticks_per_beat)

    def tempo_at(self, tick: float) -> int:
        return self.tempos[max(0, bisect.bisect_right(self.ticks, tick) - 1)]

    def seconds_at(self, tick: float) -> float:
        i = max(0, bisect.bisect_right(self.ticks, tick) - 1)
        return self.seconds[i] + (tick - self.ticks[i]) * self.tempos[i] / 1e6 / self.ticks_per_beat


def _merge_bend_channels(done: dict[tuple[int, int], list[RawNote]], tolerance: int) -> None:
    """Join channels of one track that never sound together into a single line.

    A melody with microtones needs a pitch bend per note, so files such as Orin's own MIDI
    output alternate two channels note by note. Read channel by channel, such a melody
    would lose every other note.
    """
    by_track: dict[int, list[int]] = {}
    for track, channel in done:
        if channel != DRUM_CHANNEL:
            by_track.setdefault(track, []).append(channel)
    for track, channels in by_track.items():
        if len(channels) < 2:
            continue
        merged = sorted((n for ch in channels for n in done[(track, ch)]), key=lambda n: (n.onset, n.midi))
        if any(b.onset < a.onset + a.duration - tolerance for a, b in zip(merged, merged[1:])):
            continue  # they do sound together: separate lines
        for ch in channels:
            del done[(track, ch)]
        done[(track, min(channels))] = merged


def parse_midi(data: bytes, req: NotesRequest) -> IR:
    import mido

    try:
        mid = mido.MidiFile(file=io.BytesIO(data))
        tracks = [list(track) for track in mid.tracks]
    except Exception as exc:
        reason = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:120]
        raise fail(f"This is not a MIDI file Orin can read ({reason}). Export it again as a standard MIDI "
                   "file (.mid).") from exc
    tpb = int(mid.ticks_per_beat or 0)
    if tpb <= 0:
        raise fail("This MIDI file counts time in film frames, not beats, which Orin cannot read. Export it "
                   "with a beat-based resolution.")

    stream = []
    for ti, track in enumerate(tracks):
        tick = 0
        for mi, msg in enumerate(track):
            tick += int(msg.time)
            stream.append((tick, 0 if msg.is_meta else 1, ti, mi, msg))
    stream.sort(key=lambda e: e[:4])  # by time; tempo and meter first, the rest in file order
    last_tick = stream[-1][0] if stream else 0

    tempo_changes: list[tuple[int, int]] = []
    meter: Optional[tuple[int, int]] = None
    bend = [0] * 16
    bend_range = [DEFAULT_BEND_RANGE] * 16
    rpn = [(127, 127)] * 16
    open_notes: dict[tuple[int, int, int], list[_Open]] = {}
    done: dict[tuple[int, int], list[RawNote]] = {}

    def close(key: tuple[int, int, int], end: int) -> None:
        stack = open_notes.get(key)
        if not stack:
            return
        o = stack.pop(0)
        if end > o.onset:
            done.setdefault(key[:2], []).append(RawNote(
                onset=o.onset, duration=end - o.onset, midi=o.note + o.bend_cents / 100.0,
                velocity=o.velocity, guessed=o.bent_at is not None and o.bent_at < end))

    for tick, _, ti, _, msg in stream:
        if msg.is_meta:
            if msg.type == "set_tempo":
                tempo_changes.append((tick, int(msg.tempo)))
            elif msg.type == "time_signature" and meter is None:
                meter = (int(msg.numerator), int(msg.denominator))
            continue
        ch = getattr(msg, "channel", None)
        if ch is None:
            continue
        if msg.type == "pitchwheel":
            bend[ch] = int(msg.pitch)
            cents = bend[ch] / 8192.0 * bend_range[ch] * 100.0
            for (_, och, _), stack in open_notes.items():
                if och != ch:
                    continue
                for o in stack:
                    if o.onset == tick:  # a bend sent together with the note belongs to it
                        o.bend_cents = cents
                    elif o.bent_at is None and abs(cents - o.bend_cents) > _BEND_INSIDE_CENTS:
                        o.bent_at = tick
        elif msg.type == "control_change":
            if msg.control == 101:
                rpn[ch] = (msg.value, rpn[ch][1])
            elif msg.control == 100:
                rpn[ch] = (rpn[ch][0], msg.value)
            elif msg.control == 6 and rpn[ch] == (0, 0):
                bend_range[ch] = float(msg.value) + (bend_range[ch] % 1.0)
            elif msg.control == 38 and rpn[ch] == (0, 0):
                bend_range[ch] = float(int(bend_range[ch])) + msg.value / 100.0
        elif msg.type == "note_on" and msg.velocity > 0:
            open_notes.setdefault((ti, ch, msg.note), []).append(_Open(
                onset=tick, note=int(msg.note), velocity=int(msg.velocity),
                bend_cents=bend[ch] / 8192.0 * bend_range[ch] * 100.0))
        elif msg.type in ("note_off", "note_on"):
            close((ti, ch, msg.note), tick)
    for key in list(open_notes):  # notes the file never ends
        while open_notes[key]:
            close(key, last_tick)

    if not done:
        raise fail("This MIDI file has no notes.", code="E000")
    _merge_bend_channels(done, tolerance=max(1, tpb // 16))
    melodic = {k: v for k, v in done.items() if k[1] != DRUM_CHANNEL} or done
    (track_index, channel), picked = max(melodic.items(), key=lambda kv: (len(kv[1]), -kv[0][0], -kv[0][1]))

    warnings: list[IRWarning] = []
    if len(done) > 1:
        warnings.append(IRWarning(code="one_line_kept", stage="ingest",
                                  message=f"The file has {len(done)} instrument lines. Orin reads one melody line "
                                          f"and kept the one with the most notes (track {track_index + 1}, "
                                          f"channel {channel + 1})."))
    bent_inside = any(n.guessed for n in picked)
    notes, reduced = monophonic(picked)
    if reduced:
        warnings.append(IRWarning(code="top_line_only", stage="ingest",
                                  message="Some notes sound together. Orin reads one melody line, so only the "
                                          "highest was kept. Those notes are marked as guesses."))
    if bent_inside:
        warnings.append(IRWarning(code="bend_inside_note", stage="ingest",
                                  message="Some notes are bent while they sound. Orin kept the pitch at the start "
                                          "of each; those notes are marked as guesses."))

    tempo_map = _TempoMap(tempo_changes, tpb)
    first_tick, end_tick = notes[0].onset, notes[-1].onset + notes[-1].duration
    tempos_used = {tempo_map.tempo_at(first_tick)} | {t for tick, t in tempo_changes if first_tick < tick < end_tick}
    if req.tempo_bpm is not None:
        bpm, tempo_confidence, follow_map = req.tempo_bpm, 1.0, False
    else:
        bpm = 60e6 / tempo_map.tempo_at(first_tick)
        follow_map = len(tempos_used) > 1
        if not tempo_changes:
            tempo_confidence = 0.3
            warnings.append(IRWarning(code="tempo_assumed", stage="rhythm",
                                      message="The MIDI file sets no tempo, so its default of 120 quarter notes "
                                              "per minute was used."))
        else:
            tempo_confidence = 1.0
        if follow_map:
            warnings.append(IRWarning(code="tempo_changes", stage="rhythm",
                                      message=f"The tempo changes during the piece. Note times follow the file; "
                                              f"the tempo shown is the first, {bpm:g} quarter notes per minute."))
    pulse_s = 60.0 / (bpm * SUBDIVISION)

    per_tick = SUBDIVISION / tpb
    for n in notes:
        start, end = n.onset, n.onset + n.duration
        if follow_map:
            n.onset_s, n.offset_s = tempo_map.seconds_at(start), tempo_map.seconds_at(end)
        n.onset, n.duration = start * per_tick, (end - start) * per_tick

    if meter is not None:
        cycle = meter_cycle(req.meta, meter[0], meter[1], SUBDIVISION, meter[0] * 4.0 / meter[1] * SUBDIVISION, pulse_s)
    else:
        cycle = Cycle(subdivision=SUBDIVISION, pulse_s=pulse_s)
    tonic = choose_tonic(req, notes, None, "Set the tonic in the app to change it.", warnings)
    return build_ir(req, notes, tonic=tonic, mode=Mode(), bpm=bpm, tempo_confidence=tempo_confidence, cycle=cycle,
                    total_pulses=last_tick * per_tick, warnings=warnings,
                    duration_s=tempo_map.seconds_at(last_tick) if follow_map else None)
