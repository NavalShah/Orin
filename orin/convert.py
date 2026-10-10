"""IR -> target notation (spec 9.1): adapter, then renderers, with the loss report."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from .errors import RendererUnavailable
from .formats import registry
from .formats.base import ConvertOptions, FormatMeta, FormatScore
from .ir.models import IR, Model, ModeCandidate
from .loss.models import LossReport
from .loss.texts import human_text
from .render import RenderResult


class MissingRenderer(Model):
    kind: str
    message: str
    alternative: Optional[str] = None


class Conversion(Model):
    source_format: Optional[str] = None
    target_format: str
    same_format: bool = False  # "clean-up / quantize" mode (spec 11.3)
    score: FormatScore
    loss: LossReport
    renders: dict[str, RenderResult]
    missing: tuple[MissingRenderer, ...] = ()
    suggestions: tuple[ModeCandidate, ...] = ()


def source_format_of(ir: IR) -> Optional[str]:
    return ir.source.format_hint or ir.source.detected_format or ir.meta.analysis.style


# Sentences that name the written note. The engine names it relative to the tonic
# ("b3"); on a staff the reader wants the note as it is written ("E flat").
_NAMES_A_NOTE = ("MICROTONE_ROUNDED", "PITCH_ANCHOR_UNCERTAIN")


def _with_written_names(loss: LossReport, score: FormatScore, meta: FormatMeta) -> LossReport:
    if meta.naming != "absolute":
        return loss
    try:
        from .render.spell import spell_note
    except ImportError:
        return loss
    names: dict[str, str] = {}
    for note in score.notes:
        spelling = spell_note(score, meta, note)
        names[note.id] = f"{spelling.step} {spelling.words}".strip()

    def renamed(entry):
        note_id = entry.detail.get("note_id")
        if entry.code not in _NAMES_A_NOTE or note_id not in names:
            return entry
        detail = {**entry.detail, "written": names[note_id], "written_relative": entry.detail.get("written")}
        return entry.model_copy(update={"detail": detail,
                                        "human_text": human_text(entry.code, target=meta.display_name, **detail)})

    return loss.model_copy(update={"items": tuple(renamed(i) for i in loss.items),
                                   "notices": tuple(renamed(n) for n in loss.notices)})


def convert(ir: IR, target_format: str, opts: Optional[ConvertOptions] = None,
            kinds: Optional[Sequence[str]] = None, render_opts: Optional[dict[str, Any]] = None) -> Conversion:
    """Convert an IR to ``target_format`` and render it.

    ``kinds`` defaults to every renderer the format lists. A renderer that is missing or
    fails is reported in ``missing`` with the closest alternative; it never aborts the rest.
    ``render_opts`` go to every renderer; each uses what it knows (for example ``labels``,
    the reader's preferred note names) and ignores the rest.
    """
    adapter = registry.get(target_format)
    meta = adapter.META
    result = adapter.ir_to_format(ir, opts)

    renders: dict[str, RenderResult] = {}
    missing: list[MissingRenderer] = []
    for kind in (kinds if kinds is not None else meta.renderers):
        try:
            renders[kind] = adapter.render(result.score, kind, **(render_opts or {}))
        except RendererUnavailable as exc:
            missing.append(MissingRenderer(kind=kind, message=exc.message, alternative=exc.alternative))
        except Exception as exc:  # a renderer bug must not lose the other outputs
            missing.append(MissingRenderer(kind=kind, message=f"The '{kind}' renderer failed: {exc}", alternative=None))

    source = source_format_of(ir)
    return Conversion(
        source_format=source,
        target_format=meta.id,
        same_format=source == meta.id,
        score=result.score,
        loss=_with_written_names(result.loss, result.score, meta),
        renders=renders,
        missing=tuple(missing),
        suggestions=result.suggestions,
    )
