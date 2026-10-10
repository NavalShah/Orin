"""Tonic detection (spec 7.7): histogram, drone, refinement, octave, candidates."""
import numpy as np
import pytest
from scipy.signal import resample_poly

from orin.analysis_types import PitchTrack
from orin.config import section
from orin.eval import synth
from orin.pitch.ensemble import track_pitch
from orin.pitch.voicing import clean_voicing
from orin.tonic.drone import drone_only_frames, find_drone
from orin.tonic.histogram import melody_profile, refine, stable_runs
from orin.tonic.select import detect_tonic, nearest_note

SR = 22050
SA = 146.83
MOHANAM = [0, 204, 386, 702, 884, 702, 386, 204, 0, 204, 386, 204, 0]


def track_of(y):
    y16 = resample_poly(y, 320, 441).astype(np.float32)
    return clean_voicing(track_pitch(y16, trackers="pyin"), 0.45)


def folded_error(hz, ref_hz):
    return abs((1200.0 * np.log2(hz / ref_hz) + 600.0) % 1200.0 - 600.0)


def melody(cents, tonic_hz, last_s=1.0, note_s=0.4):
    return synth.render(synth.phrase(cents, [note_s] * (len(cents) - 1) + [last_s]), tonic_hz)


def contour_track(cents, tonic_hz):
    """A PitchTrack straight from a contour: no audio, no tracker."""
    cents = np.asarray(cents, dtype=float)
    return PitchTrack(t=synth.times(cents), f0=tonic_hz * 2.0 ** (cents / 1200.0),
                      conf=np.where(np.isnan(cents), 0.0, 0.9))


@pytest.fixture(scope="module")
def mohanam():
    y = melody(MOHANAM, SA)
    return y, track_of(y)


# --- histogram method ----------------------------------------------------------------

def test_phrase_ending_on_a_long_tonic_is_found_to_the_cent_in_the_right_octave():
    y = melody([0, 200, 400, 500, 700, 400, 200, 0], 201.0, note_s=0.3)
    tonic, drone = detect_tonic(track_of(y), audio=y, sr=SR)
    assert abs(1200.0 * np.log2(tonic.hz / 201.0)) < 12.0  # not folded: the octave must be right too
    assert tonic.method == "histogram" and drone == 0.0
    assert tonic.cents_ref_hz == tonic.hz
    assert len(tonic.candidates) == 3
    assert tonic.candidates[0].hz == tonic.hz and tonic.candidates[0].confidence == tonic.confidence
    assert tonic.candidates[0].confidence > tonic.candidates[1].confidence >= tonic.candidates[2].confidence


def test_tonic_is_refined_between_histogram_bins():
    # 201.0 Hz is 46 cents above G3: nowhere near a 10-cent bin centre of any tidy reference
    for hz in (201.0, 207.3, 233.9):
        cents = synth.phrase([0, 200, 400, 700, 400, 200, 0], [0.3] * 6 + [1.0])
        tonic, _ = detect_tonic(contour_track(cents, hz))
        assert abs(1200.0 * np.log2(tonic.hz / hz)) < 1.0


def test_refine_uses_stable_frames_and_reports_when_it_cannot():
    cents = synth.concat(synth.const(703.0, 0.5), synth.silence(0.1), synth.ramp(0, 400, 0.2))
    prof = melody_profile(contour_track(cents, 55.0))  # the profile's own reference, so cents map one to one
    pc, measured = refine(prof, 700.0)
    assert measured and pc == pytest.approx(703.0, abs=0.5)
    pc, measured = refine(prof, 200.0)  # only a glide passes through here
    assert not measured
    assert [round(r.cents) for r in stable_runs(prof.cents, 0.01)] == [703]


def test_octave_holds_the_median_of_the_melody():
    low = synth.phrase([-500, -300, 0, -300, -500, -700, -500, 0], [0.3] * 7 + [1.0])  # mostly below the final
    tonic, _ = detect_tonic(contour_track(low, 220.0), tonic_hz=None)
    assert folded_error(tonic.hz, 220.0) < 5.0
    median_hz = 220.0 * 2.0 ** (np.nanmedian(low) / 1200.0)
    assert tonic.hz <= median_hz * 2.0 ** (50 / 1200.0) < 2.0 * tonic.hz
    # a melody that sits on its tonic most of the time must not drop an octave over tracker jitter
    drone_like = synth.concat(synth.const(-3.0, 2.0), synth.silence(0.05), synth.const(200, 0.3), synth.silence(0.05),
                              synth.const(0.0, 1.0))
    tonic, _ = detect_tonic(contour_track(drone_like, 220.0))
    assert abs(1200.0 * np.log2(tonic.hz / 220.0)) < 10.0


