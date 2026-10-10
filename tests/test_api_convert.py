"""API: Notation Studio conversions (spec 9.1, 11.3).

The end-to-end tests need ``orin.pipeline`` and ``orin.formats.engine``; they skip
until those modules exist and run by themselves once they do. The service-level tests
further down feed the conversion job an IR directly, so they need only the engine.
"""
from __future__ import annotations

import pytest

API = "/api/v1"
SARGAM = "tonic: D3\nraga: mohanam\nS R2 G3 P D2 S' , ,\nD2 P G3~ R2 S , , ,"
NOTE_COUNT = 11
TEXT_KINDS = {"abc", "musicxml", "sargam", "names", "tab", "describe"}
# One track, one channel, 480 ticks per beat: C4 D4 E4, a quarter note each.
MIDI_THREE_NOTES = (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0" b"MTrk\x00\x00\x00\x1f"
                    b"\x00\x90\x3c\x40\x83\x60\x80\x3c\x40" b"\x00\x90\x3e\x40\x83\x60\x80\x3e\x40"
                    b"\x00\x90\x40\x40\x83\x60\x80\x40\x40" b"\x00\xff\x2f\x00")


def need(*modules):
    for name in modules:
        pytest.importorskip(name, reason=f"waiting on {name}", exc_type=ImportError)


def need_pipeline():
    need("orin.formats.engine", "orin.pipeline")


def convert(client, headers, wait, **body):
    """POST /convert, follow the job to the end, return (job, conversion)."""
    response = client.post(f"{API}/convert", headers=headers, json=body)
    assert response.status_code == 200, response.text
    ref = response.json()
    assert set(ref) == {"job_id", "conversion_id"} and ref["conversion_id"]
    job = wait(ref["job_id"], headers)
    return job, client.get(f"{API}/conversions/{ref['conversion_id']}", headers=headers).json()


def rerun(client, headers, wait, conversion_id, **change):
    response = client.post(f"{API}/conversions/{conversion_id}/rerun", headers=headers, json=change)
    assert response.status_code == 200, response.text
    assert response.json()["conversion_id"] == conversion_id
    job = wait(response.json()["job_id"], headers)
    return job, client.get(f"{API}/conversions/{conversion_id}", headers=headers).json()


def assert_result(conversion, output_format, note_count=None):
    assert conversion["error"] is None and conversion["parse_problems"] == []
    assert conversion["output_format"] == output_format
    # outputs: text inline, SVG pages inline, and a download link for everything but SVG
    outputs = {o["kind"]: o for o in conversion["outputs"]}
    assert outputs, conversion["missing"]
    for kind, out in outputs.items():
        assert out["label"] and out["media_type"] and out["ext"]
        if kind == "svg":
            assert out["pages"] and all(page.lstrip().startswith("<") for page in out["pages"])
        elif kind == "midi":
            assert out["text"] is None and out["asset_id"] and out["url"]
        elif kind in TEXT_KINDS:
            assert out["text"] and out["asset_id"] and out["url"]
    # loss report
    loss = conversion["loss"]
    assert loss["target_format"] == output_format
    assert 0.0 <= loss["summary"]["fidelity_score"] <= 1.0
    assert all(item["human_text"] and item["explain_key"] and item["severity"] in ("info", "minor", "major")
               for item in loss["items"])
    assert "Fidelity" in conversion["how_scored"]
    # IR summary
    ir = conversion["ir"]
    assert ir["tonic"]["hz"] > 0 and ir["note_count"] == len(ir["events"]) > 0
    if note_count is not None:
        assert ir["note_count"] == note_count
    assert len(ir["contour"]["t_s"]) == len(ir["contour"]["cents"]) <= 2000
    low = {e["id"] for e in ir["events"] if e["confidence"] < 0.5}
    assert set(ir["low_confidence_event_ids"]) == low
    assert conversion["score"]["format_id"] == output_format and len(conversion["score"]["notes"]) > 0
    return outputs


# --- request validation (no pipeline needed) ---------------------------------------

