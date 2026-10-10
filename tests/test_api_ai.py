"""API: explain, narrate, chat and generate, on the offline stand-ins (spec 9.4, 9.5, 10).

Everything here needs ``orin.ai.service``; the module skips until it exists. Nothing
calls a real provider: ORIN_OFFLINE=1 is set by the app fixture.
"""
from __future__ import annotations

import pytest

pytest.importorskip("orin.ai.service", reason="waiting on orin.ai.service", exc_type=ImportError)

API = "/api/v1"
EVENT_FIELDS = {"type", "intent", "text", "card", "job_id", "message", "error"}
SPEC = {"genre_id": "carnatic", "mood": ["calm"], "instruments": ["veena"], "duration_s": 30,
        "sections": [{"genre_id": "carnatic", "description": "slow opening on the veena", "duration_s": 30}]}


@pytest.fixture
def thread(api_client, api_headers):
    return api_client.post(f"{API}/chat/threads", headers=api_headers, json={}).json()


@pytest.fixture
def send(api_client, api_headers, api_sse):
    """send(thread_id, text, **fields) -> the ChatEvents of the reply, in order."""

    def post(thread_id, text, headers=None, **fields):
        response = api_client.post(f"{API}/chat/threads/{thread_id}/messages", headers=headers or api_headers,
                                   json={"text": text, **fields})
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache, no-transform"
        events = api_sse(response.text)
        assert all(set(e) == EVENT_FIELDS for e in events)
        return events

    return post


def types(events):
    return [e["type"] for e in events]


def messages(client, headers, thread_id):
    return client.get(f"{API}/chat/threads/{thread_id}/messages", headers=headers).json()


@pytest.fixture
def conversion_id(api_app, api_client, api_headers):
    """A finished conversion with a real loss report, written straight to the database
    so these tests do not depend on the audio pipeline."""
    pytest.importorskip("orin.formats.engine", reason="waiting on orin.formats.engine", exc_type=ImportError)
    from orin.convert import convert
    from orin.formats.base import ConvertOptions
    from orin.ingest.notes import parse_notes
    from orin.ir import dump_ir

    from server.db import ConversionRow, new_id, now_iso

    ir = parse_notes("tonic: D3\nS R2 G3~ P D2 S' , ,\nD2 P G3/ R2 S , , ,", format_id="carnatic", syntax="sargam")
    result = convert(ir, "western_classical", ConvertOptions())
    assert result.loss.items, "the fixture needs a conversion that loses something"
    row = ConversionRow(
        id=new_id(), user_id=api_client.get(f"{API}/auth/me", headers=api_headers).json()["id"],
        notes_text="S R2 G3", input_format="carnatic", detected_format="carnatic", output_format="western_classical",
        ir_json=dump_ir(ir), loss_json=result.loss.model_dump_json(), status="done", created_at=now_iso(),
    )
    with api_app.state.ctx.db.session() as s:
        s.add(row)
    return row.id


# --- explain ------------------------------------------------------------------------

