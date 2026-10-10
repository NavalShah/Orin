"""The conversion matrix (spec 4.6): every source x target pair goes through the one engine.

36 pairs, one fixture phrase per source format. The 16 Tier-1 pairs carry a "tier1" id:

    pytest tests/test_matrix.py -k tier1
"""
from __future__ import annotations

import math

import pytest

from orin.eval import fixtures as fx
from orin.formats import registry
from orin.formats.base import ConversionResult
from orin.loss import LOSS_CODES, NOTICE_CODES

FORMATS = list(fx.PHRASES)
TIER1 = ("western_classical", "carnatic", "western_pop", "rock")
TET12 = ("western_classical", "western_pop", "rock")
NEUTRAL_SOURCES = ("arabic_maqam", "persian_dastgah")  # phrases with half-flat or koron degrees
FREE_TIME_SOURCES = ("persian_dastgah",)


def _pairs():
    for source in FORMATS:
        for target in FORMATS:
            tier = "tier1" if source in TIER1 and target in TIER1 else "tier2"
            yield pytest.param(source, target, id=f"{tier}-{source}-to-{target}")


PAIRS = list(_pairs())


def codes(result: ConversionResult) -> set[str]:
    return {i.code for i in result.loss.items}


def rounded(result: ConversionResult):
    return [i for i in result.loss.items if i.code == "MICROTONE_ROUNDED" and i.severity != "info"]


def test_matrix_covers_every_registered_format():
    assert set(FORMATS) == set(registry.all_formats())
    assert set(TIER1) <= set(FORMATS)
    assert len(PAIRS) == 36
    assert sum(1 for p in PAIRS if p.id.startswith("tier1-")) == 16


