"""Format (tradition) model and the adapter interface (spec sections 4 and 5).

A format is a musical SYSTEM: a pitch grid, modes, rhythm cycles, an ornament
vocabulary and a notation style. An adapter file is mostly data (a ``FormatMeta``);
one generic engine (``orin/formats/engine.py``) does the conversion for all of them:

  1. QUANTIZE  each IR event to a pitch the target can write
  2. SNAP      onsets and durations to the target's rhythm grid
  3. DROP or APPROXIMATE ornaments the target cannot express

and records every change as a LossItem or a Notice. Adapters never mutate the IR and
never raise on odd music; they degrade and log.

All tradition data is a draft until a practitioner reviews it: every mode and rhythm
template carries a ``source`` and a ``confidence``, and ``review_status`` starts at
"unreviewed".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Optional, Union

from pydantic import Field

from ..ir.models import IR, Model, ModeCandidate
from ..loss.models import LossReport

if TYPE_CHECKING:  # pragma: no cover
    from ..detect.features import StyleFeatures
    from ..render import RenderResult

Confidence = Literal["high", "medium", "low"]
Family = Literal["indian_classical", "western_art", "western_popular", "middle_eastern", "blues_derived", "other"]
PitchSystem = Literal["12tet", "just_intonation_srutis", "maqam_quarter_tones", "blues_inflected", "custom_cents_table"]
TonicModel = Literal["movable_sa", "fixed_key_signature", "movable_root"]
RhythmModel = Literal["tala_cycle", "meter_bars", "swing_grid", "free_time"]
ReviewStatus = Literal["unreviewed", "self_checked", "practitioner_reviewed"]
Step = Literal["C", "D", "E", "F", "G", "A", "B"]

RENDER_KINDS = ("abc", "midi", "musicxml", "svg", "sargam", "names", "tab", "describe")
PARSER_KINDS = ("sargam", "note_names", "abc", "musicxml", "midi")

FAMILY_LABELS: dict[str, str] = {
    "western_art": "Western art music",
    "western_popular": "Western popular",
    "indian_classical": "Indian classical",
    "middle_eastern": "Middle Eastern and Persian",
    "blues_derived": "Blues-derived",
    "other": "Other",
}


def circ_diff(a: float, b: float) -> float:
    """Signed difference a - b in cents on the octave circle, in [-600, 600)."""
    return ((a - b + 600.0) % 1200.0) - 600.0


class WrittenPitch(Model):
    """A notated pitch class: a letter and an alteration in cents (-50 = half-flat, -100 = flat)."""

    step: Step
    alter_cents: float = 0.0


class Degree(Model):
    """One pitch of a mode. Modes hold their own degrees inline, because the same grid
    position can be two different notes (204 cents is R2 or G1) and the same name can be
    tuned differently per mode (the half-flat of Rast is not the half-flat of Bayati)."""

    name: str  # "R2", "E half-flat", "3"...
    degree: int = Field(ge=1, le=7)  # diatonic degree, used to spell the note on a staff
    cents: float  # nominal, 0 <= cents < 1200 above the tonic. Nominal, not measured tuning.
    tol_lo: float = 30.0  # how far below nominal still counts as this degree
    tol_hi: float = 30.0
    role: Optional[str] = None  # e.g. "variable"

    def outside(self, folded_cents: float) -> float:
        """Cents by which a pitch falls outside this degree's tolerance window (0 if inside)."""
        d = circ_diff(folded_cents, self.cents)
        if d < -self.tol_lo:
            return -self.tol_lo - d
        if d > self.tol_hi:
            return d - self.tol_hi
        return 0.0


class GridPosition(Model):
    """One position of a format's full pitch grid: everything the notation can write."""

    name: str  # default name when no mode decides, e.g. "R2", "b3"
    cents: float
    degree: int = Field(ge=1, le=7)  # default staff spelling
    alt: tuple[tuple[str, int], ...] = ()  # other (name, degree) readings, e.g. (("G1", 3),)
    # How far from nominal still counts as this position. 0 for 12-TET (exact pitches);
    # wider where the table is nominal (swarasthanas, the 24-step naming grid).
    tol: float = 0.0


