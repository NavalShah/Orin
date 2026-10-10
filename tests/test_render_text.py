"""Text renderers: sargam, note names, ABC, guitar tab and the plain-language description."""
from __future__ import annotations

import importlib
import re

import pytest

from orin.formats import registry
from orin.render import RENDER_INFO, RenderResult, render
from orin.render import abc as abc_renderer
from orin.render.tab import fold, place
try:  # the hand builder lives in test_render_spell.py
    from test_render_spell import BEND, DROPPED, SLIDE_UP, WAVY, make_score
except ImportError:  # tests/ imported as a package
    from tests.test_render_spell import BEND, DROPPED, SLIDE_UP, WAVY, make_score

DOT_ABOVE, DOT_BELOW = "̇", "̣"

# Two cycles of Adi (32 units each). n9 is outside Mohanam; n10 is in the octave below.
MOHANAM = [
    ("S", 1, 0, 4), ("R2", 2, 204, 4), ("G3", 3, 386, 4), ("P", 5, 702, 4), ("D2", 6, 884, 4, [WAVY]),
    ("S", 1, 1200, 8), ("D2", 6, 884, 2), ("P", 5, 702, 2, [DROPPED]),
    ("N3", 7, 1088, 2, [], False), ("D2", 6, -316, 2, [SLIDE_UP]), (None, 0, 0, 4), ("S", 1, 0, 8),
]
RAST = [
    ("1", 1, 0, 2), ("2", 2, 200, 2), ("3-", 3, 350, 2, [SLIDE_UP]), ("4", 4, 500, 2), ("5", 5, 700, 4),
    ("7-", 7, 1050, 2, [WAVY]), ("1", 1, 1200, 5), (None, 0, 0, 1), ("7-", 7, 1050, 2), ("6", 6, 900, 6),
]
SHUR = [("1", 1, 0, 2), ("2 koron", 2, 135, 2), ("b3", 3, 295, 3), ("4", 4, 499, 1), ("5", 5, 703, 4),
        (None, 0, 0, 2), ("2 koron", 2, 135, 1), ("1", 1, 0, 6)]
# C major in 4/4: n5 (G) crosses the bar line, then F sharp, F natural, F sharp inside one bar.
C_MAJOR = [("1", 1, 0, 4), ("2", 2, 200, 4), ("3", 3, 400, 4), ("4", 4, 500, 2), ("5", 5, 700, 4),
           ("#4", 4, 600, 2), ("4", 4, 500, 2), ("#4", 4, 600, 2), (None, 0, 0, 2), ("1", 1, 1200, 6)]
RIFF = [("1", 1, 0, 2), ("b3", 3, 300, 2, [BEND]), ("4", 4, 500, 2), ("5", 5, 700, 2), ("b7", 7, 1000, 4, [SLIDE_UP]),
        ("1", 1, 1200, 4), ("1", 1, -1200, 4)]


def mohanam(mode="mohanam", template="adi"):
    return make_score("carnatic", MOHANAM, template=template, mode=mode, tonic_hz=146.83, tonic="D", octave=3)


def rast():
    return make_score("arabic_maqam", RAST, template="maqsum", mode="rast", pulse_s=0.25, tonic_hz=146.83,
                      transposition=-1000.0)


def shur():
    return make_score("persian_dastgah", SHUR, tonic="D", mode="shur", tonic_hz=293.66, subdivision=2)


def c_major(template="4_4"):
    return make_score("western_classical", C_MAJOR, template=template, mode="major")


def riff():
    return make_score("rock", RIFF, tonic="E", octave=3, template="4_4", mode="minor_pentatonic", tonic_hz=164.81)


def slices(out: RenderResult) -> dict[str, list[str]]:
    """note id -> the text each of its tokens covers."""
    found: dict[str, list[str]] = {}
    for token in out.tokens:
        assert 0 <= token.start < token.end <= len(out.text)
        found.setdefault(token.note_id, []).append(out.text[token.start:token.end])
    return found


def body(text: str) -> list[str]:
    """Lines that carry notes: not comments, not headers, not blank."""
    return [line for line in text.splitlines()
            if line.strip() and not line.startswith("#") and not re.match(r"^[A-Za-z_]+:", line)]


# ---------------------------------------------------------------------------
# Sargam
# ---------------------------------------------------------------------------

