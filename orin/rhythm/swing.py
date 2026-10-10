"""Swing (spec 7.11): a three-way division of the beat whose middle slot is almost unused.

The long/short ratio is measured from neighbouring long-short pairs. Above
``swing_threshold`` (about 1.4) the grid is swung; otherwise the ratio reported is 1.
"""
from __future__ import annotations

import numpy as np

from .. import config

MIDDLE_MAX_SHARE = 0.1  # the middle third may hold at most this share of the onsets
BEAT_MIN_SHARE = 0.25
OFFBEAT_MIN_SHARE = 0.2
MIN_PAIRS = 2


def detect(positions: np.ndarray, iois: np.ndarray, tatum_s: float) -> tuple[bool, float]:
    """(swung, long/short ratio). ``positions`` are whole-tatum onset positions and
    ``iois`` the intervals between the same onsets."""
    cfg = config.section("rhythm")
    positions = np.asarray(positions, dtype=int)
    iois = np.asarray(iois, dtype=float)
    if len(positions) < 4 or len(iois) != len(positions) - 1:
        return False, 1.0
    # A beat of three tatums longer than two of the largest tatum is a slow triple metre,
    # not a swung beat.
    if 3.0 * tatum_s > 2.0 * float(cfg["tatum_max_ms"]) / 1000.0:
        return False, 1.0

    ternary = False
    for phase in range(3):
        counts = np.bincount((positions - phase) % 3, minlength=3) / len(positions)
        if counts[1] <= MIDDLE_MAX_SHARE and counts[0] >= BEAT_MIN_SHARE and counts[2] >= OFFBEAT_MIN_SHARE:
            ternary = True
            break
    if not ternary:
        return False, 1.0

    steps = np.diff(positions)
    ratios = [max(iois[i], iois[i + 1]) / min(iois[i], iois[i + 1])
              for i in range(len(steps) - 1)
              if {int(steps[i]), int(steps[i + 1])} == {1, 2} and min(iois[i], iois[i + 1]) > 0]
    if len(ratios) < MIN_PAIRS:
        return False, 1.0
    ratio = float(np.median(ratios))
    if ratio < float(cfg["swing_threshold"]):
        return False, 1.0
    return True, ratio