def test_convert_request_validation(api_client, api_headers):
    def post(**body):
        return api_client.post(f"{API}/convert", headers=api_headers, json=body)

    for body in ({"output_format": "carnatic"},
                 {"output_format": "carnatic", "notes_text": "   "},
                 {"output_format": "carnatic", "notes_text": "S R2", "asset_id": "abc"}):
        response = post(**body)
        assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
        assert "exactly one" in response.json()["error"]["message"]
    assert post(notes_text="S R2").status_code == 422  # output_format is required

    unknown = post(notes_text="S R2", output_format="klingon")
    assert unknown.status_code == 400 and unknown.json()["error"]["code"] == "unknown_format"
    assert unknown.json()["error"]["hint"]
    assert post(notes_text="S R2", input_format="klingon", output_format="carnatic").json()["error"]["code"] == "unknown_format"
    assert post(asset_id="no-such-file", output_format="carnatic").status_code == 404
    assert post(notes_text="S R2", output_format="carnatic", options={"voice": "robot"}).status_code == 422
    assert api_client.get(f"{API}/conversions", headers=api_headers).json() == []  # nothing was created


def test_format_aliases_are_accepted(api_client, api_headers, api_wait_job):
    ref = api_client.post(f"{API}/convert", headers=api_headers,
                          json={"notes_text": "S R2 G3", "input_format": "Carnatic", "output_format": "staff"}).json()
    api_wait_job(ref["job_id"], api_headers)
    conversion = api_client.get(f"{API}/conversions/{ref['conversion_id']}", headers=api_headers).json()
    assert conversion["input_format"] == "carnatic" and conversion["output_format"] == "western_classical"
    assert conversion["params"]["input_format"] == "carnatic" and conversion["notes_text"] == "S R2 G3"


# --- end to end ----------------------------------------------------------------------

def test_typed_sargam_to_western_staff(api_client, api_headers, api_wait_job):
    need_pipeline()
    job, conversion = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="carnatic",
                              output_format="western_classical")
    assert job["status"] == "done" and job["error"] is None and job["progress"] == 1.0
    assert job["result"] == {"kind": "conversion", "id": conversion["id"]}
    assert conversion["status"] == "done" and conversion["input_format"] == "carnatic"
    assert conversion["source_format"] == "carnatic" and conversion["same_format"] is False
    assert conversion["needs_style_choice"] is False and conversion["source_asset"] is None
    assert conversion["notes_text"] == SARGAM
    outputs = assert_result(conversion, "western_classical", NOTE_COUNT)
    assert {"abc", "midi"} <= set(outputs) | {m["kind"] for m in conversion["missing"]}
    for missing in conversion["missing"]:  # a missing renderer is reported, with an alternative, not a failure
        assert missing["kind"] and missing["message"]

    # Download links work without a header and carry the right type.
    for kind, out in outputs.items():
        if out["url"]:
            download = api_client.get(out["url"])
            assert download.status_code == 200 and download.headers["content-type"].split(";")[0] == out["media_type"]
            if out["text"] is not None:
                assert download.content.decode("utf-8") == out["text"]
    if "midi" in outputs:
        assert api_client.get(outputs["midi"]["url"]).content[:4] == b"MThd"

    # The exports are in the Library, marked as conversion output.
    library = api_client.get(f"{API}/assets?source=conversion", headers=api_headers).json()
    assert {o["asset_id"] for o in outputs.values() if o["asset_id"]} <= {a["id"] for a in library}
    assert all(a["kind"] in ("midi", "notation") for a in library)

    # The full IR is a JSON download; the list view has the summary numbers.
    ir = api_client.get(f"{API}/conversions/{conversion['id']}/ir", headers=api_headers)
    assert ir.status_code == 200 and ir.headers["content-type"].startswith("application/json")
    assert "attachment" in ir.headers["content-disposition"]
    assert len(ir.json()["events"]) == NOTE_COUNT and ir.json()["tonic"]["hz"] == pytest.approx(146.83, abs=0.5)
    summary = api_client.get(f"{API}/conversions", headers=api_headers).json()[0]
    assert summary["id"] == conversion["id"] and summary["status"] == "done" and summary["note_count"] == NOTE_COUNT
    assert summary["fidelity"] == pytest.approx(conversion["loss"]["summary"]["fidelity_score"])
    assert summary["filename"] == "Typed notes" and summary["source_format"] == "carnatic"


