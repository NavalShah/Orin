"""The ElevenLabs wrapper against a fake client. No network: the client factory is replaced.

The fake mirrors the SDK: ``compose`` and ``convert`` return a lazy generator, so an
error only surfaces when the bytes are read.
"""
from __future__ import annotations

import io

import pytest
import soundfile as sf
from elevenlabs.core import ApiError

from orin.ai import compose_prompt, config, elevenlabs as el, gemini, service
from orin.ai.schema import MusicSpec, SpecSection

ENV = ("GEMINI_API_KEY", "GEMINI_MODEL", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
       "ELEVENLABS_MUSIC_MODEL", "ELEVENLABS_TTS_MODEL", "ORIN_OFFLINE")
FAKE_KEY = "dummy-eleven-value"
MP3 = [b"ID3", b"\x00\x01", b"audio-bytes"]
REAL_MAKE_CLIENT = el.make_client


def lazy(outcome):
    """A generator that raises or yields only when iterated, like the SDK's."""
    def gen():
        if isinstance(outcome, Exception):
            raise outcome
        yield from outcome
    return gen()


class FakeMusic:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def compose(self, **kwargs):
        self.calls.append(kwargs)
        return lazy(self.outcomes.pop(0))


class FakeTTS:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict]] = []

    def convert(self, voice_id, **kwargs):
        self.calls.append((voice_id, kwargs))
        return lazy(self.outcomes.pop(0))


class Voice:
    def __init__(self, voice_id):
        self.voice_id = voice_id


class Found:
    def __init__(self, voices):
        self.voices = voices


class FakeVoices:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeClient:
    def __init__(self, music=(), tts=(), voices=None):
        self.music = FakeMusic(music)
        self.text_to_speech = FakeTTS(tts)
        self.voices = FakeVoices(voices if voices is not None else Found([Voice("first-voice")]))


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)

    def refuse(_key):
        raise AssertionError("this test did not install a fake client")

    monkeypatch.setattr(el, "make_client", refuse)
    monkeypatch.setattr(gemini, "make_client", refuse)
    el.reset()
    yield
    el.reset()


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(el, "_sleep", waited.append)
    return waited


@pytest.fixture
def install(monkeypatch):
    made: dict = {}

    def _install(**kwargs):
        client = FakeClient(**kwargs)

        def factory(api_key):
            made["key"] = api_key
            return client

        monkeypatch.setattr(el, "make_client", factory)
        client.made = made  # type: ignore[attr-defined]
        return client

    return _install


def one_section(seconds: int = 40) -> MusicSpec:
    return MusicSpec(
        genre_id="carnatic",
        sections=[SpecSection(genre_id="carnatic", description="slow alapana over a drone", duration_s=seconds)],
        mood=["calm"], tempo_bpm=70, key_or_tonic="D", mode_hint="Mohanam", instruments=["violin", "tanpura"],
        vocals="none", duration_s=seconds, avoid=["drums"])


def two_sections() -> MusicSpec:
    return MusicSpec(
        genre_id="fusion",
        sections=[SpecSection(genre_id="carnatic", description="slow alapana over a drone", duration_s=15),
                  SpecSection(genre_id="jazz", description="piano takes the theme", duration_s=15)],
        mood=["calm"], instruments=["violin", "piano"], vocals="none", duration_s=30, avoid=["drums"],
        structure_notes="Free time, then a pulse.")


def api_error(status: int, detail=None, headers=None) -> ApiError:
    return ApiError(status_code=status, headers=headers, body=None if detail is None else {"detail": detail})


def assert_standin(result, *, contains: str):
    assert result.standin is True and result.provider.name == "offline"
    assert result.media_type == "audio/wav" and result.ext == "wav"
    assert contains in result.standin_reason
    y, sr = sf.read(io.BytesIO(result.audio))
    assert sr == 22050 and len(y) > 0
    assert "placeholder" in " ".join(result.notes)
    assert FAKE_KEY not in result.model_dump_json(exclude={"audio"})


# ---------------------------------------------------------------------------
# status, client
# ---------------------------------------------------------------------------

def test_status_with_a_key():
    st = service.status()
    assert st.music_offline is False and st.tts_offline is False and st.llm_offline is True
    assert st.music_model is None and st.tts_model is None  # empty variables: the provider default applies


