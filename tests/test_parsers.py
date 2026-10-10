"""Typed and file notes -> IR (spec 7.14): sargam, note names, ABC, MusicXML, MIDI."""
import io
import re
import unicodedata
import zipfile
from pathlib import Path

import pytest

from orin.errors import IngestError, NotesParseError
from orin.formats import registry
from orin.ingest import notes_names, notes_sargam
from orin.ingest.notes import detect_syntax, parse_notes
from orin.ir.validate import dump_ir, load_ir, validate_ir

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
C4_HZ = 261.6256
D3_HZ = 146.8324


def problems_of(text, **kw):
    with pytest.raises(NotesParseError) as info:
        parse_notes(text, **kw)
    return info.value.problems


def where(problems, code):
    return [(p.line, p.column) for p in problems if p.code == code]


def cents(ir):
    return [e.pitch_cents for e in ir.events]


def pulses(ir):
    return [(e.onset_pulse, e.duration_pulses) for e in ir.events]


def warning_codes(ir):
    return [w.code for w in ir.meta.warnings]


def check(ir, syntax):
    """What every parsed IR must satisfy."""
    assert validate_ir(ir) == []
    assert ir.source.kind == "notes" and ir.source.notes_syntax == syntax
    assert ir.source.duration_s >= ir.events[-1].offset_s > 0
    assert ir.contour == ()
    assert ir.cycle.subdivision >= 1 and ir.cycle.pulse_s > 0
    assert ir.tempo.bpm == pytest.approx(60.0 / (ir.cycle.pulse_s * ir.cycle.subdivision))
    assert ir.tonic.cents_ref_hz == ir.tonic.hz and ir.tonic.nearest_note
    for e in ir.events:
        assert e.confidence == 1.0
        assert e.pitch_cents == e.pitch_cents_median == e.pitch_cents_min == e.pitch_cents_max
        assert e.onset_pulse is not None and e.duration_pulses > 0
    assert load_ir(dump_ir(ir)) == ir  # JSON round trip is lossless
    return ir


# ---------------------------------------------------------------------------
# Sargam
# ---------------------------------------------------------------------------

GOOD_SARGAM = """\
# a test phrase
tonic: D3
raga: mohanam
tala: adi
tempo: 120
units_per_beat: 2

S R G3 P D S' , , | (S' D) P~ G/ R\\ | S. ; _ ||   # one cycle
"""


def test_sargam_good_file():
    ir = check(parse_notes(GOOD_SARGAM), "sargam")
    assert ir.source.format_hint == "carnatic"
    # Bare R, G and D take their variant from Mohanam (R2, G3, D2).
    assert cents(ir) == [0, 204, 386, 702, 884, 1200, 1200, 884, 702, 386, 204, -1200]
    # The double-speed group needs half units, so the grid is doubled: 4 pulses per beat.
    assert ir.cycle.subdivision == 4
    assert pulses(ir) == [(0, 2), (2, 2), (4, 2), (6, 2), (8, 2), (10, 6), (16, 1), (17, 1), (18, 2), (20, 2),
                          (22, 2), (24, 6)]
    assert ir.cycle.template_id == "adi" and ir.cycle.length_pulses == 32 and ir.cycle.confidence == 1.0
    assert ir.cycle.pulse_s == pytest.approx(0.125)
    assert ir.tempo.bpm == 120 and ir.tempo.confidence == 1.0
    assert [(e.onset_s, e.offset_s) for e in ir.events[:2]] == [(0.0, 0.25), (0.25, 0.5)]
    assert ir.source.duration_s == pytest.approx(4.0)  # the closing rest counts
    assert ir.tonic.method == "declared" and ir.tonic.confidence == 1.0
    assert ir.tonic.hz == pytest.approx(D3_HZ, abs=1e-3) and ir.tonic.nearest_note == "D3"
    assert abs(ir.tonic.tuning_offset_cents) < 0.01
    assert (ir.mode.mode_id, ir.mode.label_system, ir.mode.user_selected, ir.mode.confidence) == \
        ("mohanam", "carnatic", True, 1.0)
    assert ir.mode.allowed_pitches_cents == (0.0, 204.0, 386.0, 702.0, 884.0)
    assert ir.meta.warnings == ()
    assert not any(e.guessed for e in ir.events)


def test_sargam_ornaments():
    ir = parse_notes(GOOD_SARGAM)
    by_cents = {(e.pitch_cents, e.onset_pulse): e.ornament for e in ir.events}
    wavy, up, down = by_cents[(702, 18)], by_cents[(386, 20)], by_cents[(204, 22)]
    assert (wavy.kind, wavy.amplitude_cents, wavy.position, wavy.coverage, wavy.confidence) == \
        ("oscillation", 60.0, "whole", 1.0, 0.5)
    assert (up.kind, up.amplitude_cents, up.shape, up.position, up.coverage, up.confidence) == \
        ("glide", 150.0, "up", "start", 0.3, 0.5)
    assert (down.kind, down.shape) == ("glide", "down")
    assert ir.events[0].ornament.kind is None


def test_sargam_octaves_and_case():
    ir = parse_notes("s S' S'' S. S.. p' P.", syntax="sargam")
    assert cents(ir) == [0, 1200, 2400, -1200, -2400, 1902, -498]


def test_sargam_pretty_octave_marks_match_ascii():
    ascii_form = "S' R2' G3. S. P'' D2..~"
    pretty = "Ṡ Ṙ2 G3̣ Ṣ Ṗ̇ Ḍ̣2~"  # dots after the letter, precomposed Ṣ
    after_variant = "Ṡ R2̇ G3̣ Ṣ Ṗ̇ D2̣̣~"
    want = parse_notes(ascii_form, syntax="sargam")
    assert cents(want) == [1200, 1404, -814, -1200, 3102, -1516]
    for text in (pretty, after_variant, unicodedata.normalize("NFC", pretty), "S’ R2’ G3. S. P’’ D2..~"):
        got = parse_notes(text, syntax="sargam")
        assert cents(got) == cents(want) and pulses(got) == pulses(want)
        assert [e.ornament for e in got.events] == [e.ornament for e in want.events]


def test_sargam_groups_nest_and_extend():
    ir = check(parse_notes("S (R2 (G3 P) ,) ; _ M1", syntax="sargam"), "sargam")
    # Units: S 1, R2 1/2, G3 1/4, P 1/4 + 1/2 (comma at group speed) + 2 (semicolon), rest 1, M1 1.
    assert ir.cycle.subdivision == 8  # 2 units per beat, quarter units need 4 pulses each
    assert pulses(ir) == [(0, 4), (4, 2), (6, 1), (7, 11), (22, 4)]
    assert ir.source.duration_s == pytest.approx(26 * ir.cycle.pulse_s)


def test_sargam_separators_bars_and_comments_are_ignored_without_a_tala():
    plain = parse_notes("S R2 G3 M1 P D2 N3 S'", syntax="sargam")
    noisy = parse_notes("# a scale\nS-R2 - G3 | M1\n\n|| P D2* N3 || S'   # up", syntax="sargam")
    assert cents(noisy) == cents(plain) == [0, 204, 386, 498, 702, 884, 1088, 1200]
    assert pulses(noisy) == pulses(plain)
    assert cents(parse_notes("srgmpdns", syntax="sargam", mode_id="shankarabharanam")) == \
        [0, 204, 386, 498, 702, 884, 1088, 0]


