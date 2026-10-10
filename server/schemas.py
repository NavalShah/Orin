"""API contract for /api/v1 (spec 13.2). The server implements it; lib/api.ts consumes it.

Auth      ``Authorization: Bearer <token>`` on every endpoint except POST /auth/session,
          GET /health, GET /formats, GET /glossary, and asset file URLs carrying a signature.
          Every query is scoped to the token's user (spec 10.2).
Errors    Every non-2xx response is ``ErrorResponse``: {"error": {code, message, hint, retryable}}.
Streams   ``text/event-stream``; each event is one line ``data: <json>`` followed by a blank line.

ENDPOINTS (all under /api/v1)
  GET    /health                                          -> Health
  POST   /auth/session            SessionRequest          -> Session        (profile picker: get or create by name)
  GET    /auth/me                                         -> User
  PATCH  /auth/me                 PrefsUpdate             -> User
  POST   /auth/logout                                     -> Ok
  GET    /formats                                         -> FormatsResponse (registry, for the dropdowns)
  GET    /glossary                                        -> list[GlossaryEntry]
  POST   /assets                  multipart: file, source -> Asset          (upload; content-sniffed, size-capped)
  GET    /assets                  ?kind=&source=          -> list[Asset]    (the Library, newest first)
  GET    /assets/{id}                                     -> Asset
  GET    /assets/{id}/file        ?exp=&sig= or Bearer    -> the file bytes
  GET    /assets/{id}/peaks       ?buckets=               -> Peaks
  DELETE /assets/{id}                                     -> Ok
  POST   /convert                 ConvertRequest          -> JobRef
  GET    /jobs/{id}                                       -> Job
  GET    /jobs/{id}/events                                -> stream of Job  (ends at a terminal status)
  POST   /jobs/{id}/cancel                                -> Job
  GET    /conversions                                     -> list[ConversionSummary]
  GET    /conversions/{id}                                -> ConversionOut
  GET    /conversions/{id}/ir                             -> the full IR as JSON
  POST   /conversions/{id}/rerun  RerunRequest            -> JobRef         (reuses cached stages)
  POST   /explain                 ExplainRequest          -> Explanation
  POST   /narrate                 NarrateRequest          -> Narration
  GET    /chat/threads                                    -> list[Thread]
  POST   /chat/threads            ThreadCreate            -> Thread
  PATCH  /chat/threads/{id}       ThreadUpdate            -> Thread
  DELETE /chat/threads/{id}                               -> Ok
  GET    /chat/threads/{id}/messages                      -> list[Message]
  POST   /chat/threads/{id}/messages  MessageCreate       -> stream of ChatEvent
  POST   /generate                GenerateRequest         -> JobRef         (job result is a Message with an audio card)
  GET    /projects                                        -> list[ProjectSummary]
  POST   /projects                ProjectSave             -> ProjectOut
  GET    /projects/{id}                                   -> ProjectOut
  PUT    /projects/{id}           ProjectSave             -> ProjectOut
  POST   /projects/{id}/duplicate                         -> ProjectOut
  DELETE /projects/{id}                                   -> Ok

Chat flow (spec 10.3). POST .../messages streams: one ``intent`` event, then
  chat / clarify   ``delta`` events with reply text
  generate         a ``card`` of kind "spec" (the user confirms it, then the client calls POST /generate)
  transcribe       a ``job`` event and a ``card`` of kind "conversion" (the client can open /studio?conversion=ID)
then one ``message`` event with the stored assistant message, then ``done``.
An ``error`` event can arrive at any point and ends the stream.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from orin.ai.schema import Intent, MusicSpec
from orin.errors import ParseProblem
from orin.formats.base import FormatScore
from orin.ir.models import (
    Analysis,
    Cycle,
    FormatCandidate,
    IRWarning,
    Mode,
    ModeCandidate,
    Source,
    StageLog,
    Tempo,
    Tonic,
)
from orin.loss.models import LossReport
from orin.render import TokenRef

API_PREFIX = "/api/v1"

Level = Literal["beginner", "intermediate", "advanced"]
AssetKind = Literal["audio", "midi", "notation", "stem", "image"]
AssetSource = Literal["upload", "generated", "bounce", "conversion", "narration", "standin"]
JobStatus = Literal["queued", "running", "awaiting_input", "done", "failed", "cancelled"]
NotesSyntax = Literal["auto", "sargam", "note_names", "abc", "musicxml", "midi"]


# --- common -----------------------------------------------------------------

class ErrorBody(BaseModel):
    code: str
    message: str  # plain language, safe to show
    hint: Optional[str] = None
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ErrorBody


class Ok(BaseModel):
    ok: bool = True


class OfflineStatus(BaseModel):
    """True means that service is running on its built-in stand-in (spec D11)."""

    llm: bool
    music: bool
    tts: bool
    reason: Optional[str] = None  # e.g. "No GEMINI_API_KEY set"


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    offline: OfflineStatus


# --- auth -------------------------------------------------------------------

class UserPrefs(BaseModel):
    level: Level = "beginner"
    language: str = "en"
    labels: Literal["tradition", "solfege", "letters"] = "tradition"  # note-name style (spec 13.5)
    plain_language: bool = True
    notes: list[str] = Field(default_factory=list)  # things the user told Orin, e.g. "I play piano"; user-editable


class User(BaseModel):
    id: str
    name: str
    created_at: str
    prefs: UserPrefs


class SessionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class Session(BaseModel):
    token: str
    user: User


class PrefsUpdate(BaseModel):
    level: Optional[Level] = None
    language: Optional[str] = None
    labels: Optional[Literal["tradition", "solfege", "letters"]] = None
    plain_language: Optional[bool] = None
    notes: Optional[list[str]] = None


# --- registry ---------------------------------------------------------------

class ModeInfo(BaseModel):
    id: str
    label: str
    kind: str
    pitches_cents: list[float]
    degree_names: list[str]
    source: str
    confidence: str
    caveats: list[str]
    lookalikes: list[str]  # "format:mode" same pitches; a trailing "~" marks a rotation


class TemplateInfo(BaseModel):
    id: str
    label: str
    cycle_beats: int
    subdivision: int
    structure: list[str]
    accent_pattern: list[float]
    source: str
    confidence: str
    caveats: list[str]


class FormatInfo(BaseModel):
    id: str
    display_name: str
    description: str
    aliases: list[str]
    family: str
    family_label: str
    pitch_system: str
    tonic_model: str
    rhythm_model: str
    tonic_word: str  # "Sa", "tonic", "finalis"
    review_status: Literal["unreviewed", "self_checked", "practitioner_reviewed"]
    reviewer_note: str
    caveats: list[str]
    renderers: list[str]
    primary_renderer: str
    input_parsers: list[str]
    monophonic_only: bool
    modes: list[ModeInfo]
    rhythm_templates: list[TemplateInfo]
    default_template_id: Optional[str] = None


class FamilyInfo(BaseModel):
    id: str
    label: str
    format_ids: list[str]


class RendererInfo(BaseModel):
    kind: str
    label: str
    media_type: str
    ext: str


class Limits(BaseModel):
    max_upload_mb: float
    max_audio_seconds: float
    max_generate_seconds: int
    daily_generations: int


class FormatsResponse(BaseModel):
    formats: list[FormatInfo]
    families: list[FamilyInfo]  # display order, for grouped dropdowns (spec 4.5)
    renderers: list[RendererInfo]
    limits: Limits
    offline: OfflineStatus
    sargam_help: str  # the accepted typed-sargam grammar, shown in the UI help (spec 7.14)
    detect_limits: list[str]  # what auto-detect cannot tell apart (spec 8.3)
    monophonic_note: str  # the one-melody-line limit, stated in the UI (spec 7.4)


class GlossaryEntry(BaseModel):
    term: str
    definition: str  # plain language, hedged
    tradition: Optional[str] = None


# --- assets (the Library) -----------------------------------------------------

class Asset(BaseModel):
    id: str
    kind: AssetKind
    source: AssetSource
    filename: str  # display name only; never used as a path
    media_type: str
    size_bytes: int
    sha256: str
    duration_s: Optional[float] = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    url: str  # signed, relative to the API origin; usable in <audio src> and download links
    standin: bool = False  # produced by an offline stand-in, not by the real provider


class Peaks(BaseModel):
    duration_s: float
    buckets: int
    peaks: list[float]  # absolute peak per bucket, 0..1


# --- convert (Notation Studio) ------------------------------------------------

class ConvertOptionsIn(BaseModel):
    tonic_hz: Optional[float] = None  # "Set tonic" override
    mode_id: Optional[str] = None  # source mode chosen by the user
    target_mode_id: Optional[str] = None  # apply a target mode; never applied unless asked
    sensitivity: Optional[float] = None
    cycle_template_id: Optional[str] = None  # source cycle
    target_template_id: Optional[str] = None
    tempo_bpm: Optional[float] = None
    voice: Optional[Literal["auto", "male", "female", "instrument"]] = None
    separate: Optional[Literal["auto", "on", "off"]] = None
    melody_stem: Optional[Literal["auto", "vocals", "other"]] = None
    force_measured: bool = False


class ConvertRequest(BaseModel):
    """Exactly one of asset_id (audio or a notes file) or notes_text."""

    asset_id: Optional[str] = None
    notes_text: Optional[str] = None
    notes_syntax: NotesSyntax = "auto"
    input_format: str = "auto"  # a format id, or "auto" (spec 8)
    output_format: str
    options: ConvertOptionsIn = Field(default_factory=ConvertOptionsIn)


class RerunRequest(BaseModel):
    """Change any of these and re-run from the first affected stage (spec 11.3 step 7)."""

    input_format: Optional[str] = None
    output_format: Optional[str] = None
    tonic_hz: Optional[float] = None
    mode_id: Optional[str] = None
    target_mode_id: Optional[str] = None
    sensitivity: Optional[float] = None
    cycle_template_id: Optional[str] = None
    target_template_id: Optional[str] = None
    tempo_bpm: Optional[float] = None
    force_measured: Optional[bool] = None


class JobRef(BaseModel):
    job_id: str
    conversion_id: Optional[str] = None


class JobResult(BaseModel):
    kind: Literal["conversion", "asset", "message"]
    id: str


class Job(BaseModel):
    id: str
    type: Literal["convert", "generate", "narrate"]
    status: JobStatus
    progress: float  # 0..1
    stage: Optional[str] = None  # ingest, separate, pitch, tonic, segment, rhythm, notation...
    message: Optional[str] = None
    error: Optional[ErrorBody] = None
    result: Optional[JobResult] = None
    created_at: str
    updated_at: str


class EventSummary(BaseModel):
    id: str
    onset_s: float
    offset_s: float
    pitch_cents: float
    ornament_kind: Optional[str] = None
    ornament_amplitude_cents: float = 0.0
    confidence: float


class ContourOut(BaseModel):
    """The pitch contour, thinned to at most a few thousand points for plotting."""

    t_s: list[float]
    cents: list[Optional[float]]


class IRSummary(BaseModel):
    source: Source
    tonic: Tonic
    mode: Mode
    tempo: Tempo
    cycle: Cycle
    analysis: Analysis
    note_count: int
    duration_s: float
    events: list[EventSummary]
    contour: ContourOut
    warnings: list[IRWarning]
    low_confidence_event_ids: list[str]  # confidence under 0.5: flag these in the UI (spec 6.5)
    processing_log: list[StageLog]  # "How was this made?"


class OutputFile(BaseModel):
    kind: str
    label: str
    media_type: str
    ext: str
    text: Optional[str] = None
    pages: list[str] = Field(default_factory=list)  # SVG strings
    tokens: list[TokenRef] = Field(default_factory=list)  # note positions inside ``text``
    note_ids: list[str] = Field(default_factory=list)  # SVG element ids in note order
    asset_id: Optional[str] = None
    url: Optional[str] = None  # download link
    warnings: list[str] = Field(default_factory=list)


class MissingOut(BaseModel):
    kind: str
    message: str
    alternative: Optional[str] = None


class ConversionOut(BaseModel):
    id: str
    status: Literal["running", "awaiting_style", "done", "failed"]
    created_at: str
    input_format: str  # what the user asked for: a format id or "auto"
    source_format: Optional[str] = None  # the format the analysis actually ran with
    detected: list[FormatCandidate] = Field(default_factory=list)  # top 3, never a bare label
    needs_style_choice: bool = False  # low confidence or small margin: "Not sure - please choose"
    detect_message: Optional[str] = None
    output_format: str
    same_format: bool = False  # clean-up / quantize mode
    source_asset: Optional[Asset] = None
    notes_text: Optional[str] = None
    ir: Optional[IRSummary] = None
    score: Optional[FormatScore] = None
    loss: Optional[LossReport] = None
    outputs: list[OutputFile] = Field(default_factory=list)
    missing: list[MissingOut] = Field(default_factory=list)
    suggestions: list[ModeCandidate] = Field(default_factory=list)
    how_scored: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    parse_problems: list[ParseProblem] = Field(default_factory=list)  # typed-notes errors by line and column
    error: Optional[ErrorBody] = None


class ConversionSummary(BaseModel):
    id: str
    created_at: str
    status: str
    input_format: str
    source_format: Optional[str] = None
    output_format: str
    filename: Optional[str] = None
    fidelity: Optional[float] = None
    note_count: Optional[int] = None


# --- explain and narrate ------------------------------------------------------

class ExplainRequest(BaseModel):
    conversion_id: str
    item_indexes: list[int] = Field(default_factory=list)  # into loss.items; empty = the whole report
    level: Optional[Level] = None  # default: the user's preference
    question: Optional[str] = None  # e.g. "Why can't a piano play this?"


class Explanation(BaseModel):
    text: str
    try_this: list[str]
    source: Literal["gemini", "offline"]
    cached: bool
    key: str
    level: Level


class NarrateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2500)


class Narration(BaseModel):
    """``standin`` true means no audio was made; the client may read ``text`` with the
    browser's own speech synthesis. The text is always the transcript."""

    asset: Optional[Asset] = None
    standin: bool
    text: str
    message: Optional[str] = None