def test_status_reports_models_from_env(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_MUSIC_MODEL", "music_v2")
    monkeypatch.setenv("ELEVENLABS_TTS_MODEL", "some_tts_model")
    st = service.status()
    assert st.music_model == "music_v2" and st.tts_model == "some_tts_model"


def test_the_key_is_passed_explicitly(monkeypatch, install):
    seen: dict = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(el, "ElevenLabs", Recorder)
    REAL_MAKE_CLIENT("some-value")
    assert seen["api_key"] == "some-value" and seen["timeout"] > 0

    client = install(music=[MP3])
    service.compose_music(one_section(), request_id="key-1")
    assert client.made["key"] == FAKE_KEY  # the facade hands the configured key to the factory


@pytest.mark.parametrize("model, family", [
    ("", "v1"), (None, "v1"), ("music_v1", "v1"), ("music_v2", "v2"), ("music_v2_5", "v2"), ("MUSIC_V2", "v2"),
])
def test_model_family(model, family):
    assert el.model_family(model) == family


# ---------------------------------------------------------------------------
# music: prompt and plan
# ---------------------------------------------------------------------------

def test_prompt_path(install):
    client = install(music=[MP3])
    result = service.compose_music(one_section(40), request_id="p-1")
    assert result.standin is False and result.standin_reason is None
    assert result.audio == b"".join(MP3) and result.media_type == "audio/mpeg" and result.ext == "mp3"
    assert result.provider.name == "elevenlabs" and result.provider.model is None
    assert result.provider.request_id == "p-1" and result.provider.duration_s == 40.0

    (call,) = client.music.calls
    assert "composition_plan" not in call
    assert call["music_length_ms"] == 40_000
    assert call["output_format"] == "mp3_44100_128"
    assert "model_id" not in call  # no model id is sent when the variable is empty
    prompt = call["prompt"]
    assert "inspired by Carnatic" in prompt and "authentic" not in prompt.lower()
    assert "Tonal centre hint: D" in prompt and "Mode hint: Mohanam (a loose guide)" in prompt
    assert "about 70 BPM" in prompt and "violin" in prompt and "no vocals" in prompt and "Avoid: drums" in prompt


def test_model_id_comes_from_env(install, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_MUSIC_MODEL", "music_v1")
    client = install(music=[MP3])
    result = service.compose_music(one_section(), request_id="p-2")
    assert client.music.calls[0]["model_id"] == "music_v1" and result.provider.model == "music_v1"


@pytest.mark.parametrize("model", ["", "music_v1"])
def test_plan_path_v1_shape(install, monkeypatch, model):
    monkeypatch.setenv("ELEVENLABS_MUSIC_MODEL", model)
    client = install(music=[MP3])
    result = service.compose_music(two_sections(), request_id=f"plan-{model or 'default'}")
    assert result.standin is False and result.provider.duration_s == 30.0

    (call,) = client.music.calls
    assert "prompt" not in call and "music_length_ms" not in call
    assert ("model_id" in call) == bool(model)
    plan = call["composition_plan"]
    assert set(plan) == {"positive_global_styles", "negative_global_styles", "sections"}
    assert "chunks" not in plan
    assert "drums" in plan["negative_global_styles"] and "vocals" in plan["negative_global_styles"]
    assert "piano" in plan["positive_global_styles"]
    assert len(plan["sections"]) == 2
    for section in plan["sections"]:
        assert set(section) == {"section_name", "positive_local_styles", "negative_local_styles", "duration_ms", "lines"}
        assert section["duration_ms"] == 15_000 and section["lines"] == []
    assert "Carnatic" in plan["sections"][0]["section_name"] and "jazz" in plan["sections"][1]["section_name"]
    assert any("inspired by Carnatic" in s for s in plan["sections"][0]["positive_local_styles"])


@pytest.mark.parametrize("model", ["music_v2", "music_v2_5"])
def test_plan_path_v2_shape(install, monkeypatch, model):
    monkeypatch.setenv("ELEVENLABS_MUSIC_MODEL", model)
    client = install(music=[MP3])
    result = service.compose_music(two_sections(), request_id=f"plan-{model}")
    assert result.standin is False and result.provider.model == model

    (call,) = client.music.calls
    assert call["model_id"] == model
    plan = call["composition_plan"]
    assert set(plan) == {"chunks"}
    assert len(plan["chunks"]) == 2
    for chunk in plan["chunks"]:
        assert set(chunk) == {"text", "duration_ms", "positive_styles", "negative_styles"}
        assert chunk["text"].startswith("[Part ") and chunk["duration_ms"] == 15_000
        assert "drums" in chunk["negative_styles"]
    assert "slow alapana" in plan["chunks"][0]["text"] and "piano takes the theme" in plan["chunks"][1]["text"]


def test_lengths_stay_inside_provider_limits():
    assert compose_prompt.length_ms(1) == 3_000
    assert compose_prompt.length_ms(40) == 40_000
    assert compose_prompt.length_ms(900) == 300_000

    long_spec = MusicSpec(genre_id="fusion", duration_s=400, sections=[
        SpecSection(genre_id="carnatic", description="a", duration_s=300),
        SpecSection(genre_id="jazz", description="b", duration_s=100)])
    for plan in (compose_prompt.plan_v1(long_spec)["sections"], compose_prompt.plan_v2(long_spec)["chunks"]):
        assert all(3_000 <= part["duration_ms"] <= 120_000 for part in plan)
        assert sum(part["duration_ms"] for part in plan) <= 300_000
        assert len(plan) > 2  # the long section was split


def test_prompt_wording_is_honest():
    prompt = compose_prompt.spec_to_prompt(two_sections())
    assert "2 parts" in prompt and "inspired by Carnatic" in prompt and "jazz" in prompt
    for spec in (one_section(), two_sections()):
        text = " ".join([compose_prompt.spec_to_prompt(spec), str(compose_prompt.plan_v1(spec)), str(compose_prompt.plan_v2(spec))])
        for word in ("authentic", "genuine", "traditional", "pure "):
            assert word not in text.lower()
    lyrics = one_section().model_copy(update={"vocals": "lyrics"})
    assert "original lyrics" in compose_prompt.spec_to_prompt(lyrics)
    assert "vocals" not in compose_prompt.negative_styles(lyrics)


# ---------------------------------------------------------------------------
# music: errors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("error", [
    api_error(402, {"code": "payment_required", "message": "Upgrade your plan."}),
    api_error(402, {"status": "insufficient_credits", "message": "No credits."}),
    api_error(403, {"code": "subscription_required", "message": "Paid plan only."}),
    api_error(403, {"code": "feature_not_available", "message": "Not on your tier."}),
    api_error(401, {"status": "quota_exceeded", "message": "Quota exceeded."}),  # the legacy field, on a 401
    api_error(403),
    api_error(402, "Payment required"),
])
def test_plan_errors_return_a_labelled_standin(install, sleeps, error):
    client = install(music=[error])
    result = service.compose_music(one_section(12), request_id="plan-error")
    assert_standin(result, contains="paid ElevenLabs plan")
    assert len(client.music.calls) == 1 and sleeps == []  # not retried


