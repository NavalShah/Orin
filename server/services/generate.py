"""Music generation as a background job (spec 10.3 steps 7-10, 10.7, 10.9).

The provider call, its retries and its stand-in all live in ``orin.ai.service``. This
module validates the spec, enforces the quota, stores the audio in the user's Library
and records what made it (spec, provider, model, request id, duration).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from .. import schemas
from ..db import JobRow, UserRow
from ..deps import AppCtx
from ..errors import ApiError, redact
from ..jobs import JobContext
from . import assets as asset_service
from . import chat as chat_service
from .explain import ai_service

MAX_TEXT = 600
MAX_LIST = 16


def validate_spec(spec: Any) -> None:
    from orin.ai.schema import MAX_DURATION_S, MIN_DURATION_S

    def bad(message: str, hint: str = "Edit the request and try again.") -> ApiError:
        return ApiError(422, "invalid_spec", message, hint=hint)

    if not MIN_DURATION_S <= spec.duration_s <= MAX_DURATION_S:
        raise bad(f"A track must be between {MIN_DURATION_S} and {MAX_DURATION_S} seconds long.",
                  "Change the duration and try again.")
    if not (spec.genre_id or "").strip():
        raise bad("Choose a genre for the track.")
    spec.genre_id = chat_service.check_genre(spec.genre_id)
    for section in spec.sections:
        section.genre_id = chat_service.check_genre(section.genre_id) or spec.genre_id
        if section.duration_s < 1 or len(section.description) > MAX_TEXT:
            raise bad("One of the sections has a length or a description Orin cannot use.")
    if len(spec.sections) > 8 or sum(s.duration_s for s in spec.sections) > MAX_DURATION_S:
        raise bad(f"The sections add up to more than {MAX_DURATION_S} seconds.")
    if spec.tempo_bpm is not None and not 20 <= spec.tempo_bpm <= 300:
        raise bad("The tempo must be between 20 and 300 beats per minute.")
    for name in ("mood", "instruments", "avoid", "assumptions"):
        values = getattr(spec, name)
        if len(values) > MAX_LIST or any(len(v) > 120 for v in values):
            raise bad(f"'{name}' has too many entries, or one that is too long.")
    for name in ("structure_notes", "learning_goal", "key_or_tonic", "mode_hint"):
        if len(getattr(spec, name) or "") > MAX_TEXT:
            raise bad(f"'{name}' is too long.")


def generations_today(ctx: AppCtx, user_id: str) -> int:
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="microseconds")
    with ctx.db.session() as s:
        return s.execute(
            select(func.count()).select_from(JobRow).where(
                JobRow.user_id == user_id, JobRow.type == "generate", JobRow.created_at >= midnight,
                JobRow.status.notin_(("failed", "cancelled")),
            )
        ).scalar_one()


def _sanitize(ai: Any, spec: Any, notes: list[str]) -> Any:
    """Free text in a spec can be edited by the user after Orin refined it, so it goes
    through the prompt policy again before it reaches the provider (spec 10.7)."""

    def clean(text: str) -> str:
        result = ai.sanitize_prompt(text)
        if result.blocked:
            raise ApiError(400, "prompt_blocked", redact(result.reason) if result.reason else "That request cannot be used to make music.",
                           hint="Describe the sound you want (tempo, mood, instruments) without naming artists or songs.")
        notes.extend(redact(n) for n in result.notes if n not in notes)
        return result.text

    update: dict[str, Any] = {}
    if spec.structure_notes:
        update["structure_notes"] = clean(spec.structure_notes)
    if spec.sections:
        update["sections"] = [s.model_copy(update={"description": clean(s.description)}) if s.description else s
                              for s in spec.sections]
    return spec.model_copy(update=update) if update else spec


def start(ctx: AppCtx, user: UserRow, req: schemas.GenerateRequest) -> schemas.JobRef:
    spec = req.spec.model_copy(deep=True)
    validate_spec(spec)
    thread_id = req.thread_id or None
    if thread_id:
        chat_service.require_thread(ctx, user.id, thread_id)
    ai_service()  # a clear 503 now, instead of a failed job later
    limit = ctx.settings.daily_generations
    if generations_today(ctx, user.id) >= limit:
        raise ApiError(429, "quota_exceeded", f"You have used today's {limit} generation{'' if limit == 1 else 's'}.",
                       hint="The count resets at midnight UTC.")
    user_id = user.id

    def run(job: JobContext) -> dict[str, str]:
        ai = ai_service()
        notes: list[str] = []
        job.progress("prepare", 0.05, "Checking the request")
        final = _sanitize(ai, spec, notes)
        job.progress("compose", 0.15, "Composing...")
        result = ai.compose_music(final, request_id=job.job_id, cancelled=job.cancelled)
        job.check()
        if not result.audio:
            raise ApiError(502, "generation_failed", "No music came back for this request.", hint="Try again.", retryable=True)

        job.progress("store", 0.9, "Saving the track")
        notes += [redact(n) for n in result.notes if n not in notes]
        reason = redact(result.standin_reason) if result.standin_reason else None
        provider = result.provider.model_dump()
        asset = asset_service.store_bytes(
            ctx, user_id, result.audio, kind="audio", source="standin" if result.standin else "generated",
            filename=f"orin-{final.genre_id}-{job.job_id[:6]}.{result.ext}".replace(" ", "-"),
            media_type=result.media_type, standin=result.standin,
            meta={"spec": final.model_dump(mode="json"), "provider": provider, "standin_reason": reason,
                  "notes": notes, "job_id": job.job_id},
        )
        try:  # peaks for the waveform (spec 10.9); a failure here must not lose the track
            asset_service.peaks(ctx, user_id, asset.id, 800)
        except Exception:
            pass
        if not thread_id:
            return {"kind": "asset", "id": asset.id}

        out = asset_service.find_out(ctx, user_id, asset.id)
        duration = provider.get("duration_s") or (out.duration_s if out else None)
        card = schemas.Card(
            kind="audio", asset=out, spec=final, notes=notes, standin=result.standin, standin_reason=reason,
            provider=schemas.ProviderInfo(name=provider.get("name") or "offline", model=provider.get("model"),
                                          request_id=provider.get("request_id") or job.job_id, duration_s=duration),
        )
        text = "Here's your track."
        if result.standin:
            text = "This is a synthesised stand-in clip, not a generated track." + (f" {reason}" if reason else "")
        message = chat_service.add_message(
            ctx, user_id, thread_id, "assistant", text, cards=[card], track_ids=[asset.id], spec=final,
            meta={"job_id": job.job_id, "provider": provider, "standin": result.standin},
        )
        return {"kind": "message", "id": message.id}

    return schemas.JobRef(job_id=ctx.jobs.submit(user.id, "generate", run, message="Waiting to start"))
