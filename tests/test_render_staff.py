"""MusicXML and the Verovio staff: bars, ties, ids, microtonal signs, lyrics, free time."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from orin.errors import RendererUnavailable
from orin.formats import registry
from orin.render import render, result
try:  # the hand builder lives in test_render_spell.py
    from test_render_spell import DROPPED, GRACE, SLIDE_UP, WAVY, make_score
except ImportError:  # tests/ imported as a package
    from tests.test_render_spell import DROPPED, GRACE, SLIDE_UP, WAVY, make_score

TYPE_QUARTERS = {"whole": 4, "half": 2, "quarter": 1, "eighth": 0.5, "16th": 0.25, "32nd": 0.125, "64th": 0.0625}


def western():
    """D major in 4/4. n5 crosses the first bar line; n6 is a C natural, n7 a C sharp."""
    return make_score("western_classical", [
        ("1", 1, 0, 4), ("2", 2, 200, 4), ("3", 3, 400, 4), ("4", 4, 500, 2), ("5", 5, 700, 4),
        ("b7", 7, 1000, 6), (None, 0, 0, 2), ("7", 7, 1100, 6),
    ], tonic="D", template="4_4", mode="major", tonic_hz=293.66)


def chapu():
    return make_score("carnatic", [
        ("S", 1, 0, 3), ("R2", 2, 204, 2), ("G3", 3, 386, 2), ("P", 5, 702, 9), ("D2", 6, 884, 5), ("S", 1, 1200, 7),
    ], template="misra_chapu", tonic_hz=146.83, octave=3)


def samai():
    return make_score("arabic_maqam", [
        ("1", 1, 0, 3), ("2-", 2, 150, 4), ("b3", 3, 300, 3), ("4", 4, 500, 5), ("5", 5, 700, 5),
    ], tonic="D", template="samai_thaqil", mode="bayati", pulse_s=0.25)


def rast():
    return make_score("arabic_maqam", [
        ("1", 1, 0, 2), ("2", 2, 200, 2), ("3-", 3, 350, 2, [SLIDE_UP]), ("4", 4, 500, 2),
        ("5", 5, 700, 4), ("7-", 7, 1050, 2, [WAVY]), ("1", 1, 1200, 2),
        ("3-", 3, 350, 2), ("3", 3, 400, 2), ("3-", 3, 350, 2, [GRACE]), ("1", 1, 0, 2),
    ], template="maqsum", mode="rast", pulse_s=0.25, tonic_hz=146.83, transposition=-1000.0)


SHUR_PHRASE = [
    ("1", 1, 0, 2), ("2 koron", 2, 135, 2), ("b3", 3, 295, 3), ("4", 4, 499, 1), ("5", 5, 703, 4), ("4", 4, 499, 2),
    ("b3", 3, 295, 2), ("2 koron", 2, 135, 2), ("1", 1, 0, 8), (None, 0, 0, 4), ("2 koron", 2, 135, 1),
    ("b3", 3, 295, 1), ("1", 1, 0, 6, [WAVY]),
]


def shur_free(repeats=1):
    return make_score("persian_dastgah", SHUR_PHRASE * repeats, tonic="D", mode="shur", tonic_hz=293.66, subdivision=2)


def mohanam():
    return make_score("carnatic", [
        ("S", 1, 0, 4), ("R2", 2, 204, 4), ("G3", 3, 386, 4), ("P", 5, 702, 4), ("D2", 6, 884, 4, [WAVY]),
        ("S", 1, 1200, 8), ("D2", 6, 884, 2), ("P", 5, 702, 2, [DROPPED]),
    ], template="adi", mode="mohanam", tonic_hz=146.83, octave=3)


ALL = {"western": western, "chapu": chapu, "samai": samai, "rast": rast, "shur_free": shur_free, "mohanam": mohanam}


def xml_of(score, meta):
    out = render(score, meta, "musicxml")
    assert out.kind == "musicxml" and out.ext == "musicxml"
    return ET.fromstring(out.text.encode("utf-8")), out.text


def notes_in(measure):
    return [n for n in measure.findall("note") if n.find("grace") is None]


# ---------------------------------------------------------------------------
# MusicXML
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ALL))
def test_musicxml_is_well_formed_and_carries_every_note_id(name):
    score, meta = ALL[name]()
    root, text = xml_of(score, meta)
    assert root.tag == "score-partwise" and root.get("version") == "4.0"
    assert len(root.findall("part")) == 1
    ids = [n.get("id") for n in root.iter("note") if n.get("id")]
    assert len(ids) == len(set(ids))
    assert [i for i in ids if "t" not in i] == [n.id for n in score.notes]
    assert "trill" not in text
    assert {v.text for v in root.iter("voice")} == {"1"}


@pytest.mark.parametrize("name,time", [("western", (4, 4)), ("chapu", (7, 8)), ("samai", (10, 8)),
                                       ("rast", (4, 4)), ("mohanam", (8, 4))])
def test_measure_durations_add_up(name, time):
    score, meta = ALL[name]()
    root, _ = xml_of(score, meta)
    divisions = int(root.findtext(".//divisions"))
    assert (int(root.findtext(".//time/beats")), int(root.findtext(".//time/beat-type"))) == time
    full = divisions * 4 * time[0] / time[1]
    measures = root.findall("part/measure")
    assert len(measures) >= 1
    for measure in measures:
        assert sum(int(n.findtext("duration")) for n in notes_in(measure)) == full
    # Every printed value is a standard one whose length matches its <duration>.
    for note in root.iter("note"):
        if note.find("grace") is not None or note.find("rest") is not None and note.find("type") is None:
            continue
        quarters = TYPE_QUARTERS[note.findtext("type")] * (1.5 if note.find("dot") is not None else 1)
        assert quarters * divisions == int(note.findtext("duration"))
    # And the pieces of a note add up to its written length.
    per_pulse = full / score.cycle.length_pulses
    for n in score.notes:
        pieces = [x for x in root.iter("note") if x.get("id") and re.fullmatch(rf"{n.id}(t\d+)?", x.get("id"))]
        assert sum(int(x.findtext("duration")) for x in pieces) == n.duration_pulses * per_pulse


def test_a_note_crossing_a_bar_line_is_tied():
    score, meta = western()
    root, _ = xml_of(score, meta)
    first, second = root.findall("part/measure")[:2]
    head = notes_in(first)[-1]
    tail = notes_in(second)[0]
    assert head.get("id") == "n5" and tail.get("id") == "n5t2"
    assert [t.get("type") for t in head.findall("tie")] == ["start"]
    assert [t.get("type") for t in tail.findall("tie")] == ["stop"]
    assert head.find("notations/tied").get("type") == "start" and tail.find("notations/tied").get("type") == "stop"
    assert head.findtext("pitch/step") == tail.findtext("pitch/step") == "A"
    assert root.findall("part/measure")[-1].findtext("barline/bar-style") == "light-heavy"


def test_accidentals_follow_the_key_signature_and_the_bar():
    score, meta = western()
    root, _ = xml_of(score, meta)
    assert root.findtext(".//key/fifths") == "2"
    by_id = {n.get("id"): n for n in root.iter("note") if n.get("id")}
    assert by_id["n3"].findtext("pitch/step") == "F" and by_id["n3"].findtext("pitch/alter") == "1"
    assert by_id["n3"].find("accidental") is None  # F sharp is in the key signature
    assert by_id["n6"].findtext("accidental") == "natural"  # C natural against two sharps
    assert by_id["n7"].findtext("accidental") == "sharp"  # and C sharp again in the same bar
    assert by_id["n5t2"].find("accidental") is None
    assert root.find(".//metronome") is not None and root.findtext(".//per-minute") == "120"


def test_arabic_and_persian_scores_carry_explicit_microtonal_accidentals():
    score, meta = rast()
    root, _ = xml_of(score, meta)
    by_id = {n.get("id"): n for n in root.iter("note") if n.get("id")}
    for note_id in ("n3", "n6", "n8", "n10"):  # every half-flat, repeated or not
        assert by_id[note_id].findtext("accidental") == "quarter-flat"
        assert by_id[note_id].findtext("pitch/alter") == "-0.5"
    assert (by_id["n3"].findtext("pitch/step"), by_id["n6"].findtext("pitch/step")) == ("E", "B")
    assert by_id["n9"].findtext("accidental") == "natural"  # E natural after E half-flat
    assert by_id["n1"].find("accidental") is None and by_id["n1"].findtext("pitch/octave") == "4"

    score, meta = shur_free()
    root, _ = xml_of(score, meta)
    korons = [n for n in root.iter("note") if n.findtext("accidental") == "koron"]
    assert len(korons) == 3 and all(n.findtext("pitch/alter") == "-0.5" for n in korons)
    assert all(float(a.text) * 2 == int(float(a.text) * 2) for a in root.iter("alter"))


def test_carnatic_score_prints_swara_names_as_lyrics():
    score, meta = mohanam()
    root, _ = xml_of(score, meta)
    lyrics = [n.findtext("lyric/text") for n in root.iter("note") if n.get("id") and "t" not in n.get("id")]
    assert lyrics == ["S", "R2", "G3", "P", "D2", "S", "D2", "P"]
    assert [n.findtext("lyric/text") for n in root.iter("note") if n.get("id") and "t" in n.get("id")] in ([], [None])
    western_root, _ = xml_of(*western())
    assert western_root.find(".//lyric") is None


def test_ornaments_are_text_marks_and_grace_notes_never_trills():
    score, meta = rast()
    root, text = xml_of(score, meta)
    words = [w.text for w in root.iter("words")]
    assert words == ["/", "~"]
    assert all(d.get("placement") == "above" for d in root.iter("direction"))
    graces = [n for n in root.iter("note") if n.find("grace") is not None]
    assert len(graces) == 1 and graces[0].find("duration") is None
    assert graces[0].findtext("pitch/step") == "F"  # a touch from above the half-flat E
    assert "trill" not in text and "wavy-line" not in text
    dropped_root, _ = xml_of(*mohanam())
    assert [w.text for w in dropped_root.iter("words")] == ["~"]  # the dropped vibrato leaves no mark


def test_clef_follows_the_median_pitch():
    high, meta = make_score("western_classical", [("1", 1, 0, 4), ("5", 5, 700, 4)], template="4_4")
    root, _ = xml_of(high, meta)
    assert root.findtext(".//clef/sign") == "G" and root.find(".//clef/clef-octave-change") is None
    root, _ = xml_of(*mohanam())  # Sa on D3
    assert root.findtext(".//clef/sign") == "G" and root.findtext(".//clef/clef-octave-change") == "-1"
    low, meta = make_score("rock", [("1", 1, 0, 4), ("b3", 3, 300, 4)], tonic="E", octave=2, template="4_4")
    root, _ = xml_of(low, meta)
    assert root.findtext(".//clef/sign") == "F"


def test_free_time_has_no_time_signature_and_only_invisible_barlines():
    score, meta = shur_free(repeats=3)
    root, _ = xml_of(score, meta)
    assert root.find(".//time").get("print-object") == "no"
    assert root.find(".//metronome") is None
    measures = root.findall("part/measure")
    assert len(measures) > 1
    assert [m.findtext("barline/bar-style") for m in measures] == ["none"] * len(measures)
    assert max(len([n for n in notes_in(m) if n.get("id") and "t" not in n.get("id")]) for m in measures) <= 8
    assert root.findtext(".//measure-numbering") == "none"
    assert len([n for n in root.iter("note") if n.find("rest") is not None]) == 3  # the gap in each phrase


def test_a_triplet_pulse_is_written_plainly_with_a_warning():
    score, meta = make_score("western_classical", [("1", 1, 0, 2), ("2", 2, 200, 1)], subdivision=3)
    out = render(score, meta, "musicxml")
    ET.fromstring(out.text.encode("utf-8"))
    assert out.warnings and "eighth" in out.warnings[0]


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

def barlines_drawn(svg: str) -> int:
    return len(re.findall(r'<g[^>]*class="barLine"[^>]*>\s*<path', svg))


def glyphs(svg: str) -> set[str]:
    return set(re.findall(r'xlink:href="#(E[0-9A-F]{3})', svg))


@pytest.mark.parametrize("name", sorted(ALL))
def test_verovio_draws_every_note(name):
    pytest.importorskip("verovio")
    score, meta = ALL[name]()
    out = render(score, meta, "svg")
    assert out.kind == "svg" and out.media_type == "image/svg+xml" and out.pages
    svg = "".join(out.pages)
    assert all(page.lstrip().startswith("<svg") for page in out.pages)
    assert out.note_ids == tuple(n.id for n in score.notes)
    for note in score.notes:
        assert re.search(rf'<g[^>]*id="{note.id}"[^>]*class="note', svg)


def test_svg_shows_koron_and_quarter_flat_glyphs():
    pytest.importorskip("verovio")
    persian = "".join(render(*shur_free(), "svg").pages)
    assert "E460" in glyphs(persian)  # SMuFL accidentalKoron
    arabic = "".join(render(*rast(), "svg").pages)
    assert glyphs(arabic) & {"E280", "E442"}  # a quarter-tone flat
    western_svg = "".join(render(*western(), "svg").pages)
    assert not glyphs(western_svg) & {"E460", "E461", "E280", "E442"}


def test_free_time_svg_draws_no_bar_lines_and_measured_does():
    pytest.importorskip("verovio")
    free = "".join(render(*shur_free(repeats=6), "svg").pages)
    assert barlines_drawn(free) == 0
    assert 'class="meterSig"' not in free and 'class="mNum' not in free
    assert len(re.findall(r'class="system"', free)) > 1  # the line still wraps
    assert barlines_drawn("".join(render(*western(), "svg").pages)) >= 2


def test_carnatic_svg_carries_lyrics_and_the_wavy_mark():
    pytest.importorskip("verovio")
    svg = "".join(render(*mohanam(), "svg").pages)
    assert 'class="verse"' in svg and 'class="dir"' in svg


def test_unreadable_score_raises_renderer_unavailable(monkeypatch):
    pytest.importorskip("verovio")
    from orin.render import svg as svg_module

    monkeypatch.setattr(svg_module.musicxml, "render", lambda score, meta, **kw: result("musicxml", text="not music"))
    score, meta = mohanam()
    with pytest.raises(RendererUnavailable) as caught:
        render(score, meta, "svg")
    assert caught.value.kind == "svg" and caught.value.alternative == "sargam"
    score, meta = western()
    with pytest.raises(RendererUnavailable) as caught:
        render(score, meta, "svg")
    assert caught.value.alternative == "names"


# ---------------------------------------------------------------------------
# Odd music, and real engine output
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("format_id", ["western_classical", "western_pop", "rock", "carnatic", "arabic_maqam",
                                       "persian_dastgah"])
@pytest.mark.parametrize("measured", [True, False])
def test_empty_one_note_and_leaping_scores_render(format_id, measured):
    pytest.importorskip("verovio")
    meta = registry.get(format_id).META
    template = meta.rhythm_templates[0].id if measured else None
    name = meta.grid[0].name
    for notes in ([], [(name, 1, 0.0, 3)], [(name, 1, -2400.0, 1), (name, 1, 3600.0, 50), (name, 1, 0.0, 1)]):
        score, _ = make_score(format_id, notes, template=template)
        root, _ = xml_of(score, meta)
        assert root.findall("part/measure")
        out = render(score, meta, "svg")
        assert out.pages and len(out.note_ids) == len(notes)


def test_real_engine_output_renders_on_a_staff():
    pytest.importorskip("orin.formats.engine")
    pytest.importorskip("orin.ingest.notes_sargam")
    pytest.importorskip("verovio")
    from orin.formats.base import ConvertOptions
    from orin.ingest.notes import parse_notes

    text = ("tonic: D3\nraga: mohanam\ntala: adi\ntempo: 80\nunits_per_beat: 2\n"
            "S , R , G , P , | D~ , S' , | , , D , ||\n_ _ G , P , D , | S' , R' , | , , S' , ||\n")
    ir = parse_notes(text, format_id="carnatic")
    for format_id, mode in (("carnatic", "mohanam"), ("western_classical", None), ("arabic_maqam", "rast"),
                            ("persian_dastgah", "shur"), ("rock", None)):
        adapter = registry.get(format_id)
        score = adapter.ir_to_format(ir, ConvertOptions(mode_id=mode)).score
        assert len(score.notes) == len(ir.events) == 13
        root, _ = xml_of(score, adapter.META)
        assert [n.get("id") for n in root.iter("note") if n.get("id") and "t" not in n.get("id")] == \
            [n.id for n in score.notes]
        out = adapter.render(score, "svg")
        assert out.note_ids == tuple(n.id for n in score.notes)


def test_staff_rendering_is_safe_from_several_threads():
    """The server renders from a job pool. Verovio's font loading is process-wide, so
    unguarded concurrent renders fail with "font resources are not available"."""
    from concurrent.futures import ThreadPoolExecutor

    from orin.convert import convert
    from orin.eval import fixtures

    pytest.importorskip("verovio")
    ir = fixtures.phrase_for("western_classical")

    def one(_):
        conversion = convert(ir, "western_classical", kinds=("svg",))
        assert not conversion.missing, conversion.missing
        return len(conversion.renders["svg"].pages)

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert all(n >= 1 for n in pool.map(one, range(18)))
