"""pYIN pitch tracking (spec 7.5): the fallback tracker. No GPU and no model download.

Runs at 16 kHz with a 160-sample hop (exactly 10 ms), so its frames line up with the
CREPE tracker's. The analysis frame is 1024 samples: a longer one averages a fast
oscillation towards its centre and would flatten gamakas.
"""
from __future__ import annotations

import numpy as np

from ..analysis_types import PitchTrack
from ..config import section

FRAME_LENGTH = 1024
MIN_VOICED_PROBABILITY = 0.1


def track_pyin(y16k: np.ndarray, *, fmin: float, fmax: float) -> PitchTrack:
    import librosa

    cfg = section("pitch")
    sr, hop = int(cfg["sample_rate"]), int(cfg["hop_samples"])
    y = np.asarray(y16k, dtype=np.float32)
    n_frames = 1 + len(y) // hop
    t = np.arange(n_frames) * (hop / sr)
    if len(y) < FRAME_LENGTH or not np.any(y):
        return PitchTrack(t=t, f0=np.full(n_frames, np.nan), conf=np.zeros(n_frames), hop_s=hop / sr, tracker="pyin")

    fmax = min(float(fmax), sr / 2.0 - 1.0)
    f0, voiced, prob = librosa.pyin(y, fmin=float(fmin), fmax=fmax, sr=sr, frame_length=FRAME_LENGTH,
                                    hop_length=hop, center=True, fill_na=None)
    n = min(n_frames, len(f0))
    f0, voiced, prob = f0[:n].astype(float), voiced[:n], np.nan_to_num(prob[:n].astype(float))
    # pYIN's own voicing decision (its HMM path) is the reliable one for pitched sound; the
    # per-frame probability only ranks frames within each side of it. Voiced frames land in
    # 0.5..1 and unvoiced ones under 0.3, so every profile threshold agrees with the HMM.
    # The exception is noise: the HMM sometimes strings a "voiced" path through it, but
    # there the probability sits on its floor (0.01), far under any pitched frame (0.3 up).
    voiced = voiced & (prob >= MIN_VOICED_PROBABILITY)
    conf = np.where(voiced, 0.5 + 0.5 * prob, np.minimum(0.5 * prob, 0.3))
    f0 = np.where(np.isfinite(f0) & (f0 > 0), f0, np.nan)
    return PitchTrack(t=t[:n], f0=f0, conf=conf, hop_s=hop / sr, tracker="pyin")
