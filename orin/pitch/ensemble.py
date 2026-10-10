"""Tracker selection and the ensemble rule (spec 7.5).

``track_pitch`` is the entry point: torchcrepe first, librosa pYIN as the fallback, both
at 16 kHz with a 160-sample hop so their frames coincide. With two trackers the
ensemble keeps the primary one wherever they agree and, where they differ by more than
a semitone (almost always an octave error), takes the more confident and more
continuous one and counts the frame as a disagreement.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

import numpy as np

from ..analysis_types import PitchTrack
from ..config import section
from .voicing import hz_to_cents, running_median

MIN_CONF = 0.25  # both trackers must be at least this sure before a difference counts as a disagreement
CONTINUITY_HALF_FRAMES = 5
GATE_DB = -50.0  # frames this far under the loud level are silence, whatever a tracker says

_ORDER = {"auto": ("crepe",), "crepe": ("crepe",), "pyin": ("pyin",), "both": ("crepe", "pyin")}


def _continuity(cents: np.ndarray) -> np.ndarray:
    """1 where a frame sits on its own track's local median, falling off with the jump."""
    dev = np.abs(cents - running_median(cents, CONTINUITY_HALF_FRAMES))
    return np.where(np.isfinite(dev), np.exp(-dev / 100.0), 0.0)


def _pair(a: PitchTrack, b: PitchTrack) -> PitchTrack:
    n = min(len(a.f0), len(b.f0))
    ca, cb = hz_to_cents(a.f0[:n]), hz_to_cents(b.f0[:n])
    pa = np.where(np.isfinite(ca), a.conf[:n], 0.0)
    pb = np.where(np.isfinite(cb), b.conf[:n], 0.0)
    score_a = pa * (0.5 + 0.5 * _continuity(ca))
    score_b = pb * (0.5 + 0.5 * _continuity(cb))

    both = np.isfinite(ca) & np.isfinite(cb)
    apart = both & (np.abs(ca - cb) > float(section("pitch")["disagree_cents"]))
    disagree = apart & (pa >= MIN_CONF) & (pb >= MIN_CONF)
    # Agreement keeps the primary's pitch (one tracker's contour, not a blend of two) with
    # the higher of the two confidences. Otherwise the better-scoring tracker wins outright.
    agree = both & ~apart
    take_b = ~agree & (score_b > score_a)
    f0 = np.where(take_b, b.f0[:n], a.f0[:n])
    conf = np.where(agree, np.maximum(pa, pb), np.where(take_b, pb, pa))
    return PitchTrack(
        t=a.t[:n], f0=f0, conf=conf, hop_s=a.hop_s, tracker="ensemble",
        disagreements=a.disagreements + b.disagreements + int(disagree.sum()),
        octave_fixes=a.octave_fixes + b.octave_fixes)


def ensemble(tracks: Sequence[PitchTrack]) -> PitchTrack:
    """Combine trackers on the same 10 ms grid. The first track is the primary."""
    tracks = [t for t in tracks if t is not None]
    if not tracks:
        raise ValueError("ensemble() needs at least one pitch track")
    out = tracks[0]
    for other in tracks[1:]:
        out = _pair(out, other)
    return out


def _gate(track: PitchTrack, y: np.ndarray, hop: int) -> PitchTrack:
    """Silence is unvoiced: zero the confidence of frames far below the recording's loud level."""
    n = len(track.f0)
    if n == 0 or len(y) == 0:
        return track
    frame = 4 * hop
    padded = np.pad(np.square(y.astype(np.float64)), (frame // 2, frame))
    csum = np.concatenate([[0.0], np.cumsum(padded)])
    starts = np.arange(n) * hop
    rms = np.sqrt(np.maximum(csum[starts + frame] - csum[starts], 0.0) / frame)
    loud = float(np.percentile(rms, 95))
    if loud <= 0:
        return track
    quiet = rms < loud * 10.0 ** (GATE_DB / 20.0)
    if quiet.any():
        track.conf = np.where(quiet, 0.0, track.conf)
        track.f0 = np.where(quiet, np.nan, track.f0)
    return track


def _run(name: str) -> Callable[..., PitchTrack]:
    if name == "crepe":
        from .crepe import track_crepe

        return track_crepe
    from .pyin import track_pyin

    return track_pyin


def track_pitch(y16k: np.ndarray, *, voice: str = "auto", trackers: str = "auto",
                failures: Optional[list[str]] = None) -> PitchTrack:
    """16 kHz mono -> raw PitchTrack (a pitch and a confidence per 10 ms frame; times start at 0).

    ``trackers``: "auto" and "crepe" use torchcrepe, "pyin" uses pYIN, "both" runs the
    ensemble. Whatever was asked for, a tracker that cannot be imported or fails is
    skipped and pYIN takes over; the reason is appended to ``failures`` when given.
    Voicing is not decided here: see ``voicing.clean_voicing``.
    """
    cfg = section("pitch")
    presets = cfg["presets"]
    fmin, fmax = presets.get(voice, presets["auto"])
    hop = int(cfg["hop_samples"])
    y = np.asarray(y16k, dtype=np.float32)

    tracks: list[PitchTrack] = []
    for name in _ORDER.get(trackers, _ORDER["auto"]):
        try:
            tracks.append(_run(name)(y, fmin=fmin, fmax=fmax))
        except ImportError as exc:
            if failures is not None:
                failures.append(f"{name}: not installed ({exc})")
        except Exception as exc:  # a tracker crash must not lose the transcription
            if failures is not None:
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if not tracks:
        tracks.append(_run("pyin")(y, fmin=fmin, fmax=fmax))
    return _gate(ensemble(tracks), y, hop)
