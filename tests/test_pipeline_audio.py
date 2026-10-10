"""The audio pipeline end to end (spec 7.0, 7.13, 7.16), on synthesised audio.

pYIN is used throughout to keep the file fast; the CREPE path has one test, marked slow.
"""
import numpy as np
import pytest

from orin.eval import synth
from orin.options import STAGES, AnalysisOptions

for needed in ("orin.segment.notes", "orin.segment.ornaments", "orin.rhythm", "orin.mode.infer", "orin.detect.score"):
    pytest.importorskip(needed)

from orin import pipeline  # noqa: E402
from orin.cache import StageCache  # noqa: E402
from orin.errors import IngestError, UnknownFormat  # noqa: E402
from orin.ir.validate import dump_ir, load_ir, validate_ir  # noqa: E402

STEPS = [0, 200, 400, 500, 700, 500, 400, 200, 0]
TONIC_HZ = 220.0
LEAD_S = 0.5
FAST = dict(trackers="pyin", separate="off")


def write_phrase(path):
    contour = synth.concat(synth.silence(LEAD_S), synth.phrase(STEPS, [0.4] * 8 + [1.0], gap_s=0.04), synth.silence(0.3))
    return synth.write_wav(str(path), synth.render(contour, TONIC_HZ))


def logs(ir):
    out = {}
    for entry in ir.meta.processing_log:
        out.setdefault(entry.stage, entry)  # the first entry per stage
    return out


def codes(ir):
    return [w.code for w in ir.meta.warnings]


@pytest.fixture(scope="module")
def phrase(tmp_path_factory):
    root = tmp_path_factory.mktemp("pipeline")
    wav = write_phrase(root / "phrase.wav")
    cache = StageCache(root / "cache")
    ir = pipeline.audio_to_ir(wav, AnalysisOptions(style="western_classical", **FAST), cache=cache)
    return wav, cache, ir


def test_stepwise_phrase_gives_the_right_notes(phrase):
    _, _, ir = phrase
    assert len(ir.events) == len(STEPS)
    assert [e.id for e in ir.events] == [f"e{i + 1}" for i in range(len(STEPS))]
    for event, expected in zip(ir.events, STEPS):
        sounding = ir.tonic.hz * 2.0 ** (event.pitch_cents / 1200.0)
        assert abs(1200.0 * np.log2(sounding / (TONIC_HZ * 2.0 ** (expected / 1200.0)))) < 15.0
        assert abs(event.pitch_cents - expected) < 15.0  # the tonic was found too, so the cents line up
        assert event.pitch_cents_min <= event.pitch_cents_median <= event.pitch_cents_max
        assert 0.0 < event.confidence <= 1.0 and event.loudness_db is not None
    assert abs(1200.0 * np.log2(ir.tonic.hz / TONIC_HZ)) < 12.0
    assert codes(ir) == []


def test_times_are_in_the_original_file(phrase):
    _, _, ir = phrase
    # the file starts with 0.5 s of silence that preprocessing trims; reported times must not shift
    assert ir.events[0].onset_s == pytest.approx(LEAD_S, abs=0.04)
    expected_onsets = LEAD_S + np.arange(len(STEPS)) * 0.44
    assert np.allclose([e.onset_s for e in ir.events], expected_onsets, atol=0.05)
    assert ir.events[-1].offset_s == pytest.approx(LEAD_S + 8 * 0.44 + 1.0, abs=0.06)
    assert ir.contour[0].t_s > 0.3 and ir.contour_hop_s == 0.01


def test_ir_validates_and_round_trips(phrase):
    _, _, ir = phrase
    assert validate_ir(ir) == []
    assert load_ir(dump_ir(ir)) == ir
    assert len(ir.contour) > 400
    voiced = [p for p in ir.contour if p.cents is not None]
    assert len(voiced) > 0.8 * len(ir.contour) * (4.2 / 4.9)
    for event in ir.events:
        for o in (event.ornament, *event.extra_ornaments):
            if o.contour_ref is not None:
                assert 0 <= o.contour_ref[0] <= o.contour_ref[1] <= len(ir.contour)


