"""Gemini wrapper (spec 10.10): google-genai SDK, the ``generate_content`` API.

Every function here either returns a contract type or raises ``GeminiError``; the
facade in ``service.py`` catches that and uses the offline stand-in. The model id comes
from GEMINI_MODEL (spec D14) and is never written here.

Structured output only accepts a plain schema (every field required, Optional for
nullable, no defaults, no dict fields), so the model fills the ``*Draft`` classes below
and the code converts them to the contract types.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator, Literal, NamedTuple, Optional, Sequence

from pydantic import BaseModel, ValidationError

from . import offline
from .config import AIConfig, redact
from .schema import MAX_DURATION_S, MIN_DURATION_S, MusicSpec, SpecSection
from .types import ExplanationResult, IntentResult, SpecResult, Turn

try:  # the SDK is optional at import time: without it the facade stays on the stand-ins
    import httpx
    from google import genai
    from google.genai import errors, types
except Exception:  # pragma: no cover - exercised only when the package is missing
    httpx = None  # type: ignore[assignment]
    genai = None  # type: ignore[assignment]
    errors = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

TIMEOUT_MS = 30_000  # the SDK has no timeout by default
RETRY_ATTEMPTS = 3  # and no retries
RETRY_STATUS = [408, 429, 500, 502, 503, 504]
HISTORY_BUDGET_CHARS = 6_000  # token budget for context, counted in characters (spec 10.10)
MAX_TURN_CHARS = 2_000
MAX_CONTEXT_CHARS = 4_000

PROMPT_DIR = Path(__file__).parent / "prompts"
PROMPT_NAMES = ("system", "intent", "spec_refine", "loss_explain", "explain_music", "chat")
_VERSION_RE = re.compile(r"^\s*<!--\s*version:\s*(\d+)\s*-->[ \t]*\n?")

_BLOCKING_FINISH = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE"}
# Numbers a general explanation may use without them being in the report.
_GENERAL_NUMBERS = (0.0, 1.0, 2.0, 12.0, 24.0, 100.0, 440.0, 1200.0)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class GeminiError(Exception):
    """A Gemini call failed. ``friendly`` is one sentence that is safe to show a user."""

    def __init__(self, message: str, *, friendly: str = "Gemini could not answer just now.", code: Optional[int] = None):
        super().__init__(redact(message))
        self.friendly = friendly
        self.code = code


class GeminiBlocked(GeminiError):
    """The prompt or the reply was blocked. This does not raise in the SDK: the text is just empty."""


class DraftInvalid(GeminiError):
    """The model's JSON did not validate against the schema."""


class SpecProblem(ValueError):
    """A draft spec parsed but breaks a rule checked in code (spec 10.3 step 5)."""


def _friendly_for(code: Optional[int]) -> str:
    if code in (401, 403):
        return "The Gemini key was not accepted."
    if code == 404:
        return "The model named in GEMINI_MODEL was not found."
    if code == 429:
        return "Gemini is rate-limited right now."
    if code is not None and code >= 500:
        return "Gemini had a server problem."
    return "Gemini could not answer just now."


def _wrap(exc: Exception) -> GeminiError:
    if isinstance(exc, GeminiError):
        return exc
    if errors is not None and isinstance(exc, errors.APIError):
        code = exc.code if isinstance(exc.code, int) else None
        return GeminiError(f"Gemini API error {code}: {exc.message}", friendly=_friendly_for(code), code=code)
    if httpx is not None and isinstance(exc, httpx.TimeoutException):
        return GeminiError("Gemini request timed out", friendly="Gemini took too long to answer.")
    if httpx is not None and isinstance(exc, httpx.HTTPError):
        return GeminiError(f"Gemini network error: {type(exc).__name__}", friendly="Gemini could not be reached.")
    return GeminiError(f"Gemini call failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def available() -> bool:
    return genai is not None and types is not None


def make_client(api_key: str) -> Any:
    """Build the SDK client. Tests replace this with a factory that returns a fake."""
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=RETRY_ATTEMPTS, initial_delay=1.0, max_delay=8.0,
                                                 http_status_codes=RETRY_STATUS),
        ),
    )