class ModeEntry(Model):
    """A raga, maqam, dastgah or scale. A soft constraint for the engine, never a snap target."""

    id: str
    label: str
    kind: str = "mode"  # melakarta | janya | maqam | dastgah | avaz | scale | mode
    ascending: tuple[Degree, ...]
    descending: Optional[tuple[Degree, ...]] = None  # None = same pitches as ascending
    variable: Optional[tuple[Degree, ...]] = None  # alternates: upper-jins options, moteghayyer, anya swara
    # Where the tonic is conventionally written (Rast on C, Bayati on D, Huzam on E half-flat).
    conventional_tonic: Optional[WrittenPitch] = None
    key_signature_parent: Optional[Literal["major", "minor"]] = None  # 12-TET formats
    aliases: tuple[str, ...] = ()
    source: str  # where the values come from; required, never empty
    confidence: Confidence = "medium"
    caveats: tuple[str, ...] = ()

    def degrees(self) -> tuple[Degree, ...]:
        """Union of ascending, descending and variable degrees, sorted by cents."""
        seen: dict[tuple[str, int], Degree] = {}
        for group in (self.ascending, self.descending or (), self.variable or ()):
            for d in group:
                seen.setdefault((d.name, round(d.cents)), d)
        return tuple(sorted(seen.values(), key=lambda d: d.cents))

    def pitch_set(self) -> tuple[float, ...]:
        return tuple(sorted({d.cents for d in self.degrees()}))


class RhythmTemplate(Model):
    """A tala, iqa' or meter (spec 4.3). One cycle = cycle_beats * subdivision pulses."""

    id: str
    label: str
    cycle_beats: int
    subdivision: int  # pulses per beat (nadai / gati)
    # One entry per beat (len == cycle_beats) or per pulse (len == length_pulses):
    # "clap", "finger", "wave", "D" (dum), "T" (tak), "-" (rest), "strong", "weak"...
    structure: tuple[str, ...] = ()
    accent_pattern: tuple[float, ...] = ()  # same length as structure, 0..1
    swing_ratio: Optional[float] = None
    groups: tuple[int, ...] = ()  # beat grouping: (4, 2, 2) for Adi; a "|" is printed between groups
    # Staff approximation of one cycle. One pulse lasts
    # (numerator * 4 / denominator) / length_pulses quarter notes.
    time_signature: tuple[int, int] = (4, 4)
    source: str = ""
    confidence: Confidence = "medium"
    caveats: tuple[str, ...] = ()

    @property
    def length_pulses(self) -> int:
        return self.cycle_beats * self.subdivision

    @property
    def pulse_quarters(self) -> float:
        num, den = self.time_signature
        return (num * 4.0 / den) / self.length_pulses


class OrnamentRule(Model):
    """How a format treats one neutral ornament kind within an amplitude range."""

    neutral_kind: Literal["oscillation", "vibrato", "glide", "grace"]
    min_amplitude_cents: float = 0.0
    max_amplitude_cents: float = 1e9
    local_name: str  # hedged: "kampita-like", "slide", "vibrato"
    action: Literal["keep", "approximate", "drop"]
    symbol: Optional[str] = None  # text mark, e.g. "~", "/"
    symbol_down: Optional[str] = None  # used instead of ``symbol`` when the movement goes down, e.g. "\\"
    staff_mark: Optional[str] = None  # renderer hint: grace | gliss | wavy | bend
    approximation: Optional[str] = None  # words for the loss item: "a wavy-line mark"


