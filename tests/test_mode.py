"""Mode inference (spec 7.12): closest matches with lookalikes, never a certain answer."""
import numpy as np
import pytest

from orin import config
from orin.formats import registry
from orin.mode.infer import ModeScore, folded_histogram, infer_mode, score_modes

METAS = registry.list_meta()
CFG = config.section("mode")
MOHANAM = [0, 204, 386, 702, 884]
RAST = [0, 200, 350, 500, 700, 900, 1050, 1200, 1050, 900, 700, 500, 350, 200, 0]


def test_folded_histogram_shape():
    hist = folded_histogram([0.0, 1200.0, -1200.0, 702.0], [1, 1, 1, 3])
    assert hist.shape == (120,)
    assert hist.sum() == pytest.approx(1.0)
    assert int(np.argmax(hist)) in (0, 70)  # bin 0 is centred on 0 cents, bin 70 on 700
    assert hist[0] == pytest.approx(hist[70], rel=0.05)  # octaves fold onto the tonic
    assert hist[1] == pytest.approx(hist[119])  # smoothing wraps around the octave
    assert hist[35] < 1e-6


def test_folded_histogram_ignores_silence_and_handles_nothing():
    hist = folded_histogram([np.nan, 386.0, np.nan])
    assert hist.sum() == pytest.approx(1.0) and int(np.argmax(hist)) == 39
    assert folded_histogram([]).sum() == 0.0


def test_score_modes_is_sorted_and_explains_itself():
    scores = score_modes(folded_histogram(MOHANAM), METAS)
    assert all(isinstance(s, ModeScore) for s in scores)
    assert [s.score for s in scores] == sorted((s.score for s in scores), reverse=True)
    assert len(scores) == sum(len(m.mode_table) for m in METAS)
    top = scores[0]
    assert (top.format_id, top.mode_id) == ("carnatic", "mohanam")
    assert top.coverage > 0.8 and top.out_mass < 0.05 and top.unused == 0.0
    seven = next(s for s in scores if s.mode_id == "shankarabharanam")
    assert seven.unused == pytest.approx(2 / 7, abs=0.01) and seven.score < top.score


def test_a_narrow_kernel_is_widened_so_tuning_cannot_pick_the_mode():
    hist = folded_histogram([0, 200, 400, 700, 900])  # equal-tempered pentatonic
    narrow = {(s.format_id, s.mode_id): s.score for s in score_modes(hist, METAS, kernel_cents=2)}
    usual = {(s.format_id, s.mode_id): s.score for s in score_modes(hist, METAS, kernel_cents=25)}
    assert narrow == usual
    # 14 cents between a just and an equal-tempered third does not separate the lookalikes
    assert abs(usual[("carnatic", "mohanam")] - usual[("western_pop", "major_pentatonic")]) < 0.1


def test_short_phrase_gives_closest_matches_with_low_confidence():
    anchors = [0, 204, 386, 884, 702]  # ends on 702
    mode = infer_mode(anchors, [0.3] * 5, [False] * 5, metas=METAS, style="carnatic")
    assert len(mode.candidates) >= 3
    assert all(c.label_system == "carnatic" for c in mode.candidates)
    assert mode.confidence <= 0.6
    assert not mode.user_selected


def test_mohanam_is_the_closest_match_but_never_certain():
    anchors = [204, 386, 702, 884, 0]
    mode = infer_mode(anchors, [0.3, 0.3, 0.3, 0.3, 1.0], [False] * 5, metas=METAS, style="carnatic")
    assert mode.mode_id == "mohanam" and mode.label == "Mohanam" and mode.label_system == "carnatic"
    assert mode.candidates[0].id == "mohanam"
    assert mode.confidence < 0.9
    assert mode.allowed_pitches_cents == (0.0, 204.0, 386.0, 702.0, 884.0)
    long_clip = infer_mode(anchors * 10, [0.5] * 50, None, metas=METAS, style="carnatic", clip_duration_s=40.0)
    assert long_clip.mode_id == "mohanam"
    assert CFG["short_clip_cap"] < long_clip.confidence <= CFG["confidence_cap"]


