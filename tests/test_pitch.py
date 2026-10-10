"""Pitch tracking, the ensemble rule, octave repair and voicing cleanup (spec 7.5, 7.6)."""
import numpy as np
import pytest

from orin.analysis_types import PitchTrack
from orin.eval import synth
from orin.pitch.ensemble import ensemble, track_pitch
from orin.pitch.voicing import bridge_gaps, clean_voicing, median_smooth, repair_octaves

SR = 16000


def cents(f0, ref_hz):
    return 1200.0 * np.log2(np.asarray(f0, dtype=float) / ref_hz)


def flat_track(hz=220.0, n=200, conf=0.9, tracker="pyin"):
    return PitchTrack(t=np.arange(n) * 0.01, f0=np.full(n, hz), conf=np.full(n, conf), tracker=tracker)


def wobble():
    """0.5 s held, 2 s of a 5 Hz +/-80 cent oscillation, a 0.3 s rest, 0.5 s a fifth up."""
    contour = synth.concat(synth.const(0, 0.5), synth.oscillation(0, 80, 5, 2.0), synth.silence(0.3),
                           synth.const(700, 0.5))
    return synth.render(contour, 220.0, sr=SR)


# --- trackers -----------------------------------------------------------------------

def test_pyin_tracks_220_hz_within_10_cents_on_the_10_ms_grid():
    track = track_pitch(synth.tone(220.0, 1.0, sr=SR), trackers="pyin")
    assert track.tracker == "pyin" and track.hop_s == 0.01
    assert len(track.t) == 101 and np.allclose(np.diff(track.t), 0.01)
    middle = slice(15, 85)
    assert np.all(np.abs(cents(track.f0[middle], 220.0)) < 10.0)
    assert track.conf[middle].min() >= 0.5


@pytest.mark.slow
def test_crepe_tracks_220_hz_within_10_cents_and_aligns_with_pyin():
    pytest.importorskip("torchcrepe")
    y = synth.tone(220.0, 1.0, sr=SR)
    track = track_pitch(y, trackers="crepe")
    assert track.tracker == "crepe"
    assert np.all(np.abs(cents(track.f0[15:85], 220.0)) < 10.0)
    assert track.conf[15:85].min() > 0.6
    assert len(track.t) == len(track_pitch(y, trackers="pyin").t)  # same frames, so they can be compared


@pytest.mark.slow
def test_both_trackers_run_as_an_ensemble():
    pytest.importorskip("torchcrepe")
    track = track_pitch(synth.tone(220.0, 1.0, sr=SR), trackers="both")
    assert track.tracker == "ensemble" and track.disagreements == 0
    assert np.all(np.abs(cents(track.f0[15:85], 220.0)) < 10.0)


def test_pyin_takes_over_when_crepe_cannot_be_imported(monkeypatch):
    import orin.pitch.crepe as crepe

    def missing(*args, **kwargs):
        raise ImportError("No module named 'torchcrepe'")

    monkeypatch.setattr(crepe, "track_crepe", missing)
    failures: list[str] = []
    track = track_pitch(synth.tone(220.0, 0.6, sr=SR), trackers="auto", failures=failures)
    assert track.tracker == "pyin" and failures and "crepe" in failures[0]
    assert abs(np.nanmedian(cents(track.f0[10:50], 220.0))) < 10.0


def test_oscillation_keeps_85_percent_of_its_extent():
    raw = track_pitch(wobble(), trackers="pyin")
    for track in (raw, clean_voicing(raw, 0.5)):
        c = cents(track.f0, 220.0)[60:240]  # inside the oscillation, away from its ends
        half_extent = (np.nanmax(c) - np.nanmin(c)) / 2.0
        assert half_extent >= 0.85 * 80.0
        assert half_extent <= 90.0  # and nothing is exaggerated either


