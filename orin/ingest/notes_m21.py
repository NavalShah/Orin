"""ABC and MusicXML -> IR, read with music21 (spec 7.14).

Orin keeps one melody line: the first part that has notes, and the top note of any
chord (marked ``guessed``, with one warning). Time is counted in sixteenths (4 pulses
per quarter note). music21 is imported inside the functions because it is slow to load.

Whatever music21 raises is turned into one ``NotesParseError`` with the best line that
can be found: the XML parser's own position for MusicXML, and for ABC the first tune
line that fails when read on its own.
"""
from __future__ import annotations

import io
import re
import zipfile
from typing import Any, Optional, Union
from xml.etree import ElementTree

from ..errors import NotesParseError, OrinError
from ..formats.base import FormatMeta
from ..ir.models import IR, Cycle, IRWarning, Mode
from ..ir.validate import IRInvalid
from .notes import (NotesRequest, RawNote, TonicSpec, build_ir, choose_tonic, declared_mode, fail, meter_cycle,
                    monophonic)

SUBDIVISION = 4  # pulses per quarter note
DEFAULT_TEMPO = 100.0
MAX_XML_BYTES = 20 * 1024 * 1024
_ABC_PROBE_LINES = 300


def _short(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:160].rstrip(". ")


# ---------------------------------------------------------------------------
# Shared: a music21 stream -> IR
# ---------------------------------------------------------------------------

def _first_part(score: Any, warnings: list[IRWarning]) -> Any:
    from music21 import stream

    if isinstance(score, stream.Opus):
        scores = list(score.scores)
        if not scores:
            return score
        if len(scores) > 1:
            warnings.append(IRWarning(code="first_tune_only", stage="ingest",
                                      message=f"The file holds {len(scores)} tunes. Only the first was read."))
        score = scores[0]
    parts = list(getattr(score, "parts", []))
    if not parts:
        return score
    with_notes = [p for p in parts if len(p.flatten().notes) > 0]
    if len(with_notes) > 1:
        warnings.append(IRWarning(code="first_part_only", stage="ingest",
                                  message=f"The file has {len(with_notes)} parts with notes. Orin reads one melody "
                                          "line, so only the first was kept."))
    return with_notes[0] if with_notes else parts[0]


def _declared_key(flat: Any, meta: FormatMeta, warnings: list[IRWarning]) -> tuple[Optional[TonicSpec], float, Mode]:
    """Tonic (and mode, when named) from the key signature. A bare signature is read as major, with less trust."""
    from music21 import key

    signatures = list(flat.getElementsByClass(key.KeySignature))
    if not signatures or signatures[0].sharps is None:
        return None, 1.0, Mode()
    ks = signatures[0]
    if isinstance(ks, key.Key):
        entry = meta.mode(ks.mode) if ks.mode else None
        mode = declared_mode(meta, entry) if entry is not None else Mode()
        return TonicSpec(pitch_class=float(ks.tonic.pitchClass)), 1.0, mode
    major, minor = ks.asKey("major"), ks.asKey("minor")
    count = abs(ks.sharps)
    sign = ("sharp" if ks.sharps > 0 else "flat") + ("s" if count > 1 else "")
    what = "no sharps or flats" if count == 0 else f"{count} {sign}"
    warnings.append(IRWarning(
        code="key_mode_assumed", stage="tonic",
        message=f"The key signature ({what}) does not say major or minor. Orin took "
                f"{major.tonic.name.replace('-', 'b')} as the tonic; it could also be "
                f"{minor.tonic.name.replace('-', 'b')}. Set the tonic to change it."))
    return TonicSpec(pitch_class=float(major.tonic.pitchClass)), 0.6, Mode()


