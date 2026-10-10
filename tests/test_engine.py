"""The generic conversion engine (spec 5 and 9): quantise, snap, drop or approximate, and say so."""
from __future__ import annotations

import math

import pytest

from orin.eval import fixtures as fx
from orin.eval.fixtures import glide, grace, make_ir, oscillation, vibrato
from orin.formats import registry
from orin.formats.base import ConversionResult, ConvertOptions
from orin.formats.engine import convert_ir
from orin.ir.models import IR, Cycle, NoteEvent, Source, Tonic
from orin.loss import LOSS_CODES, NOTICE_CODES

FORMATS = ("western_classical", "carnatic", "western_pop", "rock", "arabic_maqam", "persian_dastgah")


def conv(ir: IR, target: str, **opts) -> ConversionResult:
    result = convert_ir(ir, registry.get(target).META, ConvertOptions(**opts) if opts else None)
    assert not any("could not be completed" in w for w in result.score.warnings), result.score.warnings
    return result


def items(result: ConversionResult, code: str, *, above_info: bool = False):
    return [i for i in result.loss.items if i.code == code and not (above_info and i.severity == "info")]


def notices(result: ConversionResult, code: str):
    return [n for n in result.loss.notices if n.code == code]


def items_about(result: ConversionResult, event_id: str):
    return [i for i in result.loss.items if i.event_id == event_id or event_id in i.event_ids]


def assert_additive(result: ConversionResult) -> None:
    total = sum(i.penalty for i in result.loss.items)
    assert result.loss.summary.fidelity_score == pytest.approx(1.0 - total, abs=1e-6)
    assert all(i.penalty >= 0 for i in result.loss.items)


def note(result: ConversionResult, event_id: str):
    return next(n for n in result.score.notes if n.source_event_id == event_id)


# ---------------------------------------------------------------------------
# Pitch
# ---------------------------------------------------------------------------

NEUTRAL = dict(source_format="arabic_maqam")  # 150 and 350 cents: the half-flat 2nd and 3rd
ADI = dict(source_format="carnatic", pulse_s=0.125, subdivision=4, cycle_pulses=32, template_id="adi")


def test_neutral_degrees_are_rounded_on_a_staff():
    result = conv(make_ir([0, 150, 350, 500, 700], **NEUTRAL), "western_classical")
    rounded = items(result, "MICROTONE_ROUNDED")
    assert {i.event_id for i in rounded} == {"e2", "e3"}
    for i in rounded:
        assert 40 <= abs(i.detail["cents_delta"]) <= 60
        assert i.severity == "major"
        assert i.detail["ambiguous"] is True
        assert i.detail["written_cents"] - i.detail["source_cents"] == pytest.approx(i.detail["cents_delta"])
        assert i.explain_key == "microtone_rounded.12tet"
        assert i.penalty > 0
        assert "{" not in i.human_text
    assert result.loss.summary.max_cents_delta == pytest.approx(50.0)
    assert result.loss.summary.pitch_score < 0.7
    assert_additive(result)


def test_neutral_degrees_survive_in_maqam_notation():
    result = conv(make_ir([0, 150, 350, 500, 700], **NEUTRAL), "arabic_maqam")
    assert items(result, "MICROTONE_ROUNDED") == []
    assert result.loss.summary.pitch_score >= 0.98
    assert [n.name for n in result.score.notes] == ["1", "2-", "3-", "4", "5"]
    assert [n.cents_delta for n in result.score.notes] == [0, 0, 0, 0, 0]


def test_neutral_degrees_are_a_major_loss_in_carnatic():
    result = conv(make_ir([0, 150, 350, 500, 700], **NEUTRAL), "carnatic")
    rounded = items(result, "MICROTONE_ROUNDED", above_info=True)
    assert {i.event_id for i in rounded} == {"e2", "e3"}
    assert all(i.severity == "major" for i in rounded)
    assert all(i.explain_key == "microtone_rounded.just_intonation_srutis" for i in rounded)
    assert [n.name for n in result.score.notes] == ["S", "R1", "G2", "M1", "P"]
    # A half-flat third is as far from G2 as from G3; a half-flat second leans to R1.
    by_event = {i.event_id: i for i in rounded}
    assert by_event["e3"].detail["ambiguous"] is True
    assert by_event["e2"].detail["ambiguous"] is False
    assert result.loss.summary.pitch_score < 1.0
    assert_additive(result)


def test_typed_major_scale_to_carnatic_is_a_retuning_not_a_rounding():
    ir = make_ir([0, 200, 400, 500, 700, 900, 1100], source_format="western_classical")
    result = conv(ir, "carnatic")
    assert items(result, "MICROTONE_ROUNDED", above_info=True) == []
    retuned = items(result, "TEMPERAMENT_RETUNED")
    assert len(retuned) == 1
    assert retuned[0].severity == "info" and retuned[0].penalty == 0
    assert retuned[0].event_ids == ("e3", "e6", "e7")  # 400->386, 900->884, 1100->1088
    assert retuned[0].detail["count"] == 3 and retuned[0].detail["max_cents"] == pytest.approx(16.0)
    assert retuned[0].detail["notes"][0] == {"event_id": "e3", "cents_delta": -14.0}
    missing = items(result, "ORNAMENT_NOT_SUPPLIED")
    assert len(missing) == 1 and missing[0].penalty == 0 and missing[0].severity == "info"
    assert result.loss.summary.pitch_score == 1.0
    assert [n.name for n in result.score.notes] == ["S", "R2", "G3", "M1", "P", "D2", "N3"]
    assert_additive(result)


def test_just_intonation_swaras_on_a_staff_are_one_grouped_item():
    result = conv(make_ir([0, 204, 386, 702, 884], source_format="carnatic"), "western_classical")
    assert items(result, "MICROTONE_ROUNDED") == []
    assert len(items(result, "TEMPERAMENT_RETUNED")) == 1
    assert [n.cents for n in result.score.notes] == [0, 200, 400, 700, 900]
    assert result.loss.summary.pitch_score == 1.0


