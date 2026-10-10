"""Typed and file notes -> IR (spec 7.14).

``parse_notes`` is the one entry point: it works out the syntax (sargam text, note-name
text, ABC, MusicXML or MIDI), hands the input to that parser and returns a validated IR.
The parsers live in ``notes_sargam``, ``notes_names``, ``notes_m21`` and ``notes_midi``;
this module also holds what they share: pitch arithmetic, tonic inference, header lines
and the final IR assembly.

Parsed notes are exact, so every event has confidence 1.0. ``guessed`` is set on an event
only where the parser had to choose (the top note of a chord, an overlapping MIDI note).
Bad text raises ``NotesParseError`` with every problem located by line and column.
"""
from __future__ import annotations

import hashlib
import math
import re
import statistics
import time
import warnings as _warnings
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Optional, Sequence, Union

from ..errors import IngestError, NotesParseError, ParseProblem
from ..formats import registry
from ..formats.base import FormatMeta, ModeEntry, RhythmTemplate
from ..ir.models import (IR, Analysis, Cycle, IRWarning, Meta, Mode, NoteEvent, Ornament, Source, StageLog,
                         Tempo, Tonic)
from ..ir.validate import assert_valid
from .sniff import decode_text, extension, sniff, text_subtype

SYNTAXES = ("sargam", "note_names", "abc", "musicxml", "midi")
_SYNTAX_ALIASES = {"names": "note_names", "note-names": "note_names", "notes": "note_names", "xml": "musicxml",
                   "mxl": "musicxml", "mid": "midi", "swara": "sargam", "text": "auto", "": "auto"}
DEFAULT_FORMAT = {"sargam": "carnatic"}
FALLBACK_FORMAT = "western_classical"

A4_HZ = 440.0
PC_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
LETTER_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
# Accidentals of the note-name syntax, in cents. Koron and sori are nominal (about -60 and +40).
ACCIDENTAL_CENTS = {"##": 200.0, "bb": -200.0, "#+": 150.0, "db": -150.0, "#": 100.0, "b": -100.0,
                    "+": 50.0, "d": -50.0, "p": -60.0, ">": 40.0}
ACCIDENTAL_PATTERN = r"##|bb|#\+|db|#|b|\+|d|p|>"  # longest first

# Nominal sizes for a notated ornament: the mark says it exists, not how big it is.
OSCILLATION_CENTS = 60.0
SLIDE_CENTS = 150.0


# ---------------------------------------------------------------------------
# Pitch arithmetic
# ---------------------------------------------------------------------------

def midi_to_hz(midi: float) -> float:
    return A4_HZ * 2.0 ** ((midi - 69.0) / 12.0)


def hz_to_midi(hz: float) -> float:
    return 69.0 + 12.0 * math.log2(hz / A4_HZ)


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def midi_label(midi: int) -> str:
    """60 -> "C4" (scientific pitch, A4 = 440 Hz)."""
    return f"{PC_NAMES[midi % 12]}{midi // 12 - 1}"


def make_tonic(hz: float, method: str, confidence: float) -> Tonic:
    midi = hz_to_midi(hz)
    nearest = round_half_up(midi)
    return Tonic(hz=round(hz, 4), cents_ref_hz=round(hz, 4), confidence=confidence, method=method,
                 tuning_offset_cents=round((midi - nearest) * 100.0, 2), nearest_note=midi_label(nearest))


# ---------------------------------------------------------------------------
# What the parsers hand back
# ---------------------------------------------------------------------------

@dataclass
class RawNote:
    """One parsed note. Give ``cents`` (relative to the tonic) or ``midi`` (absolute, may be fractional)."""

    onset: float  # pulses
    duration: float  # pulses
    cents: Optional[float] = None
    midi: Optional[float] = None
    ornament: Optional[str] = None  # "~" | "/" | "\\"
    guessed: bool = False
    velocity: Optional[int] = None
    onset_s: Optional[float] = None  # only when time does not follow one tempo (a MIDI tempo map)
    offset_s: Optional[float] = None


@dataclass
class NotesRequest:
    """The caller's choices, passed to every parser."""

    syntax: str
    format_id: str
    filename: Optional[str] = None
    tonic_hz: Optional[float] = None
    mode_id: Optional[str] = None
    tempo_bpm: Optional[float] = None
    sha256: Optional[str] = None
    started: float = field(default_factory=time.perf_counter)

    @property
    def meta(self) -> FormatMeta:
        return registry.get(self.format_id).META


