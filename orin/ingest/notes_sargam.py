"""Sargam (swara) text -> IR (spec 7.14).

Notations differ between schools, so Orin accepts exactly one grammar, stated in
``GRAMMAR_HELP`` and shown in the UI help. It is a typing convention, not a claim about
how Carnatic music should be written. Anything outside it is reported with its line and
column, and every problem in the text is collected before the parser gives up.

Problem codes:
  E001 unknown token              E005 unbalanced group
  E002 variant required           E006 bar length does not fit the tala
  E003 invalid variant            E007 bad header
  E004 extension with no note before it (warning; read as a rest)
  E008 unknown raga or tala       W101 variant not in the declared raga (warning; note kept)
  E000 no notes at all
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from ..formats import registry
from ..formats.base import FormatMeta, ModeEntry, RhythmTemplate
from ..formats.carnatic import SWARAS
from ..ir.models import IR, Cycle, IRWarning, Mode
from .notes import (NotesRequest, Problems, RawNote, TonicSpec, build_ir, declared_mode, make_tonic, match_header,
                    midi_to_hz, mode_suggestions, nearest_text, parse_positive_number, parse_tonic_text, resolve_mode)

GRAMMAR_HELP = """\
Sargam text, as Orin reads it.

Header lines (optional, one per line):
  tonic: D3            the pitch of Sa: a note name (D3, C#3) or a frequency (146.83 Hz)
  sruti: 2             or the kattai number instead (1 = C, 1.5 = C#, 2 = D ... 7 = B)
  raga: mohanam        lets you write bare letters (R G D) and flags notes outside the raga
  tala: adi            turns on bar-length checks
  tempo: 80            beats per minute (60 if left out)
  units_per_beat: 2    how many swaras fill one beat (2 if left out)

Notes:
  S R G M P D N        swaras. Capital and small letters mean the same
  R1 R2 R3  G1 G2 G3  M1 M2  D1 D2 D3  N1 N2 N3
                       the variant number. Required unless the raga settles it
  S'                   one octave up (S'' for two);  S.  one octave down.
                       A dot above or below the letter works too
  ,                    the note before lasts one unit longer;  ;  two units longer
  _                    a rest of one unit
  ( ... )              the notes inside go at double speed. Brackets can nest
  ~  /  \\              after a note: an oscillation, a slide up, a slide down
  |  ||                bar, end of cycle (checked only when a tala is set)
  spaces and -         ignored
  # ...                a comment

Example:
  tonic: D3
  raga: mohanam
  tala: adi
  S R2 G3 P D2 S' , , | D2 P G3~ R2 | S , , , ||

Notation differs between schools. This is a typing convention for Orin, not a rule
about how the music should be written. With no tonic line, Sa is assumed to be C3.
"""

HEADER_KEYS = ("tonic", "sruti", "raga", "tala", "tempo", "units_per_beat")
_KEY_ALIASES = {"shruti": "sruti", "ragam": "raga", "talam": "tala", "bpm": "tempo"}
# Kattai (sruti) number -> semitones above C. Octave 3 is assumed.
KATTAI = {1.0: 0, 1.5: 1, 2.0: 2, 2.5: 3, 3.0: 4, 4.0: 5, 4.5: 6, 5.0: 7, 5.5: 8, 6.0: 9, 6.5: 10, 7.0: 11}
VARIANTS = {"S": (), "P": (), "M": ("1", "2"), "R": ("1", "2", "3"), "G": ("1", "2", "3"),
            "D": ("1", "2", "3"), "N": ("1", "2", "3")}
DEFAULT_TEMPO = 60.0
DEFAULT_UNITS_PER_BEAT = 2
DEFAULT_SA_MIDI = 48.0  # C3
SA_OCTAVE = 3

DOT_ABOVE, DOT_BELOW = "̇", "̣"
_APOSTROPHES = {"’": "'", "ʼ": "'", "′": "'"}


@dataclass
class _Settings:
    tonic: Optional[TonicSpec] = None
    raga: Optional[ModeEntry] = None
    tala: Optional[RhythmTemplate] = None
    tempo: Optional[float] = None
    units_per_beat: int = DEFAULT_UNITS_PER_BEAT


@dataclass
class _Note:
    cents: float
    onset: Fraction  # units
    duration: Fraction
    ornament: Optional[str]


def _fmt(x: Fraction) -> str:
    return str(x.numerator) if x.denominator == 1 else f"{float(x):g}"


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------

def _tala_key(text: str) -> str:
    text = re.sub(r"\(.*?\)", "", text.lower())
    text = re.sub(r"\b(tala|talam|taal)\b", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _tala_names(meta: FormatMeta) -> dict[str, RhythmTemplate]:
    names: dict[str, RhythmTemplate] = {}
    for t in meta.rhythm_templates:
        for name in (t.id, re.sub(r"_\d+$", "", t.id), t.label):
            names.setdefault(_tala_key(name), t)
    return names


def find_tala(meta: FormatMeta, value: str) -> Optional[RhythmTemplate]:
    """A tala by id or label, ignoring case, spacing and the word "tala"."""
    return _tala_names(meta).get(_tala_key(value))


def _tala_suggestions(meta: FormatMeta, value: str) -> str:
    return nearest_text(_tala_key(value), {key: t.id for key, t in _tala_names(meta).items()})


def _read_headers(lines: list[str], meta: FormatMeta, problems: Problems) -> tuple[_Settings, set[int]]:
    settings = _Settings()
    header_lines: set[int] = set()
    sruti: Optional[TonicSpec] = None
    for index, raw in enumerate(lines):
        header = match_header(raw)
        if header is None:
            continue
        header_lines.add(index)
        line_no = index + 1
        key = _KEY_ALIASES.get(header.key, header.key)
        value, column, length = header.value, header.value_column, max(1, len(header.value))
        if key not in HEADER_KEYS:
            problems.add("E007", f"'{header.key}' is not a header Orin knows. Headers are: {', '.join(HEADER_KEYS)}.",
                         line_no, header.key_column, len(header.key))
        elif not value:
            problems.add("E007", f"The '{header.key}' header has no value after the colon.", line_no, column)
        elif key == "tonic":
            settings.tonic = parse_tonic_text(value)
            if settings.tonic is None:
                problems.add("E007", f"'{value}' is not a tonic Orin can read. Write a note name such as D3 or C#3, "
                                     "or a frequency such as 146.83 Hz. For a kattai number use 'sruti:'.",
                             line_no, column, length)
        elif key == "sruti":
            number = parse_positive_number(value, 0.5, 8.0)
            if number is not None and number in KATTAI:
                sruti = TonicSpec(pitch_class=float(KATTAI[number]))
            else:
                sruti = parse_tonic_text(value) if number is None else None
                if sruti is None:
                    problems.add("E007", f"'{value}' is not a kattai number. Use 1, 1.5, 2, 2.5, 3, 4, 4.5, 5, 5.5, "
                                         "6, 6.5 or 7 (1 = C, 2 = D, 5 = G).", line_no, column, length)
        elif key == "raga":
            found = resolve_mode(value, meta.id)
            if found is None or found[0].id != meta.id:
                problems.add("E008", f"'{value}' is not a raga in Orin's table. "
                                     f"{mode_suggestions(value, meta.id)}", line_no, column, length)
            else:
                settings.raga = found[1]
        elif key == "tala":
            settings.tala = find_tala(meta, value)
            if settings.tala is None:
                problems.add("E008", f"'{value}' is not a tala in Orin's table. "
                                     f"{_tala_suggestions(meta, value)}", line_no, column, length)
        elif key == "tempo":
            settings.tempo = parse_positive_number(value, 10.0, 480.0)
            if settings.tempo is None:
                problems.add("E007", f"'{value}' is not a tempo. Write beats per minute as a number from 10 to 480, "
                                     "such as 80.", line_no, column, length)
        elif key == "units_per_beat":
            units = parse_positive_number(value, 1, 16)
            if units is None or units != int(units):
                problems.add("E007", f"'{value}' is not a units-per-beat count. Write a whole number from 1 to 16, "
                                     "such as 2 or 4.", line_no, column, length)
            else:
                settings.units_per_beat = int(units)
    if settings.tonic is None:
        settings.tonic = sruti
    return settings, header_lines


# ---------------------------------------------------------------------------
# Bars against the tala
# ---------------------------------------------------------------------------

class _BarCheck:
    """"|" must fall on the tala's group boundaries and "||" must close a full cycle (E006).

    After a wrong bar the count is realigned to where the bar should have been, so one
    slip is reported once and not again at every later bar.
    """

    def __init__(self, tala: RhythmTemplate, units_per_beat: int):
        self.tala = tala
        self.units_per_beat = units_per_beat
        self.cycle = Fraction(tala.cycle_beats * units_per_beat)
        self.bounds: list[Fraction] = []
        total = 0
        for group in (tala.groups or (tala.cycle_beats,)):
            total += group * units_per_beat
            self.bounds.append(Fraction(total))
        self.origin = Fraction(0)  # where the current cycle count starts (moved when a wrong bar is realigned)
        self.prev = Fraction(0)  # position of the previous bar
        self.slip = Fraction(0)  # units the count was moved by inside the current cycle

    def _beats(self, units: Fraction) -> str:
        return f"{_fmt(units / self.units_per_beat)} beats of {self.units_per_beat} units"

    def _restart(self, pos: Fraction) -> None:
        self.origin, self.slip = pos, Fraction(0)

    def bar(self, pos: Fraction, double: bool, line: int, column: int, problems: Problems) -> None:
        rel = pos - self.origin
        on_cycle = rel % self.cycle == 0
        if pos == self.prev:  # an opening bar, or two bars in a row
            if on_cycle:
                self._restart(pos)
            return
        before = self.prev - self.origin  # the previous bar, counted from the cycle start
        self.prev = pos
        if double:
            if not on_cycle:
                target = max(1, round(rel / self.cycle)) * self.cycle
                found = rel - self.slip - (target - self.cycle)  # what was really written in this cycle
                problems.add("E006", f"The cycle that ends here has {_fmt(found)} units, but {self.tala.label} "
                                     f"needs {_fmt(self.cycle)} ({self._beats(self.cycle)}).", line, column, 2)
            self._restart(pos)
            return
        if on_cycle:
            self._restart(pos)
            return
        if rel % self.cycle in self.bounds:
            return
        k = int(rel // self.cycle)
        targets = [c * self.cycle + b for c in range(max(0, k - 1), k + 2) for b in self.bounds]
        targets = [t for t in targets if t > before] or [before + self.bounds[0]]
        target = min(targets, key=lambda t: (abs(t - rel), t))
        expected, found = target - before, rel - before
        problems.add("E006", f"This bar comes after {_fmt(found)} units, but {self.tala.label} expects "
                             f"{_fmt(expected)} here ({self._beats(expected)}).", line, column, 1)
        if target % self.cycle == 0:
            self._restart(pos)
        else:
            self.slip += target - rel
            self.origin = pos - target


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def _chars(line: str) -> list[tuple[str, int]]:
    """Characters with their 1-based column in the line as typed. Precomposed dotted
    letters are split into letter + combining dot, both at the original column."""
    out: list[tuple[str, int]] = []
    for i, ch in enumerate(line):
        for c in unicodedata.normalize("NFD", _APOSTROPHES.get(ch, ch)):
            out.append((c, i + 1))
    return out


def _raga_variants(raga: Optional[ModeEntry]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if raga is not None:
        for d in raga.degrees():
            if d.name in SWARAS:
                out.setdefault(d.name[0], []).append(d.name)
    return out


def _resolve(letter: str, digits: str, starred: bool, raga: Optional[ModeEntry], variants: dict[str, list[str]],
             line: int, column: int, length: int, problems: Problems) -> Optional[str]:
    """The swara name for a letter and an optional variant number, or None after an error."""
    allowed = VARIANTS[letter]
    if digits:
        if not allowed:
            problems.add("E003", f"'{letter}{digits}' is not a swara: {letter} has only one position, so it takes "
                                 f"no number. Write {letter}.", line, column, length)
            return None
        if digits not in allowed:
            problems.add("E003", f"'{letter}{digits}' is not a swara: {letter} can be "
                                 f"{', '.join(letter + v for v in allowed)}.", line, column, length)
            return None
        name = letter + digits
        if raga is not None and not starred and name not in variants.get(letter, []):
            problems.add("W101", f"{name} is not in {raga.label}. The note is kept as written.", line, column,
                         length, severity="warning")
        return name
    if not allowed:
        if raga is not None and not starred and letter not in variants:
            problems.add("W101", f"{letter} is not in {raga.label}. The note is kept as written.", line, column,
                         length, severity="warning")
        return letter
    options = ", ".join(letter + v for v in allowed[:-1]) + f" or {letter}{allowed[-1]}"
    if raga is None:
        problems.add("E002", f"'{letter}' needs a number ({options}) because no raga is set. Add the number, "
                             "or add a 'raga:' line.", line, column, length)
        return None
    in_raga = variants.get(letter, [])
    if len(in_raga) == 1:
        return in_raga[0]
    if not in_raga:
        problems.add("E002", f"'{letter}' needs a number ({options}): {raga.label} has no {letter} to take it "
                             "from.", line, column, length)
    else:
        problems.add("E002", f"'{letter}' needs a number: {raga.label} uses {' and '.join(in_raga)}, so say which.",
                     line, column, length)
    return None


def _scan(lines: list[str], header_lines: set[int], settings: _Settings,
          problems: Problems) -> tuple[list[_Note], Fraction, int]:
    """Read the note lines. Returns the notes, the total length in units and the deepest group nesting."""
    variants = _raga_variants(settings.raga)
    bars = _BarCheck(settings.tala, settings.units_per_beat) if settings.tala is not None else None
    notes: list[_Note] = []
    pos = Fraction(0)
    last: Optional[_Note] = None  # the note a comma would extend; None after a rest or at the start
    anything = False  # a note or a rest has been read
    max_depth = 0

    for index, raw in enumerate(lines):
        if index in header_lines:
            continue
        line_no = index + 1
        cs = _chars(raw)
        n = len(cs)
        open_groups: list[int] = []  # columns of unclosed "("
        i = 0
        while i < n:
            ch, col = cs[i]
            unit = Fraction(1, 2 ** len(open_groups))
            if ch.isspace() or ch == "-":
                i += 1
            elif ch == "#":
                break
            elif ch == "|":
                double = i + 1 < n and cs[i + 1][0] == "|"
                if bars is not None:
                    bars.bar(pos, double, line_no, col, problems)
                i += 2 if double else 1
            elif ch == "(":
                open_groups.append(col)
                max_depth = max(max_depth, len(open_groups))
                i += 1
            elif ch == ")":
                if open_groups:
                    open_groups.pop()
                else:
                    problems.add("E005", "This ')' closes a group that was never opened. Remove it, or add the "
                                         "'(' that starts the double-speed group.", line_no, col)
                i += 1
            elif ch in ",;":
                extra = unit * (2 if ch == ";" else 1)
                if last is not None:
                    last.duration += extra
                elif not anything:
                    problems.add("E004", f"This '{ch}' has no note before it to lengthen, so it was read as a rest.",
                                 line_no, col, severity="warning")
                pos += extra
                anything = True
                i += 1
            elif ch == "_":
                pos += unit
                last = None
                anything = True
                i += 1
            elif ch.upper() in VARIANTS and ch.isascii():
                letter = ch.upper()
                j = i + 1
                octave = 0
                digits = ""
                while j < n and cs[j][0] in (DOT_ABOVE, DOT_BELOW):
                    octave += 1 if cs[j][0] == DOT_ABOVE else -1
                    j += 1
                while j < n and cs[j][0].isascii() and cs[j][0].isdigit():
                    digits += cs[j][0]
                    j += 1
                while j < n and cs[j][0] in ("'", ".", DOT_ABOVE, DOT_BELOW):
                    octave += 1 if cs[j][0] in ("'", DOT_ABOVE) else -1
                    j += 1
                ornament: Optional[str] = None
                starred = False
                while j < n and cs[j][0] in "~/\\*":
                    if cs[j][0] == "*":
                        starred = True
                    elif ornament is None:
                        ornament = cs[j][0]
                    else:
                        break
                    j += 1
                length = cs[j - 1][1] - col + 1
                name = _resolve(letter, digits, starred, settings.raga, variants, line_no, col, length, problems)
                cents = (SWARAS[name][1] if name else 0.0) + 1200.0 * octave
                last = _Note(cents=cents, onset=pos, duration=unit, ornament=ornament)
                notes.append(last)
                pos += unit
                anything = True
                i = j
            else:
                j = i
                while j < n and not cs[j][0].isspace():
                    j += 1
                token = "".join(c for c, _ in cs[i:j])
                problems.add("E001", f"'{token}' is not something Orin can read here. Expected a swara "
                                     "(S R G M P D N, with a number where needed), a rest (_), a comma, "
                                     "a bar (|) or a bracket.", line_no, col, cs[j - 1][1] - col + 1)
                i = j
        for col in open_groups:
            problems.add("E005", "This '(' is never closed on its line. Add the matching ')'.", line_no, col)
    return notes, pos, max_depth


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_sargam(text: str, req: NotesRequest) -> IR:
    meta = req.meta if req.meta.note_names == "swara" else registry.get("carnatic").META
    problems = Problems()
    lines = text.splitlines()
    settings, header_lines = _read_headers(lines, meta, problems)
    if settings.raga is None and req.mode_id:
        found = resolve_mode(req.mode_id, meta.id)
        if found is not None and found[0].id == meta.id:
            settings.raga = found[1]

    notes, total_units, depth = _scan(lines, header_lines, settings, problems)
    if not notes and not problems.has_errors:
        problems.add("E000", "There are no notes here. Write swaras such as: S R2 G3 P D2 S'",
                     max(1, len(lines)), 1)
    problems.raise_if_errors()

    warnings: list[IRWarning] = problems.as_warnings()
    scale = 2 ** depth  # pulses per unit, so that the fastest group still lands on whole pulses
    subdivision = settings.units_per_beat * scale

    if req.tonic_hz is not None:
        tonic = make_tonic(req.tonic_hz, "user", 1.0)
    elif settings.tonic is not None:
        spec = settings.tonic
        hz = spec.hz if spec.hz is not None else midi_to_hz(12.0 * (SA_OCTAVE + 1) + (spec.pitch_class or 0.0))
        tonic = make_tonic(hz, "declared", 1.0)
    else:
        tonic = make_tonic(midi_to_hz(DEFAULT_SA_MIDI), "assumed", 0.2)
        warnings.append(IRWarning(code="tonic_assumed", stage="tonic",
                                  message="No tonic was given, so Sa was assumed to be C3. This is a guess. "
                                          "Add a line such as 'tonic: D3' or 'sruti: 2', or set Sa in the app."))

    if req.tempo_bpm is not None:
        bpm, tempo_confidence = req.tempo_bpm, 1.0
    elif settings.tempo is not None:
        bpm, tempo_confidence = settings.tempo, 1.0
    else:
        bpm, tempo_confidence = DEFAULT_TEMPO, 0.3
        warnings.append(IRWarning(code="tempo_assumed", stage="rhythm",
                                  message=f"No tempo was given, so {DEFAULT_TEMPO:g} beats per minute was assumed. "
                                          "Add a line such as 'tempo: 80' to set it."))

    pulse_s = 60.0 / (bpm * subdivision)
    tala = settings.tala
    cycle = Cycle(length_pulses=tala.cycle_beats * subdivision if tala else None, subdivision=subdivision,
                  label=tala.label if tala else None, confidence=1.0 if tala else 0.0,
                  template_id=tala.id if tala else None, pulse_s=pulse_s)
    mode = declared_mode(meta, settings.raga) if settings.raga is not None else Mode()
    raw = [RawNote(onset=int(n.onset * scale), duration=int(n.duration * scale), cents=n.cents, ornament=n.ornament)
           for n in notes]
    return build_ir(req, raw, tonic=tonic, mode=mode, bpm=bpm, tempo_confidence=tempo_confidence, cycle=cycle,
                    total_pulses=float(total_units * scale), warnings=warnings)
