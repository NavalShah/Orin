"""Evaluation metrics on hand-built inputs, the real-clip manifest, and the synthetic
regression thresholds (spec 7.15)."""
import json

import pytest

from orin.eval import metrics, run, synth


# --- metrics --------------------------------------------------------------------------

def test_tonic_accuracy_is_octave_folded_within_50_cents():
    assert metrics.tonic_correct(146.83, 146.83)
    assert metrics.tonic_correct(293.66, 146.83)  # an octave out is still the right pitch class
    assert metrics.tonic_correct(146.83 * 2 ** (40 / 1200), 146.83)
    assert not metrics.tonic_correct(146.83 * 2 ** (60 / 1200), 146.83)
    assert not metrics.tonic_correct(220.0, 146.83)  # the fifth is the classic wrong answer
    assert metrics.tonic_error_cents(220.25, 146.83) == pytest.approx(498.0, abs=1.0)
    assert metrics.tonic_accuracy([(146.83, 146.83), (220.0, 146.83), (73.4, 146.83), (150.0, 146.83)]) == 0.75
    assert metrics.tonic_accuracy([]) == 0.0


def test_onset_f1():
    ref = [0.0, 0.5, 1.0, 1.5]
    assert metrics.onset_f1(ref, [0.01, 0.52, 0.98, 1.5]) == 1.0
    assert metrics.onset_f1(ref, [0.0, 0.5]) == pytest.approx(2 * 1.0 * 0.5 / 1.5)  # precision 1, recall 0.5
    assert metrics.onset_f1(ref, [0.2, 0.7, 1.2, 1.7]) == 0.0  # 200 ms late is a miss
    assert metrics.onset_f1(ref, []) == 0.0 and metrics.onset_f1([], []) == 1.0


def test_note_f1_needs_onset_and_pitch():
    ref = [(0.0, 0.4, 220.0), (0.5, 0.9, 246.94), (1.0, 1.4, 261.63)]
    assert metrics.note_f1(ref, ref) == 1.0
    detuned = [(0.0, 0.4, 220.0 * 2 ** (30 / 1200)), (0.5, 0.9, 246.94), (1.0, 1.4, 261.63)]
    assert metrics.note_f1(ref, detuned) == 1.0  # 30 cents is inside the 50-cent tolerance
    wrong_pitch = [(0.0, 0.4, 233.08), (0.5, 0.9, 246.94), (1.0, 1.4, 261.63)]
    assert metrics.note_f1(ref, wrong_pitch) == pytest.approx(2 / 3)
    late = [(0.2, 0.6, 220.0), (0.5, 0.9, 246.94), (1.0, 1.4, 261.63)]
    assert metrics.note_f1(ref, late) == pytest.approx(2 / 3)
    short = [(0.0, 0.1, 220.0), (0.5, 0.6, 246.94), (1.0, 1.1, 261.63)]
    assert metrics.note_f1(ref, short) == 1.0  # offsets are not scored
    assert metrics.note_f1(ref, []) == 0.0


def test_median_pitch_error_matches_notes_by_onset():
    ref = [(0.0, 0.4, 220.0), (0.5, 0.9, 246.94), (1.0, 1.4, 261.63)]
    est = [(0.0, 0.4, 220.0 * 2 ** (10 / 1200)), (0.5, 0.9, 246.94 * 2 ** (-20 / 1200)),
           (1.0, 1.4, 261.63 * 2 ** (400 / 1200))]
    assert metrics.median_pitch_error_cents(ref, est) == pytest.approx(20.0, abs=0.01)
    assert metrics.median_pitch_error_cents(ref, [(5.0, 5.4, 220.0)]) is None
    assert metrics.median_pitch_error_cents([], est) is None


def test_mode_top_k_cycle_and_style_scores():
    ranked = [["mohanam", "hamsadhwani"], ["kalyani", "shankarabharanam", "mohanam"], ["rast"], []]
    truth = ["mohanam", "mohanam", "bayati", "major"]
    assert metrics.topk_accuracy(ranked, truth, 1) == 0.25
    assert metrics.topk_accuracy(ranked, truth, 3) == 0.5
    assert metrics.cycle_accuracy([32, 12, None, 16], [32, 16, None, None]) == 0.5
    truth_styles = ["carnatic", "carnatic", "western_pop", "arabic_maqam"]
    predicted = ["carnatic", "arabic_maqam", "rock", None]
    assert metrics.accuracy(truth_styles, predicted) == 0.25
    assert metrics.confusion_matrix(truth_styles, predicted) == {
        "carnatic": {"carnatic": 1, "arabic_maqam": 1}, "western_pop": {"rock": 1}, "arabic_maqam": {"none": 1}}


def test_summarise_skips_what_has_no_ground_truth():
    rows = [
        {"tonic_correct": True, "onset_f1": 1.0, "note_f1": 0.8, "pitch_error_cents": 4.0, "mode_top1": True,
         "mode_top3": True, "cycle_correct": None, "style": "carnatic", "detected_style": "carnatic"},
        {"tonic_correct": False, "onset_f1": None, "note_f1": None, "pitch_error_cents": None, "mode_top1": None,
         "mode_top3": None, "cycle_correct": True, "style": "rock", "detected_style": "western_pop"},
    ]
    out = run.summarise(rows)
    assert out["n"] == 2 and out["tonic_accuracy"] == 0.5
    assert out["onset_f1"] == 1.0 and out["note_f1"] == 0.8 and out["median_pitch_error_cents"] == 4.0
    assert out["mode_top1"] == 1.0 and out["cycle_accuracy"] == 1.0 and out["style_accuracy"] == 0.5
    assert out["style_confusion"] == {"carnatic": {"carnatic": 1}, "rock": {"western_pop": 1}}
    assert run.summarise([])["tonic_accuracy"] is None


