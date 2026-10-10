"""Prompt policy (spec 10.7): artist and song names never reach the music generator.

Rule-based and conservative. A name is only removed when it follows a cue such as
"in the style of", or is a quoted title. Ordinary music words (raga and mode names,
tradition names, instruments, genres) are protected and left alone.
"""
from __future__ import annotations

import re
from typing import Iterable

from . import offline
from .types import SanitizeResult

_STYLE = "in a similar general style"
_FEEL = "with a similar general feel"
_TITLE = "an original tune"

_NAME_NOTE = ('Removed the name "{name}": Orin does not pass artist or song names to the music generator. '
              "Describe the sound you want instead (tempo, instruments, mood, era).")
_TITLE_NOTE = ('Removed the title "{name}": Orin does not reproduce existing songs. '
               "The result will be an original tune; describe the feel you want.")

# Words after which a capitalised word is a mode or cycle name, not a person.
_ANCHORS = {"raga", "ragam", "raag", "raaga", "maqam", "makam", "dastgah", "tala", "talam", "taal",
            "avaz", "mela", "melakarta", "thaat", "iqa", "gusheh"}

# Music words: a name containing one of these is left alone ("Persian Santur", "Carnatic Violin").
_MUSIC = {
    "music", "song", "songs", "style", "styles", "piece", "tune", "track", "melody", "alapana", "alap",
    "kriti", "varnam", "taqsim", "bhajan", "sargam", "gamaka", "gamakas", "major", "minor", "pentatonic",
    "scale", "mode", "sharp", "flat", "classical", "baroque", "renaissance", "medieval",
}
# Everyday words: a name made only of these (and music words) is left alone.
_GENERAL = {
    "i", "me", "you", "us", "them", "him", "her", "it", "orin",
    "western", "eastern", "indian", "south", "north", "arabic", "arab", "persian", "iranian", "turkish",
    "african", "irish", "spanish", "chinese", "japanese", "korean", "brazilian", "cuban", "american",
    "european", "british", "french", "italian", "german", "greek", "balinese", "javanese", "middle",
    "traditional", "modern", "contemporary", "early", "old", "before", "last", "previous",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "today",
    "a", "an", "the", "my", "this", "that", "some", "of", "and", "in", "on", "for", "with",
}
_ARTICLES = {"a", "an", "the", "my", "your", "our", "this", "that", "these", "those", "some", "any",
             "old", "early", "late", "modern", "classic", "traditional", "something", "what", "how"}
_STOP = {"with", "and", "but", "for", "that", "which", "on", "at", "then", "please", "so", "in", "using", "to"}

_QUOTE_OPEN = "\"“«"
_QUOTE_CLOSE = "\"”»"
_QUOTED = "[" + _QUOTE_OPEN + "]([^" + _QUOTE_OPEN + _QUOTE_CLOSE + "]{2,60})[" + _QUOTE_CLOSE + "]"
_CAP_WORD = r"[A-Z][\w'’.&-]*"
_NAME = _CAP_WORD + r"(?:\s+(?:(?:of|the|and|de|la|van|von|&)\s+)*[A-Z0-9][\w'’.&-]*){0,5}"

# (cue pattern, replacement for "cue + name", also match a lower-case name)
_CUES: tuple[tuple[str, str, bool], ...] = (
    (r"in\s+the\s+(?:same\s+)?(?:style|vein|manner|spirit)\s+(?:of|as)", _STYLE, True),
    (r"(?:that\s+)?sounds?\s+(?:just\s+|a\s+bit\s+|a\s+lot\s+|exactly\s+|kind\s+of\s+)?like", _FEEL, False),
    (r"similar\s+to", _FEEL, False),
    (r"reminiscent\s+of", _FEEL, False),
    (r"inspired\s+by", _FEEL, False),
    (r"(?:a\s+)?cover\s+of", _TITLE, False),
    (r"like", _FEEL, False),
    (r"by", "", False),
)

_BLOCKLIST: tuple[str, ...] = (
    r"\b(?:csam|child\s*porn\w*|child\s+sexual)\b",
    r"\b(?:glorif\w+|prais\w+|celebrat\w+|promot\w+)\b.{0,40}\b(?:hitler|nazis?|genocide|terroris\w+|mass\s+shooting|school\s+shooting|ethnic\s+cleansing)\b",
    r"\b(?:kill|murder|lynch|exterminate)\s+(?:all|every)\s+(?!(?:the\s+)?(?:lights?|noise|time)\b)(?:the\s+)?\w+",
    r"\bhow\s+to\s+(?:make|build)\s+a\s+(?:bomb|bioweapon)\b",
)
_BLOCK_REASON = "This request looks abusive, so Orin will not pass it on. Try describing the music you want."


