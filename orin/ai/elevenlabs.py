"""ElevenLabs wrapper (spec 10.9): music generation and narration.

Both SDK calls return a LAZY generator of bytes: the HTTP request, and any error, happen
on the first iteration, and the SDK does not retry. So every call here is consumed
inside the try, with our own exponential backoff on 429 and 5xx.

Functions return a contract type or raise ``ProviderFailure`` (with a friendly reason);
``service.py`` turns a failure into the labelled stand-in. Model ids come from the
environment (spec D14); when a model variable is empty no model id is sent.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import compose_prompt
from .config import AIConfig, redact
from .schema import MusicSpec
from .types import MusicResult, NarrationResult, ProviderMeta

try:  # optional at import time: without the SDK the facade stays on the stand-ins
    import httpx
    from elevenlabs.client import ElevenLabs
    from elevenlabs.core import ApiError
except Exception:  # pragma: no cover - exercised only when the package is missing
    httpx = None  # type: ignore[assignment]
    ElevenLabs = None  # type: ignore[assignment,misc]
    ApiError = None  # type: ignore[assignment,misc]

OUTPUT_FORMAT = "mp3_44100_128"
CLIENT_TIMEOUT_S = 300.0  # a few minutes of music can take a while to come back
TTS_TIMEOUT_S = 60
MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 20.0
MAX_TTS_CHARS = 3000
_REMEMBER = 64

_sleep: Callable[[float], None] = time.sleep  # tests replace this

_AUTH_CODES = {"invalid_api_key", "missing_api_key", "invalid_authorization_header", "unauthorized"}
_PLAN_CODES = {"insufficient_credits", "payment_required", "subscription_required", "feature_not_available",
               "model_access_denied", "quota_exceeded", "missing_permissions", "limited_access",
               "paid_plan_required", "free_users_not_allowed", "needs_authorization"}


class ProviderFailure(Exception):
    """The provider could not do the job. ``friendly`` is safe to show; ``notes`` add detail."""

    def __init__(self, kind: str, friendly: str, notes: Optional[list[str]] = None, detail: str = ""):
        super().__init__(redact(f"{kind}: {detail or friendly}"))
        self.kind = kind
        self.friendly = friendly
        self.notes = list(notes or [])


class Cancelled(Exception):
    """The caller's ``cancelled()`` returned True."""


@dataclass
class ErrorInfo:
    status: Optional[int] = None
    code: str = ""
    message: str = ""
    data: dict = field(default_factory=dict)
    retry_after: Optional[float] = None
    kind: str = "other"  # auth | plan | prompt | bug | rate | server | timeout | network | other

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate", "server", "network")


class _Failed(Exception):
    def __init__(self, info: ErrorInfo):
        super().__init__(info.kind)
        self.info = info


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def available() -> bool:
    return ElevenLabs is not None


def make_client(api_key: str) -> Any:
    """Build the SDK client. The key is always passed explicitly: the SDK's own default is
    read from the environment at import time. Tests replace this with a fake factory."""
    return ElevenLabs(api_key=api_key, timeout=CLIENT_TIMEOUT_S)


_clients: dict[tuple[Any, str], Any] = {}
_voices: dict[tuple[Any, str], str] = {}


def get_client(cfg: AIConfig) -> Any:
    key = (make_client, cfg.elevenlabs_api_key)  # a replaced factory gets its own entry
    if key not in _clients:
        _clients.clear()
        _voices.clear()
        _clients[key] = make_client(cfg.elevenlabs_api_key)
    return _clients[key]


