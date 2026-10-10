"""Offline stand-ins (spec D11), so every AI step runs with no keys.

  intent      keyword rules
  spec        a template MusicSpec built from the words in the request
  chat        a few canned, hedged answers and a plain "I am the stand-in" fallback
  loss        template explanations keyed by explain key, at three learner levels
  music       a short synthesised placeholder clip, labelled as one
  narration   no audio; the browser reads the text

Nothing here calls a network. The loss texts never contain a digit of their own, so
every number a learner reads comes from the report.
"""
from __future__ import annotations

import hashlib
import io
import re
from typing import Any, Iterator, Optional, Sequence

from ..loss import NOTICE_CODES, LossReport, base_key
from .schema import MAX_DURATION_S, MIN_DURATION_S, Intent, MusicSpec, SpecSection
from .types import ExplanationResult, MusicResult, NarrationResult, ProviderMeta, SpecResult, Turn

LEVELS = ("beginner", "intermediate", "advanced")
DEFAULT_DURATION_S = 60
SHORT_DURATION_S = 30
LONG_DURATION_S = 120
STANDIN_MAX_S = 30
STANDIN_SR = 22050
DEFAULT_GENRE = "western_pop"

OFFLINE_SPEC_NOTE = "Built by Orin's offline stand-in from the words in your request; check it before generating."

# ---------------------------------------------------------------------------
# Word lists
# ---------------------------------------------------------------------------

# phrase -> canonical plain genre word. Registry ids and aliases are added at call time.
PLAIN_GENRES: dict[str, str] = {
    "jazz": "jazz", "blues": "blues", "lo-fi": "lo-fi", "lofi": "lo-fi", "lo fi": "lo-fi",
    "hip hop": "hip hop", "hip-hop": "hip hop", "rap": "rap", "edm": "electronic",
    "electronic": "electronic", "electronica": "electronic", "techno": "techno", "house music": "house",
    "ambient": "ambient", "folk": "folk", "country": "country", "reggae": "reggae", "funk": "funk",
    "soul": "soul", "r&b": "r&b", "metal": "metal", "punk": "punk", "hindustani": "hindustani",
    "north indian classical": "hindustani", "indian classical": "indian classical",
    "bollywood": "bollywood", "latin": "latin", "salsa": "salsa", "bossa nova": "bossa nova",
    "disco": "disco", "synthwave": "synthwave", "orchestral": "orchestral", "cinematic": "cinematic",
    "gospel": "gospel", "afrobeat": "afrobeat", "flamenco": "flamenco", "celtic": "celtic",
    "bluegrass": "bluegrass", "indie": "indie", "swing": "swing", "ragtime": "ragtime", "trap": "trap",
    "drum and bass": "drum and bass", "dubstep": "dubstep", "new age": "new age", "fusion": "fusion",
    "waltz": "waltz", "tango": "tango", "samba": "samba", "ska": "ska", "grunge": "grunge",
    "opera": "opera", "baroque": "baroque", "gamelan": "gamelan", "klezmer": "klezmer",
    "qawwali": "qawwali", "ghazal": "ghazal", "chiptune": "chiptune", "lullaby": "lullaby",
}

# Registry aliases that are instruments or notation words, not genres.
_NOT_GENRE = {"tab", "guitar tab", "guitar", "staff", "sheet music", "lead sheet"}

# phrase -> canonical instrument name
INSTRUMENTS: dict[str, str] = {
    "piano": "piano", "keyboard": "keyboard", "electric guitar": "electric guitar",
    "acoustic guitar": "acoustic guitar", "guitar": "guitar", "bass guitar": "bass", "bass": "bass",
    "drums": "drums", "drum kit": "drums", "percussion": "percussion", "violin": "violin",
    "viola": "viola", "cello": "cello", "strings": "strings", "flute": "flute", "bansuri": "bansuri",
    "saxophone": "saxophone", "sax": "saxophone", "trumpet": "trumpet", "trombone": "trombone",
    "clarinet": "clarinet", "synth": "synthesizer", "synths": "synthesizer", "synthesizer": "synthesizer",
    "organ": "organ", "harp": "harp", "ukulele": "ukulele", "banjo": "banjo", "mandolin": "mandolin",
    "tabla": "tabla", "mridangam": "mridangam", "ghatam": "ghatam", "kanjira": "kanjira",
    "veena": "veena", "sitar": "sitar", "sarod": "sarod", "tanpura": "tanpura", "tambura": "tanpura",
    "harmonium": "harmonium", "nadaswaram": "nadaswaram", "oud": "oud", "qanun": "qanun", "ney": "ney",
    "riq": "riq", "darbuka": "darbuka", "santur": "santur", "setar": "setar", "tar": "tar",
    "kamancheh": "kamancheh", "tombak": "tombak", "daf": "daf", "harmonica": "harmonica",
    "accordion": "accordion", "marimba": "marimba", "vibraphone": "vibraphone",
}

MOODS: dict[str, str] = {
    "calm": "calm", "calmer": "calm", "peaceful": "peaceful", "happy": "happy", "happier": "happy",
    "joyful": "joyful", "sad": "sad", "sadder": "sad", "melancholy": "melancholy",
    "melancholic": "melancholy", "energetic": "energetic", "dark": "dark", "darker": "dark",
    "uplifting": "uplifting", "devotional": "devotional", "dreamy": "dreamy", "romantic": "romantic",
    "tense": "tense", "playful": "playful", "epic": "epic", "mellow": "mellow", "chill": "chill",
    "relaxing": "relaxing", "meditative": "meditative", "nostalgic": "nostalgic",
    "mysterious": "mysterious", "dramatic": "dramatic", "gentle": "gentle", "bright": "bright",
    "brighter": "bright", "warm": "warm", "groovy": "groovy", "aggressive": "aggressive",
    "soothing": "soothing", "haunting": "haunting", "hopeful": "hopeful", "festive": "festive",
}

_TEMPO_WORDS: tuple[tuple[str, int], ...] = (
    ("very slow", 50), ("really slow", 50), ("very fast", 170), ("really fast", 170),
    ("slowly", 70), ("slower", 70), ("slow", 70), ("relaxed", 80), ("laid-back", 80), ("laid back", 80),
    ("mid-tempo", 100), ("moderate", 100), ("medium tempo", 100), ("upbeat", 120), ("lively", 120),
    ("up-tempo", 130), ("uptempo", 130), ("faster", 140), ("fast", 140),
)

_NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
}
_UNIT = r"(?P<unit>seconds?|secs?|minutes?|mins?)"
_DUR_RE = re.compile(
    r"\b(?:(?P<num>\d+(?:\.\d+)?)[\s-]*|(?P<word>" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")[\s-]+)"
    + _UNIT + r"\b", re.I)
_DUR_S_RE = re.compile(r"\b(\d+)s(?=\s*(?:$|[,.;!?]|long\b|clip\b|track\b|piece\b))", re.I)
_BPM_RE = re.compile(r"\b(\d{2,3})\s*bpm\b", re.I)

_GEN_VERB = (r"(?:make|create|compose|generate|produce|write|build|play me|give me|sing me|record|improvise|"
             r"can i (?:hear|get|have)|let me hear|i want to hear|i(?:'d| would) like to hear)")
_MUSIC_WORD = (r"(?:music|songs?|tracks?|piece|tune|melody|beat|clip|jam|riff|loop|alapana|composition|"
               r"instrumental|soundtrack|jingle|lullaby|groove|improvisation|solo|theme|bhajan|taqsim|ballad|anthem)")
_NOTATION_WORD = r"(?:notation|notes|sargam|swaras?|sheet music|score|tabs?|tablature|staff)"

_STRONG_SPLIT = re.compile(
    r"\s*(?:,\s*)?(?:\b(?:that|which|and)\s+)?(?:\bthen\s+)?"
    r"\b(?:turn(?:s|ing)?|morph(?:s|ing)?|transition(?:s|ing)?|chang(?:es?|ing)|shift(?:s|ing)?|"
    r"evolv(?:es?|ing)|fad(?:es?|ing)|melt(?:s|ing)?|blend(?:s|ing)?|mov(?:es?|ing)|switch(?:es|ing)?)"
    r"\s+(?:in)?to\b\s*"
    r"|\s*(?:,\s*)?\bfollowed by\b\s*"
    r"|\s*(?:,\s*)?\bbefore becoming\b\s*"
    r"|\s*(?:,\s*)?\b(?:and\s+)?then\b\s*",
    re.I)
_WEAK_SPLIT = re.compile(r"\s+into\s+", re.I)

_LEAD = re.compile(
    r"^\s*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
    r"(?:make|create|compose|generate|produce|write|build|play|give|record|improvise)(?:\s+me)?\s+", re.I)

_NOTE_HZ = {"C": 130.81, "D": 146.83, "E": 164.81, "F": 174.61, "G": 196.00, "A": 220.00, "B": 246.94}


def _metas() -> list[Any]:
    """Registry entries, or nothing if the registry cannot load. The stand-ins must not fail."""
    try:
        from ..formats import registry

        return list(registry.list_meta())
    except Exception:
        return []


def _meta_for(genre_id: Optional[str]) -> Optional[Any]:
    if not genre_id:
        return None
    try:
        from ..formats import registry

        return registry.get(genre_id).META if registry.has(genre_id) else None
    except Exception:
        return None


def genre_name(genre_id: str) -> str:
    """Display name for a registry id (without the bracketed part), else the word itself."""
    meta = _meta_for(genre_id)
    if meta is None:
        return genre_id
    return re.sub(r"\s*\(.*?\)", "", meta.display_name).strip() or meta.display_name


