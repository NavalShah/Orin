"""Notation Studio conversions (spec 9.1, 11.3): input -> IR -> target notation + loss report.

Thin on purpose. The pipeline, the conversion engine and the renderers live in ``orin/``
and are imported inside the job, so the server starts even while one of them is missing.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select

from .. import schemas
from ..db import AssetRow, ConversionRow, UserRow, dumps, loads, new_id, now_iso, owned
from ..deps import AppCtx
from ..errors import ApiError, body_for_exception, error_body, log_exception, not_found
from ..jobs import JobContext
from . import assets as asset_service
from . import formats as format_service

MAX_NOTES_CHARS = 200_000
MAX_CONTOUR_POINTS = 2000
LOW_CONFIDENCE = 0.5
STYLE_MARGIN = 0.1  # top two styles closer than this: ask the user (spec 8.2)

# RerunRequest fields by what they affect. A change on the analysis side re-runs the
# pipeline (which reuses its cached stages); a change on the target side only re-converts.
ANALYSIS_OPTIONS = ("tonic_hz", "mode_id", "sensitivity", "cycle_template_id", "tempo_bpm")
TARGET_OPTIONS = ("target_mode_id", "target_template_id", "force_measured")
PIPELINE_OPTIONS = ANALYSIS_OPTIONS + ("voice", "separate", "melody_stem")


# --- requests -------------------------------------------------------------------

def _check_formats(input_format: str, output_format: str) -> tuple[str, str]:
    source = "auto" if (input_format or "auto").strip().lower() == "auto" else format_service.canonical(input_format)
    return source, format_service.canonical(output_format)


def start(ctx: AppCtx, user_id: str, req: schemas.ConvertRequest) -> schemas.JobRef:
    has_notes = bool(req.notes_text and req.notes_text.strip())
    if bool(req.asset_id) == has_notes:
        raise ApiError(422, "validation_error", "Give either a file or typed notes: exactly one of the two.",
                       hint="Send 'asset_id' for a file, or 'notes_text' for typed notes.")
    input_format, output_format = _check_formats(req.input_format, req.output_format)

    filename: Optional[str] = None
    if req.asset_id:
        asset = asset_service.get_row(ctx, user_id, req.asset_id)
        if asset.kind not in ("audio", "midi", "notation", "stem"):
            raise ApiError(400, "unsupported_input", "That file is not audio or notes, so it cannot be converted.")
        filename = asset.filename
    elif len(req.notes_text or "") > MAX_NOTES_CHARS:
        raise ApiError(413, "too_large", "Those notes are too long to convert in one go.", hint="Split them into shorter pieces.")

    params = {
        "input_format": input_format, "output_format": output_format, "notes_syntax": req.notes_syntax,
        "options": req.options.model_dump(exclude_none=True),
    }
    conversion_id = new_id()
    with ctx.db.session() as s:
        s.add(ConversionRow(
            id=conversion_id, user_id=user_id, asset_id=req.asset_id or None,
            notes_text=req.notes_text if has_notes else None, filename=filename,
            input_format=input_format, output_format=output_format, params_json=dumps(params),
            status="running", created_at=now_iso(),
        ))
    return _submit(ctx, user_id, conversion_id, reanalyse=True, previous=None, rerun=False)


def rerun(ctx: AppCtx, user_id: str, conversion_id: str, req: schemas.RerunRequest) -> schemas.JobRef:
    """Merge the changed options into the stored ones and run again (spec 11.3 step 7).
    A field that is sent as null clears that override; a field that is not sent is kept."""
    change = req.model_dump(exclude_unset=True)
    with ctx.db.session() as s:
        row = owned(s, ConversionRow, user_id, conversion_id)
        if row is None:
            raise not_found("That conversion")
        if row.status == "running":
            raise ApiError(409, "busy", "This conversion is still running.", hint="Wait for it to finish, then change it.", retryable=True)
        previous = {"status": row.status, "params_json": row.params_json, "input_format": row.input_format,
                    "output_format": row.output_format}
        params = loads(row.params_json, {})
        options = dict(params.get("options") or {})
        input_format, output_format = _check_formats(
            change.get("input_format") or params.get("input_format", "auto"),
            change.get("output_format") or params.get("output_format", row.output_format),
        )
        reanalyse = input_format != params.get("input_format")
        for key in ANALYSIS_OPTIONS + TARGET_OPTIONS:
            if key not in change:
                continue
            before = options.get(key)
            if change[key] is None:
                options.pop(key, None)
            else:
                options[key] = change[key]
            if key in ANALYSIS_OPTIONS and options.get(key) != before:
                reanalyse = True
        params.update(input_format=input_format, output_format=output_format, options=options)
        row.params_json = dumps(params)
        row.input_format, row.output_format = input_format, output_format
        row.status = "running"
        row.error_json = None
    # A first run that failed has no IR to reuse, so it starts from the top.
    return _submit(ctx, user_id, conversion_id, reanalyse=reanalyse, previous=previous, rerun=True)


def _submit(ctx: AppCtx, user_id: str, conversion_id: str, *, reanalyse: bool, previous: Optional[dict[str, Any]],
            rerun: bool) -> schemas.JobRef:
    def restore_or_fail(error: dict[str, Any], problems: Optional[list[dict[str, Any]]] = None) -> None:
        with ctx.db.session() as s:
            row = owned(s, ConversionRow, user_id, conversion_id)
            if row is None or row.status != "running":
                return
            if previous is not None and previous["status"] != "failed":
                # A failed tweak leaves the last good result in place; the job carries the error.
                row.status = previous["status"]
                row.params_json = previous["params_json"]
                row.input_format, row.output_format = previous["input_format"], previous["output_format"]
            else:
                row.status = "failed"
                row.error_json = dumps(error)
                row.problems_json = dumps(problems or [])

    def run(job: JobContext) -> dict[str, str]:
        from orin.errors import NotesParseError

        try:
            _run(ctx, job, user_id, conversion_id, reanalyse=reanalyse, rerun=rerun)
        except NotesParseError as exc:
            restore_or_fail(body_for_exception(exc), [p.model_dump() for p in exc.problems])
            raise
        except ImportError as exc:
            log_exception(f"conversion {conversion_id}", exc)
            error = ApiError(503, "not_available", "This part of Orin is not installed on the server yet.",
                             hint="Ask whoever runs the server to finish the installation.")
            restore_or_fail(body_for_exception(error))
            raise error from None
        except Exception as exc:
            restore_or_fail(body_for_exception(exc))
            raise
        return {"kind": "conversion", "id": conversion_id}

    def on_cancel() -> None:
        restore_or_fail(error_body("cancelled", "This conversion was cancelled.", "Run it again when you are ready."))

    job_id = ctx.jobs.submit(user_id, "convert", run, message="Waiting to start", on_cancel=on_cancel)
    return schemas.JobRef(job_id=job_id, conversion_id=conversion_id)


# --- the job --------------------------------------------------------------------

def _analysis_options(ctx: AppCtx, params: dict[str, Any]):
    from orin.options import AnalysisOptions

    options = params.get("options") or {}
    chosen = {k: options[k] for k in PIPELINE_OPTIONS if options.get(k) is not None}
    return AnalysisOptions(style=params.get("input_format", "auto"), max_upload_mb=ctx.settings.max_upload_mb,
                           max_seconds=ctx.settings.max_audio_seconds, **chosen)


def _stage_cache(ctx: AppCtx) -> Any:
    """The pipeline's stage cache, kept under the data dir so re-runs reuse pitch tracks."""
    cache = ctx.memo.get("stage_cache")
    if cache is None:
        directory = ctx.settings.data_dir / "cache"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            from orin.cache import StageCache

            cache = StageCache(directory)
        except ImportError:
            cache = directory  # the pipeline also accepts a directory
        ctx.memo["stage_cache"] = cache
    return cache