def test_small_moves_inside_one_tuning_system_are_grouped_as_info():
    result = conv(make_ir([0, 210, 386], source_format="carnatic"), "carnatic")
    assert items(result, "TEMPERAMENT_RETUNED") == []
    grouped = items(result, "MICROTONE_ROUNDED")
    assert len(grouped) == 1 and grouped[0].severity == "info"
    assert grouped[0].event_ids == ("e2",) and grouped[0].detail["count"] == 1
    assert grouped[0].penalty == 0


def test_loss_threshold_is_configurable():
    ir = make_ir([0, 204], source_format="carnatic")
    assert items(conv(ir, "western_classical"), "TEMPERAMENT_RETUNED") == []  # 4 cents, under the default 5
    result = conv(ir, "western_classical", loss_threshold_cents=2.0)
    assert items(result, "TEMPERAMENT_RETUNED")[0].event_ids == ("e2",)
    assert result.score.options.loss_threshold_cents == 2.0


def test_anchor_inside_a_swarasthana_window_is_that_swara_with_no_item():
    ir = make_ir([0, 218, 372, 702], mode_id="mohanam", **ADI)  # 14 cents off R2 and G3
    result = conv(ir, "carnatic")
    assert result.score.options.mode_id == "mohanam"
    assert [(n.name, n.cents) for n in result.score.notes] == [("S", 0), ("R2", 204), ("G3", 386), ("P", 702)]
    assert note(result, "e2").cents_delta == pytest.approx(-14.0)
    assert items_about(result, "e2") == [] and items_about(result, "e3") == []
    assert result.loss.items == ()
    assert result.loss.notices == ()


def test_a_chosen_raga_names_the_degrees():
    ir = make_ir([0, 400, 700], source_format="western_classical")
    result = conv(ir, "carnatic", mode_id="mohanam")
    assert [n.name for n in result.score.notes] == ["S", "G3", "P"]
    assert items_about(result, "e2") == []
    assert result.score.options.mode_label == "Mohanam"


def test_out_of_mode_note_stays_where_it_was_played():
    ir = make_ir([0, 204, 498, 702], mode_id="mohanam", **ADI)  # Mohanam has no Ma
    result = conv(ir, "carnatic")
    ma = note(result, "e3")
    assert (ma.name, ma.cents, ma.cents_delta, ma.in_mode) == ("M1", 498, 0, False)
    out = notices(result, "OUT_OF_MODE_NOTE")
    assert [n.event_id for n in out] == ["e3"]
    assert "Mohanam" in out[0].human_text and "{" not in out[0].human_text
    assert result.loss.items == ()  # a notice, not a loss
    assert all(n.in_mode for n in result.score.notes if n.source_event_id != "e3")


def test_wide_oscillation_near_a_mode_degree_is_assigned_with_a_notice():
    plain = make_ir([0, 204, 346, 702], source_format="carnatic", mode_id="mohanam")
    shaky = make_ir([0, 204, 346, 702], source_format="carnatic", mode_id="mohanam",
                    ornaments={2: oscillation(80.0)})
    # Steady, 40 cents under Ga: it is nearer G2, which Mohanam does not have.
    steady = conv(plain, "carnatic")
    assert note(steady, "e3").name == "G2" and not note(steady, "e3").in_mode
    assert notices(steady, "PITCH_ANCHOR_UNCERTAIN") == []
    # Oscillating 80 cents either way: the anchor cannot be trusted, so it goes to the raga's Ga.
    result = conv(shaky, "carnatic")
    assert (note(result, "e3").name, note(result, "e3").cents, note(result, "e3").in_mode) == ("G3", 386, True)
    uncertain = notices(result, "PITCH_ANCHOR_UNCERTAIN")
    assert [n.event_id for n in uncertain] == ["e3"]
    assert "G3" in uncertain[0].human_text and "Mohanam" in uncertain[0].human_text
    assert items(result, "MICROTONE_ROUNDED") == []
    assert notices(result, "OUT_OF_MODE_NOTE") == []


def test_saba_reaching_above_its_octave_is_not_rounded():
    # Saba does not repeat at the octave: the note at 1100 is written as played.
    ir = make_ir([0, 150, 300, 400, 700, 800, 1000, 1100], source_format="arabic_maqam")
    result = conv(ir, "arabic_maqam", mode_id="saba")
    top = note(result, "e8")
    assert (top.cents, top.cents_delta, top.in_mode) == (1100, 0, False)
    assert [n.event_id for n in notices(result, "OUT_OF_MODE_NOTE")] == ["e8"]
    assert items(result, "MICROTONE_ROUNDED") == []
    assert result.loss.summary.pitch_score == 1.0
    assert note(result, "e4").name == "b4"  # 400 cents is Saba's lowered fourth, not a third


def test_octaves_are_kept_and_named_from_the_tonic():
    result = conv(make_ir([-100, 0, 1190, 1200, 1300, 2400], source_format="western_classical"), "western_classical")
    assert [n.octave for n in result.score.notes] == [-1, 0, 1, 1, 1, 2]
    assert [n.cents for n in result.score.notes] == [-100, 0, 1200, 1200, 1300, 2400]
    assert [n.name for n in result.score.notes] == ["7", "1", "1", "1", "b2", "1"]
    assert [n.id for n in result.score.notes] == ["n1", "n2", "n3", "n4", "n5", "n6"]


def test_note_far_out_of_range_is_folded_by_octaves():
    result = conv(make_ir([0, 4900, -3000], source_format="western_classical"), "western_classical")
    folded = items(result, "OCTAVE_FOLDED")
    assert [i.event_id for i in folded] == ["e2", "e3"]
    assert all(i.severity == "minor" and i.penalty > 0 for i in folded)
    assert note(result, "e2").cents == 2500 and note(result, "e3").cents == -1800  # the fewest octaves that fit
    assert -2400 <= min(n.cents for n in result.score.notes) and max(n.cents for n in result.score.notes) < 3600
    assert_additive(result)


