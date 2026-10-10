"""Mode inference (spec 7.12): closest matches, never a certain answer.

A duration-weighted histogram of note anchors, folded to one octave and measured from
the tonic, is scored against every mode of the candidate formats:

    score = coverage - out_of_mode_weight * out_mass - unused_degree_weight * unused

A match here is a match on PITCHES. A raga is also its phrases and gamakas, a maqam its
ajnas and paths, and none of that is modelled; several modes share one pitch set, and a
wrong tonic turns one into another. So the result always lists the top candidates with
their lookalikes, and the confidence is capped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from .. import config
from ..formats import registry
from ..formats.base import FormatMeta, ModeEntry
from ..ir.models import Mode, ModeCandidate

N_BINS_OCTAVE = 1200.0
MIN_KERNEL_CENTS = 25.0  # narrower than this, the choice between two candidate ratios could pick the raga
OUT_AT_KERNELS = 2.0  # mass further than this many kernel widths from every degree is out of the mode
MIN_DEGREE_MASS = 0.02  # a degree holding less than this share of the note time is unused
TOP_K = 3
PITCH_CLASS_GAP_CENTS = 40.0  # anchors closer than this are one pitch class
MIN_PITCH_CLASSES = 5
MARGIN_FOR_FULL_CONFIDENCE = 0.2


@dataclass
class ModeScore:
    format_id: str
    mode_id: str
    score: float
    coverage: float
    out_mass: float
    unused: float


def folded_histogram(cents, weights=None, *, bin_cents: float = 10, smooth_cents: float = 15) -> np.ndarray:
    """Occupancy of pitch over one octave: 1200 / bin_cents bins (120 by default), bin 0
    centred on 0 cents, smoothed circularly by a Gaussian of ``smooth_cents`` and summing
    to 1. NaN values are skipped. All zeros when there is nothing to count."""
    n_bins = int(round(N_BINS_OCTAVE / bin_cents))
    hist = np.zeros(n_bins)
    c = np.asarray(cents, dtype=float).ravel()
    w = np.ones(len(c)) if weights is None else np.asarray(weights, dtype=float).ravel()
    ok = np.isfinite(c) & np.isfinite(w) & (w > 0)
    if not ok.any():
        return hist
    idx = np.round((c[ok] % N_BINS_OCTAVE) / bin_cents).astype(int) % n_bins
    np.add.at(hist, idx, w[ok])
    if smooth_cents > 0:
        offsets = np.arange(n_bins) * bin_cents
        offsets = np.minimum(offsets, N_BINS_OCTAVE - offsets)  # circular distance from bin 0
        kernel = np.exp(-0.5 * (offsets / smooth_cents) ** 2)
        hist = np.real(np.fft.ifft(np.fft.fft(hist) * np.fft.fft(kernel / kernel.sum())))
        hist = np.maximum(hist, 0.0)
    total = hist.sum()
    return hist / total if total > 0 else hist


def _score_mode(hist: np.ndarray, mode: ModeEntry, sigma: float) -> tuple[float, float, float, float]:
    cfg = config.section("mode")
    bin_cents = N_BINS_OCTAVE / len(hist)
    centres = np.arange(len(hist)) * bin_cents
    degrees = np.array([d.cents for d in mode.ascending], dtype=float)
    # distance of every bin from every degree, on the octave circle
    dist = np.abs(((centres[:, None] - degrees[None, :] + 600.0) % 1200.0) - 600.0)
    nearest = dist.min(axis=1)
    coverage = float((hist * np.exp(-0.5 * (nearest / sigma) ** 2)).sum())
    out_mass = float(hist[nearest > OUT_AT_KERNELS * sigma].sum())
    degree_mass = (hist[:, None] * np.exp(-0.5 * (dist / sigma) ** 2)).sum(axis=0)
    unused = float(np.mean(degree_mass < MIN_DEGREE_MASS))
    score = coverage - float(cfg["out_of_mode_weight"]) * out_mass - float(cfg["unused_degree_weight"]) * unused
    return score, coverage, out_mass, unused


def score_modes(hist: np.ndarray, metas: Sequence[FormatMeta], *, kernel_cents: float = 25) -> list[ModeScore]:
    """Every mode of ``metas`` scored against ``hist`` (relative to the tonic), best first.

    The kernel around each degree of ``mode.ascending`` is a Gaussian that never gets
    narrower than 25 cents: tuning tables are nominal, and a finer match would be read
    as evidence it cannot give.
    """
    hist = np.asarray(hist, dtype=float)
    sigma = max(float(kernel_cents), MIN_KERNEL_CENTS)
    out: list[ModeScore] = []
    if hist.sum() <= 0:
        return out
    for meta in metas:
        for mode in meta.mode_table:
            if not mode.ascending:
                continue
            score, coverage, out_mass, unused = _score_mode(hist, mode, sigma)
            out.append(ModeScore(format_id=meta.id, mode_id=mode.id, score=round(score, 4),
                                 coverage=round(coverage, 4), out_mass=round(out_mass, 4), unused=round(unused, 4)))
    out.sort(key=lambda m: -m.score)  # stable: the registry's order breaks ties
    return out


def _pitch_classes(anchors: np.ndarray) -> int:
    """How many distinct pitch classes the anchors use, clustering on the octave circle."""
    folded = np.sort(anchors[np.isfinite(anchors)] % N_BINS_OCTAVE)
    if len(folded) == 0:
        return 0
    gaps = np.diff(np.concatenate((folded, [folded[0] + N_BINS_OCTAVE])))
    return max(1, int(np.sum(gaps > PITCH_CLASS_GAP_CENTS)))


def _lookalikes(format_id: str, mode_id: str) -> tuple[str, ...]:
    try:
        return registry.lookalikes(format_id, mode_id)
    except Exception:  # a format outside the registry: no lookalikes to show
        return ()


def _resolve(mode_id: str, metas: Sequence[FormatMeta], style: Optional[str]) -> Optional[tuple[FormatMeta, ModeEntry]]:
    ref = mode_id.rstrip("~")
    fmt, _, name = ref.rpartition(":")
    ordered = sorted(metas, key=lambda m: (m.id != (fmt or style), ))
    for meta in ordered:
        if fmt and meta.id != fmt:
            continue
        mode = meta.mode(name)
        if mode is not None:
            return meta, mode
    return registry.find_mode(ref, default_format=style)


def infer_mode(anchors_cents, durations_s, oscillating, *, metas: Sequence[FormatMeta],
               style: Optional[str] = None, clip_duration_s: float = 0.0,
               mode_id: Optional[str] = None) -> Mode:
    """Closest modes for a list of notes.

    ``anchors_cents`` are relative to the tonic, ``oscillating`` flags notes whose pitch
    is carried by an oscillation (they count for less). ``style`` restricts the search to
    that format. ``mode_id`` is the user's own choice ("rast" or "arabic_maqam:rast"): it
    wins and is marked ``user_selected``; the inferred candidates are still listed.
    """
    cfg = config.section("mode")
    anchors = np.asarray(list(anchors_cents), dtype=float)
    durations = np.asarray(list(durations_s), dtype=float)
    if len(durations) != len(anchors):
        durations = np.ones(len(anchors))
    flags = np.zeros(len(anchors), dtype=bool)
    if oscillating is not None:
        given = np.asarray(list(oscillating), dtype=bool)
        flags[:min(len(given), len(flags))] = given[:len(flags)]
    weights = np.clip(durations, 0.0, None) * np.where(flags, float(cfg["oscillating_note_weight"]), 1.0)

    metas = list(metas)
    pool = [m for m in metas if m.id == style] or metas if style else metas
    tonic_cfg = config.section("tonic")
    hist = folded_histogram(anchors, weights, bin_cents=float(tonic_cfg["histogram_bin_cents"]),
                            smooth_cents=float(tonic_cfg["histogram_smooth_cents"]))
    scores = score_modes(hist, pool, kernel_cents=float(tonic_cfg["mode_kernel_cents"]))

    by_id = {m.id: m for m in pool}
    # The same pitches under another format's name are not a second match: they are
    # listed once, with the other names as lookalikes.
    distinct: list[ModeScore] = []
    same_pitches: set[str] = set()
    candidates_list: list[ModeCandidate] = []
    for s in scores:
        if f"{s.format_id}:{s.mode_id}" in same_pitches:
            continue
        looks = _lookalikes(s.format_id, s.mode_id)
        same_pitches.update(ref for ref in looks if not ref.endswith("~"))
        entry = by_id[s.format_id].mode(s.mode_id)
        distinct.append(s)
        candidates_list.append(ModeCandidate(
            id=s.mode_id, label=entry.label if entry else s.mode_id, label_system=s.format_id,
            score=float(s.score), lookalikes=looks))
        if len(distinct) == TOP_K:
            break
    candidates = tuple(candidates_list)

    if mode_id:
        found = _resolve(mode_id, metas, style)
        if found is not None:
            meta, entry = found
            return Mode(label=entry.label, label_system=meta.id, mode_id=entry.id,
                        allowed_pitches_cents=tuple(float(c) for c in entry.pitch_set()),
                        confidence=1.0, candidates=candidates, user_selected=True)
        # Unknown to the tables: keep the user's word, with no pitches to check against.
        return Mode(label=mode_id, label_system=style, mode_id=mode_id, confidence=1.0,
                    candidates=candidates, user_selected=True)

    if not distinct:
        return Mode()
    top = distinct[0]
    entry = by_id[top.format_id].mode(top.mode_id)
    margin = top.score - distinct[1].score if len(distinct) > 1 else 0.0
    confidence = max(0.0, top.score) * (0.6 + 0.4 * min(1.0, margin / MARGIN_FOR_FULL_CONFIDENCE))
    confidence = min(confidence, float(cfg["confidence_cap"]))
    duration = clip_duration_s if clip_duration_s and clip_duration_s > 0 else float(durations.sum())
    # The same pitches carry another name in another tradition (major, Ajam,
    # Shankarabharanam). With no style to say which, the name is as weak as a short clip's.
    other_names = any(not ref.endswith("~") and not ref.startswith(f"{top.format_id}:")
                      and ref.split(":", 1)[1] != top.mode_id for ref in candidates[0].lookalikes)
    if (duration < float(cfg["short_clip_s"]) or _pitch_classes(anchors) < MIN_PITCH_CLASSES
            or (len(pool) > 1 and other_names)):
        confidence = min(confidence, float(cfg["short_clip_cap"]))
    return Mode(
        label=entry.label if entry else top.mode_id, label_system=top.format_id, mode_id=top.mode_id,
        allowed_pitches_cents=tuple(float(c) for c in entry.pitch_set()) if entry else (),
        confidence=round(float(confidence), 3), candidates=candidates, user_selected=False,
    )