@pytest.mark.parametrize("header, note, hz", [
    ("sruti: 1", "C3", 130.8128),
    ("sruti: 1.5", "C#3", 138.5913),
    ("sruti: 2", "D3", 146.8324),
    ("sruti: 5.5", "G#3", 207.6523),
    ("sruti: 7", "B3", 246.9417),
    ("shruti: 4", "F3", 174.6141),
    ("tonic: D3", "D3", 146.8324),
    ("tonic: C#3", "C#3", 138.5913),
    ("tonic: Bb2", "A#2", 116.5409),
    ("tonic: G", "G3", 195.9977),
    ("tonic: 146.83", "D3", 146.83),
    ("tonic: 146.83 Hz", "D3", 146.83),
    ("tonic: 220hz  # A", "A3", 220.0),
])
def test_sargam_tonic_headers(header, note, hz):
    ir = parse_notes(f"{header}\nS R2 G3", syntax="sargam")
    assert ir.tonic.hz == pytest.approx(hz, abs=1e-3)
    assert ir.tonic.nearest_note == note and ir.tonic.method == "declared" and ir.tonic.confidence == 1.0
    assert "tonic_assumed" not in warning_codes(ir)


def test_sargam_defaults_are_flagged():
    ir = check(parse_notes("S R2 G3 P", syntax="sargam"), "sargam")
    assert ir.tonic.nearest_note == "C3" and ir.tonic.method == "assumed" and ir.tonic.confidence < 0.5
    assert ir.tempo.bpm == 60 and ir.tempo.confidence < 1.0
    assert ir.cycle.subdivision == 2 and ir.cycle.pulse_s == pytest.approx(0.5)
    assert ir.cycle.length_pulses is None and ir.cycle.template_id is None
    assert ir.mode.mode_id is None and ir.mode.allowed_pitches_cents == ()
    assert {"tonic_assumed", "tempo_assumed"} <= set(warning_codes(ir))
    message = next(w.message for w in ir.meta.warnings if w.code == "tonic_assumed")
    assert "C3" in message and "tonic:" in message


def test_sargam_caller_overrides():
    text = "tonic: D3\nraga: mohanam\ntempo: 80\nS R G P"
    ir = check(parse_notes(text, tonic_hz=220.0, mode_id="hamsadhwani", tempo_bpm=90, filename="a.sargam"), "sargam")
    assert ir.tonic.hz == 220.0 and ir.tonic.method == "user" and ir.tonic.nearest_note == "A3"
    assert ir.mode.mode_id == "hamsadhwani" and ir.mode.user_selected
    assert ir.tempo.bpm == 90 and ir.tempo.confidence == 1.0
    assert ir.source.filename == "a.sargam" and ir.source.sha256 and len(ir.source.sha256) == 64
    assert cents(ir) == [0, 204, 386, 702]  # bare letters still follow the raga written in the text
    unknown = parse_notes("S R2", syntax="sargam", mode_id="nosuchraga")
    assert unknown.mode.mode_id is None and "mode_unknown" in warning_codes(unknown)


def test_sargam_talas_by_name():
    for value, template, beats in [("adi", "adi", 8), ("Adi tala", "adi", 8), ("rupakam", "rupakam_3", 3),
                                   ("rupakam_3", "rupakam_3", 3), ("Misra Chapu", "misra_chapu", 7),
                                   ("khanda-chapu", "khanda_chapu", 5)]:
        ir = parse_notes(f"tala: {value}\nS R2 G3", syntax="sargam")
        assert ir.cycle.template_id == template and ir.cycle.length_pulses == beats * 2
    for value in ("Mohanam", "mohanam", "MOHANAM", "Shuddha Saveri", "shuddha_saveri", "kalyani"):
        assert parse_notes(f"raga: {value}\nS", syntax="sargam").mode.mode_id == value.lower().replace(" ", "_")


def test_sargam_through_the_adapter():
    ir = registry.get("carnatic").parse_notes(GOOD_SARGAM, filename="x.sargam")
    assert ir.source.format_hint == "carnatic" and ir.source.notes_syntax == "sargam" and len(ir.events) == 12


# -- errors -----------------------------------------------------------------

def test_e001_unknown_token_required_position():
    problems = problems_of("S R2 G3 X P")
    assert [(p.code, p.line, p.column, p.length, p.severity) for p in problems] == [("E001", 1, 9, 1, "error")]
    assert "'X'" in problems[0].message and "swara" in problems[0].message


def test_e001_positions():
    problems = problems_of("S R2\n  hello G3 2 P!", syntax="sargam")
    assert [(p.code, p.line, p.column, p.length) for p in problems] == \
        [("E001", 2, 3, 5), ("E001", 2, 12, 1), ("E001", 2, 15, 1)]


def test_e002_variant_required():
    assert where(problems_of("S R G3", syntax="sargam"), "E002") == [(1, 3)]
    assert where(problems_of("S R2 G M D N", syntax="sargam"), "E002") == [(1, 6), (1, 8), (1, 10), (1, 12)]
    # Mohanam has no M or N to take a number from; R, G and D are fine.
    in_raga = problems_of("raga: mohanam\nS R G M P D N")
    assert where(in_raga, "E002") == [(2, 7), (2, 13)]
    assert "M1 or M2" in in_raga[0].message and "Mohanam" in in_raga[0].message


def test_e002_when_the_mode_uses_two_variants_of_a_swara():
    from orin.formats import carnatic
    from orin.formats._build import mode
    from orin.ingest.notes import Problems

    # A test fixture, not a raga: no row in Orin's table has two variants of one swara yet.
    fixture = mode("fixture", "Fixture", carnatic.swaras("S R2 G3 M1 P D2 N2 N3"), kind="janya",
                   source="test fixture", confidence="low")
    variants = notes_sargam._raga_variants(fixture)
    problems = Problems()
    assert notes_sargam._resolve("N", "", False, fixture, variants, 3, 7, 1, problems) is None
    assert notes_sargam._resolve("D", "", False, fixture, variants, 3, 9, 1, problems) == "D2"
    assert notes_sargam._resolve("N", "3", False, fixture, variants, 3, 11, 2, problems) == "N3"
    assert [(p.code, p.line, p.column) for p in problems.items] == [("E002", 3, 7)]
    assert "N2 and N3" in problems.items[0].message


def test_e003_invalid_variant():
    problems = problems_of("S1 P2 M3 R4 G0 D12", syntax="sargam")
    assert [(p.code, p.line, p.column, p.length) for p in problems] == \
        [("E003", 1, 1, 2), ("E003", 1, 4, 2), ("E003", 1, 7, 2), ("E003", 1, 10, 2), ("E003", 1, 13, 2),
         ("E003", 1, 16, 3)]
    assert "M1, M2" in problems[2].message


