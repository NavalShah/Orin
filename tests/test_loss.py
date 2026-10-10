"""The fidelity score and the loss report's sentences (spec 9.3)."""
from __future__ import annotations

import pytest

from orin.loss import (
    HOW_SCORED,
    LOSS_CODES,
    NOTICE_CODES,
    TEMPLATES,
    WEIGHTS,
    LossItem,
    LossReport,
    LossSummary,
    NoteLoss,
    Notice,
    base_key,
    explain_key,
    human_text,
    score,
)
from orin.loss.fidelity import ornament_loss, pitch_loss, timing_loss


def total_penalty(result) -> float:
    return sum(p.pitch + p.ornament + p.timing for p in result.penalties.values()) + result.free_time_penalty


# --- the three per-note losses ---------------------------------------------

@pytest.mark.parametrize("err, expected", [
    (0.0, 0.0), (14.0, 0.0), (25.0, 0.0),  # every just-versus-equal difference is free
    (30.0, 0.2), (37.5, 0.5), (50.0, 1.0), (80.0, 1.0), (-50.0, 1.0),
])
def test_pitch_loss(err, expected):
    assert pitch_loss(NoteLoss("n", 1.0, err_cents=err)) == pytest.approx(expected)


def test_octave_fold_costs_half_whatever_the_error():
    assert pitch_loss(NoteLoss("n", 1.0, err_cents=0.0, octave_folded=True)) == 0.5
    assert pitch_loss(NoteLoss("n", 1.0, err_cents=80.0, octave_folded=True)) == 0.5


@pytest.mark.parametrize("half, coverage, k, expected", [
    (80.0, 1.0, 1.0, 0.8),  # dropped, covering the whole note
    (80.0, 1.0, 0.5, 0.4),  # approximated: half
    (80.0, 0.5, 1.0, 0.4),
    (5.0, 1.0, 1.0, 0.1),  # any dropped movement costs at least a tenth
    (300.0, 1.0, 1.0, 1.0),
    (300.0, 2.0, 1.0, 1.0),  # coverage is a share: capped at 1
    (80.0, 1.0, 0.0, 0.0),  # kept
    (80.0, 0.0, 1.0, 0.0),  # no ornament
])
def test_ornament_loss(half, coverage, k, expected):
    n = NoteLoss("n", 1.0, orn_half_extent=half, orn_coverage=coverage, orn_k=k)
    assert ornament_loss(n) == pytest.approx(expected)


@pytest.mark.parametrize("shift, grid, expected", [
    (0.0, 0.25, 0.0), (0.05, 0.25, 0.4), (-0.05, 0.25, 0.4), (0.125, 0.25, 1.0), (0.5, 0.25, 1.0),
    (0.05, None, 0.0), (0.05, 0.0, 0.0),  # no grid, no timing loss
])
def test_timing_loss(shift, grid, expected):
    assert timing_loss(NoteLoss("n", 1.0, onset_shift_s=shift, grid_s=grid)) == pytest.approx(expected)


# --- the score ---------------------------------------------------------------

def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)
    assert WEIGHTS == {"pitch": 0.45, "ornament": 0.35, "timing": 0.20}


def test_nothing_lost_scores_one():
    result = score([NoteLoss("a", 1.0), NoteLoss("b", 2.0, err_cents=20.0, grid_s=0.25)])
    assert (result.pitch, result.ornament, result.timing, result.overall) == (1.0, 1.0, 1.0, 1.0)
    assert total_penalty(result) == 0.0


def test_no_notes_or_no_duration_scores_one():
    for notes in ([], [NoteLoss("a", 0.0, err_cents=50.0)], [NoteLoss("a", -3.0, err_cents=50.0)]):
        result = score(notes)
        assert result.overall == 1.0 and result.penalties == {} and result.free_time_penalty == 0.0


def test_hand_computed_two_note_case():
    # Two notes of one second each.
    #   a: rounded by a full quarter tone            -> pitch loss 1.0 on half the time
    #   b: an 80-cent oscillation dropped over half the note, and a 50 ms shift on a 250 ms grid
    #      -> ornament loss 0.8 * 0.5 = 0.4, timing loss 0.05 / 0.125 = 0.4, each on half the time
    result = score([
        NoteLoss("a", 1.0, err_cents=50.0),
        NoteLoss("b", 1.0, orn_half_extent=80.0, orn_coverage=0.5, orn_k=1.0, onset_shift_s=0.05, grid_s=0.25),
    ])
    assert result.pitch == pytest.approx(0.5)
    assert result.ornament == pytest.approx(0.8)
    assert result.timing == pytest.approx(0.8)
    assert result.overall == pytest.approx(0.45 * 0.5 + 0.35 * 0.8 + 0.20 * 0.8)  # 0.665
    assert result.overall == pytest.approx(0.665)
    a, b = result.penalties["a"], result.penalties["b"]
    assert (a.pitch, a.ornament, a.timing) == pytest.approx((0.225, 0.0, 0.0))
    assert (b.pitch, b.ornament, b.timing) == pytest.approx((0.0, 0.07, 0.04))


