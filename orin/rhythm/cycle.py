"""Cycle (tala, iqa', meter) suggestion (spec 7.11).

Duration-weighted onsets are folded over a template's length and correlated with its
accent pattern. This is a SUGGESTION: a melody alone cannot prove a tala or an iqa'
(Maqsum, Baladi and Sa'idi differ only in drum strokes), so the caller caps the
confidence at ``cycle_confidence_cap`` and shows every template that was tried.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

FULL_CREDIT_CYCLES = 2.0  # fewer repetitions of the cycle than this scale the score down
TIE = 0.03  # scores this close are a tie; the earlier template (the format's default) wins
MIN_EVIDENCE = 0.05  # under this no cycle is suggested at all


def accents_per_pulse(template) -> np.ndarray:
    """The template's accent pattern as one weight per pulse."""
    length = int(template.length_pulses)
    pattern = np.asarray(template.accent_pattern, dtype=float)
    out = np.zeros(length)
    if len(pattern) == length:
        return pattern.copy()
    if len(pattern) == template.cycle_beats:
        out[::template.subdivision] = pattern
    elif length:
        out[0] = 1.0  # no usable pattern: only the first beat is marked
    return out


def _without_beat_shape(per_pulse: np.ndarray, template) -> np.ndarray:
    """Remove what repeats every beat. Notes falling on beats rather than between them
    is true of every cycle, so it is not evidence for any one of them."""
    grid = per_pulse.reshape(template.cycle_beats, template.subdivision)
    return (grid - grid.mean(axis=0, keepdims=True)).ravel()


def score_template(onsets_s: np.ndarray, weights: np.ndarray, template, pulse_s: float,
                   end_s: Optional[float] = None, phase_pulses: float = 0.0) -> float:
    """0..1: how well the folded onsets follow the template's accents.

    The correlation between the two, scaled by how much of the folded shape differs from
    beat to beat at all, by how well the notes sit on the pulse grid and by how many
    times the cycle repeats. Evenly spaced notes score 0 for every template: they say
    nothing about the cycle. ``end_s`` is where the last note ends; only whole cycles
    are folded.
    """
    length = int(template.length_pulses)
    if length < 2 or len(onsets_s) < 2 or pulse_s <= 0:
        return 0.0
    onsets_s = np.asarray(onsets_s, dtype=float)
    start = float(onsets_s.min())
    x = (onsets_s - start) / pulse_s + phase_pulses
    nearest = np.round(x)
    on_grid = 1.0 - 2.0 * float(np.average(np.abs(x - nearest), weights=weights))
    extent = max(float(x.max()) + 1.0, ((end_s - start) / pulse_s + phase_pulses) if end_s is not None else 0.0)
    repeats = extent / length
    whole = int(np.floor(repeats + 0.05))
    keep = nearest < whole * length if whole >= 1 else np.ones(len(x), dtype=bool)
    if not keep.any():
        return 0.0
    folded = np.bincount(nearest[keep].astype(int) % length, weights=weights[keep], minlength=length)
    total = float((folded ** 2).sum())
    varying = _without_beat_shape(folded, template)
    accents = _without_beat_shape(accents_per_pulse(template), template)
    norm = float(np.sqrt((varying ** 2).sum() * (accents ** 2).sum()))
    if total <= 0 or norm < 1e-12:
        return 0.0
    corr = float((varying * accents).sum()) / norm
    distinct = float(np.sqrt((varying ** 2).sum() / total))  # 0 when every beat looks the same
    return float(max(0.0, corr) * distinct * max(0.0, on_grid) * min(1.0, repeats / FULL_CREDIT_CYCLES))


def pick(scored: Sequence[tuple[object, float]]) -> Optional[tuple[object, float]]:
    """Best (template, score), or None when nothing shows real evidence. ``scored`` is in
    the caller's order, which breaks ties."""
    if not scored:
        return None
    top = max(score for _, score in scored)
    if top < MIN_EVIDENCE:
        return None
    return next((tp, score) for tp, score in scored if score >= top - TIE)