_clients: dict[tuple[Any, str], Any] = {}


def get_client(cfg: AIConfig) -> Any:
    key = (make_client, cfg.gemini_api_key)  # a replaced factory gets its own entry
    if key not in _clients:
        _clients.clear()
        _clients[key] = make_client(cfg.gemini_api_key)
    return _clients[key]


def _safety_settings() -> list[Any]:
    return [
        types.SafetySetting(category=category, threshold="BLOCK_MEDIUM_AND_ABOVE")
        for category in ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                         "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")
    ]


def _config(system: str, schema: Optional[type[BaseModel]] = None) -> Any:
    # No temperature, top_p or top_k: current models reject them.
    kwargs: dict[str, Any] = {"system_instruction": system, "safety_settings": _safety_settings()}
    if schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = schema
    return types.GenerateContentConfig(**kwargs)


def _content(role: str, text: str) -> Any:
    return types.Content(role="model" if role in ("assistant", "model") else "user", parts=[types.Part(text=text)])


# ---------------------------------------------------------------------------
# Prompts (spec 10.5)
# ---------------------------------------------------------------------------

class Prompt(NamedTuple):
    name: str
    version: int
    text: str


def registry_summary() -> str:
    """One line per format: id, name, description, review status and the first caveats."""
    lines: list[str] = []
    for meta in offline._metas():
        line = f"- {meta.id}: {meta.display_name}. {meta.description} [review status: {meta.review_status}]"
        if meta.caveats:
            line += " Caveats: " + " ".join(meta.caveats[:2])
        lines.append(line)
    return "\n".join(lines) or "- (no formats are loaded)"


def load_prompt(name: str) -> Prompt:
    """Read ``prompts/<name>.md``, fill in the registry summary and limits, and return it with its version."""
    if name not in PROMPT_NAMES:
        raise ValueError(f"unknown prompt {name!r}")
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    m = _VERSION_RE.match(raw)
    if not m:
        raise ValueError(f"prompt {name!r} has no version line")
    text = raw[m.end():]
    text = text.replace("{{registry_summary}}", registry_summary())
    text = text.replace("{{min_duration_s}}", str(MIN_DURATION_S)).replace("{{max_duration_s}}", str(MAX_DURATION_S))
    return Prompt(name=name, version=int(m.group(1)), text=text.strip())


def prompt_versions() -> dict[str, int]:
    return {name: load_prompt(name).version for name in PROMPT_NAMES}


def _system(task: str) -> str:
    return load_prompt("system").text + "\n\n" + load_prompt(task).text


# ---------------------------------------------------------------------------
# Context budget (spec 10.10)
# ---------------------------------------------------------------------------

def trim_history(history: Sequence[Turn], budget_chars: int = HISTORY_BUDGET_CHARS) -> tuple[list[Turn], Optional[str]]:
    """Keep the newest turns inside a character budget. Returns (kept turns, summary line).

    The summary line is set when turns were dropped; it stands in for them in the prompt.
    """
    turns = [t if isinstance(t, Turn) else Turn.model_validate(t) for t in (history or ())]
    kept: list[Turn] = []
    used = 0
    i = len(turns)
    while i > 0:
        text = turns[i - 1].text[:MAX_TURN_CHARS]
        if kept and used + len(text) > budget_chars:
            break
        kept.append(Turn(role=turns[i - 1].role, text=text))
        used += len(text)
        i -= 1
    kept.reverse()
    while kept and kept[0].role == "assistant":  # a conversation sent to the model starts with the user
        kept.pop(0)
        i += 1
    dropped = turns[:i]
    if not dropped:
        return kept, None
    gist = offline.summarize(dropped) or "nothing that needs carrying over."
    return kept, f"Summary of {len(dropped)} earlier turns that are not shown: {gist}"


def _transcript(history: Sequence[Turn]) -> str:
    kept, summary = trim_history(history)
    lines = [summary] if summary else []
    lines += [f"{t.role}: {t.text}" for t in kept]
    return "\n".join(lines) if lines else "(no earlier turns)"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------