def _analyse(ctx: AppCtx, job: JobContext, params: dict[str, Any], asset: Optional[AssetRow],
             notes_text: Optional[str], rerun: bool):
    from orin import pipeline

    options = _analysis_options(ctx, params)

    def progress(stage: str, fraction: float = 0.0, message: str = "") -> None:
        job.progress(stage, 0.05 + 0.8 * min(1.0, max(0.0, float(fraction or 0.0))), message)

    if asset is not None and asset.kind in ("audio", "stem"):
        if not ctx.storage.exists(asset.sha256):
            raise ApiError(404, "not_found", "The audio for this conversion is no longer stored.", hint="Upload it again.")
        path = str(ctx.storage.path(asset.sha256))
        analyse = pipeline.reanalyze if rerun else pipeline.audio_to_ir
        ir = analyse(path, options, cache=_stage_cache(ctx), progress=progress)
        # The stored file is named by its hash; show the name the user knows.
        return ir.model_copy(update={"source": ir.source.model_copy(update={"filename": asset.filename})})

    if asset is not None:
        raw = Path(ctx.storage.path(asset.sha256)).read_bytes()
        subtype = asset_service.public_meta(asset).get("subtype")
        data: Any = raw if asset.kind == "midi" or subtype == "mxl" else raw.decode("utf-8-sig", errors="replace")
        filename = asset.filename
    else:
        data, filename = notes_text or "", None
    job.progress("parse", 0.3, "Reading the notes")
    return pipeline.notes_to_ir(
        data,
        format_id=None if params.get("input_format", "auto") == "auto" else params["input_format"],
        syntax=None if params.get("notes_syntax", "auto") == "auto" else params["notes_syntax"],
        filename=filename, options=options,
    )


