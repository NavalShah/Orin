"""The generic conversion engine (spec sections 5 and 9): IR -> FormatScore + LossReport.

One engine serves every format; an adapter file only supplies tables (``FormatMeta``).
``convert_ir`` does three things and records each change:

  1. QUANTIZE  every note's anchor to a pitch the target can write
  2. SNAP      onsets and lengths to the target's rhythm grid (or keep them, in free time)
  3. DROP or APPROXIMATE ornaments the target has no sign for

It never mutates the IR and never raises on odd music: it degrades and writes a loss
item (what the target could not carry) or a notice (Orin's own assumptions and doubt).

Pitch rules, in order, for each note (folded to one octave, octave restored after):
  a. a mode is in force and the anchor is inside the window of one of its degrees:
     that degree, nothing lost.
  b. a mode is in force, the note is unstable (ornament wider than 50 cents) and a
     degree is within the profile's ``mode_snap_cents``: that degree, with a
     PITCH_ANCHOR_UNCERTAIN notice.
  c. otherwise the nearest position of the format's grid (of two equally near, the one
     the mode has). Outside the mode this is an OUT_OF_MODE_NOTE notice: the note stays
     where it was played.

A note counts as rounded (MICROTONE_ROUNDED, one item per note) when its anchor is an
in-between pitch: for a mode degree, more than 25 cents outside the degree's window;
for a bare grid position, outside the position's window and more than 25 cents from it.
For the 12-tone formats (window 0) both mean "more than 25 cents away". Smaller moves
are grouped into one informational item (TEMPERAMENT_RETUNED when the tuning system
changed).

How bad a rounding is, is measured as the share of the way from the written pitch to the
midpoint between the two writable pitches around the note. Severity is "major" from 80%,
and for a grid position the fidelity score's error input is 50 cents times that share. On
the even 12-tone grid the midpoint is 50 cents away, so this is plain distance: major from
40 cents. On an uneven grid (the swarasthanas) a half-flat third sitting midway between G2
and G3 costs what it costs between E flat and E.

Notes inside the timing tolerance are passed to the score as unshifted, and every other
non-zero note penalty lands on an item, so fidelity == 1 - sum(item.penalty).

Rhythm: start pulse 0 is a cycle start. Positions come from ``onset_pulse`` when the
event has one (typed notes), otherwise from ``onset_s`` measured from the first note,
which sits ``cycle.phase_pulses`` into the cycle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .. import config
from ..ir.models import IR, ModeCandidate, NoteEvent
from ..loss.fidelity import PITCH_FREE_CENTS, PITCH_FULL_CENTS, NoteLoss, ornament_loss
from ..loss.fidelity import score as fidelity_score
from ..loss.models import LossItem, LossReport, LossSummary, Notice
from ..loss.texts import explain_key, human_text
from . import registry
from .base import (
    ConversionResult,
    ConvertOptions,
    Degree,
    FormatMeta,
    FormatScore,
    GridPosition,
    ModeEntry,
    RhythmTemplate,
    ScoreNote,
    ScoreOptions,
    ScoreOrnament,
    WrittenPitch,
    circ_diff,
)

RANGE_LO_CENTS = -2400.0  # notes outside [lo, hi) are folded by octaves (OCTAVE_FOLDED)
RANGE_HI_CENTS = 3600.0
UNSTABLE_AMPLITUDE_CENTS = 50.0  # a dominant ornament wider than this makes the anchor uncertain
MAJOR_SHARE = 0.8  # of the way to the midpoint between two writable pitches (40 of 50 cents in 12-TET)
AMBIGUOUS_CENTS = 5.0  # this close to the midpoint, either neighbour is as good a spelling
COINCIDE_CENTS = 1.0
TRANSPOSED_MIN_CENTS = 20.0
TUNING_OFFSET_MIN_CENTS = 5.0
TONIC_MIN_CONFIDENCE = 0.3
FALLBACK_TONIC_HZ = 261.6256  # C4; only used when the IR has no usable tonic
FREE_UNIT_DEFAULT_S = 0.25
FREE_UNIT_RANGE_S = (0.1, 0.4)
FREE_SUBDIVISION = 2
TIMING_MINOR_MS = 60.0
ORNAMENT_MINOR_CENTS = 30.0  # half-extent thresholds for ornament severity
ORNAMENT_MAJOR_CENTS = 60.0
DYNAMICS_MIN_RANGE_DB = 6.0
DEFAULT_VELOCITY = 80

STEP_CENTS = {"C": 0.0, "D": 200.0, "E": 400.0, "F": 500.0, "G": 700.0, "A": 900.0, "B": 1100.0}
# The twelve "sounding" tonic spellings: C, C#, D, Eb, E, F, F#, G, Ab, A, Bb, B.
SOUNDING_TONICS: tuple[tuple[str, float], ...] = (
    ("C", 0.0), ("C", 100.0), ("D", 0.0), ("E", -100.0), ("E", 0.0), ("F", 0.0),
    ("F", 100.0), ("G", 0.0), ("A", -100.0), ("A", 0.0), ("B", -100.0), ("B", 0.0),
)
_ACCIDENTAL_WORDS = {0.0: "", 100.0: " sharp", -100.0: " flat", -50.0: " half-flat", 50.0: " half-sharp"}
_ORNAMENT_K = {"drop": 1.0, "approximate": 0.5, "keep": 0.0}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an already sorted, non-empty list."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _num(x: float, digits: int = 3) -> float:
    return round(float(x), digits)


def _say(code: str, **fields: Any) -> str:
    """``human_text`` with two grammar repairs the fixed templates cannot make."""
    text = human_text(code, **fields)
    target = str(fields.get("target", ""))
    if target and target[0].lower() in "aeiou":
        text = text.replace(f"a {target} performer", f"an {target} performer")
    if fields.get("count") == 1:
        text = text.replace("so 1 notes shift", "so 1 note shifts")
    return text


def source_format_of(ir: IR) -> Optional[str]:
    return ir.source.format_hint or ir.source.detected_format or ir.meta.analysis.style


def _meta_for(format_id: Optional[str]) -> Optional[FormatMeta]:
    if not format_id:
        return None
    try:
        return registry.get(format_id).META
    except Exception:
        return None


def _cents_above_c4(hz: float, a4_hz: float = 440.0) -> float:
    return 1200.0 * math.log2(hz / a4_hz) + 900.0


def _written_pitch_text(pitch: WrittenPitch) -> str:
    word = _ACCIDENTAL_WORDS.get(float(pitch.alter_cents))
    if word is None:
        word = f" {pitch.alter_cents:+.0f} cents"
    return f"{pitch.step}{word}"


def _sounding_tonic(tonic_hz: float) -> tuple[WrittenPitch, int, float]:
    """Nearest 12-TET pitch at A4 = 440: (spelling, scientific octave, remainder in cents)."""
    cents = _cents_above_c4(tonic_hz)
    index = _round_half_up(cents / 100.0)
    step, alter = SOUNDING_TONICS[index % 12]
    return WrittenPitch(step=step, alter_cents=alter), 4 + index // 12, cents - 100.0 * index  # type: ignore[arg-type]


def _hz_text(hz: float) -> str:
    pitch, octave, _ = _sounding_tonic(hz)
    name = pitch.step + {100.0: "#", -100.0: "b"}.get(float(pitch.alter_cents), "")
    return f"{name}{octave} ({hz:.1f} Hz)"


@dataclass
class _Draft:
    """A loss item being built. ``pay`` says which share of the fidelity score it is charged."""

    fields: dict[str, Any]
    pay: Optional[tuple[str, str]] = None  # (part, note key): part is pitch | ornament | timing | free
    weight: float = 1.0  # share of the note's ornament penalty, when several ornaments were lost


@dataclass
class _Report:
    drafts: list[_Draft] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)

    def item(self, code: str, severity: str, text: str, *, qualifier: Optional[str] = None,
             detail: Optional[dict[str, Any]] = None, pay: Optional[tuple[str, str]] = None,
             weight: float = 1.0, **where: Any) -> None:
        self.drafts.append(_Draft(
            fields=dict(code=code, severity=severity, human_text=text, explain_key=explain_key(code, qualifier),
                        detail=detail or {}, **where),
            pay=pay, weight=weight))

    def notice(self, code: str, detail: dict[str, Any], *, event_id: Optional[str] = None,
               time_s: Optional[float] = None, **text_fields: Any) -> None:
        self.notices.append(Notice(code=code, event_id=event_id, time_s=time_s, detail=detail,
                                   human_text=human_text(code, **{**detail, **text_fields}),
                                   explain_key=explain_key(code)))


# ---------------------------------------------------------------------------
# 1. Tonic
# ---------------------------------------------------------------------------

def _tonic(ir: IR, opts: ConvertOptions, rep: _Report) -> tuple[float, float]:
    """(tonic in Hz, cents to add to every IR pitch because the tonic was overridden)."""
    src_hz = ir.tonic.hz if ir.tonic.hz > 0 else 0.0
    overridden = bool(opts.tonic_hz and opts.tonic_hz > 0)
    tonic_hz = float(opts.tonic_hz) if overridden else src_hz
    fallback = tonic_hz <= 0
    if fallback:
        tonic_hz = FALLBACK_TONIC_HZ
    shift = 1200.0 * math.log2(src_hz / tonic_hz) if overridden and src_hz > 0 else 0.0

    # A tonic the user set by hand is not an assumption.
    if fallback or (not overridden and (ir.tonic.method == "assumed" or ir.tonic.confidence < TONIC_MIN_CONFIDENCE)):
        named = ir.tonic.nearest_note if not fallback else None
        rep.notice("TONIC_ASSUMED", {"tonic_hz": _num(tonic_hz), "method": ir.tonic.method,
                                     "confidence": _num(ir.tonic.confidence)},
                   tonic=f"{named} ({tonic_hz:.1f} Hz)" if named else _hz_text(tonic_hz))
    if ir.tonic.method == "drone":
        rep.notice("DRONE_NOT_NOTATED", {"tonic_hz": _num(tonic_hz)})
    if abs(ir.tonic.tuning_offset_cents) > TUNING_OFFSET_MIN_CENTS:
        rep.notice("TUNING_OFFSET", {"offset_cents": _num(ir.tonic.tuning_offset_cents)})
    return tonic_hz, shift


# ---------------------------------------------------------------------------
# 2. Target mode
# ---------------------------------------------------------------------------

def _target_mode(ir: IR, meta: FormatMeta, opts: ConvertOptions, source_id: Optional[str], same_format: bool,
                 rep: _Report, warnings: list[str]) -> tuple[Optional[ModeEntry], tuple[ModeCandidate, ...]]:
    """The mode in force (never applied silently across traditions) and the target's lookalikes."""
    mode: Optional[ModeEntry] = None
    if opts.mode_id:
        mode = meta.mode(opts.mode_id)
        if mode is None:
            warnings.append(f"'{opts.mode_id}' is not a mode {meta.display_name} lists, so no mode was applied.")
    elif same_format and ir.mode.mode_id:
        mode = meta.mode(ir.mode.mode_id)

    if not ir.mode.mode_id or same_format:
        return mode, ()

    # Where does the IR's mode live? Its label system first, then the source format.
    home: Optional[FormatMeta] = None
    src_mode: Optional[ModeEntry] = None
    for candidate in (_meta_for(ir.mode.label_system), _meta_for(source_id)):
        if candidate is not None and candidate.mode(ir.mode.mode_id) is not None:
            home, src_mode = candidate, candidate.mode(ir.mode.mode_id)
            break
    if home is None or src_mode is None:
        return mode, ()

    cap = float(config.section("mode").get("confidence_cap", 0.85))
    confidence = min(ir.mode.confidence if ir.mode.confidence > 0 else cap, cap)

    def candidate_for(m: ModeEntry) -> ModeCandidate:
        return ModeCandidate(id=m.id, label=m.label, label_system=meta.id, score=confidence,
                             lookalikes=registry.lookalikes(meta.id, m.id))

    if home.id == meta.id:  # the mode is already one of the target's own
        return mode, (candidate_for(src_mode),)

    exact: list[ModeEntry] = []
    for ref in registry.lookalikes(home.id, src_mode.id):
        fmt, _, mode_id = ref.partition(":")
        if fmt == meta.id and not ref.endswith("~"):
            found = meta.mode(mode_id)
            if found is not None:
                exact.append(found)

    if exact:
        if any(m == src_mode for m in exact):
            # The very same table row (the 12-tone formats share theirs): offered, and nothing to report.
            return mode, tuple(candidate_for(m) for m in exact)
        shown = mode if mode is not None and any(m.id == mode.id for m in exact) else exact[0]
        detail = {"source_mode": src_mode.label, "source_mode_id": src_mode.id, "source_format": home.id,
                  "target_mode": shown.label, "target_mode_ids": [m.id for m in exact],
                  "applied": mode is not None and shown.id == mode.id}
        rep.item("MODE_APPROXIMATED", "minor", human_text("MODE_APPROXIMATED", **detail), detail=detail)
        return mode, tuple(candidate_for(m) for m in exact)

    if mode is None:  # with a mode chosen by hand, the notes do carry a mode name
        far = [d for d in src_mode.degrees()
               if meta.grid and min(abs(circ_diff(d.cents, g.cents)) for g in meta.grid) > PITCH_FREE_CENTS]
        detail = {"source_mode": src_mode.label, "source_mode_id": src_mode.id, "source_format": home.id,
                  "unwritable_degrees": [{"name": d.name, "cents": d.cents} for d in far]}
        rep.item("MODE_UNREPRESENTABLE", "major" if far else "minor",
                 human_text("MODE_UNREPRESENTABLE", target=meta.display_name, **detail), detail=detail)
    return mode, ()