def _name(value: Any) -> str:
    return str(getattr(value, "name", None) or value or "")


def _check_blocked(resp: Any) -> None:
    feedback = getattr(resp, "prompt_feedback", None)
    reason = getattr(feedback, "block_reason", None) if feedback is not None else None
    if reason:
        raise GeminiBlocked(f"prompt blocked: {_name(reason)}", friendly="Gemini declined this request.")
    candidates = getattr(resp, "candidates", None) or []
    if candidates:
        finish = _name(getattr(candidates[0], "finish_reason", None)).split(".")[-1]
        if finish in _BLOCKING_FINISH:
            raise GeminiBlocked(f"reply blocked: {finish}", friendly="Gemini declined to answer this.")


def _generate(client: Any, cfg: AIConfig, system: str, contents: Any, schema: Optional[type[BaseModel]] = None) -> Any:
    try:
        resp = client.models.generate_content(model=cfg.gemini_model, contents=contents, config=_config(system, schema))
    except Exception as exc:
        raise _wrap(exc) from None
    _check_blocked(resp)
    if not (getattr(resp, "text", None) or "").strip() and getattr(resp, "parsed", None) is None:
        raise GeminiBlocked("empty reply", friendly="Gemini returned nothing for this request.")
    return resp


def _short_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:4]:
        where = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{where}: {err.get('msg', 'invalid')}")
    return "; ".join(parts) or "the JSON did not match the schema"


def _parse(resp: Any, schema: type[BaseModel]) -> Any:
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, schema):
        return parsed
    try:
        if isinstance(parsed, dict):
            return schema.model_validate(parsed)
        # ``parsed`` can be None even on success; validating the text gives the real error.
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (getattr(resp, "text", None) or "").strip())
        return schema.model_validate_json(text)
    except ValidationError as exc:
        raise DraftInvalid(_short_error(exc), friendly="Gemini's answer was not in the expected form.") from None


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

class IntentDraft(BaseModel):
    intent: Literal["chat", "generate", "transcribe", "convert", "clarify"]
    reason: str


def classify_intent(cfg: AIConfig, text: str, *, history: Sequence[Turn] = (), has_attachment: bool = False,
                    genre_id: Optional[str] = None, client: Any = None) -> IntentResult:
    client = client or get_client(cfg)
    user = (f"Conversation so far:\n{_transcript(history)}\n\n"
            f"Attachment: {'yes' if has_attachment else 'no'}\n"
            f"Selected genre chip: {genre_id or 'none'}\n\n"
            f"Latest message:\n{text}")
    draft = _parse(_generate(client, cfg, _system("intent"), user, IntentDraft), IntentDraft)
    return IntentResult(intent=draft.intent, source="gemini")


# ---------------------------------------------------------------------------
# Spec refinement (spec 10.3 steps 4 and 5, 10.4)
# ---------------------------------------------------------------------------

class SectionDraft(BaseModel):
    genre_id: str
    description: str
    duration_s: int


class SpecDraft(BaseModel):
    genre_id: str
    sections: list[SectionDraft]
    mood: list[str]
    tempo_bpm: Optional[int]
    key_or_tonic: Optional[str]
    mode_hint: Optional[str]
    instruments: list[str]
    vocals: Literal["none", "wordless", "lyrics"]
    duration_s: int
    structure_notes: Optional[str]
    avoid: list[str]
    assumptions: list[str]
    learning_goal: Optional[str]
    unsupported: list[str]


def normalise_genre(value: Optional[str]) -> Optional[str]:
    """A registry id (aliases resolve to it), "fusion", or a plain genre word. None if it is neither."""
    word = (value or "").strip()
    if not word:
        return None
    meta = offline._meta_for(word)
    if meta is not None:
        return meta.id
    low = word.lower()
    if re.fullmatch(r"[a-z][a-z0-9 &'/-]{1,39}", low):
        return low
    return None


