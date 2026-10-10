"""Ornament rules on synthesised contours (spec 7.10): neutral names and measured values."""
import numpy as np
import pytest

from orin import config
from orin.analysis_types import Segment
from orin.eval import synth
from orin.segment.notes import segment_contour
from orin.segment.ornaments import annotate, classify

NEUTRAL_KINDS = {"oscillation", "vibrato", "glide", "grace"}
SHAPES = {"sine", "up", "down", "touch_above", "touch_below"}


def run(contour, profile="neutral"):
    p = config.profile(profile)
    t = synth.times(contour)
    return annotate(segment_contour(t, contour, np.ones(len(contour)), p), t, contour, p)


def dominant(contour, profile="neutral"):
    segs = run(contour, profile)
    assert len(segs) == 1
    return segs[0].ornaments[0] if segs[0].ornaments else None


# --- 3. vibrato ---------------------------------------------------------------

def test_western_vibrato():
    o = dominant(synth.oscillation(0, 25, 6, 2.0), "western")
    assert o is not None and o.kind == "vibrato"
    assert 15 <= o.amplitude_cents <= 32
    assert 5.5 <= o.rate_hz <= 6.5
    assert o.cycles >= 3 and o.coverage > 0.8 and o.position == "whole"


# --- 4. the boundaries between vibrato, oscillation and nothing ------------------

@pytest.mark.parametrize("profile", config.profile_names())
@pytest.mark.parametrize("extent, rate, want", [
    (40, 6.0, "vibrato"),
    (40, 3.5, "oscillation"),  # too slow for a vibrato
    (70, 6.0, "oscillation"),  # too wide for a vibrato
    (10, 6.0, None),  # under none_below_cents
])
def test_vibrato_oscillation_boundaries(profile, extent, rate, want):
    o = dominant(synth.oscillation(0, extent, rate, 2.0), profile)
    assert (o.kind if o else None) == want
    if o is not None:
        assert abs(o.amplitude_cents - extent) <= 8
        assert abs(o.rate_hz - rate) <= 0.5


def test_wide_oscillation_measures():
    o = dominant(synth.oscillation(0, 80, 5, 2.0), "carnatic")
    assert o.kind == "oscillation"
    assert 60 <= o.amplitude_cents <= 90 and 4.5 <= o.rate_hz <= 5.5
    assert 9 <= o.cycles <= 11
    assert 0 < o.confidence <= 1


def test_lopsided_touches_are_an_oscillation_not_a_vibrato():
    t = np.arange(200) * synth.HOP_S
    contour = np.where((t % 0.25) < 0.175 - 1e-9, 0.0, 40.0)  # narrow enough for a vibrato, but one-sided
    seg = Segment(start=0, end=200, anchor_cents=0.0, median_cents=0.0, min_cents=0.0, max_cents=40.0)
    kinds = [o.kind for o in classify(t, contour, seg, config.profile("carnatic"))]
    assert "vibrato" not in kinds


# --- 5. glides ----------------------------------------------------------------

def test_glide_up_into_a_note():
    segs = run(synth.concat(synth.const(0, 0.6), synth.ramp(0, 400, 0.3), synth.const(400, 0.6)))
    assert len(segs) == 2
    glide = segs[1].ornaments[0]
    assert glide.kind == "glide" and glide.shape == "up" and glide.position == "start"
    assert 300 <= glide.amplitude_cents <= 420
    assert segs[1].start <= glide.contour_ref[0] < glide.contour_ref[1] <= segs[1].end
    assert 0 < glide.coverage < 0.5


def test_glide_down_and_fall_at_the_end():
    down = run(synth.concat(synth.const(400, 0.6), synth.ramp(400, 0, 0.3), synth.const(0, 0.6)))
    assert down[1].ornaments[0].kind == "glide" and down[1].ornaments[0].shape == "down"
    fall = run(synth.concat(synth.const(0, 0.6), synth.ramp(0, -150, 0.15)))
    assert len(fall) == 1
    end = [o for o in fall[0].ornaments if o.kind == "glide"]
    assert end and end[0].position == "end" and end[0].shape == "down"
    assert 100 <= end[0].amplitude_cents <= 160


