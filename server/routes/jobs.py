"""Job status: poll, stream (server-sent events) or cancel (spec 13.3, 7.16)."""
from __future__ import annotations

from typing import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from .. import schemas
from ..db import UserRow
from ..deps import AppCtx, current_user, get_ctx

router = APIRouter(prefix="/jobs", tags=["jobs"])

SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def sse(model) -> str:
    return f"data: {model.model_dump_json()}\n\n"


@router.get("/{job_id}", response_model=schemas.Job)
def get_job(job_id: str, user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> schemas.Job:
    return ctx.jobs.get(user.id, job_id)


@router.get("/{job_id}/events")
def job_events(job_id: str, user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> StreamingResponse:
    ctx.jobs.get(user.id, job_id)  # a 404 must be an ordinary error response, not a broken stream
    user_id = user.id

    def events() -> Iterator[str]:
        for job in ctx.jobs.events(user_id, job_id):
            yield sse(job) if job is not None else ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/{job_id}/cancel", response_model=schemas.Job)
def cancel_job(job_id: str, user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> schemas.Job:
    return ctx.jobs.cancel(user.id, job_id)
