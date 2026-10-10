"""AI settings, read from the environment at call time (spec D12, D14).

Nothing here is read at import time, so tests and the server can change the
environment between calls. Keys are never logged and never put in a repr; use
``redact`` on any provider text before it reaches a log line or a result.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

ENV_NAMES = (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "ELEVENLABS_MUSIC_MODEL",
    "ELEVENLABS_TTS_MODEL",
    "ORIN_OFFLINE",
)
_KEY_NAMES = ("GEMINI_API_KEY", "ELEVENLABS_API_KEY")

OFFLINE_REASON = "ORIN_OFFLINE=1, so the built-in stand-ins are used."


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


@dataclass(frozen=True)
class AIConfig:
    gemini_api_key: str = field(default="", repr=False)
    gemini_model: str = ""
    elevenlabs_api_key: str = field(default="", repr=False)
    elevenlabs_voice_id: str = ""
    elevenlabs_music_model: str = ""  # empty = send no model id; the provider default applies
    elevenlabs_tts_model: str = ""
    offline: bool = False

    # Each ``*_standin_reason`` returns None when the live provider should be used,
    # else one friendly sentence saying why the stand-in is in use.

    def llm_standin_reason(self) -> Optional[str]:
        if self.offline:
            return OFFLINE_REASON
        if not self.gemini_api_key:
            return "GEMINI_API_KEY is not set, so chat and explanations use the offline stand-in."
        if not self.gemini_model:
            return ("GEMINI_MODEL is not set, so chat and explanations use the offline stand-in. "
                    "Set it to a current Gemini model id.")
        return None

    def music_standin_reason(self) -> Optional[str]:
        if self.offline:
            return OFFLINE_REASON
        if not self.elevenlabs_api_key:
            return "ELEVENLABS_API_KEY is not set, so music is a synthesised stand-in clip."
        return None

    def tts_standin_reason(self) -> Optional[str]:
        if self.offline:
            return OFFLINE_REASON
        if not self.elevenlabs_api_key:
            return "ELEVENLABS_API_KEY is not set, so narration is read by the browser's own voice."
        return None


def load() -> AIConfig:
    return AIConfig(
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_model=_env("GEMINI_MODEL"),
        elevenlabs_api_key=_env("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=_env("ELEVENLABS_VOICE_ID"),
        elevenlabs_music_model=_env("ELEVENLABS_MUSIC_MODEL"),
        elevenlabs_tts_model=_env("ELEVENLABS_TTS_MODEL"),
        offline=_env("ORIN_OFFLINE") == "1",
    )


def redact(text: object, cfg: Optional[AIConfig] = None) -> str:
    """Replace any configured key value found in ``text``. Safe to call on anything."""
    out = "" if text is None else str(text)
    secrets = {_env(name) for name in _KEY_NAMES}
    if cfg is not None:
        secrets |= {cfg.gemini_api_key, cfg.elevenlabs_api_key}
    for secret in sorted((s for s in secrets if len(s) >= 4), key=len, reverse=True):
        out = out.replace(secret, "[redacted]")
    return out
