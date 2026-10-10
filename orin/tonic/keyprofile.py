"""Key-profile sanity check for the 12-TET formats (spec 7.7, method 3).

Runs the original pipeline's Krumhansl-Schmuckler estimate on the melody's held notes.
Agreement with the chosen tonic adds a little confidence. It never overrides: the
profiles describe Western tonal music, and a raga or a maqam has no reason to follow them.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .histogram import REF_HZ, MelodyProfile

MIN_CORRELATION = 0.5
AGREEMENT_BONUS = 0.1


def midi_of(hz: float) -> float:
    return 69.0 + 12.0 * float(np.log2(hz / 440.0))


def estimate_key_pc(prof: MelodyProfile) -> Optional[tuple[int, str, float]]:
    """(pitch class with C = 0, "major" | "minor", correlation), or None when there is too
    little to go on or the original pipeline cannot be imported."""
    if not prof.runs:
        return None
    try:
        from pipeline.analyze import NAMES, estimate_key
    except Exception:
        return None
    notes = [{"midi": int(round(midi_of(REF_HZ * 2.0 ** (r.cents / 1200.0)))),
              "onset": r.start * prof.hop_s, "offset": r.end * prof.hop_s} for r in prof.runs]
    try:
        key = estimate_key(notes)
    except Exception:
        return None
    if not key or not np.isfinite(key.get("correlation", np.nan)):
        return None
    return NAMES.index(key["tonic"]), key["mode"], float(key["correlation"])


def agrees(prof: MelodyProfile, tonic_hz: float) -> bool:
    key = estimate_key_pc(prof)
    if key is None or key[2] < MIN_CORRELATION:
        return False
    return int(round(midi_of(tonic_hz))) % 12 == key[0]
