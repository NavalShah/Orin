"""Drone detection (spec 7.7, method 1): tanpura-like sustained partials as a tonic cue.

A drone is whatever stays put while the melody moves. A long-window spectrogram is
reduced to its "persistent spectrum": per frequency bin, the level reached in more than
``drone_min_presence`` of the frames, ignoring the cells where the melody's own
harmonics sit. Peaks of that spectrum are the drone's partials; partials are grouped
into fundamentals and fundamentals into pitch classes.

The drone is never subtracted from the audio (spec 7.3). This module only reports what
it found; ``tonic.select`` decides which class is the tonic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..analysis_types import PitchTrack
from ..config import section

REF_HZ = 55.0
BAND_HZ = (50.0, 2500.0)
MAX_FUNDAMENTAL_HZ = 700.0
MAX_FRAMES = 400
HARMONIC_TOL_CENTS = 25.0
CLASS_TOL_CENTS = 35.0
MELODY_EXCLUDE_CENTS = 40.0
MELODY_EXCLUDE_BINS = 5.0  # past the fifth bin a Hann window's side lobes are under -54 dB
MIN_OBSERVED = 0.25  # a bin hidden under the melody in more than 75% of frames cannot be judged
PEAK_NEIGHBOUR_BINS = 6  # rejects a window's side lobes next to its main lobe
PEAK_OVER_FLOOR = 4.0  # 12 dB above the local floor
PEAK_RANGE_DB = 36.0  # partials this far under the strongest one are ignored
STATIONARY_RATIO = 0.3  # persistent level against the bin's own loud level
BELOW_LOUDEST_DB = 45.0  # a drone is quieter than the melody, but not by more than this
PARTIALS_FOR_FULL_CONFIDENCE = 6
DRONE_ONLY_LEVEL = 2.0  # a frame no louder than this times the drone's own level holds no melody
DRONE_ONLY_WINDOW_S = 0.05


@dataclass
class DronePartial:
    hz: float
    amp: float
    presence: float


@dataclass
class DroneClass:
    hz: float  # lowest persistent fundamental of this pitch class
    pc_cents: float  # folded cents above REF_HZ
    energy: float  # summed level of the persistent partials at hz, 2 hz, 4 hz ...
    octaves: int  # how many of those octave positions hold a partial
    presence: float
    n_partials: int = 0


@dataclass
class DroneResult:
    confidence: float = 0.0  # 0..1 that a drone is present at all
    classes: list[DroneClass] = field(default_factory=list)  # most energy first
    partials: list[DronePartial] = field(default_factory=list)
    rms: float = 0.0  # the drone's own level, in the audio's units

    @property
    def lowest(self) -> Optional[DroneClass]:
        return min(self.classes, key=lambda c: c.hz) if self.classes else None


def _fold(cents: float) -> float:
    return float(cents % 1200.0)


def _circ(a: float, b: float) -> float:
    return ((a - b + 600.0) % 1200.0) - 600.0


def _melody_mask(freqs: np.ndarray, centres_s: np.ndarray, half_window_s: float,
                 track: Optional[PitchTrack]) -> np.ndarray:
    """True where a (frame, bin) cell lies on a harmonic of the melody sounding in that frame."""
    mask = np.zeros((len(centres_s), len(freqs)), dtype=bool)
    if track is None or len(track.f0) == 0:
        return mask
    bin_hz = freqs[1] - freqs[0]
    t0, hop = float(track.t[0]), float(track.hop_s)
    f0 = np.asarray(track.f0, dtype=float)
    for i, centre in enumerate(centres_s):
        a = int(np.floor((centre - half_window_s - t0) / hop))
        b = int(np.ceil((centre + half_window_s - t0) / hop)) + 1
        seg = f0[max(0, a):max(0, b)]
        seg = seg[np.isfinite(seg) & (seg > 0)]
        if seg.size == 0:
            continue
        # One representative per 50-cent step is enough: the exclusion is wider than that.
        pitches = np.unique(np.round(24.0 * np.log2(seg / REF_HZ))) / 24.0
        for p in REF_HZ * 2.0 ** pitches:
            k = np.arange(1, int(BAND_HZ[1] // p) + 1)
            if k.size == 0:
                continue
            harm = p * k
            width = np.maximum(harm * (2.0 ** (MELODY_EXCLUDE_CENTS / 1200.0) - 1.0), MELODY_EXCLUDE_BINS * bin_hz)
            lo = np.searchsorted(freqs, harm - width)
            hi = np.searchsorted(freqs, harm + width)
            for x, y in zip(lo, hi):
                mask[i, x:y] = True
    return mask


def _parabolic(q: np.ndarray, b: int) -> float:
    """Sub-bin peak position from a parabola through the log levels around bin ``b``."""
    if b <= 0 or b >= len(q) - 1:
        return 0.0
    a0, a1, a2 = (np.log(max(float(q[i]), 1e-12)) for i in (b - 1, b, b + 1))
    denom = a0 - 2.0 * a1 + a2
    return 0.0 if abs(denom) < 1e-12 else float(np.clip(0.5 * (a0 - a2) / denom, -0.5, 0.5))


def find_drone(audio: np.ndarray, sr: int, track: Optional[PitchTrack] = None, *,
               audio_offset_s: float = 0.0, min_presence: Optional[float] = None) -> DroneResult:
    """Look for a drone in ``audio``. ``track`` is the melody (so its harmonics can be
    ignored); ``audio_offset_s`` is the time of ``audio[0]`` in the track's time base."""
    from scipy.ndimage import maximum_filter1d, median_filter

    if min_presence is None:
        min_presence = float(section("tonic")["drone_min_presence"])
    y = np.asarray(audio, dtype=np.float64)
    if y.ndim > 1:
        y = y.mean(axis=0)
    n_fft = 8192 if sr >= 16000 else 4096
    if len(y) < n_fft * 3:
        return DroneResult()
    hop = max(n_fft // 4, int(np.ceil((len(y) - n_fft) / MAX_FRAMES)))
    n_frames = 1 + (len(y) - n_fft) // hop
    if n_frames < 6:
        return DroneResult()

    freqs_all = np.fft.rfftfreq(n_fft, 1.0 / sr)
    band = (freqs_all >= BAND_HZ[0]) & (freqs_all <= min(BAND_HZ[1], sr / 2.0 - 1.0))
    freqs = freqs_all[band]
    window = np.hanning(n_fft)
    spec = np.empty((n_frames, int(band.sum())))
    for i in range(n_frames):
        spec[i] = np.abs(np.fft.rfft(y[i * hop:i * hop + n_fft] * window))[band]
    if not np.any(spec > 0):
        return DroneResult()

    centres = (np.arange(n_frames) * hop + n_fft / 2.0) / sr + audio_offset_s
    excluded = _melody_mask(freqs, centres, 0.3 * n_fft / sr, track)
    observed = 1.0 - excluded.mean(axis=0)
    masked = np.where(excluded, np.nan, spec)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        persistent = np.nanpercentile(masked, 100.0 * (1.0 - min_presence), axis=0)
        loud = np.nanpercentile(masked, 95.0, axis=0)
    persistent = np.where((observed >= MIN_OBSERVED) & np.isfinite(persistent), persistent, 0.0)
    loud = np.where(np.isfinite(loud), loud, 0.0)

    loudest = float(np.percentile(spec, 99.9))
    floor = median_filter(persistent, size=81, mode="nearest")
    is_peak = (
        (persistent > 0)
        & (persistent >= maximum_filter1d(persistent, size=2 * PEAK_NEIGHBOUR_BINS + 1, mode="nearest"))
        & (persistent >= PEAK_OVER_FLOOR * np.maximum(floor, 1e-12))
        & (persistent >= STATIONARY_RATIO * np.maximum(loud, 1e-12))
        & (persistent >= loudest * 10.0 ** (-BELOW_LOUDEST_DB / 20.0))
    )
    idx = np.flatnonzero(is_peak)
    if idx.size:
        idx = idx[persistent[idx] >= persistent[idx].max() * 10.0 ** (-PEAK_RANGE_DB / 20.0)]
    if idx.size == 0:
        return DroneResult()

    bin_hz = freqs[1] - freqs[0]
    partials: list[DronePartial] = []
    for b in idx:
        level = persistent[b]
        seen = ~excluded[:, b]
        presence = float(np.mean(spec[seen, b] >= 0.5 * level)) if seen.any() else 0.0
        partials.append(DronePartial(hz=float(freqs[b] + _parabolic(persistent, int(b)) * bin_hz),
                                     amp=float(level), presence=presence))
    partials.sort(key=lambda p: p.hz)

    # Group partials into harmonic series, lowest first. A partial that is a whole multiple
    # of a lower fundamental belongs to it; anything else starts a new series.
    fundamentals: list[dict] = []
    for p in partials:
        owner = None
        for f in fundamentals:
            k = int(round(p.hz / f["hz"]))
            if k >= 2 and abs(1200.0 * np.log2(p.hz / (k * f["hz"]))) <= HARMONIC_TOL_CENTS:
                owner, order = f, k
                break
        if owner is None:
            if p.hz <= MAX_FUNDAMENTAL_HZ:
                fundamentals.append({"hz": p.hz, "members": [(1, p)]})
            continue
        owner["members"].append((order, p))
    for f in fundamentals:  # refine each fundamental from all of its harmonics
        w = np.array([p.amp for _, p in f["members"]])
        f["hz"] = float(np.sum(w * np.array([p.hz / k for k, p in f["members"]])) / np.sum(w))

    classes: list[DroneClass] = []
    for f in sorted(fundamentals, key=lambda f: f["hz"]):
        pc = _fold(1200.0 * np.log2(f["hz"] / REF_HZ))
        if any(abs(_circ(pc, c.pc_cents)) <= CLASS_TOL_CENTS for c in classes):
            continue  # an upper octave of a class already found
        octave_partials = [
            p for p in partials
            if p.hz >= f["hz"] * 0.98
            and abs(_circ(1200.0 * np.log2(p.hz / f["hz"]), 0.0)) <= HARMONIC_TOL_CENTS
        ]
        classes.append(DroneClass(
            hz=f["hz"], pc_cents=pc, energy=float(sum(p.amp for p in octave_partials)),
            octaves=len(octave_partials),
            presence=float(np.mean([p.presence for _, p in f["members"]])), n_partials=len(f["members"])))
    classes.sort(key=lambda c: c.energy, reverse=True)

    confidence = float(min(1.0, len(partials) / PARTIALS_FOR_FULL_CONFIDENCE)) if classes else 0.0
    # A sinusoid of amplitude A peaks at A * n_fft / 4 under a Hann window.
    amplitudes = np.array([4.0 * p.amp / n_fft for p in partials])
    return DroneResult(confidence=confidence, classes=classes, partials=partials,
                       rms=float(np.sqrt(np.sum(amplitudes ** 2) / 2.0)))


def drone_only_frames(track: PitchTrack, audio: np.ndarray, sr: int, drone: DroneResult, *,
                      audio_offset_s: float = 0.0) -> np.ndarray:
    """Mask of track frames where the tracker followed the drone, not the melody.

    When the singer pauses, a pitch tracker locks onto the tanpura and would report long
    notes on Sa or Pa that nobody sang. Such a frame has a pitch on one of the drone's
    classes and is no louder than the drone alone.
    """
    n = len(track.f0)
    mask = np.zeros(n, dtype=bool)
    if n == 0 or drone.rms <= 0 or not drone.classes or drone.confidence < 0.5:
        return mask
    y = np.asarray(audio, dtype=np.float64)
    if y.ndim > 1:
        y = y.mean(axis=0)
    half = max(1, int(DRONE_ONLY_WINDOW_S * sr / 2))
    csum = np.concatenate([[0.0], np.cumsum(y ** 2)])
    centre = np.round((np.asarray(track.t, dtype=float) - audio_offset_s) * sr).astype(int)
    lo, hi = np.clip(centre - half, 0, len(y)), np.clip(centre + half, 0, len(y))
    level = np.sqrt((csum[hi] - csum[lo]) / np.maximum(hi - lo, 1))
    f0 = np.asarray(track.f0, dtype=float)
    ok = np.isfinite(f0) & (f0 > 0)
    cents = np.zeros(n)
    cents[ok] = 1200.0 * np.log2(f0[ok] / REF_HZ)
    on_drone = np.zeros(n, dtype=bool)
    for c in drone.classes:
        on_drone |= np.abs(((cents - c.pc_cents + 600.0) % 1200.0) - 600.0) <= CLASS_TOL_CENTS
    return ok & on_drone & (level <= DRONE_ONLY_LEVEL * drone.rms)