def test_sargam_with_a_raga_prints_bare_letters_and_tala_bars():
    score, meta = mohanam()
    out = render(score, meta, "sargam")
    assert out.kind == "sargam" and out.media_type == "text/plain"
    assert out.text.startswith("tonic: 146.83 Hz\nraga: mohanam\ntala: adi\ntempo: 120\nunits_per_beat: 4\n")
    lines = body(out.text)
    assert lines[0] == f"S , , ,  R , , ,  G , , ,  P , , , | D~ , , ,  S{DOT_ABOVE} , , , | , , , ,  D , P , ||"
    assert lines[1] == f"N3* , D{DOT_BELOW}/ ,  _ _ _ _  S , , ,  , , , , | _ _ _ _  _ _ _ _ | _ _ _ _  _ _ _ _ ||"
    assert len(lines) == 2
    for line in lines:  # Adi is 4 + 2 + 2 beats of 4 units
        groups = line.removesuffix(" ||").split(" | ")
        assert [len(g.split()) for g in groups] == [16, 8, 8]
    got = slices(out)
    assert [got[n.id] for n in score.notes] == [
        ["S"], ["R"], ["G"], ["P"], ["D~"], [f"S{DOT_ABOVE}"], ["D"], ["P"], ["N3*"], [f"D{DOT_BELOW}/"], ["S"]]


def test_sargam_without_a_raga_numbers_every_variable_swara():
    score, meta = mohanam(mode=None)
    out = render(score, meta, "sargam")
    assert "raga:" not in out.text and "*" not in out.text.split("\n\n")[1]
    got = slices(out)
    assert [got[n.id][0] for n in score.notes] == [
        "S", "R2", "G3", "P", "D2~", f"S{DOT_ABOVE}", "D2", "P", "N3", f"D{DOT_BELOW}2/", "S"]


def test_sargam_ascii_octave_marks():
    score, meta = mohanam()
    out = render(score, meta, "sargam", pretty=False)
    assert DOT_ABOVE not in out.text and DOT_BELOW not in out.text
    got = slices(out)
    assert got["n6"] == ["S'"] and got["n10"] == ["D./"] and got["n5"] == ["D~"]
    plain = render(*mohanam(mode=None), "sargam", pretty=False)
    assert slices(plain)["n10"] == ["D2./"]
    two_up, meta = make_score("carnatic", [("P", 5, 702 + 2400, 1), ("P", 5, 702 - 2400, 1)], tonic_hz=146.83)
    assert slices(render(two_up, meta, "sargam", pretty=False)) == {"n1": ["P''"], "n2": ["P.."]}
    assert slices(render(two_up, meta, "sargam")) == {"n1": ["P" + DOT_ABOVE * 2], "n2": ["P" + DOT_BELOW * 2]}


def test_sargam_ends_with_the_legend_and_the_draft_notice():
    out = render(*mohanam(), "sargam")
    tail = out.text.rstrip("\n").split("\n\n")[-1].splitlines()
    assert all(line.startswith("#") for line in tail)
    legend = " ".join(tail)
    assert "~" in legend and "/" in legend and "\\" in legend
    assert "Orin's own marks" in legend and "not standard" in legend
    assert "unreviewed draft" in legend
    assert "outside the chosen raga" in legend
    assert "outside the chosen raga" not in render(*mohanam(mode=None), "sargam").text


def test_sargam_in_free_time_has_no_bars_and_wraps():
    score, meta = mohanam(template=None)
    out = render(score, meta, "sargam")
    assert "tala:" not in out.text and "Free rhythm" in out.text
    lines = body(out.text)
    assert len(lines) > 1 and not any("|" in line for line in lines)
    assert all(len(line.split()) <= 16 for line in lines)
    assert sum(len(line.split()) for line in lines) == sum(spec[3] for spec in MOHANAM)


def test_sargam_reads_back_through_the_parser():
    pytest.importorskip("orin.ingest.notes_sargam")
    from orin.ingest.notes import parse_notes

    for mode, template, pretty in (("mohanam", "adi", True), ("mohanam", "adi", False), (None, "adi", True),
                                   (None, None, True), ("mohanam", "misra_chapu", True)):
        score, meta = mohanam(mode=mode, template=template)
        ir = parse_notes(render(score, meta, "sargam", pretty=pretty).text, format_id="carnatic")
        assert ir.tonic.hz == pytest.approx(146.83, abs=0.01)
        assert ir.cycle.template_id == template
        assert ir.mode.mode_id == mode
        assert [(e.pitch_cents, e.onset_pulse, e.duration_pulses) for e in ir.events] == \
            [(n.cents, n.start_pulse, n.duration_pulses) for n in score.notes]
        assert [e.onset_s for e in ir.events] == pytest.approx([n.onset_s for n in score.notes], abs=1e-6)
        assert [e.ornament.kind for e in ir.events if e.ornament.kind] == ["oscillation", "glide"]
        assert not [w for w in ir.meta.warnings if w.code == "W101"]


