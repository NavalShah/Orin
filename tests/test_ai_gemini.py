"""The Gemini wrapper against a fake client. No network: the client factory is replaced."""
from __future__ import annotations

import json

import httpx
import pytest
from google.genai import errors

from orin.ai import elevenlabs, gemini, service
from orin.ai.schema import MAX_DURATION_S
from orin.ai.types import Turn
from orin.formats import registry
from orin.loss import LossItem, LossReport, LossSummary, explain_key, human_text

ENV = ("GEMINI_API_KEY", "GEMINI_MODEL", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
       "ELEVENLABS_MUSIC_MODEL", "ELEVENLABS_TTS_MODEL", "ORIN_OFFLINE")
FAKE_KEY = "dummy-gemini-value"
MODEL = "model-id-from-env"
REAL_MAKE_CLIENT = gemini.make_client


class Feedback:
    def __init__(self, block_reason):
        self.block_reason = block_reason


class Candidate:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason


class Resp:
    """What the wrapper reads from a GenerateContentResponse."""

    def __init__(self, text=None, parsed=None, block_reason=None, finish_reason="STOP"):
        self.text = text
        self.parsed = parsed
        self.prompt_feedback = Feedback(block_reason) if block_reason else None
        self.candidates = [Candidate(finish_reason)] if finish_reason else []


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def _next(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._next()

    def generate_content_stream(self, *, model, contents, config):
        self.stream_calls.append({"model": model, "contents": contents, "config": config})
        outcome = self._next()

        def chunks():
            for item in outcome:
                if isinstance(item, Exception):
                    raise item
                yield item

        return chunks()


class FakeClient:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GEMINI_MODEL", MODEL)

    def refuse(_key):
        raise AssertionError("this test did not install a fake Gemini client")

    monkeypatch.setattr(gemini, "make_client", refuse)
    monkeypatch.setattr(elevenlabs, "make_client", refuse)


@pytest.fixture
def install(monkeypatch):
    """install(outcomes) -> the fake client the facade will use."""
    made: dict = {}

    def _install(outcomes):
        client = FakeClient(outcomes)

        def factory(api_key):
            made["key"] = api_key
            return client

        monkeypatch.setattr(gemini, "make_client", factory)
        client.made = made  # type: ignore[attr-defined]
        return client

    return _install


def spec_json(**overrides) -> str:
    data = {
        "genre_id": "fusion",
        "sections": [
            {"genre_id": "carnatic", "description": "slow unmetered alapana over a drone", "duration_s": 15},
            {"genre_id": "jazz", "description": "piano takes the theme", "duration_s": 15},
        ],
        "mood": ["calm"], "tempo_bpm": None, "key_or_tonic": None, "mode_hint": None,
        "instruments": ["violin", "piano"], "vocals": "none", "duration_s": 30,
        "structure_notes": "Free time, then a pulse.", "avoid": [],
        "assumptions": ["Read short as 30 seconds."], "learning_goal": None, "unsupported": [],
    }
    data.update(overrides)
    return json.dumps(data)


def api_error(code: int, message: str = "nope") -> errors.APIError:
    return errors.APIError(code, {"error": {"code": code, "message": message, "status": "FAILED"}})


def sample_report() -> LossReport:
    item = LossItem(code="MICROTONE_ROUNDED", severity="minor", event_id="e1", time_s=1.25,
                    detail={"cents_delta": -38.0, "written": "E"},
                    human_text=human_text("MICROTONE_ROUNDED", cents_delta=-38.0, target="Western", written="E"),
                    explain_key=explain_key("MICROTONE_ROUNDED", "12tet"), penalty=0.02)
    return LossReport(items=(item,), summary=LossSummary(counts_by_code={"MICROTONE_ROUNDED": 1}, max_cents_delta=38.0,
                                                         fidelity_score=0.9, pitch_score=0.8),
                      source_format="carnatic", target_format="western_classical")


def text_of(content) -> str:
    return content if isinstance(content, str) else "".join(p.text for p in content.parts)


# ---------------------------------------------------------------------------
# status and client
# ---------------------------------------------------------------------------

def test_status_reports_the_env_model():
    st = service.status()
    assert st.llm_offline is False and st.llm_model == MODEL
    assert st.music_offline and st.tts_offline  # no ElevenLabs key here


def test_make_client_sets_a_timeout_and_retries(monkeypatch):
    seen: dict = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(gemini.genai, "Client", Recorder)
    REAL_MAKE_CLIENT("some-value")
    assert seen["api_key"] == "some-value"
    options = seen["http_options"]
    assert options.timeout == gemini.TIMEOUT_MS and options.timeout > 0
    assert options.retry_options.attempts == 3
    assert 429 in options.retry_options.http_status_codes and 503 in options.retry_options.http_status_codes


# ---------------------------------------------------------------------------
# intent
# ---------------------------------------------------------------------------

def test_structured_intent(install):
    client = install([Resp(text='{"intent": "generate", "reason": "asks for music"}',
                           parsed=gemini.IntentDraft(intent="generate", reason="asks for music"))])
    result = service.classify_intent("Make a calm tune", history=[Turn(role="user", text="hi")], has_attachment=True,
                                     genre_id="carnatic")
    assert result.intent == "generate" and result.source == "gemini"
    call = client.models.calls[0]
    assert call["model"] == MODEL  # from the environment, never hardcoded
    assert client.made["key"] == FAKE_KEY
    config = call["config"]
    assert config.response_mime_type == "application/json" and config.response_schema is gemini.IntentDraft
    assert len(config.safety_settings) == 4
    assert "carnatic" in config.system_instruction and "persian_dastgah" in config.system_instruction
    request = text_of(call["contents"])
    assert "Attachment: yes" in request and "Selected genre chip: carnatic" in request and "Make a calm tune" in request


def test_parsed_none_falls_back_to_validating_the_text(install):
    install([Resp(text='{"intent": "transcribe", "reason": "wants notation"}', parsed=None)])
    result = service.classify_intent("notation please", history=[], has_attachment=True)
    assert result.intent == "transcribe" and result.source == "gemini"


def test_unparseable_intent_degrades_to_the_standin(install):
    client = install([Resp(text='{"intent": "sing"}', parsed=None)])
    result = service.classify_intent("What is a raga?", history=[])
    assert result.intent == "chat" and result.source == "offline"
    assert len(client.models.calls) == 1


@pytest.mark.parametrize("outcome", [
    Resp(text=None, parsed=None, block_reason="SAFETY", finish_reason=None),
    Resp(text=None, parsed=None, finish_reason="SAFETY"),
    Resp(text="", parsed=None),
    api_error(429, "quota"),
    api_error(500, "boom"),
    httpx.ReadTimeout("slow"),
    RuntimeError("anything else"),
])
def test_blocked_or_failed_intent_degrades_to_the_standin(install, outcome):
    install([outcome])
    result = service.classify_intent("Make a short Carnatic alapana that turns into jazz piano", history=[])
    assert result.intent == "generate" and result.source == "offline"


def test_empty_text_does_not_call_the_model(install):
    client = install([])
    assert service.classify_intent("  ", history=[]).intent == "clarify"
    assert client.models.calls == []


def test_missing_model_id_never_builds_a_client(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "")
    st = service.status()
    assert st.llm_offline and "GEMINI_MODEL" in st.reason and st.llm_model is None
    assert service.refine_spec("a jazz tune", history=[]).source == "offline"  # the refusing factory was not called


# ---------------------------------------------------------------------------
# spec refinement
# ---------------------------------------------------------------------------

def test_structured_spec(install):
    client = install([Resp(text=spec_json(unsupported=["an exact copy of the melody"]), parsed=None)])
    result = service.refine_spec("Make a short Carnatic alapana that turns into jazz piano",
                                 history=[Turn(role="user", text="hello")], prefs={"level": "beginner"},
                                 previous_specs=[], genre_id=None)
    assert result.ok and result.source == "gemini"
    spec = result.spec
    assert spec.genre_id == "fusion" and [s.genre_id for s in spec.sections] == ["carnatic", "jazz"]
    assert spec.duration_s == 30 and spec.vocals == "none" and spec.tempo_bpm is None
    assert "Read short as 30 seconds." in result.notes
    assert any("an exact copy of the melody" in n for n in result.notes)  # never dropped silently
    assert len(client.models.calls) == 1
    config = client.models.calls[0]["config"]
    assert config.response_schema is gemini.SpecDraft
    request = text_of(client.models.calls[0]["contents"])
    assert "User preferences" in request and "beginner" in request


def test_spec_draft_schema_is_plain():
    """Structured output needs every field required and no defaults."""
    for model in (gemini.SpecDraft, gemini.SectionDraft, gemini.IntentDraft, gemini.ExplanationDraft):
        for name, field in model.model_fields.items():
            assert field.is_required(), f"{model.__name__}.{name} has a default"
    assert "additionalProperties" not in json.dumps(gemini.SpecDraft.model_json_schema())


def test_aliases_resolve_and_small_duration_drift_is_fixed(install):
    install([Resp(text=spec_json(genre_id="pop", duration_s=32, sections=[
        {"genre_id": "Pop", "description": "verse", "duration_s": 15},
        {"genre_id": "pop", "description": "chorus", "duration_s": 15}]), parsed=None)])
    result = service.refine_spec("a pop song", history=[])
    assert result.ok and result.spec.genre_id == "western_pop"
    assert sum(s.duration_s for s in result.spec.sections) == result.spec.duration_s == 32


def test_invalid_spec_triggers_exactly_one_repair_call(install):
    client = install([Resp(text=spec_json(duration_s=900), parsed=None), Resp(text=spec_json(), parsed=None)])
    result = service.refine_spec("fifteen minutes of fusion", history=[])
    assert result.ok and result.source == "gemini" and result.spec.duration_s == 30
    assert len(client.models.calls) == 2
    repair = client.models.calls[1]["contents"]
    assert [c.role for c in repair] == ["user", "model", "user"]
    assert "900" in text_of(repair[1])  # the failed draft is shown back
    assert str(MAX_DURATION_S) in text_of(repair[2]) and "failed validation" in text_of(repair[2])


def test_still_invalid_after_one_repair_asks_the_user(install):
    client = install([Resp(text=spec_json(genre_id="???"), parsed=None),
                      Resp(text=spec_json(sections=[{"genre_id": "jazz", "description": "x", "duration_s": 200}]), parsed=None)])
    result = service.refine_spec("something", history=[])
    assert result.ok is False and result.spec is None and result.question
    assert result.source == "gemini"
    assert len(client.models.calls) == 2  # one draft, one repair, no more
    assert any("add up" in n for n in result.notes)


def test_malformed_json_also_gets_one_repair(install):
    client = install([Resp(text="Sure! Here is your spec.", parsed=None), Resp(text=spec_json(), parsed=None)])
    result = service.refine_spec("a fusion piece", history=[])
    assert result.ok and len(client.models.calls) == 2


@pytest.mark.parametrize("outcome", [
    Resp(text=None, parsed=None, block_reason="PROHIBITED_CONTENT", finish_reason=None),
    api_error(503, "unavailable"),
    httpx.ConnectTimeout("no route"),
])
def test_blocked_or_failed_spec_degrades_to_the_standin(install, outcome):
    client = install([outcome])
    result = service.refine_spec("Make a short Carnatic alapana that turns into jazz piano", history=[])
    assert result.ok and result.source == "offline"
    assert [s.genre_id for s in result.spec.sections] == ["carnatic", "jazz"]
    assert any("offline stand-in built this spec" in n for n in result.notes)
    assert len(client.models.calls) == 1  # a provider failure is not a validation failure: no repair call


def test_validate_draft_lists_every_problem():
    draft = gemini.SpecDraft.model_validate_json(spec_json(genre_id="!!", duration_s=5, tempo_bpm=900))
    with pytest.raises(gemini.SpecProblem) as info:
        gemini.validate_draft(draft)
    message = str(info.value)
    assert "genre_id" in message and "duration_s" in message and "tempo_bpm" in message

    spec, notes = gemini.validate_draft(gemini.SpecDraft.model_validate_json(spec_json(sections=[], genre_id="Jazz")))
    assert spec.genre_id == "jazz" and len(spec.sections) == 1 and spec.sections[0].duration_s == 30 and notes == []


def test_artist_names_do_not_survive_the_model(install):
    install([Resp(text=spec_json(sections=[
        {"genre_id": "rock", "description": "a riff like Zara Quillfeather would play", "duration_s": 30}],
        genre_id="rock", structure_notes="In the style of Zara Quillfeather."), parsed=None)])
    result = service.refine_spec("a rock song in the style of Zara Quillfeather", history=[])
    assert result.ok and result.source == "gemini"
    assert "Quillfeather" not in result.spec.model_dump_json()
    assert any("Zara Quillfeather" in n for n in result.notes)


def test_the_key_never_reaches_a_result(install):
    install([api_error(400, f"API key {FAKE_KEY} is not valid")])
    result = service.refine_spec("a jazz tune", history=[])
    assert FAKE_KEY not in result.model_dump_json()
    error = gemini._wrap(api_error(400, f"bad key {FAKE_KEY}"))
    assert FAKE_KEY not in str(error) and "[redacted]" in str(error)


# ---------------------------------------------------------------------------
# no sampling parameters, anywhere
# ---------------------------------------------------------------------------

def test_no_sampling_parameters_in_any_config(install):
    explanation = '{"text": "The note sat between two piano keys, 38 cents from the nearer one.", "try_this": ["Use a pitch bend."]}'
    client = install([
        Resp(text='{"intent": "chat", "reason": "question"}', parsed=None),
        Resp(text=spec_json(), parsed=None),
        Resp(text=explanation, parsed=None),
        Resp(text="This piece was asked for as a fusion."),
        Resp(text="The user asked for a fusion piece."),
        [Resp(text="Hello")],
    ])
    service.classify_intent("What is a raga?", history=[])
    spec = service.refine_spec("a fusion piece", history=[]).spec
    service.explain_loss(sample_report(), target_meta=registry.get("western_classical").META)
    assert service.explain_music(spec) == "This piece was asked for as a fusion."
    assert service.summarize([Turn(role="user", text="a fusion piece")]) == "The user asked for a fusion piece."
    assert list(service.chat_reply_stream("hi", history=[])) == ["Hello"]

    calls = client.models.calls + client.models.stream_calls
    assert len(calls) == 6 and client.models.outcomes == []
    for call in calls:
        config = call["config"]
        assert config.temperature is None and config.top_p is None and config.top_k is None
        sent = config.model_dump(exclude_none=True)
        assert not {"temperature", "top_p", "top_k"} & set(sent)
        assert sent["system_instruction"] and sent["safety_settings"]
        assert call["model"] == MODEL


# ---------------------------------------------------------------------------
# loss explanation
# ---------------------------------------------------------------------------

def test_grounded_explanation_is_used(install):
    client = install([Resp(text=None, parsed=gemini.ExplanationDraft(
        text="A piano has 12 keys in each octave. This note was 38 cents away from the nearest one, so it was rounded.",
        try_this=["Bend the note on a keyboard.", "Slide on a violin.", "A third idea that is dropped."]))])
    meta = registry.get("western_classical").META
    result = service.explain_loss(sample_report(), item_indexes=(0,), source_meta=registry.get("carnatic").META,
                                  target_meta=meta, level="intermediate", language="en")
    assert result.source == "gemini" and "38 cents" in result.text
    assert len(result.try_this) == 2
    assert result.key.startswith("microtone_rounded.12tet:") and result.key.endswith("|intermediate|en")
    payload = json.loads(text_of(client.models.calls[0]["contents"]))
    assert payload["scope"] == "items" and payload["level"] == "intermediate"
    assert payload["items"][0]["detail"]["cents_delta"] == -38.0
    assert payload["target_format"]["review_status"] == "unreviewed"
    assert payload["summary"]["fidelity_score"] == 0.9


def test_explanation_with_invented_numbers_is_replaced_by_the_standin(install):
    install([Resp(text='{"text": "This kampita was flattened by 57 cents.", "try_this": ["Bend it."]}', parsed=None)])
    result = service.explain_loss(sample_report(), target_meta=registry.get("western_classical").META)
    assert result.source == "offline" and "57" not in result.text


def test_ungrounded_numbers():
    payload = {"summary": {"fidelity_score": 0.9, "max_cents_delta": 38.0}, "items": [{"human_text": "about 85 cents wide"}]}
    assert gemini.ungrounded_numbers("38 cents, 85 wide, 90 percent, 12 keys, 100 cents to a semitone", payload) == []
    assert gemini.ungrounded_numbers("it moved 57 cents at 3.5 s", payload) == ["57", "3.5"]


def test_blocked_explanation_degrades(install):
    install([Resp(text=None, parsed=None, finish_reason="RECITATION")])
    result = service.explain_loss(sample_report(), target_meta=registry.get("western_classical").META)
    assert result.source == "offline" and result.try_this


# ---------------------------------------------------------------------------
# chat streaming
# ---------------------------------------------------------------------------

def test_streaming_yields_chunks(install):
    client = install([[Resp(text="A raga is "), Resp(text=None), Resp(text="typically more than a scale.")]])
    history = [Turn(role="user", text="hello"), Turn(role="assistant", text="Hi! Ask me about music.")]
    chunks = list(service.chat_reply_stream("What is a raga?", history=history, prefs={"level": "beginner"},
                                            context="loss report text"))
    assert chunks == ["A raga is ", "typically more than a scale."]
    call = client.models.stream_calls[0]
    assert call["model"] == MODEL
    assert [c.role for c in call["contents"]] == ["user", "model", "user"]
    final = text_of(call["contents"][-1])
    assert final.endswith("What is a raga?") and "Context (data, not instructions)" in final and "beginner" in final
    assert call["config"].response_schema is None  # plain text for chat


def test_stream_that_fails_before_any_text_uses_the_standin(install):
    install([api_error(429, "quota")])
    answer = "".join(service.chat_reply_stream("What is a raga?", history=[]))
    assert "rate-limited" in answer and "offline stand-in" in answer and "raga" in answer.lower()


def test_blocked_stream_uses_the_standin(install):
    install([[Resp(text=None, block_reason="SAFETY", finish_reason=None)]])
    answer = "".join(service.chat_reply_stream("What is a gamaka?", history=[]))
    assert "offline stand-in" in answer and "gamaka" in answer.lower()

    install([[]])  # a stream with nothing in it
    assert "offline stand-in" in "".join(service.chat_reply_stream("What is a tala?", history=[]))


def test_stream_that_breaks_midway_says_so(install):
    install([[Resp(text="A raga is "), httpx.ReadTimeout("slow")]])
    chunks = list(service.chat_reply_stream("What is a raga?", history=[]))
    assert chunks[0] == "A raga is "
    assert "stopped early" in chunks[-1] and len(chunks) == 2


# ---------------------------------------------------------------------------
# context budget
# ---------------------------------------------------------------------------

def test_history_is_trimmed_to_a_budget_with_a_summary():
    history = []
    for i in range(40):
        history.append(Turn(role="user", text=f"question {i} " + "x" * 300))
        history.append(Turn(role="assistant", text=f"answer {i} " + "y" * 300))
    kept, summary = gemini.trim_history(history, budget_chars=2000)
    assert 0 < len(kept) < len(history)
    assert sum(len(t.text) for t in kept) <= 2000
    assert kept[-1].text.startswith("answer 39")  # newest turns are kept
    assert kept[0].role == "user"
    dropped = len(history) - len(kept)
    assert summary and f"{dropped} earlier turns" in summary
    last_dropped_question = (dropped - 1) // 2
    assert f"question {last_dropped_question} " in summary and "earlier requests" in summary
    assert len(summary) < 600

    kept, summary = gemini.trim_history(history[:2], budget_chars=2000)
    assert len(kept) == 2 and summary is None

    long_turn = [Turn(role="user", text="z" * 50_000)]
    kept, summary = gemini.trim_history(long_turn, budget_chars=100)
    assert len(kept) == 1 and len(kept[0].text) == gemini.MAX_TURN_CHARS and summary is None


def test_trimmed_history_reaches_the_prompt_with_its_summary(install):
    client = install([[Resp(text="ok")]])
    history = []
    for i in range(60):
        history.append(Turn(role="user", text=f"question {i} " + "x" * 400))
        history.append(Turn(role="assistant", text=f"answer {i} " + "y" * 400))
    assert list(service.chat_reply_stream("and now?", history=history)) == ["ok"]
    contents = client.models.stream_calls[0]["contents"]
    assert len(contents) < len(history)
    assert "earlier turns that are not shown" in text_of(contents[-1])
    assert sum(len(text_of(c)) for c in contents) < gemini.HISTORY_BUDGET_CHARS + 2000


# ---------------------------------------------------------------------------
# the real SDK over a fake HTTP transport: checks what goes on the wire, with no network
# ---------------------------------------------------------------------------

def sdk_client(handler):
    from google import genai
    from google.genai import types

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return genai.Client(api_key=FAKE_KEY, http_options=types.HttpOptions(
        timeout=5_000, httpx_client=http, retry_options=types.HttpRetryOptions(attempts=1)))


def candidate(text: str) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}]}


