"""FormatScore -> Standard MIDI File, with per-note pitch bend for microtones (spec 9.2).

Each note is the nearest MIDI key plus a pitch bend for the remainder, so a half-flat or
a just-intonation swara plays at its written pitch. Notes alternate between two channels
and the bend is sent before the note-on, so retuning for one note never bends the tail
of the one before. The bend range is set to 2 semitones with RPN 0. Some General MIDI
players ignore pitch bend; they will play the nearest piano key instead.

``read_back`` re-extracts onset, duration and sounding frequency from the bytes, for the
round-trip test of spec 11.5 and for anyone who wants to check a file.
"""
from __future__ import annotations

import io
import math
from typing import Any

import mido

from ..formats.base import FormatMeta, FormatScore
from . import RenderResult, result
from .spell import is_measured, pulse_quarters

TICKS_PER_QUARTER = 480
BEND_RANGE_SEMITONES = 2
CHANNELS = (0, 1)
A4_HZ = 440.0  # MIDI key 69 is 440 Hz on any standard synth, whatever the score's own reference


def key_and_bend(hz: float) -> tuple[int, int]:
    """Nearest MIDI key and the 14-bit bend (-8192..8191) that reaches ``hz`` from it."""
    exact = 69.0 + 12.0 * math.log2(hz / A4_HZ)
    key = max(0, min(127, int(math.floor(exact + 0.5))))
    bend = int(round((exact - key) / BEND_RANGE_SEMITONES * 8192))
    return key, max(-8192, min(8191, bend))


def render(score: FormatScore, meta: FormatMeta, **opts: Any) -> RenderResult:
    options = score.options
    quarter_s = options.pulse_s / float(pulse_quarters(score)) if options.pulse_s > 0 else 0.5
    tempo = max(1, min(0xFFFFFF, int(round(quarter_s * 1_000_000))))
    quarter_s = tempo / 1_000_000  # what a player will really use

    def ticks(seconds: float) -> int:
        return max(0, int(round(seconds / quarter_s * TICKS_PER_QUARTER)))

    # (tick, order, message). Order at one tick: note-offs, then bends, then note-ons.
    events: list[tuple[int, int, Any]] = []
    for channel in CHANNELS:
        for control, value in ((101, 0), (100, 0), (6, BEND_RANGE_SEMITONES), (38, 0), (101, 127), (100, 127)):
            events.append((0, 0, mido.Message("control_change", channel=channel, control=control, value=value)))
        if opts.get("program") is not None:
            events.append((0, 0, mido.Message("program_change", channel=channel, program=int(opts["program"]) % 128)))

    notes = sorted(score.notes, key=lambda n: (n.onset_s, n.start_pulse))
    bent = False
    for index, note in enumerate(notes):
        if options.tonic_hz <= 0:
            break
        channel = CHANNELS[index % len(CHANNELS)]
        key, bend = key_and_bend(options.tonic_hz * 2.0 ** (note.cents / 1200.0))
        bent = bent or abs(bend) > 40  # about one cent
        on = ticks(note.onset_s)
        off = max(on + 1, ticks(note.onset_s + note.duration_s))
        if index + 1 < len(notes):  # one note at a time
            off = min(off, max(on + 1, ticks(notes[index + 1].onset_s)))
        velocity = max(1, min(127, int(note.velocity)))
        events.append((on, 2, mido.Message("pitchwheel", channel=channel, pitch=bend)))
        events.append((on, 3, mido.Message("note_on", channel=channel, note=key, velocity=velocity)))
        events.append((off, 1, mido.Message("note_off", channel=channel, note=key, velocity=0)))

    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=f"Orin: {meta.display_name}"[:60], time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    if is_measured(score):
        num, den = score.cycle.time_signature  # type: ignore[union-attr]
        if 1 <= num <= 255 and den in (1, 2, 4, 8, 16, 32):
            track.append(mido.MetaMessage("time_signature", numerator=num, denominator=den, time=0))
    now = 0
    for tick, _, message in sorted(events, key=lambda e: (e[0], e[1])):
        track.append(message.copy(time=tick - now))
        now = tick
    track.append(mido.MetaMessage("end_of_track", time=0))

    midi = mido.MidiFile(type=0, ticks_per_beat=TICKS_PER_QUARTER)
    midi.tracks.append(track)
    buffer = io.BytesIO()
    midi.save(file=buffer)
    warnings = ()
    if bent:
        warnings = ("Notes between the piano keys are played with pitch bend. Some General MIDI players ignore "
                    "pitch bend and will play the nearest key instead.",)
    return result("midi", data=buffer.getvalue(), warnings=warnings)


def read_back(data: bytes) -> list[dict]:
    """Per note, in order: onset_s, duration_s, hz (key plus the bend in force at note-on),
    and the raw key, bend and channel."""
    midi = mido.MidiFile(file=io.BytesIO(data))
    bend: dict[int, int] = {}
    bend_range: dict[int, float] = {}
    rpn: dict[int, list[int]] = {}
    sounding: dict[tuple[int, int], dict] = {}
    out: list[dict] = []
    now = 0.0
    for message in midi:  # merged tracks, delta times in seconds under the tempo map
        now += message.time
        if message.is_meta:
            continue
        channel = getattr(message, "channel", 0)
        if message.type == "control_change":
            selected = rpn.setdefault(channel, [127, 127])
            if message.control == 101:
                selected[0] = message.value
            elif message.control == 100:
                selected[1] = message.value
            elif message.control == 6 and selected == [0, 0]:
                bend_range[channel] = float(message.value)
        elif message.type == "pitchwheel":
            bend[channel] = message.pitch
        elif message.type == "note_on" and message.velocity > 0:
            semitones = message.note + bend.get(channel, 0) / 8192.0 * bend_range.get(channel, 2.0)
            entry = {"onset_s": now, "duration_s": 0.0, "hz": A4_HZ * 2.0 ** ((semitones - 69.0) / 12.0),
                     "key": message.note, "bend": bend.get(channel, 0), "channel": channel}
            sounding[(channel, message.note)] = entry
            out.append(entry)
        elif message.type in ("note_off", "note_on"):
            entry = sounding.pop((channel, message.note), None)
            if entry is not None:
                entry["duration_s"] = now - entry["onset_s"]
    return out
