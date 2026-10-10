"""The AI layer with no keys: every step answers from the offline stand-ins (spec D11)."""
from __future__ import annotations

import io
import re

import numpy as np
import pytest
import soundfile as sf

from orin.ai import config, elevenlabs, gemini, offline, service
from orin.ai.schema import MAX_DURATION_S, MIN_DURATION_S, MusicSpec, SpecSection
from orin.ai.types import Turn
from orin.formats import registry
from orin.loss import LOSS_CODES, NOTICE_CODES, LossItem, LossReport, LossSummary, Notice, explain_key, human_text

ENV = ("GEMINI_API_KEY", "GEMINI_MODEL", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
       "ELEVENLABS_MUSIC_MODEL", "ELEVENLABS_TTS_MODEL", "ORIN_OFFLINE")
DEMO = "Make a short Carnatic alapana that turns into jazz piano"


@pytest.fixture(autouse=True)
def no_keys_no_network(monkeypatch):
    """Clear the AI environment and make any attempt to build a provider client fail."""
    for name in ENV:
        monkeypatch.delenv(name, raising=False)

    def refuse(_key):
        raise AssertionError("an offline test tried to build a provider client")

    monkeypatch.setattr(gemini, "make_client", refuse)
    monkeypatch.setattr(elevenlabs, "make_client", refuse)
    elevenlabs.reset()


def numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


def sample_report() -> LossReport:
    target = "Western (staff notation)"
    items = (
        LossItem(code="MICROTONE_ROUNDED", severity="minor", event_id="e1", time_s=1.25,
                 detail={"cents_delta": -38.0, "target": target, "written": "E"},
                 human_text=human_text("MICROTONE_ROUNDED", cents_delta=-38.0, target=target, written="E"),
                 explain_key=explain_key("MICROTONE_ROUNDED", "12tet"), penalty=0.02),
        LossItem(code="ORNAMENT_DROPPED", severity="major", event_id="e2", time_s=2.5,
                 detail={"amplitude_cents": 85.0, "rate_hz": 4.0, "kind": "oscillation"},
                 human_text=human_text("ORNAMENT_DROPPED", amplitude_cents=85.0, rate_hz=4.0, target=target),
                 explain_key=explain_key("ORNAMENT_DROPPED", "oscillation"), penalty=0.07),
        LossItem(code="ORNAMENT_DROPPED", severity="major", event_id="e3", time_s=3.5,
                 detail={"amplitude_cents": 60.0, "kind": "glide"},
                 human_text=human_text("ORNAMENT_DROPPED", amplitude_cents=60.0, target=target),
                 explain_key=explain_key("ORNAMENT_DROPPED", "glide"), penalty=0.05),
        LossItem(code="TIMING_SHIFT", severity="info", event_id="e4", time_s=4.0,
                 detail={"shift_ms": 45.0, "grid": "sixteenth-note"},
                 human_text=human_text("TIMING_SHIFT", shift_ms=45.0, grid="sixteenth-note"),
                 explain_key=explain_key("TIMING_SHIFT"), penalty=0.01),
    )
    notices = (
        Notice(code="TONIC_ASSUMED", detail={"tonic": "D"}, human_text=human_text("TONIC_ASSUMED", tonic="D"),
               explain_key=explain_key("TONIC_ASSUMED")),
    )
    summary = LossSummary(counts_by_code={"MICROTONE_ROUNDED": 1, "ORNAMENT_DROPPED": 2, "TIMING_SHIFT": 1},
                          max_cents_delta=38.0, fidelity_score=0.81, pitch_score=0.93, ornament_score=0.62,
                          timing_score=0.97)
    return LossReport(items=items, notices=notices, summary=summary, source_format="carnatic",
                      target_format="western_classical")


def allowed_numbers(report: LossReport) -> set[str]:
    out = set(numbers(report.to_text())) | set(numbers(report.model_dump_json()))
    out |= {str(n) for n in report.summary.counts_by_code.values()}
    out |= {str(len(report.items)), str(len(report.notices))}
    return out


