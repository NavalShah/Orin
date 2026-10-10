"""Arabic maqam: neutral ("quarter-tone") degrees, ajnas, iqa'at. Staff with half-flats.

UNREVIEWED DRAFT. Nothing here has been checked by a practitioner (spec D9).
What a practitioner should know about the shortcuts taken:
  - A maqam is a network of ajnas with modulation as the norm. Only its scale at the
    conventional tonic is modelled, with the usual upper-jins alternatives listed as
    "variable" notes rather than enforced by direction.
  - The 24-step grid is for NAMING. The half-flat of Rast is not the half-flat of
    Bayati, and both vary by region and performer; each degree carries a tolerance
    instead of a claim about its tuning.
  - Saba does not repeat at the octave. Only its lower octave is modelled; higher notes
    are written as played.
  - An iqa' cannot be told from a melody (Maqsum, Baladi and Sa'idi differ in drum
    strokes). Orin can suggest a cycle length; the name is the user's choice.
"""
from __future__ import annotations

from ._build import cycle, deg, grid24, mode, strokes, written
from .base import Adapter, FormatMeta, OrnamentRule, export

SCALE_SOURCE = ("Ajnas from MaqamWorld; cents derived from the ajnas on a nominal 24-step grid at the "
                "conventional tonic. To be copied from MaqamWorld or Inside Arabic Music by a practitioner.")
IQA_SOURCE = "Stroke patterns cross-checked between two rhythm libraries; MaqamWorld is the authority to verify against."
TUNING_CAVEAT = "Half-flat degrees are nominal: their real tuning depends on the maqam, the region and the performer."

# interval label -> (degree, nominal cents)
_D = {
    "1": (1, 0), "b2": (2, 100), "2-": (2, 150), "2": (2, 200), "b3": (3, 300), "3-": (3, 350), "3": (3, 400),
    "b4": (4, 400), "4-": (4, 450), "4": (4, 500), "5": (5, 700), "5+": (5, 750), "b6": (6, 800), "6-": (6, 850),
    "6": (6, 900), "b7": (7, 1000), "7-": (7, 1050), "7": (7, 1100),
}


def _degs(labels: str, tol: float = 30.0, **special):
    """``special`` maps a label to (tol_lo, tol_hi) where the usual window is wrong."""
    out = []
    for label in labels.split():
        degree, cents = _D[label]
        lo, hi = special.get(label.replace("-", "m").replace("+", "p"), (tol, tol))
        out.append(deg(label, degree, cents, tol_lo=lo, tol_hi=hi))
    return out


def _maqam(id, label, labels, tonic, *, variable=None, caveats=(), confidence="medium", **special):
    return mode(id, f"Maqam {label}", _degs(labels, **special), kind="maqam", source=SCALE_SOURCE,
                confidence=confidence, tonic=tonic,
                variable=_degs(variable) if variable else None,
                caveats=(TUNING_CAVEAT, *caveats), aliases=(label.lower(),))


MODES = (
    _maqam("rast", "Rast", "1 2 3- 4 5 6 7-", written("C"), variable="b7",
           caveats=("The upper part often uses a lowered 7th, especially descending; this is a tendency, not a rule.",)),
    _maqam("bayati", "Bayati", "1 2- b3 4 5 b6 b7", written("D"), variable="6-",
           caveats=("Its 2nd is reported slightly lower than the half-flat of Rast.",)),
    # The 2nd-to-3rd gap is usually played smaller than written, so both windows lean inward.
    _maqam("hijaz", "Hijaz", "1 b2 3 4 5 b6 b7", written("D"), variable="6-",
           b2=(15.0, 50.0), **{"3": (50.0, 15.0)},
           caveats=("The wide step between the 2nd and 3rd is usually played narrower than written.",)),
    _maqam("nahawand", "Nahawand", "1 2 b3 4 5 b6 7", written("C"), variable="b7", confidence="medium"),
    _maqam("kurd", "Kurd", "1 b2 b3 4 5 b6 b7", written("D")),
    _maqam("ajam", "Ajam", "1 2 3 4 5 6 7", written("B", -100.0), variable="b7",
           caveats=("Also taught on C.",)),
    _maqam("saba", "Saba", "1 2- b3 b4 5 b6 b7", written("D"),
           caveats=("Saba does not repeat at the octave: the notes above are not copies of the lower ones. "
                    "Only the lower octave is modelled.",)),
    _maqam("huzam", "Huzam", "1 2- 3- 4- 5+ 6- 7-", written("E", -50.0),
           caveats=("Its tonic is itself a half-flat note. Huzam is the common member of the Sikah family; "
                    "Sikah itself is rarer and not included.",)),
)


