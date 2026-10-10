"""Voicing cleanup and octave-error repair (spec 7.5, 7.6).

``clean_voicing`` turns a raw tracker output (a pitch and a confidence for every frame)
into the contour the later stages use: NaN where unvoiced, octave slips folded back,
short dropouts inside a note bridged, single-frame spikes removed. Glides and
oscillations are left alone: the median filter is three frames wide.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterator, Union

import numpy as np

from ..analysis_types import PitchTrack
from ..config import section

REF_HZ = 55.0  # arbitrary reference for working in cents
BRIDGE_MAX_JUMP_CENTS = 80.0  # a gap is only bridged when the pitch on both sides is this close
MIN_RUN_FRAMES = 3  # voiced islands shorter than this (30 ms) are tracker noise
SPIKE_CENTS = 30.0  # a frame this far from the median of its neighbours is a spike


def hz_to_cents(f0: np.ndarray, ref_hz: float = REF_HZ) -> np.ndarray:
    f0 = np.asarray(f0, dtype=float)
    out = np.full(f0.shape, np.nan)
    ok = np.isfinite(f0) & (f0 > 0)
    out[ok] = 1200.0 * np.log2(f0[ok] / ref_hz)
    return out


def cents_to_hz(cents: np.ndarray, ref_hz: float = REF_HZ) -> np.ndarray:
    return ref_hz * 2.0 ** (np.asarray(cents, dtype=float) / 1200.0)


def runs(mask: np.ndarray) -> Iterator[tuple[int, int]]:
    """(start, end) of each run of True, end exclusive."""
    padded = np.concatenate([[0], np.asarray(mask, dtype=np.int8), [0]])
    d = np.diff(padded)
    return zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist())


def running_median(x: np.ndarray, half: int) -> np.ndarray:
    """Median over [i - half, i + half], ignoring NaN. NaN where the window holds no value."""
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return x.copy()
    padded = np.concatenate([np.full(half, np.nan), x, np.full(half, np.nan)])
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nanmedian(windows, axis=1)


def _repair_cents(cents: np.ndarray, hop_s: float) -> tuple[np.ndarray, int]:
    cfg = section("pitch")
    max_frames = max(1, int(round(cfg["octave_repair_max_ms"] / 1000.0 / hop_s)))
    window = float(cfg["octave_repair_window_cents"])
    c = np.asarray(cents, dtype=float).copy()
    n = len(c)
    if n < 3:
        return c, 0
    finite = np.isfinite(c)
    # The median window is four times the longest run that may be folded, so the run
    # itself cannot drag the median with it.
    dev = c - running_median(c, max(2 * max_frames, 20))
    fixed = 0
    for sign in (1.0, -1.0):
        hit = finite & (np.abs(dev - sign * 1200.0) <= window)
        for a, b in runs(hit):
            if b - a > max_frames:
                continue
            # "and returns": the line is back in its register next to the run, on at least one side.
            before = any(finite[i] and abs(dev[i]) < 600.0 for i in range(max(0, a - 3), a))
            after = any(finite[i] and abs(dev[i]) < 600.0 for i in range(b, min(n, b + 3)))
            if before or after:
                c[a:b] -= sign * 1200.0
                fixed += b - a
    return c, fixed


def repair_octaves(values: Union[np.ndarray, PitchTrack], *, unit: str = "cents",
                   hop_s: float = 0.01) -> Union[tuple[np.ndarray, int], PitchTrack]:
    """Fold back short octave slips: any run shorter than ``octave_repair_max_ms`` that sits
    1200 +/- ``octave_repair_window_cents`` from the running median and returns.

    An array (``unit`` "cents" or "hz", NaN = unvoiced) gives ``(repaired, frames_fixed)``.
    A PitchTrack gives a new PitchTrack with ``octave_fixes`` increased.
    """
    if isinstance(values, PitchTrack):
        c, fixed = _repair_cents(hz_to_cents(values.f0), values.hop_s)
        return replace(values, f0=cents_to_hz(c), octave_fixes=values.octave_fixes + fixed)
    if unit == "hz":
        c, fixed = _repair_cents(hz_to_cents(values), hop_s)
        return cents_to_hz(c), fixed
    return _repair_cents(values, hop_s)


def bridge_gaps(cents: np.ndarray, hop_s: float = 0.01) -> tuple[np.ndarray, np.ndarray]:
    """Fill unvoiced gaps shorter than ``bridge_gap_ms`` by a straight line, but only when
    the pitch on both sides is close: a gap between two different notes is a real boundary.
    Returns (cents, mask of bridged frames)."""
    max_gap = float(section("pitch")["bridge_gap_ms"]) / 1000.0
    c = np.asarray(cents, dtype=float).copy()
    bridged = np.zeros(len(c), dtype=bool)
    for a, b in runs(~np.isfinite(c)):
        if a == 0 or b == len(c) or (b - a) * hop_s >= max_gap - 1e-9:
            continue
        left, right = c[a - 1], c[b]
        if abs(right - left) > BRIDGE_MAX_JUMP_CENTS:
            continue
        c[a:b] = np.interp(np.arange(a, b), [a - 1, b], [left, right])
        bridged[a:b] = True
    return c, bridged


def median_smooth(cents: np.ndarray, frames: int = 3, spike_cents: float = SPIKE_CENTS) -> np.ndarray:
    """Light median filter used as a spike remover.

    A frame is replaced by the median of its window only when it stands more than
    ``spike_cents`` away from that median. A plain median would also shave the turning
    points of every oscillation by a few cents per pass, and those extents are measured
    later. Voiced and unvoiced frames are never mixed: a frame is only touched when its
    whole window is voiced, so note edges stay where they are.
    """
    c = np.asarray(cents, dtype=float)
    half = max(0, int(frames) // 2)
    if half == 0 or len(c) < 2 * half + 1:
        return c.copy()
    windows = np.lib.stride_tricks.sliding_window_view(c, 2 * half + 1)
    whole = np.all(np.isfinite(windows), axis=1)
    med = np.full(len(windows), np.nan)
    med[whole] = np.median(windows[whole], axis=1)
    out = c.copy()
    centre = out[half:len(c) - half]
    spike = whole & (np.abs(centre - med) > spike_cents)
    centre[spike] = med[spike]
    return out


def clean_voicing(track: PitchTrack, threshold: float) -> PitchTrack:
    """Drop frames under the confidence threshold, repair octave slips, bridge short gaps
    inside a note and apply the light median filter (spec 7.6)."""
    cfg = section("pitch")
    f0 = np.asarray(track.f0, dtype=float)
    conf = np.asarray(track.conf, dtype=float).copy()
    voiced = np.isfinite(f0) & (f0 > 0) & (conf >= threshold)
    cents = np.where(voiced, hz_to_cents(f0), np.nan)

    cents, fixed = _repair_cents(cents, track.hop_s)
    cents, bridged = bridge_gaps(cents, track.hop_s)
    conf[bridged] = np.maximum(conf[bridged], threshold)  # bridged frames count as (just) voiced
    for a, b in runs(np.isfinite(cents)):
        if b - a < MIN_RUN_FRAMES:
            cents[a:b] = np.nan
    cents = median_smooth(cents, int(cfg["median_frames"]))

    out = np.full(len(cents), np.nan)
    ok = np.isfinite(cents)
    out[ok] = cents_to_hz(cents[ok])
    return replace(track, f0=out, conf=np.clip(conf, 0.0, 1.0), octave_fixes=track.octave_fixes + fixed)
