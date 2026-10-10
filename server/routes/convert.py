"""Notation Studio: convert, re-run, read results, explain and narrate."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from .. import schemas
from ..db import UserRow
from ..deps import AppCtx, current_user, get_ctx, limited
from ..services import conversions as conversion_service
from ..services import explain as explain_service

router = APIRouter(tags=["convert"])


@router.post("/convert", response_model=schemas.JobRef)
def convert(body: schemas.ConvertRequest, user: UserRow = Depends(limited("convert")),
            ctx: AppCtx = Depends(get_ctx)) -> schemas.JobRef:
    return conversion_service.start(ctx, user.id, body)


@router.get("/conversions", response_model=list[schemas.ConversionSummary])
def list_conversions(user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> list[schemas.ConversionSummary]:
    return conversion_service.list_summaries(ctx, user.id)


@router.get("/conversions/{conversion_id}", response_model=schemas.ConversionOut)
def get_conversion(conversion_id: str, user: UserRow = Depends(current_user),
                   ctx: AppCtx = Depends(get_ctx)) -> schemas.ConversionOut:
    return conversion_service.get_out(ctx, user.id, conversion_id)


@router.get("/conversions/{conversion_id}/ir")
def conversion_ir(conversion_id: str, user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> Response:
    """The full IR, as a JSON download."""
    body = conversion_service.ir_json(ctx, user.id, conversion_id)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="orin-ir-{conversion_id[:8]}.json"'})


@router.post("/conversions/{conversion_id}/rerun", response_model=schemas.JobRef)
def rerun_conversion(conversion_id: str, body: schemas.RerunRequest, user: UserRow = Depends(limited("convert")),
                     ctx: AppCtx = Depends(get_ctx)) -> schemas.JobRef:
    return conversion_service.rerun(ctx, user.id, conversion_id, body)


@router.post("/explain", response_model=schemas.Explanation)
def explain(body: schemas.ExplainRequest, user: UserRow = Depends(limited("explain")),
            ctx: AppCtx = Depends(get_ctx)) -> schemas.Explanation:
    return explain_service.explain(ctx, user, body)


@router.post("/narrate", response_model=schemas.Narration)
def narrate(body: schemas.NarrateRequest, user: UserRow = Depends(limited("narrate")),
            ctx: AppCtx = Depends(get_ctx)) -> schemas.Narration:
    return explain_service.narrate(ctx, user, body.text)
