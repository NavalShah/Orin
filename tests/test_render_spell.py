"""Spelling, key-signature choice and the pulse grid shared by the renderers.

Also holds ``make_score``, the hand builder the other render tests import: renderers are
tested on FormatScores written out by hand, so they do not depend on the engine.
"""
from __future__ import annotations

import pytest

from orin.formats import registry
from orin.formats.base import FormatScore, ScoreNote, ScoreOptions, ScoreOrnament, WrittenPitch
from orin.render.spell import (bar_starts, choose_fifths, key_alters, key_name, spell_note, spell_pitch,
                               spell_tonic, timeline)

NOMINAL = (0, 200, 400, 500, 700, 900, 1100)

WAVY = ScoreOrnament(kind="oscillation", action="approximate", symbol="~", staff_mark="wavy",
                     local_name="kampita-like oscillation", amplitude_cents=60.0)
SLIDE_UP = ScoreOrnament(kind="glide", action="approximate", symbol="/", staff_mark="gliss", local_name="slide",
                         amplitude_cents=150.0, shape="up")
BEND = ScoreOrnament(kind="glide", action="approximate", symbol="b", staff_mark="bend", local_name="bend",
                     amplitude_cents=100.0, shape="up")
DROPPED = ScoreOrnament(kind="vibrato", action="drop", amplitude_cents=20.0)
GRACE = ScoreOrnament(kind="grace", action="approximate", staff_mark="grace", shape="touch_above",
                      amplitude_cents=200.0)