def test_tonic_override_shifts_every_pitch():
    ir = make_ir([0, 700], tonic_hz=220.0, source_format="western_classical")
    result = conv(ir, "western_classical", tonic_hz=110.0)  # the same sounds, measured from an octave lower
    assert [n.cents for n in result.score.notes] == pytest.approx([1200, 1900])
    assert result.score.options.tonic_hz == 110.0
    assert result.score.options.written_tonic.step == "A" and result.score.options.written_tonic_octave == 2


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------

def test_a_mode_is_never_applied_across_traditions():
    result = conv(fx.western_classical_phrase(), "carnatic")
    assert result.score.options.mode_id is None
    assert [c.id for c in result.suggestions] == ["shankarabharanam"]
    assert result.suggestions[0].label_system == "carnatic"
    assert 0 < result.suggestions[0].score < 1  # a pitch match is never certain
    approx = items(result, "MODE_APPROXIMATED")
    assert len(approx) == 1 and approx[0].severity == "minor" and approx[0].penalty == 0
    assert "Shankarabharanam" in approx[0].human_text
    assert items(result, "MODE_UNREPRESENTABLE") == []


def test_mode_with_no_lookalike_is_unrepresentable():
    result = conv(fx.arabic_phrase(), "western_classical")
    assert result.suggestions == ()
    lost = items(result, "MODE_UNREPRESENTABLE")
    assert len(lost) == 1 and lost[0].severity == "major"  # Rast's half-flats are 50 cents from any key
    assert {d["name"] for d in lost[0].detail["unwritable_degrees"]} == {"3-", "7-"}
    assert items(result, "MODE_APPROXIMATED") == []
    # Minor when every degree can at least be written: Mayamalavagowla on a staff.
    ir = make_ir([0, 112, 386, 498, 702, 814, 1088], source_format="carnatic", mode_id="mayamalavagowla")
    assert items(conv(ir, "western_classical"), "MODE_UNREPRESENTABLE")[0].severity == "minor"


def test_same_format_uses_the_ir_mode_and_reports_no_mode_item():
    result = conv(fx.arabic_phrase(), "arabic_maqam")
    assert result.score.options.mode_id == "rast"
    assert result.suggestions == ()
    assert items(result, "MODE_APPROXIMATED") == [] and items(result, "MODE_UNREPRESENTABLE") == []
    assert [n.name for n in result.score.notes][:7] == ["1", "2", "3-", "4", "5", "6", "7-"]


def test_unknown_mode_id_is_a_warning_not_an_error():
    result = conv(fx.western_classical_phrase(), "carnatic", mode_id="no_such_raga")
    assert result.score.options.mode_id is None
    assert any("no_such_raga" in w for w in result.score.warnings)


# ---------------------------------------------------------------------------
# Ornaments
# ---------------------------------------------------------------------------

def gamaka_ir(**kw) -> IR:
    kw.setdefault("kind", "audio")  # a measured ornament: its width and rate come from a contour
    return make_ir([0, 386, 702], source_format="carnatic", ornaments={1: oscillation(80.0, rate_hz=5.0)}, **kw)


def test_a_typed_ornament_mark_is_not_given_a_width():
    # "G3~" in typed sargam says an oscillation exists, not how wide it is, so the
    # sentence must not quote a size nobody measured.
    result = conv(gamaka_ir(kind="notes"), "western_classical")
    dropped = items(result, "ORNAMENT_DROPPED")
    assert len(dropped) == 1 and dropped[0].detail["declared"] is True
    assert "cents" not in dropped[0].human_text and "oscillation marked on this note" in dropped[0].human_text


def test_wide_oscillation_is_dropped_on_a_staff():
    result = conv(gamaka_ir(), "western_classical")
    dropped = items(result, "ORNAMENT_DROPPED")
    assert len(dropped) == 1 and dropped[0].event_id == "e2"
    assert dropped[0].severity == "major"
    assert dropped[0].explain_key == "ornament_dropped.oscillation"
    assert dropped[0].detail["amplitude_cents"] == 80.0 and dropped[0].detail["width_cents"] == 160.0
    assert "160 cents" in dropped[0].human_text and "5 times a second" in dropped[0].human_text
    assert dropped[0].penalty > 0
    orn = note(result, "e2").ornaments[0]
    assert (orn.kind, orn.action, orn.symbol, orn.staff_mark) == ("oscillation", "drop", None, None)
    assert result.loss.summary.ornament_score < 1.0
    assert_additive(result)


def test_wide_oscillation_is_approximated_in_carnatic():
    result = conv(gamaka_ir(), "carnatic")
    approx = items(result, "ORNAMENT_APPROXIMATED")
    assert len(approx) == 1 and approx[0].severity == "minor"  # approximations are capped at minor
    assert approx[0].explain_key == "ornament_approximated.oscillation"
    assert approx[0].detail["symbol"] == "~"
    orn = note(result, "e2").ornaments[0]
    assert (orn.action, orn.symbol, orn.staff_mark) == ("approximate", "~", "wavy")
    assert orn.local_name == "kampita-like oscillation"
    assert items(result, "ORNAMENT_DROPPED") == []
    # Half the cost of dropping it.
    dropped = conv(gamaka_ir(), "western_classical")
    assert 1 - result.loss.summary.ornament_score == pytest.approx((1 - dropped.loss.summary.ornament_score) / 2)


def test_whole_tone_glide_is_a_bend_in_rock():
    up = conv(make_ir([0, 700], source_format="rock", ornaments={1: glide(200.0, "up")}), "rock")
    orn = note(up, "e2").ornaments[0]
    assert (orn.local_name, orn.staff_mark, orn.symbol, orn.action) == ("bend", "bend", "b", "approximate")
    assert items(up, "ORNAMENT_APPROXIMATED")[0].explain_key == "ornament_approximated.glide"
    down = conv(make_ir([0, 700], source_format="rock", ornaments={1: glide(200.0, "down")}), "rock")
    assert note(down, "e2").ornaments[0].symbol == "r"  # a release
    wide = conv(make_ir([0, 700], source_format="rock", ornaments={1: glide(400.0, "down")}), "rock")
    assert (note(wide, "e2").ornaments[0].local_name, note(wide, "e2").ornaments[0].symbol) == ("slide", "\\")


