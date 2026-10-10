"""Optional Basic Pitch cross-check (spec 7.5).

Basic Pitch proposes note events straight from audio. It is a second opinion for
instruments, never the source of truth: for sung, heavily ornamented lines the
continuous contour from CREPE or pYIN is. The package is optional and, when it is not
installed, ``propose_notes`` returns None and the pipeline carries on without it.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

SAMPLE_RATE = 22050  # the rate Basic Pitch expects


def available() -> bool:
    try:
        import basic_pitch  # noqa: F401
    except Exception:
        return False
    return True


def propose_notes(y22k: np.ndarray, sr: int = SAMPLE_RATE) -> Optional[list[dict]]:
    """Note proposals as dicts (onset_s, offset_s, hz, amplitude), or None when Basic Pitch
    is not installed or fails."""
    try:
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import predict
    except Exception:
        return None
    try:
        import os
        import tempfile

        import soundfile as sf

        with tempfile.TemporaryDirectory(prefix="orin-bp-") as d:
            path = os.path.join(d, "in.wav")
            sf.write(path, np.asarray(y22k, dtype=np.float32), sr)
            _, _, events = predict(path, ICASSP_2022_MODEL_PATH)
    except Exception:
        return None
    notes = [{"onset_s": float(start), "offset_s": float(end),
              "hz": float(440.0 * 2.0 ** ((float(midi) - 69.0) / 12.0)), "amplitude": float(amp)}
             for start, end, midi, amp, *_ in events]
    return sorted(notes, key=lambda n: n["onset_s"])


def agreement(notes: Optional[list[dict]], t: np.ndarray, f0: np.ndarray, tolerance_cents: float = 100.0) -> Optional[float]:
    """Share of Basic Pitch's note time during which the contour is within a semitone of
    the proposed pitch (octave-folded). None when there is nothing to compare."""
    if not notes:
        return None
    t = np.asarray(t, dtype=float)
    f0 = np.asarray(f0, dtype=float)
    hit = total = 0
    for n in notes:
        inside = (t >= n["onset_s"]) & (t < n["offset_s"]) & np.isfinite(f0)
        if not inside.any():
            continue
        diff = (1200.0 * np.log2(f0[inside] / n["hz"]) + 600.0) % 1200.0 - 600.0
        hit += int(np.sum(np.abs(diff) <= tolerance_cents))
        total += int(inside.sum())
    return hit / total if total else None