def make_score(format_id, notes, *, tonic="C", alter=0.0, octave=4, tonic_hz=261.6256, template=None,
               mode=None, pulse_s=0.125, subdivision=4, transposition=0.0, start=0):
    """A FormatScore written by hand.

    ``notes`` is a list of (name, degree, cents, pulses[, ornaments[, in_mode]]); a name of
    None is a rest. With no ``template`` the score is in free time. Returns (score, meta).
    """
    meta = registry.get(format_id).META
    cycle = meta.template(template) if template else None
    if cycle is not None:
        subdivision = cycle.subdivision
    out, pos = [], start
    for spec in notes:
        name, degree, cents, pulses = spec[:4]
        if name is not None:
            out.append(ScoreNote(
                id=f"n{len(out) + 1}", cents=cents, source_cents=cents, cents_delta=0.0, name=name, degree=degree,
                octave=int(cents // 1200), in_mode=spec[5] if len(spec) > 5 else True,
                start_pulse=pos, duration_pulses=pulses, onset_s=pos * pulse_s, duration_s=pulses * pulse_s,
                source_onset_s=pos * pulse_s, ornaments=tuple(spec[4]) if len(spec) > 4 else ()))
        pos += pulses
    entry = meta.mode(mode)
    options = ScoreOptions(
        tonic_hz=tonic_hz, mode_id=entry.id if entry else None, mode_label=entry.label if entry else None,
        template_id=template, tempo_bpm=60.0 / (pulse_s * subdivision) if cycle else None, pulse_s=pulse_s,
        subdivision=subdivision, free_time=cycle is None,
        written_tonic=WrittenPitch(step=tonic, alter_cents=alter), written_tonic_octave=octave,
        transposition_cents=transposition)
    return FormatScore(format_id=format_id, options=options, notes=tuple(out), cycle=cycle), meta


def scale(labels):
    """[(name, degree, cents)] -> notes of four pulses each."""
    return [(name, degree, cents, 4) for name, degree, cents in labels]


D_MAJOR = scale([("1", 1, 0), ("2", 2, 200), ("3", 3, 400), ("4", 4, 500), ("5", 5, 700), ("6", 6, 900),
                 ("7", 7, 1100), ("1", 1, 1200)])


def C(alter=0.0):
    return WrittenPitch(step="C", alter_cents=alter)


# ---------------------------------------------------------------------------
# Spelling
# ---------------------------------------------------------------------------

def test_rast_third_on_c_is_e_half_flat():
    sp = spell_pitch(C(), 4, 3, 350.0, "arabic")
    assert (sp.step, sp.alter, sp.octave) == ("E", -0.5, 4)
    assert sp.accidental == "quarter-flat" and sp.ascii == "d" and sp.name == "Ed4"
    assert sp.microtonal and sp.solfege == "Mi half-flat"


def test_huzam_second_above_a_half_flat_tonic_is_f_natural():
    tonic = WrittenPitch(step="E", alter_cents=-50.0)
    sp = spell_pitch(tonic, 4, 2, 150.0, "arabic")
    assert (sp.step, sp.alter, sp.octave, sp.accidental, sp.ascii) == ("F", 0.0, 4, "natural", "")
    assert spell_pitch(tonic, 4, 1, 0.0, "arabic").name == "Ed4"


def test_shur_second_on_d_is_e_koron():
    sp = spell_pitch(WrittenPitch(step="D"), 4, 2, 135.0, "persian")
    assert (sp.step, sp.alter, sp.accidental, sp.ascii) == ("E", -0.5, "koron", "p")
    assert sp.solfege == "Mi koron"
    sori = spell_pitch(WrittenPitch(step="D"), 4, 3, 340.0, "persian")
    assert (sori.step, sori.alter, sori.accidental, sori.ascii) == ("F", 0.5, "sori", ">")


def test_carnatic_r3_and_g2_share_a_pitch_but_not_a_letter():
    r3 = spell_pitch(C(), 4, 2, 316.0, "standard")
    g2 = spell_pitch(C(), 4, 3, 316.0, "standard")
    assert (r3.step, r3.alter, r3.accidental, r3.ascii) == ("D", 1.0, "sharp", "#")
    assert (g2.step, g2.alter, g2.accidental, g2.ascii) == ("E", -1.0, "flat", "b")
    assert r3.midi == g2.midi == 63


def test_octaves_follow_the_letter():
    assert spell_pitch(C(), 4, 1, 1200.0).name == "C5"
    assert spell_pitch(C(), 4, 7, -100.0).name == "B3"
    assert spell_pitch(C(), 4, 5, -500.0).name == "G3"
    assert spell_pitch(WrittenPitch(step="B"), 3, 2, 200.0).name == "C#4"
    assert spell_pitch(C(), 4, 7, 1200.0).name == "B#4"  # the octave belongs to the letter
    assert spell_pitch(C(), 4, 1, 0.0).midi == 60


def test_styles_quantise_to_their_own_grid():
    assert spell_pitch(C(), 4, 3, 386.0, "standard").alter == 0.0  # a just third is still written E
    assert spell_pitch(C(), 4, 3, 364.0, "persian").accidental == "koron"
    three_quarter = spell_pitch(C(), 4, 3, 250.0, "arabic")
    assert (three_quarter.alter, three_quarter.accidental, three_quarter.ascii) == (-1.5, "three-quarters-flat", "db")
    half_sharp = spell_pitch(C(), 4, 4, 550.0, "arabic")
    assert (half_sharp.alter, half_sharp.accidental, half_sharp.ascii) == (0.5, "quarter-sharp", "+")
    for cents in range(0, 1200, 7):
        assert spell_pitch(C(), 4, 2, cents, "standard").alter % 1 == 0
        assert spell_pitch(C(), 4, 2, cents, "arabic").alter % 0.5 == 0


def test_a_degree_that_contradicts_the_pitch_is_respelled_readably():
    sp = spell_pitch(C(), 4, 2, 600.0, "standard")  # a "second" a tritone up
    assert abs(sp.alter) <= 1 and sp.midi == 66


# ---------------------------------------------------------------------------
# Key signatures
# ---------------------------------------------------------------------------

def test_d_major_phrase_gets_two_sharps():
    score, meta = make_score("western_classical", D_MAJOR, tonic="D", template="4_4")
    assert choose_fifths(score, meta) == 2
    assert key_alters(2) == {"C": 1, "D": 0, "E": 0, "F": 1, "G": 0, "A": 0, "B": 0}
    assert key_name(2) == "D" and key_name(2, "minor") == "Bm" and key_name(-3, "minor") == "Cm"


def test_ties_go_to_the_key_of_the_written_tonic():
    # No seventh degree: one sharp and two sharps both need no accidentals.
    score, meta = make_score("western_classical", D_MAJOR[:5], tonic="D", template="4_4")
    assert choose_fifths(score, meta) == 2
    minor = scale([("1", 1, 0), ("2", 2, 200), ("b3", 3, 300), ("4", 4, 500), ("5", 5, 700)])
    score, meta = make_score("western_classical", minor, tonic="D", template="4_4")
    assert choose_fifths(score, meta) == -1


def test_key_signature_counts_only_whole_semitone_notes():
    bayati = scale([("1", 1, 0), ("2-", 2, 150), ("b3", 3, 300), ("4", 4, 500), ("5", 5, 700), ("b6", 6, 800),
                    ("b7", 7, 1000)])
    score, meta = make_score("arabic_maqam", bayati, tonic="D", mode="bayati")
    assert choose_fifths(score, meta) == -1  # B flat; the half-flat E is always written out
    assert spell_note(score, meta, score.notes[1]).name == "Ed4"
    assert spell_tonic(score, meta).name == "D4"
    empty, meta = make_score("arabic_maqam", [])
    assert choose_fifths(empty, meta) == 0


# ---------------------------------------------------------------------------
# The pulse grid
# ---------------------------------------------------------------------------

def test_timeline_is_monophonic_and_starts_in_the_first_cycle():
    score, meta = make_score("western_classical", D_MAJOR[:3], template="4_4", start=36)  # two empty bars first
    tl = timeline(score)
    assert [(s, d) for s, d, _ in tl] == [(4, 4), (8, 4), (12, 4)]
    assert bar_starts(score, 16) == [0, 16] and bar_starts(score, 17) == [0, 16, 32]

    overlapping = score.model_copy(update={"notes": tuple(
        n.model_copy(update={"start_pulse": 0, "duration_pulses": 8}) for n in score.notes)})
    assert [(s, d) for s, d, _ in timeline(overlapping)] == [(0, 1), (1, 1), (2, 8)]

    free, meta = make_score("persian_dastgah", D_MAJOR[:3], start=7)
    assert [s for s, _, _ in timeline(free)] == [0, 4, 8]
    assert bar_starts(free, 12) == []


@pytest.mark.parametrize("format_id", ["western_classical", "carnatic", "arabic_maqam", "persian_dastgah", "rock"])
def test_every_grid_position_spells_with_a_known_accidental(format_id):
    meta = registry.get(format_id).META
    notes = [(g.name, g.degree, g.cents, 1) for g in meta.grid]
    for tonic, alter in (("C", 0.0), ("D", 0.0), ("B", -100.0), ("F", 100.0), ("E", -50.0)):
        score, _ = make_score(format_id, notes, tonic=tonic, alter=alter)
        for note in score.notes:
            sp = spell_note(score, meta, note)
            assert sp.accidental and abs(sp.alter) <= 2
            if meta.accidental_style == "standard":
                assert not sp.microtonal