def ir_summary(ir) -> schemas.IRSummary:
    step = max(1, math.ceil(len(ir.contour) / MAX_CONTOUR_POINTS))
    points = ir.contour[::step]
    return schemas.IRSummary(
        source=ir.source, tonic=ir.tonic, mode=ir.mode, tempo=ir.tempo, cycle=ir.cycle,
        analysis=ir.meta.analysis, note_count=len(ir.events), duration_s=round(ir.duration_s, 3),
        events=[
            schemas.EventSummary(
                id=e.id, onset_s=round(e.onset_s, 4), offset_s=round(e.offset_s, 4), pitch_cents=round(e.pitch_cents, 1),
                ornament_kind=e.ornament.kind, ornament_amplitude_cents=round(e.ornament.amplitude_cents, 1),
                confidence=round(e.confidence, 3),
            )
            for e in ir.events
        ],
        contour=schemas.ContourOut(
            t_s=[round(p.t_s, 3) for p in points],
            cents=[None if p.cents is None else round(p.cents, 1) for p in points],
        ),
        warnings=list(ir.meta.warnings),
        low_confidence_event_ids=[e.id for e in ir.events if e.confidence < LOW_CONFIDENCE],
        processing_log=list(ir.meta.processing_log),
    )


def _unsure(ir, candidates) -> tuple[bool, Optional[str]]:
    """(ask the user?, why). The pipeline flags an unsure detection with a
    ``style_uncertain`` warning; for an IR without one, its rule is applied here (spec 8.2:
    low top score, a small margin, or a clip too short to judge)."""
    flagged = next((w for w in ir.meta.warnings if w.code == "style_uncertain"), None)
    if flagged is not None:
        return True, flagged.message
    try:
        from orin.detect.score import decide

        return decide(candidates, ir.source.duration_s or ir.duration_s)
    except ImportError:
        top = candidates[0].score
        margin = top - candidates[1].score if len(candidates) > 1 else top
        return (top < LOW_CONFIDENCE or margin < STYLE_MARGIN), None


def detection(ir, input_format: str) -> tuple[list[dict[str, Any]], bool, Optional[str]]:
    """(top three candidates, needs_style_choice, message). Never a bare single label (spec 8.2)."""
    candidates = list(ir.source.detected_candidates[:3])
    dumped = [c.model_dump() for c in candidates]
    if input_format != "auto" or not candidates:
        return dumped, False, None
    listing = ", ".join(f"{format_service.display_name(c.format_id)} ({round(c.score * 100)}%)" for c in candidates)
    ask, why = _unsure(ir, candidates)
    confidence = ir.source.detected_format_confidence
    if ask or (confidence is not None and confidence < LOW_CONFIDENCE):
        used = format_service.display_name(ir.meta.analysis.style or ir.source.detected_format or candidates[0].format_id)
        return dumped, True, (f"{why or 'Not sure - please choose.'} Closest matches: {listing}. "
                              f"This result was read as {used}; choose a style to run it again.")
    return dumped, False, f"Detected: {listing}."


