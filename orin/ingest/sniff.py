"""Content sniffing (spec 7.1): decide what an upload is from its bytes, not its name.

The extension is only used as a tie-breaker for plain text (an ABC tune with no ``X:``
line saved as ``.abc``). Everything else is decided by magic numbers or by structure.
"""
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from typing import Optional

AUDIO_SUBTYPES = ("wav", "mp3", "flac", "ogg", "m4a")
NOTES_SUBTYPES = ("midi", "musicxml", "mxl", "abc", "text")

MEDIA_TYPES: dict[str, str] = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "midi": "audio/midi",
    "musicxml": "application/vnd.recordare.musicxml+xml",
    "mxl": "application/vnd.recordare.musicxml",
    "abc": "text/vnd.abc",
    "text": "text/plain",
    "unknown": "application/octet-stream",
}

_ABC_X = re.compile(r"^X:\s*\d*\s*$", re.M)
_ABC_K = re.compile(r"^K:", re.M)
_XML_SCORE = re.compile(r"<\s*score-(partwise|timewise)\b")


@dataclass(frozen=True)
class Sniffed:
    kind: str  # "audio" | "notes" | "unknown"
    subtype: str  # wav mp3 flac ogg m4a | midi musicxml mxl abc text | unknown
    media_type: str


def _make(subtype: str) -> Sniffed:
    if subtype in AUDIO_SUBTYPES:
        kind = "audio"
    elif subtype in NOTES_SUBTYPES:
        kind = "notes"
    else:
        kind = "unknown"
    return Sniffed(kind=kind, subtype=subtype, media_type=MEDIA_TYPES[subtype])


def decode_text(data: bytes) -> Optional[str]:
    """The bytes as text, or None when they are not text.

    UTF-8 (with or without a byte-order mark) and UTF-16 with a byte-order mark are
    accepted. Anything with NUL bytes or other control characters is treated as binary.
    A character cut off at the very end is dropped, so a file's first bytes can be passed.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        encoding, body = "utf-8", data[3:]
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding, body = "utf-16", data
    else:
        encoding, body = "utf-8", data
    try:
        # Incremental, so the first bytes of a file still count as text when the cut
        # falls in the middle of a character.
        text = codecs.getincrementaldecoder(encoding)().decode(body, final=False)
    except (UnicodeDecodeError, ValueError):
        return None
    if any(ord(c) < 32 and c not in "\t\n\r\f" for c in text):
        return None
    return text


def _is_mpeg_frame(data: bytes) -> bool:
    """An MPEG audio frame header: 11 sync bits, then no reserved field values."""
    if len(data) < 3 or data[0] != 0xFF or (data[1] & 0xE0) != 0xE0:
        return False
    version = (data[1] >> 3) & 0x03
    layer = (data[1] >> 1) & 0x03
    bitrate = (data[2] >> 4) & 0x0F
    sample_rate = (data[2] >> 2) & 0x03
    return version != 1 and layer != 0 and bitrate != 0x0F and sample_rate != 3


def looks_like_abc(text: str) -> bool:
    return bool(_ABC_X.search(text) and _ABC_K.search(text))


def looks_like_musicxml(text: str) -> bool:
    return bool(_XML_SCORE.search(text[:200_000]))


def text_subtype(text: str, filename: Optional[str] = None) -> str:
    """musicxml, abc or text for content already known to be text."""
    if looks_like_musicxml(text):
        return "musicxml"
    if looks_like_abc(text):
        return "abc"
    # Tie-breaker only: a tune body with a K: line but no X: line, saved as .abc.
    if extension(filename) == "abc" and _ABC_K.search(text):
        return "abc"
    return "text"


def extension(filename: Optional[str]) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].strip().lower()


def sniff(data: bytes, filename: Optional[str] = None) -> Sniffed:
    """Classify an upload by content. Never raises; unknown content is ``kind="unknown"``."""
    if not data:
        return _make("unknown")
    head = bytes(data[:16])
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return _make("wav")
    if head[:4] == b"fLaC":
        return _make("flac")
    if head[:4] == b"OggS":
        return _make("ogg")
    if head[4:8] == b"ftyp":
        return _make("m4a")
    if head[:4] == b"MThd":
        return _make("midi")
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return _make("mxl")
    if head[:3] == b"ID3":
        return _make("mp3")
    # Text is tried before the MPEG frame sync: a UTF-16 byte-order mark (FF FE) would
    # otherwise pass for a frame header.
    text = decode_text(bytes(data))
    if text is not None:
        if not text.strip():
            return _make("unknown")
        return _make(text_subtype(text, filename))
    if _is_mpeg_frame(head):
        return _make("mp3")
    return _make("unknown")
