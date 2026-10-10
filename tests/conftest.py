"""Shared fixtures for the API tests (tests/test_api_*.py). Kept small.

Nothing is imported from ``server`` or ``orin`` at collection time, so a module that is
still being written cannot break other test files.
"""
from __future__ import annotations

import io
import json
import uuid

import pytest

API = "/api/v1"


@pytest.fixture(scope="session")
def api_app(tmp_path_factory):
    """One app for the session: offline stand-ins, and a throwaway data dir."""
    from server.config import Settings
    from server.main import create_app

    env = pytest.MonkeyPatch()
    env.setenv("ORIN_OFFLINE", "1")  # the AI layer reads this itself, at call time
    app = create_app(Settings(data_dir=tmp_path_factory.mktemp("orin-data"), offline=True))
    yield app
    app.state.ctx.jobs.shutdown()
    env.undo()


@pytest.fixture
def api_make_app(tmp_path, monkeypatch):
    """make(**settings) -> (app, client) with its own data dir, for tests that need other limits."""
    made = []

    def make(**overrides):
        from server.config import Settings
        from server.main import create_app
        from starlette.testclient import TestClient

        monkeypatch.setenv("ORIN_OFFLINE", "1")
        app = create_app(Settings(data_dir=tmp_path / f"data{len(made)}", offline=True, **overrides))
        made.append(app)
        return app, TestClient(app)

    yield make
    for app in made:
        app.state.ctx.jobs.shutdown()
        app.state.ctx.db.dispose()


@pytest.fixture
def api_client(api_app):
    from starlette.testclient import TestClient

    return TestClient(api_app)


@pytest.fixture
def api_login(api_client):
    """login(name=None) -> headers for that profile (a fresh, unique one by default)."""

    def login(name=None, client=None):
        response = (client or api_client).post(f"{API}/auth/session", json={"name": name or f"user-{uuid.uuid4().hex[:10]}"})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['token']}"}

    return login


@pytest.fixture
def api_headers(api_login):
    return api_login()


@pytest.fixture(scope="session")
def api_wav():
    """wav(seconds, freq) -> bytes of a mono sine WAV, made in memory."""

    def wav(seconds=1.0, freq=440.0, sr=22050, amp=0.5):
        import numpy as np
        import soundfile as sf

        t = np.arange(int(seconds * sr)) / sr
        buffer = io.BytesIO()
        sf.write(buffer, amp * np.sin(2 * np.pi * freq * t), sr, format="WAV", subtype="PCM_16")
        return buffer.getvalue()

    return wav


def _sse_events(text):
    out = []
    for block in text.split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
        if data:
            out.append(json.loads(data))
    return out


@pytest.fixture(scope="session")
def api_sse():
    """sse(text) -> the JSON payloads of a server-sent event stream, in order."""
    return _sse_events


@pytest.fixture
def api_wait_job(api_client):
    """wait(job_id, headers) -> the job at its terminal status, read from the event stream."""

    def wait(job_id, headers, client=None):
        response = (client or api_client).get(f"{API}/jobs/{job_id}/events", headers=headers)
        assert response.status_code == 200, response.text
        return _sse_events(response.text)[-1]

    return wait
