"""Loss report types (spec 9.3).

Two channels, so the report never blames the target tradition for Orin's own doubt:
  items    what the target notation could not carry (these cost fidelity)
  notices  assumptions and analysis uncertainty (these cost nothing)
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from ..ir.models import Model

Severity = Literal["info", "minor", "major"]

# The spec's eight codes, plus four the design review found missing.
LOSS_CODES = (
    "MICROTONE_ROUNDED",      # a pitch between the target's pitches was moved to the nearest one
    "ORNAMENT_DROPPED",       # pitch movement with no symbol in the target
    "ORNAMENT_APPROXIMATED",  # pitch movement reduced to a mark that says "something happens here"
    "TIMING_SHIFT",           # onset moved to fit the target grid
    "MODE_UNREPRESENTABLE",   # no target mode has these pitches
    "OCTAVE_FOLDED",          # note moved an octave to fit the target's range
    "FREE_TIME_MEASURED",     # unmetered source forced into bars
    "DYNAMICS_DROPPED",       # loudness not written in this output
    "TEMPERAMENT_RETUNED",    # tuning system changed; grouped, once per report
    "ORNAMENT_NOT_SUPPLIED",  # target idiom expects ornaments the source does not have
    "MODE_APPROXIMATED",      # same pitches, different thing
    "CYCLE_REINTERPRETED",    # tala / iqa' / meter mapped by length only, or assumed
)

NOTICE_CODES = (
    "OUT_OF_MODE_NOTE",          # note kept on the grid although it is outside the chosen mode
    "PITCH_ANCHOR_UNCERTAIN",    # an unstable note was assigned to a mode degree
    "TRANSPOSED_FOR_NOTATION",   # written at a conventional pitch, not the sounding one
    "DRONE_NOT_NOTATED",         # a drone was heard and used for the tonic, but is not written
    "TONIC_ASSUMED",             # no tonic was declared or detected with confidence
    "TUNING_OFFSET",             # the recording sits off A440; reported, not corrected
)


class LossItem(Model):
    code: str
    severity: Severity
    event_id: Optional[str] = None
    event_ids: tuple[str, ...] = ()  # grouped items such as TEMPERAMENT_RETUNED
    time_s: Optional[float] = None
    pulse: Optional[float] = None  # pulse position inside the cycle
    cycle_index: Optional[int] = None  # bar / avartana number, 1-based
    detail: dict[str, Any] = Field(default_factory=dict)  # numbers: cents_delta, ornament params...
    human_text: str  # one plain sentence
    explain_key: str  # lookup key for the Gemini / offline explanation
    penalty: float = 0.0  # how much of the fidelity score this item cost


class Notice(Model):
    code: str
    event_id: Optional[str] = None
    time_s: Optional[float] = None
    detail: dict[str, Any] = Field(default_factory=dict)
    human_text: str
    explain_key: str


class LossSummary(Model):
    counts_by_code: dict[str, int] = Field(default_factory=dict)
    max_cents_delta: float = 0.0
    fidelity_score: float = 1.0  # 0..1, see orin/loss/fidelity.py
    pitch_score: float = 1.0
    ornament_score: float = 1.0
    timing_score: float = 1.0


class LossReport(Model):
    items: tuple[LossItem, ...] = ()
    notices: tuple[Notice, ...] = ()
    summary: LossSummary = Field(default_factory=LossSummary)
    source_format: Optional[str] = None
    target_format: str

    def to_text(self) -> str:
        """Readable export (spec 9.3)."""
        s = self.summary
        src = self.source_format or "unknown"
        lines = [
            f"Loss report: {src} -> {self.target_format}",
            f"Fidelity {s.fidelity_score:.2f}  (pitch {s.pitch_score:.2f}, ornament {s.ornament_score:.2f}, timing {s.timing_score:.2f})",
            f"Largest pitch change: {s.max_cents_delta:.0f} cents",
            "",
        ]
        if not self.items:
            lines.append("Nothing was lost in this conversion.")
        for item in self.items:
            where = f" at {item.time_s:.2f} s" if item.time_s is not None else ""
            lines.append(f"[{item.severity}] {item.code}{where}: {item.human_text}")
        if self.notices:
            lines += ["", "Notes about the analysis (these do not count as loss):"]
            for n in self.notices:
                where = f" at {n.time_s:.2f} s" if n.time_s is not None else ""
                lines.append(f"- {n.code}{where}: {n.human_text}")
        return "\n".join(lines) + "\n"