def _from_stream(score: Any, req: NotesRequest, how_to_set: str, first_line: int) -> IR:
    from music21 import chord, harmony, meter, note, stream, tempo

    meta = req.meta
    warnings: list[IRWarning] = []
    part = _first_part(score, warnings)
    flat = part.stripTies().flatten()

    notes: list[RawNote] = []
    chords = graces = False
    for el in flat.notes:
        if isinstance(el, harmony.Harmony):  # a chord symbol is a label, not a played note
            continue
        length = float(el.duration.quarterLength)
        if el.duration.isGrace or length <= 0:
            graces = True
            continue
        if isinstance(el, chord.Chord):
            if not el.pitches:
                continue
            notes.append(RawNote(onset=float(el.offset) * SUBDIVISION, duration=length * SUBDIVISION,
                                 midi=max(float(p.ps) for p in el.pitches), guessed=len(el.pitches) > 1))
            chords = chords or len(el.pitches) > 1
        elif isinstance(el, note.Note):
            notes.append(RawNote(onset=float(el.offset) * SUBDIVISION, duration=length * SUBDIVISION,
                                 midi=float(el.pitch.ps)))
    notes, reduced = monophonic(notes)
    if not notes:
        raise fail("No notes were found in this file. Orin reads the first part that has pitched notes.",
                   code="E000", line=first_line)
    if chords or reduced:
        warnings.append(IRWarning(code="top_line_only", stage="ingest",
                                  message="The file has chords or more than one voice. Orin reads one melody line, "
                                          "so only the top note was kept. Those notes are marked as guesses."))
    if graces:
        warnings.append(IRWarning(code="grace_notes_skipped", stage="ingest",
                                  message="Grace notes were left out: they have no length of their own."))

    marks = [m for m in flat.getElementsByClass(tempo.MetronomeMark) if m.getQuarterBPM()]
    if not marks and hasattr(score, "flatten"):
        marks = [m for m in score.flatten().getElementsByClass(tempo.MetronomeMark) if m.getQuarterBPM()]
    if req.tempo_bpm is not None:
        bpm, tempo_confidence = req.tempo_bpm, 1.0
    elif marks:
        bpm = float(marks[0].getQuarterBPM())
        tempo_confidence = 0.5 if getattr(marks[0], "numberImplicit", False) else 1.0
        if len({round(float(m.getQuarterBPM()), 2) for m in marks}) > 1:
            warnings.append(IRWarning(code="tempo_changes_ignored", stage="rhythm",
                                      message=f"The tempo changes during the piece. Orin kept the first, {bpm:g} "
                                              "quarter notes per minute."))
    else:
        bpm, tempo_confidence = DEFAULT_TEMPO, 0.3
        warnings.append(IRWarning(code="tempo_assumed", stage="rhythm",
                                  message=f"The file gives no tempo, so {DEFAULT_TEMPO:g} quarter notes per minute "
                                          "was assumed."))
    pulse_s = 60.0 / (bpm * SUBDIVISION)

    total_pulses = float(flat.highestTime) * SUBDIVISION
    signatures = list(flat.getElementsByClass(meter.TimeSignature))
    if signatures:
        ts = signatures[0]
        cycle = meter_cycle(meta, int(ts.numerator), int(ts.denominator), SUBDIVISION,
                            float(ts.barDuration.quarterLength) * SUBDIVISION, pulse_s)
        measures = list(part.getElementsByClass(stream.Measure))
        pickup = float(getattr(measures[0], "paddingLeft", 0.0) or 0.0) * SUBDIVISION if measures else 0.0
        if pickup > 0 and cycle.length_pulses:
            # A pickup bar. Pulse 0 has to be a bar start, so the pulses move up by the missing
            # part of the bar; the seconds stay as they are, with the first note where it was.
            for n in notes:
                n.onset_s, n.offset_s = n.onset * pulse_s, (n.onset + n.duration) * pulse_s
                n.onset += pickup
    else:
        cycle = Cycle(subdivision=SUBDIVISION, pulse_s=pulse_s)

    declared, declared_confidence, mode = _declared_key(flat, meta, warnings)
    tonic = choose_tonic(req, notes, declared, how_to_set, warnings, declared_confidence)
    return build_ir(req, notes, tonic=tonic, mode=mode, bpm=bpm, tempo_confidence=tempo_confidence, cycle=cycle,
                    total_pulses=total_pulses, warnings=warnings, duration_s=total_pulses * pulse_s)


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------

_ABC_FIELD = re.compile(r"^([A-Za-z]):")
_ABC_QUARTER_TONE = re.compile(r"[\^_]/")


def _prepare_abc(text: str) -> str:
    """ABC's default note length is an eighth when neither L: nor M: is given; music21 wants it said."""
    lines = text.splitlines()
    fields = {m.group(1) for m in (_ABC_FIELD.match(line) for line in lines) if m}
    if "L" in fields or "M" in fields:
        return text
    for i, line in enumerate(lines):
        if line.startswith("K:"):
            return "\n".join(lines[:i] + ["L:1/8"] + lines[i:])
    return "L:1/8\n" + text


def _abc_reads(text: str) -> bool:
    from music21 import converter

    try:
        converter.parseData(_prepare_abc(text), format="abc")
        return True
    except Exception:
        return False


