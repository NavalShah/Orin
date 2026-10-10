"""Chat threads, the streamed reply, and music generation."""
from __future__ import annotations

from typing import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from .. import schemas
from ..db import UserRow
from ..deps import AppCtx, current_user, get_ctx, limited
from ..services import chat as chat_service
from ..services import generate as generate_service
from .jobs import SSE_HEADERS, sse

router = APIRouter(tags=["chat"])


@router.get("/chat/threads", response_model=list[schemas.Thread])
def list_threads(user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> list[schemas.Thread]:
    return chat_service.list_threads(ctx, user.id)


@router.post("/chat/threads", response_model=schemas.Thread)
def create_thread(body: schemas.ThreadCreate, user: UserRow = Depends(current_user),
                  ctx: AppCtx = Depends(get_ctx)) -> schemas.Thread:
    return chat_service.create_thread(ctx, user.id, body.title)


@router.patch("/chat/threads/{thread_id}", response_model=schemas.Thread)
def rename_thread(thread_id: str, body: schemas.ThreadUpdate, user: UserRow = Depends(current_user),
                  ctx: AppCtx = Depends(get_ctx)) -> schemas.Thread:
    return chat_service.rename_thread(ctx, user.id, thread_id, body.title)


@router.delete("/chat/threads/{thread_id}", response_model=schemas.Ok)
def delete_thread(thread_id: str, user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> schemas.Ok:
    chat_service.delete_thread(ctx, user.id, thread_id)
    return schemas.Ok()


@router.get("/chat/threads/{thread_id}/messages", response_model=list[schemas.Message])
def list_messages(thread_id: str, user: UserRow = Depends(current_user),
                  ctx: AppCtx = Depends(get_ctx)) -> list[schemas.Message]:
    return chat_service.list_messages(ctx, user.id, thread_id)


@router.post("/chat/threads/{thread_id}/messages")
def send_message(thread_id: str, body: schemas.MessageCreate, user: UserRow = Depends(limited("chat")),
                 ctx: AppCtx = Depends(get_ctx)) -> StreamingResponse:
    """A stream of ChatEvent. Anything wrong with the request itself is an ordinary error
    response; after that, problems arrive as an ``error`` event."""
    turn = chat_service.prepare(ctx, user, thread_id, body)

    def events() -> Iterator[str]:
        for event in chat_service.stream(ctx, user, turn):
            yield sse(event)

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/generate", response_model=schemas.JobRef)
def generate(body: schemas.GenerateRequest, user: UserRow = Depends(limited("generate")),
             ctx: AppCtx = Depends(get_ctx)) -> schemas.JobRef:
    return generate_service.start(ctx, user, body)
