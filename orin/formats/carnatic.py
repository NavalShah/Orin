"""Carnatic (sangeetham): movable Sa, sixteen swarasthana names on twelve positions,
ragas, talas, gamakas. Output is sargam text; staff is offered as an approximation.

UNREVIEWED DRAFT. Nothing here has been checked by a Carnatic musician (spec D9).
What a practitioner should know about the shortcuts taken:
  - A raga is modelled as a set of pitches. Its phrases, its order of ascent and
    descent beyond a display string, and above all its gamakas are not modelled.
    A match on pitches is a "closest match", never an identification.
  - The cents are nominal: one common 5-limit choice per swarasthana. Every variable
    swara has a second candidate a comma (about 22 cents) away, and sung intonation is
    not fixed. They are never shown as measured tuning.
  - Ornament names are hedged ("kampita-like") and applied only when the user chose
    Carnatic explicitly.
"""
from __future__ import annotations

from ._build import cycle, deg, mode
from .base import Adapter, FormatMeta, GridPosition, OrnamentRule, export

SWARA_SOURCE = ("Swarasthana names and positions: standard 16-name / 12-position scheme. "
                "Cents: a common 5-limit selection from the 22-shruti scheme; to be checked against "
                "Sambamoorthy, South Indian Music.")
MELA_SOURCE = "Standard 72-melakarta chart; to be cross-checked against Sambamoorthy, South Indian Music."
JANYA_SOURCE = "Common reference listings of the raga's arohana and avarohana; to be checked by a practitioner."
TALA_SOURCE = "Common teaching descriptions of the tala's angas and counts; to be checked by a practitioner."

# name -> (staff degree, nominal cents, tolerance)
SWARAS: dict[str, tuple[int, float, float]] = {
    "S": (1, 0.0, 15.0),
    "R1": (2, 112.0, 30.0), "R2": (2, 204.0, 30.0), "R3": (2, 316.0, 30.0),
    "G1": (3, 204.0, 30.0), "G2": (3, 316.0, 30.0), "G3": (3, 386.0, 30.0),
    "M1": (4, 498.0, 20.0), "M2": (4, 590.0, 30.0),
    "P": (5, 702.0, 15.0),
    "D1": (6, 814.0, 30.0), "D2": (6, 884.0, 30.0), "D3": (6, 1018.0, 30.0),
    "N1": (7, 884.0, 30.0), "N2": (7, 1018.0, 30.0), "N3": (7, 1088.0, 30.0),
}

# Twelve positions carry sixteen names: R2 = G1, R3 = G2, D2 = N1, D3 = N2.
GRID = (
    GridPosition(name="S", cents=0.0, degree=1, tol=15.0),
    GridPosition(name="R1", cents=112.0, degree=2, tol=30.0),
    GridPosition(name="R2", cents=204.0, degree=2, alt=(("G1", 3),), tol=30.0),
    GridPosition(name="G2", cents=316.0, degree=3, alt=(("R3", 2),), tol=30.0),
    GridPosition(name="G3", cents=386.0, degree=3, tol=30.0),
    GridPosition(name="M1", cents=498.0, degree=4, tol=20.0),
    GridPosition(name="M2", cents=590.0, degree=4, tol=30.0),
    GridPosition(name="P", cents=702.0, degree=5, tol=15.0),
    GridPosition(name="D1", cents=814.0, degree=6, tol=30.0),
    GridPosition(name="D2", cents=884.0, degree=6, alt=(("N1", 7),), tol=30.0),
    GridPosition(name="N2", cents=1018.0, degree=7, alt=(("D3", 6),), tol=30.0),
    GridPosition(name="N3", cents=1088.0, degree=7, tol=30.0),
)


def swaras(names: str):
    out = []
    for name in names.split():
        degree, cents, tol = SWARAS[name]
        out.append(deg(name, degree, cents, tol))
    return out


def _mela(id, number, label, names, **kw):
    return mode(id, f"{label} (mela {number})", swaras(names), kind="melakarta", source=MELA_SOURCE,
                confidence="high", aliases=(label.lower(),), **kw)


def _janya(id, label, names, **kw):
    return mode(id, label, swaras(names), kind="janya", source=JANYA_SOURCE, confidence="medium", **kw)


PITCH_SET_CAVEAT = "Matched on pitches only. A raga is also its phrases and gamakas, which Orin does not model."
ROTATION_CAVEAT = ("Mohanam, Hindolam, Shuddha Saveri and Madhyamavati use the same five-note shape starting "
                   "from different notes, so a wrong Sa turns one into another.")

