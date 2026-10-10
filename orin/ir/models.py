"""The Orin Intermediate Representation (spec section 6).

Format-neutral: pitch is cents relative to the tonic, time is seconds, and there
are no note names and no bar lines. A renderer may impose names, bars or a tala
later; the IR never does.

Units used everywhere:
  cents   1200 * log2(f / tonic_hz), unfolded (can be negative or above 1200).
  pulse   the smallest grid unit. One beat = ``cycle.subdivision`` pulses and one
          cycle = ``cycle.length_pulses`` pulses, so bpm = 60 / (pulse_s * subdivision).

Models are frozen and use tuples, so an adapter cannot mutate an IR it was given
(spec 5). Build a changed copy with ``model_copy(update=...)``.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

IR_VERSION = "1.0"

OrnamentKind = Literal["oscillation", "vibrato", "glide", "grace"]


class Model(BaseModel):
    """Base for every IR, loss and format model: frozen, strict keys, finite numbers."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class FormatCandidate(Model):
    """One style auto-detect result (spec 8.2). Scores are normalised to sum to 1."""

    format_id: str
    score: float


class Source(Model):
    kind: Literal["audio", "notes"]
    filename: Optional[str] = None
    format_hint: Optional[str] = None  # format id the user chose; None means auto-detect
    detected_format: Optional[str] = None
    detected_format_confidence: Optional[float] = None
    detected_candidates: tuple[FormatCandidate, ...] = ()
    duration_s: float = 0.0
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    loudness_dbfs: Optional[float] = None  # RMS level of the decoded file
    sha256: Optional[str] = None
    notes_syntax: Optional[str] = None  # sargam | note_names | abc | musicxml | midi


class TonicCandidate(Model):
    hz: float
    confidence: float
    method: str


class Tonic(Model):
    hz: float
    cents_ref_hz: float  # the frequency cents are measured from; equal to ``hz``
    confidence: float
    # drone | histogram | key_profile | declared | user | assumed
    method: str
    candidates: tuple[TonicCandidate, ...] = ()
    # Tonic versus the nearest 12-TET pitch at A4 = 440 Hz. Reported, never "corrected" (spec 7.8).
    tuning_offset_cents: float = 0.0
    nearest_note: Optional[str] = None  # e.g. "D3"; display only


class ModeCandidate(Model):
    id: str
    label: str
    label_system: str  # format id the mode belongs to
    score: float
    # "format_id:mode_id" of modes with the same pitches, or a rotation of them.
    lookalikes: tuple[str, ...] = ()


class Mode(Model):
    """Closest matches, never a certain answer (spec 7.12)."""

    label: Optional[str] = None
    label_system: Optional[str] = None
    mode_id: Optional[str] = None
    allowed_pitches_cents: tuple[float, ...] = ()
    confidence: float = 0.0
    candidates: tuple[ModeCandidate, ...] = ()
    user_selected: bool = False


class Tempo(Model):
    bpm: Optional[float] = None
    confidence: float = 0.0
    free_time: bool = False  # alapana, taqsim, avaz, rubato: render unmeasured


class Cycle(Model):
    length_pulses: Optional[int] = None  # None when free time or unknown
    subdivision: int = 1  # pulses per beat
    label: Optional[str] = None
    confidence: float = 0.0
    template_id: Optional[str] = None
    pulse_s: Optional[float] = None
    # Where the first event sits inside the cycle (anacrusis / eduppu).
    phase_pulses: float = 0.0
    swing_ratio: Optional[float] = None  # long/short subdivision ratio; above ~1.4 is swung


class Ornament(Model):
    """Measured pitch movement on a note, with a neutral name (spec 7.10).

    Tradition names (kampita, jaru, meend...) are applied by an adapter, never here.
    """

    kind: Optional[OrnamentKind] = None
    contour_ref: Optional[tuple[int, int]] = None  # [start, end) indexes into IR.contour
    # Half peak-to-peak for oscillation and vibrato; total extent for glide and grace.
    amplitude_cents: float = 0.0
    rate_hz: Optional[float] = None
    # sine | up | down | touch_above | touch_below
    shape: Optional[str] = None
    position: Optional[Literal["start", "end", "whole"]] = None
    cycles: Optional[float] = None
    coverage: float = 0.0  # share of the note's duration the ornament occupies, 0..1
    confidence: float = 0.0


class NoteEvent(Model):
    id: str
    onset_s: float
    offset_s: float
    onset_pulse: Optional[float] = None
    duration_pulses: Optional[float] = None
    pitch_cents: float  # anchor: duration-weighted median of the plateau, relative to the tonic
    pitch_cents_median: float
    pitch_cents_min: float
    pitch_cents_max: float
    loudness_db: Optional[float] = None
    velocity: Optional[int] = None  # 1..127, when the source gave one (MIDI)
    voiced_fraction: float = 1.0
    ornament: Ornament = Field(default_factory=Ornament)  # the dominant ornament
    extra_ornaments: tuple[Ornament, ...] = ()  # e.g. a glide into an oscillating note
    # min(mean pitch-tracker confidence over the note, segmentation stability). 1.0 for parsed notes.
    confidence: float = 1.0
    guessed: bool = False  # notes input: the parser had to guess something (spec 7.14)


class ContourPoint(Model):
    t_s: float
    cents: Optional[float] = None  # None = unvoiced
    confidence: float = 0.0


class IRWarning(Model):
    code: str
    message: str
    stage: Optional[str] = None


class StageLog(Model):
    """One processing_log entry, shown in the UI's "How was this made?" panel."""

    stage: str
    version: str = "1"
    params: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    cache_hit: bool = False
    note: Optional[str] = None


class Analysis(Model):
    style: Optional[str] = None  # format id the style-dependent stages ran with
    profile_id: Optional[str] = None  # segmentation profile from orin/config/styles.toml
    sensitivity: float = 1.0
    trackers: tuple[str, ...] = ()
    user_overrides: dict[str, Any] = Field(default_factory=dict)


class Meta(Model):
    warnings: tuple[IRWarning, ...] = ()
    processing_log: tuple[StageLog, ...] = ()
    analysis: Analysis = Field(default_factory=Analysis)


class IR(Model):
    version: str = IR_VERSION
    source: Source
    tonic: Tonic
    mode: Mode = Field(default_factory=Mode)
    tempo: Tempo = Field(default_factory=Tempo)
    cycle: Cycle = Field(default_factory=Cycle)
    events: tuple[NoteEvent, ...] = ()
    contour: tuple[ContourPoint, ...] = ()
    contour_hop_s: float = 0.01
    meta: Meta = Field(default_factory=Meta)

    def event(self, event_id: str) -> Optional[NoteEvent]:
        for e in self.events:
            if e.id == event_id:
                return e
        return None

    @property
    def duration_s(self) -> float:
        return max(self.source.duration_s, self.events[-1].offset_s if self.events else 0.0)


def fold(cents: float) -> float:
    """Cents folded into one octave, 0 <= result < 1200."""
    return cents % 1200.0
