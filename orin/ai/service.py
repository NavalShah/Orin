"""The AI facade (see ``orin/ai/types.py``). The server calls only these functions.

Each one uses the live provider when its key and model are configured and ORIN_OFFLINE is
not "1", and the offline stand-in otherwise. A provider failure never raises out of here:
the stand-in answers instead and the result says so.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterator, Optional, Sequence

from . import config, elevenlabs, gemini, offline, sanitize
from .schema import MusicSpec
from .types import (
    AIStatus,
    ExplanationResult,
    IntentResult,
    Level,
    MusicResult,
    NarrationResult,
    ProviderMeta,
    SanitizeResult,
    SpecResult,
    Turn,
)

log = logging.getLogger("orin.ai")

_NO_GENAI = "The google-genai package is not installed, so chat and explanations use the offline stand-in."
_NO_ELEVENLABS = "The elevenlabs package is not installed, so music and narration use the stand-ins."


def _llm_reason(cfg: config.AIConfig) -> Optional[str]:
    """None when Gemini should be used, else why the stand-in is."""
    return cfg.llm_standin_reason() or (None if gemini.available() else _NO_GENAI)


def _music_reason(cfg: config.AIConfig) -> Optional[str]:
    return cfg.music_standin_reason() or (None if elevenlabs.available() else _NO_ELEVENLABS)


def _tts_reason(cfg: config.AIConfig) -> Optional[str]:
    return cfg.tts_standin_reason() or (None if elevenlabs.available() else _NO_ELEVENLABS)


def _warn(what: str, exc: BaseException, cfg: config.AIConfig) -> None:
    log.warning("%s failed, using the stand-in: %s", what, config.redact(f"{type(exc).__name__}: {exc}", cfg))


def _friendly(exc: BaseException, fallback: str) -> str:
    return str(getattr(exc, "friendly", None) or fallback)


def _is_cancelled(cancelled: Optional[Callable[[], bool]]) -> bool:
    try:
        return bool(cancelled and cancelled())
    except Exception:
        return False


# ---------------------------------------------------------------------------

def status() -> AIStatus:
    cfg = config.load()
    llm, music, tts = _llm_reason(cfg), _music_reason(cfg), _tts_reason(cfg)
    reasons = offline._unique([r for r in (llm, music, tts) if r])
    return AIStatus(
        llm_offline=llm is not None,
        music_offline=music is not None,
        tts_offline=tts is not None,
        reason=" ".join(reasons) or None,
        llm_model=(cfg.gemini_model or None) if llm is None else None,
        music_model=(cfg.elevenlabs_music_model or None) if music is None else None,  # None = the provider default
        tts_model=(cfg.elevenlabs_tts_model or None) if tts is None else None,
    )


def sanitize_prompt(text: str) -> SanitizeResult:
    return sanitize.sanitize_prompt(text)


def classify_intent(text: str, *, history: Sequence[Turn] = (), has_attachment: bool = False,
                    genre_id: Optional[str] = None) -> IntentResult:
    cfg = config.load()
    if _llm_reason(cfg) is None and (text or "").strip():
        try:
            return gemini.classify_intent(cfg, text, history=history, has_attachment=has_attachment, genre_id=genre_id)
        except Exception as exc:
            _warn("intent classification", exc, cfg)
    return IntentResult(intent=offline.classify_intent(text, has_attachment=has_attachment, genre_id=genre_id),
                        source="offline")


def chat_reply_stream(text: str, *, history: Sequence[Turn] = (), prefs: Optional[dict] = None,
                      context: Optional[str] = None) -> Iterator[str]:
    cfg = config.load()
    notice: Optional[str] = None
    if _llm_reason(cfg) is None:
        sent = False
        try:
            for piece in gemini.chat_reply_stream(cfg, text, history=history, prefs=prefs, context=context):
                sent = True
                yield piece
            return
        except Exception as exc:
            _warn("chat reply", exc, cfg)
            reason = _friendly(exc, "Gemini could not answer just now.")
            if sent:
                yield f"\n\n[The reply stopped early. {reason}]"
                return
            notice = f"{reason} This answer comes from Orin's offline stand-in."
    yield from offline.chat_reply_chunks(text, history=history, prefs=prefs, context=context, notice=notice)


def _scrub_spec(spec: MusicSpec, names: Sequence[str]) -> tuple[MusicSpec, list[str]]:
    """Apply the prompt policy to a spec's free text. Returns the clean spec and what was changed."""
    notes: list[str] = []

    def clean(value: Optional[str]) -> Optional[str]:
        if not value:
            return value
        result = sanitize.sanitize_prompt(sanitize.scrub_names(value, names))
        notes.extend(result.notes)
        return result.text if not result.blocked else ""

    data = spec.model_dump()
    for section in data["sections"]:
        section["description"] = clean(section["description"]) or offline.genre_name(section["genre_id"])
    for key in ("structure_notes", "learning_goal", "mode_hint"):
        data[key] = clean(data[key]) or None
    for key in ("mood", "instruments", "avoid", "assumptions"):
        data[key] = [v for v in (sanitize.scrub_names(item, names) for item in data[key]) if v]
    return MusicSpec.model_validate(data), offline._unique(notes)