def _abc_error_line(text: str) -> int:
    """The first line that fails on its own: a header line whose removal fixes the header,
    else a tune line that fails with the header in front of it."""
    lines = text.splitlines()
    k = next((i for i, line in enumerate(lines) if line.startswith("K:")), None)
    if k is None:
        k = next((i for i, line in enumerate(lines) if line.strip() and not _ABC_FIELD.match(line)), len(lines)) - 1
    header = lines[: k + 1]
    if header and not _abc_reads("\n".join(header + ["C"])):
        for i in range(len(header)):
            if header[i].strip() and _abc_reads("\n".join(header[:i] + header[i + 1:] + ["C"])):
                return i + 1
        return max(1, k + 1)
    for i in range(k + 1, min(len(lines), k + 1 + _ABC_PROBE_LINES)):
        if lines[i].strip() and not _abc_reads("\n".join(header + [lines[i]])):
            return i + 1
    return min(len(lines), k + 2) if lines else 1


def parse_abc(text: str, req: NotesRequest) -> IR:
    from music21 import converter

    lines = text.splitlines()
    first_body = next((i + 1 for i, line in enumerate(lines)
                       if line.strip() and not _ABC_FIELD.match(line) and not line.startswith("%")), 1)
    # music21 misreads ABC quarter-tone accidentals without complaint, so stop before it does.
    for i, line in enumerate(lines):
        if _ABC_FIELD.match(line) or line.startswith("%"):
            continue
        quarter = _ABC_QUARTER_TONE.search(re.sub(r'"[^"]*"', lambda m: " " * len(m.group(0)), line.split("%")[0]))
        if quarter:
            raise fail("Quarter-tone accidentals (^/ and _/) in ABC are not read by Orin. Type the tune as note "
                       "names (Ed4 for E half-flat) or use MusicXML.", line=i + 1, column=quarter.start() + 1,
                       length=2)
    try:
        score = converter.parseData(_prepare_abc(text), format="abc")
        return _from_stream(score, req, "Add a key line such as 'K:D', or set the tonic in the app.", first_body)
    except (OrinError, IRInvalid):
        raise
    except Exception as exc:
        line = _abc_error_line(text)
        length = len(lines[line - 1]) if 0 < line <= len(lines) else 1
        raise fail(f"This line of ABC could not be read ({_short(exc)}). Check for an unclosed bracket or a "
                   "header field with a value ABC does not allow.", line=line, length=length) from exc


# ---------------------------------------------------------------------------
# MusicXML (.musicxml, .xml and compressed .mxl)
# ---------------------------------------------------------------------------

def _unzip_mxl(data: bytes) -> bytes:
    """The score file inside a compressed MusicXML archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            target: Optional[str] = None
            if "META-INF/container.xml" in names:
                container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
                for el in container.iter():
                    if el.tag.rsplit("}", 1)[-1] == "rootfile" and el.get("full-path") in names:
                        target = el.get("full-path")
                        break
            if target is None:
                target = next((n for n in names if not n.startswith("META-INF/")
                               and n.lower().endswith((".musicxml", ".xml"))), None)
            if target is None:
                raise fail("This zip file has no MusicXML score inside it. A compressed MusicXML file (.mxl) "
                           "holds one .musicxml or .xml file.")
            if archive.getinfo(target).file_size > MAX_XML_BYTES:
                raise fail("The score inside this compressed MusicXML file is too large for Orin to read.")
            return archive.read(target)
    except NotesParseError:
        raise
    except Exception as exc:
        raise fail(f"This compressed MusicXML file (.mxl) could not be opened ({_short(exc)}).") from exc


def parse_musicxml(data: Union[str, bytes], req: NotesRequest) -> IR:
    if isinstance(data, bytes) and data[:2] == b"PK":
        data = _unzip_mxl(data)
    if isinstance(data, str):
        data = data.lstrip("﻿")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        line, column = getattr(exc, "position", (1, 0))
        raise fail(f"The MusicXML is not well-formed here ({_short(exc)}). A tag is probably unclosed or "
                   "misspelled.", line=line, column=column + 1) from exc
    except Exception as exc:
        raise fail(f"This file could not be read as MusicXML ({_short(exc)}).") from exc
    tag = root.tag.rsplit("}", 1)[-1]
    if tag == "score-timewise":
        raise fail("This MusicXML file is laid out 'timewise', which Orin cannot read. Export it as "
                   "'score-partwise', the usual layout.")
    if tag != "score-partwise":
        raise fail(f"This XML is not a MusicXML score: it starts with <{tag}>, and a score starts with "
                   "<score-partwise>.")

    from music21 import converter

    try:
        score = converter.parseData(data, format="musicxml")
        return _from_stream(score, req, "Set the tonic in the app to change it.", 1)
    except (OrinError, IRInvalid):
        raise
    except Exception as exc:
        raise fail(f"This MusicXML file could not be read ({_short(exc)}).") from exc
