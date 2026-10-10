"""Style auto-detect scoring (spec 8): a heuristic helper, not an oracle.

Each format states priors over the style features (``FormatMeta.detect_priors``).
``default_score_fit`` turns them into a 0..1 fit, ``rank_formats`` returns the top three
with scores that sum to 1, and ``decide`` says when the UI should ask the user instead
of trusting the top one. The result is never a bare single label (spec 8.2).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from .. import config
from ..formats import registry
from ..formats.base import FormatMeta
from ..ir.models import IR, FormatCandidate
from .features import OSCILLATING_NOTE_COVERAGE, StyleFeatures, mode_fit_by_format, pitch_features

TOP_K = 3
MODE_FIT_BLEND = 0.25  # share of the fit taken from the best mode match, when known
SHARPNESS = 0.15  # softmax temperature that turns fits into shares; smaller is more decisive
NO_PRIORS_FIT = 0.5

# How far outside its ideal range a feature may be before the credit has mostly gone
# (the width of the smooth decay), in the feature's own unit.
DECAY = {
    "neutral_mass": 0.1,
    "oscillation_share": 0.1,
    "glide_share": 0.1,
    "ornament_density": 1.0,
    "drone": 0.25,
    "free_time": 0.5,
    "grid12_rms_cents": 15.0,
    "spectral_flatness": 0.1,
    "swing_ratio": 0.3,
    "tempo_bpm": 30.0,
    "duration_s": 10.0,
}

_MIN_S = f"{float(config.section('detect')['min_duration_s']):g}"

# Shown in the UI help (spec 8.3).
HONEST_LIMITS: list[str] = [
    "Auto-detect is a helper, not an oracle. It shows its closest matches with scores, and you can always choose the style yourself.",
    "Arabic and Persian music cannot be told apart from a short single melody line.",
    "Western major, minor and Phrygian cannot be told apart from maqam Ajam, Nahawand and Kurd, or from the Carnatic ragas with the same notes, when the melody is played plainly.",
    "Classical, pop and rock cannot be told apart from a single melody line: they use the same twelve pitches.",
    f"Anything under about {_MIN_S} seconds is too short to judge, so Orin asks you to choose.",
    "Fusion and crossover music can match several styles at once. Orin reports that instead of picking one.",
]


def _credit(value: float, lo: float, hi: float, decay: float) -> float:
    """1 inside [lo, hi], falling off smoothly (a Gaussian shoulder) outside."""
    if lo <= value <= hi:
        return 1.0
    distance = lo - value if value < lo else value - hi
    return math.exp(-0.5 * (distance / decay) ** 2)


def features_from_ir(ir: IR) -> tuple[StyleFeatures, set[str]]:
    """What an IR's events can say, and the names of the features they cannot."""
    anchors = [e.pitch_cents for e in ir.events]
    durations = [max(0.0, e.offset_s - e.onset_s) for e in ir.events]
    sounding = sum(durations)
    osc_time = glide_time = 0.0
    n_ornaments = 0
    stable: list[bool] = []
    for e, dur in zip(ir.events, durations):
        osc_cover = 0.0
        for o in (e.ornament, *e.extra_ornaments):
            if o.kind is None:
                continue
            n_ornaments += 1
            if o.kind == "oscillation":
                osc_cover = max(osc_cover, o.coverage)
            elif o.kind == "glide":
                glide_time += o.coverage * dur
        osc_time += osc_cover * dur
        stable.append(osc_cover < OSCILLATING_NOTE_COVERAGE)
    neutral_mass, grid12_rms = pitch_features(anchors, durations, stable)
    unknown = {"spectral_flatness"}
    if ir.source.kind == "notes":
        unknown.add("drone")
    features = StyleFeatures(
        duration_s=ir.duration_s,
        neutral_mass=neutral_mass,
        grid12_rms_cents=grid12_rms,
        oscillation_share=min(1.0, osc_time / sounding) if sounding > 0 else 0.0,
        glide_share=min(1.0, glide_time / sounding) if sounding > 0 else 0.0,
        ornament_density=n_ornaments / sounding if sounding > 0 else 0.0,
        drone=ir.tonic.confidence if ir.tonic.method == "drone" else 0.0,
        free_time=1.0 if ir.tempo.free_time else 0.0,
        swing_ratio=ir.cycle.swing_ratio,
        tempo_bpm=ir.tempo.bpm,
        mode_fit=mode_fit_by_format(anchors, durations),
    )
    return features, unknown


