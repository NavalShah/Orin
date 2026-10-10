"""Analysis options and the pipeline's public signatures (spec section 7).

``orin/pipeline.py`` implements:

    audio_to_ir(path, options=None, *, cache=None, progress=None) -> IR
    notes_to_ir(data, *, format_id=None, syntax=None, filename=None, options=None) -> IR
    reanalyze(ir_or_path, options, *, cache=None, progress=None) -> IR   # user changed tonic, mode, style...

``progress(stage, fraction, message)`` is called as each stage starts and ends.
Stages run in the order of ``STAGES``; each is cached by (content hash, stage, params)
and fails soft: on an error it falls back, adds a warning to the IR and continues.
"""
from __future__ import annotations

from typing import Callable, Literal, Optional

from .ir.models import Model

STAGES = (
    "ingest", "preprocess", "separate", "pitch", "voicing", "detect",
    "tonic", "segment", "ornament", "rhythm", "mode", "assemble",
)

# Stages that depend on the chosen style. A user override re-runs only these, reusing
# the cached pitch track (spec 8.2).
STYLE_DEPENDENT_STAGES = ("voicing", "tonic", "segment", "ornament", "rhythm", "mode", "assemble")

Progress = Callable[[str, float, str], None]


class AnalysisOptions(Model):
    style: str = "auto"  # a format id, or "auto" for style auto-detect
    tonic_hz: Optional[float] = None  # "Set Sa / tonic" override
    mode_id: Optional[str] = None  # manual mode choice; overrides inference
    sensitivity: float = 1.0  # above 1 splits notes more readily, below 1 merges more
    cycle_template_id: Optional[str] = None
    tempo_bpm: Optional[float] = None
    voice: Literal["auto", "male", "female", "instrument"] = "auto"
    separate: Literal["auto", "on", "off"] = "auto"
    melody_stem: Literal["auto", "vocals", "other"] = "auto"
    trackers: Literal["auto", "crepe", "pyin", "both"] = "auto"
    max_upload_mb: float = 10.0
    max_seconds: float = 300.0
