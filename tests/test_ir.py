"""The IR's invariants (spec 6.6), JSON round trip, readable errors and immutability."""
from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from orin.eval import fixtures as fx
from orin.eval.fixtures import make_ir, oscillation
from orin.ir.models import IR, IR_VERSION, ContourPoint, Cycle, NoteEvent, Ornament, Tempo, Tonic, fold
from orin.ir.validate import IRInvalid, assert_valid, dump_ir, load_ir, validate_ir


def codes(ir: IR) -> list[str]:
    return [p.code for p in validate_ir(ir)]


def with_events(ir: IR, events) -> IR:
    return ir.model_copy(update={"events": tuple(events)})


def moved(event: NoteEvent, **changes) -> NoteEvent:
    return event.model_copy(update=changes)


@pytest.fixture
def ir() -> IR:
    return make_ir([0, 204, 386, 702], source_format="carnatic", mode_id="mohanam",
                   ornaments={2: oscillation(80.0)}, pulse_s=0.25, subdivision=2, cycle_pulses=16)


# --- invariants -------------------------------------------------------------

@pytest.mark.parametrize("source", list(fx.PHRASES))
def test_fixture_phrases_are_valid(source):
    phrase = fx.phrase_for(source)
    assert validate_ir(phrase) == []
    assert assert_valid(phrase) is phrase
    assert phrase.version == IR_VERSION
    assert phrase.tonic.cents_ref_hz == phrase.tonic.hz


def test_events_must_be_sorted_by_onset(ir):
    a, b, c, d = ir.events
    problems = validate_ir(with_events(ir, [a, c, b, d]))
    assert "events_unsorted" in [p.code for p in problems]
    unsorted = next(p for p in problems if p.code == "events_unsorted")
    assert unsorted.path == "events[2]"
    assert "Note 3" in unsorted.message and "time order" in unsorted.message


def test_events_must_not_overlap(ir):
    a, b, c, d = ir.events
    overlapping = with_events(ir, [moved(a, offset_s=b.onset_s + 0.2), b, c, d])
    problems = validate_ir(overlapping)
    assert [p.code for p in problems] == ["events_overlap"]
    assert "cannot overlap" in problems[0].message and problems[0].path == "events[1]"
    # Touching is not overlapping.
    assert codes(with_events(ir, [moved(a, offset_s=b.onset_s), b, c, d])) == []


def test_event_must_end_after_it_starts(ir):
    a, *rest = ir.events
    assert "event_not_positive" in codes(with_events(ir, [moved(a, offset_s=a.onset_s), *rest]))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_pitch_must_be_finite_at_construction(bad):
    with pytest.raises(ValidationError) as exc:
        NoteEvent(id="x", onset_s=0.0, offset_s=1.0, pitch_cents=bad, pitch_cents_median=0.0,
                  pitch_cents_min=0.0, pitch_cents_max=0.0)
    assert exc.value.errors()[0]["type"] == "finite_number"
    assert exc.value.errors()[0]["loc"] == ("pitch_cents",)
    with pytest.raises(ValidationError):
        ContourPoint(t_s=0.0, cents=bad)
    with pytest.raises(ValidationError):
        Tonic(hz=bad, cents_ref_hz=1.0, confidence=1.0, method="user")


def test_contour_timestamps_must_increase(ir):
    good = tuple(ContourPoint(t_s=0.01 * i, cents=None if i == 3 else 10.0 * i, confidence=0.9) for i in range(6))
    assert codes(ir.model_copy(update={"contour": good})) == []
    for bad_time in (0.02, 0.03):  # going back, and standing still
        bad = (*good[:4], ContourPoint(t_s=bad_time, cents=0.0), good[5])
        problems = validate_ir(ir.model_copy(update={"contour": bad}))
        assert [p.code for p in problems] == ["contour_not_monotonic"]
        assert problems[0].path == "contour[4]"