@dataclass
class TonicSpec:
    """A declared tonic: a frequency, or a pitch class whose octave the parser still has to pick."""

    hz: Optional[float] = None
    pitch_class: Optional[float] = None  # semitones above C, may be fractional


class Problems:
    """Collects every problem in a text so the user sees them all at once."""

    def __init__(self) -> None:
        self.items: list[ParseProblem] = []

    def add(self, code: str, message: str, line: int, column: int, length: int = 1,
            severity: str = "error") -> None:
        self.items.append(ParseProblem(code=code, message=message, line=max(1, line), column=max(1, column),
                                       length=max(1, length), severity=severity))  # type: ignore[arg-type]

    @property
    def has_errors(self) -> bool:
        return any(p.severity == "error" for p in self.items)

    def raise_if_errors(self) -> None:
        if self.has_errors:
            raise NotesParseError(sorted(self.items, key=lambda p: (p.line, p.column)))

    def as_warnings(self) -> list[IRWarning]:
        return [IRWarning(code=p.code, message=f"Line {p.line}, column {p.column}: {p.message}", stage="ingest")
                for p in sorted(self.items, key=lambda p: (p.line, p.column)) if p.severity == "warning"]


def fail(message: str, *, code: str = "E010", line: int = 1, column: int = 1, length: int = 1) -> NotesParseError:
    """A NotesParseError with a single problem (file formats that have no useful columns)."""
    return NotesParseError([ParseProblem(code=code, message=message, line=max(1, line), column=max(1, column),
                                         length=max(1, length))])


# ---------------------------------------------------------------------------
# Header lines shared by the two text syntaxes
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^(\s*)([A-Za-z_]+)(\s*:\s*)(.*)$")
_COMMENT_RE = re.compile(r"(^|\s)#")
_PITCH_RE = re.compile(rf"^([A-Ga-g])({ACCIDENTAL_PATTERN})?(-?\d+)?$")
_HZ_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:hz)?$", re.I)
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")


def strip_comment(line: str) -> str:
    """Cut a comment that starts at the line start or after a space, so "C#3" and "F#4" survive."""
    m = _COMMENT_RE.search(line)
    return line[: m.start() + len(m.group(1))] if m else line


@dataclass
class Header:
    key: str
    key_column: int
    value: str
    value_column: int


def match_header(line: str, min_key_length: int = 1) -> Optional[Header]:
    """``KEY: VALUE`` at the start of a line (comment already allowed after the value)."""
    m = _HEADER_RE.match(strip_comment(line))
    if not m or len(m.group(2)) < min_key_length:
        return None
    value = m.group(4).rstrip()
    return Header(key=m.group(2).lower(), key_column=len(m.group(1)) + 1, value=value,
                  value_column=len(m.group(1)) + len(m.group(2)) + len(m.group(3)) + 1)


def parse_pitch(text: str) -> Optional[tuple[float, Optional[int]]]:
    """"F#4" -> (6.0, 4); "Ed" -> (3.5, None). Semitones above C (may be fractional), octave."""
    m = _PITCH_RE.match(text.strip().replace("♯", "#").replace("♭", "b"))
    if not m:
        return None
    semis = LETTER_SEMITONES[m.group(1).upper()] + ACCIDENTAL_CENTS.get(m.group(2) or "", 0.0) / 100.0
    return semis, (int(m.group(3)) if m.group(3) is not None else None)


def parse_tonic_text(text: str) -> Optional[TonicSpec]:
    """A note name ("D3", "C#3", "D") or a frequency ("146.83", "146.83 Hz"). None if neither."""
    text = text.strip()
    hz = _HZ_RE.match(text)
    if hz:
        value = float(hz.group(1))
        return TonicSpec(hz=value) if 20.0 <= value <= 5000.0 else None
    pitch = parse_pitch(text)
    if pitch is None:
        return None
    semis, octave = pitch
    if octave is None:
        return TonicSpec(pitch_class=semis)
    midi = 12.0 * (octave + 1) + semis
    return TonicSpec(hz=midi_to_hz(midi)) if 0.0 <= midi <= 127.0 else None


def parse_positive_number(text: str, lo: float, hi: float) -> Optional[float]:
    text = text.strip()
    if not _NUMBER_RE.match(text):
        return None
    value = float(text)
    return value if lo <= value <= hi else None


