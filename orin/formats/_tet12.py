"""The shared 12-tone equal-temperament core for the Western formats.

Pop and rock are separate adapter files (spec 14.5 Q6) that pick from these tables and
differ in default meter, mode list, ornament handling, renderers and analysis profile.
"""
from __future__ import annotations

from ._build import cycle, deg, mode
from .base import GridPosition, OrnamentRule

SOURCE = "Standard Western music theory (12-tone equal temperament)"

# 12 exact pitches. tol = 0: equal temperament writes exact pitches, so anything in
# between is a real rounding.
GRID = tuple(
    GridPosition(name=name, cents=100.0 * i, degree=degree, alt=alt)
    for i, (name, degree, alt) in enumerate([
        ("1", 1, ()), ("b2", 2, ()), ("2", 2, ()), ("b3", 3, ()), ("3", 3, ()), ("4", 4, ()),
        ("#4", 4, (("b5", 5),)), ("5", 5, ()), ("b6", 6, ()), ("6", 6, ()), ("b7", 7, ()), ("7", 7, ()),
    ])
)

_D = {  # interval label -> (degree, cents)
    "1": (1, 0), "b2": (2, 100), "2": (2, 200), "b3": (3, 300), "3": (3, 400), "4": (4, 500),
    "#4": (4, 600), "b5": (5, 600), "5": (5, 700), "b6": (6, 800), "6": (6, 900), "b7": (7, 1000), "7": (7, 1100),
}


def _degs(labels: str):
    return [deg(label, _D[label][0], _D[label][1], tol=0.0) for label in labels.split()]


def _m(id, label, labels, parent, **kw):
    return mode(id, label, _degs(labels), kind=kw.pop("kind", "scale"), source=SOURCE,
                confidence="high", parent=parent, **kw)


MODES = {
    "major": _m("major", "Major", "1 2 3 4 5 6 7", "major", aliases=("ionian",)),
    "natural_minor": _m("natural_minor", "Natural minor", "1 2 b3 4 5 b6 b7", "minor", aliases=("minor", "aeolian")),
    "harmonic_minor": _m("harmonic_minor", "Harmonic minor", "1 2 b3 4 5 b6 7", "minor"),
    "melodic_minor": _m("melodic_minor", "Melodic minor", "1 2 b3 4 5 6 7", "minor",
                        descending=_degs("1 2 b3 4 5 b6 b7"),
                        caveats=("Rises with a raised 6th and 7th and usually falls as natural minor.",)),
    "dorian": _m("dorian", "Dorian", "1 2 b3 4 5 6 b7", "minor", kind="mode"),
    "phrygian": _m("phrygian", "Phrygian", "1 b2 b3 4 5 b6 b7", "minor", kind="mode"),
    "lydian": _m("lydian", "Lydian", "1 2 3 #4 5 6 7", "major", kind="mode"),
    "mixolydian": _m("mixolydian", "Mixolydian", "1 2 3 4 5 6 b7", "major", kind="mode"),
    "locrian": _m("locrian", "Locrian", "1 b2 b3 4 b5 b6 b7", "minor", kind="mode"),
    "major_pentatonic": _m("major_pentatonic", "Major pentatonic", "1 2 3 5 6", "major"),
    "minor_pentatonic": _m("minor_pentatonic", "Minor pentatonic", "1 b3 4 5 b7", "minor"),
    "blues": _m("blues", "Blues scale", "1 b3 4 b5 5 b7", "minor",
                caveats=("Blue notes are bent between these pitches in practice, by anything from a quarter tone to a semitone.",)),
}


def modes(*ids: str):
    return tuple(MODES[i] for i in ids)


METERS = {
    "4_4": cycle("4_4", "4/4", 4, 4, (4, 4), structure=("strong", "weak", "medium", "weak"),
                 accents=(1, .3, .6, .3), source=SOURCE, confidence="high"),
    "3_4": cycle("3_4", "3/4", 3, 4, (3, 4), structure=("strong", "weak", "weak"),
                 accents=(1, .3, .3), source=SOURCE, confidence="high"),
    "2_4": cycle("2_4", "2/4", 2, 4, (2, 4), structure=("strong", "weak"), accents=(1, .4),
                 source=SOURCE, confidence="high"),
    "6_8": cycle("6_8", "6/8", 2, 3, (6, 8), structure=("strong", "medium"), accents=(1, .6),
                 source=SOURCE, confidence="high"),
    "12_8": cycle("12_8", "12/8", 4, 3, (12, 8), structure=("strong", "weak", "medium", "weak"),
                  accents=(1, .3, .6, .3), source=SOURCE, confidence="high",
                  caveats=("Often the written form of a swung 4/4.",)),
}


def meters(*ids: str):
    return tuple(METERS[i] for i in ids)


# Staff notation has no sign for a slow, wide oscillation. It must never become a trill:
# a trill names two notes, which is a different thing.
STAFF_ORNAMENTS = (
    OrnamentRule(neutral_kind="vibrato", local_name="vibrato", action="drop",
                 approximation="nothing; vibrato is left to the performer"),
    OrnamentRule(neutral_kind="oscillation", local_name="pitch oscillation", action="drop"),
    OrnamentRule(neutral_kind="glide", local_name="glissando", action="approximate", staff_mark="gliss",
                 symbol="/", symbol_down="\\", approximation="a glissando line"),
    OrnamentRule(neutral_kind="grace", local_name="grace note", action="approximate", staff_mark="grace",
                 approximation="a grace note"),
)

PARSERS = ("abc", "musicxml", "midi", "note_names")
STAFF_RENDERERS = ("svg", "abc", "names", "musicxml", "midi", "describe")

MONOPHONIC_CAVEAT = "One melody line only: chords and harmony are not transcribed."
