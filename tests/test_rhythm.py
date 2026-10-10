"""Pulse, free time, swing and cycle suggestion from note onsets (spec 7.11)."""
import numpy as np
import pytest

from orin import config, rhythm
from orin.formats import registry

CAP = config.section("rhythm")["cycle_confidence_cap"]
FORMATS = ["western_classical", "western_pop", "rock", "carnatic", "arabic_maqam", "persian_dastgah"]
IRREGULAR = [0.41, 0.97, 0.23, 1.31, 0.58, 0.19, 0.83, 0.37, 1.12, 0.29, 0.71, 0.47, 1.03]


def templates(format_id):
    return registry.get(format_id).META.rhythm_templates


def from_intervals(intervals, fill=0.9):
    intervals = np.asarray(intervals, dtype=float)
    onsets = np.concatenate(([0.0], np.cumsum(intervals)))
    durations = np.append(intervals, intervals[-1]) * fill
    return onsets, durations


def straight():
    onsets = np.arange(0.0, 8.0, 0.25)
    return onsets, np.full(len(onsets), 0.23)


def swung():
    return from_intervals(np.tile([0.333, 0.167], 16)[:-1])


def control():
    rng = np.random.default_rng(7)
    onsets = np.arange(16) * 0.5 + rng.uniform(-0.015, 0.015, 16)
    return onsets, np.full(16, 0.45)


def near_120(result):
    return any(b is not None and abs(b - 120.0) <= 3.0 for b in (result.tempo.bpm, result.half_bpm, result.double_bpm))


@pytest.mark.parametrize("format_id", FORMATS)
def test_straight_eighths(format_id):
    r = rhythm.analyze(*straight(), templates=templates(format_id))
    assert not r.tempo.free_time
    assert near_120(r)
    assert r.half_bpm == pytest.approx(r.tempo.bpm / 2) and r.double_bpm == pytest.approx(r.tempo.bpm * 2)
    assert r.cycle.swing_ratio == pytest.approx(1.0, abs=0.1)
    assert r.cycle.confidence <= CAP
    assert 0 < r.tempo.confidence < 1


@pytest.mark.parametrize("format_id", FORMATS)
def test_swing(format_id):
    r = rhythm.analyze(*swung(), templates=templates(format_id))
    assert not r.tempo.free_time
    assert r.cycle.swing_ratio == pytest.approx(2.0, abs=0.25)
    assert near_120(r)
    assert r.cycle.confidence <= CAP


@pytest.mark.parametrize("format_id", FORMATS)
def test_irregular_onsets_are_free_time(format_id):
    onsets, durations = from_intervals(IRREGULAR)
    assert len(onsets) == 14
    r = rhythm.analyze(onsets, durations, templates=templates(format_id))
    assert r.tempo.free_time is True
    assert r.tempo.bpm is None and r.half_bpm is None and r.double_bpm is None
    assert r.cycle.length_pulses is None and r.cycle.template_id is None
    assert len(r.onset_pulses) == 14 and all(p is None for p in r.onset_pulses)
    assert all(p is None for p in r.duration_pulses)


@pytest.mark.parametrize("format_id", FORMATS)
def test_slightly_loose_playing_is_not_free_time(format_id):
    r = rhythm.analyze(*control(), templates=templates(format_id))
    assert not r.tempo.free_time
    assert near_120(r)
    assert r.cycle.swing_ratio == pytest.approx(1.0, abs=0.1)
    assert r.cycle.confidence <= CAP


def test_too_few_onsets_are_free_time():
    r = rhythm.analyze([0.0, 0.5, 1.0, 1.5], [0.4] * 4, templates=templates("western_pop"))
    assert r.tempo.free_time and r.onset_pulses == [None] * 4
    empty = rhythm.analyze([], [], templates=templates("western_pop"))
    assert empty.tempo.free_time and empty.onset_pulses == []


