"""Base pulse (tatum) from note onsets by a remainder-error search (spec 7.11).

For a candidate tatum, every inter-onset interval should be close to a whole number of
tatums. The score is 1 minus twice the weighted mean remainder, so 1.0 is a perfect grid
and 0.5 is what random intervals give. Any tatum has sub-multiples that fit as well, so
among the peaks within 90% of the best the LARGEST one is taken, unless a confident beat
tracker says which one divides the beat.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.signal import find_peaks

from .. import config
from .onsets import interval_weights

GRID_RATIO = 1.002  # geometric step of the tatum search grid
WITHIN_BEST = 0.9  # peaks scoring at least this share of the best are candidates
PREFERRED_BPM = 100.0  # picks between a tempo and its half or double; both are reported
GOOD_BEAT_CONFIDENCE = 0.5
BEAT_MULTIPLES = (1, 2, 3, 4, 6, 8)


@dataclass
class TatumFit:
    tatum_s: float
    score: float  # 1 - 2 * weighted mean remainder, at ``tatum_s``
    share: float  # share of intervals within the free-time tolerance of the grid
    positions: np.ndarray  # whole-tatum position of every sorted onset, first onset = 0
    candidates: list[tuple[float, float]] = field(default_factory=list)  # (tatum_s, score), largest first


def remainders(iois: np.ndarray, tatum_s: float) -> np.ndarray:
    """Distance of each interval from the nearest whole number (at least 1) of tatums, in tatums."""
    ratio = np.asarray(iois, dtype=float) / tatum_s
    return np.minimum(np.abs(ratio - np.maximum(1.0, np.round(ratio))), 0.5)


def grid_positions(iois: np.ndarray, tatum_s: float) -> np.ndarray:
    """Whole-tatum positions by rounding each interval, so timing drift does not add up."""
    steps = np.maximum(1, np.round(np.asarray(iois, dtype=float) / tatum_s)).astype(int)
    return np.concatenate(([0], np.cumsum(steps)))


def _score(iois: np.ndarray, weights: np.ndarray, tatum_s: float) -> float:
    return float(1.0 - 2.0 * np.average(remainders(iois, tatum_s), weights=weights))


def _refine(onsets: np.ndarray, positions: np.ndarray, tatum_s: float) -> float:
    """Least-squares tatum over the whole clip, given the grid positions."""
    if len(onsets) < 3 or np.ptp(positions) == 0:
        return tatum_s
    slope = float(np.polyfit(positions.astype(float), onsets, 1)[0])
    return slope if abs(slope / tatum_s - 1.0) <= 0.05 else tatum_s


def find_tatum(onsets_s, durations_s, *, beat_period_s: Optional[float] = None) -> Optional[TatumFit]:
    """Best tatum for sorted onsets, or None when there are too few intervals to search."""
    cfg = config.section("rhythm")
    onsets = np.asarray(onsets_s, dtype=float)
    iois = np.diff(onsets)
    weights = interval_weights(durations_s)
    keep = iois > 1e-6
    iois, weights = iois[keep], weights[keep]
    if len(iois) < 2 or weights.sum() <= 0:
        return None

    lo, hi = float(cfg["tatum_min_ms"]) / 1000.0, float(cfg["tatum_max_ms"]) / 1000.0
    grid = lo * GRID_RATIO ** np.arange(int(np.log(hi / lo) / np.log(GRID_RATIO)) + 1)
    grid = np.append(grid, hi)
    scores = np.array([_score(iois, weights, tau) for tau in grid])
    peaks, _ = find_peaks(np.concatenate(([-np.inf], scores, [-np.inf])))
    peaks = peaks - 1
    if len(peaks) == 0:
        return None
    best = float(scores[peaks].max())
    eligible = [int(k) for k in peaks if scores[k] >= WITHIN_BEST * best]
    candidates = sorted(((float(grid[k]), float(scores[k])) for k in eligible), reverse=True)

    tatum = candidates[0][0]
    if beat_period_s and beat_period_s > 0:
        def off_beat(c: tuple[float, float]) -> tuple[float, float]:
            ratio = beat_period_s / c[0]
            nearest = min(BEAT_MULTIPLES, key=lambda m: abs(ratio - m))
            return (round(abs(ratio - nearest) / nearest, 2), -c[0])
        tatum = min(candidates, key=off_beat)[0]

    positions = grid_positions(np.diff(onsets), tatum)
    tatum = _refine(onsets, positions, tatum)
    share = float(np.mean(remainders(iois, tatum) <= float(cfg["free_time_fit_tolerance"])))
    return TatumFit(tatum_s=tatum, score=_score(iois, weights, tatum), share=share,
                    positions=positions, candidates=candidates)


def beat_period(beat_times, beat_confidence) -> Optional[float]:
    """Median beat period of a beat tracker's output, or None when it is not to be trusted."""
    if beat_times is None or len(beat_times) < 3:
        return None
    if beat_confidence is not None and beat_confidence < GOOD_BEAT_CONFIDENCE:
        return None
    period = float(np.median(np.diff(np.sort(np.asarray(beat_times, dtype=float)))))
    return period if period > 0 else None


def beat_from_tatum(tatum_s: float, tracker_period_s: Optional[float] = None) -> float:
    """The beat as a whole number of tatums: the tracker's when given, else the binary
    multiple (1, 2 or 4 tatums) whose tempo is nearest ``PREFERRED_BPM``."""
    if tracker_period_s:
        multiple = min(BEAT_MULTIPLES, key=lambda m: abs(tracker_period_s / tatum_s - m))
        return tatum_s * multiple
    return min((tatum_s * k for k in (1, 2, 4)),
               key=lambda beat: abs(np.log2(60.0 / beat / PREFERRED_BPM)))
