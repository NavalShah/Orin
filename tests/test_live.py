"""Real Gemini and ElevenLabs calls. Opt-in: ``pytest tests/test_live.py -m live``.

Each test skips unless its keys are in the environment (``.env`` is not read here), so
the default run never touches the network. The music test spends ElevenLabs credits on a
paid plan; on a free plan it checks that the plan error degrades to the labelled stand-in.
"""
from __future__ import annotations

import os

import pytest

from orin.ai import service
from orin.ai.schema import MAX_DURATION_S, MIN_DURATION_S, MusicSpec, SpecSection
from orin.ai.types import Turn
from orin.formats import registry
from orin.loss import LossItem, LossReport, LossSummary, explain_key, human_text

pytestmark = pytest.mark.live

_FORCED_OFFLINE = os.environ.get("ORIN_OFFLINE", "").strip() == "1"
needs_gemini = pytest.mark.skipif(
    _FORCED_OFFLINE or not (os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_MODEL")),
    reason="needs GEMINI_API_KEY and GEMINI_MODEL in the environment, and ORIN_OFFLINE unset")
needs_elevenlabs = pytest.mark.skipif(
    _FORCED_OFFLINE or not os.environ.get("ELEVENLABS_API_KEY"),
    reason="needs ELEVENLABS_API_KEY in the environment, and ORIN_OFFLINE unset")


def _report() -> LossReport:
    item = LossItem(code="MICROTONE_ROUNDED", severity="minor", event_id="e1", time_s=1.25,
                    detail={"cents_delta": -38.0, "written": "E"},
                    human_text=human_text("MICROTONE_ROUNDED", cents_delta=-38.0, target="Western (staff notation)", written="E"),
                    explain_key=explain_key("MICROTONE_ROUNDED", "12tet"), penalty=0.02)
    return LossReport(items=(item,), source_format="carnatic", target_format="western_classical",
                      summary=LossSummary(counts_by_code={"MICROTONE_ROUNDED": 1}, max_cents_delta=38.0,
                                          fidelity_score=0.9, pitch_score=0.8))


@needs_gemini
def test_live_status_and_intent():
    st = service.status()
    assert st.llm_offline is False and st.llm_model == os.environ["GEMINI_MODEL"]
    result = service.classify_intent("Make a calm Carnatic piece on veena, 20 seconds long", history=[])
    assert result.source == "gemini", "the live call fell back to the stand-in; see the orin.ai log"
    assert result.intent == "generate"
    assert service.classify_intent("What is a gamaka?", history=[]).intent == "chat"


@needs_gemini
def test_live_spec_refinement_and_edit():
    first = service.refine_spec("Make a short Carnatic alapana that turns into jazz piano", history=[])
    assert first.source == "gemini", "the live call fell back to the stand-in; see the orin.ai log"
    assert first.ok and first.spec is not None
    assert MIN_DURATION_S <= first.spec.duration_s <= MAX_DURATION_S
    assert len(first.spec.sections) >= 2 and first.spec.sections[0].genre_id == "carnatic"
    assert sum(s.duration_s for s in first.spec.sections) == first.spec.duration_s

    history = [Turn(role="user", text="Make a short Carnatic alapana that turns into jazz piano"),
               Turn(role="assistant", text="Here is what I will make.")]
    edited = service.refine_spec("same but with a flute, and make it 40 seconds", history=history,
                                 previous_specs=[first.spec])
    assert edited.ok and edited.spec.duration_s == 40
    assert any("flute" in i.lower() for i in edited.spec.instruments)


@needs_gemini
def test_live_chat_stream_and_explanations():
    chunks = list(service.chat_reply_stream("In two sentences, what is a tala?", history=[]))
    answer = "".join(chunks)
    assert answer.strip() and "offline stand-in" not in answer

    result = service.explain_loss(_report(), item_indexes=(0,), source_meta=registry.get("carnatic").META,
                                  target_meta=registry.get("western_classical").META, level="beginner")
    # A reply that states numbers missing from the report is replaced by the stand-in, so both are valid here.
    assert result.source in ("gemini", "offline")
    assert result.text.strip() and 1 <= len(result.try_this) <= 2

    spec = MusicSpec(genre_id="carnatic", duration_s=20, instruments=["veena"],
                     sections=[SpecSection(genre_id="carnatic", description="slow melody over a drone", duration_s=20)])
    assert service.explain_music(spec, level="beginner").strip()


@needs_elevenlabs
def test_live_narration():
    result = service.narrate("This note was rounded to the nearest piano key.", request_id="live-narration-1")
    if result.standin:
        assert result.audio is None and result.message  # a plan or voice problem, reported in plain words
    else:
        assert result.audio and result.media_type == "audio/mpeg" and result.provider.name == "elevenlabs"


@needs_elevenlabs
def test_live_music_or_a_labelled_standin():
    spec = MusicSpec(genre_id="jazz", duration_s=10, instruments=["piano"], vocals="none",
                     sections=[SpecSection(genre_id="jazz", description="slow solo piano", duration_s=10)])
    result = service.compose_music(spec, request_id="live-music-1")
    assert result.audio
    if result.standin:
        assert result.provider.name == "offline" and result.standin_reason and result.media_type == "audio/wav"
    else:
        assert result.provider.name == "elevenlabs" and result.media_type == "audio/mpeg"