def test_losses_are_weighted_by_note_duration():
    long_wrong = score([NoteLoss("a", 3.0, err_cents=50.0), NoteLoss("b", 1.0)])
    short_wrong = score([NoteLoss("a", 3.0), NoteLoss("b", 1.0, err_cents=50.0)])
    assert long_wrong.pitch == pytest.approx(0.25) and short_wrong.pitch == pytest.approx(0.75)
    assert long_wrong.overall == pytest.approx(1 - 0.45 * 0.75)
    assert short_wrong.overall == pytest.approx(1 - 0.45 * 0.25)


def test_a_tuning_system_change_alone_costs_nothing():
    # Equal temperament against the nominal just swaras: 14, 16 and 12 cents.
    result = score([NoteLoss(str(i), 1.0, err_cents=e) for i, e in enumerate((0, 4, 14, 2, 2, 16, 12))])
    assert result.pitch == 1.0 and result.overall == 1.0


@pytest.mark.parametrize("notes, free", [
    ([NoteLoss("a", 1.0, err_cents=50.0), NoteLoss("b", 2.5, orn_half_extent=60, orn_coverage=0.7, orn_k=0.5)], False),
    ([NoteLoss("a", 0.3, err_cents=31.0, onset_shift_s=0.04, grid_s=0.125),
      NoteLoss("b", 1.7, octave_folded=True, orn_half_extent=200, orn_coverage=1.0, orn_k=1.0),
      NoteLoss("c", 0.9, onset_shift_s=-0.2, grid_s=0.125)], False),
    ([NoteLoss("a", 1.0), NoteLoss("b", 1.0, onset_shift_s=0.03, grid_s=0.25)], True),
    ([NoteLoss("a", 1.0, onset_shift_s=0.2, grid_s=0.25, err_cents=44.0)], True),
    ([NoteLoss(str(i), 0.1 + 0.07 * i, err_cents=7.0 * i, orn_half_extent=11.0 * i, orn_coverage=0.1 * i,
               orn_k=(0.0, 0.5, 1.0)[i % 3], onset_shift_s=0.01 * i, grid_s=0.2) for i in range(12)], False),
])
def test_additivity(notes, free):
    """overall == 1 - every penalty, so each loss item can show what it cost."""
    result = score(notes, free_time_measured=free)
    assert result.overall == pytest.approx(1.0 - total_penalty(result), abs=1e-9)
    assert result.overall == pytest.approx(
        WEIGHTS["pitch"] * result.pitch + WEIGHTS["ornament"] * result.ornament + WEIGHTS["timing"] * result.timing)
    assert 0.0 <= result.overall <= 1.0


def test_free_time_forced_into_bars_caps_timing_at_one_half():
    perfect = [NoteLoss("a", 1.0, grid_s=0.25), NoteLoss("b", 1.0, grid_s=0.25)]
    assert score(perfect).timing == 1.0
    capped = score(perfect, free_time_measured=True)
    assert capped.timing == 0.5
    assert capped.free_time_penalty == pytest.approx(0.20 * 0.5)
    assert capped.overall == pytest.approx(0.9)
    assert (capped.pitch, capped.ornament) == (1.0, 1.0)

    # Partly lost already: only the rest of the way down to one half is added.
    shifted = [NoteLoss("a", 1.0, onset_shift_s=0.05, grid_s=0.25), NoteLoss("b", 1.0, grid_s=0.25)]
    assert score(shifted).timing == pytest.approx(0.8)
    part = score(shifted, free_time_measured=True)
    assert part.timing == 0.5 and part.free_time_penalty == pytest.approx(0.20 * 0.3)
    assert part.overall == pytest.approx(1 - total_penalty(part))

    # Already under one half: the cap adds nothing.
    bad = [NoteLoss("a", 1.0, onset_shift_s=0.125, grid_s=0.25)]
    worse = score(bad, free_time_measured=True)
    assert worse.timing == 0.0 and worse.free_time_penalty == 0.0


def test_how_scored_names_the_parts_and_admits_the_weights_are_a_choice():
    for word in ("Pitch (45%)", "Ornament (35%)", "Timing (20%)", "one half", "not a measurement"):
        assert word in HOW_SCORED


# --- sentences ---------------------------------------------------------------

def test_every_code_has_a_sentence():
    assert set(LOSS_CODES).isdisjoint(NOTICE_CODES)
    assert set(TEMPLATES) == set(LOSS_CODES) | set(NOTICE_CODES)
    assert len(LOSS_CODES) == 12 and len(NOTICE_CODES) == 6
    for text in TEMPLATES.values():
        assert (text[0].isupper() or text[0] == "{") and text.endswith(".")