def test_ornament_severity_follows_its_width():
    def severity(ornament, target="western_classical"):
        result = conv(make_ir([0, 700], source_format="carnatic", ornaments={1: ornament}), target)
        return [i for i in result.loss.items if i.code.startswith("ORNAMENT_")][0].severity

    assert severity(vibrato(20.0)) == "info"
    assert severity(oscillation(45.0)) == "minor"
    assert severity(oscillation(60.0)) == "major"
    assert severity(grace(100.0)) == "minor"  # a grace of 100 cents reaches 50 either side of its middle
    assert severity(glide(300.0)) == "minor"  # wide, but kept as a mark: capped
    assert severity(grace(300.0), "carnatic") == "major"  # Carnatic sargam drops quick touches


def test_tradition_names_only_when_the_style_was_chosen():
    named = conv(fx.carnatic_phrase(), "carnatic")
    assert any(o.local_name for n in named.score.notes for o in n.ornaments)
    assert named.score.options.explicit_style is True
    for target in FORMATS:
        result = conv(fx.carnatic_phrase(), target, explicit_style=False)
        assert all(o.local_name is None for n in result.score.notes for o in n.ornaments)
        assert all("local_name" not in i.detail for i in result.loss.items)
        assert result.score.options.explicit_style is False


def test_two_ornaments_on_one_note_share_its_penalty():
    ir = make_ir([0, 386], durations=[4, 4], source_format="carnatic",
                 ornaments={1: [oscillation(80.0, coverage=1.0), glide(120.0, coverage=0.5)]})
    result = conv(ir, "western_classical")
    lost = [i for i in result.loss.items if i.code.startswith("ORNAMENT_")]
    assert [i.code for i in lost] == ["ORNAMENT_DROPPED", "ORNAMENT_APPROXIMATED"]
    assert len(note(result, "e2").ornaments) == 2
    # The note is charged for its costliest ornament: 0.8 * 1.0 * 1 on half the sounding time.
    assert sum(i.penalty for i in lost) == pytest.approx(0.35 * 0.8 * 0.5)
    assert lost[0].penalty > lost[1].penalty > 0
    assert_additive(result)


def test_plain_notes_into_an_ornamented_idiom_are_flagged_once():
    for target, expected in (("carnatic", 1), ("arabic_maqam", 1), ("persian_dastgah", 1),
                             ("western_pop", 0), ("rock", 0), ("western_classical", 0)):
        assert len(items(conv(fx.western_classical_phrase(), target), "ORNAMENT_NOT_SUPPLIED")) == expected
    plain_carnatic = make_ir([0, 204, 386], source_format="carnatic", mode_id="mohanam")
    assert items(conv(plain_carnatic, "carnatic"), "ORNAMENT_NOT_SUPPLIED") == []  # same format: clean-up
    assert items(conv(fx.rock_phrase(), "carnatic"), "ORNAMENT_NOT_SUPPLIED") == []  # it has ornaments


def test_dynamics_are_reported_once_when_not_written():
    loud = make_ir([0, 200, 400], source_format="western_classical", velocities=[40, 80, 120])
    result = conv(loud, "western_classical")
    dropped = items(result, "DYNAMICS_DROPPED")
    assert len(dropped) == 1 and dropped[0].severity == "info" and dropped[0].penalty == 0
    assert [n.velocity for n in result.score.notes] == [40, 80, 120]  # still there for playback

    by_level = make_ir([0, 200, 400], source_format="western_classical", loudness_db=[-30.0, -20.0, -12.0])
    result = conv(by_level, "western_classical")
    assert len(items(result, "DYNAMICS_DROPPED")) == 1
    velocities = [n.velocity for n in result.score.notes]
    assert velocities == sorted(velocities) and velocities[-1] == 100 and all(1 <= v <= 127 for v in velocities)

    flat = make_ir([0, 200, 400], source_format="western_classical", loudness_db=[-20.0, -18.0, -19.0])
    assert items(conv(flat, "western_classical"), "DYNAMICS_DROPPED") == []
    assert [n.velocity for n in conv(fx.western_classical_phrase(), "western_classical").score.notes] == [80] * 8


# ---------------------------------------------------------------------------
# Rhythm
# ---------------------------------------------------------------------------

def test_onset_off_the_grid_is_a_timing_shift():
    # Notes found in audio at 120 bpm; the third one starts 60 ms late.
    ir = make_ir([0, 200, 400, 500], onsets_s=[0.0, 0.5, 1.06, 1.5], durations=3, pulse_s=0.125, subdivision=4,
                 cycle_pulses=16, template_id="4_4", source_format="western_classical", kind="audio")
    result = conv(ir, "western_classical")
    shifted = items(result, "TIMING_SHIFT")
    assert [i.event_id for i in shifted] == ["e3"]
    assert shifted[0].detail["shift_ms"] == pytest.approx(-60.0, abs=0.5)
    assert "60 ms" in shifted[0].human_text and "4/4" in shifted[0].human_text
    assert shifted[0].penalty > 0 and shifted[0].time_s == 1.06
    assert (shifted[0].pulse, shifted[0].cycle_index) == (8.0, 1)
    assert [n.start_pulse for n in result.score.notes] == [0, 4, 8, 12]
    assert note(result, "e3").onset_s == pytest.approx(1.0) and note(result, "e3").source_onset_s == 1.06
    assert result.loss.summary.timing_score < 1.0
    assert_additive(result)


