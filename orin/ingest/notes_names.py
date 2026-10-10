"""Plain note-name text -> IR (spec 7.14): "D4 F#4:2 Ed4 r Bb3".

Pitches are absolute (scientific octaves, A4 = 440 Hz) and may carry quarter-tone, koron
and sori accidentals, so maqam and dastgah lines can be typed without a staff. The
accepted grammar is in ``GRAMMAR_HELP``.

Problem codes: E001 unknown token, E007 bad header, E008 unknown mode, E000 no notes.
"""
from __future__ import annotations

import re
from typing import Optional

from ..ir.models import IR, Cycle, IRWarning, Mode
from .notes import (ACCIDENTAL_CENTS, ACCIDENTAL_PATTERN, LETTER_SEMITONES, NotesRequest, Problems, RawNote,
                    TonicSpec, build_ir, choose_tonic, declared_mode, match_header, meter_cycle, mode_suggestions,
                    parse_meter, parse_positive_number, parse_tonic_text, resolve_mode, strip_comment)

GRAMMAR_HELP = """\
Note names, as Orin reads them.

Header lines (optional, one per line):
  tonic: D4            the home note: a note name (D4, Bb3, or just D) or a frequency (293.66 Hz)
  tempo: 100           beats per minute (100 if left out)
  meter: 4/4           the time signature
  units_per_beat: 2    how many units fill one beat (1 if left out)
  mode: rast           a mode Orin knows, by name

Notes:
  D4  F#4  Bb3         a letter A to G, an accidental if needed, and the octave (C4 is middle C)
  #  b  ##  bb         sharp, flat, double sharp, double flat
  +  d                 half-sharp, half-flat (read as 50 cents up or down):  F+4  Ed4
  #+  db               three-quarter sharp, three-quarter flat
  p  >                 koron (about 60 cents down) and sori (about 40 cents up):  Ep4  F>4
  D4:2                 a number after a colon is the length in units (1 if left out)
  r   r:2              a rest
  ~  /  \\              after a note: an oscillation, a slide up, a slide down:  D4:2~
  |  ||                bar lines, for reading only
  # ...                a comment (at the start of a line or after a space)

Example:
  tonic: C4
  tempo: 90
  C4 D4 Ed4 F4 G4:2 r F4 | Ed4 D4 C4:3 ||

With no tonic line Orin guesses one from the notes and says so. The cents for half-flats,
koron and sori are nominal: they name the note, they do not measure a tuning.
"""

HEADER_KEYS = ("tonic", "tempo", "meter", "units_per_beat", "mode")
_KEY_ALIASES = {"bpm": "tempo"}
DEFAULT_TEMPO = 100.0
DEFAULT_UNITS_PER_BEAT = 1

_TOKEN_RE = re.compile(r"\|\||\||[^\s|]+")
_NOTE_RE = re.compile(rf"^([A-Ga-g])({ACCIDENTAL_PATTERN})?(-?\d+)(?::(\d+))?([~/\\])?\*?$")
_REST_RE = re.compile(r"^[rR](?::(\d+))?$")
_HOW_TO_SET = "Add a line such as 'tonic: D4' to set it."


def _explain(token: str) -> str:
    """Say what is wrong with a token that is not a note, a rest or a bar."""
    example = "like D4, F#4:2 or Ed4"
    head, colon, tail = token.partition(":")
    if colon and (head in ("r", "R") or _NOTE_RE.match(head)) and not re.fullmatch(r"[1-9]\d*[~/\\]?\*?", tail):
        return (f"'{token}' has a length Orin cannot read. After the colon write a whole number of 1 or more, "
                "as in D4:2.")
    if re.fullmatch(rf"[A-Ga-g](?:{ACCIDENTAL_PATTERN})?(?::\d+)?[~/\\]?\*?", token):
        return f"'{token}' has no octave number. Write the octave after the note name, {example}."
    if re.match(r"^[A-Ga-g]", token):
        return (f"'{token}' is not a note Orin can read. Expected a letter A to G, then an accidental "
                f"(# b ## bb + d #+ db p >) if needed, then the octave, {example}.")
    return f"'{token}' is not a note. Expected a note name {example}, a rest (r) or a bar line (|)."