def _store_outputs(ctx: AppCtx, user_id: str, conversion_id: str, conversion, base: str) -> list[dict[str, Any]]:
    """Binary and text renders become Library assets; SVG pages and text also stay inline."""
    outputs = []
    for kind, r in conversion.renders.items():
        out: dict[str, Any] = {
            "kind": r.kind, "label": r.label, "media_type": r.media_type, "ext": r.ext, "text": r.text,
            "pages": list(r.pages), "tokens": [t.model_dump() for t in r.tokens], "note_ids": list(r.note_ids),
            "warnings": list(r.warnings), "asset_id": None,
        }
        data = r.data if r.data is not None else (r.text.encode("utf-8") if r.text is not None else None)
        if data:
            # Several text kinds share ".txt", so the kind goes in the name.
            name = f"{base}-{conversion.target_format}" + (f"-{kind}" if r.ext == "txt" else "") + f".{r.ext}"
            asset = asset_service.store_bytes(
                ctx, user_id, data, kind="midi" if r.kind == "midi" else "notation", source="conversion",
                filename=name, media_type=r.media_type, meta={"conversion_id": conversion_id, "render": r.kind},
            )
            out["asset_id"] = asset.id
        outputs.append(out)
    return outputs


def _run(ctx: AppCtx, job: JobContext, user_id: str, conversion_id: str, *, reanalyse: bool, rerun: bool) -> None:
    from orin.convert import convert
    from orin.formats.base import ConvertOptions
    from orin.ir import dump_ir, load_ir

    with ctx.db.session() as s:
        row = owned(s, ConversionRow, user_id, conversion_id)
        if row is None:
            raise not_found("That conversion")
        params = loads(row.params_json, {})
        notes_text, asset_id = row.notes_text, row.asset_id
        stored_ir = None if reanalyse else s.execute(
            select(ConversionRow.ir_json).where(ConversionRow.id == conversion_id)).scalar_one_or_none()
        asset = owned(s, AssetRow, user_id, asset_id) if asset_id else None
        user = s.get(UserRow, user_id)
        labels = loads(user.prefs_json, {}).get("labels", "tradition") if user is not None else "tradition"
    if asset_id and asset is None:
        raise ApiError(404, "not_found", "The file for this conversion was deleted.", hint="Upload it again.")

    job.progress("ingest", 0.02, "Reading the input")
    ir = load_ir(stored_ir) if stored_ir else _analyse(ctx, job, params, asset, notes_text, rerun)
    job.check()

    job.progress("notation", 0.88, "Writing the notation")
    options = params.get("options") or {}
    input_format = params.get("input_format", "auto")
    conversion = convert(ir, params["output_format"], ConvertOptions(
        mode_id=options.get("target_mode_id"), template_id=options.get("target_template_id"),
        force_measured=bool(options.get("force_measured", False)), explicit_style=input_format != "auto",
    ), render_opts={"labels": labels})  # the reader's note-name preference (spec 13.5)
    job.check()

    job.progress("notation", 0.95, "Saving the results")
    base = Path(asset.filename).stem if asset is not None else "notes"
    outputs = _store_outputs(ctx, user_id, conversion_id, conversion, base or "notes")
    detected, needs_choice, detect_message = detection(ir, input_format)
    result = {
        "ir": ir_summary(ir).model_dump(mode="json"),
        "score": conversion.score.model_dump(mode="json"),
        "source_format": conversion.source_format,
        "same_format": conversion.same_format,
        "detected": detected, "needs_style_choice": needs_choice, "detect_message": detect_message,
        "missing": [m.model_dump() for m in conversion.missing],
        "suggestions": [m.model_dump() for m in conversion.suggestions],
    }
    with ctx.db.session() as s:
        row = owned(s, ConversionRow, user_id, conversion_id)
        if row is None:
            return  # deleted while running
        row.ir_json = dump_ir(ir)
        row.loss_json = conversion.loss.model_dump_json()
        row.outputs_json = dumps(outputs)
        row.result_json = dumps(result)
        row.detected_format = conversion.source_format
        row.fidelity = conversion.loss.summary.fidelity_score
        row.note_count = len(ir.events)
        row.problems_json = "[]"
        row.error_json = None
        # Low detection confidence: the result is still shown, computed with the top
        # candidate, and the UI asks the user to confirm or pick a style (spec 11.3 step 4).
        row.status = "awaiting_style" if needs_choice else "done"


