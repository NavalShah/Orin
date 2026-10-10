"""API: per-user isolation (spec 10.2). Another user's id answers 404, exactly like an id
that does not exist, and never returns or changes their data. Also thread CRUD, which
needs no AI layer."""
from __future__ import annotations

import pytest

API = "/api/v1"
NOTES = {"notes_text": "S R2 G3 P D2 S'", "input_format": "carnatic", "output_format": "western_classical"}
SPEC = {"genre_id": "carnatic", "duration_s": 30}


@pytest.fixture
def two_users(api_login):
    return api_login(), api_login()


def assert_hidden(response):
    assert response.status_code == 404, f"{response.request.method} {response.request.url} -> {response.status_code} {response.text}"
    assert response.json()["error"]["code"] == "not_found"


def test_assets_are_private(api_client, two_users, api_wav):
    owner, stranger = two_users
    data = api_wav(0.3, 432.0)
    asset = api_client.post(f"{API}/assets", headers=owner, files={"file": ("mine.wav", data)}).json()

    assert api_client.get(f"{API}/assets", headers=stranger).json() == []
    for method, path in (("GET", ""), ("GET", "/file"), ("GET", "/peaks"), ("DELETE", "")):
        assert_hidden(api_client.request(method, f"{API}/assets/{asset['id']}{path}", headers=stranger))
    # The stranger cannot use it as an input either.
    assert_hidden(api_client.post(f"{API}/convert", headers=stranger, json={"asset_id": asset["id"], "output_format": "carnatic"}))
    thread = api_client.post(f"{API}/chat/threads", headers=stranger, json={}).json()
    assert_hidden(api_client.post(f"{API}/chat/threads/{thread['id']}/messages", headers=stranger,
                                  json={"text": "transcribe this", "attachment_asset_ids": [asset["id"]]}))
    assert api_client.get(f"{API}/chat/threads/{thread['id']}/messages", headers=stranger).json() == []  # nothing was stored

    # Uploading the same bytes gives the stranger their own item; deleting it leaves the owner's alone.
    twin = api_client.post(f"{API}/assets", headers=stranger, files={"file": ("theirs.wav", data)}).json()
    assert twin["id"] != asset["id"] and twin["sha256"] == asset["sha256"]
    assert api_client.delete(f"{API}/assets/{twin['id']}", headers=stranger).status_code == 200
    assert api_client.get(f"{API}/assets/{asset['id']}", headers=owner).json()["filename"] == "mine.wav"
    assert api_client.get(asset["url"]).content == data


def test_conversions_are_private(api_client, two_users, api_wait_job):
    owner, stranger = two_users
    ref = api_client.post(f"{API}/convert", headers=owner, json=NOTES).json()
    api_wait_job(ref["job_id"], owner)  # done, or failed if the pipeline is not installed: the row exists either way
    conversion_id = ref["conversion_id"]
    mine = api_client.get(f"{API}/conversions/{conversion_id}", headers=owner)
    assert mine.status_code == 200 and mine.json()["notes_text"] == NOTES["notes_text"]
    assert [c["id"] for c in api_client.get(f"{API}/conversions", headers=owner).json()] == [conversion_id]

    assert api_client.get(f"{API}/conversions", headers=stranger).json() == []
    assert_hidden(api_client.get(f"{API}/conversions/{conversion_id}", headers=stranger))
    assert_hidden(api_client.get(f"{API}/conversions/{conversion_id}/ir", headers=stranger))
    assert_hidden(api_client.post(f"{API}/conversions/{conversion_id}/rerun", headers=stranger, json={"tonic_hz": 220}))
    assert_hidden(api_client.post(f"{API}/explain", headers=stranger, json={"conversion_id": conversion_id}))
    assert_hidden(api_client.get(f"{API}/jobs/{ref['job_id']}", headers=stranger))
    thread = api_client.post(f"{API}/chat/threads", headers=stranger, json={}).json()
    assert_hidden(api_client.post(f"{API}/chat/threads/{thread['id']}/messages", headers=stranger,
                                  json={"text": "what was lost?", "context": {"conversion_id": conversion_id}}))
    # The stranger's attempts changed nothing. Download links are re-signed on every read and
    # carry an expiry time, so they are left out of the comparison.
    def without_links(value):
        if isinstance(value, dict):
            return {k: without_links(v) for k, v in value.items() if k != "url"}
        if isinstance(value, list):
            return [without_links(v) for v in value]
        return value

    after = api_client.get(f"{API}/conversions/{conversion_id}", headers=owner).json()
    assert without_links(after) == without_links(mine.json())