def test_small_timing_wobble_is_free():
    ir = make_ir([0, 200, 400, 500], onsets_s=[0.0, 0.51, 0.98, 1.5], durations=3, pulse_s=0.125, subdivision=4,
                 cycle_pulses=16, template_id="4_4", source_format="western_classical", kind="audio")
    result = conv(ir, "western_classical")
    assert items(result, "TIMING_SHIFT") == []
    assert result.loss.summary.timing_score == 1.0
    assert_additive(result)


def test_exact_typed_pulses_are_kept_exactly():
    ir = fx.western_pop_phrase()
    result = conv(ir, "western_pop")
    assert items(result, "TIMING_SHIFT") == []
    assert [n.start_pulse for n in result.score.notes] == [0, 3, 6, 8, 11, 14, 16, 22]  # the syncopation survives
    assert [n.duration_pulses for n in result.score.notes] == [3, 3, 2, 3, 3, 2, 6, 10]
    assert [n.onset_s for n in result.score.notes] == pytest.approx([e.onset_s for e in ir.events])
    assert result.score.cycle.id == "4_4" and result.score.phase_pulses == 0
    assert result.score.options.tempo_bpm == pytest.approx(120.0)
    assert (result.score.options.pulse_s, result.score.options.subdivision) == (0.125, 4)
    assert items(result, "CYCLE_REINTERPRETED") == []


def test_pickup_sets_the_phase():
    ir = make_ir([0, 200, 400], durations=4, pulse_s=0.125, subdivision=4, cycle_pulses=16, template_id="4_4",
                 source_format="western_classical")
    shifted = ir.model_copy(update={"events": tuple(
        e.model_copy(update={"onset_pulse": e.onset_pulse + 12, "onset_s": e.onset_s + 1.5, "offset_s": e.offset_s + 1.5})
        for e in ir.events)})
    result = conv(shifted, "western_classical")
    assert [n.start_pulse for n in result.score.notes] == [12, 16, 20]
    assert result.score.phase_pulses == 12


def test_cycle_is_matched_by_length_and_reported():
    western = fx.western_classical_phrase()  # 4 beats
    to_carnatic = conv(western, "carnatic")
    assert to_carnatic.score.cycle.id == "adi"  # 8 beats: two bars to a cycle
    cycle_items = items(to_carnatic, "CYCLE_REINTERPRETED")
    assert len(cycle_items) == 1 and cycle_items[0].severity == "minor" and cycle_items[0].penalty == 0
    assert "Adi tala" in cycle_items[0].human_text
    assert [n.start_pulse for n in to_carnatic.score.notes] == [0, 4, 8, 12, 16, 20, 24, 28]

    three = make_ir([0, 400, 700], durations=4, pulse_s=0.125, subdivision=4, cycle_pulses=12, template_id="3_4",
                    source_format="western_classical")
    assert conv(three, "carnatic").score.cycle.id == "rupakam_3"
    assert conv(three, "western_pop").score.cycle.id == "3_4"
    assert items(conv(three, "western_pop"), "CYCLE_REINTERPRETED") == []  # the very same meter


def test_cycle_with_no_match_falls_back_to_default_or_generic():
    seven = make_ir([0, 204, 386, 702], durations=2, pulse_s=0.2, subdivision=2, cycle_pulses=14,
                    template_id="misra_chapu", source_format="carnatic")
    to_staff = conv(seven, "western_classical")
    assert to_staff.score.cycle.id == "4_4"
    assert items(to_staff, "CYCLE_REINTERPRETED")[0].detail["matched_by"] == "assumed"
    # Arabic has no default cycle (an iqa' is the user's choice): a plain count is shown.
    to_maqam = conv(seven, "arabic_maqam")
    assert (to_maqam.score.cycle.id, to_maqam.score.cycle.label) == ("generic_7", "7-beat cycle")
    assert to_maqam.score.cycle.length_pulses == 14
    assert items(to_maqam, "CYCLE_REINTERPRETED")[0].detail["matched_by"] == "generic"
    assert conv(seven, "carnatic").score.cycle.id == "misra_chapu"
    assert items(conv(seven, "carnatic"), "CYCLE_REINTERPRETED") == []


def test_an_iqa_is_never_named_from_cycle_length_alone():
    # Maqsum, Baladi and Wahda are all four beats long and differ only in drum strokes,
    # so a four-beat melody gets a plain count unless the user picks the iqa'.
    western = fx.western_classical_phrase()  # 4 beats
    to_maqam = conv(western, "arabic_maqam")
    assert to_maqam.score.cycle.id == "generic_4"
    assert items(to_maqam, "CYCLE_REINTERPRETED")[0].detail["matched_by"] == "generic"
    assert conv(western, "arabic_maqam", template_id="maqsum").score.cycle.id == "maqsum"


def test_requested_template_is_used():
    result = conv(fx.western_classical_phrase(), "arabic_maqam", template_id="wahda")
    assert result.score.cycle.id == "wahda" and result.score.options.template_id == "wahda"
    unknown = conv(fx.western_classical_phrase(), "carnatic", template_id="nope")
    assert unknown.score.cycle.id == "adi" and any("nope" in w for w in unknown.score.warnings)


def test_notes_on_one_pulse_are_pushed_apart():
    # Sixteenths into an eighth-note grid: two starts land on each pulse.
    ir = make_ir([0, 200, 400, 500], durations=1, pulse_s=0.125, subdivision=4, cycle_pulses=16,
                 template_id="4_4", source_format="western_pop")
    result = conv(ir, "arabic_maqam", template_id="maqsum")
    starts = [n.start_pulse for n in result.score.notes]
    assert starts == sorted(set(starts)) and len(starts) == 4
    assert all(n.duration_pulses >= 1 for n in result.score.notes)
    for a, b in zip(result.score.notes, result.score.notes[1:]):
        assert a.start_pulse + a.duration_pulses <= b.start_pulse
    assert items(result, "TIMING_SHIFT")
    assert_additive(result)


