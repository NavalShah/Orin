"""MIDI rendering: the spec 11.5 round trip (pitches re-extracted from the rendered file
match the adapter output), per-note pitch bend, channel alternation."""
from __future__ import annotations

import io
import math

import pytest

mido = pytest.importorskip("mido")

from orin.formats import registry  # noqa: E402
from orin.render import render  # noqa: E402
from orin.render.midi import BEND_RANGE_SEMITONES, key_and_bend, read_back  # noqa: E402
try:  # the hand builder lives in test_render_spell.py
    from test_render_spell import D_MAJOR, make_score
except ImportError:  # tests/ imported as a package
    from tests.test_render_spell import D_MAJOR, make_score


def cents_between(a: float, b: float) -> float:
    return 1200.0 * math.log2(a / b)


def expected_hz(score):
    return [score.options.tonic_hz * 2.0 ** (n.cents / 1200.0) for n in score.notes]


def messages(data: bytes):
    """(absolute tick, message) for every message of the single track."""
    midi = mido.MidiFile(file=io.BytesIO(data))
    assert midi.type == 0 and len(midi.tracks) == 1
    out, tick = [], 0
    for message in midi.tracks[0]:
        tick += message.time
        out.append((tick, message))
    return midi, out


def assert_round_trip(score, meta, tolerance_cents=1.0):
    out = render(score, meta, "midi")
    assert out.kind == "midi" and out.media_type == "audio/midi" and out.data[:4] == b"MThd"
    back = read_back(out.data)
    assert len(back) == len(score.notes)
    for got, note, hz in zip(back, score.notes, expected_hz(score)):
        assert abs(cents_between(got["hz"], hz)) < tolerance_cents
        assert got["onset_s"] == pytest.approx(note.onset_s, abs=0.003)
        assert got["duration_s"] == pytest.approx(note.duration_s, abs=0.003)
    return out, back


def test_round_trip_for_a_twelve_tone_score():
    score, meta = make_score("western_classical", D_MAJOR, tonic="D", template="4_4", mode="major",
                             tonic_hz=440.0 * 2 ** (-7 / 12))  # D4 on the A440 grid
    out, back = assert_round_trip(score, meta)
    assert [b["key"] for b in back] == [62, 64, 66, 67, 69, 71, 73, 74]
    assert all(b["bend"] == 0 for b in back)
    assert out.warnings == ()


def test_round_trip_for_a_microtonal_score():
    rast = [("1", 1, 0, 2), ("2", 2, 200, 2), ("3-", 3, 350, 2), ("4", 4, 500, 2), ("5", 5, 700, 4),
            ("7-", 7, 1050, 2), ("1", 1, 1200, 2)]
    score, meta = make_score("arabic_maqam", rast, template="maqsum", mode="rast", pulse_s=0.25, tonic_hz=146.83)
    out, back = assert_round_trip(score, meta)
    third = back[2]
    assert abs(cents_between(third["hz"], 146.83 * 2 ** (350 / 1200))) < 1.0
    assert third["hz"] == pytest.approx(179.73, abs=0.05)
    assert third["bend"] != 0  # a half-flat is not a piano key
    assert any("pitch bend" in w for w in out.warnings)


def test_round_trip_for_just_intonation_swaras_and_koron():
    mohanam = [("S", 1, 0, 4), ("R2", 2, 204, 4), ("G3", 3, 386, 4), ("P", 5, 702, 4), ("D2", 6, 884, 4),
               ("S", 1, 1200, 8), ("D2", 6, -316, 3)]
    assert_round_trip(*make_score("carnatic", mohanam, template="adi", tonic_hz=138.59))
    shur = [("1", 1, 0, 2), ("2 koron", 2, 135, 3), ("b3", 3, 295, 5)]
    assert_round_trip(*make_score("persian_dastgah", shur, tonic="D", tonic_hz=293.66, subdivision=2))


def test_channels_alternate_and_the_bend_comes_before_its_note():
    score, meta = make_score("carnatic", [("S", 1, 0, 2), ("R2", 2, 204, 2), ("G3", 3, 386, 2), ("P", 5, 702, 2)],
                             template="adi", tonic_hz=146.83)
    out = render(score, meta, "midi")
    _, events = messages(out.data)
    note_ons = [(i, tick, m) for i, (tick, m) in enumerate(events) if m.type == "note_on" and m.velocity > 0]
    assert [m.channel for _, _, m in note_ons] == [0, 1, 0, 1]
    for index, tick, message in note_ons:
        before_tick, before = events[index - 1]
        assert before.type == "pitchwheel" and before.channel == message.channel and before_tick == tick
    # Nothing retunes a channel while its note is sounding.
    sounding: dict[int, bool] = {}
    for _, message in events:
        if message.type == "note_on" and message.velocity > 0:
            sounding[message.channel] = True
        elif message.type == "note_off":
            sounding[message.channel] = False
        elif message.type == "pitchwheel":
            assert not sounding.get(message.channel, False)
    assert [b["channel"] for b in read_back(out.data)] == [0, 1, 0, 1]