# ---------------------------------------------------------------------------
# status and config
# ---------------------------------------------------------------------------

def test_status_with_no_keys_says_why():
    st = service.status()
    assert st.llm_offline and st.music_offline and st.tts_offline
    assert "GEMINI_API_KEY" in st.reason and "ELEVENLABS_API_KEY" in st.reason
    assert st.llm_model is None and st.music_model is None and st.tts_model is None


def test_key_without_model_uses_the_standin_and_says_so(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-value")
    st = service.status()
    assert st.llm_offline
    assert "GEMINI_MODEL" in st.reason
    assert service.classify_intent("What is a raga?", history=[]).source == "offline"


def test_orin_offline_forces_standins_even_with_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-value")
    monkeypatch.setenv("GEMINI_MODEL", "dummy-model")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "dummy-value-2")
    monkeypatch.setenv("ORIN_OFFLINE", "1")
    st = service.status()
    assert st.llm_offline and st.music_offline and st.tts_offline
    assert "ORIN_OFFLINE" in st.reason
    assert st.llm_model is None

    # The autouse fixture makes any client construction raise, so these prove no provider is touched.
    assert service.classify_intent(DEMO, history=[]).source == "offline"
    result = service.refine_spec(DEMO, history=[])
    assert result.ok and result.source == "offline"
    assert "".join(service.chat_reply_stream("What is a raga?", history=[]))
    music = service.compose_music(result.spec, request_id="offline-forced")
    assert music.standin and music.provider.name == "offline" and "ORIN_OFFLINE" in music.standin_reason
    narration = service.narrate("hello", request_id="offline-forced")
    assert narration.standin and narration.audio is None
    assert service.explain_loss(sample_report(), target_meta=registry.get("western_classical").META).source == "offline"


def test_env_is_read_at_call_time(monkeypatch):
    assert config.load().gemini_model == ""
    monkeypatch.setenv("GEMINI_MODEL", "set-later")
    assert config.load().gemini_model == "set-later"
    monkeypatch.setenv("ORIN_OFFLINE", "0")
    assert config.load().offline is False


def test_redact_and_repr_hide_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-value")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "dummy-eleven-value")
    cfg = config.load()
    assert "dummy-gemini-value" not in repr(cfg) and "dummy-eleven-value" not in repr(cfg)
    text = config.redact("failed with dummy-gemini-value and dummy-eleven-value in the URL")
    assert "dummy" not in text and text.count("[redacted]") == 2
    assert config.redact(None) == ""


# ---------------------------------------------------------------------------
# intent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, attachment, genre, expected", [
    (DEMO, False, None, "generate"),
    ("Compose a lo-fi beat", False, None, "generate"),
    ("Can you make me a jazz track?", False, None, "generate"),
    ("generate 30 seconds of upbeat rock music", False, None, "generate"),
    ("make it slower", False, None, "generate"),
    ("now like the previous one but with piano", False, None, "generate"),
    ("something calm", False, "carnatic", "generate"),
    ("a 20 second jazz piano tune", False, None, "generate"),
    ("can I hear something in maqam Rast?", False, None, "generate"),
    ("I want a sad song with violin", False, None, "generate"),
    ("Transcribe this recording", True, None, "transcribe"),
    ("Can you give me the notation for this?", True, None, "transcribe"),
    ("show me the sargam", True, None, "transcribe"),
    ("Convert this Carnatic clip to a western pop arrangement", True, None, "convert"),
    ("Play this as a jazz arrangement", True, None, "convert"),
    ("Make this in the style of rock", True, None, "convert"),
    ("Convert this to rock", False, None, "clarify"),
    ("make a song", False, None, "clarify"),
    ("music", False, None, "clarify"),
    ("", False, None, "clarify"),
    ("?!", False, None, "clarify"),
    ("What is a raga?", False, None, "chat"),
    ("How do I make a jazz song?", False, None, "chat"),
    ("why is it slower?", False, None, "chat"),
    ("hello", False, None, "chat"),
    ("I am learning the veena", False, None, "chat"),
    ("a raga is more than a scale, right?", False, None, "chat"),
    ("thanks!", False, None, "chat"),
])
def test_intent_keyword_rules(text, attachment, genre, expected):
    result = service.classify_intent(text, history=[], has_attachment=attachment, genre_id=genre)
    assert result.intent == expected
    assert result.source == "offline"


