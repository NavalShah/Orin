"""Loss explanations and narration (spec 9.4, 9.5). The AI layer never raises for a
provider failure: it degrades to its stand-in and says so in the result."""
from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.exc import IntegrityError

from .. import schemas
from ..auth import prefs_of
from ..db import ExplanationRow, UserRow, dumps, loads, new_id, now_iso
from ..deps import AppCtx
from ..errors import ApiError, redact
from . import assets as asset_service
from . import conversions as conversion_service
from . import formats as format_service


def ai_service() -> Any:
    try:
        from orin.ai import service
    except ImportError:
        raise ApiError(503, "not_available", "Orin's explanations and music tools are not installed on this server yet.",
                       hint="Ask whoever runs the server to finish the installation.") from None
    return service


def _meta(format_id):
    from orin.formats import registry

    return registry.get(format_id).META if format_id and registry.has(format_id) else None


def explain(ctx: AppCtx, user: UserRow, req: schemas.ExplainRequest) -> schemas.Explanation:
    report = conversion_service.loss_report(ctx, user.id, req.conversion_id)
    prefs = prefs_of(user)
    level = req.level or prefs.level
    language = prefs.language or "en"
    question = (req.question or "").strip() or None
    indexes = sorted(set(req.item_indexes))
    if any(i < 0 or i >= len(report.items) for i in indexes):
        raise ApiError(422, "validation_error", "One of the chosen loss items is not in this report.",
                       hint="Reload the conversion and choose again.")
    target_meta = _meta(report.target_format)
    if target_meta is None:
        raise ApiError(400, "unknown_format", f"'{report.target_format}' is not a format this server knows any more.")

    # Looked up before the AI call, so a repeat costs nothing (spec 9.4: cache by explain
    # key, level and language). The key also covers the numbers the text is grounded in,
    # so one conversion's "150 cents" is never served for another, and a personal question
    # is cached for that user only.
    chosen = [report.items[i] for i in indexes] if indexes else list(report.items)
    basis = {
        "keys": sorted({item.explain_key for item in chosen}),
        "items": hashlib.sha256(dumps([item.model_dump(mode="json") for item in chosen]).encode()).hexdigest(),
        "summary": report.summary.model_dump(mode="json") if not indexes else None,
        "formats": [report.source_format, report.target_format],
        "level": level, "language": language, "question": question,
        "user": user.id if question else None,
        "llm_offline": format_service.offline_status(ctx).llm,
    }
    lookup = hashlib.sha256(dumps(basis).encode()).hexdigest()
    with ctx.db.session() as s:
        hit = s.get(ExplanationRow, lookup)
    if hit is not None:
        return schemas.Explanation(text=hit.text, try_this=loads(hit.try_this_json, []), source=hit.source,  # type: ignore[arg-type]
                                   cached=True, key=hit.ai_key, level=level)

    result = ai_service().explain_loss(
        report, item_indexes=tuple(indexes), source_meta=_meta(report.source_format), target_meta=target_meta,
        level=level, question=question, language=language,
    )
    text = redact(result.text)
    try:
        with ctx.db.session() as s:
            if s.get(ExplanationRow, lookup) is None:
                s.add(ExplanationRow(key=lookup, ai_key=result.key, level=level, language=language, text=text,
                                     try_this_json=dumps(list(result.try_this)), source=result.source, created_at=now_iso()))
    except IntegrityError:
        pass  # the same question was answered twice at once; either answer will do
    return schemas.Explanation(text=text, try_this=list(result.try_this), source=result.source, cached=False,
                               key=result.key, level=level)


def narrate(ctx: AppCtx, user: UserRow, text: str) -> schemas.Narration:
    """Audio is cached by text hash (spec 9.5). On the stand-in there is no audio: the
    browser reads ``text`` itself, and the text is the transcript either way."""
    text = text.strip()
    if not text:
        raise ApiError(422, "validation_error", "There is no text to read aloud.")
    ref = "narration:" + hashlib.sha256(text.encode()).hexdigest()
    cached = asset_service.find_by_ref(ctx, user.id, ref)
    if cached is not None and ctx.storage.exists(cached.sha256):
        return schemas.Narration(asset=asset_service.asset_out(ctx, cached), standin=False, text=text)

    request_id = new_id()
    result = ai_service().narrate(text, request_id=request_id)
    message = redact(result.message) if result.message else None
    if result.standin or not result.audio:
        return schemas.Narration(asset=None, standin=True, text=text,
                                 message=message or "Narration is not set up, so your browser will read this aloud.")
    row = asset_service.store_bytes(
        ctx, user.id, result.audio, kind="audio", source="narration",
        filename=f"narration-{request_id[:8]}.{result.ext or 'mp3'}", media_type=result.media_type or "audio/mpeg",
        meta={"transcript": text, "provider": result.provider.model_dump()}, ref=ref,
    )
    return schemas.Narration(asset=asset_service.asset_out(ctx, row), standin=False, text=text, message=message)
