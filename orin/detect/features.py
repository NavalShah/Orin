"""Style features for auto-detect (spec 8.1, step 1).

``FormatMeta.detect_priors`` maps a field name below to ``(ideal_lo, ideal_hi, weight)``.
The default ``score_fit`` gives full credit inside the range and decays outside it.

Honest limit (spec 8.3): on a short single melody line these features separate families
(fixed 12-step pitch, neutral-degree modes, heavily oscillated lines) far better than
they separate neighbours such as Arabic and Persian, or classical, pop and rock.
"""
from __future__ import annotations

from typing import Optional

from pydantic import Field

from ..ir.models import Model


class StyleFeatures(Model):
    duration_s: float = 0.0
    # Share of stable-note time near 150, 350, 850 or 1050 cents, after refining the tonic.
    neutral_mass: float = 0.0
    # RMS distance of stable note anchors from the best-fitting 100-cent grid.
    grid12_rms_cents: float = 0.0
    # Share of note time carrying an oscillation wider than a vibrato.
    oscillation_share: float = 0.0
    # Share of voiced frames that are moving between pitches.
    glide_share: float = 0.0
    ornament_density: float = 0.0  # ornaments per second of sounding time
    drone: float = 0.0  # 0..1 confidence that a drone is present
    free_time: float = 0.0  # 1 when no steady pulse was found
    swing_ratio: Optional[float] = None
    spectral_flatness: float = 0.0  # crude distortion cue
    tempo_bpm: Optional[float] = None
    # Best mode-coverage score per format id, from mode inference run against that format.
    mode_fit: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Extraction (spec 8.1, step 1). Everything below reads arrays and notes; the
# model above is the contract and is not changed here.
# ---------------------------------------------------------------------------

NEUTRAL_DEGREES_CENTS = (150.0, 350.0, 850.0, 1050.0)
NEUTRAL_WINDOW_CENTS = 25.0  # half the gap between a neutral degree and its 12-step neighbours
OSCILLATING_NOTE_COVERAGE = 0.5  # a note this covered by an oscillation has no stable pitch


def pitch_features(anchors_cents, durations_s, stable=None) -> tuple[float, float]:
    """(neutral_mass, grid12_rms_cents) of note anchors measured from the tonic.

    Only stable notes count (all notes when none is stable). The tonic is refined first:
    when enough stable note time sits on it, the median of those notes becomes zero.
    """
    import numpy as np

    from .. import config

    a = np.asarray(list(anchors_cents), dtype=float)
    w = np.clip(np.asarray(list(durations_s), dtype=float), 0.0, None)
    ok = np.isfinite(a) & np.isfinite(w) & (w > 0)
    if stable is not None:
        s = np.asarray(list(stable), dtype=bool)
        if (ok & s).any():
            ok &= s
    a, w = a[ok], w[ok]
    if len(a) == 0:
        return 0.0, 0.0

    folded = ((a + 600.0) % 1200.0) - 600.0
    on_tonic = np.abs(folded) <= NEUTRAL_WINDOW_CENTS
    if w[on_tonic].sum() >= float(config.section("tonic")["refine_min_stable_s"]):
        order = np.argsort(folded[on_tonic])
        cum = np.cumsum(w[on_tonic][order])
        a = a - folded[on_tonic][order][int(np.searchsorted(cum, 0.5 * cum[-1]))]

    pc = a % 1200.0
    near = np.zeros(len(pc), dtype=bool)
    for degree in NEUTRAL_DEGREES_CENTS:
        near |= np.abs(((pc - degree + 600.0) % 1200.0) - 600.0) <= NEUTRAL_WINDOW_CENTS
    neutral_mass = float(w[near].sum() / w.sum())

    offsets = np.arange(100.0)
    dev = ((a[None, :] - offsets[:, None] + 50.0) % 100.0) - 50.0
    grid12_rms = float(np.sqrt((w[None, :] * dev ** 2).sum(axis=1) / w.sum()).min())
    return neutral_mass, grid12_rms


def mode_fit_by_format(anchors_cents, durations_s) -> dict[str, float]:
    """Best mode score per registered format, 0..1 (spec 8.1: micro-pitch fit to a style's degrees)."""
    from ..formats import registry
    from ..mode.infer import folded_histogram, score_modes

    hist = folded_histogram(anchors_cents, durations_s)
    out: dict[str, float] = {}
    if hist.sum() <= 0:
        return out
    for meta in registry.list_meta():
        scores = score_modes(hist, [meta])
        if scores:
            out[meta.id] = float(min(1.0, max(0.0, scores[0].score)))
    return out