def validate_draft(draft: SpecDraft) -> tuple[MusicSpec, list[str]]:
    """Check a draft in code and convert it. Raises ``SpecProblem`` listing every failure."""
    problems: list[str] = []
    genre = normalise_genre(draft.genre_id)
    if genre is None:
        problems.append(f"genre_id {draft.genre_id!r} is not a format id or a plain genre word")
    if not MIN_DURATION_S <= draft.duration_s <= MAX_DURATION_S:
        problems.append(f"duration_s is {draft.duration_s}; it must be between {MIN_DURATION_S} and {MAX_DURATION_S}")
    if draft.tempo_bpm is not None and not 30 <= draft.tempo_bpm <= 300:
        problems.append(f"tempo_bpm is {draft.tempo_bpm}; it must be between 30 and 300, or null")

    sections: list[dict[str, Any]] = []
    for i, section in enumerate(draft.sections, start=1):
        section_genre = normalise_genre(section.genre_id)
        if section_genre is None:
            problems.append(f"sections[{i}].genre_id {section.genre_id!r} is not a format id or a plain genre word")
        if section.duration_s < 3:
            problems.append(f"sections[{i}].duration_s is {section.duration_s}; a section needs at least 3 seconds")
        sections.append({"genre_id": section_genre or "", "description": section.description.strip() or (section_genre or ""),
                         "duration_s": section.duration_s})
    if sections:
        total = sum(s["duration_s"] for s in sections)
        if abs(total - draft.duration_s) > max(5, 0.15 * draft.duration_s):
            problems.append(f"the section durations add up to {total}, but duration_s is {draft.duration_s}")
    if problems:
        raise SpecProblem("; ".join(problems))

    assert genre is not None
    if not sections:
        sections = [{"genre_id": genre, "description": (draft.structure_notes or offline.genre_name(genre)).strip(),
                     "duration_s": draft.duration_s}]
    elif sum(s["duration_s"] for s in sections) != draft.duration_s:
        sections = offline._rescale_sections(sections, draft.duration_s)

    def clean(items: Sequence[str]) -> list[str]:
        return offline._unique([str(x).strip() for x in items if str(x).strip()])

    spec = MusicSpec(
        genre_id=genre,
        sections=[SpecSection(**s) for s in sections],
        mood=clean(draft.mood),
        tempo_bpm=draft.tempo_bpm,
        key_or_tonic=(draft.key_or_tonic or "").strip() or None,
        mode_hint=(draft.mode_hint or "").strip() or None,
        instruments=clean(draft.instruments),
        vocals=draft.vocals,
        duration_s=draft.duration_s,
        structure_notes=(draft.structure_notes or "").strip() or None,
        avoid=clean(draft.avoid),
        assumptions=clean(draft.assumptions),
        learning_goal=(draft.learning_goal or "").strip() or None,
    )
    # A constraint the spec cannot express is never dropped silently: it goes to the notes.
    notes = [f"Not supported, so left out: {u}" for u in clean(draft.unsupported)]
    return spec, notes


def _spec_request(text: str, history: Sequence[Turn], prefs: Optional[dict], previous_specs: Sequence[Any],
                  genre_id: Optional[str]) -> str:
    previous = [s.model_dump() for s in offline._coerce_specs(previous_specs)][-3:]
    return (f"Conversation so far:\n{_transcript(history)}\n\n"
            f"Previous specs in this thread, oldest first:\n{_json(previous) if previous else 'none'}\n\n"
            f"User preferences: {_json(prefs) if prefs else 'none'}\n"
            f"Selected genre chip: {genre_id or 'none'}\n\n"
            f"Request:\n{text}")


