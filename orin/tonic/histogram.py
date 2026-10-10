"""Tonic candidates from the melody's pitch histogram (spec 7.7, method 2).

The voiced pitch is folded to one octave in 10-cent bins. Each strong peak is a
candidate tonic, refined to the cent, and scored by how well the rest of the histogram
fits the mode tables of the chosen style (all styles under auto-detect), plus a bonus
when the phrase ends on it and a smaller one when the longest note sits on it.

Honest limit: a pitch set fits one mode from each of its notes (the five-note shape of
Mohanam is Hindolam, Shuddha Saveri and Madhyamavati from its other notes), so the mode
fit alone cannot choose between rotations. The bonuses do, and ``tonic.select`` caps
the confidence when that is all that separates the leading candidates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from ..analysis_types import PitchTrack
from ..config import section
from ..formats.base import FormatMeta

REF_HZ = 55.0  # arbitrary reference: histogram cents are measured from here until a tonic is known
STABLE_TOL_CENTS = 35.0
STABLE_MIN_S = 0.1
FINAL_NOTE_MIN_S = 0.25
SAME_CLASS_CENTS = 35.0
REFINE_WINDOW_CENTS = 40.0
PEAK_MIN_SHARE = 0.08  # of the tallest peak
UNSTABLE_WEIGHT = 0.3  # frames in glides and oscillations count less than held pitch
SALIENCE_WEIGHT = 0.1  # tie-breaker: how much of the melody sits on the candidate


def circ(a, b):
    """Signed difference a - b on the octave circle, in [-600, 600)."""
    return ((np.asarray(a, dtype=float) - b + 600.0) % 1200.0) - 600.0


@dataclass
class StableRun:
    start: int
    end: int  # exclusive
    cents: float  # median, unfolded, above REF_HZ

    @property
    def frames(self) -> int:
        return self.end - self.start


@dataclass
class MelodyProfile:
    cents: np.ndarray  # unfolded cents above REF_HZ; NaN = unvoiced
    weights: np.ndarray
    hop_s: float
    runs: list[StableRun] = field(default_factory=list)
    stable: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    median_cents: float = float("nan")
    voiced_s: float = 0.0
    final_pc: Optional[float] = None  # folded pitch of the last stable note of 250 ms or more
    longest_pc: Optional[float] = None


@dataclass
class Candidate:
    pc_cents: float  # folded cents above REF_HZ, refined
    mode_fit: float = 0.0
    bonus: float = 0.0
    salience: float = 0.0
    best_mode: Optional[tuple[str, str]] = None  # (format id, mode id)
    refined: bool = False  # True when enough stable frames sat on it to measure it

    @property
    def score(self) -> float:
        return self.mode_fit + self.bonus + SALIENCE_WEIGHT * self.salience


def stable_runs(cents: np.ndarray, hop_s: float, *, tol: float = STABLE_TOL_CENTS,
                min_s: float = STABLE_MIN_S) -> list[StableRun]:
    """Stretches where the pitch holds within ``tol`` of its own running mean."""
    out: list[StableRun] = []
    min_frames = max(2, int(round(min_s / hop_s)))
    n = len(cents)
    i = 0
    while i < n:
        if not np.isfinite(cents[i]):
            i += 1
            continue
        start, total, count = i, 0.0, 0
        while i < n and np.isfinite(cents[i]) and (count == 0 or abs(cents[i] - total / count) <= tol):
            total += cents[i]
            count += 1
            i += 1
        if count >= min_frames:
            out.append(StableRun(start, i, float(np.median(cents[start:i]))))
    return out


def melody_profile(track: PitchTrack) -> MelodyProfile:
    f0 = np.asarray(track.f0, dtype=float)
    cents = np.full(len(f0), np.nan)
    ok = np.isfinite(f0) & (f0 > 0)
    cents[ok] = 1200.0 * np.log2(f0[ok] / REF_HZ)
    hop = float(track.hop_s)
    runs = stable_runs(cents, hop)
    stable = np.zeros(len(cents), dtype=bool)
    for r in runs:
        stable[r.start:r.end] = True
    weights = np.where(ok, np.where(stable, 1.0, UNSTABLE_WEIGHT), 0.0)
    prof = MelodyProfile(cents=cents, weights=weights, hop_s=hop, runs=runs, stable=stable,
                         median_cents=float(np.median(cents[ok])) if ok.any() else float("nan"),
                         voiced_s=float(ok.sum() * hop))
    long_runs = [r for r in runs if r.frames * hop >= FINAL_NOTE_MIN_S - 1e-9]
    if long_runs:
        prof.final_pc = float(long_runs[-1].cents % 1200.0)
    if runs:
        prof.longest_pc = float(max(runs, key=lambda r: r.frames).cents % 1200.0)
    return prof


# ---------------------------------------------------------------------------
# Histogram and mode fit. orin.mode.infer owns both; the versions here are the
# stand-ins used when that package cannot be imported.
# ---------------------------------------------------------------------------

def _own_histogram(cents: np.ndarray, weights: Optional[np.ndarray], bin_cents: float, smooth_cents: float) -> np.ndarray:
    n_bins = int(round(1200.0 / bin_cents))
    c = np.asarray(cents, dtype=float)
    ok = np.isfinite(c)
    w = np.ones(len(c)) if weights is None else np.asarray(weights, dtype=float)
    idx = np.round((c[ok] % 1200.0) / bin_cents).astype(int) % n_bins
    hist = np.bincount(idx, weights=w[ok], minlength=n_bins).astype(float)
    if smooth_cents > 0 and hist.sum() > 0:
        sigma = smooth_cents / bin_cents
        half = int(np.ceil(3 * sigma))
        k = np.exp(-0.5 * (np.arange(-half, half + 1) / sigma) ** 2)
        k /= k.sum()
        hist = np.convolve(np.concatenate([hist[-half:], hist, hist[:half]]), k, mode="same")[half:-half]
    total = hist.sum()
    return hist / total if total > 0 else hist


def histogram(cents: np.ndarray, weights: Optional[np.ndarray] = None) -> np.ndarray:
    """120 bins that sum to 1; bin 0 is centred on 0 cents."""
    cfg = section("tonic")
    bin_cents, smooth = float(cfg["histogram_bin_cents"]), float(cfg["histogram_smooth_cents"])
    try:
        from ..mode.infer import folded_histogram
    except ImportError:
        return _own_histogram(cents, weights, bin_cents, smooth)
    return np.asarray(folded_histogram(cents, weights, bin_cents=bin_cents, smooth_cents=smooth), dtype=float)


def _own_mode_fit(hist: np.ndarray, metas: Sequence[FormatMeta], kernel_cents: float) -> tuple[float, Optional[tuple[str, str]]]:
    cfg = section("mode")
    n = len(hist)
    centres = np.arange(n) * (1200.0 / n)
    best, best_ref = 0.0, None
    for meta in metas:
        for mode in meta.mode_table:
            pitches = np.array(mode.pitch_set())
            near = np.abs(circ(centres[:, None], pitches[None, :])) <= kernel_cents  # bins x degrees
            inside = float(hist[near.any(axis=1)].sum())
            used = (near * hist[:, None]).sum(axis=0) > 0.01
            score = inside - cfg["out_of_mode_weight"] * (1.0 - inside) - cfg["unused_degree_weight"] * float(np.mean(~used))
            if score > best:
                best, best_ref = score, (meta.id, mode.id)
    return best, best_ref


def mode_fit(hist: np.ndarray, metas: Sequence[FormatMeta]) -> tuple[float, Optional[tuple[str, str]]]:
    """Best mode score for a histogram whose bin 0 is the candidate tonic, and which mode gave it."""
    kernel = float(section("tonic")["mode_kernel_cents"])
    try:
        from ..mode.infer import score_modes
    except ImportError:
        return _own_mode_fit(hist, metas, kernel)
    scores = score_modes(hist, list(metas), kernel_cents=kernel)
    if not scores:
        return 0.0, None
    top = scores[0]
    return float(top.score), (top.format_id, top.mode_id)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def refine(prof: MelodyProfile, pc_cents: float) -> tuple[float, bool]:
    """REFINE TO THE CENT: the median of the stable frames on this pitch class (at least
    ``refine_min_stable_s`` of them); failing that, a fit of everything voiced within
    40 cents. A 20-cent tonic error flips quarter-tone decisions later, so the bin
    centre is never good enough. Returns (pitch class, measured from stable frames)."""
    min_stable_s = float(section("tonic")["refine_min_stable_s"])
    ok = np.isfinite(prof.cents)
    diff = np.full(len(prof.cents), np.nan)
    diff[ok] = circ(prof.cents[ok], pc_cents)
    near = ok & (np.abs(diff) <= REFINE_WINDOW_CENTS)
    on_stable = near & prof.stable
    if on_stable.sum() * prof.hop_s >= min_stable_s - 1e-9:
        return float((pc_cents + np.median(diff[on_stable])) % 1200.0), True
    if near.sum() >= 5:
        return float((pc_cents + np.median(diff[near])) % 1200.0), False
    return float(pc_cents % 1200.0), False


def score_class(prof: MelodyProfile, pc_cents: float, metas: Sequence[FormatMeta], *,
                salience: float = 0.0, refined: bool = False) -> Candidate:
    """Score one pitch class as the tonic (also used to compare readings of a drone)."""
    cfg = section("tonic")
    fit, ref = mode_fit(histogram(prof.cents - pc_cents, prof.weights), metas)
    bonus = 0.0
    if prof.final_pc is not None and abs(float(circ(prof.final_pc, pc_cents))) <= SAME_CLASS_CENTS:
        bonus += float(cfg["final_note_bonus"])
    if prof.longest_pc is not None and abs(float(circ(prof.longest_pc, pc_cents))) <= SAME_CLASS_CENTS:
        bonus += float(cfg["longest_note_bonus"])
    return Candidate(pc_cents=float(pc_cents % 1200.0), mode_fit=fit, bonus=bonus, salience=salience,
                     best_mode=ref, refined=refined)


def histogram_candidates(prof: MelodyProfile, metas: Sequence[FormatMeta]) -> list[Candidate]:
    """Candidate tonics from the histogram's strongest peaks, best first."""
    cfg = section("tonic")
    if prof.voiced_s <= 0:
        return []
    hist = histogram(prof.cents, prof.weights)
    n = len(hist)
    if n == 0 or hist.max() <= 0:
        return []
    bin_cents = 1200.0 / n
    left, right = np.roll(hist, 1), np.roll(hist, -1)
    peaks = np.flatnonzero((hist > left) & (hist >= right) & (hist >= PEAK_MIN_SHARE * hist.max()))
    peaks = sorted(peaks, key=lambda b: hist[b], reverse=True)[: int(cfg["max_candidates"])]

    out: list[Candidate] = []
    for b in peaks:
        pc, measured = refine(prof, b * bin_cents)
        if any(abs(float(circ(pc, c.pc_cents))) <= SAME_CLASS_CENTS for c in out):
            continue
        out.append(score_class(prof, pc, metas, salience=float(hist[b] / hist.max()), refined=measured))
    out.sort(key=lambda c: c.score, reverse=True)
    return out