def model_family(model_id: Optional[str]) -> str:
    """"v2" for music_v2*, else "v1" (which is also what the API uses when no model id is sent)."""
    return "v2" if (model_id or "").strip().lower().startswith("music_v2") else "v1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def error_info(exc: Exception) -> ErrorInfo:
    """Read an SDK error. ``body["detail"]`` can be a dict, a list (422) or a string."""
    if httpx is not None and isinstance(exc, httpx.TimeoutException):
        return ErrorInfo(kind="timeout", message="request timed out")
    if httpx is not None and isinstance(exc, httpx.TransportError):
        return ErrorInfo(kind="network", message=type(exc).__name__)
    if ApiError is None or not isinstance(exc, ApiError):
        return ErrorInfo(kind="other", message=f"{type(exc).__name__}: {exc}")

    info = ErrorInfo(status=exc.status_code if isinstance(exc.status_code, int) else None)
    body = exc.body
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict):
        info.code = str(detail.get("code") or detail.get("status") or "").strip().lower()  # "status" is the legacy field
        info.message = str(detail.get("message") or "")
        data = detail.get("data")
        info.data = data if isinstance(data, dict) else {}
    elif isinstance(detail, list):
        info.message = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail[:3])
    elif detail is not None:
        info.message = str(detail)
    info.message = info.message[:300]

    headers = exc.headers or {}
    try:
        after = headers.get("retry-after") or headers.get("Retry-After")
        info.retry_after = float(after) if after is not None else None
    except (TypeError, ValueError, AttributeError):
        info.retry_after = None

    status = info.status or 0
    if info.code in ("bad_prompt", "bad_composition_plan"):
        info.kind = "prompt"
    elif info.code in _AUTH_CODES:
        info.kind = "auth"
    elif info.code in _PLAN_CODES:
        info.kind = "plan"
    elif status == 401:
        info.kind = "auth"
    elif status in (402, 403):
        info.kind = "plan"
    elif status == 422:
        info.kind = "bug"
    elif status == 429:
        info.kind = "rate"
    elif status >= 500:
        info.kind = "server"
    return info


def _delay(attempt: int, retry_after: Optional[float]) -> float:
    delay = BACKOFF_BASE_S * (2 ** (attempt - 1))
    if retry_after is not None and retry_after > 0:
        delay = max(delay, retry_after)
    return min(BACKOFF_MAX_S, delay)


def _is_cancelled(cancelled: Optional[Callable[[], bool]]) -> bool:
    try:
        return bool(cancelled and cancelled())
    except Exception:
        return False


def _consume(stream: Any) -> bytes:
    if isinstance(stream, (bytes, bytearray)):
        return bytes(stream)
    return b"".join(chunk for chunk in stream if chunk)