def _word_sets() -> tuple[set[str], set[str]]:
    """(music words, everyday words). Registry names are read at call time."""
    music = set(_MUSIC)
    for table in (offline.PLAIN_GENRES, offline.INSTRUMENTS):
        for phrase in table:
            music.update(t for t in re.findall(r"[a-z0-9&]+", phrase) if len(t) >= 3 or t == "r&b")
    for phrase, _ in offline.genre_lexicon():
        music.update(t for t in re.findall(r"[a-z0-9&]+", phrase) if len(t) >= 3)
    for meta in offline._metas():
        names: list[str] = [meta.display_name]
        for mode in meta.mode_table:
            names += [mode.id.replace("_", " "), mode.label, *mode.aliases]
        for template in meta.rhythm_templates:
            names += [template.id.replace("_", " "), template.label]
        for name in names:
            music.update(t for t in re.findall(r"[a-z]+", name.lower()) if len(t) >= 3)
    general = set(_GENERAL)
    for phrase in offline.MOODS:
        general.update(re.findall(r"[a-z]+", phrase))
    return music, general


def _tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9&]+", re.sub(r"['’]s\b", "", name.lower())) if t]


def _is_ordinary(name: str, music: set[str], general: set[str]) -> bool:
    """True when ``name`` reads as ordinary music words, or names a mode ("Raga Yaman")."""
    tokens = _tokens(name)
    if not tokens:
        return True
    if any(t in _ANCHORS or t in music for t in tokens):
        return True
    return all(t in general or t.isdigit() for t in tokens)


def _trim_name(name: str) -> str:
    name = re.sub(r"(?:\s+(?:of|the|and|de|la|van|von|&))+$", "", name.strip())
    return name.rstrip(" .,;:!?")


def _scan(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Returns the rewritten text and the (kind, name) pairs that were removed."""
    music, general = _word_sets()
    removed: list[tuple[str, str]] = []

    def replace(kind: str, name: str, replacement: str, whole: str) -> str:
        name = _trim_name(name)
        if _is_ordinary(name, music, general):
            return whole
        removed.append((kind, name))
        return replacement

    def head(cue: str) -> str:
        # A cue may be capitalised at the start of a sentence; the name stays case-sensitive.
        return r"(?<![\w-])(?i:" + cue + r")\s+"

    out = text
    # 1. cue + quoted title
    for cue, replacement, _lower in _CUES:
        out = re.sub(head(cue) + _QUOTED,
                     lambda m, r=replacement: replace("title", m.group(1), r or _TITLE, m.group(0)), out)

    # 2. any other quoted title that has a capitalised word
    def quoted(m: re.Match) -> str:
        if not re.search(r"[A-Z]", m.group(1)):
            return m.group(0)
        return replace("title", m.group(1), _TITLE, m.group(0))

    out = re.sub(_QUOTED, quoted, out)

    # 3. cue + capitalised name; for pure style cues, also a lower-case name
    for cue, replacement, lower in _CUES:
        out = re.sub(head(cue) + r"(" + _NAME + r")(?:['’]s)?",
                     lambda m, r=replacement: replace("name", m.group(1), r, m.group(0)), out)
        if not lower:
            continue

        def lower_case(m: re.Match, r: str = replacement) -> str:
            words: list[str] = []
            for w in m.group(1).split():
                if w.lower() in _STOP:
                    break
                words.append(w)
            if not words or words[0].lower() in _ARTICLES:
                return m.group(0)
            name = " ".join(words)
            if any(t in music or t in general or t in _ANCHORS for t in _tokens(name)):
                return m.group(0)
            removed.append(("name", name))
            return r + m.group(1)[len(name):]

        out = re.sub(head(cue) + r"((?:[a-z][\w'’-]*)(?:\s+[a-z][\w'’-]*){0,2})", lower_case, out)

    if removed:
        out = re.sub(r"\s{2,}", " ", out)
        out = re.sub(r"\s+([,.;:!?])", r"\1", out).strip()
    return out, removed


def find_names(text: str) -> list[str]:
    """Artist names and song titles the policy would remove from ``text``."""
    return [name for _, name in _scan(text or "")[1]]


def scrub_names(text: str, names: Iterable[str], replacement: str = "a similar artist") -> str:
    """Remove known names from model-written text, in case a model copied one through."""
    out = text or ""
    for name in names:
        if name:
            out = re.sub(re.escape(name), replacement, out, flags=re.I)
    return out


def is_blocked(text: str) -> bool:
    low = (text or "").lower()
    return any(re.search(pattern, low) for pattern in _BLOCKLIST)


def sanitize_prompt(text: str) -> SanitizeResult:
    text = text or ""
    if is_blocked(text):
        return SanitizeResult(text="", notes=[], blocked=True, reason=_BLOCK_REASON)
    out, removed = _scan(text)
    notes: list[str] = []
    for kind, name in removed:
        note = (_TITLE_NOTE if kind == "title" else _NAME_NOTE).format(name=name)
        if note not in notes:
            notes.append(note)
    return SanitizeResult(text=out, notes=notes)
