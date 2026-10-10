"""Western staff notation: 12-TET, bar lines, key signatures. The baseline renderer target."""
from __future__ import annotations

from . import _tet12 as tet
from .base import Adapter, FormatMeta, export

ADAPTER = Adapter(FormatMeta(
    id="western_classical",
    display_name="Western (staff notation)",
    description="Twelve equal pitches per octave, bar lines and key signatures.",
    aliases=("western", "classical", "staff", "sheet music"),
    family="western_art",
    order=10,
    pitch_system="12tet",
    tonic_model="fixed_key_signature",
    naming="absolute",
    tonic_word="tonic",
    grid=tet.GRID,
    mode_table=tet.modes("major", "natural_minor", "harmonic_minor", "melodic_minor",
                         "dorian", "phrygian", "lydian", "mixolydian", "locrian",
                         "major_pentatonic", "minor_pentatonic"),
    rhythm_model="meter_bars",
    rhythm_templates=tet.meters("4_4", "3_4", "2_4", "6_8"),
    default_template_id="4_4",
    ornament_table=tet.STAFF_ORNAMENTS,
    writes_dynamics=False,
    renderers=tet.STAFF_RENDERERS,
    primary_renderer="svg",
    input_parsers=tet.PARSERS,
    review_status="unreviewed",
    reviewer_note="Standard theory tables; not yet checked by a second person.",
    caveats=(
        tet.MONOPHONIC_CAVEAT,
        "Staff notation has twelve pitches per octave: anything in between is rounded and reported.",
        "A key signature is chosen to keep accidentals few. It is a spelling aid, not a claim about the mode.",
    ),
    analysis_profile="western",
    detect_priors={
        "neutral_mass": (0.0, 0.05, 2.0),
        "grid12_rms_cents": (0.0, 15.0, 1.5),
        "oscillation_share": (0.0, 0.10, 1.5),
        "drone": (0.0, 0.2, 0.5),
        "spectral_flatness": (0.0, 0.15, 0.5),
    },
))

META, parse_notes, ir_to_format, render, score_fit = export(ADAPTER)
