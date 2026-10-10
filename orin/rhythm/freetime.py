"""Free time (spec 7.11): alapana, taqsim, avaz, rubato. Render unmeasured; never force bars.

A clip is in free time when it has too few onsets to show a pulse, or when too small a
share of its inter-onset intervals sits on the best pulse grid.
"""
from __future__ import annotations

from typing import Optional

from .. import config
from ..analysis_types import RhythmResult
from ..ir.models import Cycle, Tempo


def is_free(n_onsets: int, share: Optional[float]) -> bool:
    cfg = config.section("rhythm")
    if n_onsets < int(cfg["free_time_min_onsets"]) or share is None:
        return True
    return share < float(cfg["free_time_fit_share"])


def result(n_onsets: int, share: Optional[float] = None) -> RhythmResult:
    """The free-time result: no tempo, no cycle, no pulse positions.

    ``tempo.confidence`` is the confidence of the free-time call itself: the share of
    intervals that did NOT fit a grid, or 0 when there were too few onsets to tell.
    """
    few = share is None or n_onsets < int(config.section("rhythm")["free_time_min_onsets"])
    confidence = 0.0 if few else min(1.0, max(0.0, 1.0 - share))
    return RhythmResult(
        tempo=Tempo(bpm=None, confidence=round(confidence, 3), free_time=True),
        cycle=Cycle(),
        onset_pulses=[None] * n_onsets,
        duration_pulses=[None] * n_onsets,
        cycle_candidates=[],
        half_bpm=None,
        double_bpm=None,
    )