# ---------------------------------------------------------------------------
# 3. Pitch
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Pitch:
    written: float  # cents above the tonic, unfolded
    name: str
    degree: int
    in_mode: bool
    path: str  # "degree" (rule a) | "uncertain" (rule b) | "grid" (rule c)
    dist: float  # how far the written pitch is from the anchor
    err: float  # the fidelity score's input; never above 25 unless the note counts as rounded
    rounded: bool
    half_gap: float  # half the distance between the two writable pitches around the anchor


def _fold_into_range(cents: float) -> tuple[float, int]:
    """(cents inside [-2400, 3600), octaves the note was moved)."""
    if cents < RANGE_LO_CENTS:
        n = int(math.ceil((RANGE_LO_CENTS - cents) / 1200.0))
        return cents + 1200.0 * n, n
    if cents >= RANGE_HI_CENTS:
        n = int(math.floor((cents - RANGE_HI_CENTS) / 1200.0)) + 1
        return cents - 1200.0 * n, -n
    return cents, 0


def _half_gap(grid_cents: Sequence[float], folded: float) -> float:
    if len(grid_cents) < 2:
        return 600.0
    lower = max((c for c in grid_cents if c <= folded), default=grid_cents[-1] - 1200.0)
    upper = min((c for c in grid_cents if c > folded), default=grid_cents[0] + 1200.0)
    return (upper - lower) / 2.0


