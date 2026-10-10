"""Western pop: 12-TET, 4/4 by default, major and minor keys, syncopation kept on a 16th grid."""
from __future__ import annotations

from . import _tet12 as tet
from .base import Adapter, FormatMeta, OrnamentRule, export

ORNAMENTS = (
    OrnamentRule(neutral_kind="vibrato", local_name="vibrato", action="drop",
                 approximation="nothing; vibrato is left to the singer"),
    OrnamentRule(neutral_kind="oscillation", local_name="pitch oscillation", action="drop"),
    OrnamentRule(neutral_kind="glide", local_name="slide", action="approximate", staff_mark="gliss",
                 symbol="/", symbol_down="\\", approximation="a slide line"),
    OrnamentRule(neutral_kind="grace", local_name="grace note", action="approximate", staff_mark="grace",
                 approximation="a grace note"),
)

ADAPTER = Adapter(FormatMeta(
    id="western_pop",
    display_name="Western Pop",
    description="Twelve equal pitches, 4/4 by default, with syncopated rhythm kept on a sixteenth-note grid.",
    aliases=("pop", "pop music", "lead sheet"),
    family="western_popular",
    order=30,
    pitch_system="12tet",
    tonic_model="fixed_key_signature",
    naming="absolute",
    tonic_word="key note",
    grid=tet.GRID,
    mode_table=tet.modes("major", "natural_minor", "mixolydian", "dorian", "major_pentatonic", "minor_pentatonic"),
    rhythm_model="meter_bars",
    rhythm_templates=tet.meters("4_4", "6_8", "3_4", "12_8"),
    default_template_id="4_4",
    ornament_table=ORNAMENTS,
    renderers=tet.STAFF_RENDERERS,
    primary_renderer="svg",
    input_parsers=tet.PARSERS,
    review_status="unreviewed",
    reviewer_note="Standard theory tables; not yet checked by a second person.",
    caveats=(
        tet.MONOPHONIC_CAVEAT,
        "Only the lead line is written. Chords, verse and chorus structure are not detected.",
        "From a short single line, pop and rock usually cannot be told apart.",
    ),
    analysis_profile="western",
    detect_priors={
        "neutral_mass": (0.0, 0.05, 2.0),
        "grid12_rms_cents": (0.0, 20.0, 1.2),
        "oscillation_share": (0.0, 0.12, 1.2),
        "drone": (0.0, 0.2, 0.5),
        "free_time": (0.0, 0.0, 0.6),
        "spectral_flatness": (0.0, 0.2, 0.3),
    },
))

META, parse_notes, ir_to_format, render, score_fit = export(ADAPTER)