def test_explain_loss_report_and_cache(api_client, api_headers, conversion_id):
    def explain(**body):
        response = api_client.post(f"{API}/explain", headers=api_headers, json={"conversion_id": conversion_id, **body})
        assert response.status_code == 200, response.text
        assert set(response.json()) == {"text", "try_this", "source", "cached", "key", "level"}
        return response.json()

    first = explain()
    assert first["text"] and isinstance(first["try_this"], list) and first["key"]
    assert first["source"] == "offline" and first["cached"] is False
    assert first["level"] == "beginner"  # the user's preference when none is given

    again = explain()
    assert again == {**first, "cached": True}  # same question, served from the cache

    advanced = explain(level="advanced")
    assert advanced["cached"] is False and advanced["level"] == "advanced"
    assert explain(level="advanced")["cached"] is True
    one = explain(item_indexes=[0])
    assert one["cached"] is False and one["text"]
    assert explain(item_indexes=[0, 0])["cached"] is True  # the same selection
    asked = explain(question="Why can't a piano play this?")
    assert asked["cached"] is False and asked["text"]

    # The level follows the profile when the request leaves it out.
    api_client.patch(f"{API}/auth/me", headers=api_headers, json={"level": "advanced"})
    assert explain() == {**advanced, "cached": True}

    bad = api_client.post(f"{API}/explain", headers=api_headers, json={"conversion_id": conversion_id, "item_indexes": [999]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    missing = api_client.post(f"{API}/explain", headers=api_headers, json={"conversion_id": "nope"})
    assert missing.status_code == 404


def test_explain_needs_a_finished_conversion(api_client, api_headers, api_wait_job):
    ref = api_client.post(f"{API}/convert", headers=api_headers,
                          json={"notes_text": "S R2 XX", "notes_syntax": "sargam", "input_format": "carnatic",
                                "output_format": "western_classical"}).json()
    api_wait_job(ref["job_id"], api_headers)  # fails: bad notes (or no pipeline yet)
    response = api_client.post(f"{API}/explain", headers=api_headers, json={"conversion_id": ref["conversion_id"]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "not_ready"


# --- narrate ------------------------------------------------------------------------

def test_narrate_offline_is_a_labelled_standin(api_client, api_headers):
    response = api_client.post(f"{API}/narrate", headers=api_headers, json={"text": "  A gamaka is a way of moving a note.  "})
    assert response.status_code == 200, response.text
    narration = response.json()
    assert set(narration) == {"asset", "standin", "text", "message"}
    assert narration["standin"] is True and narration["asset"] is None  # no audio: the browser reads the text
    assert narration["text"] == "A gamaka is a way of moving a note." and narration["message"]
    assert api_client.get(f"{API}/assets?source=narration", headers=api_headers).json() == []
    assert api_client.post(f"{API}/narrate", headers=api_headers, json={"text": ""}).status_code == 422
    assert api_client.post(f"{API}/narrate", headers=api_headers, json={"text": "x" * 2501}).status_code == 422


def test_narrate_audio_is_stored_and_cached_by_text(api_client, api_headers, api_login, api_wav, monkeypatch):
    from orin.ai import service
    from orin.ai.types import NarrationResult, ProviderMeta

    calls = []

    def narrate(text, *, request_id):
        calls.append(text)
        return NarrationResult(audio=api_wav(0.3, 300.0 + len(text)), media_type="audio/wav", ext="wav", standin=False,
                               provider=ProviderMeta(name="elevenlabs", model="tts-test", request_id=request_id))

    monkeypatch.setattr(service, "narrate", narrate)
    first = api_client.post(f"{API}/narrate", headers=api_headers, json={"text": "Sa is the home note."}).json()
    assert first["standin"] is False and first["text"] == "Sa is the home note."
    asset = first["asset"]
    assert asset["kind"] == "audio" and asset["source"] == "narration" and asset["standin"] is False
    assert asset["meta"]["transcript"] == "Sa is the home note."  # the transcript travels with the audio
    assert api_client.get(asset["url"]).status_code == 200

    second = api_client.post(f"{API}/narrate", headers=api_headers, json={"text": "Sa is the home note."}).json()
    assert second["asset"]["id"] == asset["id"] and len(calls) == 1  # cached by text hash
    other = api_client.post(f"{API}/narrate", headers=api_headers, json={"text": "Pa is the fifth."}).json()
    assert other["asset"]["id"] != asset["id"] and len(calls) == 2
    # The cache is per user: someone else's request is made again and stored for them.
    theirs = api_client.post(f"{API}/narrate", headers=api_login(), json={"text": "Sa is the home note."}).json()
    assert theirs["asset"]["id"] != asset["id"] and len(calls) == 3


# --- chat ---------------------------------------------------------------------------

def test_chat_question_streams_intent_deltas_message_done(api_client, api_headers, thread, send):
    events = send(thread["id"], "What is a raga?")
    kinds = types(events)
    assert kinds[0] == "intent" and events[0]["intent"] == "chat"
    assert kinds[-2:] == ["message", "done"] and "error" not in kinds
    assert set(kinds[1:-2]) == {"delta"} and len(kinds) >= 4
    reply = "".join(e["text"] for e in events if e["type"] == "delta")
    message = events[-2]["message"]
    assert message["role"] == "assistant" and message["thread_id"] == thread["id"]
    assert message["text"] == reply and "raga" in reply.lower()
    assert message["cards"] == [] and message["spec"] is None and message["meta"]["intent"] == "chat"

    stored = messages(api_client, api_headers, thread["id"])
    assert [(m["role"], m["text"]) for m in stored] == [("user", "What is a raga?"), ("assistant", reply)]
    assert stored[1] == message
    # A new thread is titled from its first message; a chosen title is left alone.
    listed = api_client.get(f"{API}/chat/threads", headers=api_headers).json()
    assert listed[0]["id"] == thread["id"] and listed[0]["title"] == "What is a raga?"
    assert listed[0]["updated_at"] > thread["updated_at"]
    named = api_client.post(f"{API}/chat/threads", headers=api_headers, json={"title": "Homework"}).json()
    send(named["id"], "What is a tala?")
    assert api_client.get(f"{API}/chat/threads", headers=api_headers).json()[0]["title"] == "Homework"


def test_chat_request_validation(api_client, api_headers, thread):
    url = f"{API}/chat/threads/{thread['id']}/messages"
    assert api_client.post(url, headers=api_headers, json={"text": ""}).status_code == 422
    assert api_client.post(url, headers=api_headers, json={"text": "x" * 4001}).status_code == 422
    bad_genre = api_client.post(url, headers=api_headers, json={"text": "make music", "genre_id": "<script>"})
    assert bad_genre.status_code == 422 and bad_genre.json()["error"]["code"] == "invalid_genre"
    assert api_client.post(f"{API}/chat/threads/nope/messages", headers=api_headers, json={"text": "hi"}).status_code == 404
    assert messages(api_client, api_headers, thread["id"]) == []  # a refused request stores nothing


def test_chat_generate_returns_a_spec_card(api_client, api_headers, thread, send):
    events = send(thread["id"], "Make a calm carnatic piece with veena, 30 seconds")
    assert events[0] == {**dict.fromkeys(EVENT_FIELDS), "type": "intent", "intent": "generate"}
    assert types(events)[-2:] == ["message", "done"]
    cards = [e["card"] for e in events if e["type"] == "card"]
    assert len(cards) == 1 and cards[0]["kind"] == "spec"
    spec = cards[0]["spec"]
    assert spec["genre_id"] == "carnatic" and spec["duration_s"] == 30 and "veena" in spec["instruments"]
    assert cards[0]["notes"]  # assumptions are shown, never hidden
    message = events[-2]["message"]
    assert message["spec"] == spec and message["cards"] == cards and message["text"]
    assert not any(e["type"] == "job" for e in events)  # nothing is generated until the user confirms

    # The spec is context for the next turn ("make it slower" edits it, it does not start over).
    follow = send(thread["id"], "make it slower")
    assert follow[0]["intent"] == "generate"
    card = next(e["card"] for e in follow if e["type"] == "card")
    assert card["spec"]["genre_id"] == "carnatic" and "veena" in card["spec"]["instruments"]


def test_chat_genre_chip_and_artist_names(api_client, api_headers, thread, send):
    events = send(thread["id"], "something gentle for studying", genre_id="arabic_maqam")
    assert events[0]["intent"] == "generate"
    assert next(e["card"] for e in events if e["type"] == "card")["spec"]["genre_id"] == "arabic_maqam"

    # A named artist is not passed on, and the user is told (spec 10.7).
    events = send(thread["id"], "Make a calm piano piece like Taylor Swift")
    card = next(e["card"] for e in events if e["type"] == "card")
    assert any("Taylor Swift" in note for note in card["notes"])
    assert "Taylor Swift" not in str(card["spec"])
    assert messages(api_client, api_headers, thread["id"])[-2]["text"] == "Make a calm piano piece like Taylor Swift"


def test_chat_transcribe_asks_for_a_file_or_starts_a_job(api_client, api_headers, thread, send, api_wav, api_wait_job):
    events = send(thread["id"], "Please transcribe this recording")
    assert events[0]["intent"] == "transcribe" and types(events)[-2:] == ["message", "done"]
    assert not any(e["type"] in ("job", "card") for e in events)
    assert "attach" in events[-2]["message"]["text"].lower()

    asset = api_client.post(f"{API}/assets", headers=api_headers, files={"file": ("tune.wav", api_wav(1.0))}).json()
    events = send(thread["id"], "Please transcribe this recording", attachment_asset_ids=[asset["id"]])
    kinds = types(events)
    assert kinds[0] == "intent" and events[0]["intent"] == "transcribe" and kinds[-2:] == ["message", "done"]
    job_event = next(e for e in events if e["type"] == "job")
    card = next(e["card"] for e in events if e["type"] == "card")
    assert kinds.index("job") < kinds.index("card") < kinds.index("message")
    assert card["kind"] == "conversion" and card["job_id"] == job_event["job_id"] and card["conversion_id"]
    message = events[-2]["message"]
    assert message["cards"] == [card] and "tune.wav" in message["text"]
    stored = messages(api_client, api_headers, thread["id"])
    assert stored[-2]["track_ids"] == [asset["id"]]  # the user's message records its attachment

    # The card points at a real job and a real conversion of that file, to Western staff notation.
    job = api_wait_job(card["job_id"], api_headers)
    assert job["type"] == "convert" and job["status"] in ("done", "failed")  # failed only if the pipeline is missing
    conversion = api_client.get(f"{API}/conversions/{card['conversion_id']}", headers=api_headers).json()
    assert conversion["source_asset"]["id"] == asset["id"]
    assert conversion["input_format"] == "auto" and conversion["output_format"] == "western_classical"


def test_chat_convert_says_inspired_by(api_client, api_headers, thread, send, api_wav, api_wait_job):
    asset = api_client.post(f"{API}/assets", headers=api_headers, files={"file": ("alapana.wav", api_wav(1.0, 220.0))}).json()
    events = send(thread["id"], "Convert this to a western pop arrangement", attachment_asset_ids=[asset["id"]])
    assert events[0]["intent"] == "convert"
    card = next(e["card"] for e in events if e["type"] == "card")
    assert card["kind"] == "conversion" and card["conversion_id"]
    text = events[-2]["message"]["text"]
    assert "inspired by" in text and "not an exact copy" in text  # generation cannot reproduce the melody (spec 10.8)
    assert any("inspired by" in note for note in card["notes"])
    api_wait_job(card["job_id"], api_headers)


def test_chat_with_loss_report_context(api_client, api_headers, thread, send, conversion_id, monkeypatch):
    from orin.ai import service

    seen = {}
    real = service.chat_reply_stream

    def spy(text, *, history, prefs=None, context=None):
        seen.update(history=list(history), prefs=prefs, context=context)
        return real(text, history=history, prefs=prefs, context=context)

    monkeypatch.setattr(service, "chat_reply_stream", spy)
    api_client.patch(f"{API}/auth/me", headers=api_headers, json={"notes": ["I play piano"], "level": "intermediate"})
    send(thread["id"], "What is a gamaka?")
    events = send(thread["id"], "Why was that lost?", context={"conversion_id": conversion_id, "item_indexes": [0]})
    assert events[0]["intent"] == "chat" and types(events)[-1] == "done"
    assert "carnatic" in seen["context"] and "western_classical" in seen["context"] and "Fidelity" in seen["context"]
    assert "They selected these items" in seen["context"]
    assert seen["prefs"]["notes"] == ["I play piano"] and seen["prefs"]["level"] == "intermediate"
    # History is the earlier turns, oldest first, without the message being answered.
    assert [(t.role, t.text) for t in seen["history"]][0] == ("user", "What is a gamaka?")
    assert len(seen["history"]) == 2 and seen["history"][1].role == "assistant"


def test_chat_blocked_prompt_gets_a_polite_reply(api_client, api_headers, thread, send, monkeypatch):
    from orin.ai import service
    from orin.ai.types import SanitizeResult

    monkeypatch.setattr(service, "sanitize_prompt", lambda text: SanitizeResult(
        text="", blocked=True, reason="This request looks abusive, so Orin will not pass it on."))
    monkeypatch.setattr(service, "classify_intent", lambda *a, **k: pytest.fail("a blocked prompt goes no further"))
    events = send(thread["id"], "something abusive")
    assert types(events) == ["intent", "delta", "message", "done"]
    text = events[-2]["message"]["text"]
    assert text.startswith("Sorry") and "will not pass it on" in text
    assert len(messages(api_client, api_headers, thread["id"])) == 2


def test_chat_failure_becomes_an_error_event(api_client, api_headers, thread, send, monkeypatch):
    from orin.ai import service

    def explode(*args, **kwargs):
        raise RuntimeError("provider said no, key=" + "sk-" + "orin-fake-key-for-tests-0001")

    monkeypatch.setattr(service, "chat_reply_stream", explode)
    events = send(thread["id"], "What is a raga?")  # still a 200 and a well-formed stream
    assert types(events) == ["intent", "error"]
    error = events[-1]["error"]
    assert set(error) == {"code", "message", "hint", "retryable"} and error["code"] == "internal_error"
    assert "provider said no" not in str(events) and "orin-fake-key" not in str(events)
    stored = messages(api_client, api_headers, thread["id"])
    assert [m["role"] for m in stored] == ["user"]  # the question is kept; no half reply is stored

    def half(*args, **kwargs):
        yield "A raga is "
        raise RuntimeError("connection dropped")

    monkeypatch.setattr(service, "chat_reply_stream", half)
    events = send(thread["id"], "What is a raga?")
    assert types(events) == ["intent", "delta", "error"]


# --- generate -----------------------------------------------------------------------

def generate(client, headers, wait, spec, thread_id=None):
    response = client.post(f"{API}/generate", headers=headers, json={"spec": spec, "thread_id": thread_id})
    assert response.status_code == 200, response.text
    assert response.json()["conversion_id"] is None
    return wait(response.json()["job_id"], headers)


def test_generate_offline_makes_a_labelled_standin_in_the_thread(api_client, api_headers, thread, api_wait_job):
    job = generate(api_client, api_headers, api_wait_job, SPEC, thread["id"])
    assert job["type"] == "generate" and job["status"] == "done", job["error"]
    assert job["result"]["kind"] == "message"

    stored = messages(api_client, api_headers, thread["id"])
    assert len(stored) == 1 and stored[0]["id"] == job["result"]["id"] and stored[0]["role"] == "assistant"
    message = stored[0]
    assert "stand-in" in message["text"]  # said in words, not only in a flag
    card = message["cards"][0]
    assert card["kind"] == "audio" and card["standin"] is True and card["standin_reason"]
    asset = card["asset"]
    assert asset["kind"] == "audio" and asset["source"] == "standin" and asset["standin"] is True
    assert asset["duration_s"] and asset["duration_s"] > 1 and message["track_ids"] == [asset["id"]]

    # Reproducibility (spec 10.3 step 10): the spec and the provider record travel with the track.
    assert card["spec"]["genre_id"] == "carnatic" and message["spec"] == card["spec"]
    assert card["provider"]["name"] == "offline" and card["provider"]["request_id"] and card["provider"]["duration_s"]
    assert asset["meta"]["spec"] == card["spec"] and asset["meta"]["provider"]["name"] == "offline"
    assert asset["meta"]["job_id"] == job["id"] and asset["meta"]["standin_reason"] == card["standin_reason"]

    # It is in the Library, it plays from its signed link, and its waveform is ready.
    library = api_client.get(f"{API}/assets?source=standin", headers=api_headers).json()
    assert [a["id"] for a in library] == [asset["id"]]
    audio = api_client.get(asset["url"])
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    peaks = api_client.get(f"{API}/assets/{asset['id']}/peaks", headers=api_headers).json()
    assert len(peaks["peaks"]) == 800 and max(peaks["peaks"]) > 0.05
    assert api_client.get(f"{API}/chat/threads", headers=api_headers).json()[0]["updated_at"] > thread["updated_at"]


def test_generate_without_a_thread_returns_the_asset(api_client, api_headers, api_wait_job):
    job = generate(api_client, api_headers, api_wait_job, {"genre_id": "jazz", "duration_s": 12})
    assert job["status"] == "done", job["error"]
    assert job["result"]["kind"] == "asset"
    asset = api_client.get(f"{API}/assets/{job['result']['id']}", headers=api_headers).json()
    assert asset["source"] == "standin" and asset["standin"] is True and asset["meta"]["spec"]["genre_id"] == "jazz"
    assert api_client.get(f"{API}/chat/threads", headers=api_headers).json() == []


def test_generate_labels_real_provider_audio_as_generated(api_client, api_headers, thread, api_wait_job, api_wav, monkeypatch):
    from orin.ai import service
    from orin.ai.types import MusicResult, ProviderMeta

    seen = {}

    def compose(spec, *, request_id, cancelled=None):
        seen.update(request_id=request_id, spec=spec, cancelled=cancelled())
        return MusicResult(audio=api_wav(2.0, 196.0), media_type="audio/wav", ext="wav", standin=False,
                           provider=ProviderMeta(name="elevenlabs", model="music-test", request_id=request_id, duration_s=2.0))

    monkeypatch.setattr(service, "compose_music", compose)
    job = generate(api_client, api_headers, api_wait_job, SPEC, thread["id"])
    assert job["status"] == "done", job["error"]
    assert seen["request_id"] == job["id"] and seen["cancelled"] is False and seen["spec"].genre_id == "carnatic"
    card = messages(api_client, api_headers, thread["id"])[0]["cards"][0]
    assert card["standin"] is False and card["standin_reason"] is None
    assert card["asset"]["source"] == "generated" and card["asset"]["standin"] is False
    assert card["provider"] == {"name": "elevenlabs", "model": "music-test", "request_id": job["id"], "duration_s": 2.0}

    # Deleting the track leaves the message readable, without a dead link.
    api_client.delete(f"{API}/assets/{card['asset']['id']}", headers=api_headers)
    gone = messages(api_client, api_headers, thread["id"])[0]["cards"][0]
    assert gone["asset"] is None and "deleted" in gone["text"]


@pytest.mark.parametrize("change,code", [
    ({"duration_s": 5}, "invalid_spec"),
    ({"duration_s": 9999}, "invalid_spec"),
    ({"genre_id": "<b>loud</b>"}, "invalid_genre"),
    ({"genre_id": ""}, "invalid_spec"),
    ({"sections": [{"genre_id": "carnatic; DROP TABLE", "description": "x", "duration_s": 10}]}, "invalid_genre"),
    ({"tempo_bpm": 5000}, "invalid_spec"),
    ({"vocals": "screaming"}, "validation_error"),
])
def test_generate_validates_the_spec(api_client, api_headers, change, code):
    response = api_client.post(f"{API}/generate", headers=api_headers, json={"spec": {**SPEC, **change}})
    assert response.status_code == 422 and response.json()["error"]["code"] == code
    assert response.json()["error"]["message"]


def test_generate_daily_quota(api_make_app, api_login, api_wait_job):
    _, client = api_make_app(daily_generations=1)
    headers = api_login(client=client)
    spec = {"genre_id": "rock", "duration_s": 10}
    first = client.post(f"{API}/generate", headers=headers, json={"spec": spec})
    assert first.status_code == 200
    assert api_wait_job(first.json()["job_id"], headers, client=client)["status"] == "done"
    second = client.post(f"{API}/generate", headers=headers, json={"spec": spec})
    assert second.status_code == 429 and second.json()["error"]["code"] == "quota_exceeded"
    assert "1 generation" in second.json()["error"]["message"]
    other = api_login(client=client)  # the quota is per user
    third = client.post(f"{API}/generate", headers=other, json={"spec": spec})
    assert third.status_code == 200 and api_wait_job(third.json()["job_id"], other, client=client)["status"] == "done"


def test_generate_failure_and_blocked_prompt(api_client, api_headers, thread, api_wait_job, monkeypatch):
    from orin.ai import service
    from orin.ai.types import SanitizeResult

    monkeypatch.setattr(service, "sanitize_prompt", lambda text: SanitizeResult(text="", blocked=True, reason="Not something Orin will make."))
    job = generate(api_client, api_headers, api_wait_job, SPEC, thread["id"])
    assert job["status"] == "failed" and job["error"]["code"] == "prompt_blocked"
    assert job["error"]["message"] == "Not something Orin will make." and job["error"]["hint"]
    assert messages(api_client, api_headers, thread["id"]) == []  # nothing was made or posted
    assert api_client.get(f"{API}/assets", headers=api_headers).json() == []