# ---------------------------------------------------------------------------
# Note names
# ---------------------------------------------------------------------------

def test_names_for_a_rast_phrase():
    score, meta = rast()
    out = render(score, meta, "names")
    assert out.kind == "names"
    for header in ("tonic: C4", "mode: rast", "meter: 4/4", "tempo: 120", "units_per_beat: 2"):
        assert f"\n{header}\n" in out.text
    assert body(out.text) == ["C4:2 D4:2 Ed4:2/ F4:2 | G4:4 Bd4:2~ C5:5 |", "r Bd4:2 A4:6 | r:4 ||"]
    got = slices(out)
    assert [got[n.id] for n in score.notes] == [
        ["C4:2"], ["D4:2"], ["Ed4:2/"], ["F4:2"], ["G4:4"], ["Bd4:2~"], ["C5:5"], ["Bd4:2"], ["A4:6"]]
    # Solfege with the tradition's word for the accidental, as a comment above each line of notes.
    lines = out.text.splitlines()
    above = lines[lines.index("C4:2 D4:2 Ed4:2/ F4:2 | G4:4 Bd4:2~ C5:5 |") - 1]
    assert above.startswith("# Do  Re  Mi half-flat  Fa") and "Si half-flat" in above
    # The sounding tonic, and that the writing is at a conventional pitch.
    assert "146.83 Hz" in out.text and "conventional pitch" in out.text and "1000 cents lower" in out.text
    assert "half-flat" in out.text and "unreviewed draft" in out.text


def test_names_for_persian_say_finalis_and_koron():
    score, meta = shur()
    out = render(score, meta, "names")
    assert "finalis" in out.text and "tonic: D4" in out.text and "The tonic" not in out.text
    assert "meter:" not in out.text and not any("|" in line for line in body(out.text))
    got = slices(out)
    assert got["n2"] == ["Ep4:2"] and got["n6"] == ["Ep4"] and got["n3"] == ["F4:3"]
    assert "Mi koron" in out.text and "p = koron" in out.text
    assert "r:2" in body(out.text)[0].split()
    assert "conventional pitch" not in out.text  # written where it sounds


def test_names_for_western_have_no_solfege_and_state_a_tuning_offset():
    score, meta = make_score("western_classical", C_MAJOR, template="4_4", tonic="D", tonic_hz=300.0,
                             transposition=37.0)
    out = render(score, meta, "names")
    assert "# Do" not in out.text and "tonic: D4" in out.text
    assert "300.00 Hz" in out.text and "37 cents above" in out.text and "conventional" not in out.text
    assert slices(out)["n6"] == ["G#4:2"] and slices(out)["n9"] == ["D5:6"]
    assert body(out.text)[-1].endswith("||")


def test_names_read_back_through_the_parser():
    pytest.importorskip("orin.ingest.notes_names")
    from orin.ingest.notes import parse_notes

    for (score, meta), tolerance in ((rast(), 0.01), (shur(), 30.0), (c_major(), 0.01), (c_major(None), 0.01)):
        ir = parse_notes(render(score, meta, "names").text, format_id=meta.id, syntax="note_names")
        assert [(e.onset_pulse, e.duration_pulses) for e in ir.events] == \
            [(n.start_pulse, n.duration_pulses) for n in score.notes]
        assert [e.pitch_cents for e in ir.events] == pytest.approx([n.cents for n in score.notes], abs=tolerance)
        assert [e.onset_s for e in ir.events] == pytest.approx([n.onset_s for n in score.notes], abs=1e-6)
        assert ir.mode.mode_id == score.options.mode_id


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------

def test_abc_for_c_major_with_a_tie_across_the_bar_line():
    score, meta = c_major()
    out = render(score, meta, "abc")
    assert out.kind == "abc" and out.ext == "abc"
    lines = out.text.splitlines()
    assert lines[0] == "X:1" and lines[1].startswith("T:")
    assert {"M:4/4", "L:1/16", "Q:1/4=120", "K:C"} <= set(lines)
    assert lines.index("K:C") == len(lines) - 2  # K: ends the header
    assert lines[-1] == "C4 D4 E4 F2G2- | G2^F2 =F2^F2 z2c6 |]"
    got = slices(out)
    assert [got[n.id] for n in score.notes] == [
        ["C4"], ["D4"], ["E4"], ["F2"], ["G2", "G2"], ["^F2"], ["=F2"], ["^F2"], ["c6"]]


