"""Chat threads and the message stream (spec 10.2, 10.3, 10.6).

Every thread and message is read and written through the owning user's id. The stream
yields ``ChatEvent`` objects in the order the contract gives: intent, then deltas or a
card (plus a job for a transcription), then the stored message, then done.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from sqlalchemy import delete, select

from .. import schemas
from ..auth import prefs_of
from ..db import MessageRow, ThreadRow, UserRow, dumps, loads, new_id, now_iso, owned
from ..deps import AppCtx
from ..errors import ApiError, body_for_exception, log_exception, not_found, redact
from . import assets as asset_service
from . import conversions as conversion_service
from .explain import ai_service

DEFAULT_TITLE = "New chat"
HISTORY_MESSAGES = 20  # the last N turns...
HISTORY_CHARS = 6000  # ...within a rough character budget (about 1500 tokens)
MAX_TURN_CHARS = 2000
CONTEXT_ITEMS = 12
TRANSCRIBE_TARGET = "western_classical"
PLAIN_GENRE = re.compile(r"[A-Za-z][A-Za-z0-9 '&+/\-]{0,39}")

INSPIRED_BY = (
    "One thing to know about changing styles: music generated from a transcription is "
    "\"inspired by\" the original, not an exact copy of its melody. For a faithful version, "
    "use the notation from the Notation Studio or its MIDI in the Workspace."
)


# --- threads --------------------------------------------------------------------

def thread_out(row: ThreadRow) -> schemas.Thread:
    return schemas.Thread(id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at,
                          summary=row.summary)


def list_threads(ctx: AppCtx, user_id: str) -> list[schemas.Thread]:
    with ctx.db.session() as s:
        rows = s.execute(select(ThreadRow).where(ThreadRow.user_id == user_id)
                         .order_by(ThreadRow.updated_at.desc())).scalars().all()
    return [thread_out(r) for r in rows]


def create_thread(ctx: AppCtx, user_id: str, title: Optional[str]) -> schemas.Thread:
    now = now_iso()
    row = ThreadRow(id=new_id(), user_id=user_id, title=(title or "").strip()[:120] or DEFAULT_TITLE,
                    created_at=now, updated_at=now)
    with ctx.db.session() as s:
        s.add(row)
    return thread_out(row)


def rename_thread(ctx: AppCtx, user_id: str, thread_id: str, title: str) -> schemas.Thread:
    with ctx.db.session() as s:
        row = owned(s, ThreadRow, user_id, thread_id)
        if row is None:
            raise not_found("That conversation")
        row.title = title.strip()[:120] or DEFAULT_TITLE
        row.updated_at = now_iso()
    return thread_out(row)


def delete_thread(ctx: AppCtx, user_id: str, thread_id: str) -> None:
    with ctx.db.session() as s:
        row = owned(s, ThreadRow, user_id, thread_id)
        if row is None:
            raise not_found("That conversation")
        s.execute(delete(MessageRow).where(MessageRow.thread_id == thread_id, MessageRow.user_id == user_id))
        s.delete(row)


def require_thread(ctx: AppCtx, user_id: str, thread_id: str) -> ThreadRow:
    with ctx.db.session() as s:
        row = owned(s, ThreadRow, user_id, thread_id)
    if row is None:
        raise not_found("That conversation")
    return row


# --- messages -------------------------------------------------------------------

def message_out(ctx: AppCtx, user_id: str, row: MessageRow) -> schemas.Message:
    cards = []
    for raw in loads(row.cards_json, []):
        asset_id = raw.pop("asset_id", None)
        card = schemas.Card(**raw)
        if asset_id:  # stored by id, so the signed link is always fresh
            card.asset = asset_service.find_out(ctx, user_id, asset_id)
            if card.asset is None:
                card.text = "This track was deleted from your Library."
        cards.append(card)
    return schemas.Message(
        id=row.id, thread_id=row.thread_id, role=row.role, text=row.text, cards=cards,  # type: ignore[arg-type]
        track_ids=loads(row.track_ids, []), spec=loads(row.spec_json), created_at=row.created_at,
        meta=loads(row.meta_json, {}),
    )


def list_messages(ctx: AppCtx, user_id: str, thread_id: str) -> list[schemas.Message]:
    require_thread(ctx, user_id, thread_id)
    with ctx.db.session() as s:
        rows = s.execute(select(MessageRow).where(MessageRow.thread_id == thread_id, MessageRow.user_id == user_id)
                         .order_by(MessageRow.created_at)).scalars().all()
    return [message_out(ctx, user_id, r) for r in rows]


def add_message(ctx: AppCtx, user_id: str, thread_id: str, role: str, text: str, *,
                cards: Optional[list[schemas.Card]] = None, track_ids: Optional[list[str]] = None,
                spec: Any = None, meta: Optional[dict[str, Any]] = None) -> schemas.Message:
    """Append a message to the user's thread. Titles a new thread from its first message."""
    stored_cards = []
    for card in cards or []:
        raw = card.model_dump(mode="json", exclude={"asset"})
        raw["asset_id"] = card.asset.id if card.asset else None
        stored_cards.append(raw)
    now = now_iso()
    row = MessageRow(
        id=new_id(), thread_id=thread_id, user_id=user_id, role=role, text=text,
        spec_json=spec.model_dump_json() if spec is not None else None, cards_json=dumps(stored_cards),
        track_ids=dumps(track_ids or []), meta_json=dumps(meta or {}), created_at=now,
    )
    with ctx.db.session() as s:
        thread = owned(s, ThreadRow, user_id, thread_id)
        if thread is None:
            raise not_found("That conversation")
        if role == "user" and thread.title == DEFAULT_TITLE:
            thread.title = auto_title(text)
        thread.updated_at = now
        s.add(row)
    return message_out(ctx, user_id, row)