def test_human_text_fills_in_the_numbers():
    below = human_text("MICROTONE_ROUNDED", cents_delta=50.0, target="Western (staff notation)", written="E")
    assert below == ("This note was performed about 50 cents below the nearest pitch Western (staff notation) "
                     "can write, so it is shown as E; the in-between pitch is lost.")
    above = human_text("MICROTONE_ROUNDED", cents_delta=-34.4, target="Carnatic", written="G2")
    assert "about 34 cents above" in above and "shown as G2" in above
    assert human_text("TIMING_SHIFT", shift_ms=59.6, grid="4/4") == "This note's start was moved by 60 ms to fit the 4/4 grid."
    assert "+200 cents" in human_text("TRANSPOSED_FOR_NOTATION", mode="Maqam Rast", written_tonic="C", interval_cents=200.0)
    assert "-37 cents" in human_text("TUNING_OFFSET", offset_cents=-37.2)
    assert human_text("DYNAMICS_DROPPED") == TEMPLATES["DYNAMICS_DROPPED"]


def test_human_text_mentions_the_rate_only_when_known():
    with_rate = human_text("ORNAMENT_DROPPED", amplitude_cents=160.0, rate_hz=5.0, target="Rock")
    assert "(about 160 cents wide, 5 times a second)" in with_rate
    for missing in ({}, {"rate_hz": None}, {"rate_hz": 0}):
        assert "(about 160 cents wide)" in human_text("ORNAMENT_DROPPED", amplitude_cents=160.0, target="Rock", **missing)


def test_human_text_leaves_unknown_fields_visible():
    """A missing number is shown as a hole, never invented."""
    text = human_text("MICROTONE_ROUNDED", cents_delta=50.0)
    assert "{target}" in text and "{written}" in text and "50 cents below" in text
    assert "{count}" in human_text("TEMPERAMENT_RETUNED", target="Carnatic")
    assert "{mode}" in human_text("OUT_OF_MODE_NOTE")
    assert "{tonic}" in human_text("TONIC_ASSUMED")
    # A value of the wrong type does not raise either.
    assert human_text("TIMING_SHIFT", shift_ms="soon", grid="4/4")


def test_human_text_for_an_unknown_code_is_still_a_sentence():
    assert human_text("SOMETHING_NEW", x=1) == "Something new."


def test_explain_keys():
    assert explain_key("MICROTONE_ROUNDED") == "microtone_rounded"
    assert explain_key("MICROTONE_ROUNDED", "12tet") == "microtone_rounded.12tet"
    assert explain_key("ORNAMENT_DROPPED", "Oscillation") == "ornament_dropped.oscillation"
    assert explain_key("TONIC_ASSUMED", None) == "tonic_assumed"
    assert base_key("ornament_dropped.oscillation") == "ornament_dropped"
    assert base_key("tonic_assumed") == "tonic_assumed"


# --- the report --------------------------------------------------------------

def item(code="MICROTONE_ROUNDED", severity="major", **kw) -> LossItem:
    return LossItem(code=code, severity=severity, human_text=kw.pop("text", "Something was lost."),
                    explain_key=explain_key(code), **kw)


def test_report_to_text():
    report = LossReport(
        items=(item(event_id="e3", time_s=1.5, text="A quarter tone was rounded.", penalty=0.1),
               item("TEMPERAMENT_RETUNED", "info", event_ids=("e1", "e2"), text="Three notes shift.")),
        notices=(Notice(code="TONIC_ASSUMED", human_text="D3 was assumed.", explain_key="tonic_assumed"),
                 Notice(code="OUT_OF_MODE_NOTE", event_id="e4", time_s=2.25, human_text="Written as played.",
                        explain_key="out_of_mode_note")),
        summary=LossSummary(counts_by_code={"MICROTONE_ROUNDED": 1, "TEMPERAMENT_RETUNED": 1}, max_cents_delta=50.0,
                            fidelity_score=0.9, pitch_score=0.78, ornament_score=1.0, timing_score=1.0),
        source_format="arabic_maqam", target_format="western_classical")
    assert report.to_text() == (
        "Loss report: arabic_maqam -> western_classical\n"
        "Fidelity 0.90  (pitch 0.78, ornament 1.00, timing 1.00)\n"
        "Largest pitch change: 50 cents\n"
        "\n"
        "[major] MICROTONE_ROUNDED at 1.50 s: A quarter tone was rounded.\n"
        "[info] TEMPERAMENT_RETUNED: Three notes shift.\n"
        "\n"
        "Notes about the analysis (these do not count as loss):\n"
        "- TONIC_ASSUMED: D3 was assumed.\n"
        "- OUT_OF_MODE_NOTE at 2.25 s: Written as played.\n")


def test_empty_report_says_nothing_was_lost():
    text = LossReport(target_format="carnatic").to_text()
    assert text.startswith("Loss report: unknown -> carnatic\n")
    assert "Fidelity 1.00" in text and "Nothing was lost in this conversion." in text
    assert "do not count as loss" not in text


def test_report_round_trips_through_json():
    report = LossReport(items=(item(event_id="e1", time_s=0.0, pulse=3.0, cycle_index=2,
                                    detail={"cents_delta": -50.0, "notes": [{"event_id": "e1"}]}),),
                        target_format="rock")
    assert LossReport.model_validate_json(report.model_dump_json()) == report
    assert report.items[0].penalty == 0.0 and report.summary.fidelity_score == 1.0


def test_severity_is_one_of_three():
    with pytest.raises(ValueError):
        item(severity="catastrophic")