def test_threads_are_private(api_client, two_users):
    owner, stranger = two_users
    thread = api_client.post(f"{API}/chat/threads", headers=owner, json={"title": "My raga questions"}).json()

    assert api_client.get(f"{API}/chat/threads", headers=stranger).json() == []
    assert_hidden(api_client.get(f"{API}/chat/threads/{thread['id']}/messages", headers=stranger))
    assert_hidden(api_client.post(f"{API}/chat/threads/{thread['id']}/messages", headers=stranger, json={"text": "hello"}))
    assert_hidden(api_client.patch(f"{API}/chat/threads/{thread['id']}", headers=stranger, json={"title": "Taken"}))
    assert_hidden(api_client.delete(f"{API}/chat/threads/{thread['id']}", headers=stranger))
    assert_hidden(api_client.post(f"{API}/generate", headers=stranger, json={"spec": SPEC, "thread_id": thread["id"]}))

    assert api_client.get(f"{API}/chat/threads", headers=owner).json()[0]["title"] == "My raga questions"
    assert api_client.get(f"{API}/chat/threads/{thread['id']}/messages", headers=owner).json() == []


def test_projects_are_private(api_client, two_users):
    owner, stranger = two_users
    body = {"name": "Mine", "project": {"tracks": [], "secret": "owner only"}}
    project = api_client.post(f"{API}/projects", headers=owner, json=body).json()

    assert api_client.get(f"{API}/projects", headers=stranger).json() == []
    assert_hidden(api_client.get(f"{API}/projects/{project['id']}", headers=stranger))
    assert_hidden(api_client.put(f"{API}/projects/{project['id']}", headers=stranger, json={"name": "Theirs", "project": {}}))
    assert_hidden(api_client.post(f"{API}/projects/{project['id']}/duplicate", headers=stranger))
    assert_hidden(api_client.delete(f"{API}/projects/{project['id']}", headers=stranger))

    assert api_client.get(f"{API}/projects/{project['id']}", headers=owner).json() == project
    assert len(api_client.get(f"{API}/projects", headers=owner).json()) == 1


def test_prefs_are_private(api_client, two_users):
    owner, stranger = two_users
    api_client.patch(f"{API}/auth/me", headers=owner, json={"notes": ["I play the oud"], "level": "advanced"})
    theirs = api_client.get(f"{API}/auth/me", headers=stranger).json()["prefs"]
    assert theirs["notes"] == [] and theirs["level"] == "beginner"


def test_thread_crud(api_client, api_headers):
    assert api_client.get(f"{API}/chat/threads", headers=api_headers).json() == []
    first = api_client.post(f"{API}/chat/threads", headers=api_headers, json={}).json()
    assert set(first) == {"id", "title", "created_at", "updated_at", "summary"}
    assert first["title"] == "New chat" and first["summary"] is None
    second = api_client.post(f"{API}/chat/threads", headers=api_headers, json={"title": "Maqam Rast"}).json()
    assert second["title"] == "Maqam Rast"
    assert [t["id"] for t in api_client.get(f"{API}/chat/threads", headers=api_headers).json()] == [second["id"], first["id"]]

    renamed = api_client.patch(f"{API}/chat/threads/{first['id']}", headers=api_headers, json={"title": "Tala practice"}).json()
    assert renamed["title"] == "Tala practice" and renamed["updated_at"] > first["updated_at"]
    assert [t["id"] for t in api_client.get(f"{API}/chat/threads", headers=api_headers).json()] == [first["id"], second["id"]]
    assert api_client.patch(f"{API}/chat/threads/{first['id']}", headers=api_headers, json={"title": ""}).status_code == 422

    assert api_client.delete(f"{API}/chat/threads/{first['id']}", headers=api_headers).json() == {"ok": True}
    assert api_client.get(f"{API}/chat/threads/{first['id']}/messages", headers=api_headers).status_code == 404
    assert [t["id"] for t in api_client.get(f"{API}/chat/threads", headers=api_headers).json()] == [second["id"]]