def test_e004_extension_with_no_note_is_a_warning():
    ir = check(parse_notes("# start\n , ; S R2 , _ , G3", syntax="sargam"), "sargam")
    assert [(w.code, w.stage) for w in ir.meta.warnings if w.code == "E004"] == [("E004", "ingest")]
    assert "Line 2, column 2" in next(w.message for w in ir.meta.warnings if w.code == "E004")
    # The leading comma and semicolon are 3 units of rest; a comma after a rest is more rest.
    assert pulses(ir) == [(3, 1), (4, 2), (8, 1)]
    problems = problems_of(", S X", syntax="sargam")  # raised with the error, still marked as a warning
    assert [(p.code, p.severity, p.line, p.column) for p in problems] == \
        [("E004", "warning", 1, 1), ("E001", "error", 1, 5)]


def test_e005_unbalanced_group():
    assert where(problems_of("S ) R2", syntax="sargam"), "E005") == [(1, 3)]
    assert where(problems_of("S R2\nS ( R2 (G3 P)", syntax="sargam"), "E005") == [(2, 3)]
    assert where(problems_of("(S R2\nG3 P)", syntax="sargam"), "E005") == [(1, 1), (2, 5)]


def test_e006_adi_line_one_unit_short_required_position():
    line = "S R2 G3 P D2 S' D2 P | G3 R2 S R2 | G3 R2 S ||"
    problems = problems_of("tala: adi\n" + line)
    assert [(p.code, p.line, p.column, p.length) for p in problems] == [("E006", 2, line.index("||") + 1, 2)]
    # Adi is 8 beats; at 2 units per beat that is 16 units, and 15 were written.
    assert "16" in problems[0].message and "15" in problems[0].message and "8 beats" in problems[0].message
    no_bars = "S R2 G3 P D2 S' D2 P G3 R2 S R2 G3 R2 S ||"
    assert where(problems_of("tala: adi\n\n" + no_bars), "E006") == [(3, no_bars.index("||") + 1)]


def test_e006_single_bar_off_the_group_boundary():
    line = "S R2 G3 P D2 S' D2 | P G3 R2 S | S R2 G3 P ||"
    problems = problems_of("tala: adi\n" + line)
    # Reported once, at the bar that is wrong; the later bars are measured from it.
    assert [(p.code, p.line, p.column, p.length) for p in problems] == [("E006", 2, line.index("|") + 1, 1)]
    assert "after 7 units" in problems[0].message and "expects 8" in problems[0].message
    long_cycle = "S R2 G3 P D2 S' D2 P | G3 R2 S R2 | G3 R2 S , , ||"
    message = problems_of("tala: adi\n" + long_cycle)[0].message
    assert "17 units" in message and "needs 16" in message


def test_e006_follows_units_per_beat_and_only_runs_with_a_tala():
    four = "S , R2 , G3 , P , D2 , S' , , , , , | D2 , P , G3 , R2 , | S , , , _ _ _ ||"
    problems = problems_of("tala: adi\nunits_per_beat: 4\n" + four)
    assert where(problems, "E006") == [(3, four.index("||") + 1)]
    assert "31 units" in problems[0].message and "needs 32" in problems[0].message
    good = four.replace("_ _ _ ||", "_ _ _ _ ||")
    ir = check(parse_notes("tala: adi\nunits_per_beat: 4\n|| " + good + "\n" + good), "sargam")
    assert ir.cycle.length_pulses == 32 and ir.source.duration_s == pytest.approx(64 * ir.cycle.pulse_s)
    assert parse_notes("S R2 | G3 || P", syntax="sargam").events  # no tala, so bars are not checked
    rupakam = parse_notes("tala: rupakam\nS R2 G3 P D2 S' || S' D2 P G3 R2 S ||")
    assert rupakam.cycle.length_pulses == 6


def test_e007_bad_header():
    text = "bogus: 1\ntempo: fast\ntonic: H9\nunits_per_beat: 0\nsruti: 3.5\ntala:\nS R2"
    problems = problems_of(text, syntax="sargam")
    assert [(p.code, p.line, p.column, p.length) for p in problems] == \
        [("E007", 1, 1, 5), ("E007", 2, 8, 4), ("E007", 3, 8, 2), ("E007", 4, 17, 1), ("E007", 5, 8, 3),
         ("E007", 6, 6, 1)]
    assert "tonic, sruti, raga, tala, tempo, units_per_beat" in problems[0].message


def test_e008_unknown_raga_or_tala_lists_nearest():
    problems = problems_of("raga: mohana\ntala: rupaka\nS R2", syntax="sargam")
    assert [(p.code, p.line, p.column, p.length) for p in problems] == [("E008", 1, 7, 6), ("E008", 2, 7, 6)]
    assert "mohanam" in problems[0].message and "rupakam_3" in problems[1].message
    far = problems_of("raga: zzzz\nS", syntax="sargam")[0].message
    assert "kalyani" in far and "hindolam" in far  # nothing close: the whole table is listed
    assert where(problems_of("raga: rast\nS", syntax="sargam"), "E008") == [(1, 7)]  # a maqam is not a raga


def test_w101_variant_outside_the_raga_warns_and_keeps_the_note():
    ir = check(parse_notes("raga: mohanam\nS R2 G2 P M1* N3"), "sargam")
    assert cents(ir) == [0, 204, 316, 702, 498, 1088]
    w101 = [w.message for w in ir.meta.warnings if w.code == "W101"]
    assert len(w101) == 2  # G2 and N3; the starred M1 is already marked as outside
    assert "Line 2, column 6" in w101[0] and "G2" in w101[0] and "Line 2, column 15" in w101[1]
    assert warning_codes(parse_notes("raga: hindolam\nS G2 M1 P")).count("W101") == 1  # Hindolam has no Pa


def test_every_sargam_problem_is_collected():
    text = "raga: nosuch\ntala: adi\nwhat: 1\n, S1 R X ) (\nS R2 G3 P |"
    problems = problems_of(text, syntax="sargam")
    assert {p.code for p in problems} == {"E001", "E002", "E003", "E004", "E005", "E006", "E007", "E008"}
    assert [(p.line, p.column) for p in problems] == sorted((p.line, p.column) for p in problems)
    assert all(p.message and p.line >= 1 and p.column >= 1 and p.length >= 1 for p in problems)


def test_notes_parse_error_message_points_at_the_first_error():
    with pytest.raises(NotesParseError) as info:
        parse_notes(", S\nS R2 Q", syntax="sargam")
    assert info.value.message.startswith("Line 2, column 6: 'Q'")
    assert info.value.code == "notes_parse_error" and info.value.hint


def test_empty_input():
    for blank in ("", "   \n", b"", b"\n\n"):
        with pytest.raises(IngestError) as info:
            parse_notes(blank)
        assert info.value.code == "empty"
    assert where(problems_of("tonic: D3\n# nothing yet\n", syntax="sargam"), "E000") == [(2, 1)]
    assert where(problems_of("_ _ _", syntax="sargam"), "E000") == [(1, 1)]
    assert where(problems_of("tempo: 90\n", syntax="note_names"), "E000") == [(1, 1)]


def test_grammar_help_is_exposed():
    for text in (notes_sargam.GRAMMAR_HELP, notes_names.GRAMMAR_HELP):
        assert "Example" in text and "tonic:" in text and len(text.splitlines()) < 45
    example = notes_sargam.GRAMMAR_HELP.split("Example:\n")[1].split("\n\n")[0]
    assert len(parse_notes(example, syntax="sargam").events) == 11
    example = notes_names.GRAMMAR_HELP.split("Example:\n")[1].split("\n\n")[0]
    assert len(parse_notes(example, syntax="note_names").events) == 9