def parse_names(text: str, req: NotesRequest) -> IR:
    meta = req.meta
    problems = Problems()
    tonic_spec: Optional[TonicSpec] = None
    tempo: Optional[float] = None
    meter: Optional[tuple[int, int]] = None
    units_per_beat = DEFAULT_UNITS_PER_BEAT
    mode = Mode()
    notes: list[RawNote] = []
    pos = 0
    lines = text.splitlines()

    for index, raw in enumerate(lines):
        line_no = index + 1
        header = match_header(raw, min_key_length=2)
        if header is not None and (header.key in HEADER_KEYS or header.key in _KEY_ALIASES or len(header.key) >= 3):
            key = _KEY_ALIASES.get(header.key, header.key)
            value, column, length = header.value, header.value_column, max(1, len(header.value))
            if key not in HEADER_KEYS:
                problems.add("E007", f"'{header.key}' is not a header Orin knows. Headers are: "
                                     f"{', '.join(HEADER_KEYS)}.", line_no, header.key_column, len(header.key))
            elif not value:
                problems.add("E007", f"The '{header.key}' header has no value after the colon.", line_no, column)
            elif key == "tonic":
                tonic_spec = parse_tonic_text(value)
                if tonic_spec is None:
                    problems.add("E007", f"'{value}' is not a tonic Orin can read. Write a note name such as D4 or "
                                         "Bb3, or a frequency such as 293.66 Hz.", line_no, column, length)
            elif key == "tempo":
                tempo = parse_positive_number(value, 10.0, 480.0)
                if tempo is None:
                    problems.add("E007", f"'{value}' is not a tempo. Write beats per minute as a number from 10 to "
                                         "480, such as 100.", line_no, column, length)
            elif key == "meter":
                meter = parse_meter(value)
                if meter is None:
                    problems.add("E007", f"'{value}' is not a meter. Write it like 4/4, 3/4 or 6/8.",
                                 line_no, column, length)
            elif key == "units_per_beat":
                units = parse_positive_number(value, 1, 16)
                if units is None or units != int(units):
                    problems.add("E007", f"'{value}' is not a units-per-beat count. Write a whole number from 1 to "
                                         "16, such as 2 or 4.", line_no, column, length)
                else:
                    units_per_beat = int(units)
            elif key == "mode":
                found = resolve_mode(value, req.format_id)
                if found is None:
                    problems.add("E008", f"'{value}' is not a mode Orin knows. In {meta.display_name}: "
                                         f"{mode_suggestions(value, req.format_id)}", line_no, column, length)
                else:
                    mode = declared_mode(*found)
            continue

        for m in _TOKEN_RE.finditer(strip_comment(raw)):
            token, column = m.group(0), m.start() + 1
            if token in ("|", "||"):
                continue
            rest = _REST_RE.match(token)
            if rest and (rest.group(1) is None or int(rest.group(1)) > 0):
                pos += int(rest.group(1) or 1)
                continue
            note = _NOTE_RE.match(token)
            if note is None or (note.group(4) is not None and int(note.group(4)) == 0):
                problems.add("E001", _explain(token), line_no, column, len(token))
                continue
            letter, accidental, octave, units, ornament = note.groups()
            midi = (12.0 * (int(octave) + 1) + LETTER_SEMITONES[letter.upper()]
                    + ACCIDENTAL_CENTS.get(accidental or "", 0.0) / 100.0)
            if not 0.0 <= midi <= 127.0:
                problems.add("E001", f"'{token}' is outside the range Orin can use. Octaves run from -1 to 9; "
                                     "middle C is C4.", line_no, column, len(token))
                continue
            duration = int(units or 1)
            notes.append(RawNote(onset=pos, duration=duration, midi=midi, ornament=ornament))
            pos += duration

    if not notes and not problems.has_errors:
        problems.add("E000", "There are no notes here. Write note names such as: D4 E4 F#4:2", max(1, len(lines)), 1)
    problems.raise_if_errors()

    warnings: list[IRWarning] = problems.as_warnings()
    tonic = choose_tonic(req, notes, tonic_spec, _HOW_TO_SET, warnings)
    if req.tempo_bpm is not None:
        bpm, tempo_confidence = req.tempo_bpm, 1.0
    elif tempo is not None:
        bpm, tempo_confidence = tempo, 1.0
    else:
        bpm, tempo_confidence = DEFAULT_TEMPO, 0.3
        warnings.append(IRWarning(code="tempo_assumed", stage="rhythm",
                                  message=f"No tempo was given, so {DEFAULT_TEMPO:g} beats per minute was assumed. "
                                          "Add a line such as 'tempo: 90' to set it."))

    pulse_s = 60.0 / (bpm * units_per_beat)
    if meter is not None:
        template = meta.template(f"{meter[0]}_{meter[1]}")
        # One beat is the meter's own beat: the template's when the format has the meter
        # (a dotted quarter in 6/8), else the note value under the slash.
        beats = template.cycle_beats if template else meter[0]
        cycle = meter_cycle(meta, meter[0], meter[1], units_per_beat, beats * units_per_beat, pulse_s)
    else:
        cycle = Cycle(subdivision=units_per_beat, pulse_s=pulse_s)
    return build_ir(req, notes, tonic=tonic, mode=mode, bpm=bpm, tempo_confidence=tempo_confidence, cycle=cycle,
                    total_pulses=float(pos), warnings=warnings)