def test_abc_accidentals_are_relative_to_the_key():
    score, meta = make_score("western_classical", [
        ("1", 1, 0, 4), ("3", 3, 400, 4), ("7", 7, 1100, 4), ("b7", 7, 1000, 4), ("7", 7, -100, 8), ("3", 3, -800, 8),
    ], tonic="D", template="4_4", mode="major")
    out = render(score, meta, "abc")
    assert "K:D" in out.text.splitlines()
    # F sharp and C sharp come from the key signature; the C natural needs a sign, and the
    # C sharp in the next bar needs none because the bar line cancelled it.
    assert out.text.splitlines()[-1] == "D4 F4 c4 =c4 | C8 F,8 |]"
    minor, meta = make_score("rock", RIFF, tonic="E", octave=3, template="4_4", mode="minor_pentatonic")
    lines = render(minor, meta, "abc").text.splitlines()
    assert "K:Em" in lines and lines[-1] == 'E,2"^b"G,2 A,2B,2 "^/"D4 E4 | E,,4 z12 |]'


def test_abc_key_field_names_the_written_tonic():
    dorian = [("1", 1, 0, 4), ("2", 2, 200, 4), ("b3", 3, 300, 4), ("4", 4, 500, 4),
              ("5", 5, 700, 4), ("6", 6, 900, 4), ("b7", 7, 1000, 4), ("1", 1, 1200, 4)]
    score, meta = make_score("western_classical", dorian, tonic="D", template="4_4", mode="dorian")
    lines = render(score, meta, "abc").text.splitlines()
    assert "K:Ddor" in lines and lines[-1] == "D4 E4 F4 G4 | A4 B4 c4 d4 |]"  # no accidentals needed
    assert any(line.startswith("N:Written tonic: D4.") and "not a claim about the mode" in line for line in lines)
    flat, meta = make_score("western_pop", dorian[:5], tonic="B", alter=-100.0, octave=3, template="4_4")
    assert "K:Bbm" in render(flat, meta, "abc").text.splitlines()


def test_abc_in_free_time_has_no_meter_and_no_bars():
    score, meta = c_major(template=None)
    out = render(score, meta, "abc")
    lines = out.text.splitlines()
    assert "M:none" in lines and not [line for line in lines if line.startswith("Q:")]
    tune = lines[lines.index("K:C") + 1:]
    assert tune and not any("|" in line for line in tune)
    # With no bar lines an accidental is repeated on every altered note.
    assert " ".join(tune).split() == ["C4", "D4", "E4", "F2", "G4", "^F2", "=F2", "^F2", "z2", "c6"]
    assert slices(out)["n8"] == ["^F2"]


def test_abc_says_when_it_cannot_write_quarter_tones():
    score, meta = rast()
    out = abc_renderer.render(score, meta)
    assert any("quarter tones" in w for w in out.warnings)
    assert not render(*c_major(), "abc").warnings


def test_abc_reads_back_through_the_parser():
    pytest.importorskip("music21")
    pytest.importorskip("orin.ingest.notes_m21")
    from orin.ingest.notes import parse_notes

    score, meta = c_major()
    ir = parse_notes(render(score, meta, "abc").text, format_id="western_classical")
    assert [round(e.pitch_cents) for e in ir.events] == [round(n.cents) for n in score.notes]
    assert [e.onset_s for e in ir.events] == pytest.approx([n.onset_s for n in score.notes], abs=1e-6)
    assert [e.offset_s - e.onset_s for e in ir.events] == pytest.approx([n.duration_s for n in score.notes], abs=1e-6)


# ---------------------------------------------------------------------------
# Guitar tab
# ---------------------------------------------------------------------------

def test_tab_fret_arithmetic():
    assert place(40, None) == (5, 0)  # E2: open sixth string
    assert place(45, None) == (4, 0)  # A2: open fifth string
    assert place(64, None) == (0, 0)  # E4: open first string, the lowest position
    assert place(52, None) == (3, 2)  # E3: fourth string, fret 2
    assert place(57, 2) == (2, 2)  # A3 stays at fret 2 on the third string
    assert place(59, 2) == (1, 0)  # B3: frets 0 and 4 are equally near, the lower wins
    assert place(64, 12) == (3, 14)  # near fret 12 the same E4 is on the fourth string
    assert place(79, None) == (0, 15)
    assert fold(28) == 40 and fold(39) == 51 and fold(100) == 76 and fold(60) == 60


