"""FormatScore -> ABC 2.1 text, for the twelve-tone formats.

The K: field is the key SIGNATURE that keeps accidentals few, named from the written
tonic ("D", "Dm", "Ddor") so that a reader gets the tonic right. It is a spelling aid, not
a claim about the mode, and the N: line says so. ABC has no standard quarter-tone signs, so a
score with half-flats is rounded to semitones here and a warning says so.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Optional

from ..formats.base import FormatMeta, FormatScore, ScoreNote
from . import RenderResult, result
from .spell import (Spelling, TextBuilder, abc_key, bar_starts, beat_pulses, choose_fifths, is_measured,
                    key_alters, ornament_symbol, pulse_quarters, spell_note, spell_tonic, tempo_bpm,
                    timeline, tonic_fifths)

_ACCIDENTALS = {-2: "__", -1: "_", 0: "=", 1: "^", 2: "^^"}
_BARS_PER_LINE = 4
_FREE_LINE_WIDTH = 60


def _semitones(sp: Spelling) -> int:
    return int(sp.alter + 0.5) if sp.alter >= 0 else -int(-sp.alter + 0.5)


def _pitch(sp: Spelling) -> str:
    """Letter and octave marks: C is middle C, c the octave above, c' above that, C, below."""
    if sp.octave >= 5:
        return sp.step.lower() + "'" * (sp.octave - 5)
    return sp.step + "," * (4 - sp.octave)


def _length(units: int) -> str:
    return "" if units == 1 else str(units)


def render(score: FormatScore, meta: FormatMeta, **opts: Any) -> RenderResult:
    warnings: list[str] = []
    measured = is_measured(score)
    tl = timeline(score)
    spellings = {n.id: spell_note(score, meta, n) for n in score.notes}
    fifths = choose_fifths(score, meta, list(spellings.values()))
    key = key_alters(fifths)
    if any(sp.microtonal for sp in spellings.values()):
        warnings.append("ABC cannot write quarter tones: notes between the twelve pitches were rounded to the "
                        "nearest semitone here. Use the staff or note-name output for them.")

    unit = pulse_quarters(score) / 4  # whole notes per pulse
    time = score.cycle.time_signature if measured else None  # type: ignore[union-attr]
    den = unit.denominator
    if den & (den - 1) or den > 64:
        unit = Fraction(1, 8)
        if measured:
            time = (score.cycle.length_pulses, 8)  # type: ignore[union-attr]
        warnings.append("The pulse does not divide a quarter note evenly, so each pulse is written as an eighth note.")
    per_pulse = unit.numerator  # L: units per pulse

    # Header
    tonic = spell_tonic(score, meta)
    mode = meta.mode(score.options.mode_id)
    parent: Optional[str] = mode.key_signature_parent if mode else None
    if parent is None:
        parent = "minor" if tonic_fifths(tonic, "minor") == fifths else "major"
    out = TextBuilder()
    out.line("X:1")
    out.line(f"T:{opts.get('title') or 'Orin transcription'}")
    out.line(f"M:{time[0]}/{time[1]}" if time else "M:none")
    out.line(f"L:1/{unit.denominator}")
    bpm = tempo_bpm(score)
    if measured and bpm:
        beat_note = unit * beat_pulses(score)  # the beat as a share of a whole note
        out.line(f"Q:{beat_note.numerator}/{beat_note.denominator}={round(bpm)}")
    out.line(f"N:Written {meta.tonic_word}: {tonic.step}{tonic.ascii}{tonic.octave}. "
             "The key signature is a spelling aid, not a claim about the mode.")
    out.line(f"K:{abc_key(tonic, fifths, parent)}")

    # Accidentals carry to the end of the bar. ABC readers differ on whether that covers
    # every octave or only the same one, so an accidental is written whenever either
    # reading would give the wrong pitch.
    by_step: dict[str, int] = {}
    by_pitch: dict[tuple[str, int], int] = {}

    def token(note: ScoreNote, units: int) -> str:
        sp = spellings[note.id]
        alter = max(-2, min(2, _semitones(sp)))
        in_key = key[sp.step]
        explicit = (by_step.get(sp.step, in_key) != alter or by_pitch.get((sp.step, sp.octave), in_key) != alter
                    or (not measured and alter != in_key))
        by_step[sp.step] = by_pitch[(sp.step, sp.octave)] = alter
        return (_ACCIDENTALS[alter] if explicit else "") + _pitch(sp) + _length(units * per_pulse)

    def annotation(note: ScoreNote) -> str:
        symbol = ornament_symbol(note)
        return f'"^{symbol}"' if symbol and '"' not in symbol else ""

    # Notes and the rests between them, in pulses.
    events: list[tuple[int, int, Optional[ScoreNote]]] = []
    cursor = 0
    for start, length, note in tl:
        if start > cursor:
            events.append((cursor, start - cursor, None))
        events.append((start, length, note))
        cursor = start + length

    if not measured:
        width = 0
        for i, (start, length, note) in enumerate(events):
            lead = "" if i == 0 else "\n" if width > _FREE_LINE_WIDTH else " "
            if lead == "\n":
                width = 0
            out.add(lead)
            before = out.length
            if note is None:
                out.add("z" + _length(length * per_pulse))
            else:
                out.add(annotation(note))
                out.add(token(note, length), note.id)
            width += out.length - before + 1
        out.line()
        return result("abc", text=out.text, tokens=out.tokens(), warnings=tuple(warnings))

    beat = beat_pulses(score)
    starts = bar_starts(score, cursor)
    for index, (b0, b1) in enumerate(zip(starts, starts[1:])):
        by_step.clear()
        by_pitch.clear()
        pos = b0
        pieces: list[tuple[int, int, Optional[ScoreNote], bool, bool]] = []
        for start, length, note in events:
            lo, hi = max(start, b0), min(start + length, b1)
            if lo < hi:
                pieces.append((lo, hi - lo, note, start < b0, start + length > b1))
                pos = hi
        if pos < b1:
            pieces.append((pos, b1 - pos, None, False, False))
        for i, (start, length, note, tied_in, tied_out) in enumerate(pieces):
            # Notes that start in the same beat are written together (a beam); a space starts a new beat.
            if i and (start - b0) // beat != (pieces[i - 1][0] - b0) // beat:
                out.add(" ")
            if note is None:
                out.add("z" + _length(length * per_pulse))
                continue
            if not tied_in:
                out.add(annotation(note))
                out.add(token(note, length), note.id)
            else:  # the tied part repeats the pitch as it was written before the bar line
                sp = spellings[note.id]
                alter = max(-2, min(2, _semitones(sp)))
                explicit = alter != key[sp.step]
                by_step[sp.step] = by_pitch[(sp.step, sp.octave)] = alter
                out.add((_ACCIDENTALS[alter] if explicit else "") + _pitch(sp) + _length(length * per_pulse), note.id)
            if tied_out:
                out.add("-")
        last = index == len(starts) - 2
        out.add(" |]" if last else " |")
        if last or (index + 1) % _BARS_PER_LINE == 0:
            out.line()
        else:
            out.add(" ")
    return result("abc", text=out.text, tokens=out.tokens(), warnings=tuple(warnings))