def test_source_and_analysis_are_recorded(phrase):
    wav, _, ir = phrase
    assert ir.source.kind == "audio" and ir.source.filename == "phrase.wav"
    assert ir.source.sample_rate == 22050 and ir.source.channels == 1
    assert ir.source.duration_s == pytest.approx(LEAD_S + 8 * 0.44 + 1.0 + 0.3, abs=0.02)
    assert len(ir.source.sha256) == 64 and ir.source.loudness_dbfs < 0
    assert ir.source.format_hint == "western_classical" and ir.source.detected_format is None
    analysis = ir.meta.analysis
    assert analysis.style == "western_classical" and analysis.profile_id == "western"
    assert analysis.trackers == ("pyin",)
    assert analysis.user_overrides == {"style": "western_classical", "trackers": "pyin", "separate": "off"}


def test_processing_log_has_every_stage_in_order(phrase):
    _, _, ir = phrase
    names = [entry.stage for entry in ir.meta.processing_log]
    assert names == list(STAGES)
    assert all(entry.duration_ms >= 0 and entry.version for entry in ir.meta.processing_log)
    assert logs(ir)["detect"].note.startswith("skipped")
    assert logs(ir)["pitch"].params["trackers"] == "pyin"


def test_second_run_hits_the_cache(phrase):
    wav, cache, first = phrase
    again = pipeline.audio_to_ir(wav, AnalysisOptions(style="western_classical", **FAST), cache=cache)
    hit = {name: entry.cache_hit for name, entry in logs(again).items()}
    for stage in ("ingest", "preprocess", "pitch", "voicing", "tonic", "segment", "ornament", "rhythm", "mode"):
        assert hit[stage], stage
    assert again.events == first.events and again.tonic == first.tonic and again.contour == first.contour


def test_reanalyze_with_a_new_tonic_does_not_track_pitch_again(phrase, monkeypatch):
    wav, cache, first = phrase
    import orin.pitch.ensemble as ensemble_module

    def forbidden(*args, **kwargs):
        raise AssertionError("pitch tracking ran again")

    monkeypatch.setattr(ensemble_module, "track_pitch", forbidden)
    options = AnalysisOptions(style="western_classical", tonic_hz=330.0, **FAST)
    for target in (wav, first):  # by path, and from the IR alone (the audio copies are in the cache)
        ir = pipeline.reanalyze(target, options, cache=cache)
        assert logs(ir)["pitch"].cache_hit and "pitch_failed" not in codes(ir)
        assert ir.tonic.hz == 330.0 and ir.tonic.method == "user" and ir.tonic.confidence == 1.0
        assert ir.meta.analysis.user_overrides["tonic_hz"] == 330.0
        assert len(ir.events) == len(STEPS) and validate_ir(ir) == []
        shift = 1200.0 * np.log2(330.0 / first.tonic.hz)
        assert ir.events[0].pitch_cents == pytest.approx(first.events[0].pitch_cents - shift, abs=2.0)
        assert ir.source.filename == "phrase.wav"


def test_other_overrides_reuse_the_pitch_track_too(phrase, monkeypatch):
    wav, cache, first = phrase
    import orin.pitch.ensemble as ensemble_module

    monkeypatch.setattr(ensemble_module, "track_pitch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-run")))
    changes = [dict(style="carnatic"), dict(style="western_classical", mode_id="dorian"),
               dict(style="western_classical", sensitivity=1.5), dict(style="western_classical", tempo_bpm=90.0),
               dict(style="western_classical", cycle_template_id="3_4"), dict(style="auto")]
    for change in changes:
        ir = pipeline.reanalyze(wav, AnalysisOptions(**change, **FAST), cache=cache)
        assert logs(ir)["pitch"].cache_hit, change
        assert not any(code.endswith("_failed") for code in codes(ir)), (change, codes(ir))
        assert validate_ir(ir) == [] and len(ir.events) >= 5
        for key, value in change.items():
            if value != "auto":
                assert ir.meta.analysis.user_overrides[key] == value
    assert ir.source.detected_format is not None  # the last change was auto-detect


