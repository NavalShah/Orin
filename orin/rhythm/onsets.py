"""Onsets (spec 7.11): note onsets from the segmentation, inter-onset intervals, and an
onset-strength envelope from audio (spectral flux plus energy rise).

Sung legato lines have weak energy onsets, so the pulse search works on NOTE onsets
(pitch-change onsets from 7.9). The envelope is for ``segment_contour(onset_env=...)``,
which uses it to split re-articulated notes of the same pitch.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

MIN_WEIGHT_S = 0.05
MAX_WEIGHT_S = 1.0
WINDOW_HOPS = 4  # analysis window = 4 hops (40 ms at the default hop)
LOG_COMPRESSION = 100.0  # log1p(100 * magnitude / peak): quiet partials still count
FLOOR_DB = 80.0  # energy floor under the loudest frame
DIP_LOOK_BACK_S = 0.08  # how far back an energy dip is looked for
RISE_FULL_DB = 20.0  # an energy rise this large counts as a full attack


def from_segments(segments: Sequence, t) -> tuple[list[float], list[float]]:
    """(onsets_s, durations_s) of segmented notes; ``t`` is the contour's frame times."""
    t = np.asarray(t, dtype=float)
    if len(t) == 0:
        return [], []
    hop = float(np.median(np.diff(t))) if len(t) > 1 else 0.01
    onsets = [float(t[min(s.start, len(t) - 1)]) for s in segments]
    durations = [float((s.end - s.start) * hop) for s in segments]
    return onsets, durations


def intervals(onsets_s) -> np.ndarray:
    """Inter-onset intervals of the sorted onsets; zero-length ones are dropped by the caller."""
    return np.diff(np.sort(np.asarray(onsets_s, dtype=float)))


def note_weights(durations_s) -> np.ndarray:
    """A longer note is a more salient onset. Clipped so one held note cannot dominate."""
    return np.clip(np.asarray(durations_s, dtype=float), MIN_WEIGHT_S, MAX_WEIGHT_S)


def interval_weights(durations_s) -> np.ndarray:
    """Weight of each inter-onset interval: the mean weight of the two notes that bound it."""
    w = note_weights(durations_s)
    return 0.5 * (w[:-1] + w[1:]) if len(w) > 1 else np.zeros(0)


def onset_envelope(audio, sr: int, hop_s: float = 0.01) -> tuple[np.ndarray, np.ndarray]:
    """(articulation strength 0..1, frame times in seconds) at ``hop_s``.

    The geometric mean of two cues, each on an absolute scale:
      flux   the share of the frame's log spectrum that is new since the previous frame
      rise   how far the energy has come back up from its recent dip (RISE_FULL_DB = 1)
    An attack out of silence is close to 1. A held tone, a vibrato, a glide and a legato
    change of pitch stay near 0, because their energy never dips: those are found from
    the pitch contour (spec 7.9), and this envelope is for what the contour cannot show,
    a note struck again at the same pitch.
    """
    from scipy.signal import stft

    y = np.asarray(audio, dtype=float)
    if y.ndim > 1:
        y = y.mean(axis=-1)
    hop = max(1, int(round(hop_s * sr)))
    window = WINDOW_HOPS * hop  # the same length in time at any sample rate
    if len(y) < window:
        return np.zeros(0), np.zeros(0)
    # Leading silence, so that a note starting at time zero is seen as an attack.
    y = np.concatenate((np.zeros(window), y))
    _, times, spec = stft(y, fs=sr, nperseg=window, noverlap=window - hop,
                          nfft=int(2 ** np.ceil(np.log2(window))), boundary=None, padded=False)
    times = np.asarray(times, dtype=float) - window / float(sr)
    mag = np.abs(spec)
    scale = float(mag.max())
    if scale <= 0:
        return np.zeros(len(times)), times
    log_mag = np.log1p(LOG_COMPRESSION * mag / scale)
    gained = np.maximum(0.0, np.diff(log_mag, axis=1)).sum(axis=0)
    flux = np.concatenate(([0.0], gained / (log_mag[:, 1:].sum(axis=0) + 1e-9)))

    energy_db = 10.0 * np.log10((mag ** 2).sum(axis=0) / scale ** 2 + 10.0 ** (-FLOOR_DB / 10.0))
    back = max(1, int(round(DIP_LOOK_BACK_S / hop_s)))
    recent_min = np.array([energy_db[max(0, i - back):i + 1].min() for i in range(len(energy_db))])
    rise = np.clip((energy_db - recent_min) / RISE_FULL_DB, 0.0, 1.0)
    return np.sqrt(np.clip(flux, 0.0, 1.0) * rise), times