def _restore_octave(anchor: float, target_cents: float) -> float:
    """``target_cents`` (one octave) moved to the octave nearest the anchor."""
    return target_cents + 1200.0 * round((anchor - target_cents) / 1200.0)


def _quantise(anchor: float, unstable: bool, degrees: Sequence[Degree], meta: FormatMeta,
              snap_cents: float, grid_cents: Sequence[float]) -> _Pitch:
    folded = anchor % 1200.0
    half_gap = _half_gap(grid_cents, folded)

    def at(target: float, name: str, degree: int, in_mode: bool, path: str, err: float, rounded: bool) -> _Pitch:
        written = _restore_octave(anchor, target)
        return _Pitch(written, name, degree, in_mode, path, abs(written - anchor), err, rounded, half_gap)

    if degrees:
        inside = [d for d in degrees if d.outside(folded) == 0]
        if inside:  # rule a
            d = min(inside, key=lambda x: abs(circ_diff(folded, x.cents)))
            return at(d.cents, d.name, d.degree, True, "degree", 0.0, False)
        if unstable:  # rule b
            d = min(degrees, key=lambda x: abs(circ_diff(folded, x.cents)))
            if abs(circ_diff(folded, d.cents)) <= snap_cents:
                err = d.outside(folded)
                return at(d.cents, d.name, d.degree, True, "uncertain", err, err > PITCH_FREE_CENTS)

    if not meta.grid:  # a format with no pitch table: write the note as played
        return _Pitch(anchor, f"{folded:.0f}c", 1, not degrees, "grid", 0.0, 0.0, False, half_gap)

    def in_mode_as(position: GridPosition) -> Optional[Degree]:
        return next((d for d in degrees if abs(circ_diff(d.cents, position.cents)) <= COINCIDE_CENTS), None)

    g = meta.nearest_grid(folded)  # rule c
    dist = abs(circ_diff(folded, g.cents))
    if degrees and in_mode_as(g) is None:  # exactly between two positions: prefer the one the mode has
        ties = [t for t in meta.grid if abs(abs(circ_diff(folded, t.cents)) - dist) <= 1e-6 and in_mode_as(t)]
        g = ties[0] if ties else g
    outside = max(0.0, dist - g.tol)
    # Outside the position's window and more than the 25 cents the score forgives anyway.
    rounded = dist > max(g.tol, PITCH_FREE_CENTS)
    err = PITCH_FULL_CENTS * dist / half_gap if rounded else outside
    match = in_mode_as(g)
    if match is not None:
        return at(g.cents, match.name, match.degree, True, "grid", err, rounded)
    return at(g.cents, g.name, g.degree, not degrees, "grid", err, rounded)