def test_manual_mode_and_cycle_are_applied(phrase):
    wav, cache, _ = phrase
    ir = pipeline.audio_to_ir(wav, AnalysisOptions(style="western_classical", mode_id="dorian",
                                                   cycle_template_id="3_4", **FAST), cache=cache)
    assert ir.mode.mode_id == "dorian" and ir.mode.user_selected
    assert ir.cycle.template_id == "3_4" and not ir.tempo.free_time
    assert all(e.onset_pulse is not None for e in ir.events)


def test_auto_detect_records_three_candidates(phrase):
    wav, cache, _ = phrase
    ir = pipeline.audio_to_ir(wav, AnalysisOptions(**FAST), cache=cache)
    candidates = ir.source.detected_candidates
    assert 1 <= len(candidates) <= 3
    assert sum(c.score for c in candidates) == pytest.approx(1.0, abs=1e-6)
    assert ir.source.detected_format == candidates[0].format_id
    assert ir.source.detected_format_confidence == candidates[0].score
    assert ir.source.format_hint is None and ir.meta.analysis.style == candidates[0].format_id
    assert "style_uncertain" in codes(ir)  # a 5-second clip is too short to call
    assert set(STAGES) <= {entry.stage for entry in ir.meta.processing_log}
    assert len(ir.events) == len(STEPS) and validate_ir(ir) == []


def test_low_noise_gives_no_events_and_a_warning(tmp_path):
    wav = synth.write_wav(str(tmp_path / "noise.wav"), synth.noise(2.0, amp=0.01))
    ir = pipeline.audio_to_ir(wav, AnalysisOptions(**FAST), cache=tmp_path / "cache")
    assert ir.events == () and ir.contour == ()
    assert "little_pitch" in codes(ir)
    message = next(w.message for w in ir.meta.warnings if w.code == "little_pitch")
    assert message.lower().startswith("little pitched melody found")
    assert ir.tonic.method == "assumed" and ir.tonic.confidence == 0.0 and ir.mode.label is None
    assert validate_ir(ir) == []
    assert [entry.stage for entry in ir.meta.processing_log] == list(STAGES)
    assert logs(ir)["segment"].note.startswith("skipped")


@pytest.mark.parametrize("target, attribute, stage", [
    ("orin.rhythm", "analyze", "rhythm"),
    ("orin.segment.notes", "segment_contour", "segment"),
    ("orin.segment.ornaments", "annotate", "ornament"),
    ("orin.mode.infer", "infer_mode", "mode"),
    ("orin.tonic.select", "detect_tonic", "tonic"),
    ("orin.pitch.voicing", "clean_voicing", "voicing"),
])
def test_a_failing_stage_warns_and_still_returns_an_ir(tmp_path, monkeypatch, target, attribute, stage):
    import importlib

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(importlib.import_module(target), attribute, broken)
    wav = write_phrase(tmp_path / "phrase.wav")
    ir = pipeline.audio_to_ir(wav, AnalysisOptions(style="western_classical", **FAST), cache=tmp_path / "cache")
    assert f"{stage}_failed" in codes(ir)
    assert validate_ir(ir) == []
    assert logs(ir)[stage].note.startswith("failed") and not logs(ir)[stage].cache_hit
    assert set(STAGES) <= {entry.stage for entry in ir.meta.processing_log}
    if stage == "tonic":
        assert ir.tonic.method == "assumed"
    else:
        assert len(ir.events) >= len(STEPS) - 1  # the fallback still finds the notes
    if stage == "rhythm":
        assert ir.tempo.free_time and ir.tempo.bpm is None
    # a failed stage is not cached: once it works again, the next run is the real thing
    monkeypatch.undo()
    healed = pipeline.audio_to_ir(wav, AnalysisOptions(style="western_classical", **FAST), cache=tmp_path / "cache")
    assert f"{stage}_failed" not in codes(healed) and not logs(healed)[stage].cache_hit


