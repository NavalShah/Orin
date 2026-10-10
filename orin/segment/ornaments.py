"""Ornament detection on one note's contour (spec 7.10).

Explainable rules on the residual (contour minus the note's anchor). What is measured
is stored (extent, rate, cycles, shape, where on the note), so an adapter can
approximate by rule. Names are neutral: oscillation, vibrato, glide, grace. A
tradition's own name is applied only by an adapter, and only when that style was chosen.

Thresholds come from ``[ornaments]`` in orin/config/styles.toml.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

import numpy as np

from .. import config
from ..analysis_types import Segment
from ..config import SegmentationProfile
from ..ir.models import Ornament
from .notes import HALF_PERIOD_SLACK, find_extrema, hop_seconds, smooth

MONOTONIC_SLACK = 1.25  # a glide's path may be this much longer than its net movement
MAX_GRACES = 3
_KIND_ORDER = {"oscillation": 0, "vibrato": 1, "glide": 2, "grace": 3}


@dataclass
class _Periodic:
    half_extent: float
    rate_hz: float
    cycles: float
    regularity: float  # 1 - coefficient of variation of the half-periods
    symmetry: float  # 1 when the swings above and below the note's centre are equal
    start: int  # frame span inside the note, [start, end)
    end: int


def _fill(x: np.ndarray) -> np.ndarray:
    """Interpolate over unvoiced frames so a bridged gap does not look like movement."""
    bad = np.isnan(x)
    if not bad.any():
        return x
    if bad.all():
        return np.zeros(len(x))
    good = np.flatnonzero(~bad)
    return np.interp(np.arange(len(x)), good, x[good])


def _measure_periodic(r: np.ndarray, rs: np.ndarray, hop_s: float, delta: float,
                      max_half_period_s: float) -> Optional[_Periodic]:
    """Measure the longest run of turning points whose spacing is an oscillation."""
    ext = find_extrema(rs, delta)
    if len(ext) < 3:
        return None
    runs: list[list[tuple[int, float, int]]] = [[ext[0]]]
    for prev, cur in zip(ext[:-1], ext[1:]):
        if (cur[0] - prev[0]) * hop_s <= max_half_period_s:
            runs[-1].append(cur)
        else:
            runs.append([cur])
    run = max(runs, key=len)
    if len(run) < 3:
        return None
    idx = np.array([e[0] for e in run])
    # Read each extreme from the unsmoothed residual, so smoothing does not shrink it.
    vals = np.array([
        (r[max(0, i - 1):i + 2].max() if kind > 0 else r[max(0, i - 1):i + 2].min())
        for i, _, kind in run
    ])
    kinds = np.array([e[2] for e in run])
    half_periods = np.diff(idx) * hop_s
    mean_hp = float(half_periods.mean())
    if mean_hp <= 0:
        return None
    half_frames = int(round(mean_hp / hop_s))
    start = max(0, int(idx[0]) - half_frames)
    end = min(len(r), int(idx[-1]) + half_frames + 1)
    centre = float(np.median(r[start:end]))
    up = float(vals[kinds > 0].mean()) - centre
    down = centre - float(vals[kinds < 0].mean())
    total = abs(up) + abs(down)
    rate = 1.0 / (2.0 * mean_hp)
    return _Periodic(
        half_extent=float(np.median(np.abs(np.diff(vals)))) / 2.0,
        rate_hz=rate,
        cycles=(end - start) * hop_s * rate,
        regularity=float(np.clip(1.0 - half_periods.std() / mean_hp, 0.0, 1.0)),
        symmetry=float(np.clip(1.0 - abs(up - down) / total, 0.0, 1.0)) if total > 0 else 0.0,
        start=start, end=end,
    )


def _rise_time_s(path: np.ndarray, hop_s: float) -> float:
    """Time the movement itself takes: the 10%-90% rise time scaled to the full extent.
    A step followed or preceded by a held pitch therefore does not count as a glide."""
    moved = np.abs(path - path[0])
    extent = moved[-1]
    if extent <= 0:
        return 0.0
    first = int(np.argmax(moved >= 0.1 * extent))
    last = int(np.argmax(moved >= 0.9 * extent))
    return max(0, last - first) * hop_s / 0.8


def _edge_glide(rs: np.ndarray, hop_s: float, orn: dict, envelope: float, *,
                at_end: bool) -> Optional[tuple[int, int, float]]:
    """A monotonic approach to (start) or departure from (end) the anchor.

    Returns (first frame, end frame, signed movement in time order) or None. ``envelope``
    is the half-extent of a periodic ornament on the note: its own swings are not glides.
    """
    x = rs[::-1] if at_end else rs
    near = np.abs(x) <= float(orn["none_below_cents"])
    if near[0] or not near.any():
        return None
    k = int(np.argmax(near))  # first frame on the anchor
    path = x[:k + 1]
    net = float(path[-1] - path[0])
    extent = abs(net)
    if extent < float(orn["glide_min_cents"]):
        return None
    if float(np.abs(np.diff(path)).sum()) > MONOTONIC_SLACK * extent:
        return None
    if _rise_time_s(path, hop_s) < float(orn["glide_min_ms"]) / 1000.0:
        return None
    if envelope > 0 and abs(float(path[0])) - envelope < 0.5 * float(orn["glide_min_cents"]):
        return None
    if at_end:
        return len(rs) - 1 - k, len(rs), -net
    return 0, k + 1, net


def _grace_runs(r: np.ndarray, hop_s: float, orn: dict, taken: np.ndarray,
                before: float, after: float) -> list[tuple[int, int, float]]:
    """Short excursions to a pitch at least grace_min_cents away: (start, end, signed peak).

    ``before`` and ``after`` are the residuals of the frames just outside the note (NaN
    when unvoiced). An excursion that was already under way before the note began, or is
    still going when it ends, is the way in from or out to the neighbouring note, not a
    touch of it.
    """
    out: list[tuple[int, int, float]] = []
    far_cents = float(orn["grace_min_cents"])
    lo_frames = float(orn["grace_min_ms"]) / 1000.0 / hop_s
    hi_frames = float(orn["grace_max_ms"]) / 1000.0 / hop_s
    for sign in (1.0, -1.0):
        far = sign * r >= far_cents
        padded = np.concatenate(([False], far, [False]))
        edges = np.flatnonzero(padded[1:] != padded[:-1])
        for a, b in zip(edges[::2], edges[1::2]):
            if not (lo_frames - 1e-9 <= b - a <= hi_frames + 1e-9) or taken[a:b].any():
                continue
            if (a == 0 and sign * before >= far_cents) or (b == len(r) and sign * after >= far_cents):
                continue
            out.append((int(a), int(b), sign * float(np.max(sign * r[a:b]))))
    out.sort(key=lambda g: -abs(g[2]))
    return sorted(out[:MAX_GRACES])


def _where(a: int, b: int, n: int) -> Optional[str]:
    if a <= 0.2 * n:
        return "start"
    if b >= 0.8 * n:
        return "end"
    return None


def classify(t, cents, segment: Segment, profile: SegmentationProfile) -> list[Ornament]:
    """Ornaments on one note, dominant first. Empty when the note is plain."""
    orn = config.section("ornaments")
    a, b = int(segment.start), int(segment.end)
    cents = np.asarray(cents, dtype=float)
    x = cents[a:b]
    n = len(x)
    if n < 3 or np.isnan(x).all():
        return []
    hop_s = hop_seconds(t)
    r = _fill(x) - float(segment.anchor_cents)
    rs = smooth(r)
    found: list[Ornament] = []
    taken = np.zeros(n, dtype=bool)

    # --- vibrato or oscillation -------------------------------------------------
    # The slowest movement that still counts as periodic: the style's own limit, or the
    # neutral profile's (about 3 Hz, spec 7.10) when the style's is stricter.
    slowest_hz = min(profile.osc_min_hz, config.profile("neutral").osc_min_hz)
    max_half_period = 0.5 / slowest_hz * (1.0 + HALF_PERIOD_SLACK)
    per = _measure_periodic(r, rs, hop_s, float(orn["none_below_cents"]), max_half_period)
    envelope = 0.0
    if per is not None and per.half_extent >= float(orn["none_below_cents"]):
        min_symmetry = float(orn.get("vibrato_min_symmetry", orn["vibrato_min_regularity"]))
        is_vibrato = (
            per.half_extent <= float(orn["vibrato_max_cents"])
            and float(orn["vibrato_min_hz"]) <= per.rate_hz <= float(orn["vibrato_max_hz"])
            and per.cycles >= float(orn["vibrato_min_cycles"])
            and per.regularity >= float(orn["vibrato_min_regularity"])
            and per.symmetry >= min_symmetry
        )
        kind = "vibrato" if is_vibrato else (
            "oscillation" if per.half_extent >= float(orn["oscillation_min_cents"]) else None)
        if kind is not None:
            coverage = (per.end - per.start) / n
            confidence = 0.4 + 0.25 * per.regularity + 0.25 * min(1.0, per.cycles / 3.0)
            confidence += 0.1 * per.symmetry if kind == "vibrato" else 0.1 * min(1.0, coverage / 0.5)
            found.append(Ornament(
                kind=kind, contour_ref=(a + per.start, a + per.end),
                amplitude_cents=round(per.half_extent, 2), rate_hz=round(per.rate_hz, 3),
                shape="sine", cycles=round(per.cycles, 2),
                position="whole" if coverage >= 0.6 else _where(per.start, per.end, n),
                coverage=round(min(1.0, coverage), 4), confidence=round(min(confidence, 0.95), 3),
            ))
            taken[per.start:per.end] = True
            envelope = per.half_extent

    # --- glides at the edges ----------------------------------------------------
    for at_end in (False, True):
        g = _edge_glide(rs, hop_s, orn, envelope, at_end=at_end)
        if g is None:
            continue
        g0, g1, net = g
        extent = abs(net)
        confidence = 0.5 + 0.4 * min(1.0, extent / (2.0 * float(orn["glide_min_cents"])))
        found.append(Ornament(
            kind="glide", contour_ref=(a + g0, a + g1), amplitude_cents=round(extent, 2),
            shape="up" if net > 0 else "down", position="end" if at_end else "start",
            coverage=round((g1 - g0) / n, 4), confidence=round(min(confidence, 0.95), 3),
        ))
        taken[g0:g1] = True

    # --- grace touches ----------------------------------------------------------
    before = float(cents[a - 1] - segment.anchor_cents) if a > 0 else float("nan")
    after = float(cents[b] - segment.anchor_cents) if b < len(cents) else float("nan")
    for g0, g1, peak in _grace_runs(r, hop_s, orn, taken, before, after):
        confidence = 0.45 + 0.35 * min(1.0, abs(peak) / (3.0 * float(orn["grace_min_cents"])))
        found.append(Ornament(
            kind="grace", contour_ref=(a + g0, a + g1), amplitude_cents=round(abs(peak), 2),
            shape="touch_above" if peak > 0 else "touch_below", position=_where(g0, g1, n),
            coverage=round((g1 - g0) / n, 4), confidence=round(confidence, 3),
        ))

    found.sort(key=lambda o: (-o.coverage, _KIND_ORDER.get(o.kind or "", 9)))
    return found


def annotate(segments: list[Segment], t, cents, profile: SegmentationProfile) -> list[Segment]:
    """Copies of ``segments`` with ``.ornaments`` filled, dominant first."""
    return [replace(seg, ornaments=classify(t, cents, seg, profile)) for seg in segments]