def test_bad_sargam_reports_line_and_column(api_client, api_headers, api_wait_job):
    need_pipeline()
    job, conversion = convert(api_client, api_headers, api_wait_job, notes_text="S R2 G3\nP X9 D2", input_format="carnatic",
                              notes_syntax="sargam", output_format="western_classical")
    assert job["status"] == "failed" and job["result"] is None
    assert job["error"]["code"] == "notes_parse_error" and "Line 2, column 3" in job["error"]["message"]
    assert conversion["status"] == "failed" and conversion["error"] == job["error"]
    assert conversion["outputs"] == [] and conversion["loss"] is None and conversion["ir"] is None
    problem = conversion["parse_problems"][0]
    assert (problem["line"], problem["column"]) == (2, 3) and problem["severity"] == "error"
    assert problem["code"] and "X9" in problem["message"]
    assert api_client.get(f"{API}/conversions/{conversion['id']}/ir", headers=api_headers).status_code == 404

    # Fixing nothing and re-running fails the same way; the conversion stays failed.
    job, conversion = rerun(api_client, api_headers, api_wait_job, conversion["id"], tempo_bpm=90)
    assert job["status"] == "failed" and conversion["status"] == "failed" and conversion["parse_problems"]


def test_same_format_is_the_labelled_cleanup_mode(api_client, api_headers, api_wait_job):
    need_pipeline()
    job, conversion = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="carnatic",
                              output_format="carnatic")
    assert job["status"] == "done" and conversion["same_format"] is True
    outputs = assert_result(conversion, "carnatic", NOTE_COUNT)  # a loss report is still produced
    assert "sargam" in outputs