def test_pulses_are_real_positions_not_snapped():
    onsets, durations = control()
    r = rhythm.analyze(onsets, durations, templates=templates("western_pop"))
    pulse_s = r.cycle.pulse_s
    assert pulse_s and r.cycle.subdivision >= 1
    assert r.tempo.bpm == pytest.approx(60.0 / (pulse_s * r.cycle.subdivision), rel=1e-3)
    assert r.onset_pulses[0] == pytest.approx(r.cycle.phase_pulses) == 0.0
    for onset, pulse, dur, dur_pulses in zip(onsets, r.onset_pulses, durations, r.duration_pulses):
        assert pulse == pytest.approx((onset - onsets[0]) / pulse_s)
        assert dur_pulses == pytest.approx(dur / pulse_s)
    assert any(abs(p - round(p)) > 1e-3 for p in r.onset_pulses)  # the jitter is kept


def test_a_rhythm_with_a_shape_suggests_a_cycle_under_the_cap():
    onsets, durations = from_intervals(np.tile([0.5, 0.25, 0.25, 0.5, 0.5], 6))
    r = rhythm.analyze(onsets, durations, templates=templates("western_pop"))
    assert r.cycle.template_id == "4_4" and r.cycle.length_pulses == 16
    assert 0 < r.cycle.confidence <= CAP
    assert {tid for tid, _ in r.cycle_candidates} == {tp.id for tp in templates("western_pop")}
    scores = [score for _, score in r.cycle_candidates]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("fixture", [straight, swung, control])
@pytest.mark.parametrize("format_id", FORMATS)
def test_inferred_cycle_confidence_never_exceeds_the_cap(fixture, format_id):
    r = rhythm.analyze(*fixture(), templates=templates(format_id))
    assert r.cycle.confidence <= CAP
    assert len(r.cycle_candidates) == len(templates(format_id))


def test_user_template_wins():
    r = rhythm.analyze(*straight(), templates=templates("carnatic"), template_id="rupakam_3")
    assert r.cycle.template_id == "rupakam_3"
    assert r.cycle.length_pulses == 12 and r.cycle.subdivision == 4
    assert r.cycle.confidence == 1.0
    assert not r.tempo.free_time


def test_user_tempo_wins():
    r = rhythm.analyze(*straight(), templates=templates("carnatic"), tempo_bpm=60)
    assert r.tempo.bpm == pytest.approx(60.0) and r.tempo.confidence == 1.0
    assert r.half_bpm == pytest.approx(30.0) and r.double_bpm == pytest.approx(120.0)
    free = rhythm.analyze(*from_intervals(IRREGULAR), templates=templates("carnatic"), tempo_bpm=90)
    assert not free.tempo.free_time and free.tempo.bpm == pytest.approx(90.0)
    assert all(p is not None for p in free.onset_pulses)


def test_confident_beat_tracker_picks_the_beat():
    onsets, durations = straight()
    slow = rhythm.analyze(onsets, durations, templates=templates("western_pop"),
                          beat_times=np.arange(0.0, 8.0, 1.0), beat_confidence=0.9)
    assert slow.tempo.bpm == pytest.approx(60.0, abs=1.0)
    ignored = rhythm.analyze(onsets, durations, templates=templates("western_pop"),
                             beat_times=np.arange(0.0, 8.0, 1.0), beat_confidence=0.1)
    assert ignored.tempo.bpm == pytest.approx(120.0, abs=1.0)


def test_onset_envelope_marks_attacks_and_nothing_else():
    from orin.eval import synth

    struck = synth.phrase([0, 0, 0], 0.4, 0.1)  # the same pitch struck three times
    env, env_t = rhythm.onset_envelope(synth.render(struck, 220.0), synth.SR)
    assert len(env) == len(env_t) > 0 and 0.0 <= env.min() and env.max() <= 1.0
    for start in (0.0, 0.5, 1.0):
        assert env[np.abs(env_t - start) < 0.06].max() > 0.5
        assert env[(env_t > start + 0.15) & (env_t < start + 0.35)].max() < 0.05
    # a held tone, a vibrato, a glide and a legato step have no attack after the first
    for contour in (synth.const(0, 1.5), synth.oscillation(0, 80, 5, 1.5),
                    synth.concat(synth.const(0, 0.5), synth.ramp(0, 400, 0.3), synth.const(400, 0.5)),
                    synth.phrase([0, 200, 400], 0.5, 0.0)):
        env, env_t = rhythm.onset_envelope(synth.render(contour, 220.0), synth.SR)
        assert env[env_t < 0.05].max() > 0.5
        assert env[env_t > 0.15].max() < 0.15
