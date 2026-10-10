"""Open endpoints: health, the format registry and the glossary. No user data."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import glossary, schemas
from ..deps import AppCtx, get_ctx
from ..services import formats as format_service

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=schemas.Health)
def health(ctx: AppCtx = Depends(get_ctx)) -> schemas.Health:
    import orin

    return schemas.Health(version=orin.__version__, offline=format_service.offline_status(ctx))


@router.get("/formats", response_model=schemas.FormatsResponse)
def formats(ctx: AppCtx = Depends(get_ctx)) -> schemas.FormatsResponse:
    return format_service.formats_response(ctx)


@router.get("/glossary", response_model=list[schemas.GlossaryEntry])
def glossary_entries() -> list[dict]:
    return glossary.entries()