def parse_meter(text: str) -> Optional[tuple[int, int]]:
    m = re.match(r"^(\d{1,2})\s*/\s*(\d{1,2})$", text.strip())
    if not m:
        return None
    num, den = int(m.group(1)), int(m.group(2))
    return (num, den) if num > 0 and den in (1, 2, 4, 8, 16, 32) else None


# ---------------------------------------------------------------------------
# Modes, meters
# ---------------------------------------------------------------------------

def _mode_keys(ref: str) -> list[str]:
    ref = ref.strip()
    keys = [ref, ref.lower(), re.sub(r"[\s\-]+", "_", ref.lower()), re.sub(r"[\s_\-]+", " ", ref.lower())]
    return list(dict.fromkeys(k for k in keys if k))


def resolve_mode(ref: str, format_id: str) -> Optional[tuple[FormatMeta, ModeEntry]]:
    """A mode by id, label or alias: in ``format_id`` first, then in any format."""
    meta = registry.get(format_id).META
    for key in _mode_keys(ref):
        entry = meta.mode(key)
        if entry is not None:
            return meta, entry
    for key in _mode_keys(ref):
        found = registry.find_mode(key, format_id)
        if found is not None:
            return found
    return None


def nearest_text(wanted: str, names: dict[str, str]) -> str:
    """"Closest: a, b." for the nearest ids, or the whole list when nothing is close.

    ``names`` maps every spelling a user might type (lower case) to the id it stands for.
    """
    close = get_close_matches(wanted.strip().lower(), list(names), n=3, cutoff=0.5)
    if close:
        return f"Closest: {', '.join(dict.fromkeys(names[c] for c in close))}."
    return f"Orin knows: {', '.join(dict.fromkeys(names.values()))}."


def mode_suggestions(ref: str, format_id: str) -> str:
    """The nearest modes of the format, as a sentence for an error message."""
    names: dict[str, str] = {}
    for m in registry.get(format_id).META.mode_table:
        for name in (m.id, m.label, *m.aliases):
            names.setdefault(name.lower(), m.id)
    return nearest_text(ref, names)


def declared_mode(meta: FormatMeta, entry: ModeEntry) -> Mode:
    return Mode(label=entry.label, label_system=meta.id, mode_id=entry.id,
                allowed_pitches_cents=entry.pitch_set(), confidence=1.0, user_selected=True)


def meter_template(meta: FormatMeta, num: int, den: int) -> Optional[RhythmTemplate]:
    return meta.template(f"{num}_{den}")


def meter_cycle(meta: FormatMeta, num: int, den: int, subdivision: int, length_pulses: Optional[float],
                pulse_s: float) -> Cycle:
    """The Cycle for a declared meter. The template id is set only when the format has that meter."""
    template = meter_template(meta, num, den)
    whole = length_pulses is not None and abs(length_pulses - round(length_pulses)) < 1e-9 and length_pulses > 0
    return Cycle(length_pulses=int(round(length_pulses)) if whole else None, subdivision=subdivision,
                 label=template.label if template else f"{num}/{den}", confidence=1.0,
                 template_id=template.id if template else None, pulse_s=pulse_s)


# ---------------------------------------------------------------------------
# Tonic
# ---------------------------------------------------------------------------

def tonic_midi_below(pitch_class: float, midis: Sequence[float]) -> float:
    """The pitch class in the octave at or just below the median pitch."""
    median = statistics.median(midis) if midis else 60.0
    return pitch_class + 12.0 * math.floor((median - pitch_class) / 12.0 + 1e-9)


def infer_tonic(pitches: Sequence[tuple[float, float]], how_to_set: str) -> tuple[Tonic, IRWarning]:
    """Guess a tonic for notes that came with none. ``pitches`` is (midi, duration) in time order.

    Three notes or more: the root of the best Krumhansl key-profile match
    (``pipeline.analyze.estimate_key``) on the notes rounded to semitones. Fewer: the first note.
    Either way the confidence is low and a warning says so.
    """
    midis = [p for p, _ in pitches]
    key = None
    if len(pitches) >= 3:
        try:
            from pipeline.analyze import NAMES, estimate_key

            notes = [{"midi": round_half_up(p), "onset": 0.0, "offset": max(float(d), 1e-6)} for p, d in pitches]
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore")
                key = estimate_key(notes)
            if key is not None and not (math.isfinite(key["correlation"]) and math.isfinite(key["margin"])):
                key = None
        except Exception:  # the profile matcher is optional here; fall back to the first note
            key = None
    if key is not None:
        midi = tonic_midi_below(float(NAMES.index(key["tonic"])), midis)
        confidence = round(max(0.1, min(0.6, 0.25 + 2.0 * float(key["margin"]))), 2)
        tonic = make_tonic(midi_to_hz(midi), "key_profile", confidence)
        why = f"the closest key-profile match ({key['tonic']} {key['mode']})"
    else:
        tonic = make_tonic(midi_to_hz(midis[0]), "assumed", 0.2)
        why = "the first note"
    warning = IRWarning(code="tonic_assumed", stage="tonic",
                        message=f"No tonic was given, so Orin assumed {tonic.nearest_note} from {why}. "
                                f"This is a guess. {how_to_set}")
    return tonic, warning