def test_other_invariants(ir):
    a, b, *rest = ir.events
    assert "duplicate_event_id" in codes(with_events(ir, [a, moved(b, id=a.id), *rest]))
    assert "event_pitch_range" in codes(with_events(ir, [moved(a, pitch_cents_median=900.0), b, *rest]))
    assert "out_of_range" in codes(with_events(ir, [moved(a, confidence=1.5), b, *rest]))
    assert "tonic_not_positive" in codes(ir.model_copy(update={"tonic": ir.tonic.model_copy(update={"hz": 0.0})}))
    assert "cycle_length" in codes(ir.model_copy(update={"cycle": Cycle(length_pulses=0)}))
    assert "cycle_subdivision" in codes(ir.model_copy(update={"cycle": Cycle(subdivision=0)}))
    assert "free_time_with_bpm" in codes(ir.model_copy(update={"tempo": Tempo(bpm=90.0, free_time=True)}))
    dangling = moved(a, ornament=Ornament(kind="glide", contour_ref=(0, 50)))
    assert "contour_ref_range" in codes(with_events(ir, [dangling, b, *rest]))


def test_assert_valid_raises_with_every_problem(ir):
    a, b, c, d = ir.events
    broken = with_events(ir, [a, c, moved(b, id=a.id), d])
    with pytest.raises(IRInvalid) as exc:
        assert_valid(broken)
    assert {p.code for p in exc.value.problems} >= {"events_unsorted", "duplicate_event_id"}
    assert str(exc.value) == "; ".join(p.message for p in exc.value.problems)


# --- JSON -------------------------------------------------------------------

@pytest.mark.parametrize("source", list(fx.PHRASES))
def test_json_round_trip_is_lossless(source):
    phrase = fx.phrase_for(source)
    contour = tuple(ContourPoint(t_s=0.01 * i, cents=None if i % 4 == 0 else 386.0 + 0.123456789 * i, confidence=0.5)
                    for i in range(20))
    phrase = phrase.model_copy(update={"contour": contour})
    text = dump_ir(phrase)
    again = load_ir(text)
    assert again == phrase
    assert dump_ir(again) == text
    assert load_ir(text.encode()) == phrase  # bytes
    assert load_ir(json.loads(text)) == phrase  # a parsed dict
    assert IR.model_validate(phrase.model_dump()) == phrase
    assert json.loads(dump_ir(phrase, indent=2)) == json.loads(text)


def test_json_keeps_exact_floats_and_nulls(ir):
    odd = with_events(ir, [ir.events[0].model_copy(update={"pitch_cents": 1e-9 + 386.3137, "onset_pulse": None})])
    again = load_ir(dump_ir(odd))
    assert again.events[0].pitch_cents == odd.events[0].pitch_cents
    assert again.events[0].onset_pulse is None
    data = json.loads(dump_ir(odd))
    assert data["version"] == IR_VERSION and data["events"][0]["ornament"]["kind"] is None
    assert data["tempo"]["free_time"] is False


# --- readable errors --------------------------------------------------------

def test_load_ir_reports_missing_and_unknown_fields_in_plain_words(ir):
    data = json.loads(dump_ir(ir))
    del data["tonic"]
    data["events"][1]["pitch"] = 3
    del data["events"][2]["onset_s"]
    with pytest.raises(IRInvalid) as exc:
        load_ir(data)
    by_path = {p.path: p for p in exc.value.problems}
    assert by_path["tonic"].message == "'tonic' is required but missing."
    assert by_path["tonic"].code == "schema.missing"
    assert by_path["events[1].pitch"].message == "'events[1].pitch' is not a field Orin knows."
    assert by_path["events[2].onset_s"].code == "schema.missing"
    assert "tonic" in str(exc.value)


def test_load_ir_reports_non_finite_numbers(ir):
    data = ir.model_dump()
    data["events"][0]["pitch_cents"] = math.nan
    with pytest.raises(IRInvalid) as exc:
        load_ir(data)
    assert exc.value.problems[0].code == "schema.finite_number"
    assert exc.value.problems[0].message == "'events[0].pitch_cents' must be a finite number (not NaN or infinity)."


