"""Rhythm from note onsets (spec 7.11): pulse, free time, swing and a cycle suggestion.

``analyze`` never snaps anything. ``onset_pulses`` and ``duration_pulses`` are real
positions on the pulse grid; snapping to a target's grid happens later, in the adapters,
which log every shift.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from .. import config
from ..analysis_types import RhythmResult
from ..ir.models import Cycle, Tempo
from . import cycle as _cycle
from . import freetime, pulse, swing
from .onsets import from_segments, note_weights, onset_envelope

__all__ = ["analyze", "from_segments", "onset_envelope"]

MAX_INFERRED_TEMPO_CONFIDENCE = 0.9  # half and double tempo stay possible without a user's word


def analyze(onsets_s: Sequence[float], durations_s: Sequence[float], *, templates,
            beat_times=None, beat_confidence: Optional[float] = None,
            template_id: Optional[str] = None, tempo_bpm: Optional[float] = None) -> RhythmResult:
    """Tempo, cycle and pulse positions for a list of notes.

    ``templates`` are the candidate RhythmTemplates, the format's default first.
    ``beat_times`` / ``beat_confidence`` are an optional beat tracker result. A
    ``template_id`` or ``tempo_bpm`` chosen by the user wins, gets confidence 1 and
    forces measured time.
    """
    cfg = config.section("rhythm")
    onsets = np.asarray(list(onsets_s), dtype=float)
    n = len(onsets)
    durations = np.asarray(list(durations_s), dtype=float)
    if len(durations) != n:
        durations = np.resize(durations, n) if len(durations) else np.zeros(n)
    templates = list(templates or ())
    user_template = next((tp for tp in templates if tp.id == template_id), None) if template_id else None
    user_tempo = float(tempo_bpm) if tempo_bpm and tempo_bpm > 0 else None

    order = np.argsort(onsets, kind="stable")
    sorted_onsets, sorted_durations = onsets[order], durations[order]
    tracker_period = pulse.beat_period(beat_times, beat_confidence)
    fit = pulse.find_tatum(sorted_onsets, sorted_durations, beat_period_s=tracker_period) if n >= 3 else None
    share = fit.share if fit is not None else None

    if freetime.is_free(n, share):
        measurable = user_tempo is not None or (user_template is not None and fit is not None)
        if n == 0 or not measurable:
            return freetime.result(n, share)

    # --- the beat ---------------------------------------------------------------
    iois = np.diff(sorted_onsets)
    swung, ratio = False, 1.0
    if user_tempo is not None:
        beat_s = 60.0 / user_tempo
        tatum_s = fit.tatum_s if fit is not None else beat_s
        third = beat_s / 3.0
        real = iois[iois > 1e-6]
        if len(real) >= 3 and float(np.mean(pulse.remainders(real, third) <= float(
                cfg["free_time_fit_tolerance"]))) >= float(cfg["free_time_fit_share"]):
            swung, ratio = swing.detect(pulse.grid_positions(iois, third), iois, third)
        tempo_confidence = 1.0
    else:
        assert fit is not None
        tatum_s = fit.tatum_s
        swung, ratio = swing.detect(fit.positions, iois, tatum_s)
        beat_s = 3.0 * tatum_s if swung else pulse.beat_from_tatum(tatum_s, tracker_period)
        tempo_confidence = max(0.0, fit.share * fit.score)
        if tracker_period is not None and beat_confidence is not None:
            tempo_confidence = 0.5 * tempo_confidence + 0.5 * float(beat_confidence)
        tempo_confidence = min(MAX_INFERRED_TEMPO_CONFIDENCE, tempo_confidence)
    beat_is_given = user_tempo is not None or swung or tracker_period is not None

    def beat_of(tp) -> float:
        """A compound template (three pulses to the beat) reads the tatum as its pulse."""
        if not beat_is_given and tp.subdivision % 3 == 0:
            return 3.0 * tatum_s
        return beat_s

    def evidence(tp) -> float:
        # Without a three-way division of the beat (swing, or a beat the user or a beat
        # tracker gave), folding two-way rhythm over a compound cycle only measures the
        # clash between the two, so a compound template is listed but not scored.
        if not beat_is_given and tp.subdivision % 3 == 0:
            return 0.0
        return _cycle.score_template(sorted_onsets, weights, tp, beat_of(tp) / tp.subdivision, end_s)

    # --- the cycle --------------------------------------------------------------
    weights = note_weights(sorted_durations)
    end_s = float(np.max(sorted_onsets + sorted_durations))
    scored = [(tp, evidence(tp)) for tp in templates]
    candidates = sorted(((tp.id, round(score, 3)) for tp, score in scored), key=lambda c: -c[1])
    chosen = (user_template, 1.0) if user_template is not None else _cycle.pick(scored)

    swing_ratio = round(ratio, 3)
    if chosen is not None:
        tp, score = chosen
        beat_s = beat_of(tp)
        pulse_s = beat_s / tp.subdivision
        cyc = Cycle(
            length_pulses=int(tp.length_pulses), subdivision=int(tp.subdivision), label=tp.label,
            confidence=1.0 if user_template is not None else round(min(float(cfg["cycle_confidence_cap"]), score), 3),
            template_id=tp.id, pulse_s=pulse_s, phase_pulses=0.0, swing_ratio=swing_ratio,
        )
    else:
        subdivision = max(1, int(round(beat_s / tatum_s)))
        pulse_s = beat_s / subdivision
        cyc = Cycle(subdivision=subdivision, pulse_s=pulse_s, swing_ratio=swing_ratio)

    bpm = 60.0 / beat_s
    start = float(sorted_onsets[0])
    return RhythmResult(
        tempo=Tempo(bpm=round(bpm, 2), confidence=round(tempo_confidence, 3), free_time=False),
        cycle=cyc,
        onset_pulses=[float((o - start) / pulse_s + cyc.phase_pulses) for o in onsets],
        duration_pulses=[float(d / pulse_s) for d in durations],
        cycle_candidates=candidates,
        half_bpm=round(bpm / 2.0, 2),
        double_bpm=round(bpm * 2.0, 2),
    )