def test_rotations_of_one_pitch_set_cap_the_confidence(mohanam):
    y, track = mohanam
    tonic, _ = detect_tonic(track)  # no audio: histogram only
    assert folded_error(tonic.hz, SA) < 12.0
    assert tonic.method == "histogram"
    assert tonic.confidence <= section("tonic")["rotation_confidence_cap"]
    one_note, _ = detect_tonic(contour_track(synth.const(0, 2.0), 220.0))
    assert one_note.confidence <= 0.5 and len(one_note.candidates) == 3  # padded with the fourth and fifth


def test_style_selects_the_mode_tables():
    rast = synth.phrase([0, 200, 350, 500, 700, 500, 350, 200, 0], [0.35] * 8 + [0.9])
    for style in (None, "auto", "arabic_maqam", "not_a_format"):
        tonic, _ = detect_tonic(contour_track(rast, 180.0), style=style)
        assert folded_error(tonic.hz, 180.0) < 5.0


# --- drone method ---------------------------------------------------------------------

def test_sa_pa_drone_gives_the_drone_method_with_the_fifth_ranked_lower(mohanam):
    y, _ = mohanam
    mix = synth.mix(y, synth.drone([SA, SA * 1.5], len(y) / SR))
    tonic, drone = detect_tonic(track_of(mix), audio=mix, sr=SR)
    assert tonic.method == "drone" and drone >= 0.5
    assert abs(1200.0 * np.log2(tonic.hz / SA)) < 20.0
    assert len(tonic.candidates) == 3
    fifth = tonic.candidates[1]
    assert fifth.method == "drone" and folded_error(fifth.hz, SA * 1.5) < 20.0
    assert fifth.confidence < tonic.confidence


def test_full_tanpura_tuning_is_confident(mohanam):
    y, _ = mohanam
    mix = synth.mix(y, synth.drone([SA * 0.75, SA, SA, SA / 2], len(y) / SR))  # Pa, Sa, Sa, low Sa
    tonic, _ = detect_tonic(track_of(mix), audio=mix, sr=SR)
    assert tonic.method == "drone" and folded_error(tonic.hz, SA) < 20.0
    assert tonic.confidence > 0.7


def test_ma_tuned_drone_keeps_sa_in_the_top_two_and_stays_modest():
    hindolam = [0, 316, 498, 814, 1018, 814, 498, 316, 0, 316, 498, 316, 0]  # no Pa: the drone is tuned Sa-Ma
    y = melody(hindolam, SA)
    mix = synth.mix(y, synth.drone([SA, SA * 4 / 3, SA * 2], len(y) / SR))
    tonic, drone = detect_tonic(track_of(mix), audio=mix, sr=SR)
    assert drone >= 0.5
    assert any(folded_error(c.hz, SA) < 20.0 for c in tonic.candidates[:2])
    assert tonic.confidence <= 0.7
    assert len(tonic.candidates) == 3


def test_chain_of_fifths_takes_the_middle(mohanam):
    y, _ = mohanam
    mix = synth.mix(y, synth.drone([SA * 2 / 3, SA, SA * 1.5], len(y) / SR))  # Ma below, Sa, Pa above
    tonic, _ = detect_tonic(track_of(mix), audio=mix, sr=SR)
    assert tonic.method == "drone" and folded_error(tonic.hz, SA) < 20.0


