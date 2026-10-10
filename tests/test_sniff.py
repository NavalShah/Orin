"""Content sniffing (spec 7.1): the bytes decide, the file name does not."""
import dataclasses

import pytest

from orin.ingest.sniff import Sniffed, decode_text, sniff

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 16
MP3_ID3 = b"ID3\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 16
MP3_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 32
FLAC = b"fLaC\x00\x00\x00\x22" + b"\x00" * 16
OGG = b"OggS\x00\x02" + b"\x00" * 20
M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A mp42isom"
MIDI = b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0MTrk\x00\x00\x00\x04\x00\xff\x2f\x00"
MUSICXML = b'<?xml version="1.0" encoding="UTF-8"?>\n<score-partwise version="3.1"><part-list/></score-partwise>'
TIMEWISE = b"<score-timewise><part-list/></score-timewise>"
MXL = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\x00" * 20
ABC = b"X:1\nT:Tune\nM:4/4\nK:D\nD E F G|\n"
SARGAM = "S R2 G3 P | D2 S' , ,\n".encode()
BINARY = bytes(range(0, 32)) * 4


@pytest.mark.parametrize("data, kind, subtype, media_type", [
    (WAV, "audio", "wav", "audio/wav"),
    (MP3_ID3, "audio", "mp3", "audio/mpeg"),
    (MP3_FRAME, "audio", "mp3", "audio/mpeg"),
    (FLAC, "audio", "flac", "audio/flac"),
    (OGG, "audio", "ogg", "audio/ogg"),
    (M4A, "audio", "m4a", "audio/mp4"),
    (MIDI, "notes", "midi", "audio/midi"),
    (MUSICXML, "notes", "musicxml", "application/vnd.recordare.musicxml+xml"),
    (TIMEWISE, "notes", "musicxml", "application/vnd.recordare.musicxml+xml"),
    (MXL, "notes", "mxl", "application/vnd.recordare.musicxml"),
    (ABC, "notes", "abc", "text/vnd.abc"),
    (SARGAM, "notes", "text", "text/plain"),
    (BINARY, "unknown", "unknown", "application/octet-stream"),
    (b"", "unknown", "unknown", "application/octet-stream"),
    (b"   \n\t ", "unknown", "unknown", "application/octet-stream"),
])
def test_sniff_by_content(data, kind, subtype, media_type):
    assert sniff(data) == Sniffed(kind=kind, subtype=subtype, media_type=media_type)


@pytest.mark.parametrize("data, filename, subtype", [
    (WAV, "song.mp3", "wav"),
    (MP3_ID3, "song.wav", "mp3"),
    (FLAC, "take.ogg", "flac"),
    (MIDI, "notes.txt", "midi"),
    (MUSICXML, "score.mid", "musicxml"),
    (ABC, "tune.wav", "abc"),
    (SARGAM, "phrase.wav", "text"),
    (SARGAM, "phrase.abc", "text"),  # nothing ABC-like inside, so the name changes nothing
    (BINARY, "song.wav", "unknown"),
])
def test_wrong_extension_does_not_fool_it(data, filename, subtype):
    assert sniff(data, filename).subtype == subtype


def test_extension_only_breaks_a_tie_for_plain_text():
    tune = b"M:4/4\nL:1/4\nK:D\nD E F G|\n"  # no X: line
    assert sniff(tune).subtype == "text"
    assert sniff(tune, "tune.txt").subtype == "text"
    assert sniff(tune, "tune.abc").subtype == "abc"
    assert sniff(tune, "TUNE.ABC").subtype == "abc"


def test_text_is_not_mistaken_for_audio():
    # A UTF-16 byte-order mark starts with FF FE, which would pass a loose MPEG frame check.
    assert sniff("S R2 G3 P".encode("utf-16")).subtype == "text"
    assert sniff("﻿X:1\nK:C\nCDEF|\n".encode("utf-8")).subtype == "abc"
    assert sniff(b"ID").subtype == "text"  # two letters, not an ID3 tag


def test_other_ff_headers_are_not_mp3():
    assert sniff(b"\xff\xf1\x50\x80" + b"\x00" * 16).kind == "unknown"  # ADTS AAC: layer bits are 00
    assert sniff(b"\xff\xff\xff\xff" + b"\x00" * 16).kind == "unknown"  # reserved bitrate and sample rate


def test_xml_that_is_not_a_score_is_text():
    assert sniff(b"<?xml version='1.0'?><html><body>hi</body></html>").subtype == "text"


def test_sniffed_is_frozen_and_never_raises():
    found = sniff(WAV)
    with pytest.raises(dataclasses.FrozenInstanceError):
        found.kind = "notes"  # type: ignore[misc]
    for size in range(0, 14):
        assert sniff(WAV[:size]).kind in ("audio", "notes", "unknown")
        assert sniff(MIDI[:size]).kind in ("audio", "notes", "unknown")


def test_decode_text():
    assert decode_text(b"S R G") == "S R G"
    assert decode_text("﻿S R G".encode("utf-8")) == "S R G"
    assert decode_text("Ṡ Ṛ".encode("utf-16")) == "Ṡ Ṛ"
    assert decode_text("S R2 \u1e60".encode("utf-8")[:-1]) == "S R2 "  # a file head cut inside a character
    assert sniff(("S R2 G3 " * 8 + "\u1e60").encode("utf-8")[:-1]).subtype == "text"
    assert decode_text(b"\x00\x01\x02") is None
    assert decode_text(b"\xff\xfb\x90\x00") is None