# ---------------------------------------------------------------------------
# 5. Rhythm
# ---------------------------------------------------------------------------

@dataclass
class _Rhythm:
    free_time: bool
    forced: bool  # a free-time source put into bars
    template: Optional[RhythmTemplate]
    pulse_s: float
    subdivision: int
    tempo_bpm: Optional[float]
    starts: list[int]
    lengths: list[int]
    onsets_s: list[float]
    durations_s: list[float]
    shifts_s: list[float]

    def where(self, i: int) -> dict[str, Any]:
        if self.template is None or self.template.length_pulses <= 0:
            return {}
        length = self.template.length_pulses
        return {"pulse": float(self.starts[i] % length), "cycle_index": self.starts[i] // length + 1}


def _duration_s(e: NoteEvent) -> float:
    return max(e.offset_s - e.onset_s, 0.0)


def _nominal_unit(events: Sequence[NoteEvent]) -> float:
    if len(events) < 2:
        return FREE_UNIT_DEFAULT_S
    gaps = sorted(b.onset_s - a.onset_s for a, b in zip(events, events[1:]) if b.onset_s - a.onset_s > 1e-9)
    if not gaps:
        return FREE_UNIT_DEFAULT_S
    return _clip(_percentile(gaps, 25.0), *FREE_UNIT_RANGE_S)


def _source_pulse_s(ir: IR, events: Sequence[NoteEvent], subdivision: int) -> Optional[float]:
    if ir.cycle.pulse_s and ir.cycle.pulse_s > 0:
        return float(ir.cycle.pulse_s)
    if ir.tempo.bpm and ir.tempo.bpm > 0:
        return 60.0 / (ir.tempo.bpm * subdivision)
    if any(e.onset_pulse is not None for e in events):  # typed pulses with no tempo: read it off the notes
        ratios = sorted(_duration_s(e) / e.duration_pulses for e in events
                        if e.duration_pulses and e.duration_pulses > 0 and _duration_s(e) > 0)
        return _percentile(ratios, 50.0) if ratios else _nominal_unit(events)
    return None


def _generic_template(beats: int, subdivision: int) -> RhythmTemplate:
    beats = int(_clip(beats, 1, 64))
    return RhythmTemplate(
        id=f"generic_{beats}", label=f"{beats}-beat cycle", cycle_beats=beats,
        subdivision=int(_clip(subdivision, 1, 16)), time_signature=(beats, 4),
        source="Synthesised by the conversion engine from the source's cycle length; not a named cycle of this tradition.",
        confidence="low", caveats=("A plain count of beats. Its grouping and accents are not known.",))


def _pick_template(ir: IR, meta: FormatMeta, opts: ConvertOptions, src_meta: Optional[FormatMeta],
                   src_subdivision: int, warnings: list[str]) -> tuple[RhythmTemplate, str]:
    """(template, how it was chosen): same | chosen | length | multiple | assumed | generic."""
    same_format = src_meta is not None and src_meta.id == meta.id
    length = ir.cycle.length_pulses
    src_beats = length / src_subdivision if length and length > 0 else None
    default = meta.template(meta.default_template_id)
    ordered = ([default] if default else []) + [t for t in meta.rhythm_templates if t is not default]
    # The source's own cycle, when the target lists the very same one (the 12-tone formats share their meters).
    src_template = src_meta.template(ir.cycle.template_id) if src_meta is not None else None
    twin = meta.template(src_template.id) if src_template is not None else None
    if twin != src_template:
        twin = None

    if opts.template_id:
        chosen = meta.template(opts.template_id)
        if chosen is not None:
            return chosen, "same" if twin is not None and twin.id == chosen.id else "chosen"
        warnings.append(f"'{opts.template_id}' is not a cycle {meta.display_name} lists, so another was used.")
    if twin is not None:
        return twin, "same"
    # A named cycle a melody cannot identify (an iqa') is never picked by its length alone.
    if src_beats and not meta.cycles_need_choice:
        equal = [t for t in ordered if t.cycle_beats == src_beats]
        if equal:
            same_shape = [t for t in equal if t.subdivision == src_subdivision]
            # Inside one format, a cycle with the same beats and subdivision is the same cycle.
            if same_format and len(same_shape) == 1:
                return same_shape[0], "same"
            return (same_shape or equal)[0], "length"
        for factor in (2, 3, 4):
            for t in ordered:
                if t.cycle_beats == src_beats * factor or t.cycle_beats * factor == src_beats:
                    return t, "multiple"
    if default is not None:
        return default, "assumed"
    return _generic_template(_round_half_up(src_beats) if src_beats and src_beats >= 1 else 4, src_subdivision), "generic"


def _cycle_item(rep: _Report, ir: IR, meta: FormatMeta, template: RhythmTemplate, how: str, src_beats: Optional[float]) -> None:
    has_source_cycle = bool(src_beats or ir.cycle.template_id)
    if how == "same" or (how == "chosen" and not has_source_cycle):
        return
    source_cycle = ir.cycle.label or ir.cycle.template_id or (f"{src_beats:g} beats" if src_beats else None)
    detail = {"target_cycle": template.label, "template_id": template.id, "matched_by": how,
              "source_cycle": source_cycle, "source_beats": src_beats, "target_beats": template.cycle_beats}
    lost = "the original grouping and accents are not shown"
    if how == "assumed":
        text = f"The source gives no cycle, so {template.label} was assumed; its grouping and accents do not come from the source."
    elif how == "generic":
        text = (f"{meta.display_name} has no default cycle, so a plain {template.label} is shown; "
                "choose the cycle yourself if you know it.")
    elif src_beats and template.cycle_beats != src_beats:
        text = (f"The cycle is shown as {template.label}, which is not the same length as the source's "
                f"{src_beats:g}-beat cycle; {lost}.")
    else:
        text = human_text("CYCLE_REINTERPRETED", **detail)
    rep.item("CYCLE_REINTERPRETED", "minor", text, detail=detail)


def _free_rhythm(events: Sequence[NoteEvent]) -> _Rhythm:
    unit = _nominal_unit(events)
    starts: list[int] = []
    for e in events:
        start = max(0, _round_half_up(e.onset_s / unit))
        if starts and start <= starts[-1]:
            start = starts[-1] + 1
        starts.append(start)
    lengths = _lengths([_duration_s(e) / unit for e in events], starts)
    return _Rhythm(True, False, None, unit, FREE_SUBDIVISION, None, starts, lengths,
                   [e.onset_s for e in events], [_duration_s(e) for e in events], [0.0] * len(events))


def _lengths(raw: Sequence[float], starts: Sequence[int]) -> list[int]:
    out = []
    for i, value in enumerate(raw):
        length = max(1, _round_half_up(value))
        if i + 1 < len(starts):
            length = min(length, starts[i + 1] - starts[i])  # a single line cannot overlap
        out.append(max(1, length))
    return out


def _snap(beats: Sequence[float], length_beats: Sequence[float], beat_s: float, template: RhythmTemplate,
          origin_s: float, forced: bool) -> _Rhythm:
    subdivision = max(1, template.subdivision)
    pulse_s = beat_s / subdivision
    starts: list[int] = []
    shifts: list[float] = []
    for b in beats:
        exact = b * subdivision
        start = max(0, _round_half_up(exact))
        if starts and start <= starts[-1]:
            start = starts[-1] + 1  # two notes on one pulse: the later one moves on
        starts.append(start)
        shifts.append((start - exact) * pulse_s)
    lengths = _lengths([d * subdivision for d in length_beats], starts)
    return _Rhythm(False, forced, template, pulse_s, subdivision, 60.0 / beat_s, starts, lengths,
                   [origin_s + s * pulse_s for s in starts], [n * pulse_s for n in lengths], shifts)


def _rhythm(ir: IR, events: Sequence[NoteEvent], meta: FormatMeta, opts: ConvertOptions,
            src_meta: Optional[FormatMeta], rep: _Report, warnings: list[str]) -> _Rhythm:
    src_subdivision = max(1, ir.cycle.subdivision)
    src_pulse_s = _source_pulse_s(ir, events, src_subdivision)
    free_source = ir.tempo.free_time or src_pulse_s is None
    override_bpm = opts.tempo_bpm if opts.tempo_bpm and opts.tempo_bpm > 0 else None

    if free_source and not opts.force_measured:
        if events:
            warnings.append("Unmeasured: the source has no steady pulse, so note lengths are approximate and no bar lines are drawn.")
        return _free_rhythm(events)

    first_s = events[0].onset_s if events else 0.0
    if free_source:  # forced into bars: the grid is Orin's, anchored on the first note
        beat_s = 60.0 / override_bpm if override_bpm else _nominal_unit(events) * FREE_SUBDIVISION
        if opts.template_id and meta.template(opts.template_id) is None:
            warnings.append(f"'{opts.template_id}' is not a cycle {meta.display_name} lists, so another was used.")
        template = (meta.template(opts.template_id) or meta.template(meta.default_template_id)
                    or _generic_template(4, FREE_SUBDIVISION))
        rhythm = _snap([(e.onset_s - first_s) / beat_s for e in events], [_duration_s(e) / beat_s for e in events],
                       beat_s, template, first_s, True)
        if events:
            rep.item("FREE_TIME_MEASURED", "major", human_text("FREE_TIME_MEASURED"),
                     detail={"target_cycle": template.label, "template_id": template.id,
                             "tempo_bpm": _num(60.0 / beat_s)},
                     pay=("free", ""))
        return rhythm

    assert src_pulse_s is not None
    src_beat_s = src_pulse_s * src_subdivision
    beat_s = 60.0 / override_bpm if override_bpm else src_beat_s
    template, how = _pick_template(ir, meta, opts, src_meta, src_subdivision, warnings)
    if events:
        length = ir.cycle.length_pulses
        _cycle_item(rep, ir, meta, template, how, length / src_subdivision if length and length > 0 else None)

    # The first note fixes the grid: it sits at its own typed pulse, or ``phase_pulses`` into the cycle.
    first_pulse = events[0].onset_pulse if events and events[0].onset_pulse is not None else max(ir.cycle.phase_pulses, 0.0)
    first_beat = first_pulse / src_subdivision
    beats = [e.onset_pulse / src_subdivision if e.onset_pulse is not None
             else first_beat + (e.onset_s - first_s) / src_beat_s for e in events]
    length_beats = [e.duration_pulses / src_subdivision if e.duration_pulses and e.duration_pulses > 0
                    else _duration_s(e) / src_beat_s for e in events]
    return _snap(beats, length_beats, beat_s, template, first_s - first_beat * beat_s, False)


# ---------------------------------------------------------------------------
# 6. Ornaments and dynamics
# ---------------------------------------------------------------------------

def _half_extent(kind: str, amplitude_cents: float) -> float:
    """Oscillation and vibrato store half the peak-to-peak; glide and grace store the whole extent."""
    return amplitude_cents if kind in ("oscillation", "vibrato") else amplitude_cents / 2.0


_ORNAMENT_WORDS = {"oscillation": "oscillation", "vibrato": "vibrato", "glide": "slide", "grace": "grace note"}


def _ornaments(e: NoteEvent, key: str, meta: FormatMeta, opts: ConvertOptions, rep: _Report,
               where: dict[str, Any], measured: bool = True) -> tuple[tuple[ScoreOrnament, ...], tuple[float, float, float]]:
    """(the note's written ornaments, the fidelity inputs of the costliest one).

    ``measured`` is False for typed notes: a mark in the notation says an ornament exists
    but not its size, so the sentence must not quote a width nobody measured.
    """
    written: list[ScoreOrnament] = []
    worst = (0.0, 0.0, 0.0)  # half extent, coverage, k
    worst_loss = 0.0
    for o in (e.ornament, *e.extra_ornaments):
        if o.kind is None:
            continue
        amplitude = abs(o.amplitude_cents)
        rule = meta.ornament_rule(o.kind, amplitude)
        action = rule.action if rule is not None else "drop"
        down = o.shape in ("down", "touch_below")
        symbol = None
        if rule is not None and action != "drop":
            symbol = rule.symbol_down if down and rule.symbol_down else rule.symbol
        written.append(ScoreOrnament(
            kind=o.kind, action=action, symbol=symbol,
            local_name=rule.local_name if rule is not None and opts.explicit_style else None,
            staff_mark=rule.staff_mark if rule is not None and action != "drop" else None,
            amplitude_cents=amplitude, rate_hz=o.rate_hz, shape=o.shape, position=o.position))
        if action == "keep":
            continue

        half = _half_extent(o.kind, amplitude)
        coverage = o.coverage if o.coverage > 0 else 1.0
        k = _ORNAMENT_K[action]
        loss = ornament_loss(NoteLoss(key, 1.0, orn_half_extent=half, orn_coverage=coverage, orn_k=k))
        if loss > worst_loss:
            worst, worst_loss = (half, coverage, k), loss

        severity = "info" if half < ORNAMENT_MINOR_CENTS else "minor" if half < ORNAMENT_MAJOR_CENTS else "major"
        detail: dict[str, Any] = {
            "note_id": key, "kind": o.kind, "action": action, "amplitude_cents": _num(amplitude),
            "width_cents": _num(2.0 * half), "half_extent_cents": _num(half), "rate_hz": o.rate_hz,
            "shape": o.shape, "position": o.position, "coverage": _num(coverage)}
        if rule is not None and opts.explicit_style:
            detail["local_name"] = rule.local_name
        if action == "approximate":
            code = "ORNAMENT_APPROXIMATED"
            severity = "minor" if severity == "major" else severity
            detail.update(symbol=symbol, staff_mark=rule.staff_mark if rule else None,
                          approximation=(rule.approximation if rule and rule.approximation else "a simple mark"))
            text = human_text(code, **detail)
        else:
            code = "ORNAMENT_DROPPED"
            if measured or o.contour_ref is not None:
                text = human_text(code, **{**detail, "amplitude_cents": 2.0 * half, "target": meta.display_name})
            else:
                detail["declared"] = True
                text = (f"The {_ORNAMENT_WORDS.get(o.kind, o.kind)} marked on this note has no symbol in "
                        f"{meta.display_name} and is shown as a plain note.")
        rep.item(code, severity, text, qualifier=o.kind, detail=detail, pay=("ornament", key), weight=loss,
                 event_id=e.id, time_s=e.onset_s, **where)
    return tuple(written), worst


def _velocity(e: NoteEvent, loudest_db: Optional[float]) -> int:
    if e.velocity is not None:
        return int(_clip(e.velocity, 1, 127))
    if e.loudness_db is not None and loudest_db is not None:  # loudest note = 100, 2 steps per dB below it
        return int(_clip(_round_half_up(100.0 + 2.0 * (e.loudness_db - loudest_db)), 20, 127))
    return DEFAULT_VELOCITY


def _dynamics(events: Sequence[NoteEvent], meta: FormatMeta, rep: _Report) -> None:
    if meta.writes_dynamics:
        return
    levels = [e.loudness_db for e in events if e.loudness_db is not None]
    velocities = {e.velocity for e in events if e.velocity is not None}
    span = max(levels) - min(levels) if levels else 0.0
    if span > DYNAMICS_MIN_RANGE_DB or len(velocities) > 1:
        detail: dict[str, Any] = {"loudness_range_db": _num(span)}
        if velocities:
            detail.update(velocity_min=min(velocities), velocity_max=max(velocities))
        rep.item("DYNAMICS_DROPPED", "info", human_text("DYNAMICS_DROPPED"), detail=detail)


# ---------------------------------------------------------------------------
# 7. Written tonic
# ---------------------------------------------------------------------------

def _written_tonic(tonic_hz: float, policy: str, mode: Optional[ModeEntry], rep: _Report) -> tuple[WrittenPitch, int, float]:
    """(spelling, scientific octave, sounding minus written in cents)."""
    if policy == "conventional" and mode is not None and mode.conventional_tonic is not None:
        pitch = mode.conventional_tonic
        sounding = _cents_above_c4(tonic_hz)
        base = STEP_CENTS[pitch.step] + pitch.alter_cents
        octaves = round((sounding - base) / 1200.0)
        transposition = sounding - (base + 1200.0 * octaves)
        if abs(transposition) > TRANSPOSED_MIN_CENTS:
            rep.notice("TRANSPOSED_FOR_NOTATION", {
                "mode": mode.label, "written_tonic": _written_pitch_text(pitch), "written_tonic_octave": 4 + octaves,
                "interval_cents": _num(transposition), "sounding_hz": _num(tonic_hz)})
        return pitch, 4 + octaves, transposition
    return _sounding_tonic(tonic_hz)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

def convert_ir(ir: IR, meta: FormatMeta, opts: Optional[ConvertOptions] = None) -> ConversionResult:
    """Convert ``ir`` to the format described by ``meta``. Never mutates ``ir``, never raises."""
    opts = opts or ConvertOptions()
    try:
        return _convert(ir, meta, opts)
    except Exception as exc:  # spec 5: degrade, do not raise
        return _failed(ir, meta, opts, exc)


def _failed(ir: IR, meta: FormatMeta, opts: ConvertOptions, exc: Exception) -> ConversionResult:
    source = source_format_of(ir)
    options = ScoreOptions(
        tonic_hz=ir.tonic.hz if ir.tonic.hz > 0 else FALLBACK_TONIC_HZ, pulse_s=FREE_UNIT_DEFAULT_S,
        subdivision=FREE_SUBDIVISION, free_time=True, loss_threshold_cents=opts.loss_threshold_cents,
        written_tonic=WrittenPitch(step="C"), explicit_style=opts.explicit_style)
    warning = f"The conversion could not be completed ({type(exc).__name__}: {exc}); no notes are shown."
    score = FormatScore(format_id=meta.id, source_format=source, options=options, warnings=(warning,))
    summary = LossSummary(fidelity_score=0.0, pitch_score=0.0, ornament_score=0.0, timing_score=0.0)
    return ConversionResult(score=score, loss=LossReport(summary=summary, source_format=source, target_format=meta.id))


def _convert(ir: IR, meta: FormatMeta, opts: ConvertOptions) -> ConversionResult:
    rep = _Report()
    warnings: list[str] = []
    target = meta.display_name

    src_meta = _meta_for(source_format_of(ir))
    source_id = src_meta.id if src_meta is not None else source_format_of(ir)
    same_format = source_id == meta.id
    events = sorted(ir.events, key=lambda e: e.onset_s)

    tonic_hz, tonic_shift = _tonic(ir, opts, rep)
    mode, suggestions = _target_mode(ir, meta, opts, source_id, same_format, rep, warnings)
    degrees = mode.degrees() if mode is not None else ()
    snap_cents = config.profile(meta.analysis_profile).mode_snap_cents
    grid_cents = sorted(g.cents for g in meta.grid)
    rhythm = _rhythm(ir, events, meta, opts, src_meta, rep, warnings)

    threshold = opts.loss_threshold_cents
    timing_tolerance_s = float(config.section("rhythm")["timing_shift_tolerance_ms"]) / 1000.0
    grid_label = rhythm.template.label if rhythm.template is not None else "pulse"
    retuned_system = src_meta is not None and src_meta.pitch_system != meta.pitch_system
    levels = [e.loudness_db for e in events if e.loudness_db is not None]
    loudest_db = max(levels) if levels else None

    if events and meta.expects_ornaments and not same_format and not any(
            o.kind is not None for e in events for o in (e.ornament, *e.extra_ornaments)):
        rep.item("ORNAMENT_NOT_SUPPLIED", "info", _say("ORNAMENT_NOT_SUPPLIED", target=target),
                 detail={"target_format": meta.id})
    _dynamics(events, meta, rep)

    notes: list[ScoreNote] = []
    note_losses: list[NoteLoss] = []
    small_moves: list[dict[str, Any]] = []  # grid notes moved a little: one grouped item

    for i, e in enumerate(events):
        key = f"n{i + 1}"
        where = rhythm.where(i)
        at = dict(event_id=e.id, time_s=e.onset_s, **where)
        source_cents = e.pitch_cents + tonic_shift
        anchor, octaves = _fold_into_range(source_cents)
        unstable = e.ornament.kind is not None and abs(e.ornament.amplitude_cents) > UNSTABLE_AMPLITUDE_CENTS
        pitch = _quantise(anchor, unstable, degrees, meta, snap_cents, grid_cents)
        moved = pitch.written - anchor  # the quantisation itself, without any octave fold

        if octaves:
            detail = {"note_id": key, "octaves": octaves, "source_cents": _num(source_cents),
                      "written_cents": _num(pitch.written)}
            rep.item("OCTAVE_FOLDED", "minor", human_text("OCTAVE_FOLDED", target=target), detail=detail,
                     pay=("pitch", key), **at)
        if pitch.rounded:
            detail = {"note_id": key, "cents_delta": _num(moved), "source_cents": _num(anchor),
                      "written_cents": _num(pitch.written), "written": pitch.name,
                      "ambiguous": abs(pitch.half_gap - pitch.dist) <= AMBIGUOUS_CENTS}
            rep.item("MICROTONE_ROUNDED", "major" if pitch.dist >= MAJOR_SHARE * pitch.half_gap else "minor",
                     human_text("MICROTONE_ROUNDED", target=target, **detail), qualifier=meta.pitch_system,
                     detail=detail, pay=None if octaves else ("pitch", key), **at)
        elif pitch.path == "grid" and abs(moved) > threshold:
            small_moves.append({"event_id": e.id, "cents_delta": _num(moved)})

        if pitch.path == "uncertain" and mode is not None:
            rep.notice("PITCH_ANCHOR_UNCERTAIN", {
                "note_id": key, "written": pitch.name, "mode": mode.label, "cents_delta": _num(moved),
                "amplitude_cents": _num(abs(e.ornament.amplitude_cents))}, event_id=e.id, time_s=e.onset_s)
        if mode is not None and not pitch.in_mode:
            rep.notice("OUT_OF_MODE_NOTE", {"note_id": key, "mode": mode.label, "written": pitch.name,
                                            "written_cents": _num(pitch.written)}, event_id=e.id, time_s=e.onset_s)

        ornaments, (orn_half, orn_coverage, orn_k) = _ornaments(e, key, meta, opts, rep, where,
                                                                    measured=ir.source.kind == "audio")

        shift_s = rhythm.shifts_s[i]
        charged_shift = shift_s if abs(shift_s) > timing_tolerance_s else 0.0  # inside the tolerance is free
        if charged_shift:
            shift_ms = shift_s * 1000.0
            detail = {"note_id": key, "shift_ms": _num(shift_ms, 1), "grid": grid_label,
                      "direction": "later" if shift_s > 0 else "earlier"}
            rep.item("TIMING_SHIFT", "info" if abs(shift_ms) < TIMING_MINOR_MS else "minor",
                     human_text("TIMING_SHIFT", **{**detail, "shift_ms": abs(shift_ms)}), detail=detail,
                     pay=("timing", key), **at)

        notes.append(ScoreNote(
            id=key, source_event_id=e.id, cents=pitch.written, source_cents=source_cents,
            cents_delta=pitch.written - source_cents, name=pitch.name, degree=pitch.degree,
            octave=int(math.floor(pitch.written / 1200.0)), in_mode=pitch.in_mode,
            start_pulse=rhythm.starts[i], duration_pulses=rhythm.lengths[i],
            onset_s=rhythm.onsets_s[i], duration_s=rhythm.durations_s[i], source_onset_s=e.onset_s,
            velocity=_velocity(e, loudest_db), ornaments=ornaments, confidence=e.confidence))
        note_losses.append(NoteLoss(
            event_id=key, duration_s=_duration_s(e), err_cents=pitch.err, octave_folded=bool(octaves),
            orn_half_extent=orn_half, orn_coverage=orn_coverage, orn_k=orn_k,
            onset_shift_s=charged_shift, grid_s=None if rhythm.free_time else rhythm.pulse_s))

    if small_moves:
        detail = {"count": len(small_moves), "max_cents": max(abs(m["cents_delta"]) for m in small_moves),
                  "notes": small_moves}
        ids = tuple(m["event_id"] for m in small_moves)
        if retuned_system:
            rep.item("TEMPERAMENT_RETUNED", "info", _say("TEMPERAMENT_RETUNED", target=target, **detail),
                     qualifier=meta.pitch_system, detail=detail, event_ids=ids)
        else:
            plural = "note sits" if len(small_moves) == 1 else "notes sit"
            rep.item("MICROTONE_ROUNDED", "info",
                     f"{len(small_moves)} {plural} up to {detail['max_cents']:.0f} cents from the pitch written for "
                     f"them in {target}; that is inside what one written pitch covers, so nothing in between is lost.",
                     qualifier=meta.pitch_system, detail=detail, event_ids=ids)

    policy = opts.written_tonic or meta.written_tonic_policy
    written_tonic, written_octave, transposition = _written_tonic(tonic_hz, policy, mode, rep)

    result = fidelity_score(note_losses, free_time_measured=rhythm.forced)
    items = _priced(rep.drafts, result)

    counts: dict[str, int] = {}
    for item in items:
        counts[item.code] = counts.get(item.code, 0) + 1
    summary = LossSummary(
        counts_by_code=counts, max_cents_delta=max((abs(n.cents_delta) for n in notes), default=0.0),
        fidelity_score=result.overall, pitch_score=result.pitch, ornament_score=result.ornament,
        timing_score=result.timing)
    options = ScoreOptions(
        tonic_hz=tonic_hz, a4_hz=440.0, mode_id=mode.id if mode else None, mode_label=mode.label if mode else None,
        template_id=rhythm.template.id if rhythm.template else None, tempo_bpm=rhythm.tempo_bpm,
        pulse_s=rhythm.pulse_s, subdivision=rhythm.subdivision, free_time=rhythm.free_time,
        loss_threshold_cents=threshold, written_tonic=written_tonic, written_tonic_octave=written_octave,
        transposition_cents=transposition, explicit_style=opts.explicit_style)
    score = FormatScore(
        format_id=meta.id, source_format=source_id, options=options, notes=tuple(notes),
        cycle=rhythm.template, phase_pulses=notes[0].start_pulse if notes and not rhythm.free_time else 0,
        warnings=tuple(warnings))
    loss = LossReport(items=items, notices=tuple(rep.notices), summary=summary,
                      source_format=source_id, target_format=meta.id)
    return ConversionResult(score=score, loss=loss, suggestions=suggestions)


def _priced(drafts: Sequence[_Draft], result: Any) -> tuple[LossItem, ...]:
    """Charge each item its share, so that fidelity == 1 - sum(item.penalty)."""
    ornament_weight: dict[str, float] = {}
    ornament_count: dict[str, int] = {}
    for d in drafts:
        if d.pay and d.pay[0] == "ornament":
            ornament_weight[d.pay[1]] = ornament_weight.get(d.pay[1], 0.0) + d.weight
            ornament_count[d.pay[1]] = ornament_count.get(d.pay[1], 0) + 1

    items = []
    for d in drafts:
        penalty = 0.0
        if d.pay is not None:
            part, key = d.pay
            note = result.penalties.get(key)
            if part == "free":
                penalty = result.free_time_penalty
            elif note is None:
                penalty = 0.0
            elif part == "pitch":
                penalty = note.pitch
            elif part == "timing":
                penalty = note.timing
            elif ornament_weight[key] > 0:
                penalty = note.ornament * d.weight / ornament_weight[key]
            else:
                penalty = note.ornament / ornament_count[key]
        items.append(LossItem(penalty=penalty, **d.fields))
    return tuple(items)