def genre_lexicon() -> list[tuple[str, str]]:
    """(phrase, genre id) pairs, longest phrase first. Registry names win over plain words."""
    entries: dict[str, str] = {}
    for meta in _metas():
        names = {meta.id, meta.id.replace("_", " "), re.sub(r"\s*\(.*?\)", "", meta.display_name)}
        names.update(meta.aliases)
        for name in names:
            name = name.strip().lower()
            if name and name not in _NOT_GENRE and name not in INSTRUMENTS:
                entries.setdefault(name, meta.id)
    for phrase, canonical in PLAIN_GENRES.items():
        entries.setdefault(phrase, canonical)
    return sorted(entries.items(), key=lambda kv: (-len(kv[0]), kv[0]))


def _negated(low: str, start: int) -> bool:
    return re.search(r"\b(?:no|not|without|avoid|non)[\s-]+(?:any\s+|the\s+)?$", low[:start]) is not None


def find_genres(text: str) -> list[tuple[int, str]]:
    """Genres named in ``text`` as (position, genre id), in order, skipping negated ones."""
    low = text.lower()
    taken: list[tuple[int, int]] = []
    hits: list[tuple[int, str]] = []
    for phrase, gid in genre_lexicon():
        pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)(?:\s+(?:classical|music)\b)*"
        for m in re.finditer(pattern, low):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            if not _negated(low, m.start()):
                hits.append((m.start(), gid))
    hits.sort()
    return hits


def _unique(seq: Sequence[str]) -> list[str]:
    out: list[str] = []
    for item in seq:
        if item and item not in out:
            out.append(item)
    return out


