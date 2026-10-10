"""The fidelity score (spec 9.3). Additive, so every loss item shows what it cost.

    err_i        = a mode degree: cents from the source anchor to that degree's tolerance window;
                   a rounded grid pitch: 50 * distance / (half the gap between the two writable
                   pitches around the anchor), so the midpoint between two pitches is always 50.
                   The conversion engine computes it (orin/formats/engine.py).
    pitch_loss_i = 0.5 if octave-folded else clip((err_i - 25) / 25, 0, 1)
    orn_loss_i   = clip(half_extent_i / 100, 0.1, 1) * coverage_i * k_i
                   (k: 1 dropped, 0.5 approximated, 0 kept)
    time_loss_i  = clip(|onset_shift_i| / (0.5 * grid_s), 0, 1)
    S_x          = 1 - sum(d_i * loss_x_i) / sum(d_i)          (d_i = note duration)
    F            = 0.45 * S_pitch + 0.35 * S_ornament + 0.20 * S_timing

A free-time source forced into bars caps S_timing at 0.5.
The weights are a design choice, not a measurement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

WEIGHTS = {"pitch": 0.45, "ornament": 0.35, "timing": 0.20}

# A pitch change counts against fidelity only beyond this many cents outside the
# target degree's tolerance window. Every nominal just-vs-equal difference is smaller,
# so a change of tuning system alone never lowers the score.
PITCH_FREE_CENTS = 25.0
PITCH_FULL_CENTS = 50.0
FREE_TIME_TIMING_CAP = 0.5

HOW_SCORED = (
    "Fidelity runs from 0 to 1 and is built from three parts. "
    "Pitch (45%): how much sounding time was moved more than 25 cents away from what was performed. "
    "Ornament (35%): how much sounding time lost its pitch movement, weighted by how wide the movement was; "
    "a movement kept as a simple mark counts half. "
    "Timing (20%): how far note starts were moved to fit the target grid; "
    "writing unmetered music in bars caps this part at one half. "
    "A change of tuning system alone does not lower the score. "
    "The weights are Orin's choice, not a measurement."
)


@dataclass(frozen=True)
class NoteLoss:
    """What one note lost. Built by the conversion engine, one per written note."""

    event_id: str
    duration_s: float
    err_cents: float = 0.0  # 0 when the anchor is inside the target degree's tolerance
    octave_folded: bool = False
    orn_half_extent: float = 0.0
    orn_coverage: float = 0.0
    orn_k: float = 0.0  # 1 dropped, 0.5 approximated, 0 kept or no ornament
    onset_shift_s: float = 0.0
    grid_s: Optional[float] = None  # None = no grid, so no timing loss


@dataclass(frozen=True)
class NotePenalty:
    pitch: float
    ornament: float
    timing: float


@dataclass(frozen=True)
class FidelityResult:
    pitch: float
    ornament: float
    timing: float
    overall: float
    penalties: dict[str, NotePenalty]  # by event id; overall = 1 - sum(all) - free_time_penalty
    free_time_penalty: float


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def pitch_loss(n: NoteLoss) -> float:
    if n.octave_folded:
        return 0.5
    span = PITCH_FULL_CENTS - PITCH_FREE_CENTS
    return _clip((abs(n.err_cents) - PITCH_FREE_CENTS) / span, 0.0, 1.0)


def ornament_loss(n: NoteLoss) -> float:
    if n.orn_k <= 0 or n.orn_coverage <= 0:
        return 0.0
    return _clip(n.orn_half_extent / 100.0, 0.1, 1.0) * _clip(n.orn_coverage, 0.0, 1.0) * n.orn_k


def timing_loss(n: NoteLoss) -> float:
    if not n.grid_s or n.grid_s <= 0:
        return 0.0
    return _clip(abs(n.onset_shift_s) / (0.5 * n.grid_s), 0.0, 1.0)


def score(notes: Sequence[NoteLoss], *, free_time_measured: bool = False) -> FidelityResult:
    total = sum(max(n.duration_s, 0.0) for n in notes)
    if total <= 0:
        return FidelityResult(1.0, 1.0, 1.0, 1.0, {}, 0.0)

    penalties: dict[str, NotePenalty] = {}
    sums = {"pitch": 0.0, "ornament": 0.0, "timing": 0.0}
    for n in notes:
        share = max(n.duration_s, 0.0) / total
        parts = {"pitch": pitch_loss(n) * share, "ornament": ornament_loss(n) * share, "timing": timing_loss(n) * share}
        for k, v in parts.items():
            sums[k] += v
        penalties[n.event_id] = NotePenalty(*(WEIGHTS[k] * parts[k] for k in ("pitch", "ornament", "timing")))

    s_pitch, s_orn, s_time = (1.0 - sums[k] for k in ("pitch", "ornament", "timing"))
    free_penalty = 0.0
    if free_time_measured and s_time > FREE_TIME_TIMING_CAP:
        free_penalty = WEIGHTS["timing"] * (s_time - FREE_TIME_TIMING_CAP)
        s_time = FREE_TIME_TIMING_CAP
    overall = WEIGHTS["pitch"] * s_pitch + WEIGHTS["ornament"] * s_orn + WEIGHTS["timing"] * s_time
    return FidelityResult(_clip(s_pitch, 0, 1), _clip(s_orn, 0, 1), _clip(s_time, 0, 1), _clip(overall, 0, 1), penalties, free_penalty)