def _with_backoff(call: Callable[[], bytes], cancelled: Optional[Callable[[], bool]] = None) -> bytes:
    """Run ``call`` (which must consume the lazy stream), retrying 429 and 5xx with backoff."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return call()
        except Exception as exc:
            info = error_info(exc)
            if info.retryable and attempt < MAX_ATTEMPTS and not _is_cancelled(cancelled):
                _sleep(_delay(attempt, info.retry_after))
                continue
            raise _Failed(info) from None


_TTS_END = "so your browser's own voice reads the text"


def _failure(info: ErrorInfo, *, music: bool, model: str = "") -> ProviderFailure:
    """A friendly reason. For music it is the reason alone (the result is already labelled a
    stand-in); for narration it also says that the browser reads the text."""
    provider_said = redact(info.message).strip()
    notes: list[str] = []
    tail = ""
    if info.kind == "auth":
        reason, tail = "The ElevenLabs key was not accepted", "Check ELEVENLABS_API_KEY."
    elif info.kind == "plan":
        if info.code == "model_access_denied" and model:
            reason = "This ElevenLabs account cannot use the model set for this"
        elif music:
            reason = "Music generation needs a paid ElevenLabs plan (or more credits)"
        else:
            reason = "This ElevenLabs plan or its remaining credits do not cover narration"
    elif info.kind == "prompt":
        reason, tail = "The music provider refused this prompt", "Try describing the sound without naming artists or songs."
    elif info.kind == "bug":
        reason = "The provider did not accept the request Orin built"
        notes.append("This is a fault in Orin's request, not in what you asked for."
                     + (f" The provider said: {provider_said}" if provider_said else ""))
    elif info.kind == "rate":
        reason, tail = "ElevenLabs is busy (rate limit) and retries did not get through", "Try again in a minute."
    elif info.kind == "server":
        reason = "ElevenLabs had a server problem"
    elif info.kind == "timeout":
        reason = "ElevenLabs took too long to answer"
    elif info.kind == "network":
        reason = "ElevenLabs could not be reached"
    else:
        reason = "Music generation failed at the provider" if music else "Narration failed at the provider"
    friendly = f"{reason}." if music else f"{reason}, {_TTS_END}."
    if tail:
        friendly += " " + tail
    return ProviderFailure(info.kind, friendly, notes, detail=f"status={info.status} code={info.code} {provider_said}")


# ---------------------------------------------------------------------------
# Idempotency: one provider call per request id
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_done: "OrderedDict[tuple[str, str], Any]" = OrderedDict()
_inflight: dict[tuple[str, str], threading.Event] = {}


def _claim(kind: str, request_id: Optional[str]) -> Any:
    """A finished result for this request id, or None once the id is marked as running."""
    if not request_id:
        return None
    key = (kind, request_id)
    while True:
        with _lock:
            if key in _done:
                return _done[key]
            running = _inflight.get(key)
            if running is None:
                _inflight[key] = threading.Event()
                return None
        running.wait(timeout=CLIENT_TIMEOUT_S)  # the same request is already at the provider


def _finish(kind: str, request_id: Optional[str], result: Any = None) -> None:
    if not request_id:
        return
    key = (kind, request_id)
    with _lock:
        if result is not None:
            _done[key] = result
            while len(_done) > _REMEMBER:
                _done.popitem(last=False)
        running = _inflight.pop(key, None)
    if running is not None:
        running.set()


def reset() -> None:
    """Forget clients, voices and finished request ids. For tests."""
    with _lock:
        _done.clear()
        _inflight.clear()
    _clients.clear()
    _voices.clear()


# ---------------------------------------------------------------------------
# Music
# ---------------------------------------------------------------------------

def build_music_request(cfg: AIConfig, spec: MusicSpec) -> dict[str, Any]:
    """Keyword arguments for ``client.music.compose``.

    More than one section means a composition plan (that is how a mid-track genre change
    is expressed), in the shape of the configured model family. Otherwise a prompt.
    """
    kwargs: dict[str, Any] = {"output_format": OUTPUT_FORMAT}
    if cfg.elevenlabs_music_model:
        kwargs["model_id"] = cfg.elevenlabs_music_model
    if len(spec.sections) > 1:
        builder = compose_prompt.plan_v2 if model_family(cfg.elevenlabs_music_model) == "v2" else compose_prompt.plan_v1
        kwargs["composition_plan"] = builder(spec)
    else:
        kwargs["prompt"] = compose_prompt.spec_to_prompt(spec)
        kwargs["music_length_ms"] = compose_prompt.length_ms(spec.duration_s)
    return kwargs


def _apply_suggestion(kwargs: dict[str, Any], info: ErrorInfo) -> Optional[tuple[dict[str, Any], str]]:
    """The provider's own rewrite of a refused prompt or plan, if it sent one."""
    if info.code == "bad_prompt" and "prompt" in kwargs:
        suggestion = info.data.get("prompt_suggestion")
        if isinstance(suggestion, str) and suggestion.strip():
            return ({**kwargs, "prompt": suggestion.strip()},
                    "The music provider would not take the prompt as written (it may have read a name in it), "
                    "so its own suggested wording was used.")
    if info.code in ("bad_composition_plan", "bad_prompt") and "composition_plan" in kwargs:
        suggestion = info.data.get("composition_plan_suggestion")
        if isinstance(suggestion, dict) and suggestion:
            return ({**kwargs, "composition_plan": suggestion},
                    "The music provider would not take the section plan as written, so its own suggested plan was used.")
    return None