def _find_words(low: str, table: dict[str, str]) -> list[tuple[int, int, str]]:
    """Whole-word hits from a phrase table as (start, end, canonical), longest phrase first."""
    taken: list[tuple[int, int]] = []
    hits: list[tuple[int, int, str]] = []
    for phrase in sorted(table, key=len, reverse=True):
        for m in re.finditer(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", low):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), m.end(), table[phrase]))
    hits.sort()
    return hits


def find_instruments(text: str) -> list[str]:
    low = text.lower()
    return _unique([name for start, _, name in _find_words(low, INSTRUMENTS) if not _negated(low, start)])


def find_moods(text: str) -> list[str]:
    low = text.lower()
    return _unique([name for start, _, name in _find_words(low, MOODS) if not _negated(low, start)])


def _tempo_word(low: str) -> Optional[tuple[str, int]]:
    for word, bpm in _TEMPO_WORDS:
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", low):
            return word, bpm
    return None


def _has_musical_content(text: str) -> bool:
    low = text.lower()
    return bool(find_genres(text) or find_instruments(text) or find_moods(text) or _tempo_word(low)
                or re.search(r"\b" + _MUSIC_WORD + r"\b", low))


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

_GREETING = {"hi", "hello", "hey", "help", "thanks", "thank", "yo", "namaste", "salaam", "salam"}
_QUESTION_LEAD = re.compile(
    r"^\s*(?:what|why|how|who|when|where|which|explain|tell me|describe|define|should|teach|whats|what's)\b", re.I)
_POLITE_REQUEST = re.compile(r"^\s*(?:please\s+)?(?:can|could|would|will)\s+you\s+(?:please\s+)?" + _GEN_VERB + r"\b", re.I)
_STRONG_TRANSCRIBE = re.compile(
    r"\b(?:transcribe|transcription|notate)\b|\bnotation\s+(?:for|of)\b|\bsheet music\s+(?:for|of)\b"
    r"|\bwrite (?:out|down) the notes\b", re.I)
_CONVERT = re.compile(
    r"\bconvert\b|\brearrange\b|\bin the style of\b|\bas an?\s+[\w\s-]{0,40}?arrangement\b"
    r"|\b(?:turn|make|change|transform)\s+(?:this|it|that)\b.*\b(?:into|in|to|as)\b"
    r"|\bversion of (?:this|it|that)\b", re.I)
_EDIT_FORM = re.compile(
    r"\bmake (?:it|this|that)\b|\b(?:slower|faster|longer|shorter)\b|\bsame (?:but|again|one|thing)\b"
    r"|\banother one\b|\bagain but\b|\bnow with\b|\bregenerate\b|\bredo\b|\blike the (?:previous|last) one\b", re.I)


def classify_intent(text: str, *, has_attachment: bool = False, genre_id: Optional[str] = None) -> Intent:
    """Keyword rules (spec 10.10). Order matters: transcribe, convert, then generate."""
    low = (text or "").strip().lower()
    words = re.findall(r"[a-z0-9&'-]+", low)
    if not words:
        return "clarify"
    if _STRONG_TRANSCRIBE.search(low) or (has_attachment and re.search(r"\b" + _NOTATION_WORD + r"\b", low)):
        return "transcribe"
    if _CONVERT.search(low):
        if has_attachment:
            return "convert"
        if re.search(r"\bconvert\b|\brearrange\b", low):
            return "clarify"  # nothing to convert: ask for a track
    question = bool(_QUESTION_LEAD.search(low)) and not _POLITE_REQUEST.search(low)
    if question:
        return "chat"
    if len(words) == 1:
        return "chat" if words[0] in _GREETING or _canned(low) else "clarify"

    verb = re.search(r"\b" + _GEN_VERB + r"\b", low) or re.search(r"\bi (?:want|need|would like|'d like) (?:a|an|some)\b", low)
    if _EDIT_FORM.search(low):
        return "generate"
    if verb:
        described = bool(find_genres(low) or find_instruments(low) or find_moods(low) or _tempo_word(low) or genre_id)
        named = bool(re.search(r"\b" + _MUSIC_WORD + r"\b", low))
        if described:
            return "generate"
        if named or re.search(r"\b(?:something|anything)\b", low):
            return "clarify"  # "make a song": too vague to act on
    if genre_id and not _canned(low):
        return "generate"  # a genre chip plus a few words is a request for music
    if (re.match(r"\s*(?:a|an|some|one|two|\d+)\b", low) and re.search(r"\b" + _MUSIC_WORD + r"\b", low)
            and (find_genres(low) or find_instruments(low) or find_moods(low) or _tempo_word(low))):
        return "generate"  # a bare description: "a 20 second jazz piano tune"
    return "chat"


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------

def _parse_duration(low: str) -> tuple[Optional[int], Optional[str]]:
    """(seconds, assumption). The assumption is set only when Orin read a vague word."""
    if re.search(r"\bminute and a half\b", low):
        return 90, None
    if re.search(r"\bhalf a minute\b", low):
        return 30, None
    for m in _DUR_RE.finditer(low):
        minutes = m.group("unit").lower().startswith("m")
        if m.group("word") and m.group("word").lower() in ("a", "an") and not minutes:
            continue  # "a second section" is not a length
        value = float(m.group("num")) if m.group("num") else float(_NUM_WORDS[m.group("word").lower()])
        return int(round(value * 60 if minutes else value)), None
    m = _DUR_S_RE.search(low)
    if m:
        return int(m.group(1)), None
    if re.search(r"\b(?:short|brief|quick)\b", low):
        return SHORT_DURATION_S, f'Read "short" as {SHORT_DURATION_S} seconds.'
    if re.search(r"\blong\b", low):
        return LONG_DURATION_S, f'Read "long" as {LONG_DURATION_S} seconds.'
    return None, None


def _clamp_duration(seconds: int) -> tuple[int, Optional[str]]:
    clamped = max(MIN_DURATION_S, min(MAX_DURATION_S, int(seconds)))
    if clamped != seconds:
        return clamped, (f"You asked for {seconds} seconds; Orin makes between {MIN_DURATION_S} and "
                         f"{MAX_DURATION_S} seconds, so this uses {clamped}.")
    return clamped, None


def _parse_vocals(low: str) -> tuple[Optional[str], Optional[str]]:
    if re.search(r"\binstrumental\b|\b(?:no|without)\s+(?:any\s+)?(?:vocals?|singing|singers?|voice|lyrics|words)\b", low):
        return "none", None
    if re.search(r"\b(?:wordless|humming|hummed|vocalise|vocalize|scat)\b", low):
        return "wordless", None
    if re.search(r"\blyrics\b|\bsings? about\b|\bsinging about\b|\bwith words\b|\bsong about\b", low):
        return "lyrics", None
    if re.search(r"\b(?:vocals?|singers?|sung|singing|voice|choir|vocalist)\b", low):
        return "wordless", "Assumed wordless vocals; ask for lyrics if you want words."
    return None, None


_VOCAL_TERMS = {"vocal", "vocals", "singing", "singer", "singers", "voice", "lyrics", "words", "any"}


def _parse_avoid(low: str) -> list[str]:
    out: list[str] = []
    for m in re.finditer(r"\b(?:no|without|avoid)\s+(?:any\s+|the\s+)?((?:heavy\s+|loud\s+)?[a-z][a-z-]+)", low):
        phrase = m.group(1).strip()
        if phrase.split()[-1] in _VOCAL_TERMS:
            continue
        out.append(INSTRUMENTS.get(phrase, phrase))
    return _unique(out)


def _parse_key_mode(text: str, genres: Sequence[str]) -> tuple[Optional[str], Optional[str]]:
    key: Optional[str] = None
    mode: Optional[str] = None
    m = re.search(r"\b(?:key of|tonic (?:of|on|is)|Sa (?:at|on|is))\s+([A-G](?:#|b)?)(?![\w#])(?:\s+(major|minor))?", text)
    if not m:
        m = re.search(r"\bin\s+([A-G](?:#|b)?)\s+(major|minor)\b", text)
    if m:
        key = m.group(1)
        mode = m.group(2)
    m = re.search(r"\b(?i:raga|ragam|raag|maqam|makam|dastgah)\s+(?:called\s+)?([A-Za-z][\w-]+(?:\s+[A-Z][\w-]+)?)", text)
    if m:
        word = m.group(1).strip()
        # A capitalised word is taken as a name; a lower-case one only if Orin has a table for it.
        known = any(meta.mode(word) is not None for meta in _metas() if meta.pitch_system != "12tet")
        if known or (word[0].isupper() and word.lower() not in {"in", "on", "that", "which", "and", "with", "music", "is", "for"}):
            mode = word[0].upper() + word[1:]
    if mode is None:
        low = text.lower()
        for gid in genres:
            meta = _meta_for(gid)
            if meta is None or meta.pitch_system == "12tet":
                continue
            for entry in meta.mode_table:
                names = {entry.id.replace("_", " "), *[a.lower() for a in entry.aliases]}
                if any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", low) for n in names if len(n) > 2):
                    return key, entry.label
    return key, mode


def _describe(part: str) -> str:
    s = _LEAD.sub("", part.strip())
    s = re.sub(r"^(?:into|to)\s+", "", s, flags=re.I)
    s = _DUR_RE.sub("", s)
    s = _DUR_S_RE.sub("", s)
    s = re.sub(r"\b(?:short|brief|quick|long)\b", "", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,.;:!?-")
    s = re.sub(r"^(?:a|an|some|the)\s+", "", s, flags=re.I)
    return s.strip(" ,.;:!?-")


def _split_sections(text: str) -> list[str]:
    """Parts of a request that names a change mid-track ("... that turns into jazz piano")."""
    parts = [p for p in _STRONG_SPLIT.split(text) if p and p.strip()]
    if len(parts) > 1 and all(_has_musical_content(p) for p in parts[1:]):
        return parts[:4]
    weak = _WEAK_SPLIT.split(text, maxsplit=1)
    if len(weak) == 2 and find_genres(weak[0]) and find_genres(weak[1]):
        return weak
    return [text]


def _split_durations(total: int, n: int) -> list[int]:
    base = total // n
    out = [base] * n
    out[-1] += total - base * n
    return out


# Words that point to a tradition when no genre is named. "raga" alone does not say which one.
_TRADITION_WORDS: tuple[tuple[str, str], ...] = (
    (r"\b(?:ragam|alapana|kriti|varnam|swaras?|gamakas?)\b", "carnatic"),
    (r"\b(?:raag|alap|bandish|meend)\b", "hindustani"),
    (r"\b(?:ragas?)\b", "indian classical"),
    (r"\b(?:taqsim|maqamat)\b", "arabic_maqam"),
    (r"\b(?:gusheh|avaz|koron|sori)\b", "persian_dastgah"),
)


def _implied_genre(text: str, mode: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(genre id, assumption) from a mode name Orin has a table for, or from a tradition's own word."""
    if mode:
        for meta in _metas():
            if meta.pitch_system != "12tet" and meta.mode(mode) is not None:
                return meta.id, f"{mode} is in Orin's {genre_name(meta.id)} table, so I assumed {genre_name(meta.id)}."
    low = text.lower()
    for pattern, gid in _TRADITION_WORDS:
        m = re.search(pattern, low)
        if m and (gid in PLAIN_GENRES.values() or _meta_for(gid) is not None):
            return gid, f'Read "{m.group(0)}" as a request for {genre_name(gid)} music.'
    return None, None


def _pref_genre(prefs: Optional[dict]) -> Optional[str]:
    if not isinstance(prefs, dict):
        return None
    for key in ("favorite_genres", "favourite_genres", "genres"):
        value = prefs.get(key)
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], str):
            hits = find_genres(value[0])
            return hits[0][1] if hits else None
    return None


def _fresh_spec(text: str, genre_id: Optional[str], prefs: Optional[dict]) -> MusicSpec:
    low = text.lower()
    assumptions: list[str] = []
    chip = None
    if genre_id:
        meta = _meta_for(genre_id)
        chip = meta.id if meta is not None else genre_id.strip().lower()

    parts = _split_sections(text)
    if len(parts) > 1:
        part_genres: list[Optional[str]] = []
        for part in parts:
            hits = find_genres(part)
            part_genres.append(hits[0][1] if hits else None)
    else:
        found = _unique([g for _, g in find_genres(text)])
        specific = [g for g in found if g != "fusion"]
        if len(specific) > 1:
            part_genres = ["fusion"]
            assumptions.append("Treated as one fusion of " + " and ".join(genre_name(g) for g in specific) + ".")
        else:
            part_genres = [specific[0] if specific else (found[0] if found else None)]

    if part_genres[0] is None:
        part_genres[0] = chip
    if part_genres[0] is None:
        implied, note = _implied_genre(parts[0], _parse_key_mode(parts[0], [])[1])
        if implied:
            part_genres[0] = implied
            assumptions.append(note or "")
    if part_genres[0] is None:
        part_genres[0] = _pref_genre(prefs)
        if part_genres[0] is None:
            part_genres[0] = DEFAULT_GENRE
            assumptions.append(f"No genre named, so I assumed {genre_name(DEFAULT_GENRE)}; name a genre to change it.")
    for i in range(1, len(part_genres)):
        if part_genres[i] is None:
            part_genres[i] = part_genres[i - 1]
    genres = [g for g in part_genres if g]

    seconds, note = _parse_duration(low)
    if note:
        assumptions.append(note)
    if seconds is None:
        seconds = DEFAULT_DURATION_S
        assumptions.append(f"No length given, so I assumed {DEFAULT_DURATION_S} seconds.")
    seconds, note = _clamp_duration(seconds)
    if note:
        assumptions.append(note)

    sections = [
        SpecSection(genre_id=g, description=_describe(part) or genre_name(g), duration_s=d)
        for g, part, d in zip(genres, parts, _split_durations(seconds, len(genres)))
    ]
    structure_notes = None
    if len(sections) > 1:
        assumptions.append(f"Split the time evenly between the {len(sections)} parts.")
        names = [genre_name(g) for g in genres]
        structure_notes = "Starts as " + names[0] + "".join(f", then moves to {n}" for n in names[1:]) + "."

    tempo: Optional[int] = None
    m = _BPM_RE.search(low)
    if m:
        tempo = max(30, min(240, int(m.group(1))))
    elif len(sections) == 1:
        hit = _tempo_word(low)
        if hit:
            tempo = hit[1]
            assumptions.append(f'Read "{hit[0]}" as about {hit[1]} BPM.')

    vocals, note = _parse_vocals(low)
    if note:
        assumptions.append(note)
    if vocals is None:
        vocals = "none"
        assumptions.append("Assumed instrumental, since no vocals were asked for.")
    instruments = find_instruments(text)
    if vocals != "none" and "voice" not in instruments:
        instruments.append("voice")
    if not instruments:
        assumptions.append("No instruments named, so the choice is left to the generator.")

    key, mode = _parse_key_mode(text, genres)
    goal = None
    m = re.search(r"\b(?:so (?:that )?i can\s+|to (?=(?:learn|understand|hear|practi[cs]e|study)\b))(.+)$", text.strip(), re.I)
    if m:
        goal = m.group(1).strip(" .!?") or None

    unique_genres = _unique(genres)
    return MusicSpec(
        genre_id=unique_genres[0] if len(unique_genres) == 1 else "fusion",
        sections=sections,
        mood=find_moods(text),
        tempo_bpm=tempo,
        key_or_tonic=key,
        mode_hint=mode,
        instruments=instruments,
        vocals=vocals,  # type: ignore[arg-type]
        duration_s=seconds,
        structure_notes=structure_notes,
        avoid=_parse_avoid(low),
        assumptions=assumptions,
        learning_goal=goal,
    )


_EDIT_REF = re.compile(r"\b(?:it|same|again|previous|last one|that one|this one|another)\b", re.I)
_EDIT_LEAD = re.compile(
    r"^\s*(?:(?:now|and|also|but|then|ok|okay)[,\s]+)*(?:slower|faster|longer|shorter|with|add|remove|without|"
    r"more|less|no|sadder|happier|calmer|darker|brighter|a bit|a little|much|slightly|like the)\b", re.I)
_FRESH = re.compile(r"\b" + _GEN_VERB + r"\s+(?:me\s+)?(?:a|an|some)\s+", re.I)


def is_edit(text: str) -> bool:
    """True when the text changes an earlier request ("make it slower") instead of starting a new one."""
    if _FRESH.search(text):
        return False
    return bool(_EDIT_REF.search(text) or _EDIT_LEAD.search(text))


def _coerce_specs(previous_specs: Sequence[Any]) -> list[MusicSpec]:
    out: list[MusicSpec] = []
    for item in previous_specs or ():
        if isinstance(item, MusicSpec):
            out.append(item)
            continue
        try:
            out.append(MusicSpec.model_validate(item))
        except Exception:
            continue
    return out


def _rescale_sections(sections: list[dict], new_total: int) -> list[dict]:
    old_total = sum(s["duration_s"] for s in sections) or 1
    out = [dict(s, duration_s=max(3, int(round(s["duration_s"] * new_total / old_total)))) for s in sections]
    if out:
        out[-1]["duration_s"] = max(3, new_total - sum(s["duration_s"] for s in out[:-1]))
    return out


def _apply_edit(prev: MusicSpec, text: str) -> Optional[MusicSpec]:
    """A copy of ``prev`` with the requested changes, or None if no change was recognised."""
    low = text.lower()
    d = prev.model_dump()
    changes: list[str] = []

    # tempo
    m = _BPM_RE.search(low)
    slower = re.search(r"\bslower\b|\bslow (?:it )?down\b|\bmore slowly\b", low)
    faster = re.search(r"\bfaster\b|\bspeed (?:it )?up\b|\bquicker\b", low)
    much = re.search(r"\b(?:much|a lot|way|far)\s+(?:slower|faster)\b", low)
    if m:
        d["tempo_bpm"] = max(30, min(240, int(m.group(1))))
        changes.append(f"Tempo set to {d['tempo_bpm']} BPM.")
    elif slower or faster:
        if prev.tempo_bpm:
            factor = (0.65 if much else 0.8) if slower else (1.5 if much else 1.25)
            d["tempo_bpm"] = max(30, min(240, int(round(prev.tempo_bpm * factor))))
            changes.append(f"Tempo changed from {prev.tempo_bpm} to {d['tempo_bpm']} BPM.")
        else:
            d["tempo_bpm"] = 70 if slower else 140
            word = "slower" if slower else "faster"
            changes.append(f'No tempo was set before, so "{word}" is read as about {d["tempo_bpm"]} BPM.')

    # length
    seconds, _ = _parse_duration(re.sub(r"\blong(?:er)?\b|\bshort(?:er)?\b", "", low))
    if seconds is None:
        if re.search(r"\btwice as long\b|\bdouble the length\b", low):
            seconds = prev.duration_s * 2
        elif re.search(r"\bhalf as long\b|\bhalf the length\b", low):
            seconds = prev.duration_s // 2
        elif re.search(r"\blonger\b", low):
            seconds = int(round(prev.duration_s * 1.5))
        elif re.search(r"\bshorter\b", low):
            seconds = int(round(prev.duration_s * 0.5))
    if seconds is not None:
        seconds = max(MIN_DURATION_S, min(MAX_DURATION_S, seconds))
        if seconds != prev.duration_s:
            d["duration_s"] = seconds
            d["sections"] = _rescale_sections(d["sections"], seconds)
            changes.append(f"Length changed from {prev.duration_s} to {seconds} seconds.")

    # instruments
    removed = [INSTRUMENTS[m.group(1)] for m in re.finditer(
        r"\b(?:no|without|remove|drop|lose|instead of)\s+(?:the\s+|any\s+)?([a-z]+(?: [a-z]+)?)", low)
        if m.group(1) in INSTRUMENTS]
    removed += [INSTRUMENTS[m.group(1)] for m in re.finditer(
        r"\b(?:no|without|remove|drop|lose|instead of)\s+(?:the\s+|any\s+)?([a-z]+)", low) if m.group(1) in INSTRUMENTS]
    removed = _unique(removed)
    added = [i for i in find_instruments(text) if i not in removed and i not in d["instruments"]]
    if added:
        d["instruments"] = d["instruments"] + added
        changes.append("Added " + ", ".join(added) + ".")
    if removed:
        d["instruments"] = [i for i in d["instruments"] if i not in removed]
        d["avoid"] = _unique(d["avoid"] + removed)
        changes.append("Removed " + ", ".join(removed) + ".")

    # vocals
    vocals, _ = _parse_vocals(low)
    if vocals is not None and vocals != prev.vocals:
        d["vocals"] = vocals
        if vocals == "none":
            d["instruments"] = [i for i in d["instruments"] if i != "voice"]
        elif "voice" not in d["instruments"]:
            d["instruments"] = d["instruments"] + ["voice"]
        changes.append({"none": "Made it instrumental.", "wordless": "Added wordless vocals.",
                        "lyrics": "Added vocals with original lyrics."}[vocals])

    # mood
    moods = [m for m in find_moods(text) if m not in d["mood"]]
    if moods:
        d["mood"] = d["mood"] + moods
        changes.append("Mood: added " + ", ".join(moods) + ".")

    # genre
    if find_genres(text):
        fresh = _fresh_spec(text, None, None)
        d["genre_id"] = fresh.genre_id
        if len(fresh.sections) > 1:
            d["sections"] = _rescale_sections([s.model_dump() for s in fresh.sections], d["duration_s"])
            d["structure_notes"] = fresh.structure_notes
        else:
            name = genre_name(fresh.genre_id)
            d["sections"] = [{"genre_id": fresh.genre_id, "duration_s": d["duration_s"],
                              "description": f"{name}, keeping the mood, tempo and instruments of the previous version"}]
            d["structure_notes"] = None
        if fresh.mode_hint:
            d["mode_hint"] = fresh.mode_hint
        elif fresh.genre_id != prev.genre_id:
            d["mode_hint"] = None
        changes.append(f"Genre changed to {genre_name(fresh.genre_id)}.")

    key, mode = _parse_key_mode(text, [d["genre_id"]])
    if key and key != prev.key_or_tonic:
        d["key_or_tonic"] = key
        changes.append(f"Tonic set to {key}.")
    if mode and mode != d.get("mode_hint"):
        d["mode_hint"] = mode
        changes.append(f"Mode hint set to {mode}.")

    if not changes:
        return None
    d["assumptions"] = ["Based on the previous request in this thread."] + changes
    return MusicSpec.model_validate(d)


def refine_spec(text: str, *, previous_specs: Sequence[Any] = (), genre_id: Optional[str] = None,
                prefs: Optional[dict] = None) -> SpecResult:
    """Template spec from keywords. Every default Orin filled in is listed in ``assumptions``."""
    text = (text or "").strip()
    previous = _coerce_specs(previous_specs)
    if not text and not genre_id:
        return SpecResult(ok=False, source="offline", notes=[OFFLINE_SPEC_NOTE],
                          question="What kind of music would you like? Name a genre, a mood or an instrument.")
    if previous and is_edit(text):
        edited = _apply_edit(previous[-1], text)
        if edited is None:
            return SpecResult(
                ok=False, source="offline", notes=[OFFLINE_SPEC_NOTE],
                question=("I could not tell what to change. The offline stand-in understands: slower, faster, "
                          "longer, shorter, a length in seconds, with or without an instrument, or a genre name."))
        return SpecResult(ok=True, spec=edited, notes=_unique([*edited.assumptions, OFFLINE_SPEC_NOTE]), source="offline")
    spec = _fresh_spec(text, genre_id, prefs)
    return SpecResult(ok=True, spec=spec, notes=_unique([*spec.assumptions, OFFLINE_SPEC_NOTE]), source="offline")


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

_DRAFT = "Orin's tables for this are drafts that no practitioner has reviewed yet, so treat the details as a starting point."

_CANNED: tuple[tuple[str, str], ...] = (
    (r"\bloss report\b|\bfidelity\b|\bwhat (?:was|is|got) lost\b|\bhow is (?:this|it) scored\b",
     "The loss report lists what a notation could not carry over from a performance: pitches between its notes "
     "that were rounded, pitch movement it has no symbol for, and timing moved to fit a grid. Each item says what "
     "changed and what it cost. A separate list of notices holds Orin's own assumptions and doubts; those do not "
     "count as loss. The fidelity score runs from 0 to 1 and combines pitch, ornament and timing. "
     "The weights are Orin's choice, not a measurement, and transcription from audio is approximate."),
    (r"\bgamakas?\b|\bgamakam\b",
     "A gamaka is, typically, the pitch movement on or between notes in Indian classical music: oscillations, "
     "slides and quick touches of neighbouring notes. In many ragas the movement is part of the note's identity, "
     "so a note written without it loses something. Orin describes such movement in neutral words (oscillation, "
     "glide, grace, vibrato) and reports when a notation cannot show it. " + _DRAFT),
    (r"\braga(?:s|m)?\b|\braags?\b",
     "A raga is a melodic framework, not just a scale. It typically has a set of notes, characteristic phrases, "
     "notes that are emphasised, and ornaments that belong to it, so two ragas can share the same notes and still "
     "be different. Orin only compares the pitches it hears with its tables, so it shows the closest matches and "
     "their lookalikes, never one certain answer. " + _DRAFT),
    (r"\bmaqam(?:s|at)?\b|\bmakam\b",
     "A maqam is a melodic mode in Arabic music. It is typically built from smaller groups of notes, often uses "
     "neutral intervals that fall between the keys of a piano, and has a usual melodic path. Tuning varies by "
     "region and performer, so written values are nominal. " + _DRAFT),
    (r"\bkoron\b|\bsori\b",
     "A koron is a sign in Persian music that lowers a note by less than a semitone, typically by roughly a "
     "quarter tone; the exact size varies with the dastgah and the performer. Its counterpart, the sori, raises a "
     "note by a similarly small amount. Neither has a key on a piano. " + _DRAFT),
    (r"\bdastgah\b",
     "A dastgah is one of the modal systems of Persian classical music. It is typically described as a family of "
     "melodic models (gusheh-ha) with a home area, not as a single scale. Orin models only the opening area, so a "
     "change of gusheh can look like notes outside the mode. " + _DRAFT),
    (r"\btala(?:s|m)?\b|\btaal\b",
     "A tala is a rhythmic cycle in Indian classical music, typically kept with hand gestures such as claps, finger "
     "counts and waves. The cycle repeats, and where a phrase lands inside it matters as much as the notes. Orin "
     "shows a cycle by its length and grouping; the accents it draws are a modelling choice. " + _DRAFT),
    (r"\bsargam\b|\bswaras?\b|\bwhat is sa\b",
     "Sargam is the Indian note-naming system: Sa Ri Ga Ma Pa Da Ni in Carnatic usage, Sa Re Ga Ma Pa Dha Ni in "
     "Hindustani. The names are relative to Sa, the tonic, which the performer chooses, so the same written line "
     "can be sung at any pitch. Several swaras typically come in more than one position; Orin's Carnatic notation "
     "marks the position with a number."),
    (r"\balapana\b|\balap\b",
     "An alapana is, typically, an unmetered improvised opening that explores a raga without a fixed beat. Because "
     "it has no steady pulse, Orin writes it in free time; putting it into bars would be reported as a loss."),
    (r"\bmicrotones?\b|\bcents?\b|\bquarter[- ]tones?\b",
     "A cent is a hundredth of a semitone, a small unit for measuring pitch. A microtone is any pitch that falls "
     "between the twelve notes of a piano. Many traditions use such pitches on purpose; staff notation has to round "
     "them, and Orin's loss report shows by how much."),
    (r"^\s*(?:thanks|thank you|thx)\b",
     "You are welcome. Ask me about a musical term, or tell me what music you would like to hear."),
    (r"\bwhat can you do\b|\bwho are you\b|\bwhat are you\b|\bhelp\b|^\s*(?:hi|hello|hey|namaste)\b",
     "I am Orin, a music-learning helper. I can make a short piece of music from a description (try: make a calm "
     "Carnatic piece that turns into jazz piano), turn a recording or typed notes into notation for several "
     "traditions in the Notation Studio, and show what each notation loses on the way. Transcription is approximate, "
     "and Orin's tradition tables are unreviewed drafts."),
)

_OFFLINE_FALLBACK = (
    "I am running as Orin's offline stand-in, with no language model connected, so I can only answer a few common "
    "questions: what a raga, gamaka, maqam, tala or koron is, and how the loss report works. "
    "You can still ask me to make music (I will build a spec and a labelled placeholder clip), convert a recording "
    "or notes in the Notation Studio, and read the loss report beside the notation. "
    "For full answers, set GEMINI_API_KEY and GEMINI_MODEL on the server."
)


def _canned(low: str) -> Optional[str]:
    for pattern, answer in _CANNED:
        if re.search(pattern, low):
            return answer
    return None


def chat_reply(text: str, *, history: Sequence[Turn] = (), prefs: Optional[dict] = None,
               context: Optional[str] = None, notice: Optional[str] = None) -> str:
    low = (text or "").strip().lower()
    answer = _canned(low)
    if answer is None:
        if re.search(r"\bconvert\b|\brearrange\b", low):
            answer = ("To convert a piece, attach a track from your Library, or open it in the Notation Studio, "
                      "and tell me the style to convert to.")
        elif not re.search(r"[a-z0-9]", low):
            answer = ("What would you like to do? You can ask me to make music, to write a recording out as "
                      "notation, or to explain a musical term.")
        elif classify_intent(low) == "clarify":
            # Too vague to act on (spec 10.3): ask, instead of guessing.
            answer = ("What kind of music would you like? Tell me a style (for example Carnatic, jazz or rock), "
                      "a mood or an instrument, and roughly how long it should be.")
        else:
            answer = _OFFLINE_FALLBACK
            if context:
                answer += " The loss report panel has a plain explanation for each item."
    return f"{notice.strip()} {answer}" if notice else answer


def chunk_text(text: str, sentences_per_chunk: int = 2) -> Iterator[str]:
    parts = re.findall(r"[^.!?]+[.!?]+(?:\s+|$)|[^.!?]+$", text)
    for i in range(0, len(parts), sentences_per_chunk):
        chunk = "".join(parts[i:i + sentences_per_chunk])
        if chunk:
            yield chunk


def chat_reply_chunks(text: str, *, history: Sequence[Turn] = (), prefs: Optional[dict] = None,
                      context: Optional[str] = None, notice: Optional[str] = None) -> Iterator[str]:
    yield from chunk_text(chat_reply(text, history=history, prefs=prefs, context=context, notice=notice))


def summarize(history: Sequence[Turn], max_chars: int = 400) -> str:
    """A crude extractive summary: the start of each user turn, keeping the newest that fit."""
    bits: list[str] = []
    for turn in history or ():
        if turn.role != "user":
            continue
        line = re.sub(r"\s+", " ", turn.text).strip()
        if line:
            bits.append(line if len(line) <= 80 else line[:77].rstrip() + "...")
    if not bits:
        return ""
    kept: list[str] = []
    used = 0
    for bit in reversed(bits):
        if kept and used + len(bit) + 2 > max_chars:
            break
        kept.append(bit)
        used += len(bit) + 2
    kept.reverse()
    omitted = len(bits) - len(kept)
    head = f"After {omitted} earlier requests, the user asked: " if omitted else "The user asked: "
    return head + "; ".join(kept)


# ---------------------------------------------------------------------------
# Loss explanations
# ---------------------------------------------------------------------------
# One entry per code: a short label, three texts (beginner, intermediate, advanced) that say
# WHY, and one or two things to try. No digits: numbers come from the report only. No
# tradition's ornament names: the report carries neutral names, and so do these texts.

_EXPLAIN: dict[str, dict[str, Any]] = {
    "microtone_rounded": {
        "label": "in-between pitches rounded",
        "beginner": "Some music uses pitches that sit between the notes this notation can write. Such a note is written as its nearest neighbour, a little like rounding a price to the nearest coin. What you read is close to what was performed, but it is not exactly the same pitch.",
        "intermediate": "The target notation has a fixed set of pitches in each octave. A performed pitch that falls between two of them has to be written as the nearer one, and the report records the distance in cents, which are hundredths of a semitone. Small distances are heard as colour; larger ones change the character of the interval.",
        "advanced": "Quantising to the target's pitch grid discards the offset between the performed anchor pitch and the nearest writable degree. The report keeps that offset in cents so it can be restored, and only offsets beyond the scoring tolerance lower the fidelity score. The number alone cannot say whether the offset is a deliberate shade of the degree, intonation, or tracking error.",
        "try": ["On an instrument that can bend pitch, such as the voice, a violin or a keyboard with a pitch bend wheel, play the written note and then lean it slightly towards its neighbour to hear what was lost.",
                "Convert to a notation with finer pitches, if one suits the music, and compare the two loss reports."],
    },
    "microtone_rounded.12tet": {
        "label": "in-between pitches rounded to piano notes",
        "beginner": "A piano has twelve keys in every octave, and staff notation writes those same twelve notes. The performed note fell in the gap between two keys, so it is shown as the nearer key. No key on a piano can play the in-between pitch.",
        "intermediate": "Twelve-tone equal temperament divides the octave into twelve equal semitones. A pitch between two of them, such as a neutral or finely shaded degree, has no sign on a standard staff, so it is written as the nearest semitone and the difference in cents is recorded.",
        "advanced": "The target grid is twelve-tone equal temperament, so each anchor pitch is rounded to the nearest whole semitone and the residual in cents is kept in the item. A neutral interval loses its identity under this rounding, because its two neighbours are wrong by similar amounts in opposite directions.",
        "try": ["Sing or play the written note, slide slowly towards the next key and stop partway: that in-between place is the pitch that was lost.",
                "On a MIDI instrument, add a small pitch bend to the note to put the in-between pitch back."],
    },
    "temperament_retuned": {
        "label": "tuning system changed",
        "beginner": "Different traditions tune the same notes in slightly different ways. This conversion keeps the notes but swaps the tuning system behind them, so each note moves by a very small amount. Most listeners hear that as a change of flavour, not as wrong notes.",
        "intermediate": "The source and the target use different tuning references, for example pure intervals against a drone on one side and equal steps on the other. The scale degrees stay the same while their nominal pitches move a little. This is reported once for the whole piece and does not lower the fidelity score.",
        "advanced": "Degree identities are preserved; the nominal tunings of the two pitch systems differ. The differences are systematic, so they are grouped into one item instead of being logged note by note, and they sit inside the scoring tolerance. Both tables are nominal values, not measurements of this performance.",
        "try": ["Sound a long tonic as a drone and sing the note above it until the two blend smoothly, then compare that with the same note on a keyboard."],
    },
    "ornament_dropped": {
        "label": "pitch movement left out",
        "beginner": "The performer did not hold this note still: its pitch moved while it sounded. This notation has no sign for that kind of movement, so only a plain note is written. You get the skeleton of the melody without the motion that brought it to life.",
        "intermediate": "Pitch movement inside a note is part of the melody in many traditions. The target notation has no symbol that matches this movement, so the note is written plain, and the size and speed of the movement survive only in the report.",
        "advanced": "The contour inside the note was classed as an ornament, and the target's ornament table has no rule that keeps a movement of this kind and size, so it is dropped. Its parameters stay in the item detail. The cost to the ornament part of the score grows with the width of the movement and with how much of the note it covered.",
        "try": ["Play the original at this spot and trace the pitch in the air with your hand while you sing the plain written note.",
                "On a violin or another instrument without frets, slide or rock the finger around the written note to imitate the movement."],
    },
    "ornament_approximated": {
        "label": "pitch movement reduced to a general mark",
        "beginner": "The pitch moved while this note sounded. The notation has a general mark that says something happens here, so that mark is used. It tells you the note is not plain, but not exactly how it should move.",
        "intermediate": "The target notation has a symbol in the same family as this movement, but the symbol does not record how wide, how fast or what shape the movement was. Read the mark as a reminder to ornament; the details are in the report.",
        "advanced": "The target's ornament table maps this kind of movement to a generic mark. Extent, rate and shape are not encoded by the mark, so the item costs half of what dropping the ornament would cost in the ornament part of the score.",
        "try": ["Treat the mark as a cue: listen to the original at this spot and copy the movement by ear.",
                "Make an ornament-marked line of your own by writing a few words above the mark that describe the movement."],
    },
    "ornament_not_supplied": {
        "label": "ornaments the target style expects are missing",
        "beginner": "The source has plain notes, but music in the target tradition is usually performed with ornaments that depend on the mode. Orin does not make those up, so the result is plainer than a performer would play it.",
        "intermediate": "The target idiom expects ornaments that belong to the mode and the phrase, and the source does not contain any. Inventing them would be guessing, so Orin writes the plain notes and tells you the line is bare.",
        "advanced": "The target format is flagged as an idiom that is incomplete without ornaments, and the source events carry none. Supplying them would need knowledge of the mode's phrases that Orin's draft tables do not hold, so nothing is added and the gap is reported instead.",
        "try": ["Listen to recordings in the target tradition and notice which notes of this mode are usually ornamented.",
                "Ask a teacher of the tradition to mark the plain line with the ornaments they would use."],
    },
    "timing_shift": {
        "label": "note starts moved onto the grid",
        "beginner": "Written music lines notes up on a grid of beats. This note began a little before or after the nearest grid line, so it was moved onto the line. The written rhythm is tidier than the performance was.",
        "intermediate": "Note starts are snapped to the target's rhythmic grid. Expressive timing, such as playing ahead of the beat or behind it, is lost in that step, and the size of each move is recorded.",
        "advanced": "The onset was quantised to the nearest grid pulse, and the size of the shift relative to the grid step sets the timing penalty. Similar shifts across many notes suggest that the detected tempo or the subdivision does not fit, not that the performer was imprecise.",
        "try": ["Clap the written rhythm with a metronome, then clap along with the recording and notice where the two differ.",
                "Convert again with a finer grid, if the notation offers one, and compare the reports."],
    },
    "free_time_measured": {
        "label": "free rhythm forced into bars",
        "beginner": "The original has no steady beat: the performer stretched and shortened notes freely. This notation needs bars and note lengths, so Orin had to add them. They make the page readable, but the performer was not counting them.",
        "intermediate": "The source is unmetered, and the target notation requires a meter. The bar lines and note values are Orin's best fit to the durations, not something in the music, and this limits the timing part of the score.",
        "advanced": "No stable pulse was found, and the conversion was asked to produce measured notation anyway. Durations were fitted to a nominal grid, so every bar line is an artefact. The timing part of the fidelity score is capped for this reason.",
        "try": ["Read the result as a row of long and short notes and ignore the bar lines.",
                "Play it without a metronome and breathe between the phrases, as the recording does."],
    },
    "cycle_reinterpreted": {
        "label": "rhythm cycle shown in another form",
        "beginner": "Every tradition groups beats in its own way. The cycle here was matched to one of the same length in the target notation, but the way the beats are grouped and stressed inside it may be different from the original.",
        "intermediate": "The source cycle was mapped to a target cycle or meter by length only. Counting gestures, groupings and accents do not carry over, so the written meter is a container of the right size, not the same rhythm cycle.",
        "advanced": "Cycle mapping matched the number of pulses and nothing else. The internal structure of the source cycle, such as its groups and accent pattern, has no equivalent in the target template, so it is not shown. The accent weights Orin draws are a modelling choice.",
        "try": ["Count the original cycle aloud with its own grouping while you follow the written bars.",
                "Mark the start of each original group in the notation with a pencil."],
    },
    "mode_approximated": {
        "label": "mode replaced by one with the same pitches",
        "beginner": "The target tradition has a mode that uses the same notes, so its name is shown. Sharing notes does not make two modes the same thing: each has its own typical phrases, important notes and ornaments.",
        "intermediate": "A target mode with the same pitch set was found and is offered as a label. A mode is typically more than its pitches: emphasis, characteristic phrases and ornaments are not implied by the match.",
        "advanced": "The match is on the pitch set only, on nominal values. Phrase grammar, emphasis and ornament practice are outside what Orin's tables model, so the target label is a lookalike and not an equivalence.",
        "try": ["Listen to a performance in each of the two modes and notice which notes each one rests on.",
                "Sing the same notes with the phrases of each mode to hear how different they feel."],
    },
    "mode_unrepresentable": {
        "label": "no matching mode in the target",
        "beginner": "The target tradition has no mode with this set of notes, so the notes are written without a mode name. Nothing is wrong with the music; the target's list simply has no place for it.",
        "intermediate": "None of the modes in the target's table has these pitches, so no mode is named and each note goes to the nearest pitch the notation can write. The table is a draft and may be missing modes.",
        "advanced": "No entry in the target's mode table matches the source pitch set within tolerance, so quantising runs against the bare pitch grid with no mode constraint. An incomplete draft table is a likely cause.",
        "try": ["Compare the written notes with the closest modes Orin lists and see which notes differ.",
                "Ask a practitioner whether the tradition has a mode for these notes that Orin's draft table lacks."],
    },
    "octave_folded": {
        "label": "notes moved by an octave",
        "beginner": "This note is higher or lower than the notation can show, so it was moved by an octave to fit. It has the same note name, but the shape of the melody changes at that point.",
        "intermediate": "The note lies outside the range the target notation or instrument can write and was shifted by an octave. The pitch class is kept, while the contour of the melody is broken where the fold happens.",
        "advanced": "The event falls outside the writable range of the target and was folded by an octave. This counts as a fixed pitch penalty for the note, because the register and the melodic contour are changed even though the pitch class is kept.",
        "try": ["Sing the passage in the original register and again as written to hear where the line jumps.",
                "Choose another tonic or instrument range, if you can, so that the passage fits without folding."],
    },
    "dynamics_dropped": {
        "label": "loudness not written",
        "beginner": "The performer played some notes louder and some softer. This output does not write loudness, so that shaping is missing from the page.",
        "intermediate": "Loudness changes were measured but this notation style, or this output, has no place for them. The notes and rhythm are unaffected.",
        "advanced": "Loudness was measured for each note, but this output has no place to write it, so it is left out. The fidelity score does not count this.",
        "try": ["Listen for where the original grows and fades, and pencil those shapes above the notes.",
                "Play the passage once at an even loudness and once following the original, and hear the difference."],
    },
    # notices: Orin's own assumptions and doubts
    "out_of_mode_note": {
        "label": "notes outside the chosen mode",
        "beginner": "This note does not belong to the mode that was chosen. Orin wrote it as it was played instead of pushing it into the mode, because the performer may have meant it.",
        "intermediate": "The note is outside the selected mode. A mode is a guide for the conversion, not a filter, so the note is written at its own pitch and flagged so you can check it.",
        "advanced": "The anchor pitch is outside every degree window of the selected mode, so it was written against the bare pitch grid. Possible causes are a deliberate outside note, a different mode from the one selected, or a pitch-tracking error.",
        "try": ["Listen to the original at this spot and decide whether the note sounds intended.",
                "Try another of the closest modes Orin lists and see whether the note then fits."],
    },
    "pitch_anchor_uncertain": {
        "label": "notes whose pitch was hard to pin down",
        "beginner": "The pitch of this note keeps moving, so there is no single steady pitch to write down. Orin chose the nearest note of the mode, and that choice is a best guess.",
        "intermediate": "The note has no stable stretch from which to read one pitch, so its anchor is uncertain. It was assigned to the nearest degree of the mode, with lower confidence than a steady note.",
        "advanced": "No sufficiently stable segment of the contour was found, so the anchor estimate has a wide spread and the degree assignment leans on the mode. Treat the written degree as a hypothesis.",
        "try": ["Slow the recording down and listen for where the pitch comes to rest.",
                "Compare this note with the same phrase elsewhere in the piece."],
    },
    "transposed_for_notation": {
        "label": "written at a conventional pitch",
        "beginner": "Some traditions write a mode at a customary pitch whatever pitch the performer chose. The notes on the page keep their relationships to each other, but they are not at the pitch you hear.",
        "intermediate": "The notation follows the convention of writing this mode on a customary tonic. The written and sounding pitches differ by a fixed interval, which the report gives; nothing inside the melody changes.",
        "advanced": "The format's written-tonic policy is conventional, so the score is transposed by a constant interval relative to the sounding tonic. This is a notation convention, not a loss.",
        "try": ["Play the written tonic and the tonic of the recording one after the other to hear the distance.",
                "Ask for the notation at sounding pitch instead, if you prefer to read what you hear."],
    },
    "drone_not_notated": {
        "label": "drone heard but not written",
        "beginner": "A steady background note, a drone, was heard under the melody. Orin used it to find the home note, but the drone itself is not written in the notation.",
        "intermediate": "A sustained drone was detected and used as evidence for the tonic. Only the melody line is notated, so the drone is left off the page.",
        "advanced": "Stable sustained partials consistent with a drone were found and weighted in the tonic estimate. Orin writes one melodic line, so the drone is recorded as a notice and not transcribed.",
        "try": ["Hum the drone note while you read the melody, to hear each note against it.",
                "Play the melody over a held tonic and fifth to bring back the setting."],
    },
    "tonic_assumed": {
        "label": "home note assumed",
        "beginner": "Orin could not tell with confidence which note is the home note of this music, so it assumed one. If the assumption is wrong, the note names will all be shifted, so set the home note yourself if you know it.",
        "intermediate": "No tonic was given, and the evidence in the audio was too weak to pick one with confidence. Relative note names depend on the tonic, so an assumed tonic makes every name provisional.",
        "advanced": "Tonic detection did not reach its confidence threshold, so a fallback tonic was used. Every degree label is conditional on it, and a wrong tonic appears as a consistent rotation of the degree names.",
        "try": ["Listen for the note the melody keeps returning to and resting on, and set that as the tonic.",
                "If there is a drone or a final long note, match its pitch and use it as the tonic."],
    },
    "tuning_offset": {
        "label": "recording tuned off standard pitch",
        "beginner": "The whole recording sits a little above or below standard concert pitch. That is normal, since many performers choose their own pitch. Orin reports the difference and does not change the music.",
        "intermediate": "The recording's reference pitch differs from the standard one. Orin measures notes relative to the tonic, so this does not affect the notation; it only matters if you play along on a fixed-pitch instrument.",
        "advanced": "A global offset from the standard reference was estimated and reported. Pitches are analysed relative to the detected tonic, so the offset does not enter the degree assignment or the fidelity score.",
        "try": ["If you play along on a keyboard, use its tuning control to move it by the reported amount.",
                "Tune a string instrument to the recording's tonic instead of to a tuner."],
    },
}

# Neutral ornament kinds. The qualifier comes from the item's explain key, so naming it is grounded.
_ORNAMENT_KIND: dict[str, dict[str, str]] = {
    "oscillation": {
        "beginner": "An oscillation is a slow, wide rocking of the pitch around a note.",
        "intermediate": "An oscillation is a repeated and fairly wide pitch movement around a note, slower and wider than a vibrato; in several traditions it is part of the note itself.",
        "advanced": "Oscillation is Orin's neutral word for a periodic contour too wide or too slow to be classed as vibrato; a tradition's own name is applied only by that tradition's adapter.",
        "try": "Hold the written note and rock the pitch slowly up and down, wider than a normal vibrato.",
    },
    "glide": {
        "beginner": "A glide is a smooth slide from one pitch to another.",
        "intermediate": "A glide is a continuous slide between two pitches, in which the path between them is heard and not only the two ends.",
        "advanced": "Glide is Orin's neutral word for a monotonic contour between two anchors; its extent and direction are in the item detail.",
        "try": "Slide one finger along a violin string, or use a pitch bend, from the starting pitch to the written note.",
    },
    "vibrato": {
        "beginner": "A vibrato is a small, quick wobble of the pitch around a held note.",
        "intermediate": "A vibrato is a narrow, regular and fairly fast pitch wobble centred on the note, usually heard as tone colour more than as melody.",
        "advanced": "Vibrato is Orin's neutral word for a periodic contour that is narrow and fast and centred on the anchor pitch.",
        "try": "Hold the note steady first, then add a gentle, even wobble.",
    },
    "grace": {
        "beginner": "A grace is a very short note that touches a neighbouring pitch just before or after the main note.",
        "intermediate": "A grace is a brief touch of another pitch at the edge of a note, too short to count as a note of its own.",
        "advanced": "Grace is Orin's neutral word for a short excursion at a note boundary whose duration is below the minimum for a separate event.",
        "try": "Flick a quick neighbouring note just before the written note, so fast that it takes no time of its own.",
    },
}

_UNKNOWN = {
    "label": "something the notation could not carry",
    "beginner": "This item marks something in the performance that the target notation could not carry over as it was.",
    "intermediate": "This item marks a detail of the source that the target notation could not represent exactly.",
    "advanced": "This item records a detail of the source that the target format could not represent; Orin has no specific explanation for this code.",
    "try": ["Compare the original with the notation at this spot."],
}


def _level(level: Optional[str]) -> str:
    return level if level in LEVELS else "beginner"


def explanation_for(key: str, level: str = "beginner") -> tuple[str, list[str]]:
    """(why text, try-this list) for an explain key. Falls back from "code.qualifier" to "code"."""
    level = _level(level)
    key = (key or "").strip().lower()
    entry = _EXPLAIN.get(key)
    if entry is not None:
        return entry[level], list(entry["try"][:2])
    code = base_key(key)
    qualifier = key.split(".", 1)[1] if "." in key else ""
    base = _EXPLAIN.get(code, _UNKNOWN)
    if code in ("ornament_dropped", "ornament_approximated") and qualifier in _ORNAMENT_KIND:
        kind = _ORNAMENT_KIND[qualifier]
        return f"{kind[level]} {base[level]}", [kind["try"], base["try"][0]]
    return base[level], list(base["try"][:2])


def label_for(key: str) -> str:
    key = (key or "").strip().lower()
    entry = _EXPLAIN.get(key) or _EXPLAIN.get(base_key(key))
    return entry["label"] if entry else base_key(key).replace("_", " ")


def explain_keys() -> tuple[str, ...]:
    """Every key with its own text: all codes, plus the qualified versions."""
    qualified = [f"{code}.{kind}" for code in ("ornament_dropped", "ornament_approximated") for kind in _ORNAMENT_KIND]
    return tuple(sorted({*_EXPLAIN, *qualified}))


def _digest(text: str, n: int = 10) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def selected_items(report: LossReport, item_indexes: Sequence[int]) -> list[Any]:
    return [report.items[i] for i in item_indexes or () if isinstance(i, int) and 0 <= i < len(report.items)]


def explain_cache_key(report: LossReport, item_indexes: Sequence[int], level: str, language: str,
                      question: Optional[str] = None, *, specific: bool = False) -> str:
    """Cache key (spec 9.4): explain key(s) + level + language.

    A whole-report explanation states that report's counts, and a question changes the
    answer, so both add a digest. ``specific`` adds one for text that quotes the items.
    """
    items = selected_items(report, item_indexes)
    if items:
        head = "+".join(_unique([it.explain_key for it in items]))
        if specific:
            head += ":" + _digest("|".join(it.model_dump_json() for it in items))
    else:
        head = "report:" + _digest(report.model_dump_json())
    key = f"{head}|{_level(level)}|{language or 'en'}"
    if question and question.strip():
        key += "|q:" + _digest(question.strip().lower(), 8)
    return key


def _format_label(meta: Optional[Any], fallback: Optional[str]) -> str:
    if meta is not None:
        return str(meta.display_name)
    return genre_name(fallback) if fallback else "the source"


def _counts(report: LossReport) -> dict[str, int]:
    counts = {k: int(v) for k, v in (report.summary.counts_by_code or {}).items() if v}
    if not counts:
        for item in report.items:
            counts[item.code] = counts.get(item.code, 0) + 1
    return counts


def _report_text(report: LossReport, source_meta: Optional[Any], target_meta: Optional[Any], level: str) -> tuple[str, list[str]]:
    s = report.summary
    src = _format_label(source_meta, report.source_format)
    tgt = _format_label(target_meta, report.target_format)
    lines: list[str] = []
    tries: list[str] = []

    score = f"Going from {src} to {tgt}, the fidelity score is {s.fidelity_score:.2f} on a scale from zero to one"
    if level == "beginner":
        lines.append(score + "; the closer to one, the more of the performance the notation kept.")
    else:
        lines.append(score + f" (pitch {s.pitch_score:.2f}, ornament {s.ornament_score:.2f}, timing {s.timing_score:.2f}).")
    if level == "advanced":
        lines.append("The score adds up what each note lost, weighted by how long the note lasts; "
                     "the weights are Orin's design choice, not a measurement.")

    counts = _counts(report)
    loss_counts = sorted(((c, n) for c, n in counts.items() if c not in NOTICE_CODES), key=lambda kv: (-kv[1], kv[0]))
    if not loss_counts and not report.items:
        lines.append("Nothing was lost in this conversion: the target notation could write everything Orin found.")
    else:
        lines.append("What changed, by kind: " + ", ".join(f"{label_for(c)} ({n})" for c, n in loss_counts) + ".")
        for code, _n in loss_counts[:2]:
            # Use the qualified key only when every item of this code shares it.
            keys = {it.explain_key for it in report.items if it.code == code}
            key = keys.pop() if len(keys) == 1 else code.lower()
            why, try_this = explanation_for(key, level)
            lines.append(f"{label_for(key).capitalize()}: {why}")
            tries.extend(try_this[:1])
    if abs(s.max_cents_delta) > 0:
        lines.append(f"The largest pitch change was {abs(s.max_cents_delta):.0f} cents.")

    if report.notices:
        notice_counts: dict[str, int] = {}
        for n in report.notices:
            notice_counts[n.code] = notice_counts.get(n.code, 0) + 1
        listed = ", ".join(f"{label_for(c)} ({k})" for c, k in sorted(notice_counts.items(), key=lambda kv: (-kv[1], kv[0])))
        lines.append("Orin also noted its own assumptions and doubts, which do not count as loss: " + listed + ".")

    status = getattr(target_meta, "review_status", None)
    if target_meta is not None and status != "practitioner_reviewed":
        lines.append(f"Orin's {tgt} tables are a draft that no practitioner has reviewed yet, and transcription is approximate.")
    if not tries:
        tries = ["Play the original and the notation one after the other and listen for what differs."]
    return "\n\n".join(lines), _unique(tries)[:2]


def explain_loss(report: LossReport, *, item_indexes: Sequence[int] = (), source_meta: Optional[Any] = None,
                 target_meta: Optional[Any] = None, level: str = "beginner", question: Optional[str] = None,
                 language: str = "en") -> ExplanationResult:
    """Template explanation. With ``item_indexes`` it explains those kinds of loss (the text is
    the same for every item with that explain key); without, it explains the whole report from
    the report's own counts and largest values."""
    level = _level(level)
    items = selected_items(report, item_indexes)
    if items:
        keys = _unique([it.explain_key for it in items])[:3]
        blocks: list[str] = []
        tries: list[str] = []
        for key in keys:
            why, try_this = explanation_for(key, level)
            blocks.append(why if len(keys) == 1 else f"{label_for(key).capitalize()}: {why}")
            tries.extend(try_this)
        text = "\n\n".join(blocks)
        tries = _unique(tries)[:2]
    else:
        text, tries = _report_text(report, source_meta, target_meta, level)
    extras: list[str] = []
    if question and question.strip():
        extras.append("Orin's offline stand-in cannot answer a specific question, so this is the general explanation.")
    if (language or "en").lower() not in ("en", "en-us", "en-gb"):
        extras.append("The offline stand-in writes in English only.")
    if extras:
        text = " ".join(extras) + "\n\n" + text
    return ExplanationResult(text=text, try_this=tries, source="offline",
                             key=explain_cache_key(report, item_indexes, level, language, question))


# ---------------------------------------------------------------------------
# Explain this music
# ---------------------------------------------------------------------------

def explain_music(spec: MusicSpec, *, level: str = "beginner") -> str:
    level = _level(level)
    lines: list[str] = []
    meta = _meta_for(spec.genre_id)
    name = genre_name(spec.genre_id)
    if spec.genre_id == "fusion":
        lines.append("This piece was requested as a fusion of more than one style.")
    elif meta is not None:
        lines.append(f"This piece was requested in the manner of {name}. {meta.description}")
    else:
        lines.append(f"This piece was requested as {name}.")
    if len(spec.sections) > 1:
        parts = "; ".join(f"part {i} ({genre_name(s.genre_id)}, about {s.duration_s} seconds): {s.description}"
                          for i, s in enumerate(spec.sections, start=1))
        lines.append(f"It has {len(spec.sections)} parts. {parts[0].upper() + parts[1:]}.")
        lines.append("Listen for the moment the style changes, and for what stays the same across it.")
    elif spec.sections:
        lines.append(f"Requested as: {spec.sections[0].description}.")
    if spec.tempo_bpm:
        lines.append(f"The tempo asked for is about {spec.tempo_bpm} beats per minute.")
    if spec.instruments:
        lines.append("Instruments asked for: " + ", ".join(spec.instruments) + ".")
    if spec.mood:
        lines.append("Mood asked for: " + ", ".join(spec.mood) + ".")
    if spec.mode_hint:
        lines.append(f"The mode hint was {spec.mode_hint}. It is a hint to the generator, not a guarantee that the result follows that mode.")
    ornamented = [genre_name(s.genre_id) for s in spec.sections if getattr(_meta_for(s.genre_id), "expects_ornaments", False)]
    if ornamented:
        lines.append(f"In {_unique(ornamented)[0]} music the pitch typically moves on and between notes; listen for slides and oscillations instead of fixed steps.")
    if level != "beginner" and meta is not None and meta.caveats:
        lines.append("Orin's notes on this tradition: " + " ".join(meta.caveats[:2]))
    lines.append("Generated music is inspired by a style; it is not a performance by a practitioner of that tradition. "
                 "To see the actual notes, send the track to the Notation Studio.")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Music stand-in
# ---------------------------------------------------------------------------

_FALLBACK_DEGREES = (0.0, 200.0, 400.0, 700.0, 900.0)  # a plain five-note scale, for genres Orin has no table for


def _tonic_hz(key_or_tonic: Optional[str]) -> float:
    if key_or_tonic:
        m = re.match(r"^\s*([A-G])([#b]?)", key_or_tonic)
        if m:
            hz = _NOTE_HZ[m.group(1)]
            return hz * (2 ** (1 / 12) if m.group(2) == "#" else 2 ** (-1 / 12) if m.group(2) == "b" else 1.0)
    return 220.0


def _section_contour(degrees: Sequence[float], dur_s: float, beat_s: float, ornament: bool):
    """Up the mode and back down, repeated to fill ``dur_s``. Returns cents at the synth hop."""
    import numpy as np

    from ..eval import synth

    up = list(degrees) + [1200.0]
    line = up + up[-2::-1]  # ... top, then back down to the tonic
    long_at = {len(up) - 1, len(line) - 1}
    n_frames = max(1, int(round(dur_s / synth.HOP_S)))
    parts: list[Any] = []
    frames = 0
    while frames < n_frames:
        for i, cents in enumerate(line):
            long_note = i in long_at
            note_s = beat_s * (2.0 if long_note else 1.0)
            if long_note and ornament:
                part = synth.oscillation(cents, 45.0, 3.0, note_s)  # wide and slow: clearly not a vibrato
            else:
                part = synth.const(cents, note_s)
            gap = synth.silence(0.03)
            parts += [part, gap]
            frames += len(part) + len(gap)
            if frames >= n_frames:
                break
    contour = np.concatenate(parts)[:n_frames]
    tail = min(len(contour), int(round(0.08 / synth.HOP_S)))
    if tail and len(contour) > 2 * tail:
        contour[-tail:] = np.nan  # a short breath before the next section
    return contour


def compose_music(spec: MusicSpec, *, request_id: str, reason: Optional[str] = None,
                  notes: Sequence[str] = ()) -> MusicResult:
    """A labelled placeholder clip: one synthesised line per section, never longer than 30 s."""
    import numpy as np
    import soundfile as sf

    from ..eval import synth

    total_s = float(max(1, min(int(spec.duration_s or DEFAULT_DURATION_S), STANDIN_MAX_S)))
    sections = list(spec.sections) or [SpecSection(genre_id=spec.genre_id, description=spec.genre_id, duration_s=int(total_s))]
    weight = sum(max(s.duration_s, 1) for s in sections)
    tonic = _tonic_hz(spec.key_or_tonic)
    beat_s = max(0.25, min(1.0, 60.0 / spec.tempo_bpm)) if spec.tempo_bpm else 0.5

    out_notes = ["Stand-in clip: a simple up-and-down line synthesised by Orin. It is a placeholder to keep the flow "
                 "working, not generated music and not a performance in any tradition."]
    audio: list[Any] = []
    for index, section in enumerate(sections, start=1):
        dur = total_s * max(section.duration_s, 1) / weight
        meta = _meta_for(section.genre_id)
        ornament = False
        if meta is not None and meta.mode_table:
            mode = meta.mode(spec.mode_hint) or meta.mode_table[0]
            degrees = [d.cents for d in mode.ascending]
            ornament = bool(meta.expects_ornaments)
            out_notes.append(f"Part {index} steps through the notes of {mode.label} from Orin's {meta.review_status} "
                             f"{meta.display_name} table.")
        else:
            degrees = list(_FALLBACK_DEGREES)
            out_notes.append(f'Part {index}: Orin has no table for "{section.genre_id}", so a plain five-note scale is used.')
        y = synth.render(_section_contour(degrees, dur, beat_s, ornament), tonic, sr=STANDIN_SR, amp=0.4)
        if meta is not None and meta.family == "indian_classical":
            y = synth.mix(y, synth.drone([tonic / 2.0, tonic * 0.75], dur, sr=STANDIN_SR, amp=0.05))
        audio.append(y)

    y = np.concatenate(audio) if audio else np.zeros(0)
    n = int(round(total_s * STANDIN_SR))
    y = np.pad(y, (0, max(0, n - len(y))))[:n]
    fade = min(len(y) // 2, int(0.02 * STANDIN_SR))
    if fade:
        y[:fade] *= np.linspace(0.0, 1.0, fade)
        y[-fade:] *= np.linspace(1.0, 0.0, fade)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0.89:
        y = y / peak * 0.89
    buf = io.BytesIO()
    sf.write(buf, y, STANDIN_SR, format="WAV", subtype="PCM_16")

    if (spec.duration_s or 0) > STANDIN_MAX_S:
        out_notes.append(f"The stand-in is capped at {STANDIN_MAX_S} seconds; the full {spec.duration_s} seconds needs the music provider.")
    return MusicResult(
        audio=buf.getvalue(), media_type="audio/wav", ext="wav", standin=True,
        standin_reason=(reason or "Music generation is not connected.").strip(),
        provider=ProviderMeta(name="offline", model=None, request_id=request_id, duration_s=round(len(y) / STANDIN_SR, 3)),
        notes=_unique([*notes, *out_notes]),
    )


def narrate(text: str, *, request_id: str, message: Optional[str] = None) -> NarrationResult:
    message = (message or "Narration audio is not connected.").strip()
    if "browser" not in message:
        message += " Your browser's own voice reads the text instead."
    return NarrationResult(
        audio=None, media_type=None, ext=None, standin=True, message=message,
        provider=ProviderMeta(name="offline", model=None, request_id=request_id),
    )


__all__ = [
    "LEVELS", "classify_intent", "refine_spec", "chat_reply", "chat_reply_chunks",
    "chunk_text", "summarize", "explanation_for", "explain_keys", "explain_loss", "explain_cache_key",
    "explain_music", "compose_music", "narrate", "find_genres", "find_instruments", "find_moods",
    "genre_lexicon", "genre_name", "is_edit", "selected_items", "label_for",
]