def choose_tonic(req: NotesRequest, notes: Sequence[RawNote], declared: Optional[TonicSpec],
                 how_to_set: str, warnings: list[IRWarning], declared_confidence: float = 1.0) -> Tonic:
    """User override, else the declared tonic, else an inferred one (absolute-pitch syntaxes)."""
    if req.tonic_hz is not None:
        return make_tonic(req.tonic_hz, "user", 1.0)
    midis = [n.midi for n in notes if n.midi is not None]
    if declared is not None and declared.hz is not None:
        return make_tonic(declared.hz, "declared", declared_confidence)
    if declared is not None and declared.pitch_class is not None:
        return make_tonic(midi_to_hz(tonic_midi_below(declared.pitch_class, midis)), "declared", declared_confidence)
    tonic, warning = infer_tonic([(n.midi, n.duration) for n in notes if n.midi is not None], how_to_set)
    warnings.append(warning)
    return tonic


# ---------------------------------------------------------------------------
# Making a line monophonic (MusicXML voices, MIDI)
# ---------------------------------------------------------------------------

def monophonic(notes: Sequence[RawNote]) -> tuple[list[RawNote], bool]:
    """Keep one note at a time: the highest when notes sound together.

    A short legato overlap (under a quarter of either note) is not harmony; the earlier
    note is simply ended where the next begins. Returns the notes and whether anything
    that really overlapped was dropped or cut (those notes are marked ``guessed``).
    """
    out: list[RawNote] = []
    reduced = False
    for n in sorted(notes, key=lambda x: (x.onset, -(x.midi or 0.0))):
        if n.duration <= 0:
            continue
        cur = out[-1] if out else None
        if cur is None or n.onset >= cur.onset + cur.duration - 1e-9:
            out.append(n)
            continue
        overlap = cur.onset + cur.duration - n.onset
        legato = n.onset > cur.onset and overlap <= 0.25 * min(cur.duration, n.duration)
        if legato:
            cur.duration = n.onset - cur.onset
            out.append(n)
        elif n.onset > cur.onset and (n.midi or 0.0) > (cur.midi or 0.0):
            cur.duration = n.onset - cur.onset
            cur.guessed = n.guessed = reduced = True
            out.append(n)
        else:
            cur.guessed = reduced = True
    return out, reduced


# ---------------------------------------------------------------------------
# IR assembly
# ---------------------------------------------------------------------------

def ornament_for(mark: Optional[str]) -> Ornament:
    if mark == "~":
        return Ornament(kind="oscillation", amplitude_cents=OSCILLATION_CENTS, position="whole", coverage=1.0,
                        confidence=0.5)
    if mark in ("/", "\\"):
        return Ornament(kind="glide", amplitude_cents=SLIDE_CENTS, shape="up" if mark == "/" else "down",
                        position="start", coverage=0.3, confidence=0.5)
    return Ornament()


def apply_mode_override(req: NotesRequest, mode: Mode, warnings: list[IRWarning]) -> Mode:
    """``mode_id`` passed by the caller wins over a mode named in the text."""
    if not req.mode_id:
        return mode
    found = resolve_mode(req.mode_id, req.format_id)
    if found is None:
        warnings.append(IRWarning(code="mode_unknown", stage="mode",
                                  message=f"'{req.mode_id}' is not a mode Orin knows, so it was not applied. "
                                          f"{mode_suggestions(req.mode_id, req.format_id)}"))
        return mode
    return declared_mode(*found)


