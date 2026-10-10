"""FormatScore -> staff as SVG, by Verovio over the MusicXML renderer's output.

Verovio keeps each ``<note id>`` as the id of the note's SVG group, so the UI can
highlight a note by its ScoreNote id. A note split by a bar line or into tied values has
further groups with the ids "<id>t2", "<id>t3"...
"""
from __future__ import annotations

import threading
from typing import Any

from ..errors import RendererUnavailable
from ..formats.base import FormatMeta, FormatScore
from . import RenderResult, closest_alternative, result
from . import musicxml

_VEROVIO_LOCK = threading.Lock()
_toolkit: Any = None

OPTIONS: dict[str, Any] = {
    "pageWidth": 1600,
    "adjustPageHeight": True,
    "scale": 40,
    "footer": "none",
    "header": "none",
    "breaks": "auto",
    "svgViewBox": True,
    "pageMarginTop": 30,
    "pageMarginBottom": 30,
    "pageMarginLeft": 30,
    "pageMarginRight": 30,
}


def render(score: FormatScore, meta: FormatMeta, **opts: Any) -> RenderResult:
    source = musicxml.render(score, meta, **opts)
    alternative = closest_alternative(meta, "svg") or "names"
    try:
        import verovio
    except ImportError as exc:
        raise RendererUnavailable("svg", "The staff renderer (Verovio) is not installed.",
                                  alternative=alternative) from exc

    options = dict(OPTIONS)
    for key in ("pageWidth", "scale"):
        if opts.get(key):
            options[key] = opts[key]
    # Verovio keeps its fonts in process-wide state, and a toolkit being created or freed
    # in one thread breaks one in use in another ("font resources are not available").
    # The server renders from a job pool, so one toolkit is made once, never freed, and
    # used under a lock.
    global _toolkit
    with _VEROVIO_LOCK:
        if _toolkit is None:
            verovio.enableLog(verovio.LOG_ERROR)
            _toolkit = verovio.toolkit()
        toolkit = _toolkit
        toolkit.setOptions(options)
        if not toolkit.loadData(source.text or ""):
            message = (toolkit.getLog() or "").strip() or "Verovio could not read the score."
            raise RendererUnavailable("svg", message, alternative=alternative)
        pages = tuple(toolkit.renderToSVG(page) for page in range(1, toolkit.getPageCount() + 1))
    drawn = "".join(pages)
    note_ids = tuple(n.id for n in score.notes if f'id="{n.id}"' in drawn)
    return result("svg", pages=pages, note_ids=note_ids, warnings=source.warnings)
