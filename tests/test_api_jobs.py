"""API: background jobs. Progress, the event stream, failure, cancel, restart recovery
(spec 13.3, 7.16). The job functions here are dummies; no pipeline is needed."""
from __future__ import annotations

import threading
import time

import pytest

API = "/api/v1"
TERMINAL = {"done", "failed", "cancelled", "awaiting_input"}
JOB_FIELDS = {"id", "type", "status", "progress", "stage", "message", "error", "result", "created_at", "updated_at"}


@pytest.fixture
def user(api_client, api_headers):
    """(user id, headers) for a fresh profile."""
    return api_client.get(f"{API}/auth/me", headers=api_headers).json()["id"], api_headers


def poll(client, headers, job_id, until, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"{API}/jobs/{job_id}", headers=headers).json()
        if until(job):
            return job
        time.sleep(0.01)
    raise AssertionError(f"job never got there: {job}")


def test_progress_then_done(api_app, api_client, user, api_sse):
    user_id, headers = user
    release = threading.Event()

    def work(job):
        job.progress("pitch", 0.4, "Tracking pitch")
        assert release.wait(10)
        job.progress("pitch", 0.2, "A stage that reports a lower number")  # the bar never runs backwards
        job.progress("notation", 0.9, "Writing the notation")
        return {"kind": "conversion", "id": "c-123"}

    job_id = api_app.state.ctx.jobs.submit(user_id, "convert", work, message="Waiting to start")
    running = poll(api_client, headers, job_id, lambda j: j["stage"] == "pitch")
    assert set(running) == JOB_FIELDS
    assert running["status"] == "running" and running["type"] == "convert"
    assert running["progress"] == pytest.approx(0.4) and running["message"] == "Tracking pitch"
    assert running["error"] is None and running["result"] is None

    release.set()
    response = api_client.get(f"{API}/jobs/{job_id}/events", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert all(block.startswith("data: ") for block in response.text.strip().split("\n\n"))
    events = api_sse(response.text)
    assert all(set(e) == JOB_FIELDS for e in events)
    assert [e["status"] for e in events if e["status"] in TERMINAL] == ["done"]  # the stream ends at the terminal status
    assert events[-1]["status"] == "done" and events[-1]["progress"] == 1.0
    assert events[-1]["result"] == {"kind": "conversion", "id": "c-123"}
    progress = [e["progress"] for e in events]
    assert progress == sorted(progress)

    # A finished job streams its final state once and ends.
    again = api_sse(api_client.get(f"{API}/jobs/{job_id}/events", headers=headers).text)
    assert len(again) == 1 and again[0] == events[-1]
    assert api_client.get(f"{API}/jobs/{job_id}", headers=headers).json() == events[-1]


def test_stream_sends_the_current_state_first(api_app, api_client, user, api_sse):
    user_id, headers = user
    release = threading.Event()

    def work(job):
        job.progress("separate", 0.3, "Separating the melody")
        assert release.wait(10)
        return None

    job_id = api_app.state.ctx.jobs.submit(user_id, "convert", work)
    poll(api_client, headers, job_id, lambda j: j["stage"] == "separate")
    threading.Timer(0.3, release.set).start()  # finish while the stream is open
    events = api_sse(api_client.get(f"{API}/jobs/{job_id}/events", headers=headers).text)
    assert events[0]["status"] == "running" and events[0]["stage"] == "separate"
    assert events[-1]["status"] == "done" and events[-1]["result"] is None
    assert len(events) >= 2


def test_failing_job_has_the_error_shape(api_app, api_client, user, api_wait_job, caplog):
    from orin.errors import IngestError

    user_id, headers = user
    # Made-up, and split so that no key-shaped string sits in the source (secret scanners block those).
    secret = "sk-" + "orin-fake-key-for-tests-0001"

    def crash(job):
        job.progress("pitch", 0.5, "Tracking pitch")
        raise RuntimeError(f"/secret/path exploded, key={secret}")

    def refuse(job):
        raise IngestError("That recording is silent.", code="empty", hint="Record it again, closer to the microphone.")

    job = api_wait_job(api_app.state.ctx.jobs.submit(user_id, "convert", crash), headers)
    assert job["status"] == "failed" and job["result"] is None and job["progress"] == pytest.approx(0.5)
    assert set(job["error"]) == {"code", "message", "hint", "retryable"}
    assert job["error"]["code"] == "internal_error" and job["error"]["retryable"] is True
    assert "exploded" not in str(job) and secret not in str(job) and "Traceback" not in str(job)
    assert "exploded" in caplog.text and secret not in caplog.text  # logged for the operator, without the key

    job = api_wait_job(api_app.state.ctx.jobs.submit(user_id, "convert", refuse), headers)
    assert job["status"] == "failed"
    assert job["error"] == {"code": "empty", "message": "That recording is silent.",
                            "hint": "Record it again, closer to the microphone.", "retryable": False}
    assert job["message"] == "That recording is silent."


def test_cancel_a_running_job(api_app, api_client, user, api_sse):
    user_id, headers = user
    started, stopped, hook = threading.Event(), threading.Event(), threading.Event()

    def work(job):
        started.set()
        try:
            while True:
                job.progress("compose", 0.2, "Composing...")  # raises once the job is cancelled
                time.sleep(0.01)
        finally:
            stopped.set()

    job_id = api_app.state.ctx.jobs.submit(user_id, "generate", work, on_cancel=hook.set)
    assert started.wait(10)
    response = api_client.post(f"{API}/jobs/{job_id}/cancel", headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert stopped.wait(10) and hook.wait(10)  # the worker really stopped, and the cleanup hook ran
    events = api_sse(api_client.get(f"{API}/jobs/{job_id}/events", headers=headers).text)
    assert [e["status"] for e in events] == ["cancelled"]
    # Cancelling again, or cancelling a finished job, changes nothing.
    assert api_client.post(f"{API}/jobs/{job_id}/cancel", headers=headers).json()["status"] == "cancelled"


def test_cancel_a_queued_job_and_a_job_that_ignores_cancel(api_make_app, api_login, api_wait_job):
    app, client = api_make_app(job_workers=1)
    headers = api_login(client=client)
    user_id = client.get(f"{API}/auth/me", headers=headers).json()["id"]
    jobs = app.state.ctx.jobs
    release, ran, hook = threading.Event(), threading.Event(), threading.Event()

    def stubborn(job):  # never looks at the cancel flag
        assert release.wait(10)
        return {"kind": "asset", "id": "a-1"}

    first = jobs.submit(user_id, "generate", stubborn)
    queued = jobs.submit(user_id, "generate", lambda job: ran.set(), on_cancel=hook.set)  # waits for the only worker
    assert client.get(f"{API}/jobs/{queued}", headers=headers).json()["status"] == "queued"
    assert client.post(f"{API}/jobs/{queued}/cancel", headers=headers).json()["status"] == "cancelled"
    assert client.post(f"{API}/jobs/{first}/cancel", headers=headers).json()["status"] == "cancelled"
    release.set()
    assert hook.wait(10) and not ran.is_set()  # the queued job never ran; its hook did
    done = api_wait_job(first, headers, client=client)
    assert done["status"] == "cancelled" and done["result"] is None  # a late result does not undo a cancel


def test_done_job_cannot_be_cancelled(api_app, api_client, user, api_wait_job):
    user_id, headers = user
    job_id = api_app.state.ctx.jobs.submit(user_id, "narrate", lambda job: {"kind": "asset", "id": "a-9"})
    assert api_wait_job(job_id, headers)["status"] == "done"
    after = api_client.post(f"{API}/jobs/{job_id}/cancel", headers=headers).json()
    assert after["status"] == "done" and after["result"] == {"kind": "asset", "id": "a-9"}


def test_jobs_are_private(api_app, api_client, user, api_login):
    user_id, headers = user
    job_id = api_app.state.ctx.jobs.submit(user_id, "convert", lambda job: None)
    stranger = api_login()
    for method, path in (("GET", f"/jobs/{job_id}"), ("GET", f"/jobs/{job_id}/events"), ("POST", f"/jobs/{job_id}/cancel")):
        response = api_client.request(method, f"{API}{path}", headers=stranger)
        assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
    assert api_client.get(f"{API}/jobs/nope", headers=headers).status_code == 404
    assert api_client.get(f"{API}/jobs/nope/events", headers=headers).status_code == 404


def test_jobs_left_running_are_failed_at_startup(api_make_app, api_login):
    from server.config import Settings
    from server.db import JobRow, now_iso
    from server.main import create_app
    from starlette.testclient import TestClient

    app, client = api_make_app()
    headers = api_login("restart-user", client=client)
    user_id = client.get(f"{API}/auth/me", headers=headers).json()["id"]
    ctx = app.state.ctx
    with ctx.db.session() as s:  # what a killed process leaves behind
        for job_id, status in (("j-running", "running"), ("j-queued", "queued"), ("j-done", "done")):
            s.add(JobRow(id=job_id, user_id=user_id, type="convert", status=status, progress=0.3,
                         created_at=now_iso(), updated_at=now_iso()))
    ctx.db.dispose()

    restarted = TestClient(create_app(Settings(data_dir=ctx.settings.data_dir, offline=True)))
    headers = api_login("restart-user", client=restarted)
    for job_id in ("j-running", "j-queued"):
        job = restarted.get(f"{API}/jobs/{job_id}", headers=headers).json()
        assert job["status"] == "failed" and job["error"]["code"] == "interrupted" and job["error"]["retryable"] is True
    assert restarted.get(f"{API}/jobs/j-done", headers=headers).json()["status"] == "done"