# ---------------------------------------------------------------------------
# spec
# ---------------------------------------------------------------------------

def test_demo_request_makes_two_sections():
    result = service.refine_spec(DEMO, history=[])
    assert result.ok and result.source == "offline" and result.question is None
    spec = result.spec
    assert [s.genre_id for s in spec.sections] == ["carnatic", "jazz"]
    assert spec.genre_id == "fusion"
    assert spec.duration_s == 30
    assert sum(s.duration_s for s in spec.sections) == spec.duration_s
    assert "piano" in spec.instruments
    assert spec.vocals == "none"
    assert spec.structure_notes
    # every default Orin filled in is listed
    joined = " ".join(spec.assumptions)
    assert '"short"' in joined and "evenly" in joined and "instrumental" in joined.lower()
    assert set(spec.assumptions) <= set(result.notes)
    assert any("offline stand-in" in n for n in result.notes)


@pytest.mark.parametrize("text, genres", [
    ("a slow carnatic violin line, then rock guitar", ["carnatic", "rock"]),
    ("carnatic veena into jazz piano, one minute", ["carnatic", "jazz"]),
    ("arabic maqam on oud followed by lo-fi beats", ["arabic_maqam", "lo-fi"]),
    ("a calm piano intro that turns into a fast drum solo", ["western_pop", "western_pop"]),
    ("blues that morphs into rock and then into jazz", ["blues", "rock", "jazz"]),
])
def test_change_words_make_sections(text, genres):
    spec = service.refine_spec(text, history=[]).spec
    assert [s.genre_id for s in spec.sections] == genres
    assert sum(s.duration_s for s in spec.sections) == spec.duration_s
    assert all(s.description for s in spec.sections)


def test_into_alone_does_not_split_ordinary_sentences():
    spec = service.refine_spec("make a jazz tune to get me into a good mood", history=[]).spec
    assert len(spec.sections) == 1 and spec.genre_id == "jazz"


def test_spec_reads_words_and_numbers():
    spec = service.refine_spec(
        "a 2 minute slow blues in E minor with guitar and no drums so I can hear the blue notes", history=[]).spec
    assert spec.genre_id == "blues"
    assert spec.duration_s == 120
    assert spec.tempo_bpm == 70 and any("slow" in a for a in spec.assumptions)
    assert spec.key_or_tonic == "E" and spec.mode_hint == "minor"
    assert spec.instruments == ["guitar"]
    assert spec.avoid == ["drums"]
    assert spec.learning_goal == "hear the blue notes"

    spec = service.refine_spec("fast pop song at 128 bpm with lyrics about rain, 45 seconds", history=[]).spec
    assert spec.genre_id == "western_pop"  # a registry alias
    assert spec.tempo_bpm == 128
    assert spec.vocals == "lyrics" and "voice" in spec.instruments
    assert spec.duration_s == 45


@pytest.mark.parametrize("text, seconds", [
    ("a jazz tune, 30 seconds", 30), ("one minute of jazz", 60), ("a short jazz tune", 30),
    ("a 20-second jazz sting", 20), ("jazz for a minute and a half", 90), ("half a minute of jazz", 30),
    ("a jazz tune", 60),
])
def test_spec_duration(text, seconds):
    assert service.refine_spec(text, history=[]).spec.duration_s == seconds


def test_spec_duration_is_limited_and_the_user_is_told():
    result = service.refine_spec("ten minutes of ambient music", history=[])
    assert result.spec.duration_s == MAX_DURATION_S
    assert any(str(MAX_DURATION_S) in a and "600" in a for a in result.spec.assumptions)
    result = service.refine_spec("5 seconds of rock", history=[])
    assert result.spec.duration_s == MIN_DURATION_S