class FormatMeta(Model):
    """Registry entry (spec 4.1) plus what the engine and renderers need."""

    id: str
    display_name: str
    description: str  # one line, used in the UI and in prompts
    aliases: tuple[str, ...] = ()
    family: Family
    order: int = 100  # sort key inside the registry
    pitch_system: PitchSystem
    tonic_model: TonicModel
    naming: Literal["relative", "absolute"]  # sargam is relative to Sa; letters and solfege are absolute
    written_tonic_policy: Literal["sounding", "conventional"] = "sounding"
    tonic_word: str = "tonic"  # "Sa", "tonic", "finalis"
    accidental_style: Literal["standard", "arabic", "persian"] = "standard"
    note_names: Literal["letters", "solfege", "swara"] = "letters"
    grid: tuple[GridPosition, ...]  # pitch_table: every pitch this notation can write, one octave
    mode_table: tuple[ModeEntry, ...]
    rhythm_model: RhythmModel
    rhythm_templates: tuple[RhythmTemplate, ...]
    default_template_id: Optional[str] = None  # None = free time by default
    # True when naming a cycle is a claim a melody cannot support (an iqa' is defined by
    # its drum strokes). Such a cycle is used only when the user picks it; otherwise the
    # engine writes a plain "N-beat cycle".
    cycles_need_choice: bool = False
    ornament_table: tuple[OrnamentRule, ...]
    expects_ornaments: bool = False  # the idiom is bare without them (ORNAMENT_NOT_SUPPLIED)
    writes_dynamics: bool = False
    renderers: tuple[str, ...]  # subset of RENDER_KINDS
    primary_renderer: str  # what the Notation Studio shows first
    input_parsers: tuple[str, ...]  # subset of PARSER_KINDS
    monophonic_only: bool = True
    review_status: ReviewStatus = "unreviewed"
    reviewer_note: str = ""
    caveats: tuple[str, ...] = ()
    analysis_profile: str = "western"  # key in orin/config/styles.toml
    # Style auto-detect priors: feature name -> (ideal_lo, ideal_hi, weight). See detect/features.py.
    detect_priors: dict[str, tuple[float, float, float]] = Field(default_factory=dict)

    def mode(self, mode_id: Optional[str]) -> Optional[ModeEntry]:
        if not mode_id:
            return None
        key = mode_id.lower()
        for m in self.mode_table:
            if m.id == key or key in (a.lower() for a in m.aliases) or m.label.lower() == key:
                return m
        return None

    def template(self, template_id: Optional[str]) -> Optional[RhythmTemplate]:
        if not template_id:
            return None
        for t in self.rhythm_templates:
            if t.id == template_id:
                return t
        return None

    def ornament_rule(self, kind: str, amplitude_cents: float) -> Optional[OrnamentRule]:
        for r in self.ornament_table:
            if r.neutral_kind == kind and r.min_amplitude_cents <= amplitude_cents <= r.max_amplitude_cents:
                return r
        return None

    def nearest_grid(self, folded_cents: float) -> GridPosition:
        return min(self.grid, key=lambda g: abs(circ_diff(folded_cents, g.cents)))


# ---------------------------------------------------------------------------
# Adapter output
# ---------------------------------------------------------------------------

class ScoreOrnament(Model):
    kind: str  # neutral kind
    action: Literal["keep", "approximate", "drop"]
    local_name: Optional[str] = None  # only set when the format was chosen explicitly
    symbol: Optional[str] = None
    staff_mark: Optional[str] = None
    amplitude_cents: float = 0.0
    rate_hz: Optional[float] = None
    shape: Optional[str] = None
    position: Optional[str] = None


class ScoreNote(Model):
    id: str  # "n1", "n2"...: a valid XML name, reused as the SVG element id
    source_event_id: Optional[str] = None
    cents: float  # written pitch, cents above the tonic, unfolded
    source_cents: float  # the IR anchor
    cents_delta: float  # written - source
    name: str  # relative name ("R2") or scale-degree label; absolute spelling is the renderer's job
    degree: int = Field(ge=1, le=7)
    octave: int  # 0 = the octave starting on the tonic, 1 = the one above, -1 = below
    in_mode: bool = True
    start_pulse: int  # always set; in free time the grid is nominal and no bars are drawn
    duration_pulses: int
    onset_s: float  # written timing, for playback and MIDI
    duration_s: float
    source_onset_s: float
    velocity: int = 80
    ornaments: tuple[ScoreOrnament, ...] = ()
    confidence: float = 1.0