def spectral_flatness(audio, sr: int) -> float:
    """Median spectral flatness of the loud frames, 0..1. A crude distortion cue: near 0
    for a clean tone, higher for a distorted or noisy one."""
    import numpy as np
    from scipy.signal import stft

    y = np.asarray(audio, dtype=float)
    if y.ndim > 1:
        y = y.mean(axis=-1)
    n_fft = 2048
    if len(y) < n_fft or not sr:
        return 0.0
    freqs, _, spec = stft(y, fs=sr, nperseg=n_fft, noverlap=n_fft * 3 // 4, boundary=None, padded=False)
    power = np.abs(spec[(freqs >= 100.0) & (freqs <= 8000.0)]) ** 2 + 1e-12
    energy = power.sum(axis=0)
    loud = energy > 0.05 * float(energy.max())
    if not loud.any():
        return 0.0
    flat = np.exp(np.log(power[:, loud]).mean(axis=0)) / power[:, loud].mean(axis=0)
    return float(np.clip(np.median(flat), 0.0, 1.0))


def extract_features(*, t, cents, conf, segments, tonic, rhythm=None, audio=None, sr=None,
                     drone_confidence: float = 0.0, mode_fit: Optional[dict[str, float]] = None) -> StyleFeatures:
    """Style features from a contour and its annotated notes (``segment.ornaments.annotate``).

    ``rhythm`` is a RhythmResult, ``audio``/``sr`` the mono signal for the timbre cue.
    ``mode_fit`` is computed here against every registered format when not supplied.
    """
    import numpy as np

    t = np.asarray(t, dtype=float)
    cents = np.asarray(cents, dtype=float)
    hop = float(np.median(np.diff(t))) if len(t) > 1 else 0.01
    voiced_frames = int(np.isfinite(cents).sum())
    if audio is not None and sr:
        duration = len(audio) / float(sr)
    else:
        duration = float(t[-1] - t[0] + hop) if len(t) else 0.0

    anchors = [float(s.anchor_cents) for s in segments]
    durations = [float((s.end - s.start) * hop) for s in segments]
    sounding = float(sum(durations))
    osc_time, glide_frames, n_ornaments = 0.0, 0, 0
    stable: list[bool] = []
    for seg, dur in zip(segments, durations):
        osc_cover = 0.0
        for o in seg.ornaments:
            n_ornaments += 1
            if o.kind == "oscillation":
                osc_cover = max(osc_cover, float(o.coverage))
            elif o.kind == "glide":
                glide_frames += (o.contour_ref[1] - o.contour_ref[0]) if o.contour_ref else int(
                    round(o.coverage * (seg.end - seg.start)))
        osc_time += osc_cover * dur
        stable.append(osc_cover < OSCILLATING_NOTE_COVERAGE)

    neutral_mass, grid12_rms = pitch_features(anchors, durations, stable)
    drone = float(drone_confidence or 0.0)
    if tonic is not None and getattr(tonic, "method", None) == "drone":
        drone = max(drone, float(tonic.confidence))
    if mode_fit is None:
        mode_fit = mode_fit_by_format(anchors, durations)

    return StyleFeatures(
        duration_s=max(0.0, duration),
        neutral_mass=neutral_mass,
        grid12_rms_cents=grid12_rms,
        oscillation_share=min(1.0, osc_time / sounding) if sounding > 0 else 0.0,
        glide_share=min(1.0, glide_frames / voiced_frames) if voiced_frames else 0.0,
        ornament_density=n_ornaments / sounding if sounding > 0 else 0.0,
        drone=min(1.0, max(0.0, drone)),
        free_time=1.0 if rhythm is not None and rhythm.tempo.free_time else 0.0,
        swing_ratio=rhythm.cycle.swing_ratio if rhythm is not None else None,
        spectral_flatness=spectral_flatness(audio, sr) if audio is not None and sr else 0.0,
        tempo_bpm=rhythm.tempo.bpm if rhythm is not None else None,
        mode_fit=dict(mode_fit),
    )