def auto_title(text: str) -> str:
    line = re.sub(r"\s+", " ", text).strip()
    if len(line) <= 48:
        return line or DEFAULT_TITLE
    return line[:48].rsplit(" ", 1)[0].rstrip(" ,.;:") + "..."


# --- context --------------------------------------------------------------------

@dataclass
class Turn:
    """Everything the stream needs, gathered (and validated) before it starts."""

    thread_id: str
    text: str
    attachments: list[Any]
    genre_id: Optional[str]
    history: list[Any]
    previous_specs: list[Any]
    prefs: dict[str, Any]
    context_text: Optional[str] = None
    notes: list[str] = field(default_factory=list)


def _history(ctx: AppCtx, user_id: str, thread_id: str) -> tuple[list[Any], list[Any]]:
    """(turns oldest first, earlier specs oldest first) for this thread."""
    from orin.ai.schema import MusicSpec
    from orin.ai.types import Turn as AITurn

    with ctx.db.session() as s:
        rows = s.execute(select(MessageRow).where(MessageRow.thread_id == thread_id, MessageRow.user_id == user_id)
                         .order_by(MessageRow.created_at.desc()).limit(60)).scalars().all()
    turns, used = [], 0
    for row in rows[:HISTORY_MESSAGES]:
        text = (row.text or "").strip()[:MAX_TURN_CHARS]
        if not text:
            continue
        used += len(text)
        if used > HISTORY_CHARS and turns:
            break
        turns.append(AITurn(role=row.role, text=text))  # type: ignore[arg-type]
    specs = []
    for row in rows:
        if row.spec_json and len(specs) < 5:
            try:
                specs.append(MusicSpec.model_validate_json(row.spec_json))
            except ValueError:
                pass
    return turns[::-1], specs[::-1]


def loss_context(ctx: AppCtx, user_id: str, context: Optional[schemas.ChatContext]) -> Optional[str]:
    """A compact text of the loss report the user is asking about (spec 3.5: Notation -> Chat)."""
    if context is None or not context.conversion_id:
        return None
    try:
        report = conversion_service.loss_report(ctx, user_id, context.conversion_id)
    except ApiError as exc:
        if exc.status_code == 404:
            raise
        return None  # the conversion has no report yet: answer without it
    s = report.summary
    lines = [
        f"The user is looking at a loss report for a conversion from {report.source_format or 'an unknown style'} "
        f"to {report.target_format}.",
        f"Fidelity {s.fidelity_score:.2f} (pitch {s.pitch_score:.2f}, ornament {s.ornament_score:.2f}, "
        f"timing {s.timing_score:.2f}). Largest pitch change: {s.max_cents_delta:.0f} cents.",
    ]
    if s.counts_by_code:
        lines.append("Counts: " + ", ".join(f"{code} x{n}" for code, n in s.counts_by_code.items()) + ".")
    chosen = [report.items[i] for i in context.item_indexes if 0 <= i < len(report.items)]
    items = chosen or list(report.items[:CONTEXT_ITEMS])
    if chosen:
        lines.append("They selected these items:")
    for item in items[:CONTEXT_ITEMS]:
        where = f" at {item.time_s:.2f} s" if item.time_s is not None else ""
        lines.append(f"- [{item.severity}] {item.code}{where}: {item.human_text}")
    if not chosen and len(report.items) > len(items):
        lines.append(f"(and {len(report.items) - len(items)} more items)")
    for notice in report.notices[:4]:
        lines.append(f"- note, not a loss: {notice.human_text}")
    return "\n".join(lines)


def check_genre(genre_id: Optional[str]) -> Optional[str]:
    """A registry id (canonical form) or a plain genre word. Anything else is refused."""
    from orin.formats import registry

    genre = (genre_id or "").strip()
    if not genre:
        return None
    if registry.has(genre):
        return registry.get(genre).META.id
    if PLAIN_GENRE.fullmatch(genre):
        return genre
    raise ApiError(422, "invalid_genre", f"'{genre[:40]}' is not a genre Orin can use.",
                   hint="Pick a style from the list, or type a plain genre word such as 'jazz'.")


