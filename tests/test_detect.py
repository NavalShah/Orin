"""Style auto-detect (spec 8): ranked candidates with scores, and honest limits."""
import numpy as np
import pytest

from orin import config, rhythm
from orin.detect.features import StyleFeatures, extract_features, pitch_features
from orin.detect.score import HONEST_LIMITS, decide, default_score_fit, rank_formats
from orin.eval import synth
from orin.formats import registry
from orin.ir.models import IR, FormatCandidate, NoteEvent, Source, Tonic
from orin.segment.notes import segment_contour
from orin.segment.ornaments import annotate

WESTERN = {"western_classical", "western_pop", "rock"}
TONIC = Tonic(hz=220.0, cents_ref_hz=220.0, confidence=0.8, method="histogram")
RAST = [0, 200, 350, 500, 700, 900, 1050, 1200, 1050, 900, 700, 500, 350, 200, 0]
MAJOR = [0, 200, 400, 500, 700, 900, 1100, 1200, 1100, 900, 700, 500, 400, 200, 0]
MOHANAM = [0, 204, 386, 702, 884, 702, 386, 204]


def features_of(contour, profile="neutral", **kw):
    p = config.profile(profile)
    t = synth.times(contour)
    conf = np.ones(len(contour))
    segments = annotate(segment_contour(t, contour, conf, p), t, contour, p)
    return extract_features(t=t, cents=contour, conf=conf, segments=segments, tonic=TONIC, **kw)


def neutral_phrase():
    return features_of(synth.phrase(RAST * 3, 0.3, 0.02))


def plain_phrase():
    return features_of(synth.phrase(MAJOR * 3, 0.3, 0.02))


def oscillated_phrase():
    notes = [synth.concat(synth.oscillation(c, 80, 5, 0.8), synth.silence(0.05)) for c in MOHANAM * 2]
    return features_of(synth.concat(*notes), drone_confidence=0.9)


def test_features_of_a_neutral_degree_phrase():
    f = neutral_phrase()
    assert f.neutral_mass == pytest.approx(4 / 15, abs=0.03)
    assert f.grid12_rms_cents > 15
    assert f.oscillation_share == 0.0 and f.glide_share == 0.0 and f.drone == 0.0
    assert f.duration_s == pytest.approx(14.4, abs=0.2)
    assert set(f.mode_fit) == set(registry.all_formats())
    assert f.mode_fit["arabic_maqam"] > f.mode_fit["western_classical"]


def test_features_of_a_plain_and_an_oscillated_phrase():
    plain = plain_phrase()
    assert plain.neutral_mass == 0.0 and plain.grid12_rms_cents < 1.0 and plain.ornament_density == 0.0
    shaken = oscillated_phrase()
    assert shaken.oscillation_share > 0.8 and shaken.drone == pytest.approx(0.9)
    assert shaken.ornament_density > 1.0 and shaken.neutral_mass == 0.0


def test_glides_rhythm_and_timbre_reach_the_features():
    contour = synth.concat(*[synth.concat(synth.const(0, 0.4), synth.ramp(0, 400, 0.3), synth.const(400, 0.4),
                                          synth.silence(0.05)) for _ in range(4)])
    onsets = np.concatenate(([0.0], np.cumsum([0.41, 0.97, 0.23, 1.31, 0.58, 0.19, 0.83, 0.37, 1.12])))
    free = rhythm.analyze(onsets, np.full(len(onsets), 0.2), templates=())
    clean = synth.render(contour, 220.0)
    f = features_of(contour, rhythm=free, audio=clean, sr=synth.SR)
    assert 0.15 < f.glide_share < 0.35
    assert f.free_time == 1.0 and f.tempo_bpm is None
    assert f.duration_s == pytest.approx(len(clean) / synth.SR)
    noisy = features_of(contour, audio=clean + synth.noise(len(clean) / synth.SR, amp=0.2), sr=synth.SR)
    assert f.spectral_flatness < 0.05 < noisy.spectral_flatness
    drone_tonic = extract_features(t=synth.times(contour), cents=contour, conf=np.ones(len(contour)), segments=[],
                                   tonic=Tonic(hz=220.0, cents_ref_hz=220.0, confidence=0.7, method="drone"))
    assert drone_tonic.drone == pytest.approx(0.7)


def test_tonic_is_refined_before_neutral_degrees_are_counted():
    sharp = [c + 20.0 for c in RAST]  # the whole phrase sits 20 cents above the detected tonic
    neutral_mass, _ = pitch_features(sharp, [0.4] * len(sharp))
    assert neutral_mass == pytest.approx(4 / 15, abs=0.01)
    assert pitch_features([c + 20.0 for c in MAJOR], [0.4] * len(MAJOR))[0] == 0.0


# --- 12. ranking ----------------------------------------------------------------