def _iqa(id, label, beats, subdivision, time_signature, pattern, confidence, caveats=()):
    structure, accents = strokes(pattern)
    return cycle(id, label, beats, subdivision, time_signature, structure=structure, accents=accents,
                 source=IQA_SOURCE, confidence=confidence,
                 caveats=("D = dum, T = tak, - = rest. Accent weights are Orin's modelling choice.", *caveats))


IQAAT = (
    _iqa("maqsum", "Maqsum (4/4)", 4, 2, (4, 4), "D T - T D - T -", "high"),
    _iqa("baladi", "Baladi (4/4)", 4, 2, (4, 4), "D D - T D - T -", "high", ("Naming varies by region.",)),
    _iqa("wahda", "Wahda (4/4)", 4, 2, (4, 4), "D - - T - - T -", "medium"),
    _iqa("malfuf", "Malfuf (2/4)", 2, 4, (2, 4), "D - - T - - T -", "high"),
    _iqa("samai_thaqil", "Sama'i Thaqil (10/8)", 10, 1, (10, 8), "D - - T - D D T - -", "medium"),
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
    id="arabic_maqam",
    display_name="Arabic Maqam",
    description="Arabic classical: maqamat with neutral (half-flat) degrees, written on a staff with half-flat signs.",
    aliases=("maqam", "arabic", "arabic classical", "arab music", "makam"),
    family="middle_eastern",
    order=50,
    pitch_system="maqam_quarter_tones",
    tonic_model="movable_root",
    naming="absolute",
    written_tonic_policy="conventional",
    tonic_word="tonic",
    accidental_style="arabic",
    note_names="solfege",
    grid=grid24(tol=25.0),
    mode_table=MODES,
    rhythm_model="tala_cycle",
    rhythm_templates=IQAAT,
    default_template_id=None,  # taqsim is unmetered; a cycle is the user's choice
    cycles_need_choice=True,
    ornament_table=ORNAMENTS,
    expects_ornaments=True,
    renderers=("svg", "names", "musicxml", "midi", "describe"),
    primary_renderer="svg",
    input_parsers=("note_names", "musicxml", "midi"),
    review_status="unreviewed",
    reviewer_note="Draft tables derived from MaqamWorld's ajnas. Not reviewed by a practitioner.",
    caveats=(
        "Draft, not reviewed by a practitioner.",
        "Maqam tuning varies by region and performer. The 24-step grid here is for naming notes, not for tuning them.",
        "A maqam is more than a scale: its ajnas, its modulations and its typical paths are not modelled.",
        "Each maqam is written at its conventional pitch (Rast on C, Bayati on D); the sounding pitch is stated beside it.",
        "The rhythmic cycle (iqa') cannot be identified from a melody. Choose it yourself if you know it.",
        "One melody line only.",
    ),
    analysis_profile="arabic",
    detect_priors={
        "neutral_mass": (0.12, 1.0, 3.0),
        "oscillation_share": (0.0, 0.25, 0.6),
        "glide_share": (0.05, 0.5, 0.4),
        "drone": (0.0, 0.4, 0.3),
    },
))

META, parse_notes, ir_to_format, render, score_fit = export(ADAPTER)