# --- chat ---------------------------------------------------------------------

class Thread(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    summary: Optional[str] = None


class ThreadCreate(BaseModel):
    title: Optional[str] = None


class ThreadUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ProviderInfo(BaseModel):
    name: str  # "elevenlabs", "gemini", "offline"
    model: Optional[str] = None
    request_id: Optional[str] = None
    duration_s: Optional[float] = None


class Card(BaseModel):
    """Something richer than text inside a chat message."""

    kind: Literal["spec", "audio", "conversion", "notice"]
    text: Optional[str] = None
    spec: Optional[MusicSpec] = None  # kind "spec": "Here's what I'll make"
    notes: list[str] = Field(default_factory=list)  # assumptions, rewrites of artist names, unsupported asks
    asset: Optional[Asset] = None  # kind "audio"
    standin: bool = False
    standin_reason: Optional[str] = None
    provider: Optional[ProviderInfo] = None
    conversion_id: Optional[str] = None  # kind "conversion"
    job_id: Optional[str] = None


class Message(BaseModel):
    id: str
    thread_id: str
    role: Literal["user", "assistant"]
    text: str
    cards: list[Card] = Field(default_factory=list)
    track_ids: list[str] = Field(default_factory=list)
    spec: Optional[MusicSpec] = None
    created_at: str
    meta: dict[str, Any] = Field(default_factory=dict)


class ChatContext(BaseModel):
    conversion_id: Optional[str] = None  # "Ask Orin about this loss report"
    item_indexes: list[int] = Field(default_factory=list)


class MessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    attachment_asset_ids: list[str] = Field(default_factory=list)
    genre_id: Optional[str] = None  # genre chip
    context: Optional[ChatContext] = None


class ChatEvent(BaseModel):
    type: Literal["intent", "delta", "card", "job", "message", "error", "done"]
    intent: Optional[Intent] = None
    text: Optional[str] = None
    card: Optional[Card] = None
    job_id: Optional[str] = None
    message: Optional[Message] = None
    error: Optional[ErrorBody] = None


class GenerateRequest(BaseModel):
    spec: MusicSpec
    thread_id: Optional[str] = None  # append the result to this thread as an assistant message


# --- workspace projects -------------------------------------------------------

class ProjectSave(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    project: dict[str, Any]  # the Workspace's own versioned JSON; opaque to the server
    version: int = 1


class ProjectOut(BaseModel):
    id: str
    name: str
    project: dict[str, Any]
    version: int
    created_at: str
    updated_at: str


class ProjectSummary(BaseModel):
    id: str
    name: str
    version: int
    updated_at: str
    track_count: int
    clip_count: int
    duration_s: float
    tempo: Optional[float] = None


ALL_MODELS: tuple[type[BaseModel], ...] = (
    ErrorBody, ErrorResponse, Ok, OfflineStatus, Health,
    UserPrefs, User, SessionRequest, Session, PrefsUpdate,
    ModeInfo, TemplateInfo, FormatInfo, FamilyInfo, RendererInfo, Limits, FormatsResponse, GlossaryEntry,
    Asset, Peaks,
    ConvertOptionsIn, ConvertRequest, RerunRequest, JobRef, JobResult, Job,
    EventSummary, ContourOut, IRSummary, OutputFile, MissingOut, ConversionOut, ConversionSummary,
    ExplainRequest, Explanation, NarrateRequest, Narration,
    Thread, ThreadCreate, ThreadUpdate, ProviderInfo, Card, Message, ChatContext, MessageCreate, ChatEvent,
    GenerateRequest, ProjectSave, ProjectOut, ProjectSummary,
)