def test_invalid_key_and_denied_model(install, monkeypatch):
    install(music=[api_error(401, {"status": "invalid_api_key", "message": f"Invalid API key {FAKE_KEY}"})])
    result = service.compose_music(one_section(12), request_id="auth-1")
    assert_standin(result, contains="ELEVENLABS_API_KEY")

    monkeypatch.setenv("ELEVENLABS_MUSIC_MODEL", "music_v2")
    install(music=[api_error(403, {"code": "model_access_denied", "message": "No access."})])
    result = service.compose_music(one_section(12), request_id="auth-2")
    assert_standin(result, contains="cannot use the model")


def test_bad_prompt_retries_once_with_the_suggestion(install, sleeps):
    refused = api_error(400, {"code": "bad_prompt", "message": "Names an artist.",
                              "data": {"prompt_suggestion": "A calm violin melody over a drone."}})
    client = install(music=[refused, MP3])
    result = service.compose_music(one_section(), request_id="bad-1")
    assert result.standin is False and result.audio == b"".join(MP3)
    assert len(client.music.calls) == 2 and sleeps == []
    assert client.music.calls[1]["prompt"] == "A calm violin melody over a drone."
    assert client.music.calls[1]["music_length_ms"] == client.music.calls[0]["music_length_ms"]
    assert any("suggested wording was used" in n for n in result.notes)


