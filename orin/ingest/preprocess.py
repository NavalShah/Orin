"""Preprocessing (spec 7.2): trim, normalise, optional high-pass, quality warnings and a
polyphony score.

Trimming returns ``trim_offset_s``: the time of the first kept sample in the ORIGINAL
file. Every later stage adds it back, so all reported times are in the file's own time.
No denoising is done: it smears pitch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

PAD_S = 0.1
TRIM_TOP_DB = 40.0
TARGET_PEAK = 0.89  # about -1 dBFS: loud enough for the trackers, never clipped
HIGHPASS_HZ = 50.0
CLIPPING_WARN_FRACTION = 0.0005
LOW_SNR_DB = 10.0
NOISY_FLATNESS = 0.3
SEPARATE_ABOVE_POLYPHONY = 0.4


@dataclass
class Preprocessed:
    y: np.ndarray  # trimmed, filtered, normalised mono
    sr: int
    trim_offset_s: float = 0.0  # time of y[0] in the original file
    trim_end_s: float = 0.0  # time just after y[-1] in the original file
    gain: float = 1.0
    highpass_hz: Optional[float] = HIGHPASS_HZ
    clipping_fraction: float = 0.0
    snr_db: Optional[float] = None  # None when the recording has no quiet part to measure a floor from
    flatness: float = 0.0
    polyphony: float = 0.0  # 0 = one clean harmonic line, 1 = dense mix or noise
    needs_separation: bool = False
    warnings: list[tuple[str, str]] = field(default_factory=list)  # (code, message)

    def summary(self) -> dict:
        """The JSON-friendly part, enough to redo the cheap array work from a cache."""
        return {
            "trim_offset_s": self.trim_offset_s, "trim_end_s": self.trim_end_s, "gain": self.gain,
            "highpass_hz": self.highpass_hz, "clipping_fraction": self.clipping_fraction,
            "snr_db": self.snr_db, "flatness": self.flatness, "polyphony": self.polyphony,
            "needs_separation": self.needs_separation, "warnings": [list(w) for w in self.warnings],
        }


def _frame_rms(y: np.ndarray, frame: int, hop: int) -> np.ndarray:
    if len(y) < frame:
        return np.array([float(np.sqrt(np.mean(np.square(y, dtype=np.float64))))]) if len(y) else np.zeros(1)
    n = 1 + (len(y) - frame) // hop
    sq = np.square(y.astype(np.float64))
    csum = np.concatenate([[0.0], np.cumsum(sq)])
    starts = np.arange(n) * hop
    return np.sqrt(np.maximum(csum[starts + frame] - csum[starts], 0.0) / frame)


def trim_bounds(y: np.ndarray, sr: int, *, top_db: float = TRIM_TOP_DB, pad_s: float = PAD_S) -> tuple[float, float]:
    """(start_s, end_s) of the sounding part, padded by ``pad_s`` on each side."""
    total = len(y) / sr
    frame, hop = max(64, int(0.03 * sr)), max(16, int(0.01 * sr))
    rms = _frame_rms(y, frame, hop)
    peak = float(rms.max()) if rms.size else 0.0
    if peak <= 0.0:
        return 0.0, total
    loud = np.flatnonzero(rms >= peak * 10.0 ** (-top_db / 20.0))
    start = loud[0] * hop / sr - pad_s
    end = (loud[-1] * hop + frame) / sr + pad_s
    return float(max(0.0, start)), float(min(total, end))


def highpass(y: np.ndarray, sr: int, cutoff_hz: float = HIGHPASS_HZ) -> np.ndarray:
    """Zero-phase second-order high-pass: removes rumble without shifting onsets."""
    if cutoff_hz is None or cutoff_hz <= 0 or len(y) < 64:
        return y
    from scipy.signal import butter, sosfiltfilt

    sos = butter(2, cutoff_hz, btype="highpass", fs=sr, output="sos")
    return sosfiltfilt(sos, y).astype(y.dtype, copy=False)


def normalise(y: np.ndarray, target_peak: float = TARGET_PEAK) -> tuple[np.ndarray, float]:
    """Scale so the peak sits at ``target_peak``. Returns (signal, gain). Silence is left alone."""
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak <= 1e-9:
        return y, 1.0
    gain = target_peak / peak
    return (y * gain).astype(y.dtype, copy=False), float(gain)


def estimate_snr_db(y: np.ndarray, sr: int) -> Optional[float]:
    """Loud frames against quiet frames, in dB. None when there are no quiet frames
    (a continuous sound gives nothing to measure a noise floor from)."""
    rms = _frame_rms(y, max(64, int(0.05 * sr)), max(32, int(0.025 * sr)))
    if rms.size < 8:
        return None
    db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    loud, quiet = float(np.percentile(db, 95)), float(np.percentile(db, 10))
    if loud - quiet < 6.0:
        return None
    return loud - quiet


def _spectra(y: np.ndarray, sr: int, n_fft: int = 2048, max_frames: int = 150) -> tuple[np.ndarray, np.ndarray]:
    """Power spectra (bins x frames) of up to ``max_frames`` of the louder frames, and bin frequencies."""
    hop = n_fft // 2
    if len(y) < n_fft:
        y = np.pad(y, (0, n_fft - len(y)))
    n = 1 + (len(y) - n_fft) // hop
    idx = np.arange(n)[:, None] * hop + np.arange(n_fft)[None, :]
    frames = y[idx].astype(np.float64)
    energy = np.sum(frames ** 2, axis=1)
    keep = np.flatnonzero(energy >= energy.max() * 1e-3) if energy.max() > 0 else np.arange(0)
    if keep.size > max_frames:
        keep = keep[np.linspace(0, keep.size - 1, max_frames).astype(int)]
    spec = np.abs(np.fft.rfft(frames[keep] * np.hanning(n_fft)[None, :], axis=1)) ** 2
    return spec.T, np.fft.rfftfreq(n_fft, 1.0 / sr)


def spectral_flatness(y: np.ndarray, sr: int) -> float:
    """Median flatness of the louder frames between 100 Hz and 4 kHz: near 0 for a tone, about 0.5 for noise."""
    spec, freqs = _spectra(y, sr)
    band = (freqs >= 100.0) & (freqs <= 4000.0)
    if spec.shape[1] == 0 or not band.any():
        return 0.0
    p = spec[band] + 1e-12
    flat = np.exp(np.mean(np.log(p), axis=0)) / np.mean(p, axis=0)
    return float(np.median(flat))


def polyphony_score(y: np.ndarray, sr: int) -> float:
    """0 when one harmonic series explains each frame, towards 1 for mixes, percussion and noise.

    For each frame the best single fundamental (60 to 1000 Hz) is found by how much of the
    spectrum up to 4 kHz falls on its first twelve harmonics, corrected for how much any
    signal would hit by chance. Chords built on simple ratios share a low fundamental and
    score low, so this is a "can separation be skipped" test, not a polyphony detector.
    """
    spec, freqs = _spectra(y, sr)
    band = (freqs >= 50.0) & (freqs <= 4000.0)
    if spec.shape[1] == 0 or not band.any():
        return 0.0
    p, f = spec[band], freqs[band]
    total = p.sum(axis=0)
    ok = total > 0
    if not ok.any():
        return 0.0
    bin_hz = freqs[1] - freqs[0]
    f0s = 60.0 * 2.0 ** (np.arange(0, int(1200 * np.log2(1000.0 / 60.0)) + 1, 25) / 1200.0)
    k = np.arange(1, 13)
    # distance from every bin to the nearest of the first 12 harmonics of each candidate
    harm = f0s[:, None] * k[None, :]  # candidates x harmonics
    dist = np.abs(f[None, :, None] - harm[:, None, :])  # candidates x bins x harmonics
    width = np.maximum(1.5 * bin_hz, 0.02 * harm)[:, None, :]
    mask = np.any(dist <= width, axis=2).astype(np.float64)  # candidates x bins
    cover = mask.mean(axis=1, keepdims=True)
    explained = (mask @ p[:, ok]) / total[ok][None, :]
    score = (explained - cover) / np.maximum(1.0 - cover, 1e-6)
    return float(np.clip(1.0 - np.median(score.max(axis=0)), 0.0, 1.0))


def preprocess(y: np.ndarray, sr: int, *, highpass_hz: Optional[float] = HIGHPASS_HZ,
               target_peak: float = TARGET_PEAK, clipping: Optional[float] = None) -> Preprocessed:
    """Full preprocessing of one mono signal. ``clipping`` is the share of clipped samples
    measured on the decoded file (see ``ingest.audio.clipping_fraction``); it cannot be
    measured here once the audio has been resampled."""
    y = np.asarray(y, dtype=np.float32)
    start_s, end_s = trim_bounds(y, sr)
    pre = Preprocessed(y=y, sr=sr, trim_offset_s=start_s, trim_end_s=end_s, highpass_hz=highpass_hz,
                       clipping_fraction=float(clipping or 0.0))
    pre.snr_db = estimate_snr_db(y, sr)
    pre.y, pre.gain = _shape(y, sr, start_s, end_s, highpass_hz, None, target_peak)
    pre.flatness = spectral_flatness(pre.y, sr)
    pre.polyphony = polyphony_score(pre.y, sr)
    pre.needs_separation = pre.polyphony > SEPARATE_ABOVE_POLYPHONY

    if pre.clipping_fraction > CLIPPING_WARN_FRACTION:
        pre.warnings.append(("clipping", "The recording is clipped (too loud at the source). "
                                         "Pitch and note edges may be less reliable."))
    if (pre.snr_db is not None and pre.snr_db < LOW_SNR_DB) or pre.flatness > NOISY_FLATNESS:
        pre.warnings.append(("low_snr", "The recording is very noisy. Expect missed or wrong notes; "
                                        "a closer, quieter recording will work better."))
    return pre


def _shape(y: np.ndarray, sr: int, start_s: float, end_s: float, highpass_hz: Optional[float],
           gain: Optional[float], target_peak: float = TARGET_PEAK) -> tuple[np.ndarray, float]:
    a, b = int(round(start_s * sr)), int(round(end_s * sr))
    out = np.asarray(y[..., a:max(a, b)], dtype=np.float32)
    if highpass_hz:
        out = highpass(out, sr, highpass_hz) if out.ndim == 1 else np.stack([highpass(c, sr, highpass_hz) for c in out])
    if gain is None:
        return normalise(out, target_peak)
    return (out * gain).astype(np.float32, copy=False), float(gain)


def apply_like(y: np.ndarray, sr: int, pre: "Preprocessed | dict") -> np.ndarray:
    """Give another copy of the same recording (another sample rate, or stereo) the same
    trim, filter and gain, so every copy shares one time origin and one level."""
    s = pre.summary() if isinstance(pre, Preprocessed) else pre
    out, _ = _shape(y, sr, s["trim_offset_s"], s["trim_end_s"], s.get("highpass_hz"), s.get("gain", 1.0))
    return out