def test_wire_request_has_a_plain_schema_and_no_sampling_parameters(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=candidate(spec_json()))

    monkeypatch.setattr(gemini, "make_client", lambda key: sdk_client(handler))
    result = service.refine_spec("Make a short Carnatic alapana that turns into jazz piano", history=[])
    assert result.ok and result.source == "gemini" and len(result.spec.sections) == 2

    (request,) = requests
    assert f"/models/{MODEL}:generateContent" in str(request.url)
    body = json.loads(request.content)
    generation = body["generationConfig"]
    assert not {"temperature", "topP", "topK"} & set(generation)
    assert generation["responseMimeType"] == "application/json"
    schema = generation["responseSchema"]
    assert sorted(schema["required"]) == sorted(schema["properties"])  # every field required
    assert schema["properties"]["tempo_bpm"]["nullable"] is True
    assert schema["properties"]["vocals"]["enum"] == ["none", "wordless", "lyrics"]
    assert len(body["safetySettings"]) == 4 and "systemInstruction" in body


def test_wire_blocked_prompt_and_http_errors_degrade(monkeypatch):
    replies = iter([
        httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
        httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]}),
        httpx.Response(429, json={"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
    ])
    monkeypatch.setattr(gemini, "make_client", lambda key: sdk_client(lambda request: next(replies)))
    for _ in range(2):
        assert service.classify_intent("What is a raga?", history=[]).source == "offline"
    result = service.refine_spec("a jazz tune", history=[])
    assert result.ok and result.source == "offline"
    assert any("rate-limited" in n for n in result.notes)


def test_wire_streaming(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert ":streamGenerateContent" in str(request.url)
        events = [json.dumps(candidate(text)) for text in ("A raga is ", "typically more than a scale.")]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content="".join(f"data: {e}\n\n" for e in events).encode())

    monkeypatch.setattr(gemini, "make_client", lambda key: sdk_client(handler))
    assert list(service.chat_reply_stream("What is a raga?", history=[])) == ["A raga is ", "typically more than a scale."]