def compose_music(cfg: AIConfig, spec: MusicSpec, *, request_id: str, cancelled: Optional[Callable[[], bool]] = None,
                  client: Any = None) -> MusicResult:
    done = _claim("music", request_id)
    if done is not None:
        return done
    result: Optional[MusicResult] = None
    try:
        if _is_cancelled(cancelled):
            raise Cancelled()
        client = client or get_client(cfg)
        kwargs = build_music_request(cfg, spec)
        notes: list[str] = []
        audio = b""
        for round_ in range(2):  # the request as built, then at most one provider-suggested rewrite
            try:
                audio = _with_backoff(lambda: _consume(client.music.compose(**kwargs)), cancelled)
                break
            except _Failed as failed:
                rewrite = _apply_suggestion(kwargs, failed.info) if round_ == 0 else None
                if rewrite is None:
                    raise _failure(failed.info, music=True, model=cfg.elevenlabs_music_model) from None
                kwargs, note = rewrite
                notes.append(note)
        if _is_cancelled(cancelled):
            raise Cancelled()
        if not audio:
            raise ProviderFailure("other", "The music provider returned no audio.")
        length = kwargs.get("music_length_ms") or compose_prompt.plan_length_ms(kwargs.get("composition_plan") or {})
        result = MusicResult(
            audio=audio, media_type="audio/mpeg", ext="mp3", standin=False, standin_reason=None,
            provider=ProviderMeta(name="elevenlabs", model=cfg.elevenlabs_music_model or None, request_id=request_id,
                                  duration_s=round(length / 1000.0, 3) if length else None),
            notes=notes,
        )
        return result
    finally:
        _finish("music", request_id, result)


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------

_SET_VOICE = "Set ELEVENLABS_VOICE_ID on the server to a voice id from your ElevenLabs voice library."


def resolve_voice(cfg: AIConfig, client: Any) -> str:
    """ELEVENLABS_VOICE_ID when set, else the first voice the account can use. No voice id is built in."""
    if cfg.elevenlabs_voice_id:
        return cfg.elevenlabs_voice_id
    key = (make_client, cfg.elevenlabs_api_key)
    if key in _voices:
        return _voices[key]
    try:
        found = client.voices.search(page_size=1)
        voices = getattr(found, "voices", None) or []
        voice_id = getattr(voices[0], "voice_id", None) if voices else None
    except Exception as exc:
        info = error_info(exc)
        if info.kind in ("auth", "timeout", "network", "rate", "server"):
            raise _failure(info, music=False) from None
        voice_id = None
    if not voice_id:
        raise ProviderFailure("voice", f"No voice could be found on this ElevenLabs account, {_TTS_END}. {_SET_VOICE}")
    _voices[key] = str(voice_id)
    return _voices[key]


def narrate(cfg: AIConfig, text: str, *, request_id: str, client: Any = None) -> NarrationResult:
    done = _claim("tts", request_id)
    if done is not None:
        return done
    result: Optional[NarrationResult] = None
    try:
        client = client or get_client(cfg)
        voice_id = resolve_voice(cfg, client)
        kwargs: dict[str, Any] = {"text": (text or "")[:MAX_TTS_CHARS], "output_format": OUTPUT_FORMAT,
                                  "request_options": {"timeout_in_seconds": TTS_TIMEOUT_S}}
        if cfg.elevenlabs_tts_model:
            kwargs["model_id"] = cfg.elevenlabs_tts_model
        try:
            audio = _with_backoff(lambda: _consume(client.text_to_speech.convert(voice_id, **kwargs)))
        except _Failed as failed:
            failure = _failure(failed.info, music=False, model=cfg.elevenlabs_tts_model)
            if not cfg.elevenlabs_voice_id and failed.info.kind in ("plan", "other", "bug"):
                # The account's first voice may not be usable on this plan (spec 10.9).
                failure.friendly = f"{failure.friendly} If the default voice is not available on your plan: {_SET_VOICE}"
            raise failure from None
        if not audio:
            raise ProviderFailure("other", f"The narration provider returned no audio, {_TTS_END}.")
        message = None
        if len(text or "") > MAX_TTS_CHARS:
            message = f"Only the first {MAX_TTS_CHARS} characters were narrated."
        result = NarrationResult(
            audio=audio, media_type="audio/mpeg", ext="mp3", standin=False, message=message,
            provider=ProviderMeta(name="elevenlabs", model=cfg.elevenlabs_tts_model or None, request_id=request_id),
        )
        return result
    finally:
        _finish("tts", request_id, result)
