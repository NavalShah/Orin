"""API: the error shape, profile-picker sessions, and the security basics (spec 13.2, 13.4)."""
from __future__ import annotations

import hashlib

import pytest

API = "/api/v1"
# Made-up, and split so that no key-shaped string sits in the source (secret scanners block those).
FAKE_KEYS = ("AIza" + "-orin-fake-key-for-tests-0000000001", "sk-" + "orin-fake-key-for-tests-0001")


def assert_error(response, status, code=None):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "hint", "retryable"}
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert error["hint"] is None or isinstance(error["hint"], str)
    assert isinstance(error["retryable"], bool)
    assert "Traceback" not in response.text and 'File "' not in response.text
    if code:
        assert error["code"] == code
    return error


# --- error shape ------------------------------------------------------------------

def test_health_is_open(api_client):
    response = api_client.get(f"{API}/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["version"]
    assert set(body["offline"]) == {"llm", "music", "tts", "reason"}
    assert body["offline"]["llm"] is True  # ORIN_OFFLINE=1


def test_401_shape(api_client):
    error = assert_error(api_client.get(f"{API}/auth/me"), 401, "unauthorized")
    assert error["hint"]
    assert_error(api_client.get(f"{API}/auth/me", headers={"Authorization": "Bearer not-a-real-token"}), 401, "unauthorized")
    assert_error(api_client.get(f"{API}/auth/me", headers={"Authorization": "Basic abc"}), 401, "unauthorized")


def test_404_and_405_shape(api_client, api_headers):
    assert_error(api_client.get(f"{API}/no-such-endpoint"), 404, "not_found")
    assert_error(api_client.get("/somewhere/else"), 404, "not_found")
    assert_error(api_client.get(f"{API}/projects/does-not-exist", headers=api_headers), 404, "not_found")
    assert_error(api_client.delete(f"{API}/health"), 405, "method_not_allowed")


def test_422_shape_is_plain_language(api_client, api_headers):
    error = assert_error(api_client.post(f"{API}/auth/session", json={}), 422, "validation_error")
    assert "'name' is required" in error["message"]
    error = assert_error(api_client.post(f"{API}/auth/session", json={"name": ""}), 422, "validation_error")
    assert "name" in error["message"]
    assert_error(api_client.post(f"{API}/auth/session", content=b"{not json", headers={"Content-Type": "application/json"}),
                 422, "validation_error")
    error = assert_error(api_client.patch(f"{API}/auth/me", json={"level": "wizard"}, headers=api_headers), 422)
    assert "level" in error["message"] and "pydantic" not in error["message"].lower()


def test_unexpected_exception_is_a_clean_500(api_make_app, capsys, caplog):
    app, client = api_make_app()

    @app.get(f"{API}/boom")
    def boom():
        raise RuntimeError(f"kaboom with key {FAKE_KEYS[0]} and {FAKE_KEYS[1]}")

    response = client.get(f"{API}/boom")
    error = assert_error(response, 500, "internal_error")
    assert error["retryable"] is True
    assert "kaboom" not in response.text and "RuntimeError" not in response.text
    logged = caplog.text + "".join(capsys.readouterr())
    assert "kaboom" in logged  # the traceback is in the server log...
    for key in FAKE_KEYS:
        assert key not in response.text and key not in logged  # ...and keys are in neither


def test_orin_errors_map_to_400_and_422(api_make_app):
    from orin.errors import NotesParseError, ParseProblem, UnknownFormat

    app, client = api_make_app()

    @app.get(f"{API}/unknown-format")
    def unknown():
        raise UnknownFormat("'klingon' is not a format Orin knows.", hint="Known formats: carnatic.")

    @app.get(f"{API}/bad-notes")
    def bad_notes():
        raise NotesParseError([ParseProblem(code="E001", message="'X' is not a swara.", line=2, column=5)])

    error = assert_error(client.get(f"{API}/unknown-format"), 400, "unknown_format")
    assert error["hint"] == "Known formats: carnatic."
    error = assert_error(client.get(f"{API}/bad-notes"), 422, "notes_parse_error")
    assert "Line 2, column 5" in error["message"]


def test_redact_removes_key_shaped_text(monkeypatch):
    from server.errors import redact

    monkeypatch.setenv("ELEVENLABS_API_KEY", "plain-looking-secret-value")
    text = redact(f"call failed: {FAKE_KEYS[0]} / {FAKE_KEYS[1]} / plain-looking-secret-value / Authorization: Bearer abcdefghijklmnop")
    for secret in (*FAKE_KEYS, "plain-looking-secret-value", "abcdefghijklmnop"):
        assert secret not in text
    assert redact("Line 2, column 5: 'X' is not a swara.") == "Line 2, column 5: 'X' is not a swara."


# --- sessions ---------------------------------------------------------------------

def test_session_me_prefs_logout(api_app, api_client):
    response = api_client.post(f"{API}/auth/session", json={"name": "  Asha   Rao "})
    assert response.status_code == 200
    session = response.json()
    token, user = session["token"], session["user"]
    assert len(token) >= 32 and user["name"] == "Asha Rao"
    assert user["prefs"] == {"level": "beginner", "language": "en", "labels": "tradition", "plain_language": True, "notes": []}
    headers = {"Authorization": f"Bearer {token}"}

    assert api_client.get(f"{API}/auth/me", headers=headers).json() == user

    response = api_client.patch(f"{API}/auth/me", headers=headers, json={"level": "advanced", "notes": ["I play veena", "  "]})
    assert response.status_code == 200
    prefs = response.json()["prefs"]
    assert prefs["level"] == "advanced" and prefs["notes"] == ["I play veena"] and prefs["language"] == "en"
    assert api_client.get(f"{API}/auth/me", headers=headers).json()["prefs"] == prefs

    # Same name (any case) is the same profile, with a new token; both tokens work.
    again = api_client.post(f"{API}/auth/session", json={"name": "asha rao"}).json()
    assert again["user"]["id"] == user["id"] and again["token"] != token
    assert again["user"]["prefs"]["level"] == "advanced"

    # Only the SHA-256 of a token is stored.
    from sqlalchemy import select

    from server.db import SessionRow

    with api_app.state.ctx.db.session() as s:
        stored = set(s.execute(select(SessionRow.token_hash)).scalars().all())
    assert hashlib.sha256(token.encode()).hexdigest() in stored and token not in stored

    assert api_client.post(f"{API}/auth/logout", headers=headers).json() == {"ok": True}
    assert_error(api_client.get(f"{API}/auth/me", headers=headers), 401, "unauthorized")
    other = {"Authorization": f"Bearer {again['token']}"}
    assert api_client.get(f"{API}/auth/me", headers=other).status_code == 200  # logout ends one session only


@pytest.mark.parametrize("method,path", [
    ("GET", "/auth/me"), ("PATCH", "/auth/me"), ("POST", "/auth/logout"),
    ("GET", "/assets"), ("POST", "/assets"), ("GET", "/assets/x"), ("GET", "/assets/x/file"),
    ("GET", "/assets/x/peaks"), ("DELETE", "/assets/x"),
    ("POST", "/convert"), ("GET", "/jobs/x"), ("GET", "/jobs/x/events"), ("POST", "/jobs/x/cancel"),
    ("GET", "/conversions"), ("GET", "/conversions/x"), ("GET", "/conversions/x/ir"), ("POST", "/conversions/x/rerun"),
    ("POST", "/explain"), ("POST", "/narrate"),
    ("GET", "/chat/threads"), ("POST", "/chat/threads"), ("PATCH", "/chat/threads/x"), ("DELETE", "/chat/threads/x"),
    ("GET", "/chat/threads/x/messages"), ("POST", "/chat/threads/x/messages"), ("POST", "/generate"),
    ("GET", "/projects"), ("POST", "/projects"), ("GET", "/projects/x"), ("PUT", "/projects/x"),
    ("POST", "/projects/x/duplicate"), ("DELETE", "/projects/x"),
])
def test_every_user_endpoint_needs_a_token(api_client, method, path):
    assert_error(api_client.request(method, f"{API}{path}"), 401, "unauthorized")


@pytest.mark.parametrize("path", ["/health", "/formats", "/glossary"])
def test_open_endpoints(api_client, path):
    assert api_client.get(f"{API}{path}").status_code == 200


# --- security ---------------------------------------------------------------------

def test_cors_allows_only_the_app_origin(api_client):
    allowed, other = "http://localhost:3000", "http://evil.example"
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    ok = api_client.options(f"{API}/convert", headers={"Origin": allowed, **preflight})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == allowed
    assert "authorization" in ok.headers["access-control-allow-headers"].lower()
    assert api_client.options(f"{API}/convert", headers={"Origin": "http://127.0.0.1:3000", **preflight}).status_code == 200
    bad = api_client.options(f"{API}/convert", headers={"Origin": other, **preflight})
    assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers
    assert "access-control-allow-origin" not in api_client.get(f"{API}/health", headers={"Origin": other}).headers
    # Error responses carry the header too, so the browser can read the error body.
    denied = api_client.get(f"{API}/auth/me", headers={"Origin": allowed})
    assert denied.status_code == 401 and denied.headers["access-control-allow-origin"] == allowed
    assert "access-control-allow-credentials" not in denied.headers  # bearer tokens, no cookies


def test_cors_follows_the_configured_origin(api_make_app):
    _, client = api_make_app(app_origin="https://orin.example")
    assert client.get(f"{API}/health", headers={"Origin": "https://orin.example"}).headers["access-control-allow-origin"] == "https://orin.example"
    assert "access-control-allow-origin" not in client.get(f"{API}/health", headers={"Origin": "http://localhost:3000"}).headers


def test_rate_limit_returns_429_per_user(api_make_app, api_login):
    _, client = api_make_app(rate_limits={"convert": (2, 60.0)})
    one, two = api_login(client=client), api_login(client=client)
    body = {"notes_text": "S R2 G3", "input_format": "carnatic", "output_format": "western_classical"}
    assert client.post(f"{API}/convert", json=body, headers=one).status_code == 200
    assert client.post(f"{API}/convert", json=body, headers=one).status_code == 200
    limited = client.post(f"{API}/convert", json=body, headers=one)
    error = assert_error(limited, 429, "rate_limited")
    assert error["retryable"] is True and int(limited.headers["retry-after"]) >= 1
    assert client.post(f"{API}/convert", json=body, headers=two).status_code == 200  # buckets are per user


def test_rate_limited_endpoints(api_make_app, api_login):
    _, client = api_make_app(rate_limits={"convert": (0, 60.0), "generate": (0, 60.0), "narrate": (0, 60.0), "chat": (0, 60.0)})
    headers = api_login(client=client)
    for path in ("/convert", "/conversions/x/rerun", "/generate", "/narrate", "/chat/threads/x/messages"):
        assert_error(client.post(f"{API}{path}", json={}, headers=headers), 429, "rate_limited")