@pytest.mark.parametrize("source, target", PAIRS)
def test_pair_converts(source, target):
    ir = fx.phrase_for(source)
    before = ir.model_dump()
    result = registry.get(target).ir_to_format(ir)
    assert ir.model_dump() == before  # the IR is never changed

    # --- a valid score -------------------------------------------------------
    score = result.score
    assert not any("could not be completed" in w for w in score.warnings), score.warnings
    assert score.format_id == target and score.source_format == source
    notes = score.notes
    assert len(notes) == len(ir.events)
    assert [n.id for n in notes] == [f"n{i + 1}" for i in range(len(notes))]
    assert len({n.id for n in notes}) == len(notes)
    assert [n.source_event_id for n in notes] == [e.id for e in ir.events]
    starts = [n.start_pulse for n in notes]
    assert starts == sorted(starts) and starts[0] >= 0
    assert all(b > a for a, b in zip(starts, starts[1:]))
    for a, b in zip(notes, notes[1:]):
        assert a.start_pulse + a.duration_pulses <= b.start_pulse  # one line: no overlap
    grid = {round(g.cents, 6) for g in registry.get(target).META.grid}
    mode = registry.get(target).META.mode(score.options.mode_id)
    writable = grid | ({round(d.cents, 6) for d in mode.degrees()} if mode else set())
    for n, e in zip(notes, ir.events):
        assert n.duration_pulses >= 1 and n.duration_s > 0
        assert 1 <= n.degree <= 7 and n.name and 1 <= n.velocity <= 127
        assert all(math.isfinite(x) for x in (n.cents, n.source_cents, n.cents_delta, n.onset_s, n.duration_s))
        assert n.source_cents == e.pitch_cents and n.source_onset_s == e.onset_s
        assert n.cents_delta == pytest.approx(n.cents - n.source_cents)
        assert abs(n.cents_delta) <= 60
        assert n.octave == math.floor(n.cents / 1200)
        assert round(n.cents % 1200, 6) in writable  # every written pitch is one the target can write
        assert len(n.ornaments) == sum(1 for o in (e.ornament, *e.extra_ornaments) if o.kind)

    options = score.options
    assert options.tonic_hz == ir.tonic.hz and options.pulse_s > 0 and options.subdivision >= 1
    assert options.written_tonic.step in "CDEFGAB" and 0 <= options.written_tonic_octave <= 8
    if source in FREE_TIME_SOURCES:
        assert score.free_time and score.cycle is None and options.tempo_bpm is None
    else:
        assert not score.free_time and score.cycle is not None
        assert options.template_id == score.cycle.id and options.subdivision == score.cycle.subdivision
        assert options.tempo_bpm == pytest.approx(60.0 / (options.pulse_s * options.subdivision))
        assert score.phase_pulses == notes[0].start_pulse

    # --- a report whose numbers add up ----------------------------------------
    loss = result.loss
    assert loss.source_format == source and loss.target_format == target
    summary = loss.summary
    for value in (summary.fidelity_score, summary.pitch_score, summary.ornament_score, summary.timing_score):
        assert 0.0 <= value <= 1.0
    assert summary.fidelity_score == pytest.approx(1.0 - sum(i.penalty for i in loss.items), abs=1e-6)
    assert summary.fidelity_score == pytest.approx(
        0.45 * summary.pitch_score + 0.35 * summary.ornament_score + 0.20 * summary.timing_score)
    assert summary.max_cents_delta == pytest.approx(max(abs(n.cents_delta) for n in notes))
    assert sum(summary.counts_by_code.values()) == len(loss.items)
    for item in loss.items:
        assert item.code in LOSS_CODES and item.penalty >= 0
        assert item.human_text and "{" not in item.human_text
        assert item.explain_key.startswith(item.code.lower())
    for notice in loss.notices:
        assert notice.code in NOTICE_CODES and "{" not in notice.human_text
    assert loss.to_text().startswith(f"Loss report: {source} -> {target}\n")
    assert ConversionResult.model_validate_json(result.model_dump_json()) == result

    # --- loss where loss is expected, and none where it is not -----------------
    if source in NEUTRAL_SOURCES and target in TET12:
        # A half-flat or koron degree has no key on a piano.
        assert rounded(result), "a neutral degree must be reported as rounded on a 12-tone staff"
        assert summary.pitch_score < 1.0 and summary.max_cents_delta >= 30
    if source == "carnatic" and target in TET12:
        assert codes(result) & {"ORNAMENT_DROPPED", "ORNAMENT_APPROXIMATED"}, "the gamakas must be reported"
        assert summary.ornament_score < 1.0
        assert "TEMPERAMENT_RETUNED" in codes(result)
    if source in TET12 and target == "carnatic":
        assert summary.counts_by_code.get("TEMPERAMENT_RETUNED") == 1
        assert summary.pitch_score == 1.0
    if source == target:
        # Clean-up mode: the IR's own mode is used and nothing is rounded.
        assert rounded(result) == []
        assert options.mode_id == ir.mode.mode_id
        assert codes(result) <= {"ORNAMENT_DROPPED", "ORNAMENT_APPROXIMATED", "MICROTONE_ROUNDED"}
        assert summary.pitch_score == 1.0 and summary.timing_score == 1.0
        assert result.suggestions == ()
        assert all(n.in_mode for n in notes) and summary.max_cents_delta == 0
    else:
        # A mode is never applied across traditions; lookalikes are only offered.
        assert options.mode_id is None and options.mode_label is None
        assert all(c.label_system == target for c in result.suggestions)
        assert ("MODE_APPROXIMATED" in codes(result)) + ("MODE_UNREPRESENTABLE" in codes(result)) <= 1
        if "MODE_APPROXIMATED" in codes(result):
            assert result.suggestions
        if "MODE_UNREPRESENTABLE" in codes(result):
            assert result.suggestions == ()
    if source in TET12 and target in TET12:
        assert summary.pitch_score == 1.0 and summary.timing_score == 1.0 and summary.max_cents_delta == 0
        assert "CYCLE_REINTERPRETED" not in codes(result)  # 4/4 is 4/4
    if source in FREE_TIME_SOURCES:
        assert not codes(result) & {"TIMING_SHIFT", "CYCLE_REINTERPRETED", "FREE_TIME_MEASURED"}


@pytest.mark.parametrize("source, target", PAIRS)
def test_pair_converts_when_forced_into_bars_with_no_style_names(source, target):
    from orin.formats.base import ConvertOptions

    ir = fx.phrase_for(source)
    result = registry.get(target).ir_to_format(ir, ConvertOptions(force_measured=True, explicit_style=False))
    assert not any("could not be completed" in w for w in result.score.warnings)
    assert len(result.score.notes) == len(ir.events)
    assert result.score.cycle is not None and not result.score.free_time
    assert all(o.local_name is None for n in result.score.notes for o in n.ornaments)
    forced = [i for i in result.loss.items if i.code == "FREE_TIME_MEASURED"]
    assert len(forced) == (1 if source in FREE_TIME_SOURCES else 0)
    assert 0.0 <= result.loss.summary.fidelity_score <= 1.0
    assert result.loss.summary.fidelity_score == pytest.approx(1.0 - sum(i.penalty for i in result.loss.items), abs=1e-6)
