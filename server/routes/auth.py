"""Profile-picker sessions. See server/auth.py for what this does and does not protect."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import auth, schemas
from ..db import UserRow
from ..deps import AppCtx, current_user, get_ctx

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/session", response_model=schemas.Session)
def open_session(body: schemas.SessionRequest, ctx: AppCtx = Depends(get_ctx)) -> schemas.Session:
    return auth.open_session(ctx, body.name)


@router.get("/me", response_model=schemas.User)
def me(user: UserRow = Depends(current_user)) -> schemas.User:
    return auth.user_out(user)


@router.patch("/me", response_model=schemas.User)
def update_me(body: schemas.PrefsUpdate, user: UserRow = Depends(current_user),
              ctx: AppCtx = Depends(get_ctx)) -> schemas.User:
    return auth.update_prefs(ctx, user.id, body)


@router.post("/logout", response_model=schemas.Ok)
def logout(request: Request, user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> schemas.Ok:
    auth.close_session(ctx, request)
    return schemas.Ok()
