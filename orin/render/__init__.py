"""Renderers: FormatScore -> notation (spec 9.2).

Each kind lives in its own module exposing ``render(score, meta, **opts) -> RenderResult``.
Modules are imported lazily, so a missing optional package (Verovio) only disables its
own kind. If a kind is unavailable, callers get ``RendererUnavailable`` naming the
closest alternative (spec 11.3).
"""
from __future__ import annotations

import importlib
from typing import Any, Optional

from pydantic import ConfigDict

from ..errors import RendererUnavailable
from ..formats.base import FormatMeta, FormatScore
from ..ir.models import Model

# kind -> (module, label, media type, file extension)
RENDER_INFO: dict[str, tuple[str, str, str, str]] = {
    "svg": ("svg", "Staff", "image/svg+xml", "svg"),
    "sargam": ("sargam", "Sargam text", "text/plain", "txt"),
    "names": ("names_text", "Note names", "text/plain", "txt"),
    "abc": ("abc", "ABC", "text/vnd.abc", "abc"),
    "tab": ("tab", "Guitar tab", "text/plain", "txt"),
    "musicxml": ("musicxml", "MusicXML", "application/vnd.recordare.musicxml+xml", "musicxml"),
    "midi": ("midi", "MIDI", "audio/midi", "mid"),
    "describe": ("describe", "Plain-language description", "text/plain", "txt"),
}

# What to offer when a kind is missing, most similar first.
_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "svg": ("names", "sargam", "abc", "describe"),
    "abc": ("names", "svg", "describe"),
    "sargam": ("names", "describe"),
    "names": ("sargam", "abc", "describe"),
    "tab": ("names", "svg", "describe"),
    "musicxml": ("abc", "midi"),
    "midi": ("musicxml",),
    "describe": ("names", "sargam"),
}


class TokenRef(Model):
    """Where one note sits in a text rendering, so the UI can highlight it."""

    note_id: str
    start: int  # character offsets into RenderResult.text
    end: int


class RenderResult(Model):
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64")

    kind: str
    label: str
    media_type: str
    ext: str
    text: Optional[str] = None  # text kinds, and the MusicXML source
    data: Optional[bytes] = None  # binary kinds (MIDI)
    pages: tuple[str, ...] = ()  # SVG, one string per system/page
    tokens: tuple[TokenRef, ...] = ()
    note_ids: tuple[str, ...] = ()  # SVG: element ids, in note order
    warnings: tuple[str, ...] = ()


def closest_alternative(meta: FormatMeta, kind: str) -> Optional[str]:
    for alt in _ALTERNATIVES.get(kind, ()):
        if alt in meta.renderers:
            return alt
    return next((k for k in meta.renderers if k != kind), None)


def render(score: FormatScore, meta: FormatMeta, kind: str, **opts: Any) -> RenderResult:
    info = RENDER_INFO.get(kind)
    if info is None or kind not in meta.renderers:
        alt = closest_alternative(meta, kind)
        raise RendererUnavailable(kind, f"{meta.display_name} has no '{kind}' output.", alternative=alt)
    module_name = info[0]
    try:
        module = importlib.import_module(f"orin.render.{module_name}")
    except ImportError as exc:
        alt = closest_alternative(meta, kind)
        raise RendererUnavailable(kind, f"The '{kind}' renderer is not installed ({exc.name or exc}).", alternative=alt) from exc
    return module.render(score, meta, **opts)


def result(kind: str, **fields: Any) -> RenderResult:
    """Build a RenderResult with the label, media type and extension filled in."""
    _, label, media_type, ext = RENDER_INFO[kind]
    return RenderResult(kind=kind, label=label, media_type=media_type, ext=ext, **fields)