def test_a_step_is_not_a_glide():
    segs = run(synth.concat(synth.const(0, 0.5), synth.const(400, 0.5)))
    assert len(segs) == 2
    assert not any(o.kind == "glide" for s in segs for o in s.ornaments)


def test_an_oscillation_is_not_reported_as_glides_or_graces():
    for phase in (0.0, np.pi / 2, np.pi):
        segs = run(synth.oscillation(0, 120, 4, 2.0, phase=phase), "carnatic")
        assert len(segs) == 1
        assert [o.kind for o in segs[0].ornaments] == ["oscillation"]


# --- 8. grace -----------------------------------------------------------------

@pytest.mark.parametrize("profile", ["carnatic", "western", "neutral"])
def test_grace_touch_at_the_start(profile):
    segs = run(synth.concat(synth.const(200, 0.06), synth.const(0, 0.5)), profile)
    assert len(segs) == 1
    graces = [o for o in segs[0].ornaments if o.kind == "grace"]
    assert len(graces) == 1
    assert 150 <= graces[0].amplitude_cents <= 220
    assert graces[0].shape == "touch_above" and graces[0].position == "start"
    assert not any(o.kind == "glide" for o in segs[0].ornaments)


def test_grace_touch_below_in_the_middle():
    segs = run(synth.concat(synth.const(0, 0.4), synth.const(-100, 0.05), synth.const(0, 0.4)), "carnatic")
    assert len(segs) == 1
    assert segs[0].ornaments[0].kind == "grace" and segs[0].ornaments[0].shape == "touch_below"


def test_the_way_in_from_the_previous_note_is_not_a_grace():
    """A tracker takes a few frames to move between two notes. Those frames open the
    second note at the first note's pitch; that is a transition, not a touch."""
    slow_step = synth.concat(synth.const(0, 0.4), synth.ramp(0, 200, 0.04), synth.const(200, 0.4))
    for profile in config.profile_names():
        segs = run(slow_step, profile)
        assert len(segs) == 2
        assert not any(o.kind == "grace" for s in segs for o in s.ornaments), profile
    touch_between = synth.concat(synth.const(0, 0.4), synth.const(300, 0.05), synth.const(200, 0.4))
    segs = run(touch_between, "carnatic")
    assert len(segs) == 2
    assert [o.kind for o in segs[1].ornaments] == ["grace"] and segs[1].ornaments[0].shape == "touch_above"


def test_long_first_pitch_is_its_own_note_with_no_grace():
    segs = run(synth.concat(synth.const(200, 0.18), synth.const(0, 0.5)), "carnatic")
    assert len(segs) == 2
    assert not any(o.kind == "grace" for s in segs for o in s.ornaments)


# --- contract -----------------------------------------------------------------

def test_plain_note_has_no_ornament():
    assert dominant(synth.const(0, 1.0)) is None


def test_names_are_neutral_and_fields_are_filled():
    contours = [
        synth.oscillation(0, 80, 5, 2.0),
        synth.oscillation(0, 25, 6, 2.0),
        synth.concat(synth.const(0, 0.6), synth.ramp(0, 400, 0.3), synth.const(400, 0.6)),
        synth.concat(synth.const(200, 0.06), synth.const(0, 0.5)),
    ]
    seen = set()
    for contour in contours:
        for profile in config.profile_names():
            for seg in run(contour, profile):
                for o in seg.ornaments:
                    seen.add(o.kind)
                    assert o.kind in NEUTRAL_KINDS
                    assert o.shape in SHAPES
                    assert o.amplitude_cents > 0
                    assert 0 < o.coverage <= 1 and 0 < o.confidence <= 1
                    assert seg.start <= o.contour_ref[0] < o.contour_ref[1] <= seg.end
                coverages = [o.coverage for o in seg.ornaments]
                assert coverages == sorted(coverages, reverse=True)  # dominant first
    assert seen == NEUTRAL_KINDS


def test_annotate_returns_copies():
    contour = synth.oscillation(0, 80, 5, 1.0)
    p = config.profile("carnatic")
    t = synth.times(contour)
    plain = segment_contour(t, contour, np.ones(len(contour)), p)
    noted = annotate(plain, t, contour, p)
    assert plain[0].ornaments == [] and noted[0].ornaments
    assert (noted[0].start, noted[0].end, noted[0].anchor_cents) == (plain[0].start, plain[0].end, plain[0].anchor_cents)
