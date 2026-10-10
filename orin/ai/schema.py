"""The refined music spec (spec 10.4) and chat intents (spec 10.3).

``MusicSpec`` is the validated form the API and the UI use. The model-facing schema
that Gemini fills (all fields required, nullable where empty, no defaults) lives beside
the Gemini client, because structured output only accepts a plain schema.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Intent = Literal["chat", "generate", "transcribe", "convert", "clarify"]
Vocals = Literal["none", "wordless", "lyrics"]

MIN_DURATION_S = 10
MAX_DURATION_S = 300  # provider limit is longer; kept at 5 minutes until a longer track is seen to work


class SpecSection(BaseModel):
    genre_id: str  # a registry id, or a plain genre word such as "jazz"
    description: str
    duration_s: int


class MusicSpec(BaseModel):
    genre_id: str  # a registry id, a plain genre word, or "fusion"
    sections: list[SpecSection] = Field(default_factory=list)  # more than one = mid-track transition
    mood: list[str] = Field(default_factory=list)
    tempo_bpm: Optional[int] = None
    key_or_tonic: Optional[str] = None  # "D", "natural", or None
    mode_hint: Optional[str] = None  # e.g. "Mohanam"; a hint to the generator, not a guarantee
    instruments: list[str] = Field(default_factory=list)
    vocals: Vocals = "none"
    duration_s: int = 60
    structure_notes: Optional[str] = None
    avoid: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)  # what Orin filled in; shown to the user
    learning_goal: Optional[str] = None