def test_spec_defaults_are_listed_as_assumptions():
    spec = service.refine_spec("make a happy tune", history=[]).spec
    text = " ".join(spec.assumptions)
    assert "No genre named" in text and "No length given" in text and "instrumental" in text
    assert spec.mood == ["happy"]


def test_spec_genre_from_chip_alias_and_mode_name():
    assert service.refine_spec("something calm", history=[], genre_id="arabic_maqam").spec.genre_id == "arabic_maqam"
    assert service.refine_spec("a sangeetham piece on veena", history=[]).spec.genre_id == "carnatic"
    spec = service.refine_spec("a calm piece in raga Mohanam on violin", history=[]).spec
    assert spec.genre_id == "carnatic" and spec.mode_hint == "Mohanam"
    assert any("Mohanam" in a for a in spec.assumptions)
    # "raga" alone does not say which tradition, and a stray word is not taken as a raga name
    spec = service.refine_spec("a slow raga on sitar", history=[]).spec
    assert spec.genre_id == "indian classical" and spec.mode_hint is None


def test_spec_two_named_genres_without_a_change_is_one_fusion_section():
    spec = service.refine_spec("a mix of carnatic and jazz with violin", history=[]).spec
    assert spec.genre_id == "fusion" and len(spec.sections) == 1


def test_multi_turn_edits():
    first = service.refine_spec("an upbeat rock riff with electric guitar, 40 seconds", history=[]).spec
    assert first.tempo_bpm == 120

    slower = service.refine_spec("make it slower", history=[], previous_specs=[first])
    assert slower.ok and slower.spec.tempo_bpm < first.tempo_bpm
    assert slower.spec.genre_id == "rock" and slower.spec.instruments == first.instruments
    assert any("Tempo changed" in a for a in slower.spec.assumptions)

    faster = service.refine_spec("faster please", history=[], previous_specs=[first, slower.spec])
    assert faster.spec.tempo_bpm > slower.spec.tempo_bpm

    piano = service.refine_spec("now like the previous one but with piano", history=[], previous_specs=[first])
    assert piano.spec.instruments == ["electric guitar", "piano"]
    assert piano.spec.tempo_bpm == first.tempo_bpm and piano.spec.duration_s == first.duration_s

    longer = service.refine_spec("longer", history=[], previous_specs=[first])
    assert longer.spec.duration_s == 60
    assert sum(s.duration_s for s in longer.spec.sections) == 60

    other = service.refine_spec("same but as jazz, without the electric guitar", history=[], previous_specs=[first])
    assert other.spec.genre_id == "jazz" and other.spec.sections[0].genre_id == "jazz"
    assert "electric guitar" not in other.spec.instruments and "electric guitar" in other.spec.avoid

    # previous specs may arrive as plain dicts
    as_dict = service.refine_spec("make it 20 seconds", history=[], previous_specs=[first.model_dump()])
    assert as_dict.spec.duration_s == 20


def test_edit_without_a_previous_tempo_states_the_guess():
    first = service.refine_spec(DEMO, history=[]).spec
    assert first.tempo_bpm is None
    slower = service.refine_spec("make it slower", history=[], previous_specs=[first]).spec
    assert slower.tempo_bpm == 70
    assert any("No tempo was set" in a for a in slower.assumptions)
    assert [s.genre_id for s in slower.sections] == ["carnatic", "jazz"]


def test_unrecognised_edit_asks_instead_of_guessing():
    first = service.refine_spec("a jazz tune", history=[]).spec
    result = service.refine_spec("make it wobblier", history=[], previous_specs=[first])
    assert not result.ok and result.spec is None and result.question


def test_a_new_request_ignores_previous_specs():
    first = service.refine_spec("a jazz tune", history=[]).spec
    result = service.refine_spec("make a calm carnatic piece on veena", history=[], previous_specs=[first])
    assert result.spec.genre_id == "carnatic" and result.spec.instruments == ["veena"]


