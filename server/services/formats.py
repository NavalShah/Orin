"""The registry as the UI needs it (spec 4.5), plus the offline status of the AI layer."""
from __future__ import annotations

from typing import Optional

from .. import schemas
from ..deps import AppCtx
from ..errors import redact

MONOPHONIC_NOTE = (
    "Orin follows one melody line at a time. If several instruments or voices sound together, "
    "it picks the lead line (the voice, or the lead instrument) and ignores chords and harmony."
)
SARGAM_HELP_FALLBACK = (
    "Type swaras separated by spaces: S R1 R2 G1 G2 G3 M1 M2 P D1 D2 D3 N1 N2 N3. "
    "The full grammar help is not available on this server yet."
)
DETECT_LIMITS_FALLBACK = [
    "Auto-detect is a helper, not an oracle: a short single melody line can fit several styles.",
    "Neighbouring styles, such as pop and rock, can be impossible to tell apart from the melody alone.",
    "Fusion and crossover music may match several styles at once.",
]


def offline_status(ctx: AppCtx) -> schemas.OfflineStatus:
    """True = that service runs on its stand-in. If the AI layer is missing, everything is."""
    try:
        from orin.ai import service

        st = service.status()
        return schemas.OfflineStatus(llm=st.llm_offline, music=st.music_offline, tts=st.tts_offline,
                                     reason=redact(st.reason) if st.reason else None)
    except Exception:
        return schemas.OfflineStatus(llm=True, music=True, tts=True,
                                     reason="The AI layer is not available on this server, so stand-ins are used.")


def max_generate_seconds() -> int:
    from orin.ai.schema import MAX_DURATION_S

    return int(MAX_DURATION_S)


def limits(ctx: AppCtx) -> schemas.Limits:
    return schemas.Limits(
        max_upload_mb=ctx.settings.max_upload_mb, max_audio_seconds=ctx.settings.max_audio_seconds,
        max_generate_seconds=max_generate_seconds(), daily_generations=ctx.settings.daily_generations,
    )


def canonical(format_id: str) -> str:
    """The registry id for an id or alias. Raises UnknownFormat (a 400) with suggestions."""
    from orin.formats import registry

    return registry.get(format_id).META.id


def display_name(format_id: Optional[str]) -> str:
    from orin.formats import registry

    if format_id and registry.has(format_id):
        return registry.get(format_id).META.display_name
    return format_id or "unknown"


def _format_info(meta) -> schemas.FormatInfo:
    from orin.formats import registry
    from orin.formats.base import FAMILY_LABELS

    modes = [
        schemas.ModeInfo(
            id=m.id, label=m.label, kind=m.kind,
            pitches_cents=[d.cents for d in m.ascending], degree_names=[d.name for d in m.ascending],
            source=m.source, confidence=m.confidence, caveats=list(m.caveats),
            lookalikes=list(registry.lookalikes(meta.id, m.id)),
        )
        for m in meta.mode_table
    ]
    templates = [
        schemas.TemplateInfo(
            id=t.id, label=t.label, cycle_beats=t.cycle_beats, subdivision=t.subdivision,
            structure=list(t.structure), accent_pattern=list(t.accent_pattern),
            source=t.source, confidence=t.confidence, caveats=list(t.caveats),
        )
        for t in meta.rhythm_templates
    ]
    return schemas.FormatInfo(
        id=meta.id, display_name=meta.display_name, description=meta.description, aliases=list(meta.aliases),
        family=meta.family, family_label=FAMILY_LABELS.get(meta.family, meta.family),
        pitch_system=meta.pitch_system, tonic_model=meta.tonic_model, rhythm_model=meta.rhythm_model,
        tonic_word=meta.tonic_word, review_status=meta.review_status, reviewer_note=meta.reviewer_note,
        caveats=list(meta.caveats), renderers=list(meta.renderers), primary_renderer=meta.primary_renderer,
        input_parsers=list(meta.input_parsers), monophonic_only=meta.monophonic_only,
        modes=modes, rhythm_templates=templates, default_template_id=meta.default_template_id,
    )


def _help_texts() -> tuple[str, list[str]]:
    try:
        from orin.ingest.notes_sargam import GRAMMAR_HELP

        sargam = str(GRAMMAR_HELP)
    except Exception:
        sargam = SARGAM_HELP_FALLBACK
    try:
        from orin.detect.score import HONEST_LIMITS

        detect = [HONEST_LIMITS] if isinstance(HONEST_LIMITS, str) else [str(x) for x in HONEST_LIMITS]
    except Exception:
        detect = list(DETECT_LIMITS_FALLBACK)
    return sargam, detect


def formats_response(ctx: AppCtx) -> schemas.FormatsResponse:
    from orin.formats import registry
    from orin.render import RENDER_INFO

    static = ctx.memo.get("formats")
    if static is None:  # the registry does not change while the process runs
        static = {
            "formats": [_format_info(meta) for meta in registry.list_meta()],
            "families": [schemas.FamilyInfo(id=f, label=label, format_ids=ids) for f, label, ids in registry.families()],
            "renderers": [schemas.RendererInfo(kind=k, label=label, media_type=mt, ext=ext)
                          for k, (_, label, mt, ext) in RENDER_INFO.items()],
        }
        ctx.memo["formats"] = static
    sargam, detect = _help_texts()
    return schemas.FormatsResponse(
        **static, limits=limits(ctx), offline=offline_status(ctx),
        sargam_help=sargam, detect_limits=detect, monophonic_note=MONOPHONIC_NOTE,
    )