def test_rerun_merges_options_and_reuses_the_analysis(api_client, api_headers, api_wait_job):
    need_pipeline()
    _, first = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="carnatic",
                       output_format="western_classical")
    conversion_id = first["id"]

    # A target-side change only re-converts: the stored IR is reused untouched.
    job, second = rerun(api_client, api_headers, api_wait_job, conversion_id, output_format="carnatic")
    assert job["status"] == "done" and job["result"] == {"kind": "conversion", "id": conversion_id}
    assert second["output_format"] == "carnatic" and second["same_format"] is True and second["ir"] == first["ir"]
    assert_result(second, "carnatic", NOTE_COUNT)
    assert second["params"]["output_format"] == "carnatic" and second["params"]["input_format"] == "carnatic"

    # An analysis-side change re-reads the notes with the new option; earlier choices are kept.
    job, third = rerun(api_client, api_headers, api_wait_job, conversion_id, tonic_hz=220.0)
    assert job["status"] == "done" and third["output_format"] == "carnatic"
    assert third["ir"]["tonic"]["hz"] == pytest.approx(220.0) and third["params"]["options"]["tonic_hz"] == 220.0

    # null clears an override; a field that is not sent is left alone.
    job, fourth = rerun(api_client, api_headers, api_wait_job, conversion_id, tonic_hz=None, force_measured=True)
    assert job["status"] == "done" and "tonic_hz" not in fourth["params"]["options"]
    assert fourth["params"]["options"]["force_measured"] is True
    assert fourth["ir"]["tonic"]["hz"] == pytest.approx(first["ir"]["tonic"]["hz"])

    bad = api_client.post(f"{API}/conversions/{conversion_id}/rerun", headers=api_headers, json={"output_format": "klingon"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "unknown_format"
    assert api_client.get(f"{API}/conversions/{conversion_id}", headers=api_headers).json() == fourth  # unchanged
    assert len(api_client.get(f"{API}/conversions", headers=api_headers).json()) == 1  # re-runs do not pile up


def test_notes_files_from_the_library(api_client, api_headers, api_wait_job):
    need_pipeline()
    upload = api_client.post(f"{API}/assets", headers=api_headers, files={"file": ("mohanam.txt", SARGAM.encode())}).json()
    assert upload["kind"] == "notation"
    job, conversion = convert(api_client, api_headers, api_wait_job, asset_id=upload["id"], input_format="carnatic",
                              notes_syntax="sargam", output_format="western_classical")
    assert job["status"] == "done", job["error"]
    assert conversion["source_asset"]["id"] == upload["id"] and conversion["notes_text"] is None
    outputs = assert_result(conversion, "western_classical", NOTE_COUNT)
    assert api_client.get(f"{API}/conversions", headers=api_headers).json()[0]["filename"] == "mohanam.txt"

    # A MIDI file from the Library is read as notes too (bytes, not text).
    midi = api_client.post(f"{API}/assets", headers=api_headers, files={"file": ("three.mid", MIDI_THREE_NOTES)}).json()
    assert midi["kind"] == "midi"
    job, from_midi = convert(api_client, api_headers, api_wait_job, asset_id=midi["id"],
                             input_format="western_classical", output_format="western_classical")
    assert job["status"] == "done", job["error"]
    assert_result(from_midi, "western_classical", 3)
    assert from_midi["ir"]["source"]["notes_syntax"] == "midi" and from_midi["same_format"] is True

    # Deleting the source file does not break reading the conversion.
    assert api_client.delete(f"{API}/assets/{upload['id']}", headers=api_headers).status_code == 200
    assert api_client.get(f"{API}/conversions/{conversion['id']}", headers=api_headers).json()["source_asset"] is None


def test_audio_with_auto_detect(api_app, api_client, api_headers, api_wait_job, api_sse):
    need_pipeline()
    import io

    import numpy as np
    import soundfile as sf

    sr = 22050
    tones = []
    for hz in (261.63, 329.63, 392.0, 329.63, 261.63):  # C E G E C, half a second each
        t = np.arange(int(0.5 * sr)) / sr
        tones.append(0.5 * np.sin(2 * np.pi * hz * t) * np.minimum(1, np.minimum(t, 0.5 - t) / 0.02))
    buffer = io.BytesIO()
    sf.write(buffer, np.concatenate(tones), sr, format="WAV")
    asset = api_client.post(f"{API}/assets", headers=api_headers, files={"file": ("arpeggio.wav", buffer.getvalue())}).json()

    response = api_client.post(f"{API}/convert", headers=api_headers,
                               json={"asset_id": asset["id"], "input_format": "auto", "output_format": "western_classical"})
    ref = response.json()
    events = api_sse(api_client.get(f"{API}/jobs/{ref['job_id']}/events", headers=api_headers).text)
    assert events[-1]["status"] == "done", events[-1]["error"]  # low detection confidence still finishes the job
    conversion = api_client.get(f"{API}/conversions/{ref['conversion_id']}", headers=api_headers).json()
    assert conversion["input_format"] == "auto" and conversion["source_asset"]["id"] == asset["id"]
    assert conversion["ir"]["source"]["filename"] == "arpeggio.wav"  # the name the user knows, not the storage key
    assert_result(conversion, "western_classical")

    # Never a bare label: up to three candidates with scores, and a sentence about them (spec 8.2).
    assert 1 <= len(conversion["detected"]) <= 3
    assert all(c["format_id"] and 0.0 <= c["score"] <= 1.0 for c in conversion["detected"])
    assert conversion["source_format"]
    if conversion["needs_style_choice"]:
        assert conversion["status"] == "awaiting_style" and conversion["detect_message"].startswith("Not sure")
    else:
        assert conversion["status"] == "done" and conversion["detect_message"].startswith("Detected: ")
    assert "%" in conversion["detect_message"]
    assert (api_app.state.ctx.settings.data_dir / "cache").is_dir()  # stage cache lives under the data dir

    # The user's choice always wins and settles the status.
    job, chosen = rerun(api_client, api_headers, api_wait_job, conversion["id"], input_format="western_classical")
    assert job["status"] == "done", job["error"]
    assert chosen["status"] == "done" and chosen["needs_style_choice"] is False and chosen["input_format"] == "western_classical"
    assert chosen["source_format"] == "western_classical"
    assert_result(chosen, "western_classical")


# --- service level: the job around a given IR (needs only the engine) ---------------

@pytest.fixture
def given_ir(monkeypatch):
    """Make the conversion job analyse to a prepared IR (or raise), and count the calls."""
    need("orin.formats.engine", "orin.ingest.notes")
    from orin.ingest.notes import parse_notes

    from server.services import conversions

    state = {"ir": parse_notes(SARGAM, format_id="carnatic", syntax="sargam"), "error": None, "calls": 0}

    def analyse(ctx, job, params, asset, notes_text, rerun):
        state["calls"] += 1
        job.progress("pitch", 0.5, "Tracking pitch")
        if state["error"] is not None:
            raise state["error"]
        return state["ir"]

    monkeypatch.setattr(conversions, "_analyse", analyse)
    return state


def with_detection(ir, candidates, confidence=None):
    from orin.ir import FormatCandidate

    source = ir.source.model_copy(update={
        "duration_s": 30.0,  # long enough that the length is not what makes detection unsure
        "format_hint": None, "detected_format": candidates[0][0], "detected_format_confidence": confidence,
        "detected_candidates": tuple(FormatCandidate(format_id=f, score=s) for f, s in candidates),
    })
    return ir.model_copy(update={"source": source})


def test_low_confidence_detection_awaits_a_style_choice(given_ir, api_client, api_headers, api_wait_job):
    given_ir["ir"] = with_detection(given_ir["ir"], [("carnatic", 0.41), ("arabic_maqam", 0.38), ("persian_dastgah", 0.12),
                                                     ("rock", 0.09)])
    job, conversion = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="auto",
                              output_format="western_classical")
    # The result is there, computed with the top candidate, and the job is done; only the
    # conversion says it wants the user to confirm (spec 11.3 step 4).
    assert job["status"] == "done" and conversion["status"] == "awaiting_style"
    assert conversion["needs_style_choice"] is True and conversion["source_format"] == "carnatic"
    assert [c["format_id"] for c in conversion["detected"]] == ["carnatic", "arabic_maqam", "persian_dastgah"]  # top three
    message = conversion["detect_message"]
    assert message.startswith("Not sure - please choose")
    assert "Carnatic" in message and "(41%)" in message and "Arabic Maqam (38%)" in message
    assert_result(conversion, "western_classical", NOTE_COUNT)
    assert conversion["score"]["options"]["explicit_style"] is False  # auto-detected: no tradition names applied
    assert api_client.get(f"{API}/conversions", headers=api_headers).json()[0]["status"] == "awaiting_style"

    job, chosen = rerun(api_client, api_headers, api_wait_job, conversion["id"], input_format="carnatic")
    assert job["status"] == "done" and chosen["status"] == "done" and given_ir["calls"] == 2


def test_confident_detection_lists_the_candidates(given_ir, api_client, api_headers, api_wait_job):
    given_ir["ir"] = with_detection(given_ir["ir"], [("carnatic", 0.72), ("arabic_maqam", 0.21), ("rock", 0.04)], confidence=0.72)
    job, conversion = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="auto",
                              output_format="western_classical")
    assert job["status"] == "done" and conversion["status"] == "done" and conversion["needs_style_choice"] is False
    assert conversion["detect_message"].startswith("Detected: ") and "(72%)" in conversion["detect_message"]
    assert conversion["detect_message"].count("%") == 3  # never a bare single label

    # An explicit style says nothing about detection, whatever the IR carries.
    job, explicit = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="carnatic",
                            output_format="western_classical")
    assert explicit["needs_style_choice"] is False and explicit["detect_message"] is None
    assert explicit["score"]["options"]["explicit_style"] is True