def test_empty_request_asks_a_question():
    result = service.refine_spec("", history=[])
    assert not result.ok and result.question


def test_named_artist_is_removed_before_the_spec_and_noted():
    result = service.refine_spec("Make a rock song in the style of Zara Quillfeather with drums", history=[])
    assert result.ok
    assert "Quillfeather" not in result.spec.model_dump_json()
    assert any("Zara Quillfeather" in n for n in result.notes)


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question, word", [
    ("What is a raga?", "raga"), ("what's a gamaka", "gamaka"), ("Explain maqam", "maqam"),
    ("what is a tala?", "tala"), ("What is a koron?", "koron"), ("What is the loss report?", "loss report"),
])
def test_canned_answers_are_hedged(question, word):
    chunks = list(service.chat_reply_stream(question, history=[]))
    answer = "".join(chunks)
    assert len(chunks) >= 2
    assert word in answer.lower()
    low = answer.lower()
    assert "typically" in low or "approximate" in low
    assert "draft" in low or "not a measurement" in low


def test_what_can_you_do_and_fallback():
    answer = "".join(service.chat_reply_stream("what can you do?", history=[]))
    assert "Orin" in answer and "notation" in answer.lower()

    chunks = list(service.chat_reply_stream("Tell me about the weather in Chennai", history=[Turn(role="user", text="hi")]))
    answer = "".join(chunks)
    assert len(chunks) >= 2
    assert "offline stand-in" in answer
    assert "GEMINI_API_KEY" in answer and "Notation Studio" in answer


def test_vague_requests_get_a_question_not_the_fallback():
    answer = "".join(service.chat_reply_stream("make a song", history=[]))
    assert answer.startswith("What kind of music") and "offline stand-in" not in answer
    answer = "".join(service.chat_reply_stream("Convert this to rock", history=[]))
    assert "attach a track" in answer
    assert "".join(service.chat_reply_stream("", history=[])).startswith("What would you like to do?")


def test_summarize_offline():
    history = [Turn(role="user", text="Make a calm carnatic piece"), Turn(role="assistant", text="Here it is."),
               Turn(role="user", text="make it slower")]
    summary = service.summarize(history)
    assert "calm carnatic piece" in summary and "make it slower" in summary
    assert service.summarize([]) == ""


# ---------------------------------------------------------------------------
# loss explanations
# ---------------------------------------------------------------------------

QUALIFIED = ["microtone_rounded.12tet"] + [f"{code}.{kind}" for code in ("ornament_dropped", "ornament_approximated")
                                            for kind in ("oscillation", "glide", "vibrato", "grace")]


@pytest.mark.parametrize("code", LOSS_CODES + NOTICE_CODES)
def test_every_code_is_explained_at_three_levels(code):
    texts = []
    for level in offline.LEVELS:
        text, tries = offline.explanation_for(explain_key(code), level)
        assert len(text) > 80
        assert 1 <= len(tries) <= 2 and all(len(t) > 20 for t in tries)
        assert numbers(text) == [] and numbers(" ".join(tries)) == []  # no number of its own
        texts.append(text)
    assert len(set(texts)) == 3
    assert explain_key(code) in offline._EXPLAIN  # its own text, not the generic fallback


@pytest.mark.parametrize("key", QUALIFIED)
def test_qualified_keys_have_their_own_text(key):
    for level in offline.LEVELS:
        text, tries = offline.explanation_for(key, level)
        base, _ = offline.explanation_for(key.split(".")[0], level)
        assert text != base
        assert 1 <= len(tries) <= 2
        assert numbers(text) == []
    kind = key.split(".")[1]
    if kind != "12tet":
        assert kind in offline.explanation_for(key, "beginner")[0].lower()


def test_explain_key_falls_back_to_the_bare_code():
    assert offline.explanation_for("timing_shift.swing", "beginner") == offline.explanation_for("timing_shift", "beginner")
    assert offline.explanation_for("ornament_dropped.unheard_of", "advanced") == offline.explanation_for("ornament_dropped", "advanced")
    text, tries = offline.explanation_for("not_a_code", "beginner")
    assert text and tries
    assert offline.explanation_for("TIMING_SHIFT", "no-such-level") == offline.explanation_for("timing_shift", "beginner")