# --- cases and the manifest ---------------------------------------------------------------

def test_synthetic_cases_carry_their_ground_truth():
    assert len({case.id for case in run.SYNTH_CASES}) == len(run.SYNTH_CASES) >= 5
    assert {"western_classical", "carnatic", "arabic_maqam"} <= {case.style for case in run.SYNTH_CASES}
    case = next(c for c in run.SYNTH_CASES if c.id == "carnatic_mohanam_drone")
    y, truth = case.render()
    assert len(truth.notes) == len(case.notes) and truth.tonic_hz == case.tonic_hz
    assert truth.notes[0][0] == pytest.approx(run.LEAD_S) and truth.notes[0][2] == pytest.approx(case.tonic_hz)
    assert all(b > a for a, b, _ in truth.notes)
    assert all(nxt[0] == pytest.approx(prev[1] + run.GAP_S) for prev, nxt in zip(truth.notes, truth.notes[1:]))
    assert len(y) / synth.SR == pytest.approx(truth.notes[-1][1] + run.LEAD_S, abs=0.02)


def test_shipped_manifest_is_an_empty_list_and_runs():
    assert json.loads(run.DEFAULT_MANIFEST.read_text()) == []
    out = run.run_real()
    assert out["clips"] == 0 and out["evaluated"] == 0 and out["cases"] == [] and out["tonic_accuracy"] is None


def test_manifest_clips_need_a_licence_a_provenance_and_a_file(tmp_path):
    manifest = tmp_path / "clips.json"
    manifest.write_text(json.dumps([
        {"path": "a.wav", "style": "carnatic", "tonic_hz": 146.83, "provenance": "self-recorded"},
        {"path": "gone.wav", "licence": "CC0", "provenance": "self-recorded", "style": "carnatic", "tonic_hz": 146.83},
    ]))
    out = run.run_real(manifest)
    assert out["clips"] == 2 and out["evaluated"] == 0
    assert out["skipped"] == [{"path": "a.wav", "reason": "missing licence"},
                              {"path": "gone.wav", "reason": "file not found"}]
    assert run.load_manifest(manifest)[0]["path"] == "a.wav"
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"clips": []}))
    assert run.load_manifest(wrapped) == []


# --- the pipeline against ground truth -------------------------------------------------------

def _need_pipeline():
    for needed in ("orin.segment.notes", "orin.segment.ornaments", "orin.rhythm", "orin.mode.infer",
                   "orin.detect.score"):
        pytest.importorskip(needed)


def test_run_real_scores_a_clip_against_its_manifest_entry(tmp_path):
    _need_pipeline()
    case = run.SynthCase("clip", "western_classical", 220.0, run._steps([0, 200, 400, 200, 0], 0.4, 0.8), mode_id="major")
    y, truth = case.render()
    synth.write_wav(str(tmp_path / "clip.wav"), y)
    manifest = tmp_path / "clips.json"
    manifest.write_text(json.dumps([{
        "path": "clip.wav", "licence": "CC0 (synthesised for this test)", "provenance": "orin.eval.synth",
        "style": "western_classical", "tonic_hz": 220.0, "notes": [list(n) for n in truth.notes], "cycle_pulses": None,
    }]))
    out = run.run_real(manifest, trackers="pyin", cache=tmp_path / "cache")
    assert out["evaluated"] == 1 and out["skipped"] == []
    row = out["cases"][0]
    assert row["licence"].startswith("CC0") and row["provenance"] == "orin.eval.synth"
    assert row["tonic_correct"] and row["note_f1"] == 1.0 and row["pitch_error_cents"] < 10.0
    assert row["mode_top1"] is None  # no reference mode in the manifest, so none is scored
    assert row["cycle_correct"] in (True, False)  # a reference cycle (free time) was given


# Floors a little under what the pipeline scores today (pYIN: tonic 1.0, onset and note F1 1.0,
# median pitch error under 1 cent, mode top-1 1.0). A change that drops below them is a regression.
THRESHOLDS = {"tonic_accuracy": 1.0, "onset_f1": 0.95, "note_f1": 0.95, "mode_top1": 0.8, "mode_top3": 1.0}
MAX_MEDIAN_PITCH_ERROR_CENTS = 8.0


def test_synthetic_regression_thresholds():
    _need_pipeline()
    out = run.run_synthetic(trackers="pyin")
    assert out["n"] == len(run.SYNTH_CASES)
    summary = {k: v for k, v in out.items() if k != "cases"}
    for key, floor in THRESHOLDS.items():
        assert out[key] is not None and out[key] >= floor, (key, summary)
    assert out["median_pitch_error_cents"] <= MAX_MEDIAN_PITCH_ERROR_CENTS, summary
    for row in out["cases"]:
        assert row["tonic_error_cents"] < 15.0, row
        assert row["note_f1"] >= 0.85, row
        assert not [code for code in row["warnings"] if code.endswith("_failed")], row
    by_id = {row["id"]: row for row in out["cases"]}
    assert by_id["carnatic_mohanam_drone"]["tonic_method"] == "drone"
    # Style auto-detect is reported, with its confusion matrix, but only loosely gated: on
    # five short single-line clips, classical against pop is not decidable (spec 8.3).
    assert out["style_accuracy"] is not None and out["style_accuracy"] >= 0.4, out["style_confusion"]
    assert sum(sum(r.values()) for r in out["style_confusion"].values()) == len(run.SYNTH_CASES)