def test_missing_renderer_is_reported_not_fatal(given_ir, monkeypatch, api_client, api_headers, api_wait_job):
    import orin.render
    from orin.errors import RendererUnavailable

    real = orin.render.render

    def render(score, meta, kind, **opts):
        if kind == "svg":
            raise RendererUnavailable("svg", "The 'svg' renderer is not installed (verovio).", alternative="names")
        return real(score, meta, kind, **opts)

    monkeypatch.setattr(orin.render, "render", render)
    job, conversion = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="carnatic",
                              output_format="western_classical")
    assert job["status"] == "done" and conversion["status"] == "done"
    assert conversion["missing"] == [{"kind": "svg", "message": "The 'svg' renderer is not installed (verovio).",
                                      "alternative": "names"}]
    assert "svg" not in {o["kind"] for o in conversion["outputs"]} and conversion["outputs"]


def test_failed_rerun_keeps_the_last_good_result(given_ir, api_client, api_headers, api_wait_job):
    from orin.errors import IngestError

    _, good = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="carnatic",
                      output_format="western_classical")
    given_ir["error"] = IngestError("A tonic of 5.0 Hz is outside the range Orin accepts.", code="bad_option")
    job, after = rerun(api_client, api_headers, api_wait_job, good["id"], tonic_hz=5.0)
    assert job["status"] == "failed" and job["error"]["code"] == "bad_option"  # the job says what went wrong
    assert after == good  # status, params and outputs are as they were

    # A first run that fails is a failed conversion with the error on it.
    job, failed = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="carnatic",
                          output_format="western_classical")
    assert job["status"] == "failed" and failed["status"] == "failed" and failed["error"]["code"] == "bad_option"

    # An unexpected crash never leaks its text.
    given_ir["error"] = RuntimeError("numpy exploded at /private/path")
    job, crashed = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="carnatic",
                           output_format="western_classical")
    assert crashed["status"] == "failed" and crashed["error"]["code"] == "internal_error"
    assert "exploded" not in str(crashed) and "exploded" not in str(job)