def test_loss_texts_use_neutral_ornament_words_only():
    for key in offline.explain_keys():
        for level in offline.LEVELS:
            text, tries = offline.explanation_for(key, level)
            low = (text + " " + " ".join(tries)).lower()
            for word in ("gamaka", "kampita", "meend", "andolan", "jaru"):
                assert word not in low


@pytest.mark.parametrize("level", offline.LEVELS)
def test_whole_report_explanation_uses_only_the_reports_numbers(level):
    report = sample_report()
    result = service.explain_loss(report, source_meta=registry.get("carnatic").META,
                                  target_meta=registry.get("western_classical").META, level=level)
    assert result.source == "offline"
    allowed = allowed_numbers(report)
    found = numbers(result.text + " " + " ".join(result.try_this))
    assert found, "the summary should quote the report"
    assert [n for n in found if n not in allowed] == []
    assert "0.81" in result.text and "38" in result.text
    assert "(2)" in result.text  # two ornaments dropped: the actual count
    assert 1 <= len(result.try_this) <= 2
    assert "draft" in result.text  # the target table is unreviewed
    assert result.key.startswith("report:") and result.key.endswith(f"|{level}|en")
    # ornament names and note names come from the report or not at all
    for word in ("gamaka", "kampita", "meend"):
        assert word not in result.text.lower()


def test_whole_report_explanations_differ_by_level_and_report():
    report = sample_report()
    meta = registry.get("western_classical").META
    texts = {level: service.explain_loss(report, target_meta=meta, level=level) for level in offline.LEVELS}
    assert len({t.text for t in texts.values()}) == 3
    assert len({t.key for t in texts.values()}) == 3
    empty = LossReport(target_format="western_classical")
    result = service.explain_loss(empty, target_meta=meta)
    assert "Nothing was lost" in result.text and result.try_this
    assert result.key != texts["beginner"].key


def test_item_explanations_are_generic_per_key_and_cacheable():
    report = sample_report()
    meta = registry.get("western_classical").META
    one = service.explain_loss(report, item_indexes=(1,), target_meta=meta, level="beginner")
    assert one.key == "ornament_dropped.oscillation|beginner|en"
    assert "oscillation" in one.text.lower()
    assert numbers(one.text) == []  # the item's own numbers are in its human_text, not here
    assert 1 <= len(one.try_this) <= 2

    several = service.explain_loss(report, item_indexes=(0, 1, 2, 99), target_meta=meta, level="advanced", language="en")
    assert several.key == "microtone_rounded.12tet+ornament_dropped.oscillation+ornament_dropped.glide|advanced|en"
    assert "glide" in several.text.lower() and len(several.try_this) == 2

    asked = service.explain_loss(report, item_indexes=(0,), target_meta=meta, question="Why can a piano not play it?")
    assert "|q:" in asked.key and "cannot answer a specific question" in asked.text


def test_explain_music_does_not_promise_the_mode():
    spec = service.refine_spec("a calm piece in raga Mohanam on violin, 30 seconds", history=[]).spec
    text = service.explain_music(spec, level="beginner")
    assert "Mohanam" in text and "hint" in text and "not a guarantee" in text
    assert "inspired by" in text and "Notation Studio" in text
    assert service.explain_music(spec, level="advanced") != text


# ---------------------------------------------------------------------------
# music and narration stand-ins
# ---------------------------------------------------------------------------

def read_wav(data: bytes):
    y, sr = sf.read(io.BytesIO(data))
    return y, sr


