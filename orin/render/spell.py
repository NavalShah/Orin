"""Shared by the staff and text renderers: absolute spelling, key-signature choice, and
the pulse grid of a FormatScore.

A ScoreNote says how far it is above the tonic (``cents``) and which staff letter it
uses relative to the tonic's letter (``degree``). Spelling turns that into a letter,
an accidental and an octave at the written tonic. Accidentals are quantised to what
the format's notation can draw: semitones ("standard"), or the 50-cent naming grid
("arabic", "persian"). The quantised sign is a NAME, not a claim about tuning.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional, Sequence

from ..formats.base import FormatMeta, FormatScore, ScoreNote, ScoreOrnament, WrittenPitch

STEPS = "CDEFGAB"
NATURAL_CENTS = {"C": 0, "D": 200, "E": 400, "F": 500, "G": 700, "A": 900, "B": 1100}
SOLFEGE = {"C": "Do", "D": "Re", "E": "Mi", "F": "Fa", "G": "Sol", "A": "La", "B": "Si"}  # fixed do

# quantised alteration in cents -> (MusicXML accidental name, ASCII accidental, words)
_STANDARD = {
    -200: ("flat-flat", "bb", "double-flat"),
    -100: ("flat", "b", "flat"),
    0: ("natural", "", ""),
    100: ("sharp", "#", "sharp"),
    200: ("double-sharp", "##", "double-sharp"),
}
_ARABIC = {
    **_STANDARD,
    -150: ("three-quarters-flat", "db", "flat-and-a-half"),
    -50: ("quarter-flat", "d", "half-flat"),
    50: ("quarter-sharp", "+", "half-sharp"),
    150: ("three-quarters-sharp", "#+", "sharp-and-a-half"),
}
_PERSIAN = {**_ARABIC, -50: ("koron", "p", "koron"), 50: ("sori", ">", "sori")}
_TABLES = {"standard": _STANDARD, "arabic": _ARABIC, "persian": _PERSIAN}

_SHARP_ORDER = "FCGDAEB"
_MAJOR_FIFTHS = {"C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F": -1}
_MAJOR_KEY_NAMES = {-7: "Cb", -6: "Gb", -5: "Db", -4: "Ab", -3: "Eb", -2: "Bb", -1: "F", 0: "C",
                    1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#"}
_MINOR_KEY_NAMES = {-7: "Ab", -6: "Eb", -5: "Bb", -4: "F", -3: "C", -2: "G", -1: "D", 0: "A",
                    1: "E", 2: "B", 3: "F#", 4: "C#", 5: "G#", 6: "D#", 7: "A#"}


@dataclass(frozen=True)
class Spelling:
    step: str
    alter: float  # semitones, exact halves only: -0.5 is a half-flat
    octave: int  # scientific: C4 is middle C
    accidental: str  # MusicXML accidental name; "natural" when alter is 0
    ascii: str  # "#", "b", "+", "d", "#+", "db", "p", ">", "##", "bb" or ""
    words: str  # "half-flat", "koron", "sharp"... or ""

    @property
    def microtonal(self) -> bool:
        return self.alter != int(self.alter)

    @property
    def name(self) -> str:
        """Letter, ASCII accidental and octave: "Ed4"."""
        return f"{self.step}{self.ascii}{self.octave}"

    @property
    def midi(self) -> float:
        """The written pitch as a MIDI note number (x.5 for a half-flat or half-sharp)."""
        return 12 * (self.octave + 1) + NATURAL_CENTS[self.step] / 100.0 + self.alter

    @property
    def solfege(self) -> str:
        """Fixed-do name with the accidental in words: "Mi half-flat", "Mi koron"."""
        return f"{SOLFEGE[self.step]} {self.words}".strip()


def _quantise(alter_cents: float, grid: int) -> int:
    return int(math.floor(alter_cents / grid + 0.5)) * grid


def spell_pitch(tonic: WrittenPitch, tonic_octave: int, degree: int, cents: float,
                style: str = "standard") -> Spelling:
    """Spell the pitch ``cents`` above a tonic written as ``tonic`` in ``tonic_octave``."""
    table = _TABLES.get(style, _STANDARD)
    grid = 100 if table is _STANDARD else 50
    tonic_step = tonic.step if tonic.step in NATURAL_CENTS else "C"
    absolute = NATURAL_CENTS[tonic_step] + tonic.alter_cents + 1200.0 * tonic_octave + cents

    def at(step: str) -> tuple[int, int]:
        octave = int(math.floor((absolute - NATURAL_CENTS[step]) / 1200.0 + 0.5))
        return octave, _quantise(absolute - NATURAL_CENTS[step] - 1200.0 * octave, grid)

    index = (STEPS.index(tonic_step) + max(1, min(7, int(degree))) - 1) % 7
    step = STEPS[index]
    octave, alter = at(step)
    if alter not in table:
        # The degree and the pitch disagree (a 2nd written 600 cents up). Use the letter
        # that needs the smallest accidental instead of an unreadable one.
        step = min(STEPS, key=lambda s: abs(at(s)[1]))
        octave, alter = at(step)
    name, text, words = table.get(alter, _STANDARD[0])
    return Spelling(step=step, alter=alter / 100.0, octave=octave, accidental=name, ascii=text, words=words)


def spell_note(score: FormatScore, meta: FormatMeta, note: ScoreNote) -> Spelling:
    opts = score.options
    return spell_pitch(opts.written_tonic, opts.written_tonic_octave, note.degree, note.cents, meta.accidental_style)


def spell_tonic(score: FormatScore, meta: FormatMeta) -> Spelling:
    opts = score.options
    return spell_pitch(opts.written_tonic, opts.written_tonic_octave, 1, 0.0, meta.accidental_style)


# ---------------------------------------------------------------------------
# Key signatures. A spelling aid only: never a claim about the mode.
# ---------------------------------------------------------------------------

def key_alters(fifths: int) -> dict[str, int]:
    """Step -> alteration in semitones under a key signature of ``fifths`` sharps (flats if negative)."""
    out = {s: 0 for s in STEPS}
    for s in _SHARP_ORDER[:max(0, fifths)]:
        out[s] = 1
    for s in _SHARP_ORDER[::-1][:max(0, -fifths)]:
        out[s] = -1
    return out


def tonic_fifths(tonic: Spelling, parent: str = "major") -> Optional[int]:
    """Key signature of the major or minor key on this tonic, or None if it has none within 7."""
    if tonic.microtonal:
        return None
    fifths = _MAJOR_FIFTHS[tonic.step] + 7 * int(tonic.alter) - (3 if parent == "minor" else 0)
    return fifths if -7 <= fifths <= 7 else None


def key_name(fifths: int, parent: str = "major") -> str:
    """"D" for two sharps as a major key, "Bm" as a minor one (ABC spelling)."""
    fifths = max(-7, min(7, fifths))
    return _MINOR_KEY_NAMES[fifths] + "m" if parent == "minor" else _MAJOR_KEY_NAMES[fifths]


_ABC_MODES = {1: "lyd", 0: "", -1: "mix", -2: "dor", -3: "m", -4: "phr", -5: "loc"}


def abc_key(tonic: Spelling, fifths: int, parent: str = "major") -> str:
    """The ABC K: value for a key signature. Named from the written tonic wherever ABC has
    a word for that signature on that tonic ("D", "Dm", "Ddor", "Dmix"), so a reader gets the
    right tonic; otherwise the major or minor key that has the signature."""
    if not tonic.microtonal and abs(tonic.alter) <= 1:
        offset = fifths - (_MAJOR_FIFTHS[tonic.step] + 7 * int(tonic.alter))
        if offset in _ABC_MODES:
            return f"{tonic.step}{tonic.ascii}{_ABC_MODES[offset]}"
    return key_name(fifths, parent)


def choose_fifths(score: FormatScore, meta: FormatMeta, spellings: Optional[Sequence[Spelling]] = None) -> int:
    """The key signature (-7..7) that leaves the fewest explicit accidentals.

    Only notes with a whole-semitone accidental count: half-flats, koron and sori are
    always written out. Ties go to the key of the written tonic (the chosen mode's
    parent first), then to the fewest sharps or flats.
    """
    if spellings is None:
        spellings = [spell_note(score, meta, n) for n in score.notes]
    plain = [s for s in spellings if not s.microtonal]
    tonic = spell_tonic(score, meta)
    mode = meta.mode(score.options.mode_id)
    parents = ([mode.key_signature_parent] if mode and mode.key_signature_parent else []) + ["major", "minor"]
    preferred = [f for f in (tonic_fifths(tonic, p) for p in parents) if f is not None]
    if not plain:
        return preferred[0] if mode and mode.key_signature_parent and preferred else 0

    def cost(fifths: int) -> tuple[int, int, int]:
        alters = key_alters(fifths)
        explicit = sum(1 for s in plain if alters[s.step] != int(s.alter))
        rank = preferred.index(fifths) if fifths in preferred else len(preferred)
        return explicit, rank, abs(fifths)

    return min(range(-7, 8), key=lambda f: (*cost(f), f < 0))


# ---------------------------------------------------------------------------
# The pulse grid
# ---------------------------------------------------------------------------

def is_measured(score: FormatScore) -> bool:
    return score.cycle is not None and not score.options.free_time and score.cycle.length_pulses > 0


def beat_pulses(score: FormatScore) -> int:
    """Pulses per beat."""
    if score.cycle is not None and score.cycle.subdivision > 0:
        return score.cycle.subdivision
    return max(1, score.options.subdivision)


def pulse_quarters(score: FormatScore) -> Fraction:
    """Length of one pulse in quarter notes, as an exact fraction."""
    cycle = score.cycle
    if cycle is not None and cycle.length_pulses > 0 and cycle.time_signature[1] > 0:
        num, den = cycle.time_signature
        return Fraction(num * 4, den * cycle.length_pulses)
    return Fraction(1, max(1, score.options.subdivision))


def tempo_bpm(score: FormatScore) -> Optional[float]:
    """Beats per minute, from the options or from the pulse length."""
    opts = score.options
    if opts.tempo_bpm and opts.tempo_bpm > 0:
        return float(opts.tempo_bpm)
    if opts.pulse_s and opts.pulse_s > 0:
        return 60.0 / (opts.pulse_s * beat_pulses(score))
    return None


def bar_starts(score: FormatScore, end_pulse: int) -> list[int]:
    """Pulse of every bar line from 0 up to the first one at or after ``end_pulse`` (at least
    one bar). Pulse 0 of a ``timeline`` is a cycle start; [] in free time."""
    if not is_measured(score):
        return []
    length = score.cycle.length_pulses  # type: ignore[union-attr]
    out = [0, length]
    while out[-1] < end_pulse:
        out.append(out[-1] + length)
    return out


def timeline(score: FormatScore) -> list[tuple[int, int, ScoreNote]]:
    """(start pulse, length in pulses, note) in time order, made strictly monophonic:
    starts are increasing, lengths at least 1, and no note runs into the next.

    Pulse 0 is a cycle start, as the conversion engine writes it, so a first note that
    starts later is preceded by rests. Whole empty cycles before the first note are
    dropped. In free time the first note starts at 0.
    """
    notes = sorted(score.notes, key=lambda n: (n.start_pulse, n.onset_s))
    if not notes:
        return []
    shift = notes[0].start_pulse
    if is_measured(score):
        length = score.cycle.length_pulses  # type: ignore[union-attr]
        shift = (shift // length) * length
    out: list[tuple[int, int, ScoreNote]] = []
    for note in notes:
        start = note.start_pulse - shift
        length = max(1, note.duration_pulses)
        if out:
            prev_start, prev_length, prev = out[-1]
            start = max(start, prev_start + 1)
            if prev_start + prev_length > start:
                out[-1] = (prev_start, start - prev_start, prev)
        out.append((start, length, note))
    return out


def ornament_symbol(note: ScoreNote, allowed: Optional[str] = None) -> str:
    """Text mark of the first ornament the notation keeps, or "". ``allowed`` limits the marks."""
    for orn in note.ornaments:
        symbol = written_symbol(orn)
        if symbol and (allowed is None or symbol in allowed):
            return symbol
    return ""


def written_symbol(orn: ScoreOrnament) -> str:
    if orn.action == "drop":
        return ""
    if orn.symbol:
        return orn.symbol
    if orn.staff_mark == "wavy":
        return "~"
    if orn.staff_mark == "gliss":
        return "\\" if orn.shape == "down" else "/"
    return ""


class TextBuilder:
    """Accumulates a text rendering and remembers where each note's token landed."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.spans: list[tuple[str, int, int]] = []  # (note id, start, end)

    def add(self, text: str, note_id: Optional[str] = None) -> None:
        if note_id is not None and text:
            self.spans.append((note_id, self.length, self.length + len(text)))
        self.parts.append(text)
        self.length += len(text)

    def line(self, text: str = "") -> None:
        self.add(text + "\n")

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def tokens(self) -> tuple:
        from . import TokenRef

        return tuple(TokenRef(note_id=i, start=a, end=b) for i, a, b in self.spans)