def build_ir(req: NotesRequest, notes: Sequence[RawNote], *, tonic: Tonic, mode: Mode, bpm: float,
             tempo_confidence: float, cycle: Cycle, total_pulses: float, warnings: Sequence[IRWarning],
             duration_s: Optional[float] = None) -> IR:
    """Turn parsed notes into a validated IR. ``cycle`` must carry ``subdivision`` and ``pulse_s``.

    Pulse 0 must be the start of a cycle when a cycle is declared: the conversion engine
    reads ``onset_pulse`` as a position in the cycle.
    """
    pulse_s = cycle.pulse_s or 60.0 / (bpm * cycle.subdivision)
    tonic_midi = hz_to_midi(tonic.hz)
    warnings = list(warnings)
    mode = apply_mode_override(req, mode, warnings)
    events = []
    for i, n in enumerate(notes, 1):
        cents = n.cents if n.cents is not None else ((n.midi or 0.0) - tonic_midi) * 100.0
        cents = round(cents, 3) + 0.0  # + 0.0 turns -0.0 into 0.0
        onset_s = n.onset_s if n.onset_s is not None else n.onset * pulse_s
        offset_s = n.offset_s if n.offset_s is not None else (n.onset + n.duration) * pulse_s
        events.append(NoteEvent(
            id=f"e{i}", onset_s=round(onset_s, 9), offset_s=round(offset_s, 9),
            onset_pulse=round(float(n.onset), 6), duration_pulses=round(float(n.duration), 6),
            pitch_cents=cents, pitch_cents_median=cents, pitch_cents_min=cents, pitch_cents_max=cents,
            velocity=n.velocity, ornament=ornament_for(n.ornament), confidence=1.0, guessed=n.guessed))
    last = events[-1].offset_s if events else 0.0
    if cycle.length_pulses and events:
        # Pulse 0 is a cycle start, so the first note's own pulse is its place in the cycle.
        cycle = cycle.model_copy(update={"phase_pulses": round((events[0].onset_pulse or 0.0) % cycle.length_pulses, 6)})
    total_s = duration_s if duration_s is not None else total_pulses * pulse_s
    log = StageLog(stage="ingest", params={"syntax": req.syntax, "events": len(events)},
                   duration_ms=round((time.perf_counter() - req.started) * 1000.0, 2),
                   note="Notes were read as written; no audio analysis was run.")
    ir = IR(
        source=Source(kind="notes", filename=req.filename, format_hint=req.format_id,
                      duration_s=round(max(total_s, last), 9), sha256=req.sha256, notes_syntax=req.syntax),
        tonic=tonic, mode=mode,
        tempo=Tempo(bpm=round(bpm, 4), confidence=tempo_confidence),
        cycle=cycle, events=tuple(events),
        meta=Meta(warnings=tuple(warnings), processing_log=(log,), analysis=Analysis(style=req.format_id)),
    )
    return assert_valid(ir)


# ---------------------------------------------------------------------------
# Syntax detection and dispatch
# ---------------------------------------------------------------------------

_SARGAM_TOKEN = re.compile(r"^[SRGMPDNsrgmpdn123'.,;_()~/\\*̣̇’\-]+$")
_NAMES_TOKEN = re.compile(rf"^(?:[A-Ga-g](?:{ACCIDENTAL_PATTERN})?-?\d+(?::\d+)?[~/\\]?\*?|[rR](?::\d+)?)$")
_SARGAM_KEYS = {"sruti", "shruti", "raga", "tala"}
_NAMES_KEYS = {"meter", "mode"}
_SARGAM_EXTENSIONS = {"sargam", "swara", "sgm"}
_NAMES_EXTENSIONS = {"notes", "names"}


def _text_syntax(text: str, filename: Optional[str], format_id: Optional[str]) -> str:
    """sargam or note_names, by which grammar the tokens fit. Hints only break a tie."""
    import unicodedata

    sargam = names = 0
    for raw in unicodedata.normalize("NFD", text).splitlines():
        header = match_header(raw, min_key_length=2)
        if header is not None:
            sargam += 5 * (header.key in _SARGAM_KEYS)
            names += 5 * (header.key in _NAMES_KEYS)
            continue
        for token in strip_comment(raw).replace("|", " ").split():
            sargam += bool(_SARGAM_TOKEN.match(token))
            names += bool(_NAMES_TOKEN.match(token))
    if sargam != names:
        return "sargam" if sargam > names else "note_names"
    ext = extension(filename)
    if ext in _SARGAM_EXTENSIONS:
        return "sargam"
    if ext in _NAMES_EXTENSIONS:
        return "note_names"
    if format_id and format_id != "auto" and registry.has(format_id):
        parsers = registry.get(format_id).META.input_parsers
        if "sargam" in parsers and "note_names" not in parsers:
            return "sargam"
    return "note_names"