def test_bad_prompt_twice_or_without_a_suggestion_is_a_standin(install):
    refused = api_error(400, {"code": "bad_prompt", "message": "No.", "data": {"prompt_suggestion": "Try this."}})
    client = install(music=[refused, refused])
    result = service.compose_music(one_section(12), request_id="bad-2")
    assert_standin(result, contains="refused this prompt")
    assert len(client.music.calls) == 2  # one retry only

    client = install(music=[api_error(400, {"code": "bad_prompt", "message": "No."})])
    result = service.compose_music(one_section(12), request_id="bad-3")
    assert_standin(result, contains="refused this prompt")
    assert len(client.music.calls) == 1


def test_bad_composition_plan_retries_once_with_the_suggested_plan(install):
    suggestion = {"positive_global_styles": ["calm"], "negative_global_styles": [], "sections": []}
    refused = api_error(400, {"code": "bad_composition_plan", "message": "No.",
                              "data": {"composition_plan_suggestion": suggestion}})
    client = install(music=[refused, MP3])
    result = service.compose_music(two_sections(), request_id="bad-4")
    assert result.standin is False and len(client.music.calls) == 2
    assert client.music.calls[1]["composition_plan"] == suggestion
    assert any("suggested plan was used" in n for n in result.notes)


def test_rate_limit_backs_off_then_succeeds(install, sleeps):
    client = install(music=[api_error(429, {"code": "rate_limit_exceeded", "message": "Slow down."}),
                            api_error(503, "Service unavailable"), MP3])
    result = service.compose_music(one_section(), request_id="rate-1")
    assert result.standin is False and result.audio == b"".join(MP3)
    assert len(client.music.calls) == 3
    assert sleeps == [1.0, 2.0]  # exponential


def test_retry_after_header_is_respected(install, sleeps):
    client = install(music=[api_error(429, {"code": "system_busy", "message": "Busy."}, headers={"retry-after": "7"}), MP3])
    assert service.compose_music(one_section(), request_id="rate-2").standin is False
    assert sleeps == [7.0] and len(client.music.calls) == 2


def test_rate_limit_that_never_clears_gives_up(install, sleeps):
    client = install(music=[api_error(429, {"code": "rate_limit_exceeded", "message": "Slow down."})] * el.MAX_ATTEMPTS)
    result = service.compose_music(one_section(12), request_id="rate-3")
    assert_standin(result, contains="busy")
    assert len(client.music.calls) == el.MAX_ATTEMPTS and len(sleeps) == el.MAX_ATTEMPTS - 1
    assert all(s <= el.BACKOFF_MAX_S for s in sleeps)


@pytest.mark.parametrize("detail", [
    [{"loc": ["body", "composition_plan", "sections", 0, "duration_ms"], "msg": "Input should be greater than 3000", "type": "x"}],
    ["a bare string in a list"],
    "Unprocessable entity",
])
def test_422_is_our_bug_and_does_not_crash(install, sleeps, detail):
    client = install(music=[api_error(422, detail)])
    result = service.compose_music(one_section(12), request_id="bug-1")
    assert_standin(result, contains="did not accept the request Orin built")
    assert any("fault in Orin's request" in n for n in result.notes)
    assert len(client.music.calls) == 1 and sleeps == []


@pytest.mark.parametrize("error", [
    ApiError(status_code=500, body=None),
    ApiError(status_code=400, body="plain text body"),
    ApiError(status_code=400, body={"detail": None}),
    ApiError(status_code=400, body={"unexpected": "shape"}),
    ApiError(status_code=None, body=["a", "list"]),
    ValueError("not an API error at all"),
])
def test_odd_error_bodies_do_not_crash(install, sleeps, error):
    install(music=[error] * el.MAX_ATTEMPTS)
    result = service.compose_music(one_section(12), request_id="odd-1")
    assert result.standin and result.provider.name == "offline" and result.standin_reason


