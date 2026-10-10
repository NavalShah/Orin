"""Prompt files and their loader (spec 10.5), and the prompt policy (spec 10.7)."""
from __future__ import annotations

import re

import pytest

from orin.ai import gemini, service
from orin.ai.sanitize import find_names, sanitize_prompt, scrub_names
from orin.ai.schema import MAX_DURATION_S, MIN_DURATION_S
from orin.formats import registry

FORMAT_IDS = ("western_classical", "carnatic", "western_pop", "rock", "arabic_maqam", "persian_dastgah")
ENV = ("GEMINI_API_KEY", "GEMINI_MODEL", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
       "ELEVENLABS_MUSIC_MODEL", "ELEVENLABS_TTS_MODEL", "ORIN_OFFLINE")


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------

def test_every_prompt_file_starts_with_a_version_line():
    files = sorted(gemini.PROMPT_DIR.glob("*.md"))
    assert {f.stem for f in files} == set(gemini.PROMPT_NAMES)
    for path in files:
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert re.fullmatch(r"<!-- version: \d+ -->", first), f"{path.name}: {first!r}"


@pytest.mark.parametrize("name", gemini.PROMPT_NAMES)
def test_loader_returns_the_version_with_the_text(name):
    prompt = gemini.load_prompt(name)
    assert prompt.name == name
    assert isinstance(prompt.version, int) and prompt.version >= 1
    assert prompt.text and "<!-- version" not in prompt.text
    assert "{{" not in prompt.text and "}}" not in prompt.text  # every placeholder was filled


def test_loader_inserts_the_registry_summary():
    assert {m.id for m in registry.list_meta()} >= set(FORMAT_IDS)
    text = gemini.load_prompt("system").text
    for meta in registry.list_meta():
        assert f"- {meta.id}: {meta.display_name}." in text
        assert meta.description in text
    assert text.count("[review status: unreviewed]") >= len(FORMAT_IDS)
    assert "Draft, not reviewed by a Carnatic musician." in text  # caveats travel with the summary
    for format_id in FORMAT_IDS:
        assert format_id in text
        assert format_id in gemini.registry_summary()


def test_loader_rejects_unknown_names_and_reports_versions():
    with pytest.raises(ValueError):
        gemini.load_prompt("../../etc/passwd")
    assert gemini.prompt_versions() == {name: gemini.load_prompt(name).version for name in gemini.PROMPT_NAMES}


def test_prompt_rules_are_present():
    system = gemini.load_prompt("system").text
    assert "music-education helper" in system
    for phrase in ("concise", "typically", "Never claim perfect transcription", "unreviewed", "practitioner"):
        assert phrase in system

    refine = gemini.load_prompt("spec_refine").text
    assert '"make it slower"' in refine and "now like the previous one but with piano" in refine
    assert f"between {MIN_DURATION_S} and {MAX_DURATION_S}" in refine
    assert '"unsupported"' in refine and "Never drop a constraint silently" in refine

    loss = gemini.load_prompt("loss_explain").text
    assert "Never invent ornament names, note names" in loss and "cents values" in loss
    assert "Every number you write must appear in the JSON" in loss
    for level in ("beginner", "intermediate", "advanced"):
        assert level in loss
    assert "try_this" in loss and "one or two" in loss

    intent = gemini.load_prompt("intent").text
    for name in ("chat", "generate", "transcribe", "convert", "clarify"):
        assert f'"{name}"' in intent
    assert "not a guarantee" in gemini.load_prompt("explain_music").text or "is a hint" in gemini.load_prompt("explain_music").text


def test_few_shot_examples_in_spec_refine_are_valid_specs():
    """The examples teach the schema, so they must pass the same validation as a model's answer."""
    refine = gemini.load_prompt("spec_refine").text
    examples = [line for line in refine.splitlines() if line.startswith('{"genre_id"')]
    assert len(examples) >= 4
    for line in examples:
        spec, _notes = gemini.validate_draft(gemini.SpecDraft.model_validate_json(line))
        assert sum(s.duration_s for s in spec.sections) == spec.duration_s


