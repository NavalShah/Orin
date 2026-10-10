"""Persian classical music: the dastgah system, with koron and sori. Staff with koron/sori signs.

UNREVIEWED DRAFT, AND THE LEAST VERIFIED TABLE IN ORIN (spec D9).
The cents below were computed from Hormoz Farhat's interval classes as reported in
secondary summaries; Farhat's book itself was not consulted. Every row needs checking
against "The Dastgah Concept in Persian Music" by someone who knows the repertoire.

What a practitioner should know about the shortcuts taken:
  - There is no "scale" in this music: a dastgah is a collection of melodic models
    (gusheh-ha) and the pitch set changes as they unfold. Only the opening (daramad)
    area is modelled.
  - Koron and sori are not exact quarter tones. Koron lowers by roughly 60 cents and
    sori raises by roughly 40; both are flexible.
  - The reference note is called the finalis here, not the tonic.
  - Ornaments are given generic names only. Orin does not claim to recognise tahrir or
    tekiyeh.
"""
from __future__ import annotations

from ._build import cycle, deg, grid24, mode, written
from .base import Adapter, FormatMeta, OrnamentRule, export

SOURCE = ("Interval classes after Hormoz Farhat (semitone ~90, small neutral second ~135, large neutral "
          "second ~160, whole tone ~204, plus tone ~270 cents) as given in secondary summaries; scale rows "
          "read from an unattributed reference table. To be checked against Farhat (1990).")
METER_SOURCE = "Plain meters, not named cycles. Reng is commonly in 6/8; other attributions are unverified."
FLEX = "Neutral degrees here are flexible in performance; the cents are nominal."


def _d(pairs):
    """[(label, degree, cents), ...] -> degrees with a 30-cent tolerance."""
    return [deg(label, degree, cents, 30.0) for label, degree, cents in pairs]


MODES = (
    mode("shur", "Dastgah-e Shur", _d([
        ("1", 1, 0), ("2 koron", 2, 135), ("b3", 3, 295), ("4", 4, 499), ("5", 5, 703), ("b6", 6, 793), ("b7", 7, 997),
    ]), kind="dastgah", source=SOURCE, confidence="low", tonic=written("D"),
        variable=[deg("5 koron", 5, 634, 30.0, role="variable")],
        caveats=(FLEX, "The 5th is usually lowered by a microtone in descent; that is part of Shur, not a wrong note."),
        aliases=("shur",)),
    mode("mahur", "Dastgah-e Mahur", _d([
        ("1", 1, 0), ("2", 2, 204), ("3", 3, 408), ("4", 4, 498), ("5", 5, 702), ("6", 6, 906), ("7", 7, 1110),
    ]), kind="dastgah", source=SOURCE, confidence="medium", tonic=written("C"),
        caveats=("Shares its pitches with the Western major scale; it is not the same thing.",), aliases=("mahur",)),
    mode("homayun", "Dastgah-e Homayun", _d([
        ("1", 1, 0), ("2 koron", 2, 135), ("3", 3, 405), ("4", 4, 498), ("5", 5, 702), ("b6", 6, 792), ("b7", 7, 996),
    ]), kind="dastgah", source=SOURCE, confidence="low", tonic=written("G"),
        caveats=(FLEX, "The wide step between the 2nd and 3rd is unstable in size."), aliases=("homayun",)),
    mode("bayat_e_esfahan", "Avaz-e Bayat-e Esfahan", _d([
        ("1", 1, 0), ("2", 2, 204), ("b3", 3, 294), ("4", 4, 498), ("5", 5, 702), ("6 koron", 6, 837), ("7", 7, 1107),
    ]), kind="avaz", source=SOURCE, confidence="low", tonic=written("G"),
        caveats=(FLEX, "Classed as an avaz of Homayun in the 7 + 5 scheme; some theorists treat it as independent. "
                       "Its relation to Homayun as written here is Orin's derivation."),
        aliases=("esfahan", "isfahan", "bayat-e esfahan")),
    mode("chahargah", "Dastgah-e Chahargah", _d([
        ("1", 1, 0), ("2 koron", 2, 135), ("3", 3, 405), ("4", 4, 498), ("5", 5, 702), ("6 koron", 6, 837), ("7", 7, 1107),
    ]), kind="dastgah", source=SOURCE, confidence="low", tonic=written("C"),
        caveats=(FLEX, "Both wide steps are unstable in size."), aliases=("chahargah",)),
    mode("segah", "Dastgah-e Segah", _d([
        ("1", 1, 0), ("2-", 2, 160), ("3-", 3, 364), ("4", 4, 499), ("5 koron", 5, 659), ("6-", 6, 863), ("7-", 7, 1067),
    ]), kind="dastgah", source=SOURCE, confidence="low", tonic=written("E", -60.0),
        variable=[deg("b7", 7, 998, 30.0, role="variable")],
        caveats=(FLEX, "The finalis is itself a koron note. Its lowered 5th separates it from the Arabic Sikah family; "
                       "the two should not be equated."),
        aliases=("segah",)),
    mode("nava", "Dastgah-e Nava", _d([
        ("1", 1, 0), ("2", 2, 204), ("b3", 3, 294), ("4", 4, 498), ("5", 5, 702), ("6 koron", 6, 837), ("b7", 7, 997),
    ]), kind="dastgah", source=SOURCE, confidence="low", tonic=written("G"), caveats=(FLEX,), aliases=("nava",)),
)

