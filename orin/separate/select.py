"""Choosing the melody stem (spec 7.3, 7.4).

v1 transcribes one melodic line: the vocals when there is singing, otherwise the
"other" stem (lead guitar, violin, flute...). The user can override the choice.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

VOCAL_ENERGY_SHARE = 0.1  # vocals carry at least this share of the mix's energy
MELODY_STEMS = ("vocals", "other")


def stem_energy(stem: np.ndarray) -> float:
    x = np.asarray(stem, dtype=np.float64)
    return float(np.mean(x ** 2)) if x.size else 0.0


def select_melody_stem(stems: dict[str, np.ndarray], override: Optional[str] = None) -> str:
    """Name of the stem to transcribe. ``override`` ("vocals" or "other") wins when that
    stem exists; "auto" or None picks vocals when its energy share is above the threshold."""
    if override in MELODY_STEMS and override in stems:
        return override
    energy = {name: stem_energy(x) for name, x in stems.items()}
    total = sum(energy.values())
    if "vocals" in stems and total > 0 and energy["vocals"] / total >= VOCAL_ENERGY_SHARE:
        return "vocals"
    if "other" in stems:
        return "other"
    return "vocals" if "vocals" in stems else max(energy, key=energy.get)


def to_mono(stem: np.ndarray) -> np.ndarray:
    x = np.asarray(stem, dtype=np.float32)
    return x.mean(axis=0) if x.ndim > 1 else x