def test_a_failing_pitch_tracker_is_survived(tmp_path, monkeypatch):
    import orin.pitch.ensemble as ensemble_module

    monkeypatch.setattr(ensemble_module, "track_pitch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    wav = write_phrase(tmp_path / "phrase.wav")
    ir = pipeline.audio_to_ir(wav, AnalysisOptions(style="western_classical", **FAST), cache=tmp_path / "cache")
    assert "pitch_failed" in codes(ir) and "little_pitch" in codes(ir)
    assert ir.events == () and validate_ir(ir) == []


def test_progress_is_reported_for_every_stage(phrase):
    wav, cache, _ = phrase
    seen = []
    pipeline.audio_to_ir(wav, AnalysisOptions(style="western_classical", **FAST), cache=cache,
                         progress=lambda stage, fraction, message: seen.append((stage, fraction, message)))
    assert [s for s, _, _ in seen][::2] == list(STAGES)  # each stage reports its start and its end
    fractions = [f for _, f, _ in seen]
    assert fractions == sorted(fractions) and fractions[0] == 0.0 and fractions[-1] == 1.0
    assert all(isinstance(m, str) and m for _, _, m in seen)

    def broken(stage, fraction, message):
        raise RuntimeError("the listener went away")

    assert pipeline.audio_to_ir(wav, AnalysisOptions(style="western_classical", **FAST), cache=cache, progress=broken)


def test_bad_input_and_bad_style_are_errors_not_warnings(tmp_path):
    text = tmp_path / "notes.wav"
    text.write_text("S R2 G3 P D2")
    with pytest.raises(IngestError) as info:
        pipeline.audio_to_ir(text, cache=tmp_path / "cache")
    assert info.value.code == "unsupported_type"
    wav = write_phrase(tmp_path / "phrase.wav")
    with pytest.raises(IngestError) as info:
        pipeline.audio_to_ir(wav, AnalysisOptions(max_seconds=1.0, **FAST), cache=tmp_path / "cache")
    assert info.value.code == "too_long"
    with pytest.raises(UnknownFormat):
        pipeline.audio_to_ir(wav, AnalysisOptions(style="klingon_opera", **FAST), cache=tmp_path / "cache")


def test_drone_under_a_melody_is_used_for_the_tonic_and_not_transcribed(tmp_path):
    notes = [0, 204, 386, 702, 386, 204, 0]
    contour = synth.concat(synth.silence(0.6), synth.phrase(notes, [0.4] * 6 + [1.0], gap_s=0.04), synth.silence(0.6))
    y = synth.render(contour, 293.66)
    mix = synth.mix(y, synth.drone([146.83, 220.25, 293.66], len(y) / synth.SR))
    wav = synth.write_wav(str(tmp_path / "drone.wav"), mix)
    ir = pipeline.audio_to_ir(wav, AnalysisOptions(style="carnatic", trackers="pyin"), cache=tmp_path / "cache")
    assert ir.tonic.method == "drone"
    assert abs((1200.0 * np.log2(ir.tonic.hz / 146.83) + 600.0) % 1200.0 - 600.0) < 20.0
    assert logs(ir)["separate"].note.startswith("skipped")  # a drone is not accompaniment to strip
    assert len(ir.events) == len(notes)  # the drone alone, before and after the singing, is not a note
    assert all(abs(e.pitch_cents - expected) < 15.0 for e, expected in zip(ir.events, notes))


@pytest.mark.slow
def test_default_tracker_end_to_end(tmp_path):
    pytest.importorskip("torchcrepe")
    wav = write_phrase(tmp_path / "phrase.wav")
    ir = pipeline.audio_to_ir(wav, AnalysisOptions(style="western_classical", separate="off"), cache=tmp_path / "cache")
    assert ir.meta.analysis.trackers == ("crepe",)
    assert len(ir.events) == len(STEPS)
    assert all(abs(e.pitch_cents - expected) < 15.0 for e, expected in zip(ir.events, STEPS))
    assert codes(ir) == [] and validate_ir(ir) == []


# --- notes input and overrides on an existing IR --------------------------------------------

def test_notes_to_ir_fills_the_mode():
    pytest.importorskip("orin.ingest.notes")
    ir = pipeline.notes_to_ir("S R2 G3 P D2 P G3 R2 S", format_id="carnatic", syntax="sargam")
    assert ir.source.kind == "notes" and len(ir.events) == 9
    assert ir.mode.candidates, "the parser left the mode empty and the pipeline must fill it"
    assert "mohanam" in [c.id for c in ir.mode.candidates]
    assert validate_ir(ir) == []
    chosen = pipeline.notes_to_ir("S R2 G3 P D2 P G3 R2 S", format_id="carnatic", syntax="sargam",
                                  options=AnalysisOptions(mode_id="mohanam", tonic_hz=146.83))
    assert chosen.mode.mode_id == "mohanam" and chosen.tonic.hz == pytest.approx(146.83)


def test_reanalyze_applies_overrides_to_a_notes_ir():
    pytest.importorskip("orin.ingest.notes")
    ir = pipeline.notes_to_ir("S R2 G3 P D2 P G3 R2 S", format_id="carnatic", syntax="sargam")
    moved = pipeline.reanalyze(ir, AnalysisOptions(tonic_hz=ir.tonic.hz * 1.5, mode_id="mohanam"))
    assert moved.tonic.hz == pytest.approx(ir.tonic.hz * 1.5) and moved.tonic.method == "user"
    # sargam is relative to Sa: a new Sa transposes, the swaras stay what was typed
    assert [e.pitch_cents for e in moved.events] == [e.pitch_cents for e in ir.events]
    assert moved.mode.mode_id == "mohanam" and moved.mode.user_selected
    assert moved.meta.analysis.user_overrides["tonic_hz"] == pytest.approx(ir.tonic.hz * 1.5)
    assert moved.meta.analysis.user_overrides["mode_id"] == "mohanam"
    assert validate_ir(moved) == [] and ir.tonic.method != "user"  # the input IR is untouched


def test_reanalyze_without_the_audio_remeasures_from_the_new_tonic(phrase, tmp_path):
    _, _, first = phrase
    moved = pipeline.reanalyze(first, AnalysisOptions(tonic_hz=first.tonic.hz * 2.0 ** (200 / 1200.0)),
                               cache=tmp_path / "an-empty-cache")
    assert "reanalyze_without_audio" in codes(moved)
    assert moved.tonic.method == "user"
    # the sounding pitch is the fact: every note is now 200 cents lower against the new tonic
    for before, after in zip(first.events, moved.events):
        assert after.pitch_cents == pytest.approx(before.pitch_cents - 200.0, abs=1e-6)
        assert (after.onset_s, after.offset_s) == (before.onset_s, before.offset_s)
    assert validate_ir(moved) == []


# --- stem separation (spec 7.3): optional, and never a reason to fail -------------------------

def test_select_melody_stem_prefers_vocals_when_they_are_there():
    from orin.separate.select import select_melody_stem, to_mono

    voice = np.stack([synth.tone(220.0, 0.5, sr=8000)] * 2)
    quiet = voice * 0.001
    band = np.stack([synth.tone(110.0, 0.5, sr=8000)] * 2)
    assert select_melody_stem({"vocals": voice, "other": band, "drums": band, "bass": band}) == "vocals"
    assert select_melody_stem({"vocals": quiet, "other": band, "drums": band, "bass": band}) == "other"
    assert select_melody_stem({"vocals": voice, "other": band}, "other") == "other"  # the user's choice wins
    assert select_melody_stem({"vocals": quiet, "other": band}, "vocals") == "vocals"
    assert select_melody_stem({"vocals": voice, "other": band}, "auto") == "vocals"
    assert to_mono(voice).shape == (4000,)


def test_separation_fails_soft_without_the_package_or_the_weights(monkeypatch):
    import sys

    from orin.separate import demucs as sep

    y = np.stack([synth.tone(220.0, 0.5, sr=44100)] * 2).astype(np.float32)
    monkeypatch.setattr(sep, "weights_available", lambda name=None: False)
    monkeypatch.setattr(sep, "_load_model", lambda name: (_ for _ in ()).throw(AssertionError("tried to load or download")))
    monkeypatch.delenv("ORIN_DEMUCS_DOWNLOAD", raising=False)
    stems, reason = sep.separate_with_reason(y, 44100)
    assert stems is None and "not been downloaded" in reason
    assert sep.separate(y, 44100) is None

    monkeypatch.setitem(sys.modules, "demucs", None)  # as if the package were not installed
    monkeypatch.setitem(sys.modules, "demucs.apply", None)
    stems, reason = sep.separate_with_reason(y, 44100)
    assert stems is None and "not installed" in reason


@pytest.mark.slow
def test_separation_runs_on_arrays_with_demucs_own_test_model():
    pytest.importorskip("demucs")
    from orin.separate.demucs import STEMS, separate_with_reason

    y = np.stack([synth.tone(220.0, 1.0, sr=44100)] * 2).astype(np.float32)
    stems, reason = separate_with_reason(y, 44100, model="demucs_unittest")  # random weights, nothing to download
    assert reason is None and set(stems) == set(STEMS)
    assert all(s.shape == y.shape and s.dtype == np.float32 and np.isfinite(s).all() for s in stems.values())


def test_unavailable_separation_warns_and_transcribes_the_mix(tmp_path, monkeypatch):
    from orin.separate import demucs as sep

    monkeypatch.setattr(sep, "separate_with_reason", lambda *a, **k: (None, "Stem separation is not installed."))
    wav = write_phrase(tmp_path / "phrase.wav")
    ir = pipeline.audio_to_ir(wav, AnalysisOptions(style="western_classical", trackers="pyin", separate="on"),
                              cache=tmp_path / "cache")
    assert "separation_unavailable" in codes(ir)
    assert len(ir.events) == len(STEPS) and validate_ir(ir) == []
    assert logs(ir)["pitch"].params["source"] == "mix"


def test_separated_stems_feed_pitch_and_rhythm(tmp_path, monkeypatch):
    from orin.separate import demucs as sep

    contour = synth.concat(synth.silence(LEAD_S), synth.phrase(STEPS, [0.4] * 8 + [1.0], gap_s=0.04), synth.silence(0.3))
    melody = synth.render(contour, TONIC_HZ, sr=44100)
    seconds = len(melody) / 44100
    drums = synth.clicks(np.arange(0.5, seconds, 0.44), seconds, sr=44100)
    # a moving line a tritone off the melody's notes: transcribed with it, the notes would be wrong
    backing = synth.render(synth.phrase([600, 100, 800, 300, 600, 100, 800, 300], 0.6, gap_s=0.03), TONIC_HZ / 2,
                           sr=44100, amp=0.5)
    synth.write_wav(str(tmp_path / "band.wav"), synth.mix(melody, drums, backing), sr=44100)

    def fake(audio_44k, sr, **kwargs):
        assert sr == 44100 and audio_44k.shape[0] == 2
        stereo = lambda x: np.stack([x, x]).astype(np.float32)  # noqa: E731
        return {"vocals": stereo(melody), "drums": stereo(drums), "other": stereo(backing),
                "bass": stereo(np.zeros_like(melody))}, None

    monkeypatch.setattr(sep, "separate_with_reason", fake)
    ir = pipeline.audio_to_ir(tmp_path / "band.wav", AnalysisOptions(style="western_classical", trackers="pyin", separate="on"),
                              cache=tmp_path / "cache")
    assert logs(ir)["pitch"].params["source"] == "vocals"
    assert logs(ir)["rhythm"].params["pulse_source"] == "drums"
    assert "separation_unavailable" not in codes(ir)
    assert len(ir.events) == len(STEPS)
    assert all(abs(e.pitch_cents - expected) < 15.0 for e, expected in zip(ir.events, STEPS))
    assert ir.events[0].onset_s == pytest.approx(LEAD_S, abs=0.05)  # stem times are the file's times too
    other = pipeline.audio_to_ir(tmp_path / "band.wav", AnalysisOptions(style="western_classical", trackers="pyin",
                                                                      separate="on", melody_stem="other"),
                                 cache=tmp_path / "cache")
    assert logs(other)["pitch"].params["source"] == "other" and not logs(other)["pitch"].cache_hit
    assert other.meta.analysis.user_overrides["melody_stem"] == "other"