def test_tab_for_a_riff_with_bends_and_slides():
    score, meta = riff()
    out = render(score, meta, "tab")
    assert out.kind == "tab" and not out.warnings
    got = slices(out)
    assert [got[n.id] for n in score.notes] == [["2"], ["0b"], ["2"], ["0"], ["3/"], ["5"], ["0"]]
    assert [t.note_id for t in out.tokens] == [n.id for n in score.notes]
    systems = [block.splitlines() for block in out.text.strip().split("\n\n")[1:]]
    assert len(systems) == 2  # two bars of 4/4
    for system in systems:
        assert [line[:2] for line in system] == ["e|", "B|", "G|", "D|", "A|", "E|"]
        assert len({len(line) for line in system}) == 1 and all(line.endswith("|") for line in system)
        assert all(set(line[2:]) <= set("-|0123456789b/") for line in system)
    line_of = {note_id: out.text[:t.start].splitlines()[-1][:1] for t in out.tokens for note_id in [t.note_id]}
    assert [line_of[n.id] for n in score.notes] == ["D", "G", "G", "B", "B", "B", "E"]
    assert "b = bend" in out.text and "/ = slide up" in out.text and "standard tuning" in out.text


def test_tab_moves_out_of_range_notes_and_warns():
    score, meta = make_score("rock", [("1", 1, 0, 4), ("1", 1, -1200, 4)], tonic="C", octave=3, template="4_4")
    out = render(score, meta, "tab")
    assert len(out.warnings) == 1 and "moved by octaves" in out.warnings[0] and out.warnings[0].startswith("1 note ")
    assert slices(out) == {"n1": ["3"], "n2": ["3"]}  # C2 is written as C3, fifth string fret 3
    free, meta = make_score("rock", RIFF, tonic="E", octave=3)
    assert "|-" in render(free, meta, "tab").text and "free rhythm" in render(free, meta, "tab").text


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------

def test_describe_a_measured_carnatic_score():
    score, meta = mohanam()
    text = render(score, meta, "describe").text
    assert "11 notes" in text and "about 6 seconds" in text
    assert "starts on the Sa" in text and "one octave above the Sa" in text and "ends on the Sa" in text
    assert "2 steps below the Sa" in text
    assert "cycles of 8 beats" in text and "Adi" in text and "120 beats per minute" in text
    assert "1 note with a kampita-like oscillation" in text and "1 note with a slide" in text
    assert "1 ornament in the source could not be written" in text
    assert "Mohanam" in text and "not an identification" in text and "1 of its 11 notes falls outside" in text
    assert "\n" not in text.strip() and not re.search(r"[#|~*]", text)  # plain sentences only


def test_describe_free_rhythm_and_no_mode():
    score, meta = mohanam(mode=None, template=None)
    text = render(score, meta, "describe").text
    assert "free rhythm, with no bar lines" in text and "cycles of" not in text
    assert "Mohanam" not in text and "mode" not in text
    persian = render(*shur(), "describe").text
    assert "finalis" in persian and "tonic" not in persian and "free rhythm" in persian
    assert "Dastgah-e Shur" in persian and "No ornaments are marked." in persian


def test_describe_uses_neutral_names_when_the_style_was_not_chosen():
    auto = WAVY.model_copy(update={"local_name": None})
    score, meta = make_score("carnatic", [("S", 1, 0, 4, [auto]), ("P", 5, 702, 4, [auto])], template="adi")
    text = render(score, meta, "describe").text
    assert "2 notes with an oscillation" in text
    assert "kampita" not in text


# ---------------------------------------------------------------------------
# Odd music, token spans, and real engine output
# ---------------------------------------------------------------------------

def test_every_renderer_module_exposes_render():
    for kind, (module, *_rest) in RENDER_INFO.items():
        assert callable(importlib.import_module(f"orin.render.{module}").render), kind


@pytest.mark.parametrize("format_id", ["western_classical", "western_pop", "rock", "carnatic", "arabic_maqam",
                                       "persian_dastgah"])
