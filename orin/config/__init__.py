"""Loads ``styles.toml``: per-style pipeline defaults (spec 13.8)."""
from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..ir.models import Model

_PATH = Path(__file__).with_name("styles.toml")


class SegmentationProfile(Model):
    id: str
    voicing_threshold: float
    min_note_ms: float
    plateau_enter_cents: float
    plateau_exit_cents: float
    osc_min_hz: float
    osc_max_hz: float
    max_merge_half_extent_cents: float
    out_and_back_ms: float
    mode_snap_cents: float

    def scaled(self, sensitivity: float) -> "SegmentationProfile":
        """Apply the UI's sensitivity: above 1 splits notes more readily, below 1 merges more."""
        s = min(max(sensitivity, 0.25), 4.0)
        if s == 1.0:
            return self
        return self.model_copy(update={
            "plateau_enter_cents": self.plateau_enter_cents / s,
            "plateau_exit_cents": self.plateau_exit_cents / s,
            "min_note_ms": self.min_note_ms / s,
            "out_and_back_ms": self.out_and_back_ms / s,
        })


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    with _PATH.open("rb") as f:
        return tomllib.load(f)


def section(name: str) -> dict[str, Any]:
    """A top-level table such as "pitch", "ornaments", "tonic", "rhythm", "mode", "detect"."""
    return load()[name]


def profile(name: str, sensitivity: float = 1.0) -> SegmentationProfile:
    profiles = load()["profiles"]
    key = name if name in profiles else "neutral"
    return SegmentationProfile(id=key, **profiles[key]).scaled(sensitivity)


def profile_names() -> list[str]:
    return list(load()["profiles"])