@pytest.mark.slow
def test_crepe_oscillation_keeps_85_percent_of_its_extent():
    pytest.importorskip("torchcrepe")
    track = clean_voicing(track_pitch(wobble(), trackers="crepe"), 0.5)
    c = cents(track.f0, 220.0)[60:240]
    assert (np.nanmax(c) - np.nanmin(c)) / 2.0 >= 0.85 * 80.0


def test_unvoiced_gaps_stay_unvoiced():
    track = clean_voicing(track_pitch(wobble(), trackers="pyin"), 0.5)
    voiced = np.isfinite(track.f0)
    assert not voiced[256:274].any()  # the 0.3 s rest at 2.5 s (tracker windows blur its edges a little)
    assert voiced[20:240].all() and voiced[285:320].all()
    silence = clean_voicing(track_pitch(np.zeros(SR, dtype=np.float32), trackers="pyin"), 0.5)
    assert not np.isfinite(silence.f0).any()
    noise = clean_voicing(track_pitch(synth.noise(1.5, sr=SR, amp=0.3), trackers="pyin"), 0.5)
    assert np.isfinite(noise.f0).mean() < 0.05


def test_voice_presets_set_the_search_range():
    low = synth.tone(98.0, 0.8, sr=SR)
    assert abs(np.nanmedian(cents(track_pitch(low, voice="male", trackers="pyin").f0[15:65], 98.0))) < 10.0
    female = clean_voicing(track_pitch(low, voice="female", trackers="pyin"), 0.5)  # 98 Hz is under its 130 Hz floor
    found = female.f0[np.isfinite(female.f0)]
    assert found.size == 0 or found.min() >= 129.0


# --- octave repair ---------------------------------------------------------------------

def test_short_octave_spikes_are_folded_back():
    track = flat_track(220.0, 200)
    track.f0[50:56] = 440.0  # 60 ms an octave up
    track.f0[120:126] = 110.0  # 60 ms an octave down
    fixed = repair_octaves(track)
    assert fixed.octave_fixes == 12
    assert np.allclose(fixed.f0, 220.0)
    assert track.f0[50] == 440.0  # the input is not modified

    cleaned = clean_voicing(track, 0.5)
    assert cleaned.octave_fixes == 12 and np.allclose(cleaned.f0, 220.0)


def test_octave_repair_on_arrays_in_cents_and_hz():
    c = np.zeros(200)
    c[50:56] = 1200.0
    c[120:126] = -1190.0  # inside the +/-60 cent window around an octave
    c[150:153] = np.nan
    repaired, n = repair_octaves(c)
    assert n == 12 and np.nanmax(np.abs(repaired)) <= 10.0 and np.isnan(repaired[150:153]).all()

    hz = np.full(200, 220.0)
    hz[80:90] = 440.0
    repaired_hz, n_hz = repair_octaves(hz, unit="hz")
    assert n_hz == 10 and np.allclose(repaired_hz, 220.0)


def test_real_octave_leaps_and_other_intervals_are_left_alone():
    c = np.zeros(300)
    c[100:140] = 1200.0  # 400 ms: a note an octave up, not a slip
    c[200:206] = 700.0  # a short fifth: not an octave error
    repaired, n = repair_octaves(c)
    assert n == 0 and np.array_equal(repaired, c)


# --- voicing cleanup ---------------------------------------------------------------------

def test_frames_under_the_threshold_are_dropped():
    track = flat_track(220.0, 100)
    track.conf[40:60] = 0.3
    cleaned = clean_voicing(track, 0.5)
    assert np.isnan(cleaned.f0[40:60]).all() and np.isfinite(cleaned.f0[:40]).all()
    assert np.isfinite(clean_voicing(track, 0.25).f0).all()  # the threshold is the caller's


