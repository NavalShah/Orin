"""Types passed between the audio-analysis stages (spec section 7).

The stages are split across packages, and these are the seams between them:

  pitch/   audio -> PitchTrack                                   (7.5, 7.6)
  tonic/   PitchTrack (+ audio) -> ir.Tonic                      (7.7)
  segment/ cents contour -> list[Segment], with ornaments         (7.9, 7.10)
  rhythm/  note onsets (+ beat tracker output) -> RhythmResult    (7.11)
  mode/    note anchors -> ir.Mode                                (7.12)
  detect/  IR + features -> ranked formats                        (8)

Everything downstream of the pitch track works on plain arrays at a 10 ms hop, so it
can be tested with synthesised contours (orin/eval/synth.py) and no audio at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .ir.models import Cycle, Ornament, Tempo


@dataclass
class PitchTrack:
    t: np.ndarray  # frame times in seconds, in the ORIGINAL file's time (trim offset added back)
    f0: np.ndarray  # Hz; NaN = unvoiced
    conf: np.ndarray  # 0..1 per frame
    hop_s: float = 0.01
    tracker: str = "pyin"  # crepe | pyin | ensemble
    disagreements: int = 0  # frames where trackers differed by more than a semitone
    octave_fixes: int = 0  # frames folded back by octave-error repair

    def cents(self, tonic_hz: float) -> np.ndarray:
        """Unfolded cents relative to the tonic; NaN where unvoiced (spec 7.8)."""
        with np.errstate(divide="ignore", invalid="ignore"):
            return 1200.0 * np.log2(self.f0 / tonic_hz)


@dataclass
class Segment:
    """One note found in a contour. Frame indexes are [start, end)."""

    start: int
    end: int
    anchor_cents: float  # duration-weighted median of the plateau, not the mean (spec 7.9)
    median_cents: float
    min_cents: float
    max_cents: float
    voiced_fraction: float = 1.0
    confidence: float = 1.0  # min(mean tracker confidence, plateau stability)
    ornaments: list[Ornament] = field(default_factory=list)  # dominant first; filled by segment.ornaments
    onset_split: bool = False  # started by an onset (re-articulation), not by a pitch change


@dataclass
class RhythmResult:
    tempo: Tempo
    cycle: Cycle
    # Per note, aligned with the onsets passed in. None for every note in free time.
    onset_pulses: list[Optional[float]]
    duration_pulses: list[Optional[float]]
    # (template_id, score) for the cycle templates tried, best first. A melody alone
    # cannot prove a cycle, so the UI shows these as suggestions.
    cycle_candidates: list[tuple[str, float]] = field(default_factory=list)
    half_bpm: Optional[float] = None
    double_bpm: Optional[float] = None
