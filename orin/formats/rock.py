"""Rock: 12-TET, riff-based, pentatonic and blues-scale tendencies. Staff plus six-string guitar tab.

Tab can carry things staff cannot: a bend or a slide is one symbol on a fret number. That
makes rock a useful contrast in the loss report.
"""
from __future__ import annotations

from . import _tet12 as tet
from .base import Adapter, FormatMeta, OrnamentRule, export

ORNAMENTS = (
    OrnamentRule(neutral_kind="vibrato", local_name="vibrato", action="approximate", symbol="~",
                 approximation="a vibrato mark (~) in the tab"),
    OrnamentRule(neutral_kind="oscillation", max_amplitude_cents=120, local_name="wide vibrato",
                 action="approximate", symbol="~", approximation="a vibrato mark (~) in the tab"),
    OrnamentRule(neutral_kind="oscillation", min_amplitude_cents=120.0001, local_name="pitch oscillation",
                 action="drop"),
    OrnamentRule(neutral_kind="glide", max_amplitude_cents=250, local_name="bend", action="approximate",
                 symbol="b", symbol_down="r", staff_mark="bend",
                 approximation="a string bend (b) or release (r) in the tab"),
    OrnamentRule(neutral_kind="glide", min_amplitude_cents=250.0001, local_name="slide", action="approximate",
                 symbol="/", symbol_down="\\", staff_mark="gliss", approximation="a slide (/ or \\) in the tab"),
    OrnamentRule(neutral_kind="grace", local_name="grace note", action="approximate", staff_mark="grace",
                 symbol="h", symbol_down="p", approximation="a hammer-on (h) or pull-off (p) in the tab"),
)

ADAPTER = Adapter(FormatMeta(
    id="rock",
    display_name="Rock",
    description="Twelve equal pitches, riff-based, written as staff plus six-string guitar tab.",
    aliases=("rock music", "guitar", "tab", "guitar tab"),
    family="western_popular",
    order=40,
    pitch_system="12tet",
    tonic_model="fixed_key_signature",
    naming="absolute",
    tonic_word="key note",
    grid=tet.GRID,
    mode_table=tet.modes("minor_pentatonic", "blues", "natural_minor", "mixolydian", "dorian", "major",
                         "major_pentatonic"),
    rhythm_model="meter_bars",
    rhythm_templates=tet.meters("4_4", "6_8", "12_8"),
    default_template_id="4_4",
    ornament_table=ORNAMENTS,
    renderers=("svg", "tab", "abc", "names", "musicxml", "midi", "describe"),
    primary_renderer="svg",
    input_parsers=tet.PARSERS,
    review_status="unreviewed",
    reviewer_note="Standard theory tables; the tab fingering is a simple lowest-position choice.",
    caveats=(
        tet.MONOPHONIC_CAVEAT,
        "Power chords and riffs with more than one note at a time are reduced to their top line.",
        "Tab is in standard tuning (E A D G B E). Fingering is one playable option, not the player's choice.",
        "From a short single line, pop and rock usually cannot be told apart.",
    ),
    analysis_profile="rock",
    detect_priors={
        "neutral_mass": (0.0, 0.06, 1.5),
        "grid12_rms_cents": (0.0, 25.0, 1.0),
        "oscillation_share": (0.0, 0.2, 0.8),
        "spectral_flatness": (0.15, 1.0, 1.5),
        "free_time": (0.0, 0.0, 0.6),
        "drone": (0.0, 0.2, 0.4),
    },
))

META, parse_notes, ir_to_format, render, score_fit = export(ADAPTER)
