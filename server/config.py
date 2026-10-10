"""Server settings, read from the environment (spec 13.8).

Provider keys and model ids (GEMINI_*, ELEVENLABS_*) are read by ``orin.ai`` only; this
module never touches them, so they cannot leak from here.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

DEFAULT_ORIGIN = "http://localhost:3000"


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(env.get(name) or default)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass
class Settings:
    data_dir: Path = Path("./data")
    offline: bool = False  # ORIN_OFFLINE=1; the AI layer reads the same variable itself
    max_upload_mb: float = 10.0
    max_audio_seconds: float = 300.0
    daily_generations: int = 20
    secret_key: str = ""  # empty = a random per-process secret (signed links die on restart)
    app_origin: str = DEFAULT_ORIGIN
    asset_url_ttl_s: int = 24 * 3600
    stem_ttl_s: int = 24 * 3600  # temp stems are swept at startup once older than this
    job_workers: int = 2
    # Token buckets per user: name -> (burst, seconds to refill one token).
    rate_limits: dict[str, tuple[int, float]] = field(default_factory=lambda: {
        "convert": (20, 3.0),
        "generate": (5, 20.0),
        "narrate": (15, 4.0),
        "chat": (30, 2.0),
        "explain": (30, 2.0),
    })

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if not self.secret_key:
            self.secret_key = secrets.token_hex(32)

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def cors_origins(self) -> list[str]:
        origin = self.app_origin.rstrip("/")
        # localhost and 127.0.0.1 are different origins to a browser; allow both in dev.
        return [origin, "http://127.0.0.1:3000"] if origin == DEFAULT_ORIGIN else [origin]

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        return cls(
            data_dir=Path(env.get("ORIN_DATA_DIR") or "./data"),
            offline=(env.get("ORIN_OFFLINE") or "").strip().lower() in ("1", "true", "yes", "on"),
            max_upload_mb=_float(env, "ORIN_MAX_UPLOAD_MB", 10.0),
            max_audio_seconds=_float(env, "ORIN_MAX_AUDIO_SECONDS", 300.0),
            daily_generations=int(_float(env, "ORIN_DAILY_GENERATIONS", 20)),
            secret_key=env.get("ORIN_SECRET_KEY") or "",
            app_origin=env.get("ORIN_APP_ORIGIN") or DEFAULT_ORIGIN,
        )