def test_lookalikes_are_attached():
    mode = infer_mode(MOHANAM, [0.5] * 5, None, metas=METAS, style="carnatic")
    top = mode.candidates[0]
    assert "western_pop:major_pentatonic" in top.lookalikes
    assert any(ref.endswith("~") for ref in top.lookalikes)  # rotations: Hindolam, Madhyamavati...
    assert top.lookalikes == registry.lookalikes("carnatic", "mohanam")


def test_rast_phrase_within_arabic():
    mode = infer_mode(RAST, [0.4] * len(RAST), None, metas=METAS, style="arabic_maqam", clip_duration_s=30.0)
    assert mode.mode_id == "rast" and mode.label_system == "arabic_maqam"
    assert len(mode.candidates) == 3
    assert mode.confidence <= CFG["confidence_cap"]
    assert 350.0 in mode.allowed_pitches_cents


def test_short_clip_and_few_pitch_classes_are_capped():
    short = infer_mode(RAST, [0.2] * len(RAST), None, metas=METAS, style="arabic_maqam", clip_duration_s=5.0)
    assert short.confidence <= CFG["short_clip_cap"]
    few = infer_mode([0, 200, 400, 0], [10.0] * 4, None, metas=METAS, clip_duration_s=40.0)
    assert few.confidence <= CFG["short_clip_cap"]


def test_oscillating_notes_count_for_less():
    anchors = [0, 204, 386, 702, 884, 590]
    plain = infer_mode(anchors, [1.0] * 6, [False] * 6, metas=METAS, style="carnatic")
    shaken = infer_mode(anchors, [1.0] * 6, [False] * 5 + [True], metas=METAS, style="carnatic")
    score = {c.id: c.score for c in shaken.candidates}
    assert score["mohanam"] > {c.id: c.score for c in plain.candidates}.get("mohanam", -1.0)


def test_without_a_style_every_format_is_searched():
    mode = infer_mode(RAST, [0.4] * len(RAST), None, metas=METAS, clip_duration_s=30.0)
    assert (mode.label_system, mode.mode_id) == ("arabic_maqam", "rast")
    assert len(mode.candidates) == 3


def test_the_same_pitches_are_listed_once_with_their_other_names():
    major = [0, 200, 400, 500, 700, 900, 1100, 1200]
    mode = infer_mode(major, [0.5] * 8, None, metas=METAS, clip_duration_s=30.0)
    assert mode.mode_id == "major" and mode.label_system == "western_classical"
    top = mode.candidates[0]
    for ref in ("western_pop:major", "rock:major", "arabic_maqam:ajam", "carnatic:shankarabharanam"):
        assert ref in top.lookalikes
    assert len(mode.candidates) == 3
    assert len({c.id for c in mode.candidates}) == 3
    listed = {f"{c.label_system}:{c.id}" for c in mode.candidates}
    assert not listed & {ref for ref in top.lookalikes if not ref.endswith("~")}
    # with no style to choose between major, Ajam and Shankarabharanam, the name is a weak claim
    assert mode.confidence <= CFG["short_clip_cap"]
    western = infer_mode(major, [0.5] * 8, None, metas=METAS, style="western_classical", clip_duration_s=30.0)
    assert western.mode_id == "major" and CFG["short_clip_cap"] < western.confidence <= CFG["confidence_cap"]


def test_user_mode_wins():
    mode = infer_mode(MOHANAM, [0.5] * 5, None, metas=METAS, style="carnatic", mode_id="kalyani")
    assert mode.user_selected and mode.mode_id == "kalyani" and mode.label_system == "carnatic"
    assert 590.0 in mode.allowed_pitches_cents
    assert mode.candidates and mode.candidates[0].id == "mohanam"  # the closest matches are still shown
    other = infer_mode(MOHANAM, [0.5] * 5, None, metas=METAS, style="carnatic", mode_id="arabic_maqam:rast")
    assert other.user_selected and (other.label_system, other.mode_id) == ("arabic_maqam", "rast")
    unknown = infer_mode(MOHANAM, [0.5] * 5, None, metas=METAS, style="carnatic", mode_id="not-a-raga")
    assert unknown.user_selected and unknown.mode_id == "not-a-raga" and unknown.allowed_pitches_cents == ()


def test_no_notes_gives_an_empty_mode():
    mode = infer_mode([], [], [], metas=METAS, style="carnatic")
    assert mode.mode_id is None and mode.confidence == 0.0 and mode.candidates == ()
