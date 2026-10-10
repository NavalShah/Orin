"""Note segmentation on synthesised contours (spec 7.9). No audio is needed."""
import numpy as np
import pytest

from orin import config
from orin.eval import synth
from orin.segment.notes import segment_contour
from orin.segment.ornaments import annotate

PROFILES = config.profile_names()


def run(contour, profile="carnatic", sensitivity=1.0, **kw):
    p = config.profile(profile, sensitivity)
    t = synth.times(contour)
    segments = segment_contour(t, contour, np.ones(len(contour)), p, **kw)
    return annotate(segments, t, contour, p)


def assert_ordered(segments):
    for seg in segments:
        assert seg.start < seg.end
    for a, b in zip(segments[:-1], segments[1:]):
        assert a.end <= b.start


def touch_pattern(total_s=2.0, cycle_s=0.25, rest_s=0.175, excursion=200.0):
    t = np.arange(int(round(total_s / synth.HOP_S))) * synth.HOP_S
    return np.where((t % cycle_s) < rest_s - 1e-9, 0.0, excursion)


# --- 1. a wide gamaka is one note --------------------------------------------

def test_wide_oscillation_is_one_note():
    segs = run(synth.oscillation(0, 80, 5, 2.0), "carnatic")
    assert len(segs) == 1
    seg = segs[0]
    assert abs(seg.anchor_cents) <= 15
    assert seg.max_cents - seg.min_cents >= 120
    assert seg.ornaments and seg.ornaments[0].kind == "oscillation"
    assert 4.5 <= seg.ornaments[0].rate_hz <= 5.5
    assert 60 <= seg.ornaments[0].amplitude_cents <= 90


@pytest.mark.parametrize("extent", [60, 100, 150, 200])
@pytest.mark.parametrize("rate", [3, 5, 7])
def test_oscillation_sweep_is_one_note(extent, rate):
    segs = run(synth.oscillation(0, extent, rate, 2.0), "carnatic")
    assert len(segs) == 1
    assert abs(segs[0].anchor_cents) <= 15


def test_oscillation_on_part_of_a_note_is_still_one_note():
    contour = synth.concat(synth.const(0, 0.8), synth.oscillation(0, 80, 5, 1.2))
    segs = run(contour, "carnatic")
    assert len(segs) == 1
    assert abs(segs[0].anchor_cents) <= 15


def test_two_oscillating_notes_stay_two():
    contour = synth.concat(synth.oscillation(0, 70, 5, 1.0), synth.oscillation(400, 70, 5, 1.0))
    segs = run(contour, "carnatic")
    assert len(segs) == 2
    assert abs(segs[0].anchor_cents) <= 25 and abs(segs[1].anchor_cents - 400) <= 25


# --- 2. repeated touches of a neighbour --------------------------------------

def test_repeated_touches_are_one_note_anchored_on_the_rest_pitch():
    segs = run(touch_pattern(), "carnatic")
    assert len(segs) == 1
    assert abs(segs[0].anchor_cents) <= 25  # the median, not the mean (which would be 56)
    assert segs[0].max_cents >= 170


def test_anchor_is_a_median_not_a_mean():
    contour = synth.concat(synth.const(0, 0.3), synth.const(200, 0.12), synth.const(0, 0.3))
    segs = run(contour, "carnatic")
    assert len(segs) == 1
    assert abs(segs[0].anchor_cents) <= 5
    assert np.nanmean(contour) > 30


# --- 5. a glide belongs to the note it arrives on -----------------------------

@pytest.mark.parametrize("profile", PROFILES)
def test_glide_between_two_plateaus(profile):
    contour = synth.concat(synth.const(0, 0.6), synth.ramp(0, 400, 0.3), synth.const(400, 0.6))
    segs = run(contour, profile)
    assert len(segs) == 2
    assert abs(segs[0].anchor_cents) <= 15 and abs(segs[1].anchor_cents - 400) <= 15
    assert not any(60 < s.anchor_cents < 340 for s in segs)
    glides = [o for o in segs[1].ornaments if o.kind == "glide"]
    assert glides and glides[0].shape == "up" and glides[0].position == "start"
    assert 300 <= glides[0].amplitude_cents <= 420
    assert not any(o.kind == "glide" for o in segs[0].ornaments)
    assert_ordered(segs)