def test_error_info_reads_every_detail_shape():
    info = el.error_info(api_error(403, {"code": "subscription_required", "message": "m", "data": {"k": 1}}))
    assert (info.status, info.code, info.kind, info.data) == (403, "subscription_required", "plan", {"k": 1})
    assert el.error_info(api_error(401, {"status": "quota_exceeded"})).kind == "plan"
    assert el.error_info(api_error(401, {"status": "invalid_api_key"})).kind == "auth"
    info = el.error_info(api_error(422, [{"msg": "bad field"}, {"msg": "another"}]))
    assert info.kind == "bug" and "bad field" in info.message
    assert el.error_info(api_error(429, "slow")).retryable
    assert el.error_info(api_error(502)).retryable
    assert not el.error_info(api_error(400, "no")).retryable


def test_the_key_never_appears_in_a_result(install):
    install(music=[api_error(422, f"Rejected. Authorization header was {FAKE_KEY}")])
    result = service.compose_music(one_section(12), request_id="leak-1")
    dumped = result.model_dump_json(exclude={"audio"})
    assert FAKE_KEY not in dumped and "[redacted]" in dumped
    failure = el.ProviderFailure("other", "friendly", detail=f"key {FAKE_KEY}")
    assert FAKE_KEY not in str(failure)
    assert FAKE_KEY not in repr(config.load())


def test_empty_audio_is_a_standin(install):
    install(music=[[]])
    assert_standin(service.compose_music(one_section(12), request_id="empty-1"), contains="no audio")


# ---------------------------------------------------------------------------
# cancellation and idempotency
# ---------------------------------------------------------------------------

def test_cancelled_before_the_call_makes_no_request(install):
    client = install(music=[MP3])
    result = service.compose_music(one_section(), request_id="c-1", cancelled=lambda: True)
    assert result.audio == b"" and "cancelled" in result.notes
    assert client.music.calls == []


def test_cancelled_while_composing_returns_nothing(install):
    client = install(music=[MP3])
    answers = iter([False, False, True, True, True])
    result = service.compose_music(one_section(), request_id="c-2", cancelled=lambda: next(answers, True))
    assert len(client.music.calls) == 1
    assert result.audio == b"" and "cancelled" in result.notes


def test_one_provider_call_per_request_id(install):
    client = install(music=[MP3, [b"second"]])
    first = service.compose_music(one_section(), request_id="same")
    again = service.compose_music(one_section(), request_id="same")
    assert len(client.music.calls) == 1 and again.audio == first.audio
    other = service.compose_music(one_section(), request_id="different")
    assert len(client.music.calls) == 2 and other.audio == b"second"


def test_a_failed_request_id_can_be_retried(install, sleeps):
    client = install(music=[api_error(500)] * el.MAX_ATTEMPTS + [MP3])
    assert service.compose_music(one_section(12), request_id="retry-me").standin is True
    assert service.compose_music(one_section(12), request_id="retry-me").standin is False
    assert len(client.music.calls) == el.MAX_ATTEMPTS + 1


# ---------------------------------------------------------------------------
# narration
# ---------------------------------------------------------------------------