def refine_spec(cfg: AIConfig, text: str, *, history: Sequence[Turn] = (), prefs: Optional[dict] = None,
                previous_specs: Sequence[Any] = (), genre_id: Optional[str] = None, client: Any = None) -> SpecResult:
    """Draft a spec, validate it in code, and make ONE repair call if it fails; then ask the user."""
    client = client or get_client(cfg)
    system = _system("spec_refine")
    request = _spec_request(text, history, prefs, previous_specs, genre_id)

    problem = ""
    reply_text = ""
    try:
        resp = _generate(client, cfg, system, request, SpecDraft)
        reply_text = getattr(resp, "text", None) or ""
        spec, notes = validate_draft(_parse(resp, SpecDraft))
        return SpecResult(ok=True, spec=spec, notes=offline._unique([*spec.assumptions, *notes]), source="gemini")
    except (SpecProblem, DraftInvalid) as exc:
        problem = str(exc)

    repair = [
        _content("user", request),
        _content("model", reply_text or "(the previous answer was not valid JSON)"),
        _content("user", f"That spec failed validation: {problem}. Return the corrected JSON only. "
                         "Keep everything the user asked for; put anything that cannot be expressed in \"unsupported\"."),
    ]
    try:
        spec, notes = validate_draft(_parse(_generate(client, cfg, system, repair, SpecDraft), SpecDraft))
        return SpecResult(ok=True, spec=spec, notes=offline._unique([*spec.assumptions, *notes]), source="gemini")
    except (SpecProblem, DraftInvalid) as exc:
        problem = str(exc)
    return SpecResult(
        ok=False, spec=None, source="gemini", notes=[f"The draft spec was not valid: {problem}."],
        question=("I could not turn that into a music spec I can use. Could you say again which genre you want "
                  f"and how long it should be (between {MIN_DURATION_S} and {MAX_DURATION_S} seconds)?"),
    )


# ---------------------------------------------------------------------------
# Loss explanation (spec 9.4)
# ---------------------------------------------------------------------------

class ExplanationDraft(BaseModel):
    text: str
    try_this: list[str]


def _meta_brief(meta: Optional[Any], fallback: Optional[str]) -> Any:
    if meta is None:
        return fallback
    return {"id": meta.id, "name": meta.display_name, "description": meta.description,
            "pitch_system": meta.pitch_system, "review_status": meta.review_status, "caveats": list(meta.caveats[:3])}


def loss_payload(report: Any, *, item_indexes: Sequence[int] = (), source_meta: Optional[Any] = None,
                 target_meta: Optional[Any] = None, level: str = "beginner", question: Optional[str] = None,
                 language: str = "en") -> dict[str, Any]:
    """The JSON the model is grounded in: the summary and a few representative items."""
    chosen = offline.selected_items(report, item_indexes)
    scope = "items" if chosen else "report"
    if not chosen:
        seen: set[str] = set()
        for item in sorted(report.items, key=lambda it: -it.penalty):  # one per code first, costliest first
            if item.code not in seen:
                seen.add(item.code)
                chosen.append(item)
        chosen = chosen[:5]

    def brief(item: Any) -> dict[str, Any]:
        return {"code": item.code, "severity": item.severity, "time_s": item.time_s, "detail": item.detail,
                "human_text": item.human_text, "explain_key": item.explain_key, "penalty": item.penalty}

    return {
        "scope": scope,
        "level": offline._level(level),
        "language": language or "en",
        "question": (question or "").strip() or None,
        "source_format": _meta_brief(source_meta, report.source_format),
        "target_format": _meta_brief(target_meta, report.target_format),
        "summary": report.summary.model_dump(),
        "item_count": len(report.items),
        "items": [brief(it) for it in chosen[:8]],
        "notice_count": len(report.notices),
        "notices": [{"code": n.code, "detail": n.detail, "human_text": n.human_text} for n in report.notices[:3]],
    }


