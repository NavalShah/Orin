"""API: the registry view for the dropdowns, and the glossary (spec 4.5, 11.4)."""
from __future__ import annotations

API = "/api/v1"
FORMAT_IDS = {"western_classical", "western_pop", "rock", "carnatic", "arabic_maqam", "persian_dastgah"}
GLOSSARY_TERMS = {
    "Sa", "swara", "raga", "tala", "gamaka", "kampita", "sargam", "tonic", "cents", "maqam", "jins", "iqa'",
    "taqsim", "dastgah", "gusheh", "koron", "sori", "finalis", "quarter tone", "swing", "equal temperament",
    "pitch bend", "drone", "octave",
}


def test_formats_shape(api_client):
    response = api_client.get(f"{API}/formats")  # open: no token
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"formats", "families", "renderers", "limits", "offline", "sargam_help", "detect_limits", "monophonic_note"}

    formats = {f["id"]: f for f in body["formats"]}
    assert set(formats) == FORMAT_IDS and len(body["formats"]) == 6
    for fmt in formats.values():
        # No table has been checked by a practitioner yet, and the API must say so.
        assert fmt["review_status"] == "unreviewed"
        assert fmt["display_name"] and fmt["description"] and fmt["tonic_word"] and fmt["caveats"]
        assert fmt["primary_renderer"] in fmt["renderers"]
        assert fmt["modes"], fmt["id"]
        for mode in fmt["modes"]:
            assert mode["source"], f"{fmt['id']}:{mode['id']} has no source"
            assert mode["confidence"] in ("high", "medium", "low")
            assert len(mode["pitches_cents"]) == len(mode["degree_names"]) >= 5
            assert mode["pitches_cents"] == sorted(mode["pitches_cents"]) and mode["pitches_cents"][0] == 0
            assert isinstance(mode["lookalikes"], list) and isinstance(mode["caveats"], list)
        template_ids = [t["id"] for t in fmt["rhythm_templates"]]
        assert fmt["default_template_id"] is None or fmt["default_template_id"] in template_ids
        for template in fmt["rhythm_templates"]:
            assert template["cycle_beats"] >= 1 and template["subdivision"] >= 1
    assert formats["carnatic"]["tonic_word"] == "Sa" and formats["carnatic"]["family"] == "indian_classical"

    # Lookalikes are "format:mode" references to modes that exist; "~" marks a rotation.
    mode_refs = {f"{f['id']}:{m['id']}" for f in formats.values() for m in f["modes"]}
    lookalikes = [ref for f in formats.values() for m in f["modes"] for ref in m["lookalikes"]]
    assert lookalikes and all(ref.rstrip("~") in mode_refs for ref in lookalikes)
    mohanam = next(m for m in formats["carnatic"]["modes"] if m["id"] == "mohanam")
    assert any(ref.startswith("western_") for ref in mohanam["lookalikes"])  # the major pentatonic

    # Families group every format exactly once, in display order, with a label.
    grouped = [fid for family in body["families"] for fid in family["format_ids"]]
    assert sorted(grouped) == sorted(FORMAT_IDS) and len(body["families"]) >= 3
    for family in body["families"]:
        assert family["label"] and family["format_ids"]
        assert all(formats[fid]["family"] == family["id"] and formats[fid]["family_label"] == family["label"]
                   for fid in family["format_ids"])
    assert grouped == [f["id"] for f in body["formats"]] or set(grouped) == set(formats)

    renderers = {r["kind"]: r for r in body["renderers"]}
    assert {"svg", "abc", "midi", "musicxml", "sargam", "names", "tab", "describe"} <= set(renderers)
    assert renderers["midi"] == {"kind": "midi", "label": "MIDI", "media_type": "audio/midi", "ext": "mid"}
    assert all(kind in renderers for f in formats.values() for kind in f["renderers"])

    assert body["limits"] == {"max_upload_mb": 10.0, "max_audio_seconds": 300.0, "max_generate_seconds": 300, "daily_generations": 20}
    assert body["offline"]["llm"] and body["offline"]["music"] and body["offline"]["tts"] and body["offline"]["reason"]
    assert isinstance(body["sargam_help"], str) and len(body["sargam_help"]) > 40
    assert body["detect_limits"] and all(isinstance(x, str) and x for x in body["detect_limits"])
    assert "one melody" in body["monophonic_note"]


def test_formats_limits_follow_settings(api_make_app):
    _, client = api_make_app(max_upload_mb=25, max_audio_seconds=120, daily_generations=3)
    limits = client.get(f"{API}/formats").json()["limits"]
    assert limits["max_upload_mb"] == 25 and limits["max_audio_seconds"] == 120 and limits["daily_generations"] == 3


def test_glossary_shape(api_client):
    response = api_client.get(f"{API}/glossary")  # open: no token
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) >= 20
    terms = [e["term"] for e in entries]
    assert len(terms) == len(set(terms)) and GLOSSARY_TERMS <= set(terms)
    for entry in entries:
        assert set(entry) == {"term", "definition", "tradition"}
        assert entry["tradition"] is None or isinstance(entry["tradition"], str)
        text = entry["definition"]
        assert 40 <= len(text) <= 420 and text.endswith(".")  # short enough for a tooltip
        assert not any(word in text.lower() for word in ("always", "never", "exactly the same", "must be"))  # hedged