def test_free_time_is_rendered_unmeasured_and_loses_no_timing():
    ir = fx.persian_phrase()
    for target in FORMATS:
        result = conv(ir, target)
        assert result.score.free_time is True and result.score.options.free_time is True
        assert result.score.cycle is None and result.score.options.template_id is None
        assert result.score.options.tempo_bpm is None
        for code in ("TIMING_SHIFT", "FREE_TIME_MEASURED", "CYCLE_REINTERPRETED"):
            assert items(result, code) == []
        assert result.loss.summary.timing_score == 1.0
        assert [n.onset_s for n in result.score.notes] == [e.onset_s for e in ir.events]
        assert [n.duration_s for n in result.score.notes] == pytest.approx([e.offset_s - e.onset_s for e in ir.events])
        starts = [n.start_pulse for n in result.score.notes]
        assert all(b > a for a, b in zip(starts, starts[1:]))
        assert all(n.duration_pulses >= 1 for n in result.score.notes)
        assert any("Unmeasured" in w for w in result.score.warnings)
        assert all(i.pulse is None and i.cycle_index is None for i in result.loss.items)


def test_free_time_grid_unit_comes_from_the_short_gaps():
    ir = make_ir([0, 200, 400, 500, 700], durations=[1, 1, 4, 1, 6], free_time=True, pulse_s=0.3)
    result = conv(ir, "western_classical")
    assert result.score.options.pulse_s == pytest.approx(0.3) and result.score.options.subdivision == 2
    assert [n.start_pulse for n in result.score.notes] == [0, 1, 2, 6, 7]
    assert [n.duration_pulses for n in result.score.notes] == [1, 1, 4, 1, 6]


def test_ir_without_any_pulse_information_is_treated_as_free_time():
    ir = make_ir([0, 200, 400], onsets_s=[0.1, 0.9, 1.4], kind="audio")
    bare = ir.model_copy(update={"tempo": ir.tempo.model_copy(update={"bpm": None}), "cycle": Cycle()})
    result = conv(bare, "western_classical")
    assert result.score.free_time and result.score.cycle is None


def test_forcing_free_time_into_bars_is_one_major_item():
    result = conv(fx.persian_phrase(), "western_classical", force_measured=True)
    forced = items(result, "FREE_TIME_MEASURED")
    assert len(forced) == 1 and forced[0].severity == "major"
    assert result.score.free_time is False and result.score.cycle.id == "4_4"
    assert result.score.options.tempo_bpm == pytest.approx(75.0)  # nominal unit 0.4 s, two to a beat
    assert result.loss.summary.timing_score <= 0.5
    assert forced[0].penalty == pytest.approx(0.2 * 0.5)  # every onset fits, so the cap is the whole cost
    assert_additive(result)

    paced = conv(fx.persian_phrase(), "carnatic", force_measured=True, tempo_bpm=90.0)
    assert paced.score.cycle.id == "adi" and paced.score.options.tempo_bpm == pytest.approx(90.0)
    assert len(items(paced, "FREE_TIME_MEASURED")) == 1
    assert_additive(paced)
    # A format with no default cycle still gets bars when asked.
    generic = conv(fx.persian_phrase(), "persian_dastgah", force_measured=True)
    assert generic.score.cycle.id == "generic_4" and len(items(generic, "FREE_TIME_MEASURED")) == 1


def test_force_measured_changes_nothing_for_measured_music():
    plain = conv(fx.western_classical_phrase(), "western_classical")
    forced = conv(fx.western_classical_phrase(), "western_classical", force_measured=True)
    assert items(forced, "FREE_TIME_MEASURED") == []
    assert forced.score.notes == plain.score.notes


# ---------------------------------------------------------------------------
# Tonic
# ---------------------------------------------------------------------------

def test_rast_is_written_on_c_and_the_transposition_is_stated():
    ir = make_ir([0, 200, 350, 500], tonic_hz=fx.D3_HZ, source_format="arabic_maqam", mode_id="rast")
    result = conv(ir, "arabic_maqam")
    opts = result.score.options
    assert (opts.written_tonic.step, opts.written_tonic.alter_cents, opts.written_tonic_octave) == ("C", 0, 3)
    assert opts.transposition_cents == pytest.approx(200.0, abs=0.1)  # sounds a tone above where it is written
    moved = notices(result, "TRANSPOSED_FOR_NOTATION")
    assert len(moved) == 1 and "Rast" in moved[0].human_text and "+200" in moved[0].human_text

    on_c = make_ir([0, 200, 350, 500], tonic_hz=fx.C4_HZ, source_format="arabic_maqam", mode_id="rast")
    result = conv(on_c, "arabic_maqam")
    assert notices(result, "TRANSPOSED_FOR_NOTATION") == []
    assert result.score.options.written_tonic_octave == 4
    assert abs(result.score.options.transposition_cents) < 1


def test_huzam_is_written_on_e_half_flat():
    ir = make_ir([0, 150, 350], source_format="arabic_maqam", mode_id="huzam")
    opts = conv(ir, "arabic_maqam").score.options
    assert (opts.written_tonic.step, opts.written_tonic.alter_cents) == ("E", -50.0)
    assert opts.mode_id == "huzam"


def test_written_tonic_is_the_sounding_pitch_otherwise():
    ir = make_ir([0, 200, 350, 500], tonic_hz=fx.D3_HZ, source_format="arabic_maqam", mode_id="rast")
    asked = conv(ir, "arabic_maqam", written_tonic="sounding")
    assert (asked.score.options.written_tonic.step, asked.score.options.written_tonic_octave) == ("D", 3)
    assert notices(asked, "TRANSPOSED_FOR_NOTATION") == []
    no_mode = conv(make_ir([0, 200], tonic_hz=fx.D3_HZ, source_format="western_classical"), "arabic_maqam")
    assert no_mode.score.options.written_tonic.step == "D"  # conventional needs a mode
    cases = {233.08: ("B", -100.0, 3), 277.18: ("C", 100.0, 4), 311.13: ("E", -100.0, 4), 440.0: ("A", 0.0, 4)}
    for hz, (step, alter, octave) in cases.items():
        opts = conv(make_ir([0], tonic_hz=hz, source_format="western_classical"), "western_classical").score.options
        assert (opts.written_tonic.step, opts.written_tonic.alter_cents, opts.written_tonic_octave) == (step, alter, octave)
        assert abs(opts.transposition_cents) < 1
    sharp = conv(make_ir([0], tonic_hz=150.0, source_format="western_classical"), "western_classical").score.options
    assert sharp.written_tonic.step == "D" and sharp.transposition_cents == pytest.approx(37.0, abs=0.5)