def test_find_drone_reports_classes_and_ignores_the_melody(mohanam):
    y, track = mohanam
    assert find_drone(y, SR, track).confidence == 0.0  # a melody alone is not a drone
    held = synth.tone(220.0, 4.0)
    assert find_drone(held, SR, track_of(held)).confidence < 0.5  # nor is one held note
    assert find_drone(synth.noise(4.0, amp=0.1), SR).confidence == 0.0

    mix = synth.mix(y, synth.drone([SA, SA * 1.5], len(y) / SR))
    found = find_drone(mix, SR, track_of(mix))
    assert found.confidence == 1.0 and len(found.classes) == 2
    by_pitch = sorted(found.classes, key=lambda c: c.hz)
    assert abs(1200.0 * np.log2(by_pitch[0].hz / SA)) < 5.0  # measured from the partials, to a few cents
    assert abs(1200.0 * np.log2(by_pitch[1].hz / (SA * 1.5))) < 5.0
    assert found.lowest is by_pitch[0] and found.rms > 0


def test_frames_where_the_tracker_followed_the_drone_are_found():
    contour = synth.concat(synth.const(386, 0.6), synth.silence(1.0), synth.const(204, 0.6), synth.silence(0.8),
                           synth.const(0, 1.0))
    y = synth.render(contour, SA * 2)
    mix = synth.mix(y, synth.drone([SA, SA * 1.5], len(y) / SR, amp=0.1))
    track = track_of(mix)
    assert np.isfinite(track.f0[80:140]).mean() > 0.5  # the tracker does lock onto the drone in the rest
    mask = drone_only_frames(track, mix, SR, find_drone(mix, SR, track))
    assert mask[80:140].sum() >= np.isfinite(track.f0[80:140]).sum() - 2
    assert not mask[5:55].any() and not mask[310:390].any()  # sung notes are kept, the final Sa included


# --- overrides and reporting ---------------------------------------------------------------

def test_user_tonic_wins(mohanam):
    y, track = mohanam
    tonic, _ = detect_tonic(track, audio=y, sr=SR, tonic_hz=150.0)
    assert tonic.hz == 150.0 and tonic.method == "user" and tonic.confidence == 1.0
    assert tonic.candidates[0].method == "user" and len(tonic.candidates) == 3
    assert all(c.method != "user" for c in tonic.candidates[1:])  # what Orin would have picked is still listed


def test_tuning_offset_and_nearest_note():
    assert nearest_note(440.0) == ("A4", pytest.approx(0.0, abs=1e-9))
    name, offset = nearest_note(SA)
    assert name == "D3" and abs(offset) < 1.0
    name, offset = nearest_note(201.0)
    assert name == "G3" and offset == pytest.approx(43.6, abs=0.2)
    tonic, _ = detect_tonic(contour_track(synth.phrase([0, 200, 400, 0], [0.3, 0.3, 0.3, 1.0]), 201.0))
    assert tonic.nearest_note == "G3" and tonic.tuning_offset_cents == pytest.approx(43.6, abs=1.5)
    assert -50.0 <= tonic.tuning_offset_cents < 50.0


def test_no_melody_gives_an_assumed_tonic():
    silent = PitchTrack(t=np.arange(100) * 0.01, f0=np.full(100, np.nan), conf=np.zeros(100))
    tonic, drone = detect_tonic(silent)
    assert tonic.method == "assumed" and tonic.confidence == 0.0 and tonic.hz > 0 and drone == 0.0
    user, _ = detect_tonic(silent, tonic_hz=196.0)
    assert user.method == "user" and user.hz == 196.0


def test_key_profile_agreement_never_overrides():
    from orin.tonic import keyprofile

    # C major scale ending on C: the key profile agrees with the histogram's choice
    cents = synth.phrase([0, 200, 400, 500, 700, 900, 1100, 1200, 700, 400, 0], [0.3] * 10 + [1.0])
    c4 = 261.63
    prof = melody_profile(contour_track(cents, c4))
    key = keyprofile.estimate_key_pc(prof)
    assert key is not None and key[0] == 0 and key[1] == "major"
    assert keyprofile.agrees(prof, c4) and not keyprofile.agrees(prof, c4 * 1.5)
    # the same notes ending on a long D: the phrase ending decides, whatever the key profile says
    dorian = synth.phrase([200, 400, 500, 700, 900, 1100, 1200, 1400, 900, 500, 200], [0.3] * 10 + [1.2])
    tonic, _ = detect_tonic(contour_track(dorian, c4), style="western_classical")
    assert folded_error(tonic.hz, c4 * 2.0 ** (200 / 1200.0)) < 5.0