# ---------------------------------------------------------------------------
# Note names
# ---------------------------------------------------------------------------

def test_names_accidentals():
    text = "tonic: C4\nC4 C#4 Db4 C##4 Dbb4 E+4 Ed4 F#+4 Gdb4 Ep4 F>4 B3 C5"
    ir = check(parse_notes(text, syntax="note_names"), "note_names")
    #                   C  C#   Db   C##  Dbb E+   Ed   F#+  Gdb  Ep   F>   B3    C5
    assert cents(ir) == [0, 100, 100, 200, 0, 450, 350, 650, 550, 340, 540, -100, 1200]
    assert ir.source.format_hint == "western_classical"
    assert not any(e.guessed for e in ir.events)


def test_names_letters_are_case_insensitive_and_accidentals_take_the_longest_match():
    ir = parse_notes("tonic: C4\nd4 bb3 db4 dd4 ddb4 Bbb3 a3", syntax="note_names")
    assert cents(ir) == [200, -200, 100, 150, 50, -300, -300]
    assert where(problems_of("tonic: C4\nd4 dB4"), "E001") == [(2, 4)]  # accidentals are lower case


def test_names_durations_rests_and_bars():
    ir = check(parse_notes("tonic: D4\ntempo: 120\nD4:2 E4 r | F#4:3 r:2 || G4", syntax="note_names"), "note_names")
    assert pulses(ir) == [(0, 2), (2, 1), (4, 3), (9, 1)]
    assert cents(ir) == [0, 200, 400, 500]
    assert ir.cycle.subdivision == 1 and ir.cycle.pulse_s == pytest.approx(0.5)
    assert [(e.onset_s, e.offset_s) for e in ir.events] == [(0.0, 1.0), (1.0, 1.5), (2.0, 3.5), (4.5, 5.0)]
    trailing = parse_notes("tonic: D4\ntempo: 120\nD4 r:3", syntax="note_names")
    assert trailing.source.duration_s == pytest.approx(2.0)


def test_names_ornaments_comments_and_sharps():
    ir = parse_notes("tonic: D4   # home\n# F#4 G4\nD4:2~ F#4/ A4:2\\ G4* # tail", syntax="note_names")
    assert cents(ir) == [0, 400, 700, 500]
    assert [e.ornament.kind for e in ir.events] == ["oscillation", "glide", "glide", None]
    assert [e.ornament.shape for e in ir.events] == [None, "up", "down", None]
    assert pulses(ir) == [(0, 2), (2, 1), (3, 2), (5, 1)]


def test_names_headers_meter_units_and_mode():
    text = "tonic: D4\ntempo: 90\nmeter: 4/4\nunits_per_beat: 4\nmode: dorian\nD4:4 E4:4 F4:8 | G4:16 ||"
    ir = check(parse_notes(text), "note_names")
    assert ir.cycle.template_id == "4_4" and ir.cycle.length_pulses == 16 and ir.cycle.subdivision == 4
    assert ir.cycle.label == "4/4" and ir.cycle.confidence == 1.0
    assert ir.tempo.bpm == 90 and ir.tempo.confidence == 1.0
    assert ir.cycle.pulse_s == pytest.approx(60 / 90 / 4)
    assert (ir.mode.mode_id, ir.mode.label_system, ir.mode.user_selected) == ("dorian", "western_classical", True)
    assert ir.mode.allowed_pitches_cents == (0.0, 200.0, 300.0, 500.0, 700.0, 900.0, 1000.0)
    # 6/8 is two dotted-quarter beats in the format's template.
    six = parse_notes("tonic: D4\nmeter: 6/8\nunits_per_beat: 3\nD4 E4 F#4 G4:3", syntax="note_names")
    assert six.cycle.template_id == "6_8" and six.cycle.length_pulses == 6
    # A meter the format does not list still gives a cycle length, with no template.
    five = parse_notes("tonic: D4\nmeter: 5/4\nD4 E4", syntax="note_names")
    assert five.cycle.template_id is None and five.cycle.length_pulses == 5 and five.cycle.label == "5/4"


def test_names_mode_from_another_format_and_format_hint():
    ir = parse_notes("tonic: C4\nmode: rast\nC4 D4 Ed4 F4", syntax="note_names")
    assert (ir.mode.mode_id, ir.mode.label_system) == ("rast", "arabic_maqam")
    assert ir.source.format_hint == "western_classical"
    assert 350.0 in ir.mode.allowed_pitches_cents
    arabic = registry.get("arabic_maqam").parse_notes("tonic: D4\nmode: Bayati\nD4 Ed4 F4 G4")
    assert arabic.source.format_hint == "arabic_maqam" and arabic.mode.mode_id == "bayati"
    assert cents(arabic) == [0, 150, 300, 500]
    persian = registry.get("persian_dastgah").parse_notes("tonic: D4\nD4 Ep4 F4 G4 A4 Bb4 C5 F>4")
    assert cents(persian) == [0, 140, 300, 500, 700, 800, 1000, 340]


def test_names_tonic_forms():
    assert parse_notes("tonic: 293.66 Hz\nD4 E4", syntax="note_names").tonic.nearest_note == "D4"
    # A tonic with no octave is put at or just below the middle of the notes.
    ir = parse_notes("tonic: G\nD4 E4 G4 B4 D5", syntax="note_names")
    assert ir.tonic.nearest_note == "G4" and ir.tonic.method == "declared" and cents(ir)[0] == -500
    half_flat = parse_notes("tonic: Ed4\nEd4 F4 G4", syntax="note_names")
    assert cents(half_flat) == [0, 150, 350] and half_flat.tonic.tuning_offset_cents == pytest.approx(-50, abs=0.01)
    assert half_flat.tonic.nearest_note == "E4"
    user = parse_notes("tonic: D4\nD4 E4", syntax="note_names", tonic_hz=C4_HZ)
    assert user.tonic.method == "user" and cents(user) == [200, 400]


def test_names_missing_tonic_is_inferred_and_flagged():
    ir = check(parse_notes("D4 E4 F#4 G4 A4 B4 C#5 D5:2 A4 F#4 D4:2", syntax="note_names"), "note_names")
    assert ir.tonic.method == "key_profile" and ir.tonic.nearest_note == "D4"
    assert 0 < ir.tonic.confidence <= 0.6
    assert cents(ir)[:3] == [0, 200, 400]
    flagged = [w for w in ir.meta.warnings if w.code == "tonic_assumed"]
    assert len(flagged) == 1 and "D4" in flagged[0].message and "tonic:" in flagged[0].message
    assert ir.tempo.confidence < 1.0 and "tempo_assumed" in warning_codes(ir)
    # The octave is the one at or just below the median pitch.
    high = parse_notes("D5 E5 F#5 G5 A5 B5 C#6 D6:2 A5 F#5 D5:2", syntax="note_names")
    assert high.tonic.nearest_note == "D5"


def test_names_fewer_than_three_notes_assume_the_first():
    ir = check(parse_notes("F#4 A4", syntax="note_names"), "note_names")
    assert ir.tonic.method == "assumed" and ir.tonic.nearest_note == "F#4" and ir.tonic.confidence < 0.5
    assert cents(ir) == [0, 300] and "tonic_assumed" in warning_codes(ir)