def test_tonic_notices():
    assumed = conv(make_ir([0, 200], tonic_method="assumed", tonic_confidence=0.0), "western_classical")
    assert len(notices(assumed, "TONIC_ASSUMED")) == 1
    assert "D3" in notices(assumed, "TONIC_ASSUMED")[0].human_text
    unsure = conv(make_ir([0, 200], tonic_method="histogram", tonic_confidence=0.2), "western_classical")
    assert len(notices(unsure, "TONIC_ASSUMED")) == 1
    sure = conv(make_ir([0, 200], tonic_method="histogram", tonic_confidence=0.8), "western_classical")
    assert notices(sure, "TONIC_ASSUMED") == []

    drone = conv(make_ir([0, 200], tonic_method="drone", tonic_confidence=0.9), "carnatic")
    assert len(notices(drone, "DRONE_NOT_NOTATED")) == 1

    off = conv(make_ir([0, 200], tonic_hz=150.0), "western_classical")  # 37 cents above D3
    tuning = notices(off, "TUNING_OFFSET")
    assert len(tuning) == 1 and "+37" in tuning[0].human_text
    assert notices(conv(make_ir([0, 200]), "western_classical"), "TUNING_OFFSET") == []
    # Notices never cost fidelity.
    assert assumed.loss.summary.fidelity_score == 1.0 and off.loss.summary.fidelity_score == 1.0


# ---------------------------------------------------------------------------
# Rules of the engine (spec 5)
# ---------------------------------------------------------------------------

def test_same_format_clean_up_still_returns_a_loss_report():
    result = conv(fx.carnatic_phrase(), "carnatic")
    assert result.loss.source_format == "carnatic" and result.loss.target_format == "carnatic"
    assert result.score.source_format == "carnatic" and result.score.format_id == "carnatic"
    assert result.score.options.mode_id == "mohanam" and result.score.cycle.id == "adi"
    assert items(result, "ORNAMENT_APPROXIMATED")  # even sargam only marks a gamaka
    assert result.loss.summary.fidelity_score < 1.0
    assert result.loss.summary.counts_by_code == {"ORNAMENT_APPROXIMATED": 4}
    assert "Loss report: carnatic -> carnatic" in result.loss.to_text()

    clean = conv(fx.western_classical_phrase(), "western_classical")
    assert clean.loss.items == () and clean.loss.summary.fidelity_score == 1.0
    assert "Nothing was lost" in clean.loss.to_text()


def test_the_input_ir_is_never_changed():
    for source in fx.PHRASES:
        ir = fx.phrase_for(source)
        before = ir.model_dump()
        for target in FORMATS:
            conv(ir, target)
            conv(ir, target, force_measured=True, tonic_hz=200.0, explicit_style=False)
        assert ir.model_dump() == before


def test_result_is_reproducible_and_carries_its_options():
    ir = fx.arabic_phrase()
    a = conv(ir, "carnatic", mode_id="kharaharapriya", template_id="adi", tempo_bpm=90.0)
    b = conv(ir, "carnatic", mode_id="kharaharapriya", template_id="adi", tempo_bpm=90.0)
    assert a == b
    o = a.score.options
    assert (o.mode_id, o.template_id, o.tempo_bpm, o.a4_hz) == ("kharaharapriya", "adi", pytest.approx(90.0), 440.0)
    assert o.tonic_hz == ir.tonic.hz and o.pulse_s == pytest.approx(60 / 90 / 4)
    assert ConversionResult.model_validate_json(a.model_dump_json()) == a


def empty_ir(**kw) -> IR:
    return make_ir([], **kw)


@pytest.mark.parametrize("target", FORMATS)
def test_empty_and_one_note_irs_do_not_raise(target):
    for ir in (empty_ir(), empty_ir(free_time=True), empty_ir(source_format=target, mode_id="major")):
        for opts in ({}, {"force_measured": True}, {"mode_id": "major", "template_id": "4_4"}):
            result = conv(ir, target, **opts)
            assert result.score.notes == () and result.loss.items == ()
            assert result.loss.summary.fidelity_score == 1.0

    for ir in (make_ir([386]), make_ir([386], free_time=True), make_ir([386], onsets_s=[3.3], kind="audio"),
               make_ir([386], ornaments={0: oscillation(90.0)}, source_format="carnatic", mode_id="mohanam")):
        for opts in ({}, {"force_measured": True}):
            result = conv(ir, target, **opts)
            assert len(result.score.notes) == 1 and result.score.notes[0].id == "n1"
            assert result.score.notes[0].duration_pulses >= 1
            assert 0.0 <= result.loss.summary.fidelity_score <= 1.0
            assert_additive(result)


