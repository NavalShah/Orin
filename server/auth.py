"""Profile-picker sessions (spec 10.2, open question Q3).

DEMO-GRADE BY DESIGN: there are no passwords. Anyone can pick any name and gets that
profile. What the server does guarantee is isolation between tokens: a session token is
random, only its SHA-256 is stored, and every query is scoped to the token's user.

Tokens travel in the Authorization header, never in a cookie, so a cross-site form or
image request carries no credentials: there is no CSRF surface to protect (spec 13.4).
"""
from __future__ import annotations

import hashlib
import re
import secrets
from typing import Optional

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from . import schemas
from .db import SessionRow, UserRow, dumps, loads, new_id, now_iso
from .deps import AppCtx
from .errors import ApiError

MAX_NOTES = 50
MAX_NOTE_CHARS = 200


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def unauthorized(message: str = "Sign in to continue.") -> ApiError:
    return ApiError(401, "unauthorized", message, hint="Pick a profile, then try again.",
                    headers={"WWW-Authenticate": "Bearer"})


def user_for_token(ctx: AppCtx, token: Optional[str]) -> Optional[UserRow]:
    if not token:
        return None
    with ctx.db.session() as s:
        session = s.get(SessionRow, hash_token(token))
        return s.get(UserRow, session.user_id) if session else None


def user_for_request(ctx: AppCtx, request: Request) -> UserRow:
    token = bearer_token(request)
    if token is None:
        raise unauthorized()
    user = user_for_token(ctx, token)
    if user is None:
        raise unauthorized("Your session has ended.")
    return user


def clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", "".join(ch for ch in name if ch.isprintable())).strip()
    if not name:
        raise ApiError(422, "validation_error", "Enter a name for your profile.")
    return name[:40]


def prefs_of(row: UserRow) -> schemas.UserPrefs:
    try:
        return schemas.UserPrefs.model_validate(loads(row.prefs_json, {}))
    except ValueError:
        return schemas.UserPrefs()


def user_out(row: UserRow) -> schemas.User:
    return schemas.User(id=row.id, name=row.name, created_at=row.created_at, prefs=prefs_of(row))


def open_session(ctx: AppCtx, name: str) -> schemas.Session:
    name = clean_name(name)
    token = secrets.token_urlsafe(32)

    def get_or_create() -> UserRow:
        with ctx.db.session() as s:
            user = s.execute(select(UserRow).where(UserRow.name_key == name.casefold())).scalar_one_or_none()
            if user is None:
                user = UserRow(id=new_id(), name=name, name_key=name.casefold(), created_at=now_iso(),
                               prefs_json=dumps(schemas.UserPrefs().model_dump()))
                s.add(user)
            s.add(SessionRow(token_hash=hash_token(token), user_id=user.id, created_at=now_iso()))
            return user

    try:
        user = get_or_create()
    except IntegrityError:  # two first sign-ins with the same name at once: the second finds the first
        user = get_or_create()
    return schemas.Session(token=token, user=user_out(user))


def close_session(ctx: AppCtx, request: Request) -> None:
    token = bearer_token(request)
    if token:
        with ctx.db.session() as s:
            s.execute(delete(SessionRow).where(SessionRow.token_hash == hash_token(token)))


def update_prefs(ctx: AppCtx, user_id: str, change: schemas.PrefsUpdate) -> schemas.User:
    with ctx.db.session() as s:
        user = s.get(UserRow, user_id)
        prefs = prefs_of(user).model_dump()
        for key, value in change.model_dump(exclude_unset=True).items():
            if value is not None:
                prefs[key] = value
        prefs["language"] = (prefs.get("language") or "en").strip()[:16] or "en"
        prefs["notes"] = [n.strip()[:MAX_NOTE_CHARS] for n in prefs.get("notes", []) if n.strip()][:MAX_NOTES]
        user.prefs_json = dumps(prefs)
    return user_out(user)
