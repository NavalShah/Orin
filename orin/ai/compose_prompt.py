"""MusicSpec -> the text sent to the music generator (spec 10.3 step 7).

``spec_to_prompt`` builds one descriptive prompt. ``plan_v1`` and ``plan_v2`` build a
composition plan, which is how a mid-track genre change is expressed; the two model
families take different shapes and each rejects the other's.

Wording rules: a registry genre is always "inspired by", never promised as the real
thing, and the tonic and mode are passed as hints.
"""
from __future__ import annotations

import math
import re
from typing import Any, Optional

from . import offline
from .schema import MusicSpec, SpecSection

MIN_LENGTH_MS = 3_000  # provider minimum for a track and for a plan section
MAX_LENGTH_MS = 300_000  # the provider allows more; Orin stays at five minutes
MAX_SECTION_MS = 120_000  # music_v1 limit for one plan section
_MAX_STYLE_CHARS = 120


def style_label(genre_id: str) -> str:
    """A short genre name fit for a prompt: "Carnatic", "Western classical", "jazz"."""
    meta = offline._meta_for(genre_id)
    if meta is None:
        return (genre_id or "").strip()
    name = offline.genre_name(meta.id)
    id_words = meta.id.replace("_", " ")
    if id_words != name.lower() and id_words.startswith(name.lower()):
        return id_words.capitalize()  # "Western" alone would mean something else
    return name


def genre_phrase(genre_id: str) -> str:
    """How a genre is described to the generator. Registry genres are "inspired by"."""
    meta = offline._meta_for(genre_id)
    if meta is None:
        word = (genre_id or "").strip()
        return "a fusion of styles" if word.lower() == "fusion" else word
    # The registry description ends with how the tradition is notated; that part is no use here.
    about = re.split(r",?\s+written\s+(?:as|on)\b", meta.description)[0].strip().rstrip(".")
    return f"inspired by {style_label(genre_id)} music ({about})"


def _vocals_phrase(spec: MusicSpec) -> str:
    return {"none": "instrumental, no vocals", "wordless": "wordless vocals, no lyrics",
            "lyrics": "vocals with original lyrics"}[spec.vocals]


def _hints(spec: MusicSpec) -> list[str]:
    out: list[str] = []
    if spec.key_or_tonic:
        out.append(f"tonal centre hint: {spec.key_or_tonic}")
    if spec.mode_hint:
        out.append(f"mode hint: {spec.mode_hint} (a loose guide)")
    return out


def _clip(text: str, limit: int = _MAX_STYLE_CHARS) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]  # str.capitalize would lower-case "D" and "Mohanam"


def length_ms(seconds: float) -> int:
    return int(max(MIN_LENGTH_MS, min(MAX_LENGTH_MS, round(float(seconds) * 1000))))


def spec_to_prompt(spec: MusicSpec) -> str:
    seconds = int(round(length_ms(spec.duration_s) / 1000))
    sections = list(spec.sections)
    lines: list[str] = []
    if len(sections) > 1:
        lines.append(f"A {seconds}-second piece of music in {len(sections)} parts.")
        for i, section in enumerate(sections, start=1):
            lines.append(f"Part {i} (about {section.duration_s} seconds), {genre_phrase(section.genre_id)}: "
                         f"{_clip(section.description, 200)}.")
    else:
        genre = sections[0].genre_id if sections else spec.genre_id
        phrase = genre_phrase(genre)
        if offline._meta_for(genre) is not None:
            lines.append(f"A {seconds}-second piece of music {phrase}.")
        else:
            lines.append(f"A {seconds}-second piece of music: {phrase}.")
        description = sections[0].description if sections else ""
        if description and description.strip().lower() not in (genre.lower(), style_label(genre).lower()):
            lines.append(_cap(_clip(description, 300)).rstrip(".") + ".")
    if spec.mood:
        lines.append("Mood: " + ", ".join(spec.mood) + ".")
    if spec.tempo_bpm:
        lines.append(f"Tempo: about {spec.tempo_bpm} BPM.")
    if spec.instruments:
        lines.append("Instruments: " + ", ".join(spec.instruments) + ".")
    lines.append(_cap(_vocals_phrase(spec)) + ".")
    for hint in _hints(spec):
        lines.append(_cap(hint) + ".")
    if spec.structure_notes:
        lines.append("Structure: " + _clip(spec.structure_notes, 300).rstrip(".") + ".")
    if spec.avoid:
        lines.append("Avoid: " + ", ".join(spec.avoid) + ".")
    return " ".join(lines)