@pytest.mark.parametrize("target", FORMATS)
def test_wild_values_degrade_instead_of_raising(target):
    def event(i, onset, offset, cents, **kw):
        return NoteEvent(id=f"w{i}", onset_s=onset, offset_s=offset, pitch_cents=cents, pitch_cents_median=cents,
                         pitch_cents_min=cents, pitch_cents_max=cents, **kw)

    wild = IR(
        source=Source(kind="audio", format_hint="not_a_format"),
        tonic=Tonic(hz=0.0, cents_ref_hz=0.0, confidence=0.0, method="assumed", tuning_offset_cents=-49.0),
        cycle=Cycle(length_pulses=-3, subdivision=0, pulse_s=-1.0, phase_pulses=-7.5),
        events=(
            event(1, 5.0, 4.0, 1e7),  # ends before it starts, absurdly high
            event(2, 0.0, 0.0, -1e7, velocity=900),  # zero length, absurdly low, out of order
            event(3, 5.0, 9e9, 151.3, ornament=oscillation(-5000.0, coverage=7.0)),
            event(4, 5.0, 5.1, 599.99, loudness_db=-400.0, onset_pulse=-12.0, duration_pulses=-1.0),
            event(4, 1e9, 1e9 + 1, 0.0, extra_ornaments=(glide(1e6), grace(0.0))),  # duplicate id
        ),
    )
    before = wild.model_dump()
    for opts in ({}, {"force_measured": True}, {"tempo_bpm": 1e-3, "tonic_hz": 1e-6}, {"mode_id": "?", "template_id": "?"}):
        result = conv(wild, target, **opts)
        assert len(result.score.notes) == 5
        assert [n.id for n in result.score.notes] == ["n1", "n2", "n3", "n4", "n5"]
        starts = [n.start_pulse for n in result.score.notes]
        assert starts == sorted(starts) and all(n.duration_pulses >= 1 for n in result.score.notes)
        assert all(1 <= n.velocity <= 127 and 1 <= n.degree <= 7 for n in result.score.notes)
        assert all(math.isfinite(n.cents) and -2400 <= n.cents <= 3600 for n in result.score.notes)
        assert 0.0 <= result.loss.summary.fidelity_score <= 1.0
        assert len(items(result, "OCTAVE_FOLDED")) == 2
        assert notices(result, "TUNING_OFFSET")
        assert_additive(result)
    assert notices(conv(wild, target), "TONIC_ASSUMED")
    assert wild.model_dump() == before


def test_every_item_and_notice_is_well_formed():
    for source in fx.PHRASES:
        for target in FORMATS:
            for opts in ({}, {"force_measured": True}):
                result = conv(fx.phrase_for(source), target, **opts)
                for item in result.loss.items:
                    assert item.code in LOSS_CODES
                    assert item.human_text.endswith(".") and "{" not in item.human_text
                    assert item.explain_key.split(".")[0] == item.code.lower()
                    assert item.severity in ("info", "minor", "major")
                    if item.event_id is not None:
                        assert item.time_s is not None and fx.phrase_for(source).event(item.event_id) is not None
                for n in result.loss.notices:
                    assert n.code in NOTICE_CODES
                    assert "{" not in n.human_text and n.explain_key == n.code.lower()
                counts = result.loss.summary.counts_by_code
                assert sum(counts.values()) == len(result.loss.items)
                assert all(counts[c] == len(items(result, c)) for c in counts)
                assert result.loss.summary.max_cents_delta == pytest.approx(
                    max(abs(n.cents_delta) for n in result.score.notes))
                assert_additive(result)


def test_adapter_entry_point_uses_the_engine():
    ir = fx.rock_phrase()
    assert registry.get("carnatic").ir_to_format(ir) == conv(ir, "carnatic")
    assert registry.get("rock").ir_to_format(ir, ConvertOptions(explicit_style=False)) == conv(ir, "rock", explicit_style=False)


def test_choosing_the_suggested_mode_applies_it():
    result = conv(fx.western_classical_phrase(), "carnatic", mode_id="shankarabharanam")
    assert result.score.options.mode_id == "shankarabharanam"
    approx = items(result, "MODE_APPROXIMATED")
    assert len(approx) == 1 and approx[0].detail["applied"] is True
    assert [n.name for n in result.score.notes] == ["S", "R2", "G3", "M1", "P", "D2", "N3", "S"]
    assert all(n.in_mode for n in result.score.notes)
    # Inside a swara's window the note is that swara: nothing is reported for it.
    assert items(result, "TEMPERAMENT_RETUNED") == [] and items(result, "MICROTONE_ROUNDED") == []


def test_a_mode_chosen_by_hand_is_not_called_unrepresentable():
    result = conv(fx.arabic_phrase(), "western_classical", mode_id="major")
    assert result.score.options.mode_id == "major"
    assert items(result, "MODE_UNREPRESENTABLE") == [] and items(result, "MODE_APPROXIMATED") == []
    assert len(items(result, "MICROTONE_ROUNDED")) == 4  # the half-flats are still rounded, and said to be
    # Exactly between two keys, the one the chosen mode has is written: E, not E flat.
    assert (note(result, "e3").name, note(result, "e3").cents) == ("3", 400)
    assert (note(result, "e7").name, note(result, "e7").cents) == ("7", 1100)
    assert notices(result, "OUT_OF_MODE_NOTE") == []
    without = conv(fx.arabic_phrase(), "western_classical")
    assert (note(without, "e3").name, note(without, "e3").cents) == ("b3", 300)


def test_shared_twelve_tone_rows_are_offered_without_a_loss_item():
    result = conv(fx.western_classical_phrase(), "western_pop")
    assert [c.id for c in result.suggestions] == ["major"]
    assert result.score.options.mode_id is None  # still not applied without being asked
    assert result.loss.items == () and result.score.cycle.id == "4_4"


def test_sentences_read_naturally():
    plain = conv(fx.western_classical_phrase(), "arabic_maqam")
    assert "an Arabic Maqam performer" in items(plain, "ORNAMENT_NOT_SUPPLIED")[0].human_text
    one = conv(make_ir([0, 400], source_format="western_classical"), "carnatic")
    assert "so 1 note shifts by up to 14 cents" in items(one, "TEMPERAMENT_RETUNED")[0].human_text


def test_an_internal_failure_degrades_to_an_empty_score(monkeypatch):
    from orin.formats import engine

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "_convert", boom)
    result = convert_ir(fx.carnatic_phrase(), registry.get("western_classical").META)
    assert result.score.notes == () and result.score.format_id == "western_classical"
    assert any("could not be completed" in w and "boom" in w for w in result.score.warnings)
    assert result.loss.summary.fidelity_score == 0.0  # an empty page is not "nothing was lost"
    assert (result.loss.source_format, result.loss.target_format) == ("carnatic", "western_classical")
