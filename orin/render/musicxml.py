"""FormatScore -> MusicXML 4.0, written by hand (one part, one voice).

The staff is an approximation for every format except the Western ones, and says so
through what it prints: half-flat, koron and sori signs are always explicit, a Carnatic
score carries its swara names as lyrics, and an oscillation is a "~" above the note,
never a trill (a trill names two notes, which is a different thing).

Free time prints note lengths but no time signature and no bar lines. The notes are
still grouped into measures of about eight so a line can wrap; those measures end in
invisible barlines.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
from statistics import median
from typing import Any, Optional
from xml.sax.saxutils import escape, quoteattr

from ..formats.base import FormatMeta, FormatScore, ScoreNote
from . import RenderResult, result
from .spell import (Spelling, bar_starts, beat_pulses, choose_fifths, is_measured, key_alters,
                    pulse_quarters, spell_note, spell_pitch, tempo_bpm, timeline, written_symbol)

_TYPE_NAMES = ("whole", "half", "quarter", "eighth", "16th", "32nd", "64th", "128th")
_BEAMABLE = set(_TYPE_NAMES[3:])
_FREE_NOTES_PER_MEASURE = 8
_BEAT_UNITS = {Fraction(4): ("whole", False), Fraction(3): ("half", True), Fraction(2): ("half", False),
               Fraction(3, 2): ("quarter", True), Fraction(1): ("quarter", False),
               Fraction(3, 4): ("eighth", True), Fraction(1, 2): ("eighth", False),
               Fraction(1, 4): ("16th", False)}


@dataclass
class _Atom:
    """One printed note or rest: a single standard note value."""

    note: Optional[ScoreNote]
    pos: int  # divisions from the start of the measure
    dur: int  # divisions
    type: str
    dots: int = 0
    tie_stop: bool = False
    tie_start: bool = False
    piece: int = 1  # 1 for the note's first printed piece, 2 for the next tied piece...
    beam: Optional[str] = None
    whole_measure: bool = False


@dataclass
class _Measure:
    atoms: list[_Atom]
    length: int  # divisions


@dataclass
class Layout:
    """Everything the writer needs; also what the tests and the ABC renderer look at."""

    divisions: int
    pulse: int  # divisions per pulse
    time: Optional[tuple[int, int]]  # None in free time
    fifths: int
    measures: list[_Measure]
    spellings: dict[str, Spelling]
    warnings: list[str]


def _values(divisions: int) -> list[tuple[str, int]]:
    """Standard note values that are a whole number of divisions, longest first."""
    out = []
    for k, name in enumerate(_TYPE_NAMES):
        if (4 * divisions) % (2 ** k) == 0:
            out.append((name, 4 * divisions // 2 ** k))
    return out


def _split(pos: int, dur: int, beat: int, values: list[tuple[str, int]]) -> list[tuple[int, str, int]]:
    """Cut a length into standard values: (divisions, type, dots). Off the beat, values
    fill up to the next beat first, so the beats stay visible. ``beat`` 0 = no alignment."""
    out: list[tuple[int, str, int]] = []
    while dur > 0:
        off = pos % beat if beat else 0
        room = dur if off == 0 else min(dur, beat - off)
        name, value = values[-1]
        for candidate, v in values:
            if v <= room and (off == 0 or (beat - off) % v == 0):
                name, value = candidate, v
                break
        value = min(value, dur)
        dots = 0
        dotted = value + value // 2
        if off == 0 and value % 2 == 0 and dotted <= dur and (not beat or value >= beat or dotted <= beat):
            value, dots = dotted, 1
        out.append((value, name, dots))
        pos += value
        dur -= value
    return out


def _beam_groups(score: FormatScore, divisions: int, pulse: int, time: Optional[tuple[int, int]],
                 measure_len: int) -> list[int]:
    """Boundaries (in divisions from the start of a full measure) where a beam must break."""
    groups = [divisions]  # a quarter note
    if time is not None and time[1] == 8 and divisions % 2 == 0:
        eighth = divisions // 2
        cycle = score.cycle
        beat = (cycle.subdivision if cycle else 1) * pulse
        if time[0] % 3 == 0 and time[0] > 3:
            groups = [3 * eighth]
        elif cycle is not None and cycle.groups and sum(cycle.groups) * beat == measure_len:
            groups = [g * beat for g in cycle.groups]
        else:
            groups = [2 * eighth]
    bounds, pos, i = [], 0, 0
    while pos < measure_len:
        pos += max(1, groups[i % len(groups)])
        bounds.append(pos)
        i += 1
    return bounds


def _set_beams(atoms: list[_Atom], bounds: list[int]) -> None:
    run: list[_Atom] = []

    def close() -> None:
        if len(run) > 1:
            for i, atom in enumerate(run):
                atom.beam = "begin" if i == 0 else "end" if i == len(run) - 1 else "continue"
        run.clear()

    group = None
    for atom in atoms:
        g = bisect_right(bounds, atom.pos)
        if atom.note is None or atom.type not in _BEAMABLE or g != group:
            close()
        if atom.note is not None and atom.type in _BEAMABLE:
            run.append(atom)
            group = g
    close()


def layout(score: FormatScore, meta: FormatMeta) -> Layout:
    warnings: list[str] = []
    measured = is_measured(score)
    quarters = pulse_quarters(score)
    time: Optional[tuple[int, int]] = score.cycle.time_signature if measured else None  # type: ignore[union-attr]
    den = quarters.denominator
    if den & (den - 1) or den > 16:
        # A pulse that is not a binary share of a quarter note would need tuplets.
        quarters = Fraction(1, 2)
        if measured:
            time = (score.cycle.length_pulses, 8)  # type: ignore[union-attr]
        warnings.append("The pulse does not divide a quarter note evenly, so each pulse is written as an eighth note.")
    divisions, pulse = quarters.denominator, quarters.numerator
    values = _values(divisions)

    tl = timeline(score)
    spellings = {n.id: spell_note(score, meta, n) for n in score.notes}
    fifths = choose_fifths(score, meta, list(spellings.values()))

    # Notes and the rests between them, in divisions.
    events: list[tuple[int, int, Optional[ScoreNote]]] = []
    cursor = 0
    for start, length, note in tl:
        if start * pulse > cursor:
            events.append((cursor, start * pulse - cursor, None))
        events.append((start * pulse, length * pulse, note))
        cursor = (start + length) * pulse
    end = cursor

    measures: list[_Measure] = []
    pieces: dict[str, int] = {}

    def atoms_for(start: int, dur: int, note: Optional[ScoreNote], origin: int, beat: int,
                  tie_in: bool, tie_out: bool) -> list[_Atom]:
        parts = _split(start - origin if beat else 0, dur, beat, values)
        out, pos = [], start - origin
        for i, (value, name, dots) in enumerate(parts):
            atom = _Atom(note=note, pos=pos, dur=value, type=name, dots=dots)
            if note is not None:
                pieces[note.id] = pieces.get(note.id, 0) + 1
                atom.piece = pieces[note.id]
                atom.tie_stop = tie_in or i > 0
                atom.tie_start = tie_out or i < len(parts) - 1
            out.append(atom)
            pos += value
        return out

    if measured:
        bar_len = score.cycle.length_pulses * pulse  # type: ignore[union-attr]
        beat = beat_pulses(score) * pulse
        starts = [b * pulse for b in bar_starts(score, end // pulse)]
        bounds = _beam_groups(score, divisions, pulse, time, bar_len)
        for b0, b1 in zip(starts, starts[1:]):
            atoms: list[_Atom] = []
            cursor = b0
            for start, dur, note in events:
                lo, hi = max(start, b0), min(start + dur, b1)
                if lo >= hi:
                    continue
                atoms += atoms_for(lo, hi - lo, note, b0, beat, start < b0, start + dur > b1)
                cursor = hi
            if cursor < b1:
                atoms += atoms_for(cursor, b1 - cursor, None, b0, beat, False, False)
            if len([a for a in atoms if a.note is None]) == len(atoms):
                atoms = [_Atom(note=None, pos=0, dur=b1 - b0, type="whole", whole_measure=True)]
            _set_beams(atoms, bounds)
            measures.append(_Measure(atoms=atoms, length=b1 - b0))
    else:
        chunk: list[tuple[int, int, Optional[ScoreNote]]] = []
        chunks = [chunk]
        for event in events:
            if event[2] is not None and sum(1 for e in chunk if e[2] is not None) >= _FREE_NOTES_PER_MEASURE:
                chunk = []
                chunks.append(chunk)
            chunk.append(event)
        for chunk in chunks:
            if not chunk:
                atoms = [_Atom(note=None, pos=0, dur=4 * divisions, type="whole", whole_measure=True)]
                measures.append(_Measure(atoms=atoms, length=4 * divisions))
                continue
            origin = chunk[0][0]
            atoms = []
            for start, dur, note in chunk:
                atoms += atoms_for(start, dur, note, origin, 0, False, False)
            length = chunk[-1][0] + chunk[-1][1] - origin
            _set_beams(atoms, _beam_groups(score, divisions, pulse, None, length))
            measures.append(_Measure(atoms=atoms, length=length))

    return Layout(divisions=divisions, pulse=pulse, time=time, fifths=fifths, measures=measures,
                  spellings=spellings, warnings=warnings)


def _clef(spellings: dict[str, Spelling]) -> str:
    mid = median([s.midi for s in spellings.values()]) if spellings else 67.0
    if mid >= 62:
        return "<clef><sign>G</sign><line>2</line></clef>"
    if mid >= 53:
        return "<clef><sign>G</sign><line>2</line><clef-octave-change>-1</clef-octave-change></clef>"
    return "<clef><sign>F</sign><line>4</line></clef>"


def _alter_text(alter: float) -> str:
    return str(int(alter)) if alter == int(alter) else str(alter)


def _pitch(sp: Spelling) -> str:
    alter = f"<alter>{_alter_text(sp.alter)}</alter>" if sp.alter else ""
    return f"<pitch><step>{sp.step}</step>{alter}<octave>{sp.octave}</octave></pitch>"


def _words(text: str) -> str:
    return (f'<direction placement="above"><direction-type><words>{escape(text)}</words>'
            f"</direction-type></direction>")


def _tempo(score: FormatScore) -> str:
    bpm = tempo_bpm(score)
    if not bpm:
        return ""
    beat = pulse_quarters(score) * beat_pulses(score)
    unit, dotted = _BEAT_UNITS.get(beat, ("quarter", False))
    shown = bpm if beat in _BEAT_UNITS else bpm * float(beat)
    dot = "<beat-unit-dot/>" if dotted else ""
    return (f'<direction placement="above"><direction-type><metronome><beat-unit>{unit}</beat-unit>{dot}'
            f"<per-minute>{round(shown)}</per-minute></metronome></direction-type>"
            f'<sound tempo="{bpm * float(beat):.2f}"/></direction>')


def _grace(score: FormatScore, meta: FormatMeta, note: ScoreNote) -> Optional[Spelling]:
    """The neighbouring pitch a grace-note ornament touches, if the ornament says which way."""
    for orn in note.ornaments:
        if orn.action == "drop" or orn.staff_mark != "grace" or orn.symbol or orn.amplitude_cents <= 0:
            continue
        direction = {"touch_above": 1, "touch_below": -1}.get(orn.shape or "")
        if direction is None:
            continue
        opts = score.options
        degree = (note.degree - 1 + direction) % 7 + 1
        return spell_pitch(opts.written_tonic, opts.written_tonic_octave, degree,
                           note.cents + direction * orn.amplitude_cents, meta.accidental_style)
    return None


def to_xml(score: FormatScore, meta: FormatMeta, lay: Layout) -> str:
    measured = lay.time is not None
    key = key_alters(lay.fifths)
    lyrics = meta.naming == "relative"
    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
        '"http://www.musicxml.org/dtds/partwise.dtd">',
        '<score-partwise version="4.0">',
        '<part-list><score-part id="P1"><part-name print-object="no">Melody</part-name></score-part></part-list>',
        '<part id="P1">',
    ]
    state: dict[tuple[str, int], float] = {}

    def accidental(sp: Spelling) -> str:
        """Explicit sign for every microtonal note, and wherever the key signature and
        what was already written (in this bar, or so far in free time) say otherwise."""
        slot = (sp.step, sp.octave)
        in_key = float(key[sp.step])
        current = state.get(slot, in_key)
        state[slot] = sp.alter
        if sp.microtonal or current != sp.alter or (not measured and sp.alter != in_key):
            return f"<accidental>{sp.accidental}</accidental>"
        return ""

    for index, measure in enumerate(lay.measures):
        if measured:
            state.clear()
        out.append(f'<measure number="{index + 1}">')
        if index == 0:
            if lay.time is not None:
                time = f"<time><beats>{lay.time[0]}</beats><beat-type>{lay.time[1]}</beat-type></time>"
            else:
                time = '<time print-object="no"><senza-misura/></time>'
                # Verovio would otherwise number the invisible layout measures.
                out.append("<print><measure-numbering>none</measure-numbering></print>")
            out.append(f"<attributes><divisions>{lay.divisions}</divisions><key><fifths>{lay.fifths}</fifths></key>"
                       f"{time}{_clef(lay.spellings)}</attributes>")
            if measured:
                out.append(_tempo(score))
        for atom in measure.atoms:
            if atom.note is None:
                if atom.whole_measure:
                    out.append(f'<note><rest measure="yes"/><duration>{atom.dur}</duration><voice>1</voice></note>')
                else:
                    out.append(f"<note><rest/><duration>{atom.dur}</duration><voice>1</voice>"
                               f"<type>{atom.type}</type>{'<dot/>' * atom.dots}</note>")
                continue
            note, sp = atom.note, lay.spellings[atom.note.id]
            first = atom.piece == 1
            if first:
                for orn in note.ornaments:
                    symbol = written_symbol(orn)
                    if symbol:
                        out.append(_words(symbol))
                grace = _grace(score, meta, note)
                if grace is not None:
                    out.append(f'<note><grace slash="yes"/>{_pitch(grace)}<voice>1</voice><type>eighth</type>'
                               f"{accidental(grace)}</note>")
            note_id = note.id if first else f"{note.id}t{atom.piece}"
            parts = [f"<note id={quoteattr(note_id)}>", _pitch(sp), f"<duration>{atom.dur}</duration>"]
            if atom.tie_stop:
                parts.append('<tie type="stop"/>')
            if atom.tie_start:
                parts.append('<tie type="start"/>')
            parts.append(f"<voice>1</voice><type>{atom.type}</type>{'<dot/>' * atom.dots}")
            if first:
                parts.append(accidental(sp))
            if atom.beam:
                parts.append(f'<beam number="1">{atom.beam}</beam>')
            if atom.tie_stop or atom.tie_start:
                tied = ('<tied type="stop"/>' if atom.tie_stop else "") + ('<tied type="start"/>' if atom.tie_start else "")
                parts.append(f"<notations>{tied}</notations>")
            if lyrics and first:
                parts.append(f'<lyric number="1"><syllabic>single</syllabic><text>{escape(note.name)}</text></lyric>')
            parts.append("</note>")
            out.append("".join(parts))
        if not measured:
            out.append('<barline location="right"><bar-style>none</bar-style></barline>')
        elif index == len(lay.measures) - 1:
            out.append('<barline location="right"><bar-style>light-heavy</bar-style></barline>')
        out.append("</measure>")
    out += ["</part>", "</score-partwise>"]
    return "\n".join(line for line in out if line) + "\n"


def render(score: FormatScore, meta: FormatMeta, **opts: Any) -> RenderResult:
    lay = layout(score, meta)
    return result("musicxml", text=to_xml(score, meta, lay), warnings=tuple(lay.warnings))