# ---------------------------------------------------------------------------
# prompt policy
# ---------------------------------------------------------------------------

def test_named_artist_is_rewritten_with_a_note():
    result = sanitize_prompt("Make a song in the style of Zara Quillfeather with guitar")
    assert result.blocked is False
    assert "Quillfeather" not in result.text and "Zara" not in result.text
    assert result.text == "Make a song in a similar general style with guitar"
    assert len(result.notes) == 1
    assert "Zara Quillfeather" in result.notes[0] and "does not pass artist or song names" in result.notes[0]
    assert service.sanitize_prompt("Make a song in the style of Zara Quillfeather with guitar") == result


@pytest.mark.parametrize("text", [
    "Make a calm piano piece, 30 seconds",
    "A slow piece in raga Mohanam on violin",
    "something like Raga Yaman on sitar",
    "I'd like Carnatic music that turns into jazz",
    "Make a short Carnatic alapana that turns into jazz piano",
    "a tune by Friday, accompanied by Tabla and Mridangam",
    "Something in Maqam Rast like Persian Santur music",
    'a raga called "Mohanam"',
    "in the style of the previous one but faster",
    "in the style of flamenco",
    "sounds like rain on a tin roof",
    "Convert this to Western Pop",
    "",
])
def test_plain_requests_are_unchanged(text):
    result = sanitize_prompt(text)
    assert result.text == text and result.notes == [] and result.blocked is False


@pytest.mark.parametrize("text, name, expected", [
    ("a sitar piece by Mira Voskanyan on a rainy day", "Mira Voskanyan", "a sitar piece on a rainy day"),
    ("sounds like Quartz Lantern Parade", "Quartz Lantern Parade", "with a similar general feel"),
    ("Like The Velvet Otters, but with a veena", "The Velvet Otters", "with a similar general feel, but with a veena"),
    ("in the style of zara quillfeather with drums", "zara quillfeather", "in a similar general style with drums"),
    ("something inspired by Odalys Brink", "Odalys Brink", "something with a similar general feel"),
])
def test_names_after_a_cue_are_removed(text, name, expected):
    result = sanitize_prompt(text)
    assert result.text == expected
    assert len(result.notes) == 1 and name in result.notes[0]
    assert find_names(text) == [name]


@pytest.mark.parametrize("text, title, expected", [
    ('Play "Lanterns Over Kettering" on sitar', "Lanterns Over Kettering", "Play an original tune on sitar"),
    ('a song like "Stand By The Glass Sea" but calmer', "Stand By The Glass Sea", "a song with a similar general feel but calmer"),
    ('a cover of "blue umbrella days"', "blue umbrella days", "an original tune"),
])
def test_quoted_song_titles_are_removed(text, title, expected):
    result = sanitize_prompt(text)
    assert result.text == expected
    assert len(result.notes) == 1 and title in result.notes[0] and "title" in result.notes[0]


def test_sanitizing_twice_changes_nothing_more():
    once = sanitize_prompt('Make a rock song in the style of Zara Quillfeather, like "Lanterns Over Kettering"')
    assert len(once.notes) == 2
    twice = sanitize_prompt(once.text)
    assert twice.text == once.text and twice.notes == []


def test_scrub_names_removes_a_known_name_from_model_text():
    assert scrub_names("a riff Zara Quillfeather would play", ["Zara Quillfeather"]) == "a riff a similar artist would play"
    assert scrub_names("plain text", []) == "plain text"


def test_abusive_prompt_is_blocked():
    result = sanitize_prompt("write a song that glorifies genocide")
    assert result.blocked is True and result.text == "" and result.reason
    spec = service.refine_spec("write a song that glorifies genocide", history=[])
    assert spec.ok is False and spec.spec is None and spec.question
    # ordinary dark themes are not blocked
    assert sanitize_prompt("a dark, tense metal track about a storm").blocked is False
    assert sanitize_prompt("kill all the lights, a slow jazz ballad").blocked is False