def test_narration_uses_the_configured_voice(install, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-from-env")
    client = install(tts=[MP3])
    result = service.narrate("This note was rounded.", request_id="t-1")
    assert result.standin is False and result.audio == b"".join(MP3)
    assert result.media_type == "audio/mpeg" and result.ext == "mp3"
    assert result.provider.name == "elevenlabs" and result.provider.model is None and result.provider.request_id == "t-1"
    (voice_id, kwargs), = client.text_to_speech.calls
    assert voice_id == "voice-from-env"
    assert kwargs["text"] == "This note was rounded." and kwargs["output_format"] == "mp3_44100_128"
    assert "model_id" not in kwargs  # no model id is sent when the variable is empty
    assert client.voices.calls == []


def test_narration_falls_back_to_the_first_voice(install, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_TTS_MODEL", "tts-model-from-env")
    client = install(tts=[MP3, MP3])
    result = service.narrate("Hello.", request_id="t-2")
    assert result.standin is False and result.provider.model == "tts-model-from-env"
    assert client.voices.calls == [{"page_size": 1}]
    assert client.text_to_speech.calls[0][0] == "first-voice"
    assert client.text_to_speech.calls[0][1]["model_id"] == "tts-model-from-env"
    service.narrate("Again.", request_id="t-3")
    assert len(client.voices.calls) == 1  # the voice is looked up once


@pytest.mark.parametrize("voices", [
    Found([]),
    Found(None),
    api_error(401, {"status": "missing_permissions", "message": "voices_read is missing"}),
])
def test_no_usable_voice_tells_the_user_to_set_one(install, voices):
    client = install(tts=[MP3], voices=voices)
    result = service.narrate("Hello.", request_id="t-4")
    assert result.standin is True and result.audio is None
    assert "ELEVENLABS_VOICE_ID" in result.message and "browser" in result.message
    assert client.text_to_speech.calls == []


def test_default_voice_not_on_the_plan_says_how_to_fix_it(install):
    install(tts=[api_error(402, {"code": "payment_required", "message": "This voice needs a paid plan."})])
    result = service.narrate("Hello.", request_id="t-5")
    assert result.standin is True and result.audio is None
    assert "ELEVENLABS_VOICE_ID" in result.message and "browser" in result.message
    assert FAKE_KEY not in result.model_dump_json()


def test_narration_backs_off_and_is_idempotent(install, sleeps, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-from-env")
    client = install(tts=[api_error(429, "slow down"), MP3])
    first = service.narrate("Hello.", request_id="t-6")
    again = service.narrate("Hello.", request_id="t-6")
    assert first.standin is False and again.audio == first.audio
    assert len(client.text_to_speech.calls) == 2 and sleeps == [1.0]


def test_narration_failure_and_empty_text(install, sleeps, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-from-env")
    client = install(tts=[api_error(401, {"status": "invalid_api_key", "message": "bad"})])
    result = service.narrate("Hello.", request_id="t-7")
    assert result.standin and "ELEVENLABS_API_KEY" in result.message
    assert service.narrate("   ", request_id="t-8").standin  # nothing to read: no call
    assert len(client.text_to_speech.calls) == 1


# ---------------------------------------------------------------------------
# the real SDK over a fake HTTP transport: checks what goes on the wire, with no network
# ---------------------------------------------------------------------------

def sdk_client(handler):
    import httpx
    from elevenlabs.client import ElevenLabs

    return ElevenLabs(api_key=FAKE_KEY, httpx_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_wire_music_request_and_lazy_errors(monkeypatch, sleeps):
    import json

    import httpx

    requests: list[httpx.Request] = []
    replies = iter([
        httpx.Response(200, content=b"ID3-fake-mp3"),
        httpx.Response(402, json={"detail": {"code": "payment_required", "status": "payment_required", "message": "Upgrade."}}),
        httpx.Response(422, json={"detail": [{"loc": ["body", "prompt"], "msg": "Field required", "type": "missing"}]}),
        httpx.Response(403, json={"detail": "Forbidden"}),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(replies)

    monkeypatch.setattr(el, "make_client", lambda key: sdk_client(handler))
    result = service.compose_music(one_section(12), request_id="wire-1")
    assert result.standin is False and result.audio == b"ID3-fake-mp3"
    request = requests[0]
    assert request.url.path == "/v1/music" and request.url.params["output_format"] == "mp3_44100_128"
    assert request.headers["xi-api-key"] == FAKE_KEY
    body = json.loads(request.content)
    assert set(body) == {"prompt", "music_length_ms"} and body["music_length_ms"] == 12_000  # no model id

    assert_standin(service.compose_music(one_section(12), request_id="wire-2"), contains="paid ElevenLabs plan")
    assert_standin(service.compose_music(one_section(12), request_id="wire-3"), contains="request Orin built")
    assert_standin(service.compose_music(one_section(12), request_id="wire-4"), contains="paid ElevenLabs plan")
    assert len(requests) == 4 and sleeps == []


def test_wire_narration_with_voice_lookup(monkeypatch):
    import json

    import httpx

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/voices"):
            return httpx.Response(200, json={"voices": [{"voice_id": "v-first", "name": "First"}],
                                             "has_more": False, "total_count": 1})
        return httpx.Response(200, content=b"ID3-speech")

    monkeypatch.setattr(el, "make_client", lambda key: sdk_client(handler))
    result = service.narrate("Hello there.", request_id="wire-tts")
    assert result.standin is False and result.audio == b"ID3-speech"
    lookup, speech = requests
    assert lookup.method == "GET" and lookup.url.params["page_size"] == "1"
    assert speech.url.path == "/v1/text-to-speech/v-first"
    assert json.loads(speech.content) == {"text": "Hello there."}  # no model id when the variable is empty