class ScoreOptions(Model):
    """Everything needed to reproduce the result (spec 5: output includes the options used)."""

    tonic_hz: float
    a4_hz: float = 440.0
    mode_id: Optional[str] = None
    mode_label: Optional[str] = None
    template_id: Optional[str] = None
    tempo_bpm: Optional[float] = None
    pulse_s: float  # seconds per grid pulse
    subdivision: int  # pulses per beat
    free_time: bool = False  # unmeasured: renderers print lengths but no bar lines
    loss_threshold_cents: float = 5.0
    written_tonic: WrittenPitch  # where the tonic is written on a staff
    written_tonic_octave: int = 4  # scientific pitch octave of the written tonic
    transposition_cents: float = 0.0  # sounding minus written
    explicit_style: bool = True  # False = style was auto-detected, so no tradition names are applied


class FormatScore(Model):
    format_id: str
    source_format: Optional[str] = None
    options: ScoreOptions
    notes: tuple[ScoreNote, ...] = ()
    cycle: Optional[RhythmTemplate] = None  # the template in force; None in free time
    # Pulse 0 is always a cycle start. This is the first note's start pulse, so a value
    # above 0 means the melody enters part-way through a cycle (pickup / eduppu).
    phase_pulses: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def free_time(self) -> bool:
        return self.options.free_time


class ConvertOptions(Model):
    """Target-side choices. Everything is optional; the engine picks hedged defaults."""

    # Target mode. None = no mode: notes go to the nearest grid pitch. A mode is only ever
    # applied when the caller asks for it or the source and target formats are the same.
    mode_id: Optional[str] = None
    template_id: Optional[str] = None  # None = best match for the IR's cycle length
    tonic_hz: Optional[float] = None
    tempo_bpm: Optional[float] = None
    loss_threshold_cents: float = 5.0  # every pitch change above this is logged (spec 5)
    force_measured: bool = False  # put a free-time source into bars (logs FREE_TIME_MEASURED)
    written_tonic: Optional[Literal["sounding", "conventional"]] = None  # None = the format's policy
    explicit_style: bool = True


class ConversionResult(Model):
    score: FormatScore
    loss: LossReport
    # Target modes with the same pitches as the source's mode. Offered, never applied silently.
    suggestions: tuple[ModeCandidate, ...] = ()


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class Adapter:
    """Spec section 5. Subclass only when a format needs behaviour the tables cannot express."""

    def __init__(self, meta: FormatMeta):
        self.META = meta

    @property
    def id(self) -> str:
        return self.META.id

    def parse_notes(self, data: Union[str, bytes], *, syntax: Optional[str] = None,
                    filename: Optional[str] = None, **kw: Any) -> IR:
        from ..ingest.notes import parse_notes

        return parse_notes(data, format_id=self.META.id, syntax=syntax, filename=filename, **kw)

    def ir_to_format(self, ir: IR, opts: Optional[ConvertOptions] = None) -> ConversionResult:
        from .engine import convert_ir

        return convert_ir(ir, self.META, opts)

    def render(self, score: FormatScore, kind: str, **kw: Any) -> "RenderResult":
        from ..render import render

        return render(score, self.META, kind, **kw)

    def score_fit(self, ir: IR, features: Optional["StyleFeatures"] = None) -> float:
        from ..detect.score import default_score_fit

        return default_score_fit(self.META, ir, features)


def export(adapter: Adapter):
    """Module-level names the spec asks each adapter file to expose:

        META, parse_notes, ir_to_format, render, score_fit = export(ADAPTER)
    """
    return adapter.META, adapter.parse_notes, adapter.ir_to_format, adapter.render, adapter.score_fit
