"""Result types for the AI layer, and the facade the server calls (spec 9.4, 9.5, 10).

``orin/ai/service.py`` implements these functions. Each one picks the live provider
when a key is configured and the offline stand-in otherwise (ORIN_OFFLINE=1 forces the
stand-in), and never raises for a provider failure: it degrades to the stand-in and
says so in the result.

    status() -> AIStatus
    sanitize_prompt(text) -> SanitizeResult
    classify_intent(text, *, history, has_attachment=False, genre_id=None) -> IntentResult
    chat_reply_stream(text, *, history, prefs=None, context=None) -> Iterator[str]
    refine_spec(text, *, history, prefs=None, previous_specs=(), genre_id=None) -> SpecResult
    explain_loss(report, *, item_indexes=(), source_meta=None, target_meta, level="beginner",
                 question=None, language="en") -> ExplanationResult
    explain_music(spec, *, level="beginner") -> str
    summarize(history) -> str
    compose_music(spec, *, request_id, cancelled=None) -> MusicResult
    narrate(text, *, request_id) -> NarrationResult

``history`` is a list of ``Turn``. ``prefs`` is the user's preference dict
(level, language, notes). ``context`` is extra grounding text, such as a loss report.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .schema import Intent, MusicSpec

Level = Literal["beginner", "intermediate", "advanced"]


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class AIStatus(BaseModel):
    """True = that service is on its stand-in."""

    llm_offline: bool
    music_offline: bool
    tts_offline: bool
    reason: Optional[str] = None
    llm_model: Optional[str] = None
    music_model: Optional[str] = None
    tts_model: Optional[str] = None


class SanitizeResult(BaseModel):
    """Prompt policy (spec 10.7): named artists and songs become plain descriptions."""

    text: str  # safe to pass on
    notes: list[str] = Field(default_factory=list)  # what was changed, to tell the user
    blocked: bool = False  # moderation refused the prompt outright
    reason: Optional[str] = None


class IntentResult(BaseModel):
    intent: Intent
    source: Literal["gemini", "offline"] = "offline"


class SpecResult(BaseModel):
    ok: bool  # False: could not build a valid spec even after one repair attempt
    spec: Optional[MusicSpec] = None
    notes: list[str] = Field(default_factory=list)  # assumptions, rewrites, unsupported constraints
    question: Optional[str] = None  # what to ask the user when ok is False
    source: Literal["gemini", "offline"] = "offline"


class ExplanationResult(BaseModel):
    text: str
    try_this: list[str] = Field(default_factory=list)
    source: Literal["gemini", "offline"] = "offline"
    key: str  # cache key: explain_key(s) + level + language


class ProviderMeta(BaseModel):
    name: str  # elevenlabs | gemini | offline
    model: Optional[str] = None
    request_id: Optional[str] = None
    duration_s: Optional[float] = None


class MusicResult(BaseModel):
    audio: bytes
    media_type: str  # audio/mpeg from the provider, audio/wav from the stand-in
    ext: str
    standin: bool  # True: a synthesised placeholder, clearly labelled as one
    standin_reason: Optional[str] = None  # friendly: "Music generation needs a paid ElevenLabs plan."
    provider: ProviderMeta
    notes: list[str] = Field(default_factory=list)  # e.g. the provider's suggested rewrite was used


class NarrationResult(BaseModel):
    audio: Optional[bytes] = None  # None when on the stand-in: the browser reads the text itself
    media_type: Optional[str] = None
    ext: Optional[str] = None
    standin: bool
    message: Optional[str] = None  # e.g. how to set ELEVENLABS_VOICE_ID
    provider: ProviderMeta