def test_target_side_rerun_does_not_reanalyse(given_ir, api_client, api_headers, api_wait_job):
    _, first = convert(api_client, api_headers, api_wait_job, notes_text=SARGAM, input_format="carnatic",
                       output_format="western_classical")
    assert given_ir["calls"] == 1
    for change in ({"output_format": "arabic_maqam"}, {"force_measured": True}, {"target_template_id": None}):
        job, _ = rerun(api_client, api_headers, api_wait_job, first["id"], **change)
        assert job["status"] == "done", job["error"]
    assert given_ir["calls"] == 1  # the stored IR was reused every time
    job, _ = rerun(api_client, api_headers, api_wait_job, first["id"], sensitivity=1.5)
    assert job["status"] == "done" and given_ir["calls"] == 2  # an analysis option re-runs the pipeline
    job, _ = rerun(api_client, api_headers, api_wait_job, first["id"], sensitivity=1.5)
    assert given_ir["calls"] == 2  # the same value again changes nothing


def test_rerun_is_refused_while_running(given_ir, monkeypatch, api_client, api_headers, api_wait_job):
    import threading

    from server.services import conversions

    release = threading.Event()
    inner = conversions._analyse

    def slow(*args):
        assert release.wait(10)
        return inner(*args)

    monkeypatch.setattr(conversions, "_analyse", slow)
    ref = api_client.post(f"{API}/convert", headers=api_headers,
                          json={"notes_text": SARGAM, "input_format": "carnatic", "output_format": "carnatic"}).json()
    running = api_client.get(f"{API}/conversions/{ref['conversion_id']}", headers=api_headers).json()
    assert running["status"] == "running" and running["outputs"] == []
    busy = api_client.post(f"{API}/conversions/{ref['conversion_id']}/rerun", headers=api_headers, json={"tonic_hz": 200})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "busy" and busy.json()["error"]["retryable"] is True

    # Cancelling the job marks the conversion too.
    assert api_client.post(f"{API}/jobs/{ref['job_id']}/cancel", headers=api_headers).json()["status"] == "cancelled"
    release.set()
    for _ in range(500):
        cancelled = api_client.get(f"{API}/conversions/{ref['conversion_id']}", headers=api_headers).json()
        if cancelled["status"] != "running":
            break
        threading.Event().wait(0.01)
    assert cancelled["status"] == "failed" and cancelled["error"]["code"] == "cancelled"
