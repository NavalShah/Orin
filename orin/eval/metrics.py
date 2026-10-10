"""Evaluation metrics (spec 7.15). mir_eval does the note and onset matching.

Notes are ``(onset_s, offset_s, hz)`` tuples. Pitches are compared as sounding
frequencies, not as cents above the tonic, so a wrong tonic is scored once (by
``tonic_accuracy``) and does not also wreck the note scores.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

import numpy as np

Note = tuple[float, float, float]


def cents_between(a_hz: float, b_hz: float) -> float:
    return float(1200.0 * np.log2(a_hz / b_hz))


def tonic_error_cents(est_hz: float, ref_hz: float) -> float:
    """Distance in cents, folded to the octave: 0..600. The octave is a display choice."""
    return float(abs((cents_between(est_hz, ref_hz) + 600.0) % 1200.0 - 600.0))


def tonic_correct(est_hz: float, ref_hz: float, tolerance_cents: float = 50.0) -> bool:
    return tonic_error_cents(est_hz, ref_hz) <= tolerance_cents


def tonic_accuracy(pairs: Iterable[tuple[float, float]], tolerance_cents: float = 50.0) -> float:
    """Share of (estimated_hz, reference_hz) pairs within ``tolerance_cents``, octave-folded."""
    pairs = list(pairs)
    return float(np.mean([tonic_correct(e, r, tolerance_cents) for e, r in pairs])) if pairs else 0.0


def onset_f1(ref_onsets: Sequence[float], est_onsets: Sequence[float], window_s: float = 0.05) -> float:
    import mir_eval

    ref, est = np.sort(np.asarray(ref_onsets, dtype=float)), np.sort(np.asarray(est_onsets, dtype=float))
    if len(ref) == 0 or len(est) == 0:
        return 1.0 if len(ref) == len(est) else 0.0
    return float(mir_eval.onset.f_measure(ref, est, window=window_s)[0])


def _split(notes: Sequence[Note]) -> tuple[np.ndarray, np.ndarray]:
    notes = sorted(notes)
    intervals = np.array([[n[0], max(n[1], n[0] + 1e-3)] for n in notes], dtype=float).reshape(-1, 2)
    return intervals, np.array([n[2] for n in notes], dtype=float)


def note_f1(ref_notes: Sequence[Note], est_notes: Sequence[Note], *, onset_tolerance_s: float = 0.05,
            pitch_tolerance_cents: float = 50.0) -> float:
    """Note F1 on onset and pitch (offsets ignored, as is usual for sung material)."""
    import mir_eval

    if not ref_notes or not est_notes:
        return 1.0 if len(ref_notes) == len(est_notes) else 0.0
    ref_i, ref_p = _split(ref_notes)
    est_i, est_p = _split(est_notes)
    return float(mir_eval.transcription.precision_recall_f1_overlap(
        ref_i, ref_p, est_i, est_p, onset_tolerance=onset_tolerance_s,
        pitch_tolerance=pitch_tolerance_cents, offset_ratio=None)[2])


def median_pitch_error_cents(ref_notes: Sequence[Note], est_notes: Sequence[Note], *,
                             onset_tolerance_s: float = 0.1) -> Optional[float]:
    """Median absolute pitch error of the notes matched by onset alone. None when nothing matches."""
    import mir_eval

    if not ref_notes or not est_notes:
        return None
    ref_i, ref_p = _split(ref_notes)
    est_i, est_p = _split(est_notes)
    matched = mir_eval.transcription.match_note_onsets(ref_i, est_i, onset_tolerance=onset_tolerance_s)
    if not matched:
        return None
    return float(np.median([abs(cents_between(est_p[e], ref_p[r])) for r, e in matched]))


def topk_accuracy(ranked: Sequence[Sequence[str]], truth: Sequence[str], k: int) -> float:
    """Share of cases whose true label is among the first ``k`` of its ranked list."""
    if not truth:
        return 0.0
    return float(np.mean([t in list(r)[:k] for r, t in zip(ranked, truth)]))


def cycle_accuracy(est: Sequence[Optional[int]], ref: Sequence[Optional[int]]) -> float:
    """Share of cases with the right cycle length in pulses (None = free time, which must match too)."""
    if not ref:
        return 0.0
    return float(np.mean([e == r for e, r in zip(est, ref)]))


def accuracy(truth: Sequence[str], predicted: Sequence[Optional[str]]) -> float:
    if not truth:
        return 0.0
    return float(np.mean([t == p for t, p in zip(truth, predicted)]))


def confusion_matrix(truth: Sequence[str], predicted: Sequence[Optional[str]]) -> dict[str, dict[str, int]]:
    """{true label: {predicted label: count}}. A missing prediction is counted under "none"."""
    out: dict[str, dict[str, int]] = {}
    for t, p in zip(truth, predicted):
        row = out.setdefault(t, {})
        key = p if p is not None else "none"
        row[key] = row.get(key, 0) + 1
    return out