def check_ranking(candidates):
    assert 1 <= len(candidates) <= 3
    assert sum(c.score for c in candidates) == pytest.approx(1.0)
    assert [c.score for c in candidates] == sorted((c.score for c in candidates), reverse=True)
    assert len({c.format_id for c in candidates}) == len(candidates)
    assert all(isinstance(c, FormatCandidate) and registry.has(c.format_id) for c in candidates)


def test_neutral_degree_phrase_ranks_a_maqam_family_format_first():
    ranked = rank_formats(neutral_phrase())
    check_ranking(ranked)
    assert ranked[0].format_id in {"arabic_maqam", "persian_dastgah"}
    assert {c.format_id for c in ranked[:2]} == {"arabic_maqam", "persian_dastgah"}


def test_plain_twelve_tone_phrase_ranks_a_western_format_first():
    ranked = rank_formats(plain_phrase())
    check_ranking(ranked)
    assert ranked[0].format_id in WESTERN
    # classical, pop and rock cannot be told apart from one line, so auto-detect must ask
    ask, message = decide(ranked, 14.4)
    assert ask and message.startswith("Not sure - please choose")


def test_heavily_oscillated_phrase_over_a_drone_ranks_carnatic_first():
    ranked = rank_formats(oscillated_phrase())
    check_ranking(ranked)
    assert ranked[0].format_id == "carnatic"
    assert decide(ranked, 13.6) == (False, None)


def test_short_clip_asks_the_user():
    f = features_of(synth.phrase([0, 200, 400, 500, 700], 1.0, 0.02))
    assert f.duration_s < config.section("detect")["min_duration_s"]
    ask, message = decide(rank_formats(f), f.duration_s)
    assert ask and "Not sure - please choose" in message
    sure = (FormatCandidate(format_id="carnatic", score=0.8), FormatCandidate(format_id="rock", score=0.2))
    ask, message = decide(sure, 5.0)
    assert ask and "Not sure - please choose" in message
    assert decide(sure, 30.0) == (False, None)


def test_decide_asks_when_scores_are_low_or_close():
    close = (FormatCandidate(format_id="western_pop", score=0.52), FormatCandidate(format_id="rock", score=0.48))
    low = tuple(FormatCandidate(format_id=f, score=s) for f, s in
                (("carnatic", 0.4), ("rock", 0.35), ("western_pop", 0.25)))
    for candidates in (close, low, ()):
        ask, message = decide(candidates, 30.0)
        assert ask and "Not sure - please choose" in message


# --- score_fit --------------------------------------------------------------------

def test_default_score_fit_is_bounded_and_follows_the_priors():
    shaken, plain = oscillated_phrase(), plain_phrase()
    for adapter in registry.all_formats().values():
        for f in (shaken, plain, StyleFeatures()):
            assert 0.0 <= adapter.score_fit(None, f) <= 1.0
    carnatic, western = registry.get("carnatic"), registry.get("western_classical")
    assert carnatic.score_fit(None, shaken) > carnatic.score_fit(None, plain)
    assert western.score_fit(None, plain) > western.score_fit(None, shaken)
    blended = plain.model_copy(update={"mode_fit": {"carnatic": 1.0}})
    bare = plain.model_copy(update={"mode_fit": {}})
    assert default_score_fit(carnatic.META, None, blended) > default_score_fit(carnatic.META, None, bare)


def test_score_fit_from_an_ir_alone():
    def ir_of(cents):
        events = tuple(NoteEvent(id=f"e{i}", onset_s=0.5 * i, offset_s=0.5 * i + 0.45, pitch_cents=c,
                                 pitch_cents_median=c, pitch_cents_min=c, pitch_cents_max=c)
                       for i, c in enumerate(cents))
        return IR(source=Source(kind="notes", duration_s=0.5 * len(cents)),
                  tonic=Tonic(hz=220.0, cents_ref_hz=220.0, confidence=1.0, method="declared"), events=events)

    plain, neutral = ir_of(MAJOR * 2), ir_of(RAST * 2)
    western, arabic = registry.get("western_classical"), registry.get("arabic_maqam")
    for ir in (plain, neutral):
        for adapter in registry.all_formats().values():
            assert 0.0 <= adapter.score_fit(ir) <= 1.0
    assert western.score_fit(plain) > arabic.score_fit(plain)
    assert arabic.score_fit(neutral) > western.score_fit(neutral)
    assert default_score_fit(western.META, None, None) == 0.0


def test_honest_limits_say_what_cannot_be_told_apart():
    text = " ".join(HONEST_LIMITS).lower()
    assert all(isinstance(line, str) and line.endswith(".") for line in HONEST_LIMITS)
    for words in (("arabic", "persian"), ("major", "phrygian", "ajam", "nahawand", "kurd", "carnatic"),
                  ("classical", "pop", "rock"), ("10 seconds",), ("not an oracle",)):
        assert all(w in text for w in words), words