# --- 6 and 7. plain phrases ----------------------------------------------------

STEPS = [0, 200, 400, 500, 700, 500, 400, 200, 0]


@pytest.mark.parametrize("profile", PROFILES)
def test_stepwise_phrase(profile):
    segs = run(synth.phrase(STEPS, 0.30, 0.02), profile)
    assert len(segs) == 9
    for seg, want in zip(segs, STEPS):
        assert abs(seg.anchor_cents - want) <= 10
    assert_ordered(segs)


@pytest.mark.parametrize("profile", PROFILES)
def test_stepwise_phrase_with_bridged_gaps(profile):
    """The pitch stage bridges short gaps, so the same phrase also arrives with no gaps."""
    segs = run(synth.phrase(STEPS, 0.30, 0.0), profile)
    assert [round(s.anchor_cents) for s in segs] == STEPS
    assert_ordered(segs)


@pytest.mark.parametrize("profile", PROFILES)
def test_repeated_notes_split_at_silences(profile):
    segs = run(synth.phrase([0, 0, 0, 0], 0.30, 0.03), profile)
    assert len(segs) == 4
    assert all(abs(s.anchor_cents) <= 10 for s in segs)


# --- 8. a short touch is not a note -------------------------------------------

@pytest.mark.parametrize("profile", ["carnatic", "western", "neutral"])
def test_short_touch_merges_and_long_one_does_not(profile):
    short = run(synth.concat(synth.const(200, 0.06), synth.const(0, 0.5)), profile)
    assert len(short) == 1
    assert abs(short[0].anchor_cents) <= 10
    long = run(synth.concat(synth.const(200, 0.18), synth.const(0, 0.5)), profile)
    assert len(long) == 2
    assert abs(long[0].anchor_cents - 200) <= 10 and abs(long[1].anchor_cents) <= 10


# --- 9. other profiles may differ, but must behave ------------------------------

@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("rate", [3, 5, 7])
def test_wide_oscillation_under_any_profile_is_well_formed(profile, rate):
    contour = synth.oscillation(0, 150, rate, 2.0)
    segs = run(contour, profile)
    assert len(segs) >= 1
    assert_ordered(segs)
    assert all(0 <= s.start and s.end <= len(contour) for s in segs)
    assert all(0.0 <= s.confidence <= 1.0 for s in segs)


# --- hysteresis, out-and-back, onsets, edges -----------------------------------

@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("extent, rate", [(40, 3.5), (30, 2.0), (45, 4.0), (20, 1.0)])
def test_a_slow_wobble_within_the_plateau_is_one_note(profile, extent, rate):
    """Slower than the profile's oscillation rates, but too narrow to be two pitches."""
    p = config.profile(profile)
    slow = rate < p.osc_min_hz / 1.15
    if slow and 1.5 * extent > p.plateau_enter_cents:
        pytest.skip("its turning points are two pitches under this profile")
    segs = run(synth.oscillation(0, extent, rate, 2.0), profile)
    assert len(segs) == 1
    assert abs(segs[0].anchor_cents) <= extent


def test_neighbouring_notes_are_more_than_the_enter_threshold_apart():
    rng = np.random.default_rng(3)
    for profile in PROFILES:
        p = config.profile(profile)
        for extent in (30, 45, 60, 90):
            contour = synth.oscillation(0, extent, 3.3, 3.0) + rng.normal(0, 3, 300)
            segs = run(contour, profile)
            assert_ordered(segs)
            for a, b in zip(segs[:-1], segs[1:]):
                if a.end == b.start and not b.onset_split:
                    assert abs(a.anchor_cents - b.anchor_cents) > 0.5 * p.plateau_enter_cents


def test_small_step_stays_one_note_and_large_step_splits():
    assert len(run(synth.concat(synth.const(0, 0.5), synth.const(50, 0.5)), "western")) == 1
    assert len(run(synth.concat(synth.const(0, 0.5), synth.const(100, 0.5)), "western")) == 2


