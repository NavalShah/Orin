"""CREPE-family pitch tracking through torchcrepe (spec 7.5): the primary tracker.

16 kHz mono in, one frame every 160 samples (exactly 10 ms), with a per-frame
confidence (the network's periodicity). The "tiny" and "full" models ship inside the
torchcrepe package, so nothing is downloaded.

Decoding: torchcrepe's Viterbi path picks the pitch bin (it suppresses octave jumps),
then the pitch is refined to a weighted average of the nine bins around it, as in the
original CREPE. torchcrepe's own decoders return 20-cent bins with random dither,
which is too coarse for cent-level tonic work and not reproducible.
"""
from __future__ import annotations

import os

import numpy as np

from ..analysis_types import PitchTrack
from ..config import section

DEFAULT_MODEL = "tiny"  # about 12x real time on a laptop CPU; "full" is far slower than real time
CENTS_PER_BIN = 20.0
BIN0_CENTS = 1997.3794084376191  # cents above 10 Hz of bin 0, from the CREPE paper
PITCH_BINS = 360


def model_name() -> str:
    name = os.environ.get("ORIN_CREPE_MODEL", DEFAULT_MODEL).strip().lower()
    return name if name in ("tiny", "full") else DEFAULT_MODEL


def _decode(probabilities):
    """torchcrepe decoder: (batch, 360, time) probabilities -> (bins, pitch in Hz)."""
    import torch
    import torchcrepe

    bins, _ = torchcrepe.decode.viterbi(probabilities)
    p = torch.nan_to_num(probabilities.detach(), nan=0.0, neginf=0.0, posinf=0.0).clamp(min=0.0)
    offsets = torch.arange(-4, 5, device=p.device).view(1, 9, 1)
    idx = bins.unsqueeze(1) + offsets
    inside = ((idx >= 0) & (idx < PITCH_BINS)).to(p.dtype)
    idx = idx.clamp(0, PITCH_BINS - 1)
    w = torch.gather(p, 1, idx) * inside
    cents = (w * (CENTS_PER_BIN * idx.to(p.dtype) + BIN0_CENTS)).sum(dim=1)
    total = w.sum(dim=1)
    centre = CENTS_PER_BIN * bins.to(p.dtype) + BIN0_CENTS
    cents = torch.where(total > 1e-9, cents / total.clamp(min=1e-9), centre)
    return bins, 10.0 * 2.0 ** (cents / 1200.0)


def track_crepe(y16k: np.ndarray, *, fmin: float, fmax: float, model: str | None = None,
                batch_size: int = 512) -> PitchTrack:
    """Raises ImportError when torch or torchcrepe is missing; the caller falls back to pYIN."""
    import torch
    import torchcrepe

    cfg = section("pitch")
    sr, hop = int(cfg["sample_rate"]), int(cfg["hop_samples"])
    y = np.asarray(y16k, dtype=np.float32)
    n_frames = 1 + len(y) // hop
    t = np.arange(n_frames) * (hop / sr)
    if len(y) < hop or not np.any(y):
        return PitchTrack(t=t, f0=np.full(n_frames, np.nan), conf=np.zeros(n_frames), hop_s=hop / sr, tracker="crepe")

    audio = torch.from_numpy(np.ascontiguousarray(y))[None]
    with torch.no_grad():
        pitch, periodicity = torchcrepe.predict(
            audio, sr, hop_length=hop, fmin=float(fmin), fmax=float(min(fmax, torchcrepe.MAX_FMAX)),
            model=model or model_name(), decoder=_decode, return_periodicity=True,
            batch_size=batch_size, device="cpu", pad=True)
    f0 = pitch[0].cpu().numpy().astype(float)
    conf = np.clip(np.nan_to_num(periodicity[0].cpu().numpy().astype(float)), 0.0, 1.0)
    n = min(n_frames, len(f0))
    f0 = np.where(np.isfinite(f0[:n]) & (f0[:n] > 0), f0[:n], np.nan)
    return PitchTrack(t=t[:n], f0=f0, conf=conf[:n], hop_s=hop / sr, tracker="crepe")