MODES = (
    _mela("mayamalavagowla", 15, "Mayamalavagowla", "S R1 G3 M1 P D1 N3", caveats=(PITCH_SET_CAVEAT,)),
    _mela("shankarabharanam", 29, "Shankarabharanam", "S R2 G3 M1 P D2 N3", caveats=(PITCH_SET_CAVEAT,)),
    _mela("kalyani", 65, "Kalyani", "S R2 G3 M2 P D2 N3", caveats=(PITCH_SET_CAVEAT,)),
    _mela("kharaharapriya", 22, "Kharaharapriya", "S R2 G2 M1 P D2 N2", caveats=(PITCH_SET_CAVEAT,)),
    _mela("harikambhoji", 28, "Harikambhoji", "S R2 G3 M1 P D2 N2", caveats=(PITCH_SET_CAVEAT,)),
    # No parent raga is shown for Mohanam: sources disagree (28, 65 or 29).
    _janya("mohanam", "Mohanam", "S R2 G3 P D2", caveats=(PITCH_SET_CAVEAT, ROTATION_CAVEAT)),
    _janya("hamsadhwani", "Hamsadhwani", "S R2 G3 P N3", caveats=(PITCH_SET_CAVEAT,)),
    _janya("hindolam", "Hindolam", "S G2 M1 D1 N2",
           caveats=(PITCH_SET_CAVEAT, ROTATION_CAVEAT, "Has no Pa; a drone may be tuned Sa-Ma instead of Sa-Pa.")),
    _janya("shuddha_saveri", "Shuddha Saveri", "S R2 M1 P D2", caveats=(PITCH_SET_CAVEAT, ROTATION_CAVEAT)),
    _janya("madhyamavati", "Madhyamavati", "S R2 M1 P N2", caveats=(PITCH_SET_CAVEAT, ROTATION_CAVEAT)),
)

TALAS = (
    cycle("adi", "Adi tala (8)", 8, 4, (8, 4),
          structure=("clap", "finger", "finger", "finger", "clap", "wave", "clap", "wave"),
          accents=(1, .3, .3, .3, .7, .2, .7, .2), groups=(4, 2, 2), source=TALA_SOURCE, confidence="high",
          caveats=("Accent weights are Orin's modelling choice.",)),
    cycle("rupakam_3", "Rupakam (3-count, as commonly performed)", 3, 4, (3, 4),
          structure=("clap", "clap", "wave"), accents=(1, .6, .3), groups=(3,), source=TALA_SOURCE,
          confidence="medium",
          caveats=("The formal Rupaka tala is a drutam plus a laghu (6 counts); this is the 3-count form heard in performance.",)),
    cycle("misra_chapu", "Misra Chapu (7)", 7, 2, (7, 8),
          structure=("clap", "-", "-", "clap", "-", "clap", "-"), accents=(1, .2, .2, .8, .2, .8, .2),
          groups=(3, 2, 2), source=TALA_SOURCE, confidence="medium",
          caveats=("Hand gestures are not confirmed; only the 3+2+2 grouping is.",)),
    cycle("khanda_chapu", "Khanda Chapu (5)", 5, 2, (5, 8),
          structure=("clap", "-", "clap", "clap", "-"), accents=(1, .2, .7, .7, .2),
          groups=(2, 3), source=TALA_SOURCE, confidence="medium",
          caveats=("Hand gestures are not confirmed; only the 2+3 grouping is.",)),
)

# The marks are Orin's own, stated in the legend of every sargam output. Gamakas are
# usually not notated at all; kampita is never drawn as a trill.
ORNAMENTS = (
    OrnamentRule(neutral_kind="oscillation", local_name="kampita-like oscillation", action="approximate",
                 symbol="~", staff_mark="wavy", approximation="a wavy mark (~)"),
    OrnamentRule(neutral_kind="vibrato", local_name="light shake", action="drop"),
    OrnamentRule(neutral_kind="glide", local_name="jaru-like slide", action="approximate",
                 symbol="/", symbol_down="\\", staff_mark="gliss", approximation="a slide mark (/ or \\)"),
    OrnamentRule(neutral_kind="grace", local_name="quick touch of a neighbouring note", action="drop"),
)

ADAPTER = Adapter(FormatMeta(
    id="carnatic",
    display_name="Carnatic (Sangeetham)",
    description="South Indian classical: movable Sa, swaras, ragas, talas and gamakas, written as sargam text.",
    aliases=("sangeetham", "south indian classical", "karnatic", "carnatic music"),
    family="indian_classical",
    order=20,
    pitch_system="just_intonation_srutis",
    tonic_model="movable_sa",
    naming="relative",
    tonic_word="Sa",
    note_names="swara",
    grid=GRID,
    mode_table=MODES,
    rhythm_model="tala_cycle",
    rhythm_templates=TALAS,
    default_template_id="adi",
    ornament_table=ORNAMENTS,
    expects_ornaments=True,
    renderers=("sargam", "svg", "musicxml", "midi", "describe"),
    primary_renderer="sargam",
    input_parsers=("sargam", "midi", "musicxml"),
    review_status="unreviewed",
    reviewer_note="Draft tables written from standard references. Not reviewed by a Carnatic musician.",
    caveats=(
        "Draft, not reviewed by a Carnatic musician.",
        "Ragas are matched on pitches only and shown as closest matches. Gamakas, which are part of a raga's identity, are not modelled.",
        "Swara positions are nominal: twelve positions, not the 22 shrutis, and not measured tuning.",
        "A tala cannot be proven from the melody alone. Orin suggests one; choose it yourself if you know it.",
        "One melody line only.",
    ),
    analysis_profile="carnatic",
    detect_priors={
        "oscillation_share": (0.2, 1.0, 2.5),
        "drone": (0.5, 1.0, 1.5),
        "glide_share": (0.15, 1.0, 1.0),
        "neutral_mass": (0.0, 0.08, 1.2),
        "grid12_rms_cents": (10.0, 60.0, 0.6),
    },
))

META, parse_notes, ir_to_format, render, score_fit = export(ADAPTER)