def test_out_and_back_depends_on_the_profile():
    contour = synth.concat(synth.const(0, 0.5), synth.const(200, 0.12), synth.const(0, 0.5))
    touched = run(contour, "carnatic")
    assert len(touched) == 1  # within out_and_back_ms: an ornament of the note
    assert abs(touched[0].anchor_cents) <= 5 and touched[0].max_cents >= 190
    assert len(run(contour, "western")) == 3  # too long for the Western profile: three notes


def test_a_short_note_between_two_alike_is_not_swallowed():
    """An excursion that rests on its own pitch is a note, even when it returns in time."""
    semitones = [0, 100, 200, 300, 200, 100, 0]
    for profile in PROFILES:
        segs = run(synth.phrase(semitones, 0.25, 0.0), profile)
        assert [round(s.anchor_cents) for s in segs] == semitones, profile


def test_strong_onsets_split_a_steady_note_but_not_an_oscillation():
    n = 120
    t = np.arange(n) * synth.HOP_S
    env = np.zeros(n)
    for frame in (0, 40, 80):
        env[frame] = 1.0
        env[frame + 1] = 0.5
    steady = run(synth.const(0, 1.2), "western", onset_env=env, onset_env_t=t)
    assert [s.start for s in steady] == [0, 40, 80]
    assert [s.onset_split for s in steady] == [False, True, True]
    assert len(run(synth.const(0, 1.2), "western")) == 1
    shaken = run(synth.oscillation(0, 80, 5, 1.2), "carnatic", onset_env=env, onset_env_t=t)
    assert len(shaken) == 1


def test_sensitivity_changes_the_thresholds_not_the_contract():
    contour = synth.concat(synth.const(0, 0.5), synth.const(50, 0.5))
    assert len(run(contour, "western", sensitivity=1.0)) == 1
    assert len(run(contour, "western", sensitivity=2.0)) == 2


def test_empty_and_silent_input():
    p = config.profile("neutral")
    assert segment_contour(np.zeros(0), np.zeros(0), np.zeros(0), p) == []
    silent = synth.silence(1.0)
    assert segment_contour(synth.times(silent), silent, np.zeros(len(silent)), p) == []


def test_confidence_follows_the_tracker():
    contour = synth.const(0, 0.5)
    p = config.profile("neutral")
    seg = segment_contour(synth.times(contour), contour, np.full(len(contour), 0.4), p)[0]
    assert seg.confidence == pytest.approx(0.4)
    assert seg.voiced_fraction == 1.0


def test_a_note_struck_again_as_it_starts_to_oscillate_is_split_there():
    contour = synth.concat(synth.const(0, 0.8), synth.oscillation(0, 80, 5, 0.8))
    t = synth.times(contour)
    env = np.zeros(len(contour))
    env[0], env[80] = 1.0, 0.8
    segs = run(contour, "carnatic", onset_env=env, onset_env_t=t)
    assert [(s.start, s.onset_split) for s in segs] == [(0, False), (80, True)]
    assert segs[1].ornaments[0].kind == "oscillation" and not segs[0].ornaments
    assert len(run(contour, "carnatic")) == 1


def test_a_weak_or_rippling_onset_envelope_splits_nothing():
    contour = synth.const(0, 2.0)
    t = synth.times(contour)
    ripple = np.where(np.arange(len(contour)) % 10 == 0, 0.05, 0.0)  # a held tone's own flux
    assert len(run(contour, "western", onset_env=ripple, onset_env_t=t)) == 1
    attack = ripple.copy()
    attack[0] = 1.0
    assert len(run(contour, "western", onset_env=attack, onset_env_t=t)) == 1


@pytest.mark.parametrize("profile", PROFILES)
def test_tracker_jitter_does_not_change_the_notes(profile):
    rng = np.random.default_rng(11)
    cases = [
        (synth.phrase(STEPS, 0.30, 0.0), len(STEPS)),
        (synth.concat(synth.const(0, 0.6), synth.ramp(0, 400, 0.3), synth.const(400, 0.6)), 2),
        (synth.oscillation(0, 80, 5, 2.0), 1),
        (synth.concat(synth.const(200, 0.06), synth.const(0, 0.5)), 1),
        (synth.const(0, 2.0), 1),
    ]
    for contour, want in cases:
        for _ in range(5):
            assert len(run(contour + rng.normal(0.0, 5.0, len(contour)), profile)) == want