def test_names_bad_tokens_are_located():
    problems = problems_of("tonic: C4\nD4 X4 F# G4:0 H2\n  Dx4 r:0 C12 E4:2:3", syntax="note_names")
    assert [(p.code, p.line, p.column, p.length) for p in problems] == \
        [("E001", 2, 4, 2), ("E001", 2, 7, 2), ("E001", 2, 10, 4), ("E001", 2, 15, 2),
         ("E001", 3, 3, 3), ("E001", 3, 7, 3), ("E001", 3, 11, 3), ("E001", 3, 15, 6)]
    assert "octave" in problems[1].message  # F# has no octave number
    assert "length" in problems[2].message  # G4:0
    assert "range" in problems[6].message  # C12


def test_names_bad_headers_are_located():
    problems = problems_of("tonic: H\ntempo: 0\nmeter: 4\nmode: nosuchmode\nswing: yes\nunits_per_beat: 2.5\nD4",
                           syntax="note_names")
    assert [(p.code, p.line, p.column) for p in problems] == \
        [("E007", 1, 8), ("E007", 2, 8), ("E007", 3, 8), ("E008", 4, 7), ("E007", 5, 1), ("E007", 6, 17)]
    assert "dorian" in problems_of("mode: dorien\nD4", syntax="note_names")[0].message


# ---------------------------------------------------------------------------
# Syntax detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("S R2 G3 P D2 S'", "sargam"),
    ("srgm pdns", "sargam"),
    ("G3 R2 G3 P , , D2 _", "sargam"),
    ("raga: mohanam\nG D G", "sargam"),
    ("D4 E4 F#4:2 G4", "note_names"),
    ("C4 D4 Ed4 r:2 Bd4", "note_names"),
    ("meter: 3/4\nD2 G3 D2", "note_names"),
    ("X:1\nT:t\nM:4/4\nK:D\nDEFG|", "abc"),
    ('<?xml version="1.0"?><score-partwise version="3.1"></score-partwise>', "musicxml"),
])
def test_detect_syntax_from_content(text, expected):
    assert detect_syntax(text) == expected
    assert detect_syntax(text.encode("utf-8")) == expected
    assert detect_syntax(text, filename="wrong.sargam" if expected != "sargam" else "wrong.notes") == expected


def test_detect_syntax_uses_hints_only_to_break_a_tie():
    tie = "D2 G3 D2 G3"  # valid in both text grammars
    assert detect_syntax(tie) == "note_names"
    assert detect_syntax(tie, filename="x.sargam") == "sargam"
    assert detect_syntax(tie, filename="x.notes", format_id="carnatic") == "note_names"
    assert detect_syntax(tie, format_id="carnatic") == "sargam"
    assert detect_syntax(tie, format_id="western_classical") == "note_names"
    assert parse_notes(tie, format_id="carnatic").source.notes_syntax == "sargam"
    assert parse_notes(tie, syntax="auto").source.notes_syntax == "note_names"


def test_detect_syntax_rejects_what_is_not_notes():
    with pytest.raises(IngestError) as info:
        detect_syntax(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 20)
    assert info.value.code == "unsupported_type"
    with pytest.raises(IngestError):
        detect_syntax(bytes(range(32)))
    with pytest.raises(IngestError):
        parse_notes("S R2 G3", syntax="tablature")


# ---------------------------------------------------------------------------
# ABC and MusicXML (music21)
# ---------------------------------------------------------------------------

ABC_TUNE = """X:1
T:Test
M:4/4
L:1/4
Q:1/4=120
K:D
D E F G | A2 z B | [DFA]2 "G"c d |]
"""


def test_abc_pitches_onsets_and_headers():
    ir = check(parse_notes(ABC_TUNE), "abc")
    assert ir.source.format_hint == "western_classical"
    # K:D sharpens F and C. The chord [DFA] keeps its top note, A.
    assert cents(ir) == [0, 200, 400, 500, 700, 900, 700, 1100, 1200]
    assert pulses(ir) == [(0, 4), (4, 4), (8, 4), (12, 4), (16, 8), (28, 4), (32, 8), (40, 4), (44, 4)]
    assert ir.cycle.subdivision == 4 and ir.cycle.template_id == "4_4" and ir.cycle.length_pulses == 16
    assert ir.tempo.bpm == 120 and ir.tempo.confidence == 1.0
    assert ir.events[1].onset_s == pytest.approx(0.5) and ir.source.duration_s == pytest.approx(6.0)
    assert ir.tonic.method == "declared" and ir.tonic.nearest_note == "D4" and ir.tonic.confidence == 1.0
    assert (ir.mode.mode_id, ir.mode.label_system, ir.mode.user_selected) == ("major", "western_classical", True)


def test_abc_chord_keeps_the_top_note_and_flags_it():
    ir = parse_notes(ABC_TUNE)
    assert [e.guessed for e in ir.events] == [False] * 6 + [True, False, False]
    assert warning_codes(ir).count("top_line_only") == 1
    two = parse_notes(ABC_TUNE.replace('"G"c d', "[ce] [df]"))
    assert warning_codes(two).count("top_line_only") == 1  # warned once, however many chords
    assert [e.guessed for e in two.events][-3:] == [True, True, True]


def test_abc_defaults_and_modes():
    ir = check(parse_notes("X:1\nK:Edor\nE F G A | B c d e |"), "abc")
    assert ir.tempo.bpm == 100 and ir.tempo.confidence < 1.0 and "tempo_assumed" in warning_codes(ir)
    assert ir.tonic.nearest_note == "E4" and ir.mode.mode_id == "dorian"
    assert cents(ir) == [0, 200, 300, 500, 700, 900, 1000, 1200]
    assert pulses(ir)[:3] == [(0, 2), (2, 2), (4, 2)]  # ABC's default length, an eighth
    assert ir.cycle.length_pulses is None and ir.cycle.template_id is None


def test_abc_without_a_key_infers_the_tonic():
    ir = check(parse_notes("X:1\nM:4/4\nL:1/4\nD E ^F G | A B ^c d | d2 A2 | D4 |", syntax="abc"), "abc")
    assert ir.tonic.method == "key_profile" and ir.tonic.nearest_note == "D4"
    assert "tonic_assumed" in warning_codes(ir) and ir.mode.mode_id is None


def test_abc_pickup_bar_keeps_pulse_zero_on_a_bar_line():
    ir = check(parse_notes("X:1\nM:4/4\nL:1/4\nK:G\nD | G A B c | d4 |"), "abc")
    assert pulses(ir)[:2] == [(12, 4), (16, 4)]  # the pickup is the last beat of a bar
    assert ir.cycle.phase_pulses == 12 and ir.events[0].onset_s == 0.0
    assert cents(ir)[:2] == [-500, 0]