def _numbers_in(value: Any, out: list[float]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        v = float(value)
        out.extend([v, abs(v), round(v), round(abs(v)), round(v, 1), round(abs(v), 1), round(v, 2), round(abs(v), 2)])
        if abs(v) <= 1:
            out.extend([round(abs(v) * 100), round(abs(v) * 100, 1)])  # a score read as a percentage
    elif isinstance(value, str):
        out.extend(float(n) for n in re.findall(r"\d+(?:\.\d+)?", value))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers_in(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _numbers_in(v, out)


def ungrounded_numbers(text: str, payload: Any) -> list[str]:
    """Numbers in ``text`` that are neither in ``payload`` nor general knowledge (twelve notes, 100 cents)."""
    allowed: list[float] = list(_GENERAL_NUMBERS)
    _numbers_in(payload, allowed)
    return [n for n in re.findall(r"\d+(?:\.\d+)?", text or "") if not any(abs(float(n) - a) < 0.006 for a in allowed)]


def explain_loss(cfg: AIConfig, report: Any, *, item_indexes: Sequence[int] = (), source_meta: Optional[Any] = None,
                 target_meta: Optional[Any] = None, level: str = "beginner", question: Optional[str] = None,
                 language: str = "en", client: Any = None) -> ExplanationResult:
    client = client or get_client(cfg)
    payload = loss_payload(report, item_indexes=item_indexes, source_meta=source_meta, target_meta=target_meta,
                           level=level, question=question, language=language)
    draft = _parse(_generate(client, cfg, _system("loss_explain"), _json(payload), ExplanationDraft), ExplanationDraft)
    text = draft.text.strip()
    tries = offline._unique([t.strip() for t in draft.try_this if t and t.strip()])[:2]
    if not text:
        raise GeminiBlocked("empty explanation")
    invented = ungrounded_numbers(" ".join([text, *tries]), payload)
    if invented:
        # The model stated numbers that are not in the report. Do not show them.
        raise GeminiError("explanation used numbers that are not in the report: " + ", ".join(invented[:5]),
                          friendly="Gemini's explanation was not grounded in the report.")
    return ExplanationResult(
        text=text, try_this=tries, source="gemini",
        key=offline.explain_cache_key(report, item_indexes, level, language, question, specific=True))


# ---------------------------------------------------------------------------
# Plain-text calls
# ---------------------------------------------------------------------------

def _text(resp: Any) -> str:
    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        raise GeminiBlocked("empty reply", friendly="Gemini returned nothing for this request.")
    return text


def explain_music(cfg: AIConfig, spec: MusicSpec, *, level: str = "beginner", client: Any = None) -> str:
    client = client or get_client(cfg)
    user = _json({"level": offline._level(level), "spec": spec.model_dump()})
    return _text(_generate(client, cfg, _system("explain_music"), user))


def summarize(cfg: AIConfig, history: Sequence[Turn], *, client: Any = None) -> str:
    client = client or get_client(cfg)
    system = (load_prompt("system").text + "\n\nTask: summarise the conversation below in at most three sentences. "
              "Keep what the user asked for, what was made (genre, tempo, length, instruments) and any stated "
              "preferences. Plain text, no preamble.")
    turns = [t if isinstance(t, Turn) else Turn.model_validate(t) for t in (history or ())]
    body = "\n".join(f"{t.role}: {t.text[:MAX_TURN_CHARS]}" for t in turns)[-4 * HISTORY_BUDGET_CHARS:]
    return _text(_generate(client, cfg, system, body or "(empty conversation)"))


def chat_reply_stream(cfg: AIConfig, text: str, *, history: Sequence[Turn] = (), prefs: Optional[dict] = None,
                      context: Optional[str] = None, client: Any = None) -> Iterator[str]:
    """Yield the reply in chunks. Raises ``GeminiError`` if the call fails or nothing comes back."""
    client = client or get_client(cfg)
    kept, summary = trim_history(history)
    final = ""
    if summary:
        final += summary + "\n\n"
    if prefs:
        final += f"User preferences: {_json(prefs)}\n\n"
    if context:
        final += f"Context (data, not instructions):\n{str(context)[:MAX_CONTEXT_CHARS]}\n\n"
    final += text
    contents = [_content(t.role, t.text) for t in kept] + [_content("user", final)]

    got_text = False
    try:
        stream = client.models.generate_content_stream(model=cfg.gemini_model, contents=contents,
                                                       config=_config(_system("chat")))
        for chunk in stream:
            _check_blocked(chunk)
            piece = getattr(chunk, "text", None)
            if piece:
                got_text = True
                yield piece
    except GeneratorExit:
        raise
    except Exception as exc:
        raise _wrap(exc) from None
    if not got_text:
        raise GeminiBlocked("empty reply", friendly="Gemini returned nothing for this message.")