def test_standin_clip_is_valid_labelled_audio():
    spec = service.refine_spec(DEMO, history=[]).spec.model_copy(update={"duration_s": 20})
    result = service.compose_music(spec, request_id="req-1")
    assert result.standin is True
    assert result.media_type == "audio/wav" and result.ext == "wav"
    assert result.provider.name == "offline" and result.provider.model is None and result.provider.request_id == "req-1"
    assert "stand-in" in result.standin_reason and "ELEVENLABS_API_KEY" in result.standin_reason
    assert "placeholder" in result.notes[0] and "not generated music" in result.notes[0]
    assert any("unreviewed" in n and "Carnatic" in n for n in result.notes)  # which draft table was used
    assert any('"jazz"' in n for n in result.notes)

    assert result.audio[:4] == b"RIFF"
    y, sr = read_wav(result.audio)
    assert sr == 22050 and y.ndim == 1
    assert len(y) / sr == pytest.approx(20.0, abs=0.01)
    assert result.provider.duration_s == pytest.approx(20.0, abs=0.01)
    assert float(np.sqrt(np.mean(y ** 2))) > 0.02  # not silent
    assert float(np.max(np.abs(y))) <= 0.9
    # both halves sound
    half = len(y) // 2
    assert np.sqrt(np.mean(y[:half] ** 2)) > 0.02 and np.sqrt(np.mean(y[half:] ** 2)) > 0.02


def test_standin_clip_is_capped_at_thirty_seconds():
    spec = MusicSpec(genre_id="rock", sections=[SpecSection(genre_id="rock", description="riff", duration_s=120)],
                     duration_s=120, tempo_bpm=120)
    result = service.compose_music(spec, request_id="req-2")
    y, sr = read_wav(result.audio)
    assert len(y) / sr == pytest.approx(30.0, abs=0.01)
    assert any("capped at 30 seconds" in n for n in result.notes)


def test_standin_clip_follows_the_mode_table_and_ornaments():
    """A Carnatic section gets a drone and a wide oscillation; a Western one is plain."""
    plain = offline._section_contour([0.0, 200.0, 400.0], 6.0, 0.5, ornament=False)
    moving = offline._section_contour([0.0, 200.0, 400.0], 6.0, 0.5, ornament=True)
    assert len(plain) == len(moving) == 600
    voiced = ~np.isnan(plain)
    assert set(np.unique(plain[voiced])) <= {0.0, 200.0, 400.0, 1200.0}
    assert np.nanmax(moving) > 1200.0 + 30.0  # the long top note oscillates widely

    spec = MusicSpec(genre_id="carnatic", mode_hint="Mohanam", duration_s=10,
                     sections=[SpecSection(genre_id="carnatic", description="x", duration_s=10)])
    result = service.compose_music(spec, request_id="req-3")
    assert any("Mohanam" in n for n in result.notes)
    y, _ = read_wav(result.audio)
    assert len(y) == 10 * 22050


def test_standin_handles_a_spec_without_sections():
    result = service.compose_music(MusicSpec(genre_id="ambient", duration_s=12), request_id="req-4")
    y, sr = read_wav(result.audio)
    assert len(y) / sr == pytest.approx(12.0, abs=0.01) and result.standin


def test_cancelled_request_returns_no_audio():
    spec = service.refine_spec("a jazz tune", history=[]).spec
    result = service.compose_music(spec, request_id="req-5", cancelled=lambda: True)
    assert result.audio == b"" and result.standin and "cancelled" in result.notes
    assert "Cancelled" in result.standin_reason


def test_narration_standin():
    result = service.narrate("This note was rounded.", request_id="n-1")
    assert result.standin is True and result.audio is None and result.media_type is None and result.ext is None
    assert "browser" in result.message and "ELEVENLABS_API_KEY" in result.message
    assert result.provider.name == "offline" and result.provider.request_id == "n-1"


def test_facade_does_not_raise_on_odd_input():
    assert service.classify_intent(None, history=[]).intent == "clarify"  # type: ignore[arg-type]
    assert "".join(service.chat_reply_stream("", history=[]))
    assert service.sanitize_prompt("").text == ""
    assert service.explain_loss(sample_report(), item_indexes=(42,), target_meta=None, level="expert").text  # type: ignore[arg-type]