def test_short_gaps_are_bridged_only_inside_a_note():
    track = flat_track(220.0, 300)
    track.conf[50:54] = 0.1  # 40 ms dropout inside a held note
    track.conf[150:158] = 0.1  # 80 ms: a real break
    track.f0[220:] = 220.0 * 2 ** (300 / 1200)
    track.conf[216:220] = 0.1  # 40 ms gap between two different notes
    cleaned = clean_voicing(track, 0.5)
    assert np.isfinite(cleaned.f0[50:54]).all() and np.allclose(cleaned.f0[50:54], 220.0)
    assert cleaned.conf[50:54].min() >= 0.5  # bridged frames count as voiced for later stages
    assert np.isnan(cleaned.f0[150:158]).all()
    assert np.isnan(cleaned.f0[216:220]).all()


def test_bridge_interpolates_between_close_pitches():
    c = np.concatenate([np.full(20, 0.0), np.full(4, np.nan), np.full(20, 50.0)])
    bridged, mask = bridge_gaps(c)
    assert mask[20:24].all() and mask.sum() == 4
    assert np.all(np.diff(bridged[19:25]) > 0)  # a straight line from 0 to 50


def test_median_filter_removes_spikes_and_keeps_glides_and_oscillations():
    glide = np.linspace(0.0, 400.0, 40)
    np.testing.assert_allclose(median_smooth(glide), glide)
    wave = synth.oscillation(0, 80, 5, 1.0)
    np.testing.assert_allclose(median_smooth(wave), wave)
    spiky = np.zeros(50)
    spiky[25] = 90.0
    assert np.abs(median_smooth(spiky)).max() == 0.0
    edge = np.concatenate([np.full(5, np.nan), np.zeros(10)])
    np.testing.assert_array_equal(median_smooth(edge), edge)  # note edges are untouched

    track = flat_track(220.0, 100)
    track.f0[30] = 220.0 * 2 ** (150 / 1200)
    assert np.allclose(clean_voicing(track, 0.5).f0, 220.0)


def test_voiced_blips_shorter_than_30_ms_are_dropped():
    track = flat_track(220.0, 100, conf=0.1)
    track.conf[40:42] = 0.9
    track.conf[60:80] = 0.9
    cleaned = clean_voicing(track, 0.5)
    assert np.isnan(cleaned.f0[40:42]).all() and np.isfinite(cleaned.f0[60:80]).all()


# --- ensemble ------------------------------------------------------------------------------

def test_ensemble_prefers_the_confident_tracker_and_counts_disagreements():
    steady = flat_track(220.0, 200, conf=0.8, tracker="pyin")
    slipping = flat_track(220.0, 200, conf=0.9, tracker="crepe")
    slipping.f0[50:60] = 440.0  # an octave error it is not sure about
    slipping.conf[50:60] = 0.35
    for order in ((slipping, steady), (steady, slipping)):
        out = ensemble(order)
        assert out.tracker == "ensemble" and out.disagreements == 10
        assert np.allclose(out.f0, 220.0)
        assert np.allclose(out.conf[50:60], 0.8)  # the winner's confidence
        assert np.allclose(out.conf[:50], 0.9)  # where they agree, the higher of the two


def test_ensemble_prefers_the_continuous_tracker_at_equal_confidence():
    steady = flat_track(220.0, 200, conf=0.8)
    jumping = flat_track(220.0, 200, conf=0.8)
    jumping.f0[100:103] = 440.0  # same confidence, but it leaves its own line
    out = ensemble([jumping, steady])
    assert np.allclose(out.f0, 220.0) and out.disagreements == 3


def test_ensemble_keeps_the_primary_where_they_agree_and_fills_its_gaps():
    primary = flat_track(220.0, 100, conf=0.9)
    other = flat_track(221.0, 100, conf=0.7)  # 8 cents apart: agreement
    primary.f0[20:30] = np.nan
    primary.conf[20:30] = 0.0
    out = ensemble([primary, other])
    assert out.disagreements == 0
    assert np.allclose(out.f0[:20], 220.0) and np.allclose(out.f0[20:30], 221.0)
    assert ensemble([primary]) is primary
    with pytest.raises(ValueError):
        ensemble([])
