"""API: Workspace projects (spec 12.7). The project JSON is opaque to the server."""
from __future__ import annotations

API = "/api/v1"

# The shape lib/workspace/project.ts saves: positions in seconds, clips at the top level.
PROJECT = {
    "version": 1, "name": "Mohanam sketch", "tempo": 92, "timeSignature": [4, 4], "tonicHz": 146.83,
    "tracks": [{"id": "t1", "name": "Voice", "kind": "audio"}, {"id": "t2", "name": "Keys", "kind": "midi"}],
    "clips": [
        {"id": "c1", "trackId": "t1", "kind": "audio", "assetId": "a1", "startSec": 0, "offsetSec": 0, "lengthSec": 4.5},
        {"id": "c2", "trackId": "t2", "kind": "midi", "startSec": 4, "lengthSec": 8.25, "notes": []},
        {"id": "c3", "trackId": "t2", "kind": "midi", "startSec": 1, "lengthSec": 2, "notes": []},
    ],
    "anything": {"the": ["workspace", "wants"], "unicode": "ಸ ರಿ ಗ"},
}


def test_projects_crud_and_duplicate(api_client, api_headers):
    assert api_client.get(f"{API}/projects", headers=api_headers).json() == []

    created = api_client.post(f"{API}/projects", headers=api_headers, json={"name": "Mohanam sketch", "project": PROJECT})
    assert created.status_code == 200, created.text
    project = created.json()
    assert set(project) == {"id", "name", "project", "version", "created_at", "updated_at"}
    assert project["name"] == "Mohanam sketch" and project["version"] == 1
    assert project["project"] == PROJECT  # stored and returned untouched
    assert api_client.get(f"{API}/projects/{project['id']}", headers=api_headers).json() == project

    summaries = api_client.get(f"{API}/projects", headers=api_headers).json()
    assert summaries == [{"id": project["id"], "name": "Mohanam sketch", "version": 1, "updated_at": project["updated_at"],
                          "track_count": 2, "clip_count": 3, "duration_s": 12.25, "tempo": 92.0}]

    changed = dict(PROJECT, tempo=120, clips=PROJECT["clips"][:1])
    saved = api_client.put(f"{API}/projects/{project['id']}", headers=api_headers,
                           json={"name": "Faster", "project": changed, "version": 2}).json()
    assert saved["id"] == project["id"] and saved["name"] == "Faster" and saved["version"] == 2
    assert saved["project"] == changed and saved["created_at"] == project["created_at"]
    assert saved["updated_at"] > project["updated_at"]

    copy = api_client.post(f"{API}/projects/{project['id']}/duplicate", headers=api_headers).json()
    assert copy["id"] != project["id"] and copy["name"] == "Faster (copy)"
    assert copy["project"] == changed and copy["version"] == 2

    listed = api_client.get(f"{API}/projects", headers=api_headers).json()
    assert [p["id"] for p in listed] == [copy["id"], project["id"]]  # most recently saved first
    assert listed[1]["tempo"] == 120.0 and listed[1]["clip_count"] == 1 and listed[1]["duration_s"] == 4.5

    assert api_client.delete(f"{API}/projects/{project['id']}", headers=api_headers).json() == {"ok": True}
    assert api_client.get(f"{API}/projects/{project['id']}", headers=api_headers).status_code == 404
    assert api_client.delete(f"{API}/projects/{project['id']}", headers=api_headers).status_code == 404
    assert [p["id"] for p in api_client.get(f"{API}/projects", headers=api_headers).json()] == [copy["id"]]  # the copy is independent


def test_project_summary_tolerates_any_shape(api_client, api_headers):
    for body in ({}, {"tracks": "nope", "clips": None, "tempo": "fast"}, {"tracks": [1, 2], "clips": [3, {"startSec": "x"}]}):
        response = api_client.post(f"{API}/projects", headers=api_headers, json={"name": "Odd", "project": body})
        assert response.status_code == 200 and response.json()["project"] == body
    summaries = api_client.get(f"{API}/projects", headers=api_headers).json()
    assert len(summaries) == 3 and all(s["tempo"] is None and s["duration_s"] == 0.0 for s in summaries)


def test_project_validation(api_client, api_headers):
    for body in ({"project": {}}, {"name": "", "project": {}}, {"name": "x" * 121, "project": {}}, {"name": "No project"},
                 {"name": "List", "project": []}):
        response = api_client.post(f"{API}/projects", headers=api_headers, json=body)
        assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    assert api_client.put(f"{API}/projects/missing", headers=api_headers, json={"name": "x", "project": {}}).status_code == 404
    assert api_client.post(f"{API}/projects/missing/duplicate", headers=api_headers).status_code == 404