def global_styles(spec: MusicSpec) -> list[str]:
    out = list(spec.mood)
    if spec.tempo_bpm:
        out.append(f"about {spec.tempo_bpm} bpm")
    out += list(spec.instruments)
    out.append(_vocals_phrase(spec))
    out += _hints(spec)
    if spec.structure_notes:
        out.append(_clip(spec.structure_notes))
    return [_clip(s) for s in offline._unique(out)]


def negative_styles(spec: MusicSpec) -> list[str]:
    out = list(spec.avoid)
    if spec.vocals == "none":
        out.append("vocals")
    return [_clip(s) for s in offline._unique(out)]


def plan_parts(spec: MusicSpec) -> list[tuple[str, SpecSection, int]]:
    """(name, section, duration_ms) per plan section. Long sections are split, and the
    total is scaled down to the five-minute limit."""
    sections = list(spec.sections) or [SpecSection(genre_id=spec.genre_id, description=spec.genre_id,
                                                   duration_s=spec.duration_s)]
    total = sum(max(s.duration_s, 1) for s in sections) * 1000
    scale = min(1.0, MAX_LENGTH_MS / total) if total else 1.0
    parts: list[tuple[str, SpecSection, int]] = []
    for i, section in enumerate(sections, start=1):
        ms = max(MIN_LENGTH_MS, int(round(max(section.duration_s, 1) * 1000 * scale)))
        pieces = max(1, math.ceil(ms / MAX_SECTION_MS))
        name = _clip(f"Part {i}: {style_label(section.genre_id)}", 60)
        for k in range(pieces):
            piece_name = name if pieces == 1 else f"{name} ({k + 1} of {pieces})"
            parts.append((piece_name, section, max(MIN_LENGTH_MS, ms // pieces)))
    return parts


def plan_v1(spec: MusicSpec) -> dict[str, Any]:
    """Composition plan for ``music_v1``, which is also the provider default when no model id is sent."""
    return {
        "positive_global_styles": global_styles(spec),
        "negative_global_styles": negative_styles(spec),
        "sections": [
            {
                "section_name": name,
                "positive_local_styles": offline._unique([_clip(genre_phrase(section.genre_id)), _clip(section.description)]),
                "negative_local_styles": [],
                "duration_ms": ms,
                "lines": [],
            }
            for name, section, ms in plan_parts(spec)
        ],
    }


def plan_v2(spec: MusicSpec) -> dict[str, Any]:
    """Composition plan for the ``music_v2*`` family: chunks of bracketed text with styles."""
    styles = global_styles(spec)
    negative = negative_styles(spec)
    return {
        "chunks": [
            {
                "text": f"[{name}] {_clip(section.description, 300).rstrip('.')}. Music {genre_phrase(section.genre_id)}."
                if offline._meta_for(section.genre_id) is not None
                else f"[{name}] {_clip(section.description, 300).rstrip('.')}. Style: {genre_phrase(section.genre_id)}.",
                "duration_ms": ms,
                "positive_styles": offline._unique([_clip(style_label(section.genre_id)), *styles]),
                "negative_styles": list(negative),
            }
            for name, section, ms in plan_parts(spec)
        ],
    }


def plan_length_ms(plan: dict[str, Any]) -> Optional[int]:
    parts = plan.get("sections") or plan.get("chunks") or []
    try:
        return int(sum(int(p["duration_ms"]) for p in parts)) or None
    except (KeyError, TypeError, ValueError):
        return None