# --- reading --------------------------------------------------------------------

def get_out(ctx: AppCtx, user_id: str, conversion_id: str) -> schemas.ConversionOut:
    from orin.loss import HOW_SCORED

    with ctx.db.session() as s:
        row = owned(s, ConversionRow, user_id, conversion_id)
    if row is None:
        raise not_found("That conversion")
    result = loads(row.result_json, {})
    outputs = []
    for out in loads(row.outputs_json, []):
        asset_id = out.get("asset_id")
        live = asset_service.find_out(ctx, user_id, asset_id)  # links are re-signed on every read
        outputs.append(schemas.OutputFile(**{**out, "asset_id": live.id if live else None, "url": live.url if live else None}))
    return schemas.ConversionOut(
        id=row.id, status=row.status, created_at=row.created_at,  # type: ignore[arg-type]
        input_format=row.input_format, source_format=row.detected_format or result.get("source_format"),
        detected=result.get("detected", []), needs_style_choice=bool(result.get("needs_style_choice", False)),
        detect_message=result.get("detect_message"), output_format=row.output_format,
        same_format=bool(result.get("same_format", False)),
        source_asset=asset_service.find_out(ctx, user_id, row.asset_id), notes_text=row.notes_text,
        ir=result.get("ir"), score=result.get("score"), loss=loads(row.loss_json),
        outputs=outputs, missing=result.get("missing", []), suggestions=result.get("suggestions", []),
        how_scored=HOW_SCORED, params=loads(row.params_json, {}),
        parse_problems=loads(row.problems_json, []), error=loads(row.error_json),
    )


def list_summaries(ctx: AppCtx, user_id: str) -> list[schemas.ConversionSummary]:
    c = ConversionRow
    with ctx.db.session() as s:  # only the small columns: the stored results can be megabytes
        rows = s.execute(
            select(c.id, c.created_at, c.status, c.input_format, c.detected_format, c.output_format, c.filename,
                   c.asset_id, c.fidelity, c.note_count)
            .where(c.user_id == user_id).order_by(c.created_at.desc()).limit(200)
        ).all()
    return [
        schemas.ConversionSummary(
            id=r.id, created_at=r.created_at, status=r.status, input_format=r.input_format,
            source_format=r.detected_format, output_format=r.output_format,
            filename=r.filename or (None if r.asset_id else "Typed notes"),
            fidelity=r.fidelity, note_count=r.note_count,
        )
        for r in rows
    ]


def ir_json(ctx: AppCtx, user_id: str, conversion_id: str) -> str:
    with ctx.db.session() as s:
        found = s.execute(
            select(ConversionRow.id, ConversionRow.ir_json)
            .where(ConversionRow.id == conversion_id, ConversionRow.user_id == user_id)
        ).first()
    if found is None:
        raise not_found("That conversion")
    if not found[1]:
        raise ApiError(404, "not_ready", "This conversion has no analysis to download yet.",
                       hint="Wait for it to finish, or run it again.")
    return found[1]


def loss_report(ctx: AppCtx, user_id: str, conversion_id: str):
    """The stored LossReport of the user's conversion (for explain and chat context)."""
    from orin.loss import LossReport

    with ctx.db.session() as s:
        row = owned(s, ConversionRow, user_id, conversion_id)
    if row is None:
        raise not_found("That conversion")
    if not row.loss_json:
        raise ApiError(409, "not_ready", "This conversion has no loss report yet.", hint="Wait for it to finish.", retryable=True)
    return LossReport.model_validate_json(row.loss_json)


def fail_interrupted(ctx: AppCtx) -> None:
    """Conversions left 'running' by a previous process (their job was failed at startup)."""
    from sqlalchemy import update

    error = dumps(error_body("interrupted", "The server restarted before this conversion finished.", "Run it again.", True))
    with ctx.db.session() as s:
        s.execute(update(ConversionRow).where(ConversionRow.status == "running").values(status="failed", error_json=error))