def test_abc_broken_input_raises_with_a_line():
    broken = "X:1\nM:4/4\nL:1/4\nK:D\nD E F G |\nA B [ce d |\nD4 |\n"
    problems = problems_of(broken, syntax="abc")
    assert [(p.line, p.column, p.severity) for p in problems] == [(6, 1, "error")]
    assert "ABC" in problems[0].message and "Traceback" not in problems[0].message
    assert where(problems_of("X:1\nM:4/4\nL:banana\nK:D\nD E F G |\n", syntax="abc"), "E010") == [(3, 1)]
    assert where(problems_of("X:1\nT:empty\nM:4/4\nK:C\n", syntax="abc"), "E000") == [(1, 1)]
    # music21 misreads quarter-tone accidentals silently, so Orin refuses them by position.
    quarter = problems_of('X:1\nM:4/4\nL:1/4\nK:C\nC D "_/x" E | F _/E D C |\n')
    assert [(p.line, p.column, p.length) for p in quarter] == [(5, 17, 2)] and "Ed4" in quarter[0].message


def _xml_note(step, octave, alter=None, chord=False, tie=None, duration=1):
    chord_tag = "<chord/>" if chord else ""
    alter_tag = "" if alter is None else f"<alter>{alter}</alter>"
    tie_tag = "" if tie is None else f'<tie type="{tie}"/>'
    return (f"<note>{chord_tag}<pitch><step>{step}</step>{alter_tag}<octave>{octave}</octave></pitch>"
            f"<duration>{duration}</duration>{tie_tag}<type>quarter</type></note>")


def _musicxml(mode="<mode>major</mode>"):
    n = _xml_note
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>Melody</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions><key><fifths>2</fifths>{mode}</key>
        <time><beats>3</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>
      <direction><direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>90</per-minute></metronome>
        </direction-type><sound tempo="90"/></direction>
      {n('D', 4)}{n('F', 4, alter=1)}{n('A', 4, chord=True)}{n('E', 4, alter=-0.5)}
    </measure>
    <measure number="2">
      {n('D', 5, tie='start')}{n('D', 5, tie='stop')}<note><rest/><duration>1</duration><type>quarter</type></note>
    </measure>
  </part>