def refine_spec(text: str, *, history: Sequence[Turn] = (), prefs: Optional[dict] = None,
                previous_specs: Sequence[Any] = (), genre_id: Optional[str] = None) -> SpecResult:
    cfg = config.load()
    policy = sanitize.sanitize_prompt(text or "")
    if policy.blocked:
        return SpecResult(ok=False, spec=None, notes=[policy.reason or ""], question=policy.reason, source="offline")
    names = sanitize.find_names(text or "")
    extra: list[str] = []

    if _llm_reason(cfg) is None and (text or "").strip():
        try:
            # Gemini sees the request as written so it can turn a named artist into plain attributes;
            # whatever it writes is then checked against the policy before it goes anywhere else.
            result = gemini.refine_spec(cfg, text, history=history, prefs=prefs, previous_specs=previous_specs,
                                        genre_id=genre_id)
            notes = list(result.notes)
            spec = result.spec
            if spec is not None:
                spec, scrub_notes = _scrub_spec(spec, names)
                notes += scrub_notes
            return result.model_copy(update={"spec": spec, "notes": offline._unique([*policy.notes, *notes])})
        except Exception as exc:
            _warn("spec refinement", exc, cfg)
            extra.append(f"{_friendly(exc, 'Gemini could not answer just now.')} "
                         "Orin's offline stand-in built this spec instead.")

    result = offline.refine_spec(policy.text, previous_specs=previous_specs, genre_id=genre_id, prefs=prefs)
    return result.model_copy(update={"notes": offline._unique([*policy.notes, *result.notes, *extra])})


def explain_loss(report: Any, *, item_indexes: Sequence[int] = (), source_meta: Optional[Any] = None,
                 target_meta: Optional[Any] = None, level: Level = "beginner", question: Optional[str] = None,
                 language: str = "en") -> ExplanationResult:
    cfg = config.load()
    kwargs = dict(item_indexes=tuple(item_indexes or ()), source_meta=source_meta, target_meta=target_meta,
                  level=level, question=question, language=language)
    if _llm_reason(cfg) is None:
        try:
            return gemini.explain_loss(cfg, report, **kwargs)
        except Exception as exc:
            _warn("loss explanation", exc, cfg)
    return offline.explain_loss(report, **kwargs)


def explain_music(spec: MusicSpec, *, level: Level = "beginner") -> str:
    cfg = config.load()
    if _llm_reason(cfg) is None:
        try:
            return gemini.explain_music(cfg, spec, level=level)
        except Exception as exc:
            _warn("music explanation", exc, cfg)
    return offline.explain_music(spec, level=level)


def summarize(history: Sequence[Turn]) -> str:
    cfg = config.load()
    if _llm_reason(cfg) is None and history:
        try:
            return gemini.summarize(cfg, history)
        except Exception as exc:
            _warn("summary", exc, cfg)
    return offline.summarize(history)


def _cancelled_result(request_id: str) -> MusicResult:
    # No audio at all: the caller cancelled, so there is nothing to keep.
    return MusicResult(audio=b"", media_type="audio/wav", ext="wav", standin=True,
                       standin_reason="Cancelled before any music was made.",
                       provider=ProviderMeta(name="offline", request_id=request_id, duration_s=0.0),
                       notes=["cancelled"])


def compose_music(spec: MusicSpec, *, request_id: str, cancelled: Optional[Callable[[], bool]] = None) -> MusicResult:
    cfg = config.load()
    if _is_cancelled(cancelled):
        return _cancelled_result(request_id)
    spec, notes = _scrub_spec(spec, ())
    reason = _music_reason(cfg)
    if reason is None:
        try:
            result = elevenlabs.compose_music(cfg, spec, request_id=request_id, cancelled=cancelled)
            if _is_cancelled(cancelled):
                return _cancelled_result(request_id)
            return result.model_copy(update={"notes": offline._unique([*notes, *result.notes])}) if notes else result
        except elevenlabs.Cancelled:
            return _cancelled_result(request_id)
        except elevenlabs.ProviderFailure as exc:
            _warn("music generation", exc, cfg)
            reason = exc.friendly
            notes = [*notes, *exc.notes]
        except Exception as exc:
            _warn("music generation", exc, cfg)
            reason = "Music generation failed unexpectedly."
    if _is_cancelled(cancelled):
        return _cancelled_result(request_id)
    return offline.compose_music(spec, request_id=request_id, reason=config.redact(reason, cfg),
                                 notes=[config.redact(n, cfg) for n in notes])


def narrate(text: str, *, request_id: str) -> NarrationResult:
    cfg = config.load()
    message = _tts_reason(cfg)
    if message is None and (text or "").strip():
        try:
            return elevenlabs.narrate(cfg, text, request_id=request_id)
        except elevenlabs.ProviderFailure as exc:
            _warn("narration", exc, cfg)
            message = exc.friendly
        except Exception as exc:
            _warn("narration", exc, cfg)
            message = "Narration failed unexpectedly, so your browser's own voice reads the text."
    return offline.narrate(text, request_id=request_id, message=config.redact(message, cfg) if message else None)


__all__ = [
    "status", "sanitize_prompt", "classify_intent", "chat_reply_stream", "refine_spec", "explain_loss",
    "explain_music", "summarize", "compose_music", "narrate",
]
