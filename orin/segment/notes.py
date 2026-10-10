"""Note segmentation on a cents contour (spec 7.9).

The prototype split any note whose gamaka was wider than about 60 cents. Here the
order of work prevents that:

  1. voiced runs        split at unvoiced gaps (short gaps were bridged by the caller)
  2. oscillation regions found FIRST and never split: a run of alternating extrema
                        at an oscillation rate, around a bounded midline, is one note
  3. plateaus           elsewhere, with hysteresis: a new note needs a stable level more
                        than ``plateau_enter_cents`` away; a plateau is left only beyond
                        ``plateau_exit_cents``
  4. out-and-back       an excursion that returns to the same plateau in time is an
                        ornament of that note, unless it rested on its own pitch
  5. glide frames       between two plateaus belong to the following note
  6. short fragments    merge into the neighbour closest in pitch
  7. onset splits       a strong onset inside a long steady note re-articulates it

Every threshold comes from the segmentation profile (orin/config/styles.toml). The
constants below are shapes (ratios and frame counts), not musical thresholds.
"""
from __future__ import annotations

from bisect import insort
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .. import config
from ..analysis_types import Segment
from ..config import SegmentationProfile

SMOOTH_FRAMES = 3  # light smoothing before looking for extrema and plateaus
HALF_PERIOD_SLACK = 0.15  # tolerance on the profile's oscillation-rate limits
STEADY_RANGE_SHARE = 0.75  # a window is steady when its range is under this share of plateau_exit_cents
REVERSAL_SHARE = 1.5  # a turn counts as an extremum after this many times ornaments.none_below_cents
ONSET_PEAK_SHARE = 0.3  # an onset peak is strong above this share of the largest peak
ONSET_FLOOR = 0.15  # ...and above this, on the 0..1 scale of orin.rhythm.onsets.onset_envelope
ONSET_DIP_SHARE = 0.5  # ...when the envelope before it fell under this share of the peak
ONSET_SEPARATION_S = 0.1  # ...and it is the largest peak this close on either side
ONSET_STEADY_FRAMES = 2  # the pitch must sit on the plateau this many frames either side of a split
UNSETTLED_STABILITY = 0.3  # stability ceiling for a note with no plateau at all
# Step 4 treats an excursion that returns in time as an ornament. One that RESTS on its
# own pitch for at least this share of out_and_back_ms is a short note instead (S R S at
# speed), not a touch. Set to 1.0 for the bare rule.
OUT_AND_BACK_DWELL_SHARE = 0.5


# ---------------------------------------------------------------------------
# small array helpers (shared with segment.ornaments)
# ---------------------------------------------------------------------------

def hop_seconds(t) -> float:
    t = np.asarray(t, dtype=float)
    if len(t) < 2:
        return 0.01
    d = np.diff(t)
    d = d[d > 0]
    return float(np.median(d)) if len(d) else 0.01


def smooth(x: np.ndarray, k: int = SMOOTH_FRAMES) -> np.ndarray:
    """Moving average that keeps the array length and does not sag at the edges."""
    x = np.asarray(x, dtype=float)
    if k <= 1 or len(x) < k:
        return x.copy()
    kernel = np.ones(k)
    return np.convolve(x, kernel, mode="same") / np.convolve(np.ones(len(x)), kernel, mode="same")


def weighted_median(x: np.ndarray, w: Optional[np.ndarray] = None) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return float("nan")
    if w is None:
        return float(np.median(x))
    w = np.nan_to_num(np.asarray(w, dtype=float), nan=0.0)
    if w.sum() <= 0:
        return float(np.median(x))
    order = np.argsort(x, kind="stable")
    cum = np.cumsum(w[order])
    return float(x[order][min(int(np.searchsorted(cum, 0.5 * cum[-1])), len(x) - 1)])