</score-partwise>
"""


def test_musicxml_pitches_onsets_and_headers():
    ir = check(parse_notes(_musicxml()), "musicxml")
    # F#4 + A4 is a chord (top note A kept); E half-flat keeps its 50 cents; the tied D5s are one note.
    assert cents(ir) == [0, 700, 150, 1200]
    assert pulses(ir) == [(0, 4), (4, 4), (8, 4), (12, 8)]
    assert [e.guessed for e in ir.events] == [False, True, False, False]
    assert warning_codes(ir).count("top_line_only") == 1
    assert ir.tempo.bpm == 90 and ir.tempo.confidence == 1.0
    assert ir.cycle.template_id == "3_4" and ir.cycle.length_pulses == 12 and ir.cycle.subdivision == 4
    assert ir.tonic.method == "declared" and ir.tonic.nearest_note == "D4" and ir.tonic.confidence == 1.0
    assert ir.mode.mode_id == "major"
    assert ir.source.duration_s == pytest.approx(6 * 60 / 90)  # the closing rest counts


def test_musicxml_bytes_mxl_and_bare_key_signature():
    xml = _musicxml(mode="")
    plain = parse_notes(xml.encode("utf-8"), filename="score.musicxml")
    # Two sharps without a mode: D is taken as the tonic, with less trust and a warning.
    assert plain.tonic.nearest_note == "D4" and plain.tonic.method == "declared" and plain.tonic.confidence < 1.0
    assert "key_mode_assumed" in warning_codes(plain) and plain.mode.mode_id is None
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as archive:
        archive.writestr("META-INF/container.xml",
                         '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        archive.writestr("score.xml", xml)
    zipped = check(parse_notes(packed.getvalue(), filename="score.mxl"), "musicxml")
    assert cents(zipped) == cents(plain) and pulses(zipped) == pulses(plain)
    carnatic = registry.get("carnatic").parse_notes(xml, tonic_hz=D3_HZ * 2)
    assert carnatic.source.format_hint == "carnatic" and carnatic.tonic.method == "user"
    assert carnatic.cycle.template_id is None and carnatic.cycle.length_pulses == 12  # no 3/4 tala template


def test_musicxml_pickup_bar_keeps_pulse_zero_on_a_bar_line():
    pickup = f'<measure number="0" implicit="yes"><attributes><divisions>1</divisions><key><fifths>1</fifths>' \
             f'<mode>major</mode></key><time><beats>3</beats><beat-type>4</beat-type></time></attributes>' \
             f'{_xml_note("D", 4)}</measure>'
    bar = f'<measure number="1">{_xml_note("G", 4)}{_xml_note("A", 4)}{_xml_note("B", 4)}</measure>'
    xml = ('<score-partwise version="3.1"><part-list><score-part id="P1"><part-name>M</part-name></score-part>'
           f'</part-list><part id="P1">{pickup}{bar}</part></score-partwise>')
    ir = check(parse_notes(xml), "musicxml")
    assert pulses(ir) == [(8, 4), (12, 4), (16, 4), (20, 4)] and ir.cycle.length_pulses == 12
    assert ir.cycle.phase_pulses == 8 and ir.events[0].onset_s == 0.0
    assert ir.tonic.nearest_note == "G4" and cents(ir) == [-500, 0, 200, 400]


def test_musicxml_broken_input_raises_with_a_position():
    broken = _musicxml().replace("</part>", "")
    problems = problems_of(broken)
    assert len(problems) == 1 and problems[0].severity == "error"
    assert problems[0].line == len(broken.splitlines()) and problems[0].column >= 1
    assert "well-formed" in problems[0].message
    for text in ("<score-timewise><part-list/></score-timewise>", "<html><body/></html>",
                 "<score-partwise><part-list/></score-partwise>"):
        problems = problems_of(text, syntax="musicxml")
        assert len(problems) == 1 and problems[0].line == 1 and "Traceback" not in problems[0].message
    with pytest.raises(NotesParseError):
        parse_notes(b"PK\x03\x04 this is not a real zip archive", filename="score.mxl")


# ---------------------------------------------------------------------------
# MIDI (mido)
# ---------------------------------------------------------------------------

def _midi_bytes(build, ticks_per_beat=480, tempo=500_000, meter=(4, 4)):
    import mido

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    if tempo is not None:
        track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    if meter is not None:
        track.append(mido.MetaMessage("time_signature", numerator=meter[0], denominator=meter[1], time=0))
    build(mido, mid, track)
    out = io.BytesIO()
    mid.save(file=out)
    return out.getvalue()


def _melody(mido, mid, track):
    def note(pitch, velocity, length=480, wait=0):
        track.append(mido.Message("note_on", note=pitch, velocity=velocity, time=wait))
        track.append(mido.Message("note_off", note=pitch, velocity=0, time=length))

    note(60, 100)
    track.append(mido.Message("pitchwheel", pitch=2048, time=0))  # +50 cents at the default 2-semitone range
    note(62, 80)
    track.append(mido.Message("pitchwheel", pitch=0, time=0))
    note(64, 60, length=960, wait=480)  # after a beat of silence
    track.append(mido.Message("pitchwheel", pitch=-4096, time=0))  # -100 cents
    note(67, 127, length=240)


def test_midi_cents_onsets_and_velocity():
    ir = check(parse_notes(_midi_bytes(_melody), tonic_hz=C4_HZ, filename="tune.mid"), "midi")
    assert cents(ir) == pytest.approx([0, 250, 400, 600], abs=0.01)
    assert pulses(ir) == [(0, 4), (4, 4), (12, 8), (20, 2)]
    assert [(e.onset_s, e.offset_s) for e in ir.events] == [(0.0, 0.5), (0.5, 1.0), (1.5, 2.5), (2.5, 2.75)]
    assert [e.velocity for e in ir.events] == [100, 80, 60, 127]
    assert not any(e.guessed for e in ir.events)
    assert ir.tempo.bpm == 120 and ir.tempo.confidence == 1.0
    assert ir.cycle.subdivision == 4 and ir.cycle.template_id == "4_4" and ir.cycle.length_pulses == 16
    assert ir.tonic.method == "user" and ir.source.format_hint == "western_classical"


def test_midi_tonic_is_inferred_unless_given():
    def scale(mido, mid, track):
        for pitch in (62, 64, 66, 67, 69, 71, 73, 74, 69, 66, 62):
            track.append(mido.Message("note_on", note=pitch, velocity=90, time=0))
            track.append(mido.Message("note_off", note=pitch, velocity=0, time=240))

    ir = check(parse_notes(_midi_bytes(scale, tempo=None, meter=None)), "midi")
    assert ir.tonic.method == "key_profile" and ir.tonic.nearest_note == "D4" and "tonic_assumed" in warning_codes(ir)
    assert cents(ir)[:4] == [0, 200, 400, 500] and pulses(ir)[:2] == [(0, 2), (2, 2)]
    # No tempo in the file: MIDI's own default, flagged.
    assert ir.tempo.bpm == 120 and ir.tempo.confidence < 1.0 and "tempo_assumed" in warning_codes(ir)
    assert ir.cycle.length_pulses is None
    slower = parse_notes(_midi_bytes(scale), tempo_bpm=60)
    assert slower.tempo.bpm == 60 and slower.events[1].onset_s == pytest.approx(0.5)


def test_midi_bend_range_from_rpn_zero():
    def build(mido, mid, track):
        for control, value in ((101, 0), (100, 0), (6, 12), (38, 0)):
            track.append(mido.Message("control_change", control=control, value=value, time=0))
        track.append(mido.Message("pitchwheel", pitch=4096, time=0))  # half of 12 semitones
        track.append(mido.Message("note_on", note=60, velocity=64, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))

    ir = parse_notes(_midi_bytes(build), tonic_hz=C4_HZ)
    assert cents(ir) == pytest.approx([600], abs=0.01)


def test_midi_bend_with_and_inside_a_note():
    def build(mido, mid, track):
        track.append(mido.Message("note_on", note=60, velocity=64, time=0))
        track.append(mido.Message("pitchwheel", pitch=2048, time=0))  # sent with the note: it is the note's pitch
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
        track.append(mido.Message("pitchwheel", pitch=0, time=0))
        track.append(mido.Message("note_on", note=62, velocity=64, time=0))
        track.append(mido.Message("pitchwheel", pitch=4096, time=240))  # bent while it sounds
        track.append(mido.Message("note_off", note=62, velocity=0, time=240))
        track.append(mido.Message("pitchwheel", pitch=0, time=0))

    ir = check(parse_notes(_midi_bytes(build), tonic_hz=C4_HZ), "midi")
    assert cents(ir) == pytest.approx([50, 200], abs=0.01)  # the pitch at the start of each note
    assert [e.guessed for e in ir.events] == [False, True]
    assert warning_codes(ir).count("bend_inside_note") == 1


def test_midi_overlaps_keep_the_highest_note_and_warn_once():
    def chords(mido, mid, track):
        for low, high in ((60, 67), (62, 69)):
            track.append(mido.Message("note_on", note=low, velocity=70, time=0))
            track.append(mido.Message("note_on", note=high, velocity=90, time=0))
            track.append(mido.Message("note_off", note=low, velocity=0, time=480))
            track.append(mido.Message("note_off", note=high, velocity=0, time=0))
        track.append(mido.Message("note_on", note=64, velocity=50, time=0))
        track.append(mido.Message("note_off", note=64, velocity=0, time=480))

    ir = check(parse_notes(_midi_bytes(chords), tonic_hz=C4_HZ), "midi")
    assert cents(ir) == [700, 900, 400] and [e.velocity for e in ir.events] == [90, 90, 50]
    assert [e.guessed for e in ir.events] == [True, True, False]
    assert warning_codes(ir).count("top_line_only") == 1


def test_midi_legato_overlap_is_not_harmony():
    def legato(mido, mid, track):
        track.append(mido.Message("note_on", note=64, velocity=80, time=0))
        track.append(mido.Message("note_on", note=62, velocity=80, time=470))  # starts just before 64 ends
        track.append(mido.Message("note_off", note=64, velocity=0, time=20))
        track.append(mido.Message("note_off", note=62, velocity=0, time=470))

    ir = check(parse_notes(_midi_bytes(legato), tonic_hz=C4_HZ), "midi")
    assert cents(ir) == [400, 200] and not any(e.guessed for e in ir.events)
    assert "top_line_only" not in warning_codes(ir)


def test_midi_picks_the_busiest_line_and_follows_the_tempo_map():
    def build(mido, mid, track):
        track.append(mido.Message("note_on", note=36, velocity=100, channel=9, time=0))  # drums: passed over
        track.append(mido.Message("note_off", note=36, velocity=0, channel=9, time=10))
        track.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=950))  # 60 bpm from beat 2
        bass, lead = mido.MidiTrack(), mido.MidiTrack()
        mid.tracks.extend([bass, lead])
        bass.append(mido.Message("note_on", note=40, velocity=90, channel=1, time=0))
        bass.append(mido.Message("note_off", note=40, velocity=0, channel=1, time=1920))
        for pitch in (72, 74, 76):
            lead.append(mido.Message("note_on", note=pitch, velocity=90, channel=0, time=0))
            lead.append(mido.Message("note_off", note=pitch, velocity=0, channel=0, time=480))

    ir = check(parse_notes(_midi_bytes(build), tonic_hz=C4_HZ * 2), "midi")
    assert cents(ir) == [0, 200, 400] and pulses(ir) == [(0, 4), (4, 4), (8, 4)]
    assert {"one_line_kept", "tempo_changes"} <= set(warning_codes(ir))
    assert ir.tempo.bpm == 120
    assert [(e.onset_s, e.offset_s) for e in ir.events] == [(0.0, 0.5), (0.5, 1.0), (1.0, 2.0)]


def test_midi_broken_input_raises():
    for data in (b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0MTrk\x00\x00\x00\x40\x00\x90", b"MThd"):
        problems = problems_of(data)
        assert len(problems) == 1 and "MIDI" in problems[0].message
    assert where(problems_of(_midi_bytes(lambda mido, mid, track: None)), "E000") == [(1, 1)]
    with pytest.raises(IngestError):
        parse_notes("MThd as text", syntax="midi")


# ---------------------------------------------------------------------------
# Examples, and one validity sweep over every syntax
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, syntax, events", [
    ("mohanam.sargam", "sargam", 35),
    ("scale.abc", "abc", 16),
    ("rast.notes", "note_names", 23),
    ("riff.notes", "note_names", 21),
])
def test_example_files_parse(name, syntax, events):
    data = (EXAMPLES / name).read_bytes()
    assert len(data.splitlines()) < 20
    ir = check(parse_notes(data, filename=name), syntax)
    assert len(ir.events) == events and ir.source.filename == name
    assert ir.tonic.method == "declared" and "tonic_assumed" not in warning_codes(ir)
    assert parse_notes(data.decode("utf-8"), filename=name).events == ir.events


def test_examples_say_what_they_claim():
    mohanam = parse_notes((EXAMPLES / "mohanam.sargam").read_text())
    assert mohanam.mode.mode_id == "mohanam" and mohanam.cycle.template_id == "adi"
    assert {e.pitch_cents % 1200 for e in mohanam.events} == {0, 204, 386, 702, 884}
    assert {"oscillation", "glide"} <= {e.ornament.kind for e in mohanam.events}
    assert mohanam.source.duration_s == pytest.approx(3 * 16 * mohanam.cycle.pulse_s)  # three full cycles
    assert "not been reviewed by a musician" in (EXAMPLES / "mohanam.sargam").read_text()
    scale = parse_notes((EXAMPLES / "scale.abc").read_text())
    assert scale.tonic.nearest_note == "D4" and scale.mode.mode_id == "major"
    rast = parse_notes((EXAMPLES / "rast.notes").read_text())
    assert rast.tonic.nearest_note == "C4" and {350, 1050} <= {e.pitch_cents % 1200 for e in rast.events}
    riff = parse_notes((EXAMPLES / "riff.notes").read_text())
    assert riff.tonic.nearest_note == "E2" and riff.cycle.template_id == "4_4"
    assert {e.pitch_cents % 1200 for e in riff.events} == {0, 300, 500, 700, 1000}  # E G A B D


@pytest.mark.parametrize("format_id", ["carnatic", "western_classical", "western_pop", "rock", "arabic_maqam",
                                       "persian_dastgah"])
def test_every_format_gets_a_valid_ir_from_every_syntax(format_id):
    adapter = registry.get(format_id)
    inputs = {"sargam": GOOD_SARGAM, "note_names": "D4 E4 F#4:2 r A4~", "abc": ABC_TUNE, "musicxml": _musicxml(),
              "midi": _midi_bytes(_melody)}
    for syntax, data in inputs.items():
        ir = check(adapter.parse_notes(data, syntax=syntax), syntax)
        assert ir.source.format_hint == format_id
        assert check(adapter.parse_notes(data), syntax).events == ir.events  # detected, not told


# ---------------------------------------------------------------------------
# Round trips through the engine and the text renderers (switch on once those exist)
# ---------------------------------------------------------------------------

def _body(text):
    """Text without comments and blank lines, whitespace collapsed."""
    lines = [re.sub(r"(^|\s)#.*$", "", line).strip() for line in text.splitlines()]
    return "\n".join(" ".join(line.split()) for line in lines if line)


def _music(ir):
    """Pitch, timing in beats and ornament of every note: what a round trip must keep."""
    sub = ir.cycle.subdivision
    return [(round(e.pitch_cents, 1), e.onset_pulse / sub, e.duration_pulses / sub, e.ornament.kind, e.ornament.shape)
            for e in ir.events]


def _through_engine(text, format_id, syntax, kind, **render_opts):
    pytest.importorskip("orin.formats.engine")
    pytest.importorskip(f"orin.render.{ {'sargam': 'sargam', 'names': 'names_text'}[kind] }")
    adapter = registry.get(format_id)
    ir = adapter.parse_notes(text, syntax=syntax)
    score = adapter.ir_to_format(ir).score
    out = adapter.render(score, kind, **render_opts).text
    return ir, out, adapter.parse_notes(out, syntax=syntax)


SARGAM_CANONICAL = """\
tonic: 146.83 Hz
raga: mohanam
tala: adi
tempo: 80
units_per_beat: 4

