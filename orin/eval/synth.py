"""Synthesised test signals. Tests, the eval runner and the offline music stand-in use these.

Two layers:
  contours  arrays of cents at a 10 ms hop (NaN = silence). Fast and exact; use them to
            test segmentation, ornaments, quantisation and loss.
  audio     ``render`` turns a contour into a harmonic tone by phase accumulation; use it
            to test pitch tracking, tonic and drone detection end to end.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

SR = 22050
HOP_S = 0.01
PARTIALS = (1.0, 0.5, 0.3, 0.2)


def _n(dur_s: float, hop_s: float = HOP_S) -> int:
    return max(1, int(round(dur_s / hop_s)))


def const(cents: float, dur_s: float, hop_s: float = HOP_S) -> np.ndarray:
    return np.full(_n(dur_s, hop_s), float(cents))


def silence(dur_s: float, hop_s: float = HOP_S) -> np.ndarray:
    return np.full(_n(dur_s, hop_s), np.nan)


def ramp(start_cents: float, end_cents: float, dur_s: float, hop_s: float = HOP_S) -> np.ndarray:
    return np.linspace(start_cents, end_cents, _n(dur_s, hop_s))


def oscillation(center_cents: float, half_extent_cents: float, rate_hz: float, dur_s: float,
                hop_s: float = HOP_S, phase: float = 0.0) -> np.ndarray:
    t = np.arange(_n(dur_s, hop_s)) * hop_s
    return center_cents + half_extent_cents * np.sin(2 * np.pi * rate_hz * t + phase)


def concat(*parts: np.ndarray) -> np.ndarray:
    return np.concatenate([np.asarray(p, dtype=float) for p in parts])


def phrase(cents: Sequence[float], note_s: float | Sequence[float] = 0.3, gap_s: float = 0.02,
           hop_s: float = HOP_S) -> np.ndarray:
    """Held notes separated by short silences. ``note_s`` is one length or one per note."""
    lengths = [note_s] * len(cents) if isinstance(note_s, (int, float)) else list(note_s)
    parts: list[np.ndarray] = []
    for i, (c, d) in enumerate(zip(cents, lengths)):
        parts.append(const(c, d, hop_s))
        if gap_s > 0 and i < len(cents) - 1:
            parts.append(silence(gap_s, hop_s))
    return concat(*parts)


def times(contour: np.ndarray, hop_s: float = HOP_S) -> np.ndarray:
    return np.arange(len(contour)) * hop_s


def render(contour: np.ndarray, tonic_hz: float, *, sr: int = SR, hop_s: float = HOP_S, amp: float = 0.5,
           partials: Sequence[float] = PARTIALS, fade_s: float = 0.01) -> np.ndarray:
    """Contour (cents relative to ``tonic_hz``) -> mono audio. NaN frames are silent."""
    contour = np.asarray(contour, dtype=float)
    n_samples = int(round(len(contour) * hop_s * sr))
    if n_samples == 0:
        return np.zeros(0)
    frame_pos = np.arange(n_samples) / (hop_s * sr)
    idx = np.clip(frame_pos.astype(int), 0, len(contour) - 1)
    voiced_frames = ~np.isnan(contour)

    # Fill silent frames with the nearest voiced value so interpolation never sees NaN.
    filled = contour.copy()
    if voiced_frames.any():
        good = np.flatnonzero(voiced_frames)
        filled = np.interp(np.arange(len(contour)), good, contour[good])
    else:
        return np.zeros(n_samples)

    cents = np.interp(frame_pos, np.arange(len(contour)), filled)
    freq = tonic_hz * 2.0 ** (cents / 1200.0)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    y = np.zeros(n_samples)
    for k, weight in enumerate(partials, start=1):
        y += weight * np.sin(k * phase) * (k * freq < sr / 2)  # drop partials above Nyquist
    y /= max(sum(partials), 1e-9)

    # Gate by voicing with short fades so note edges do not click.
    gate = voiced_frames[idx].astype(float)
    fade = max(1, int(fade_s * sr))
    kernel = np.hanning(2 * fade + 1)
    gate = np.convolve(gate, kernel / kernel.sum(), mode="same")
    return amp * y * gate


def tone(freq_hz: float, dur_s: float, *, sr: int = SR, amp: float = 0.5,
         partials: Sequence[float] = PARTIALS) -> np.ndarray:
    return render(const(0.0, dur_s), freq_hz, sr=sr, amp=amp, partials=partials)


def drone(freqs_hz: Iterable[float], dur_s: float, *, sr: int = SR, amp: float = 0.06,
          n_partials: int = 8) -> np.ndarray:
    """Tanpura-like bed: steady tones rich in partials."""
    t = np.arange(int(round(dur_s * sr))) / sr
    y = np.zeros(len(t))
    for f in freqs_hz:
        for k in range(1, n_partials + 1):
            if k * f < sr / 2:
                y += np.sin(2 * np.pi * k * f * t) / k
    peak = np.max(np.abs(y)) or 1.0
    return amp * y / peak


def clicks(onsets_s: Iterable[float], dur_s: float, *, sr: int = SR, click_s: float = 0.04,
           freq_hz: float = 1000.0, amp: float = 0.6) -> np.ndarray:
    y = np.zeros(int(round(dur_s * sr)))
    n = int(click_s * sr)
    burst = amp * np.sin(2 * np.pi * freq_hz * np.arange(n) / sr) * np.hanning(n)
    for t in onsets_s:
        i = int(round(t * sr))
        if 0 <= i < len(y):
            m = min(n, len(y) - i)
            y[i:i + m] += burst[:m]
    return y


def noise(dur_s: float, *, sr: int = SR, amp: float = 0.01, seed: int = 0) -> np.ndarray:
    return amp * np.random.default_rng(seed).standard_normal(int(round(dur_s * sr)))


def mix(*signals: np.ndarray) -> np.ndarray:
    """Sum signals of different lengths (shorter ones are zero-padded at the end)."""
    n = max(len(s) for s in signals)
    y = np.zeros(n)
    for s in signals:
        y[:len(s)] += s
    return y


def write_wav(path: str, y: np.ndarray, sr: int = SR) -> str:
    import soundfile as sf

    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    sf.write(path, y / peak * 0.89 if peak > 0.89 else y, sr, subtype="PCM_16")
    return path