@pytest.mark.parametrize("measured", [True, False])
def test_every_renderer_handles_empty_one_note_and_leaping_scores(format_id, measured):
    meta = registry.get(format_id).META
    template = meta.rhythm_templates[0].id if measured else None
    name = meta.grid[0].name
    for notes in ([], [(name, 1, 0.0, 3)], [(name, 1, -3600.0, 1), (name, 1, 4800.0, 70), (name, 1, 0.0, 1)]):
        score, _ = make_score(format_id, notes, template=template)
        for kind in meta.renderers:
            if kind == "svg":
                pytest.importorskip("verovio")
            out = render(score, meta, kind)
            assert isinstance(out, RenderResult) and out.kind == kind
            assert out.text or out.data or out.pages
            if kind in ("sargam", "names", "abc", "tab"):
                assert sorted(slices(out)) == sorted(n.id for n in score.notes)


@pytest.mark.parametrize("builder", [mohanam, rast, shur, c_major, riff])
def test_token_spans_cover_non_blank_text_and_do_not_overlap(builder):
    score, meta = builder()
    for kind in ("sargam", "names", "abc", "tab"):
        if kind not in meta.renderers:
            continue
        out = render(score, meta, kind)
        spans = sorted((t.start, t.end) for t in out.tokens)
        assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:]))
        assert {t.note_id for t in out.tokens} == {n.id for n in score.notes}
        for token in out.tokens:
            piece = out.text[token.start:token.end]
            assert piece.strip() == piece and piece and "\n" not in piece and "|" not in piece


def test_real_engine_output_renders_and_sargam_round_trips():
    pytest.importorskip("orin.formats.engine")
    pytest.importorskip("orin.ingest.notes_sargam")
    from orin.formats.base import ConvertOptions
    from orin.ingest.notes import parse_notes

    text = ("tonic: D3\nraga: mohanam\ntala: adi\ntempo: 80\nunits_per_beat: 2\n"
            "S , R , G , P , | D~ , S' , | , , D , ||\n_ _ G , P , D , | S' , R' , | , , S' , ||\n"
            "D , P , , , G/ , | R , S , | , , , , ||\n")
    carnatic = registry.get("carnatic")
    ir = parse_notes(text, format_id="carnatic")

    def shape(score):
        return [(n.name, n.octave, n.start_pulse, n.duration_pulses, [o.symbol for o in n.ornaments])
                for n in score.notes]

    first = carnatic.ir_to_format(ir, ConvertOptions(mode_id="mohanam")).score
    for pretty in (True, False):
        again = parse_notes(carnatic.render(first, "sargam", pretty=pretty).text, format_id="carnatic")
        assert shape(carnatic.ir_to_format(again, ConvertOptions(mode_id="mohanam")).score) == shape(first)

    for format_id, mode in (("western_classical", None), ("western_pop", None), ("rock", None),
                            ("arabic_maqam", "rast"), ("persian_dastgah", "shur")):
        adapter = registry.get(format_id)
        score = adapter.ir_to_format(ir, ConvertOptions(mode_id=mode)).score
        for kind in adapter.META.renderers:
            if kind in ("svg", "musicxml", "midi"):
                continue  # covered in test_render_staff.py and test_render_midi.py
            out = adapter.render(score, kind)
            assert out.text
            if kind != "describe":
                assert {t.note_id for t in out.tokens} == {n.id for n in score.notes}
        names = adapter.render(score, "names").text
        back = adapter.ir_to_format(parse_notes(names, format_id=format_id, syntax="note_names"),
                                    ConvertOptions(mode_id=mode)).score
        assert [(n.start_pulse, n.duration_pulses, round(n.cents)) for n in back.notes] == \
            [(n.start_pulse, n.duration_pulses, round(n.cents)) for n in score.notes]


def test_note_name_labels_follow_the_readers_choice():
    """Spec 13.5: note names are selectable (the tradition's own, Do Re Mi, or letters)."""
    from orin.convert import convert
    from orin.eval import fixtures

    ir = fixtures.phrase_for("western_classical")

    def names(target, **render_opts):
        return convert(ir, target, kinds=("names",), render_opts=render_opts).renders["names"].text

    assert "Do" not in names("western_classical")  # Western staff uses letters by default
    assert "Do" in names("western_classical", labels="solfege")
    assert "Do" in names("arabic_maqam")  # fixed-do solfege is the Arabic convention
    assert "Do" not in names("arabic_maqam", labels="letters")
    # Every renderer accepts the option; those that have no use for it ignore it.
    assert not convert(ir, "carnatic", render_opts={"labels": "letters"}).missing