METERS = (
    cycle("6_8", "6/8", 2, 3, (6, 8), structure=("strong", "medium"), accents=(1, .6), source=METER_SOURCE,
          confidence="medium"),
    cycle("2_4", "2/4", 2, 4, (2, 4), structure=("strong", "weak"), accents=(1, .4), source=METER_SOURCE,
          confidence="low"),
    cycle("4_4", "4/4", 4, 4, (4, 4), structure=("strong", "weak", "medium", "weak"), accents=(1, .3, .6, .3),
          source=METER_SOURCE, confidence="low"),
)

ORNAMENTS = (
    OrnamentRule(neutral_kind="oscillation", local_name="shake", action="approximate", symbol="~",
                 staff_mark="wavy", approximation="a wavy line"),
    OrnamentRule(neutral_kind="vibrato", local_name="vibrato", action="drop"),
    OrnamentRule(neutral_kind="glide", local_name="slide", action="approximate", symbol="/", symbol_down="\\",
                 staff_mark="gliss", approximation="a slide line"),
    OrnamentRule(neutral_kind="grace", local_name="grace note", action="approximate", staff_mark="grace",
                 approximation="a grace note"),
)

ADAPTER = Adapter(FormatMeta(
    id="persian_dastgah",
    display_name="Persian (Dastgah)",
    description="Persian classical: the dastgah system with koron and sori, written on a staff with koron/sori signs.",
    aliases=("persian", "persian classical", "dastgah", "iranian classical", "radif"),
    family="middle_eastern",
    order=60,
    pitch_system="custom_cents_table",
    tonic_model="movable_root",
    naming="absolute",
    written_tonic_policy="conventional",
    tonic_word="finalis",
    accidental_style="persian",
    note_names="solfege",
    grid=grid24(tol=25.0),
    mode_table=MODES,
    rhythm_model="free_time",
    rhythm_templates=METERS,
    default_template_id=None,  # avaz is unmetered
    ornament_table=ORNAMENTS,
    expects_ornaments=True,
    renderers=("svg", "names", "musicxml", "midi", "describe"),
    primary_renderer="svg",
    input_parsers=("note_names", "musicxml", "midi"),
    review_status="unreviewed",
    reviewer_note="Draft computed from secondary summaries of Farhat. The least verified table in Orin.",
    caveats=(
        "Draft, not reviewed by a practitioner, and the least verified table in Orin.",
        "A dastgah is a family of melodic models (gusheh-ha), not a scale. Only the opening area is modelled, so a change of gusheh will look like notes outside the mode.",
        "Koron and sori are not exact quarter tones, and their size varies.",
        "The reference note is called the finalis. Each mode is written at a conventional pitch; the sounding pitch is stated beside it.",
        "Persian and Arabic music usually cannot be told apart from a short clip.",
        "One melody line only.",
    ),
    analysis_profile="persian",
    detect_priors={
        "neutral_mass": (0.12, 1.0, 3.0),
        "oscillation_share": (0.0, 0.25, 0.6),
        "free_time": (0.5, 1.0, 0.5),
        "drone": (0.0, 0.3, 0.3),
    },
))

META, parse_notes, ir_to_format, render, score_fit = export(ADAPTER)