def default_score_fit(meta: FormatMeta, ir: Optional[IR], features: Optional[StyleFeatures] = None) -> float:
    """0..1: how well the features sit inside ``meta.detect_priors``. ``Adapter.score_fit`` calls this.

    Each prior is feature -> (lo, hi, weight): full credit inside [lo, hi], decaying
    smoothly outside; the fit is the weighted mean, blended with the format's best mode
    match when ``features.mode_fit`` has one. Without ``features`` they are computed from
    the IR's events, and priors on what an IR cannot show are skipped.
    """
    unknown: set[str] = set()
    if features is None:
        if ir is None:
            return 0.0
        features, unknown = features_from_ir(ir)
    total = weight_sum = 0.0
    for name, (lo, hi, weight) in meta.detect_priors.items():
        value = getattr(features, name, None)
        if name in unknown or weight <= 0 or not isinstance(value, (int, float)):
            continue
        decay = DECAY.get(name) or max(0.5 * (hi - lo), 1e-6)
        total += weight * _credit(float(value), lo, hi, decay)
        weight_sum += weight
    fit = total / weight_sum if weight_sum > 0 else NO_PRIORS_FIT
    mode_fit = features.mode_fit.get(meta.id)
    if mode_fit is not None:
        fit = (1.0 - MODE_FIT_BLEND) * fit + MODE_FIT_BLEND * min(1.0, max(0.0, float(mode_fit)))
    return float(min(1.0, max(0.0, fit)))


def rank_formats(features: StyleFeatures) -> tuple[FormatCandidate, ...]:
    """The three best-fitting formats, best first, with scores that sum to 1.

    The scores are shares among the candidates shown, not probabilities.
    """
    fits: list[tuple[str, float]] = []
    for adapter in registry.all_formats().values():
        try:
            fit = float(adapter.score_fit(None, features))  # type: ignore[arg-type]
        except Exception:  # an adapter with its own score_fit that needs an IR
            fit = default_score_fit(adapter.META, None, features)
        fits.append((adapter.id, fit))
    if not fits:
        return ()
    best = max(fit for _, fit in fits)
    shares = [(fid, math.exp((fit - best) / SHARPNESS)) for fid, fit in fits]
    shares.sort(key=lambda s: -s[1])  # stable: the registry's order breaks ties
    top = shares[:TOP_K]
    total = sum(share for _, share in top)
    return tuple(FormatCandidate(format_id=fid, score=share / total) for fid, share in top)


def decide(candidates: Sequence[FormatCandidate], duration_s: float) -> tuple[bool, Optional[str]]:
    """(ask the user?, message). True when the top score is low, the top two are close,
    or the clip is too short; the UI then prefills the dropdown with the top candidate."""
    cfg = config.section("detect")
    if not candidates:
        return True, "Not sure - please choose a style."
    top = candidates[0].score
    second = candidates[1].score if len(candidates) > 1 else 0.0
    reasons: list[str] = []
    if duration_s < float(cfg["min_duration_s"]):
        reasons.append(f"the clip is shorter than {_MIN_S} seconds")
    if top < float(cfg["min_confidence"]):
        reasons.append("no style stands out")
    elif top - second < float(cfg["min_margin"]):
        reasons.append("the two closest styles score almost the same")
    if not reasons:
        return False, None
    return True, f"Not sure - please choose. Auto-detect could not decide because {' and '.join(reasons)}."