def test_bend_range_tempo_and_time_signature_are_declared():
    score, meta = make_score("carnatic", [("S", 1, 0, 3), ("P", 5, 702, 4)], template="misra_chapu", tonic_hz=146.83)
    midi, events = messages(render(score, meta, "midi").data)
    first_note = next(i for i, (_, m) in enumerate(events) if m.type == "note_on")
    setup = [m for _, m in events[:first_note]]
    for channel in (0, 1):
        controls = [(m.control, m.value) for m in setup if m.type == "control_change" and m.channel == channel]
        assert controls[:4] == [(101, 0), (100, 0), (6, BEND_RANGE_SEMITONES), (38, 0)]
    tempo = next(m for m in setup if m.type == "set_tempo")
    quarter_s = score.options.pulse_s / score.cycle.pulse_quarters
    assert tempo.tempo == round(quarter_s * 1_000_000)
    signature = next(m for m in setup if m.type == "time_signature")
    assert (signature.numerator, signature.denominator) == (7, 8)
    assert events[-1][1].type == "end_of_track"

    free, meta = make_score("persian_dastgah", [("1", 1, 0, 2)], tonic_hz=220.0, subdivision=2)
    _, events = messages(render(free, meta, "midi").data)
    assert not [m for _, m in events if m.type == "time_signature"]


def test_key_and_bend_arithmetic():
    assert key_and_bend(440.0) == (69, 0)
    assert key_and_bend(261.6256) == (60, 0)
    key, bend = key_and_bend(440.0 * 2 ** (0.5 / 12))  # a quarter tone above A4
    assert key in (69, 70) and abs(bend) in (2048, 2047)
    key, bend = key_and_bend(440.0 * 2 ** (0.25 / 12))
    assert (key, bend) == (69, 1024)
    assert key_and_bend(1e-3)[0] == 0 and key_and_bend(1e6)[0] == 127


def test_notes_never_overlap_and_velocity_is_kept():
    score, meta = make_score("rock", [("1", 1, 0, 4), ("5", 5, 700, 4)], tonic="E", octave=2, template="4_4",
                             tonic_hz=82.41)
    long_first = score.notes[0].model_copy(update={"duration_s": 5.0, "velocity": 300})
    score = score.model_copy(update={"notes": (long_first, score.notes[1])})
    back = read_back(render(score, meta, "midi").data)
    assert back[0]["onset_s"] + back[0]["duration_s"] <= back[1]["onset_s"] + 1e-6
    _, events = messages(render(score, meta, "midi").data)
    assert [m.velocity for _, m in events if m.type == "note_on"] == [127, 80]


@pytest.mark.parametrize("format_id", ["western_classical", "western_pop", "rock", "carnatic", "arabic_maqam",
                                       "persian_dastgah"])
def test_empty_and_one_note_scores_give_a_valid_file(format_id):
    meta = registry.get(format_id).META
    empty, _ = make_score(format_id, [], template=meta.default_template_id)
    data = render(empty, meta, "midi").data
    assert data[:4] == b"MThd" and read_back(data) == []
    one, _ = make_score(format_id, [(meta.grid[0].name, 1, 0.0, 3)], template=meta.default_template_id)
    back = read_back(render(one, meta, "midi").data)
    assert len(back) == 1 and abs(cents_between(back[0]["hz"], one.options.tonic_hz)) < 1.0


def test_round_trip_for_real_engine_output():
    pytest.importorskip("orin.formats.engine")
    pytest.importorskip("orin.ingest.notes_names")
    from orin.formats.base import ConvertOptions
    from orin.ingest.notes import parse_notes

    text = "tonic: C4\ntempo: 90\nunits_per_beat: 2\nmode: rast\nC4 D4 Ed4:2 F4 G4:3 r Bd4 C5:2 | Ed4:4~ D4 C4:3 ||\n"
    ir = parse_notes(text, format_id="arabic_maqam", syntax="note_names")
    for format_id, mode in (("arabic_maqam", "rast"), ("carnatic", None), ("western_classical", None)):
        adapter = registry.get(format_id)
        score = adapter.ir_to_format(ir, ConvertOptions(mode_id=mode)).score
        assert len(score.notes) == len(ir.events)
        # The file plays the pitches the adapter wrote (spec 11.5), microtones included.
        assert_round_trip(score, adapter.META)
