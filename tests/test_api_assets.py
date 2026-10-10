"""API: the Library. Uploads are size-capped, content-sniffed and stored under
server-chosen names; files are served through signed links (spec 13.3, 13.4)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

API = "/api/v1"

# A minimal standard MIDI file: one track, one quarter note (middle C) at 480 ticks per beat.
MIDI_BYTES = (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0"
              b"MTrk\x00\x00\x00\x0d\x00\x90\x3c\x40\x83\x60\x80\x3c\x40\x00\xff\x2f\x00")


def upload(client, headers, data, name="tone.wav", source=None):
    form = {"source": source} if source else None
    return client.post(f"{API}/assets", headers=headers, files={"file": (name, data, "application/octet-stream")}, data=form)


def error_of(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code and error["message"]
    return error


def blobs(data_dir: Path) -> list[Path]:
    return [p for p in (data_dir / "assets").rglob("*") if p.is_file()]


def test_upload_list_get_and_dedupe(api_client, api_headers, api_wav):
    data = api_wav(1.0)
    response = upload(api_client, api_headers, data)
    assert response.status_code == 200, response.text
    asset = response.json()
    assert asset["kind"] == "audio" and asset["source"] == "upload" and asset["media_type"] == "audio/wav"
    assert asset["filename"] == "tone.wav" and asset["size_bytes"] == len(data) and len(asset["sha256"]) == 64
    assert asset["duration_s"] == pytest.approx(1.0, abs=0.01) and asset["standin"] is False
    assert asset["url"].startswith(f"{API}/assets/{asset['id']}/file?exp=")

    assert api_client.get(f"{API}/assets/{asset['id']}", headers=api_headers).json()["id"] == asset["id"]
    listed = api_client.get(f"{API}/assets", headers=api_headers).json()
    assert [a["id"] for a in listed] == [asset["id"]]

    # The same bytes again are the same Library item; different bytes are a new one, listed first.
    assert upload(api_client, api_headers, data, name="copy.wav").json()["id"] == asset["id"]
    second = upload(api_client, api_headers, api_wav(0.5, 330.0), name="other.wav", source="bounce").json()
    assert second["source"] == "bounce"
    assert [a["id"] for a in api_client.get(f"{API}/assets", headers=api_headers).json()] == [second["id"], asset["id"]]
    assert [a["id"] for a in api_client.get(f"{API}/assets?source=bounce", headers=api_headers).json()] == [second["id"]]
    assert api_client.get(f"{API}/assets?kind=midi", headers=api_headers).json() == []
    error_of(api_client.get(f"{API}/assets?kind=spaceship", headers=api_headers), 422, "validation_error")
    error_of(upload(api_client, api_headers, data, source="generated"), 422, "validation_error")


def test_kind_comes_from_the_content_not_the_name(api_client, api_headers, api_wav):
    midi = upload(api_client, api_headers, MIDI_BYTES, name="song.wav").json()
    assert midi["kind"] == "midi" and midi["media_type"] == "audio/midi"
    notes = upload(api_client, api_headers, b"S R2 G3 P D2 S'\n", name="tune.mp3").json()
    assert notes["kind"] == "notation" and notes["media_type"].startswith("text/")
    audio = upload(api_client, api_headers, api_wav(0.2), name="notes.txt").json()
    assert audio["kind"] == "audio"


def test_signed_url_bearer_and_range(api_app, api_client, api_headers, api_login, api_wav):
    data = api_wav(1.0, 523.25)
    asset = upload(api_client, api_headers, data).json()
    file_path = f"{API}/assets/{asset['id']}/file"

    # The signed link works with no Authorization header, which is what <audio src> needs.
    response = api_client.get(asset["url"])
    assert response.status_code == 200 and response.content == data
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment")
    partial = api_client.get(asset["url"], headers={"Range": "bytes=0-99"})
    assert partial.status_code == 206 and partial.content == data[:100]

    # Tampered, mismatched and expired links are refused (403, so the client keeps its session).
    exp, sig = asset["url"].split("exp=")[1].split("&sig=")
    flipped = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    error_of(api_client.get(f"{file_path}?exp={exp}&sig={flipped}"), 403, "link_expired")
    error_of(api_client.get(f"{file_path}?exp={int(exp) + 60}&sig={sig}"), 403, "link_expired")
    error_of(api_client.get(f"{file_path}?exp={exp}"), 403, "link_expired")
    error_of(api_client.get(f"{file_path}?exp=soon&sig={sig}"), 403, "link_expired")
    signer = api_app.state.ctx.signer
    past = int(time.time()) - 5
    error_of(api_client.get(f"{file_path}?exp={past}&sig={signer.sign(asset['id'], past)}"), 403, "link_expired")
    other = upload(api_client, api_headers, api_wav(0.3, 200.0)).json()
    error_of(api_client.get(f"{API}/assets/{other['id']}/file?exp={exp}&sig={sig}"), 403, "link_expired")

    # A Bearer token works for the owner only (even beside a stale link); no credentials at all is a 401.
    assert api_client.get(file_path, headers=api_headers).content == data
    assert api_client.get(f"{file_path}?exp={past}&sig={flipped}", headers=api_headers).content == data
    error_of(api_client.get(f"{file_path}?exp={past}&sig={flipped}", headers=api_login()), 404, "not_found")
    error_of(api_client.get(file_path, headers=api_login()), 404, "not_found")
    error_of(api_client.get(file_path), 401, "unauthorized")


def test_peaks(api_client, api_headers, api_wav):
    asset = upload(api_client, api_headers, api_wav(1.0, amp=0.5)).json()
    response = api_client.get(f"{API}/assets/{asset['id']}/peaks?buckets=64", headers=api_headers)
    assert response.status_code == 200, response.text
    peaks = response.json()
    assert peaks["buckets"] == 64 and len(peaks["peaks"]) == 64
    assert peaks["duration_s"] == pytest.approx(1.0, abs=0.01)
    assert all(0.0 <= p <= 1.0 for p in peaks["peaks"]) and max(peaks["peaks"]) == pytest.approx(0.5, abs=0.02)
    assert api_client.get(f"{API}/assets/{asset['id']}/peaks?buckets=64", headers=api_headers).json() == peaks  # cached
    assert len(api_client.get(f"{API}/assets/{asset['id']}/peaks", headers=api_headers).json()["peaks"]) == 800
    # The cache lives in the asset's meta on the server and is not sent with every asset.
    assert "_peaks" not in api_client.get(f"{API}/assets/{asset['id']}", headers=api_headers).json()["meta"]
    notes = upload(api_client, api_headers, b"S R2 G3 P\n", name="tune.txt").json()
    error_of(api_client.get(f"{API}/assets/{notes['id']}/peaks", headers=api_headers), 400, "not_audio")


def test_delete_removes_the_row_and_the_file(api_app, api_client, api_headers, api_login, api_wav):
    storage = api_app.state.ctx.storage
    data = api_wav(0.4, 610.0)
    asset = upload(api_client, api_headers, data).json()
    shared = upload(api_client, (other := api_login()), data).json()  # another user, same bytes
    assert storage.exists(asset["sha256"]) and shared["id"] != asset["id"]

    assert api_client.delete(f"{API}/assets/{asset['id']}", headers=api_headers).json() == {"ok": True}
    error_of(api_client.get(f"{API}/assets/{asset['id']}", headers=api_headers), 404, "not_found")
    error_of(api_client.get(asset["url"]), 404, "not_found")
    error_of(api_client.delete(f"{API}/assets/{asset['id']}", headers=api_headers), 404, "not_found")
    assert storage.exists(asset["sha256"])  # still used by the other user's item
    assert api_client.get(shared["url"]).content == data

    assert api_client.delete(f"{API}/assets/{shared['id']}", headers=other).status_code == 200
    assert not storage.exists(asset["sha256"])  # last reference gone: the bytes are deleted


def test_oversized_upload_is_rejected(api_make_app, api_login, api_wav):
    app, client = api_make_app(max_upload_mb=0.02)  # about 20 KB
    headers = api_login(client=client)
    data_dir = app.state.ctx.settings.data_dir

    # Over the cap but inside the multipart allowance: stopped while the file is being stored.
    error = error_of(upload(client, headers, api_wav(1.0)), 413, "too_large")
    assert "0.02 MB" in error["message"] and error["hint"]
    # Far over the cap: refused from the stream, before any of it is stored.
    error_of(upload(client, headers, api_wav(10.0)), 413, "too_large")
    assert blobs(data_dir) == []
    assert client.get(f"{API}/assets", headers=headers).json() == []
    assert upload(client, headers, api_wav(0.2)).status_code == 200  # a small file still goes through


def test_upload_without_content_length_is_still_capped(api_make_app, api_login):
    """The cap counts bytes as they arrive; it does not trust the Content-Length header."""
    app, client = api_make_app(max_upload_mb=0.02)
    headers = api_login(client=client)
    boundary = "orinboundary"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.wav\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode()

    def body():
        yield head
        for _ in range(40):
            yield b"RIFF" + os.urandom(8188)  # 40 x 8 KB, sent chunked
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post(f"{API}/assets", content=body(),
                           headers={**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    error_of(response, 413, "too_large")
    assert blobs(app.state.ctx.settings.data_dir) == []


def test_too_long_audio_is_rejected(api_make_app, api_login, api_wav):
    app, client = api_make_app(max_audio_seconds=0.5)
    headers = api_login(client=client)
    error = error_of(upload(client, headers, api_wav(1.0)), 413, "too_long")
    assert "minutes" in error["message"] and error["hint"]
    assert blobs(app.state.ctx.settings.data_dir) == []


def test_junk_and_empty_files_are_rejected(api_app, api_client, api_headers):
    before = len(blobs(api_app.state.ctx.settings.data_dir))
    error = error_of(upload(api_client, api_headers, os.urandom(4096), name="song.wav"), 415, "unsupported_type")
    assert "WAV" in error["hint"]
    error_of(upload(api_client, api_headers, b"\x00\x01\x02\x03" * 100, name="tune.txt"), 415, "unsupported_type")
    error_of(upload(api_client, api_headers, b"", name="empty.wav"), 400, "empty_file")
    error_of(api_client.post(f"{API}/assets", headers=api_headers, data={"source": "upload"}), 422, "validation_error")
    assert len(blobs(api_app.state.ctx.settings.data_dir)) == before  # nothing was kept
    assert api_client.get(f"{API}/assets", headers=api_headers).json() == []


@pytest.mark.parametrize("name,shown", [
    ("../../evil.wav", "evil.wav"),
    ("..\\..\\windows\\evil.wav", "evil.wav"),
    ("/etc/passwd", "passwd"),
    ("....//....//x.wav", "x.wav"),
    ("..", "upload"),
])
def test_client_filename_never_reaches_the_filesystem(api_app, api_client, api_headers, api_wav, name, shown):
    data_dir = Path(api_app.state.ctx.settings.data_dir)
    roots = (data_dir, data_dir.parent, data_dir.parent.parent)

    def strays() -> set[Path]:
        # The parents are pytest's shared temp area, where other tests keep files of their
        # own (one is called x.wav), so only files that appear during the upload count.
        return {p for root in roots for p in root.rglob("*") if p.name in ("evil.wav", "passwd", "x.wav")}

    before = strays()
    asset = upload(api_client, api_headers, api_wav(0.1, 700.0 + len(name)), name=name).json()
    assert asset["filename"] == shown  # display text only
    stored = api_app.state.ctx.storage.path(asset["sha256"])
    assert stored.is_file() and stored.name == asset["sha256"]
    assert stored.resolve().is_relative_to((data_dir / "assets").resolve())
    assert strays() == before


def test_stem_ttl_sweep(api_app, api_client, api_headers, api_wav):
    """Temp stems older than the TTL are removed at startup (spec 13.3)."""
    from server.db import AssetRow
    from server.services import assets as asset_service

    ctx = api_app.state.ctx
    old = upload(api_client, api_headers, api_wav(0.1, 910.0)).json()
    fresh = upload(api_client, api_headers, api_wav(0.1, 920.0)).json()
    kept = upload(api_client, api_headers, api_wav(0.1, 930.0)).json()
    with ctx.db.session() as s:
        s.get(AssetRow, old["id"]).kind = "stem"
        s.get(AssetRow, old["id"]).created_at = "2020-01-01T00:00:00.000000+00:00"
        s.get(AssetRow, fresh["id"]).kind = "stem"
        s.get(AssetRow, kept["id"]).created_at = "2020-01-01T00:00:00.000000+00:00"  # old, but not a stem
    assert asset_service.sweep_stems(ctx) == 1
    remaining = {a["id"] for a in api_client.get(f"{API}/assets", headers=api_headers).json()}
    assert remaining == {fresh["id"], kept["id"]}
    assert not ctx.storage.exists(old["sha256"])