def prepare(ctx: AppCtx, user: UserRow, thread_id: str, body: schemas.MessageCreate) -> Turn:
    """Validate, load context and store the user's message. Errors here are ordinary
    error responses; once the stream starts, errors become ``error`` events."""
    require_thread(ctx, user.id, thread_id)
    attachments = [asset_service.get_row(ctx, user.id, asset_id) for asset_id in body.attachment_asset_ids[:4]]
    genre_id = check_genre(body.genre_id)
    context_text = loss_context(ctx, user.id, body.context)
    history, specs = _history(ctx, user.id, thread_id)
    add_message(ctx, user.id, thread_id, "user", body.text, track_ids=[a.id for a in attachments],
                meta={"genre_id": genre_id, "context": body.context.model_dump() if body.context else None})
    return Turn(thread_id=thread_id, text=body.text, attachments=attachments, genre_id=genre_id, history=history,
                previous_specs=specs, prefs=prefs_of(user).model_dump(), context_text=context_text)


# --- the stream -----------------------------------------------------------------

def stream(ctx: AppCtx, user: UserRow, turn: Turn) -> Iterator[schemas.ChatEvent]:
    try:
        yield from _stream(ctx, user, turn)
    except Exception as exc:  # never a broken connection: the client gets an error event
        body = body_for_exception(exc)
        if body["code"] == "internal_error":
            log_exception(f"chat stream {turn.thread_id}", exc)
        yield schemas.ChatEvent(type="error", error=body)


def _delta(text: str) -> schemas.ChatEvent:
    return schemas.ChatEvent(type="delta", text=text)


def _stream(ctx: AppCtx, user: UserRow, turn: Turn) -> Iterator[schemas.ChatEvent]:
    ai = ai_service()
    cards: list[schemas.Card] = []
    spec = None
    text = ""

    clean = ai.sanitize_prompt(turn.text)
    notes = [redact(n) for n in clean.notes]
    if clean.blocked:
        intent = "chat"
        yield schemas.ChatEvent(type="intent", intent=intent)
        reason = redact(clean.reason).strip() if clean.reason else ""
        text = ("Sorry, I can't help with that request. " + (reason + " " if reason else "") +
                "You could describe the music you have in mind instead: its mood, tempo and instruments.")
        yield _delta(text)
    else:
        intent = ai.classify_intent(clean.text, history=turn.history, has_attachment=bool(turn.attachments),
                                    genre_id=turn.genre_id).intent
        yield schemas.ChatEvent(type="intent", intent=intent)

        if intent in ("transcribe", "convert"):
            usable = [a for a in turn.attachments if a.kind in ("audio", "midi", "notation")]
            if not usable:
                text = ("Attach a recording or a notes file from your Library and I'll write it out as notation. "
                        "You can also open the Notation Studio and drop a file there.")
                yield _delta(text)
            else:
                asset = usable[0]
                ctx.limiter.check("convert", user.id)
                ref = conversion_service.start(ctx, user.id, schemas.ConvertRequest(
                    asset_id=asset.id, input_format="auto", output_format=TRANSCRIBE_TARGET))
                yield schemas.ChatEvent(type="job", job_id=ref.job_id)
                text = (f"I'm transcribing \"{asset.filename}\" into Western staff notation. Open it in the Notation "
                        "Studio to see the notes, how sure Orin is about them, and what the notation could not keep.")
                card_notes = list(notes)
                if intent == "convert":
                    text += " " + INSPIRED_BY
                    card_notes.append(INSPIRED_BY)
                card = schemas.Card(kind="conversion", text=f"Transcription of {asset.filename}", notes=card_notes,
                                    conversion_id=ref.conversion_id, job_id=ref.job_id)
                cards.append(card)
                yield schemas.ChatEvent(type="card", card=card)

        elif intent == "generate":
            result = ai.refine_spec(clean.text, history=turn.history, prefs=turn.prefs,
                                    previous_specs=tuple(turn.previous_specs), genre_id=turn.genre_id)
            notes += [redact(n) for n in result.notes if n not in notes]
            if result.ok and result.spec is not None:
                spec = result.spec
                text = "Here's what I'll make. Check the details, then press Generate, or tell me what to change."
                card = schemas.Card(kind="spec", text="Here's what I'll make", spec=spec, notes=notes)
                cards.append(card)
                yield schemas.ChatEvent(type="card", card=card)
            else:
                text = (result.question or "Tell me a little more about the music you want: "
                        "a style, a mood or an instrument is enough to start.")
                if notes:
                    text = " ".join(notes) + "\n\n" + text
                yield _delta(text)

        else:  # chat, clarify
            if notes:  # e.g. an artist's name was replaced by a description: say so (spec 10.7)
                text = " ".join(notes) + "\n\n"
                yield _delta(text)
            for piece in ai.chat_reply_stream(clean.text, history=turn.history, prefs=turn.prefs,
                                              context=turn.context_text):
                if piece:
                    text += piece
                    yield _delta(piece)
            if not text.strip():
                text = "I don't have an answer for that yet. Could you ask it another way?"
                yield _delta(text)

    message = add_message(ctx, user.id, turn.thread_id, "assistant", text, cards=cards, spec=spec,
                          meta={"intent": intent, "notes": notes})
    yield schemas.ChatEvent(type="message", message=message)
    yield schemas.ChatEvent(type="done")