def test_load_ir_reports_wrong_types_and_bad_json(ir):
    data = json.loads(dump_ir(ir))
    data["events"][0]["onset_s"] = "soon"
    data["source"]["kind"] = "video"
    with pytest.raises(IRInvalid) as exc:
        load_ir(json.dumps(data))
    paths = {p.path for p in exc.value.problems}
    assert paths == {"events[0].onset_s", "source.kind"}
    assert all(p.message.startswith(f"'{p.path}'") and p.message.endswith(".") for p in exc.value.problems)

    with pytest.raises(IRInvalid) as exc:
        load_ir("{ this is not json")
    assert exc.value.problems and str(exc.value)


def test_load_ir_also_checks_the_invariants(ir):
    a, b, c, d = ir.events
    text = dump_ir(with_events(ir, [a, c, b, d]))
    with pytest.raises(IRInvalid) as exc:
        load_ir(text)
    assert "events_unsorted" in [p.code for p in exc.value.problems]


# --- immutability -----------------------------------------------------------

def test_models_are_frozen(ir):
    with pytest.raises(ValidationError):
        ir.tonic.hz = 440.0
    with pytest.raises(ValidationError):
        ir.events[0].pitch_cents = 100.0
    with pytest.raises(ValidationError):
        ir.events[0].ornament.kind = "glide"
    with pytest.raises(ValidationError):
        ir.events = ()
    assert isinstance(ir.events, tuple) and isinstance(ir.mode.allowed_pitches_cents, tuple)
    with pytest.raises((TypeError, AttributeError)):
        ir.events.append(ir.events[0])  # type: ignore[attr-defined]


def test_a_changed_copy_leaves_the_original_alone(ir):
    before = ir.model_dump()
    changed = ir.model_copy(update={"tonic": ir.tonic.model_copy(update={"hz": 220.0})})
    assert changed.tonic.hz == 220.0 and ir.tonic.hz == fx.D3_HZ
    assert ir.model_dump() == before
    assert changed != ir


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Tonic(hz=220.0, cents_ref_hz=220.0, confidence=1.0, method="user", note_name="A3")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Ornament(kind="trill")  # type: ignore[arg-type]  # only the four neutral kinds exist


# --- small conveniences ------------------------------------------------------

def test_helpers(ir):
    assert ir.event("e3") is ir.events[2] and ir.event("nope") is None
    assert ir.duration_s == ir.events[-1].offset_s
    assert make_ir([]).duration_s == 0.0
    assert fold(1586.0) == 386.0 and fold(-100.0) == 1100.0 and fold(1200.0) == 0.0


def test_make_ir_pulse_semantics():
    typed = make_ir([0, 200, 400], durations=[2, 1, 3], pulse_s=0.2, subdivision=4, cycle_pulses=16)
    assert [e.onset_pulse for e in typed.events] == [0, 2, 3]
    assert [e.duration_pulses for e in typed.events] == [2, 1, 3]
    assert [e.onset_s for e in typed.events] == pytest.approx([0.0, 0.4, 0.6])
    assert typed.tempo.bpm == pytest.approx(60 / (0.2 * 4))  # bpm = 60 / (pulse_s * subdivision)
    assert typed.cycle.length_pulses == 16 and typed.cycle.pulse_s == 0.2

    free = make_ir([0, 200], free_time=True)
    assert free.tempo.free_time and free.tempo.bpm is None and free.cycle.pulse_s is None
    assert all(e.onset_pulse is None and e.duration_pulses is None for e in free.events)

    heard = make_ir([0, 200], onsets_s=[0.1, 0.72], kind="audio")
    assert [e.onset_s for e in heard.events] == [0.1, 0.72]
    assert all(e.onset_pulse is None for e in heard.events) and heard.source.kind == "audio"
    assert validate_ir(typed) == validate_ir(free) == validate_ir(heard) == []