S , R , G , P , D , S' , , , , , | D , P , G~ , R , | S. , , , _ _ _ _ ||
G/ , , , P , D , S' , , , D\\ , P , | G , R , S , R , | S , , , , , , , ||
"""

NAMES_CANONICAL = """\
tonic: D4
mode: major
meter: 4/4
tempo: 100
units_per_beat: 4

D4:4 E4:4 F#4:4 G4:4 | A4:8 r:4 B4:2 C#5:2 |
D5:12 A4:4/ | D4:16 ||
"""


def test_sargam_round_trip_through_the_engine():
    ir, out, again = _through_engine(SARGAM_CANONICAL, "carnatic", "sargam", "sargam", pretty=False)
    assert _music(again) == _music(ir)
    assert again.tonic.hz == pytest.approx(ir.tonic.hz, abs=0.01)
    assert (again.mode.mode_id, again.cycle.template_id, again.tempo.bpm) == ("mohanam", "adi", 80)
    assert _body(out) == _body(SARGAM_CANONICAL)


def test_sargam_round_trip_with_pretty_octave_dots():
    ir, out, again = _through_engine(SARGAM_CANONICAL, "carnatic", "sargam", "sargam")
    assert "̇" in out and "̣" in out  # dots above and below
    assert _music(again) == _music(ir)


def test_sargam_round_trip_from_looser_typing():
    loose = "tonic: D3\nraga: mohanam\ntala: adi\nS R2 G3 P D2 S' ; | (S' D2 P G3) R2~ S. | S , _ _ ||"
    ir, out, again = _through_engine(loose, "carnatic", "sargam", "sargam")
    assert _music(again) == _music(ir)


def test_names_round_trip_through_the_engine():
    ir, out, again = _through_engine(NAMES_CANONICAL, "western_classical", "note_names", "names")
    assert _music(again) == _music(ir)
    assert again.tonic.nearest_note == "D4" and again.mode.mode_id == "major" and again.cycle.template_id == "4_4"
    assert _body(out) == _body(NAMES_CANONICAL)


def test_names_round_trip_keeps_half_flats():
    rast = "tonic: C4\nmode: rast\ntempo: 90\nunits_per_beat: 2\nC4 D4 Ed4 F4 G4:2 A4 Bd4 C5:2 r:2"
    ir, out, again = _through_engine(rast, "arabic_maqam", "note_names", "names")
    assert "Ed4" in out and "Bd4" in out
    assert _music(again) == _music(ir)


def test_orins_own_midi_reads_back_with_every_note():
    """Orin's MIDI alternates two channels so each note can carry its own bend; the parser
    must read that as one melody, microtones included."""
    pytest.importorskip("orin.formats.engine")
    pytest.importorskip("orin.render.midi")
    from orin.convert import convert
    from orin.eval import fixtures

    ir = fixtures.phrase_for("arabic_maqam")  # has half-flat degrees
    conversion = convert(ir, "arabic_maqam", kinds=("midi",))
    written = conversion.score.notes
    back = parse_notes(conversion.renders["midi"].data, syntax="midi", tonic_hz=conversion.score.options.tonic_hz)
    assert len(back.events) == len(written)
    assert not [w for w in back.meta.warnings if w.code == "one_line_kept"]
    for event, note in zip(back.events, written):
        assert abs(event.pitch_cents - note.cents) < 2.0
