"""What every route and service shares: the app context and the request dependencies."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Request

from .config import Settings
from .db import Database, UserRow
from .jobs import JobManager
from .ratelimit import RateLimiter
from .storage import Storage, UrlSigner


@dataclass
class AppCtx:
    settings: Settings
    db: Database
    storage: Storage
    signer: UrlSigner
    jobs: JobManager
    limiter: RateLimiter
    memo: dict[str, Any] = field(default_factory=dict)  # small process-lifetime caches (the registry view)


def get_ctx(request: Request) -> AppCtx:
    return request.app.state.ctx


def current_user(request: Request, ctx: AppCtx = Depends(get_ctx)) -> UserRow:
    from .auth import user_for_request

    return user_for_request(ctx, request)


def limited(name: str):
    """Dependency: authenticate, then take one token from the user's ``name`` bucket."""

    def dep(user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> UserRow:
        ctx.limiter.check(name, user.id)
        return user

    return dep