def find_extrema(s: np.ndarray, delta: float) -> list[tuple[int, float, int]]:
    """Turning points of ``s`` as (index, value, +1 max / -1 min), strictly alternating.

    A turn is confirmed once the contour has come back by ``delta``. A flat top or bottom
    is placed at its centre. The start of the array and an unconfirmed last swing are not
    turning points and are left out.
    """
    out: list[tuple[int, float, int]] = []
    if len(s) < 3:
        return out
    eps = 0.1 * delta
    hi = lo = float(s[0])
    hi_a = hi_b = lo_a = lo_b = 0
    direction = 0
    for i in range(1, len(s)):
        v = float(s[i])
        if direction >= 0:
            if v > hi + eps:
                hi, hi_a, hi_b = v, i, i
            elif v >= hi - eps:
                hi, hi_b = max(hi, v), i
        if direction <= 0:
            if v < lo - eps:
                lo, lo_a, lo_b = v, i, i
            elif v <= lo + eps:
                lo, lo_b = min(lo, v), i
        if direction >= 0 and v < hi - delta:
            if direction > 0:
                out.append(((hi_a + hi_b) // 2, hi, 1))
            direction = -1
            lo, lo_a, lo_b = v, i, i
        elif direction <= 0 and v > lo + delta:
            if direction < 0:
                out.append(((lo_a + lo_b) // 2, lo, -1))
            direction = 1
            hi, hi_a, hi_b = v, i, i
    return out


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of each run of True."""
    if len(mask) == 0:
        return []
    padded = np.concatenate(([False], mask.astype(bool), [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]


# ---------------------------------------------------------------------------
# step 2: oscillation regions
# ---------------------------------------------------------------------------

def find_osc_regions(s: np.ndarray, hop_s: float, p: SegmentationProfile,
                     delta: float) -> list[tuple[int, int, int, int]]:
    """(start, end, core start, core end) frame ranges of ``s`` that are one oscillating
    note. The core runs from the first turning point to the last; [start, end) adds the
    partial swings on either side.

    A pair of neighbouring extrema belongs to a region when its half-extent is at most
    ``max_merge_half_extent_cents`` and its half-period is an oscillation rate
    (``osc_min_hz``..``osc_max_hz``). The midline (midpoints of successive extrema) must
    stay within ``plateau_enter_cents`` of its running median; where it moves on, a new
    region starts.
    """
    ext = find_extrema(s, delta)
    if len(ext) < 3:
        return []
    # Turning points are found to the nearest frame, hence the half hop either way.
    hp_lo = 0.5 / p.osc_max_hz * (1.0 - HALF_PERIOD_SLACK) - 0.5 * hop_s
    hp_hi = 0.5 / p.osc_min_hz * (1.0 + HALF_PERIOD_SLACK) + 0.5 * hop_s
    n = len(s)
    regions: list[tuple[int, int, int, int]] = []
    prev_end = 0
    k = 0
    while k + 2 < len(ext):
        mids: list[float] = []
        j = k
        while j + 1 < len(ext):
            (i0, v0, _), (i1, v1, _) = ext[j], ext[j + 1]
            half_period = (i1 - i0) * hop_s
            half_extent = abs(v1 - v0) / 2.0
            mid = (v0 + v1) / 2.0
            ok = half_extent <= p.max_merge_half_extent_cents and hp_lo <= half_period <= hp_hi
            if ok and mids and abs(mid - float(np.median(mids))) > p.plateau_enter_cents:
                ok = False
            if not ok:
                break
            mids.append(mid)
            j += 1
        if j - k < 2:  # fewer than 3 extrema
            k += 1
            continue
        idx = [e[0] for e in ext[k:j + 1]]
        vals = [e[1] for e in ext[k:j + 1]]
        lo, hi = min(vals) - delta, max(vals) + delta
        # The partial swings before the first and after the last turning point belong to
        # the same movement: take up to one period of them while they stay in the envelope.
        reach = max(3, int(round(2.0 * float(np.median(np.diff(idx))))))
        a = idx[0]
        while a > prev_end and idx[0] - a < reach and lo <= s[a - 1] <= hi:
            a -= 1
        b = idx[-1] + 1
        while b < n and b - 1 - idx[-1] < reach and lo <= s[b] <= hi:
            b += 1
        a = max(a, prev_end)
        if b > a:
            regions.append((a, b, max(a, idx[0]), idx[-1] + 1))
            prev_end = b
        k = j
    return regions


# ---------------------------------------------------------------------------
# steps 3 to 5: plateaus with hysteresis
# ---------------------------------------------------------------------------

@dataclass
class _Proto:
    """A note being built. Indexes are relative to the voiced run."""

    start: int
    end: int = 0
    members: list[int] = field(default_factory=list)  # plateau and oscillation frames
    values: list[float] = field(default_factory=list)  # member values, kept sorted
    osc: list[tuple[int, int]] = field(default_factory=list)  # cores of oscillation regions
    settled: bool = True
    onset_split: bool = False

    @property
    def level(self) -> float:
        return self.values[len(self.values) // 2] if self.values else float("nan")

    def add(self, i: int, v: float) -> None:
        self.members.append(i)
        insort(self.values, float(v))


def _min_frames(p: SegmentationProfile, hop_s: float) -> int:
    """Frames a stretch needs so that it spans min_note_ms from its first frame to its last."""
    return max(2, int(round(p.min_note_ms / 1000.0 / hop_s)) + 1)


def _segment_run(s: np.ndarray, hop_s: float, p: SegmentationProfile, delta: float) -> list[_Proto]:
    n = len(s)
    w = _min_frames(p, hop_s)
    steady_range = STEADY_RANGE_SHARE * p.plateau_exit_cents
    regions = {a: (b, core_a, core_b) for a, b, core_a, core_b in find_osc_regions(s, hop_s, p, delta)}
    region_starts = sorted(regions)

    steady = np.zeros(n, dtype=bool)  # steady[i]: frames i..i+w-1 hold one level
    window_median = np.full(n, np.nan)
    if n >= w:
        win = sliding_window_view(s, w)
        ok = (win.max(axis=1) - win.min(axis=1)) <= steady_range
        steady[:len(ok)] = ok
        window_median[:len(ok)] = np.median(win, axis=1)
    in_steady = np.convolve(steady.astype(int), np.ones(w, dtype=int))[:n] > 0

    out_and_back = int(round(p.out_and_back_ms / 1000.0 / hop_s))
    max_dwell = OUT_AND_BACK_DWELL_SHARE * out_and_back
    hold = max(2, w // 2)
    protos: list[_Proto] = []
    cur: Optional[_Proto] = None
    away: Optional[int] = None  # first frame of the current stretch outside the plateau

    def comes_back(i: int) -> bool:
        """Step 4: is the excursion that began at ``away`` a touch that returns to the
        plateau in time, without resting on the pitch it reached at frame ``i``?"""
        assert cur is not None and away is not None
        level = cur.level
        last = min(n, away + out_and_back + 1)
        nxt = next((r for r in region_starts if r > i), n)
        for r in range(i + 1, min(last, nxt)):
            if abs(s[r] - level) <= p.plateau_exit_cents:
                back = s[r:r + hold]
                if not np.all(np.abs(back - level) <= p.plateau_exit_cents):
                    return False
                rested = np.abs(s[i:r] - window_median[i]) <= steady_range
                dwell = len(rested) if rested.all() else int(np.argmin(rested))
                return dwell < max_dwell
        return False

    def glide_start(i: int) -> int:
        """Step 5: the moving frames just before a new plateau go to the new note."""
        floor = away if away is not None else i
        b = i
        while b > floor and not in_steady[b - 1]:
            b -= 1
        return b

    def close(at: int) -> None:
        assert cur is not None
        cur.end = at
        protos.append(cur)

    i = 0
    while i < n:
        if i in regions:
            end, core_a, core_b = regions[i]
            level = float(np.median(s[i:end]))
            if cur is not None and abs(level - cur.level) > p.plateau_enter_cents:
                boundary = glide_start(i)
                close(boundary)
                cur = _Proto(start=boundary)
            elif cur is None:
                cur = _Proto(start=0)
            for k in range(i, end):
                cur.add(k, s[k])
            cur.osc.append((core_a, core_b))
            away = None
            i = end
            continue
        v = float(s[i])
        if cur is None:
            if steady[i]:
                cur = _Proto(start=0)
                cur.add(i, v)
        elif abs(v - cur.level) <= p.plateau_exit_cents:
            cur.add(i, v)
            away = None
        else:
            if away is None:
                away = i
            if (steady[i] and abs(window_median[i] - cur.level) > p.plateau_enter_cents
                    and not comes_back(i)):
                boundary = glide_start(i)
                close(boundary)
                cur = _Proto(start=boundary)
                cur.add(i, v)
                away = None
        i += 1

    if cur is None:  # nothing ever settled: one unsettled note
        cur = _Proto(start=0, settled=False)
    close(n)
    return [pr for pr in protos if pr.end > pr.start]


# ---------------------------------------------------------------------------
# step 6: short fragments
# ---------------------------------------------------------------------------

def _level(pr: _Proto, s: np.ndarray) -> float:
    return pr.level if pr.values else float(np.median(s[pr.start:pr.end]))


def _absorb(keep: _Proto, other: _Proto) -> None:
    keep.start, keep.end = min(keep.start, other.start), max(keep.end, other.end)
    keep.members = sorted(keep.members + other.members)
    keep.values = sorted(keep.values + other.values)
    keep.osc = sorted(keep.osc + other.osc)
    keep.settled = keep.settled or other.settled


def _merge_alike(protos: list[_Proto], s: np.ndarray, p: SegmentationProfile) -> list[_Proto]:
    """Neighbours in one voiced run must sit more than ``plateau_enter_cents`` apart to be
    two notes. A slow wobble can open a note on each of its turns; once their levels are
    measured over the whole note they turn out to be one plateau, and are joined here."""
    protos = list(protos)
    k = 0
    while k + 1 < len(protos):
        if abs(_level(protos[k], s) - _level(protos[k + 1], s)) <= p.plateau_enter_cents:
            _absorb(protos[k], protos[k + 1])
            del protos[k + 1]
            k = max(0, k - 1)  # the joined note has a new level: compare it with the one before
        else:
            k += 1
    return protos


def _merge_fragments(protos: list[_Proto], s: np.ndarray, min_frames: int) -> list[_Proto]:
    protos = list(protos)
    while len(protos) > 1:
        short = [k for k, pr in enumerate(protos) if pr.end - pr.start < min_frames]
        if not short:
            break
        k = min(short, key=lambda q: protos[q].end - protos[q].start)
        neighbours = [q for q in (k - 1, k + 1) if 0 <= q < len(protos)]
        target = min(neighbours, key=lambda q: abs(_level(protos[q], s) - _level(protos[k], s)))
        keep, frag = protos[target], protos[k]
        keep.start, keep.end = min(keep.start, frag.start), max(keep.end, frag.end)  # its frames, not its level
        del protos[k]
    return protos


# ---------------------------------------------------------------------------
# step 7: onset splits
# ---------------------------------------------------------------------------

def _onset_peak_times(env, env_t, look_back_s: float) -> list[float]:
    """Times of the strong peaks of an onset envelope: well above the rest, the largest
    nearby, and preceded by a dip."""
    env = np.nan_to_num(np.asarray(env, dtype=float), nan=0.0)
    env_t = np.asarray(env_t, dtype=float)
    if len(env) < 3 or len(env) != len(env_t) or float(env.max()) <= 0:
        return []
    hop = hop_seconds(env_t)
    threshold = max(ONSET_PEAK_SHARE * float(env.max()), ONSET_FLOOR)
    back = max(1, int(round(look_back_s / hop)))
    apart = max(1, int(round(ONSET_SEPARATION_S / hop)))
    out: list[float] = []
    for k in np.flatnonzero(env >= threshold):
        lo = max(0, k - apart)
        if lo + int(np.argmax(env[lo:k + apart + 1])) != k:
            continue
        before = env[max(0, k - back):k]
        if len(before) == 0 or float(before.min()) <= ONSET_DIP_SHARE * env[k]:
            out.append(float(env_t[k]))
    return out


def _split_at_onsets(protos: list[_Proto], onset_frames: list[int], min_frames: int) -> list[_Proto]:
    """Re-articulated notes: split a long steady note at a strong onset. Never inside an
    oscillation, and never where the pitch is moving (a glide has its own spectral flux)."""
    if not onset_frames:
        return protos
    out: list[_Proto] = []
    for pr in protos:
        on_plateau = set(pr.members)
        cuts: list[int] = []
        for f in onset_frames:
            last = cuts[-1] if cuts else pr.start
            if f - last < min_frames or pr.end - f < min_frames:
                continue
            if any(a <= f < b for a, b in pr.osc):
                continue
            if not all(k in on_plateau for k in range(f - ONSET_STEADY_FRAMES, f + ONSET_STEADY_FRAMES + 1)):
                continue
            cuts.append(f)
        if not cuts:
            out.append(pr)
            continue
        bounds = [pr.start, *cuts, pr.end]
        for n_part, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
            part = _Proto(start=a, end=b, settled=pr.settled,
                          onset_split=pr.onset_split if n_part == 0 else True)
            part.members = [m for m in pr.members if a <= m < b]
            part.osc = [(x, y) for x, y in pr.osc if a <= x < b]
            out.append(part)
    return out


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------

def _to_segment(pr: _Proto, offset: int, cents: np.ndarray, conf: np.ndarray, p: SegmentationProfile) -> Segment:
    a, b = offset + pr.start, offset + pr.end
    x = cents[a:b]
    voiced = ~np.isnan(x)
    members = np.asarray([offset + m for m in pr.members if pr.start <= m < pr.end], dtype=int)
    members = members[~np.isnan(cents[members])] if len(members) else members
    plateau = members if len(members) else np.arange(a, b)[voiced]
    anchor = weighted_median(cents[plateau], conf[plateau])
    xv = x[voiced]
    mean_conf = float(np.mean(conf[a:b][voiced])) if voiced.any() else 0.0
    if pr.settled and len(members):
        stability = float(np.sqrt(len(members) / max(1, b - a)))
    else:
        spread = float(np.percentile(xv, 90) - np.percentile(xv, 10)) if len(xv) else 0.0
        stability = UNSETTLED_STABILITY * max(0.0, 1.0 - spread / (2.0 * p.max_merge_half_extent_cents))
    return Segment(
        start=int(a), end=int(b),
        anchor_cents=float(anchor),
        median_cents=float(np.median(xv)),
        min_cents=float(xv.min()), max_cents=float(xv.max()),
        voiced_fraction=float(voiced.mean()),
        confidence=float(min(max(min(mean_conf, stability), 0.0), 1.0)),
        onset_split=pr.onset_split,
    )


def segment_contour(t, cents, conf, profile: SegmentationProfile, *, onset_env=None,
                    onset_env_t=None) -> list[Segment]:
    """Contour -> notes. ``cents`` is relative to the tonic, NaN where unvoiced.

    ``onset_env`` is an optional onset-strength envelope sampled at ``onset_env_t``
    seconds (or frame-aligned with ``t`` when that is None); see ``orin.rhythm.onsets``.
    Voiced runs shorter than ``min_note_ms`` are dropped as tracker blips. Ornaments are
    filled in afterwards by ``orin.segment.ornaments.annotate``.
    """
    t = np.asarray(t, dtype=float)
    cents = np.asarray(cents, dtype=float)
    n = min(len(t), len(cents))
    if n == 0:
        return []
    t, cents = t[:n], cents[:n]
    conf = np.ones(n) if conf is None else np.nan_to_num(np.asarray(conf, dtype=float)[:n], nan=0.0)
    if len(conf) < n:
        conf = np.concatenate([conf, np.ones(n - len(conf))])
    hop_s = hop_seconds(t)
    delta = REVERSAL_SHARE * float(config.section("ornaments")["none_below_cents"])
    min_frames = _min_frames(profile, hop_s)
    shortest = max(2, int(round(profile.min_note_ms / 1000.0 / hop_s)))

    onset_frames: list[int] = []
    if onset_env is not None and len(onset_env) >= 3:
        env_t = t if onset_env_t is None else onset_env_t
        peaks = _onset_peak_times(onset_env, env_t, profile.min_note_ms / 1000.0)
        onset_frames = sorted({int(np.searchsorted(t, pt)) for pt in peaks})

    segments: list[Segment] = []
    for a, b in _true_runs(~np.isnan(cents)):
        if b - a < shortest:
            continue
        s = smooth(cents[a:b])
        protos = _segment_run(s, hop_s, profile, delta)
        protos = _merge_alike(protos, s, profile)
        protos = _merge_fragments(protos, s, min_frames)
        protos = _split_at_onsets(protos, [f - a for f in onset_frames if a < f < b], min_frames)
        segments.extend(_to_segment(pr, a, cents, conf, profile) for pr in protos)
    return segments