def detect_syntax(data: Union[str, bytes], filename: Optional[str] = None,
                  format_id: Optional[str] = None) -> str:
    """sargam | note_names | abc | musicxml | midi, decided by content (spec 7.1)."""
    if isinstance(data, (bytes, bytearray, memoryview)):
        raw = bytes(data)
        found = sniff(raw, filename)
        if found.subtype == "midi":
            return "midi"
        if found.subtype in ("musicxml", "mxl"):
            return "musicxml"
        if found.subtype == "abc":
            return "abc"
        if found.kind == "audio":
            raise IngestError("This is an audio file, not written notes.", code="unsupported_type",
                              hint="Upload it as audio, or type the notes instead.")
        if found.subtype != "text":
            raise IngestError("Orin could not tell what kind of notes file this is.", code="unsupported_type",
                              hint="Use sargam text, note names, ABC, MusicXML or MIDI.")
        text = decode_text(raw) or ""
    else:
        text = data
        subtype = text_subtype(text, filename)
        if subtype in ("musicxml", "abc"):
            return subtype
    return _text_syntax(text, filename, format_id)


def _as_text(data: Union[str, bytes]) -> str:
    if isinstance(data, str):
        return data.lstrip("﻿")
    text = decode_text(data)
    if text is None:
        raise IngestError("The notes file is not readable text.", code="decode_failed",
                          hint="Save it as plain UTF-8 text and try again.")
    return text.lstrip("﻿")


def parse_notes(data: Union[str, bytes], *, format_id: Optional[str] = None, syntax: Optional[str] = None,
                filename: Optional[str] = None, tonic_hz: Optional[float] = None,
                mode_id: Optional[str] = None, tempo_bpm: Optional[float] = None) -> IR:
    """Notes in any accepted syntax -> a validated IR.

    ``syntax`` None or "auto" means detect. ``tonic_hz``, ``mode_id`` and ``tempo_bpm``
    override whatever the notes declare. Raises ``NotesParseError`` (problems located by
    line and column) on bad notes and ``IngestError`` on input that is not notes at all.
    """
    if isinstance(data, (bytearray, memoryview)):
        data = bytes(data)
    if data is None or not (data.strip() if isinstance(data, (str, bytes)) else data):
        raise IngestError("There are no notes to read.", code="empty", hint="Type some notes or choose a file.")
    if tonic_hz is not None and not (math.isfinite(tonic_hz) and 20.0 <= tonic_hz <= 5000.0):
        raise IngestError(f"A tonic of {tonic_hz} Hz is outside the range Orin accepts (20 to 5000 Hz).",
                          code="bad_option")
    if tempo_bpm is not None and not (math.isfinite(tempo_bpm) and 10.0 <= tempo_bpm <= 480.0):
        raise IngestError(f"A tempo of {tempo_bpm} beats per minute is outside the range Orin accepts (10 to 480).",
                          code="bad_option")

    chosen = (syntax or "auto").strip().lower()
    chosen = _SYNTAX_ALIASES.get(chosen, chosen)
    if chosen == "auto":
        chosen = detect_syntax(data, filename, format_id)
    if chosen not in SYNTAXES:
        raise IngestError(f"'{syntax}' is not a notes syntax Orin reads.", code="unsupported_type",
                          hint=f"Use one of: {', '.join(SYNTAXES)}.")

    wanted = format_id if format_id and format_id != "auto" else DEFAULT_FORMAT.get(chosen, FALLBACK_FORMAT)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    req = NotesRequest(syntax=chosen, format_id=registry.get(wanted).META.id, filename=filename,
                       tonic_hz=tonic_hz, mode_id=mode_id, tempo_bpm=tempo_bpm,
                       sha256=hashlib.sha256(raw).hexdigest())

    if chosen == "sargam":
        from .notes_sargam import parse_sargam

        return parse_sargam(_as_text(data), req)
    if chosen == "note_names":
        from .notes_names import parse_names

        return parse_names(_as_text(data), req)
    if chosen == "abc":
        from .notes_m21 import parse_abc

        return parse_abc(_as_text(data), req)
    if chosen == "musicxml":
        from .notes_m21 import parse_musicxml

        return parse_musicxml(data, req)
    from .notes_midi import parse_midi

    if isinstance(data, str):
        raise IngestError("A MIDI file must be uploaded as a file, not pasted as text.", code="unsupported_type")
    return parse_midi(data, req)
