"""The Library: upload, list, download, waveform peaks, delete."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse

from .. import schemas
from ..auth import bearer_token, user_for_request
from ..db import AssetRow, UserRow
from ..deps import AppCtx, current_user, get_ctx
from ..errors import ApiError, not_found
from ..services import assets as asset_service

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post("", response_model=schemas.Asset)
def upload(file: UploadFile = File(...), source: str = Form("upload"), user: UserRow = Depends(current_user),
           ctx: AppCtx = Depends(get_ctx)) -> schemas.Asset:
    row = asset_service.store_upload(ctx, user.id, file.file, file.filename, source)
    return asset_service.asset_out(ctx, row)


@router.get("", response_model=list[schemas.Asset])
def list_assets(kind: Optional[schemas.AssetKind] = None, source: Optional[schemas.AssetSource] = None,
                user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> list[schemas.Asset]:
    return asset_service.list_assets(ctx, user.id, kind, source)


@router.get("/{asset_id}", response_model=schemas.Asset)
def get_asset(asset_id: str, user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> schemas.Asset:
    return asset_service.asset_out(ctx, asset_service.get_row(ctx, user.id, asset_id))


@router.get("/{asset_id}/file")
def asset_file(asset_id: str, request: Request, exp: Optional[str] = None, sig: Optional[str] = None,
               ctx: AppCtx = Depends(get_ctx)) -> FileResponse:
    """The bytes. Either a signed link (so <audio src> works without headers) or a Bearer token."""
    if ctx.signer.verify(asset_id, exp, sig):
        with ctx.db.session() as s:
            row = s.get(AssetRow, asset_id)  # the signature is the permission for this one file
    elif bearer_token(request) is None and (exp is not None or sig is not None):
        # 403, not 401: a stale link must not sign the user out.
        raise ApiError(403, "link_expired", "This link has expired or is not valid.",
                       hint="Reload the page to get a fresh link.", retryable=True)
    else:
        row = asset_service.get_row(ctx, user_for_request(ctx, request).id, asset_id)
    if row is None or not ctx.storage.exists(row.sha256):
        raise not_found("That file")
    media_type = row.media_type + ("; charset=utf-8" if row.media_type.startswith("text/") else "")
    # Always an attachment with a fixed type: nothing a user uploaded is ever rendered as a
    # page from this origin. Media elements ignore the disposition, so <audio src> still plays.
    return FileResponse(
        ctx.storage.path(row.sha256), media_type=media_type, filename=row.filename,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


@router.get("/{asset_id}/peaks", response_model=schemas.Peaks)
def asset_peaks(asset_id: str, buckets: int = Query(800, ge=1, le=100000), user: UserRow = Depends(current_user),
                ctx: AppCtx = Depends(get_ctx)) -> schemas.Peaks:
    return asset_service.peaks(ctx, user.id, asset_id, buckets)


@router.delete("/{asset_id}", response_model=schemas.Ok)
def delete_asset(asset_id: str, user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> schemas.Ok:
    asset_service.delete_asset(ctx, user.id, asset_id)
    return schemas.Ok()
