"""Tonic detection (spec 7.7): everything downstream is measured from this pitch.

Order of methods:
  1. a drone, when one is present (``drone.py``);
  2. the melody's pitch histogram scored against mode tables (``histogram.py``);
  3. for the 12-TET formats, a key-profile check that can only add confidence.

Then, whatever the method: the chosen pitch class is refined to the cent from the
melody's own stable frames, placed in the octave that holds the melody's median pitch,
and returned with the two next-best candidates. A wrong tonic is the most damaging
error in the pipeline, so the confidence is deliberately modest and the UI lets the
user override it (``tonic_hz`` wins, with method "user").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from ..analysis_types import PitchTrack
from ..config import section
from ..formats.base import FormatMeta
from ..ir.models import Tonic, TonicCandidate
from . import keyprofile
from .drone import DroneResult, find_drone
from .histogram import (REF_HZ, SAME_CLASS_CENTS, Candidate, MelodyProfile, circ, histogram_candidates,
                        melody_profile, refine, score_class)

FIFTH_CENTS = 702.0
FIFTH_TOL_CENTS = 35.0
DRONE_MIN_CONFIDENCE = 0.5  # under this the "drone" is a stray partial or two
AMBIGUOUS_DRONE_CAP = 0.7  # a Ma-Sa reading, or drone cues that disagree
ROTATION_MARGIN = 0.05  # mode fits this close mean both candidates fit some mode equally well
OCTAVE_SLACK_CENTS = 50.0
ASSUMED_HZ = 220.0
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


@dataclass
class _Ranked:
    pc_cents: float  # folded, above REF_HZ
    confidence: float
    method: str


def metas_for(style: Optional[str]) -> list[FormatMeta]:
    """The formats whose mode tables are consulted: one style, or all under auto-detect."""
    from ..formats import registry

    if style and style != "auto" and registry.has(style):
        return [registry.get(style).META]
    return registry.list_meta()


def nearest_note(hz: float) -> tuple[str, float]:
    """Nearest 12-TET note at A4 = 440 Hz ("D3") and the offset from it in cents."""
    midi = 69.0 + 12.0 * float(np.log2(hz / 440.0))
    nearest = int(np.floor(midi + 0.5))
    return f"{NOTE_NAMES[nearest % 12]}{nearest // 12 - 1}", float(100.0 * (midi - nearest))


def _is_fifth_above(upper_pc: float, lower_pc: float) -> bool:
    return abs(float(circ(upper_pc - lower_pc, FIFTH_CENTS))) <= FIFTH_TOL_CENTS


def _place(pc_cents: float, median_cents: float) -> float:
    """Hz of this pitch class in the octave whose span tonic..2*tonic holds the melody's
    median pitch. A median sitting a hair under the tonic still counts as on it."""
    if not np.isfinite(median_cents):
        k = np.floor((1200.0 * np.log2(ASSUMED_HZ / REF_HZ) - pc_cents + 600.0) / 1200.0)
    else:
        k = np.floor((median_cents - pc_cents + OCTAVE_SLACK_CENTS) / 1200.0)
    return float(REF_HZ * 2.0 ** ((pc_cents + 1200.0 * k) / 1200.0))


def _rank_drone(drone: DroneResult, prof: MelodyProfile, metas: Sequence[FormatMeta]) -> list[_Ranked]:
    """Decide which drone class is the tonic.

    Two classes a fifth apart can be Sa-Pa or Ma-Sa. Both readings are scored on: energy
    and number of octaves (a tanpura has three Sa strings to one Pa or Ma), which class
    holds the lowest persistent fundamental (the low Sa string), and which one the melody
    fits better. Three classes in a chain of fifths (Ma-Sa-Pa) give the middle one.
    """
    classes = drone.classes
    fits = [max(score_class(prof, c.pc_cents, metas).score, 0.01) for c in classes]
    total_energy = sum(c.energy for c in classes) or 1.0
    total_octaves = sum(c.octaves for c in classes) or 1.0
    total_fit = sum(fits)
    lowest = drone.lowest
    scores = [0.30 * c.energy / total_energy + 0.10 * c.octaves / total_octaves
              + 0.25 * (c is lowest) + 0.35 * f / total_fit for c, f in zip(classes, fits)]

    chain_middle = None
    for i, mid in enumerate(classes):
        below = any(_is_fifth_above(mid.pc_cents, o.pc_cents) for o in classes if o is not mid)
        above = any(_is_fifth_above(o.pc_cents, mid.pc_cents) for o in classes if o is not mid)
        if below and above:
            chain_middle = i
            break
    if chain_middle is not None:
        scores[chain_middle] = max(scores) + 0.5

    order = sorted(range(len(classes)), key=lambda i: scores[i], reverse=True)
    top = order[0]
    if len(classes) == 1:
        conf = 0.8
    elif chain_middle is not None:
        conf = 0.85
    else:
        second = order[1]
        related = (_is_fifth_above(classes[top].pc_cents, classes[second].pc_cents)
                   or _is_fifth_above(classes[second].pc_cents, classes[top].pc_cents))
        if related:
            conf = float(np.clip(0.55 + 1.2 * (scores[top] - scores[second]), 0.5, 0.9))
            cues_agree = (classes[top] is lowest
                          and classes[top].energy >= max(c.energy for c in classes)
                          and fits[top] >= max(fits))
            ma_sa_reading = _is_fifth_above(classes[top].pc_cents, classes[second].pc_cents)
            if ma_sa_reading or not cues_agree:
                conf = min(conf, AMBIGUOUS_DRONE_CAP)
        else:
            conf = float(np.clip(0.4 + (scores[top] - scores[second]), 0.3, 0.6))
    conf *= min(1.0, drone.confidence)

    ranked = [_Ranked(classes[top].pc_cents, conf, "drone")]
    for i in order[1:]:
        ranked.append(_Ranked(classes[i].pc_cents, conf * 0.8 * min(1.0, scores[i] / scores[top]), "drone"))
    return ranked


def _rank_histogram(cands: Sequence[Candidate], prof: MelodyProfile, metas: Sequence[FormatMeta]) -> list[_Ranked]:
    cfg = section("tonic")
    if not cands:
        return []
    top = cands[0]
    runner_up = cands[1].score if len(cands) > 1 else 0.0
    conf = float(np.clip(0.35 + 1.5 * (top.score - runner_up), 0.15, 0.8))
    if not top.refined:
        conf *= 0.85
    if any(m.pitch_system == "12tet" for m in metas) and keyprofile.agrees(prof, _place(top.pc_cents, prof.median_cents)):
        conf = min(0.9, conf + keyprofile.AGREEMENT_BONUS)
    # Two candidates that each fit some mode about equally are rotations of one pitch set:
    # only the phrase ending and the longest note separate them, which is weak evidence.
    # Fewer than three pitch classes say almost nothing about which of them is the tonic.
    rotations = len(cands) > 1 and cands[1].mode_fit >= top.mode_fit - ROTATION_MARGIN
    if rotations or len(cands) < 3:
        conf = min(conf, float(cfg["rotation_confidence_cap"]))
    out = [_Ranked(top.pc_cents, conf, "histogram")]
    for c in cands[1:]:
        out.append(_Ranked(c.pc_cents, conf * float(np.exp(-(top.score - c.score) / 0.2)) * 0.9, "histogram"))
    return out


def _merge(primary: list[_Ranked], extra: list[_Ranked], scale: float) -> list[_Ranked]:
    out = list(primary)
    floor = min((r.confidence for r in out), default=1.0)
    for r in extra:
        if any(abs(float(circ(r.pc_cents, o.pc_cents))) <= SAME_CLASS_CENTS for o in out):
            continue
        out.append(_Ranked(r.pc_cents, min(r.confidence * scale, floor * 0.9), r.method))
    return out


def _pad(ranked: list[_Ranked]) -> list[_Ranked]:
    """Always offer three: when the melody gives fewer, add the usual confusions (the
    tonic taken for its fourth or its fifth)."""
    out = list(ranked)
    for interval in (498.0, FIFTH_CENTS):
        if len(out) >= 3:
            break
        pc = (out[0].pc_cents + interval) % 1200.0
        if any(abs(float(circ(pc, o.pc_cents))) <= SAME_CLASS_CENTS for o in out):
            continue
        out.append(_Ranked(pc, min(0.05, out[-1].confidence * 0.5), "assumed"))
    return out


def _build(hz: float, confidence: float, method: str, candidates: Sequence[TonicCandidate]) -> Tonic:
    name, offset = nearest_note(hz)
    return Tonic(hz=float(hz), cents_ref_hz=float(hz), confidence=float(np.clip(confidence, 0.0, 1.0)),
                 method=method, candidates=tuple(candidates)[:3], tuning_offset_cents=offset, nearest_note=name)


def detect_tonic(track: PitchTrack, *, audio: Optional[np.ndarray] = None, sr: Optional[int] = None,
                 style: Optional[str] = None, tonic_hz: Optional[float] = None,
                 audio_offset_s: float = 0.0) -> tuple[Tonic, float]:
    """Returns (Tonic, drone confidence 0..1).

    ``track`` is the cleaned pitch track. ``audio`` (mono) and ``sr`` enable the drone
    method; ``audio_offset_s`` is the time of ``audio[0]`` in the track's time base.
    ``style`` picks the mode tables (None or "auto" means all formats).
    """
    metas = metas_for(style)
    prof = melody_profile(track)

    drone = DroneResult()
    if audio is not None and sr:
        try:
            drone = find_drone(audio, int(sr), track, audio_offset_s=audio_offset_s)
        except Exception:
            drone = DroneResult()  # the histogram method needs no audio
    drone_conf = float(drone.confidence)
    has_drone = drone_conf >= DRONE_MIN_CONFIDENCE and bool(drone.classes)

    ranked: list[_Ranked] = []
    if prof.voiced_s > 0:
        cands = histogram_candidates(prof, metas)
        by_histogram = _rank_histogram(cands, prof, metas)
        if has_drone:
            ranked = _merge(_rank_drone(drone, prof, metas), by_histogram, 0.5)
            # Refine to the cent from the melody's own stable frames on that class; with
            # too few of them the drone's measured pitch stands.
            for r in ranked:
                if r.method == "drone":
                    pc, measured = refine(prof, r.pc_cents)
                    r.pc_cents = pc if measured else r.pc_cents
        else:
            ranked = by_histogram
    elif has_drone:
        ranked = [_Ranked(c.pc_cents, 0.5 * drone_conf, "drone") for c in drone.classes[:1]]

    if ranked:
        ranked = _pad(ranked)[:3]
        median = prof.median_cents
        if not np.isfinite(median) and has_drone:
            median = 1200.0 * float(np.log2(drone.classes[0].hz / REF_HZ))
        found = [TonicCandidate(hz=_place(r.pc_cents, median), confidence=float(np.clip(r.confidence, 0.0, 1.0)),
                                method=r.method) for r in ranked]
    else:
        found = []

    if tonic_hz is not None and np.isfinite(tonic_hz) and tonic_hz > 0:
        user = TonicCandidate(hz=float(tonic_hz), confidence=1.0, method="user")
        others = [c for c in found if abs(float(circ(1200.0 * np.log2(c.hz / tonic_hz), 0.0))) > SAME_CLASS_CENTS]
        return _build(float(tonic_hz), 1.0, "user", [user, *others]), drone_conf
    if not found:
        return _build(ASSUMED_HZ, 0.0, "assumed", []), drone_conf
    return _build(found[0].hz, found[0].confidence, found[0].method, found), drone_conf
