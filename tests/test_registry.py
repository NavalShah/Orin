"""The format registry (spec section 4): discovery, the tables' shape, lookalikes."""
from __future__ import annotations

import textwrap

import pytest

from orin.errors import UnknownFormat
from orin.eval.fixtures import make_ir
from orin.formats import registry
from orin.formats.base import FAMILY_LABELS, PARSER_KINDS, RENDER_KINDS, Adapter, FormatMeta

SIX = ["western_classical", "carnatic", "western_pop", "rock", "arabic_maqam", "persian_dastgah"]
METAS = registry.list_meta()
MODES = [(meta, mode) for meta in METAS for mode in meta.mode_table]
TEMPLATES = [(meta, t) for meta in METAS for t in meta.rhythm_templates]


def ids(pairs):
    return [f"{meta.id}:{entry.id}" for meta, entry in pairs]


# --- discovery ---------------------------------------------------------------

def test_six_formats_are_discovered_in_order():
    assert list(registry.all_formats()) == SIX
    assert [m.id for m in METAS] == SIX
    assert all(isinstance(a, Adapter) and isinstance(a.META, FormatMeta) for a in registry.all_formats().values())
    assert [m.order for m in METAS] == sorted(m.order for m in METAS)


def test_families_are_grouped_in_display_order():
    assert registry.families() == [
        ("western_art", "Western art music", ["western_classical"]),
        ("indian_classical", "Indian classical", ["carnatic"]),
        ("western_popular", "Western popular", ["western_pop", "rock"]),
        ("middle_eastern", "Middle Eastern and Persian", ["arabic_maqam", "persian_dastgah"]),
    ]
    assert all(meta.family in FAMILY_LABELS for meta in METAS)


def test_lookup_by_id_and_alias():
    assert registry.get("carnatic").id == "carnatic"
    assert registry.get("  Carnatic ").id == "carnatic"
    assert registry.get("sangeetham").id == "carnatic"
    assert registry.get("Staff").id == "western_classical"
    assert registry.get("guitar tab").id == "rock"
    assert registry.get("maqam").id == "arabic_maqam"
    assert registry.get("dastgah").id == "persian_dastgah"
    assert registry.has("pop") and not registry.has("gamelan") and not registry.has("")


def test_unknown_id_gives_a_hint():
    with pytest.raises(UnknownFormat) as exc:
        registry.get("carnatik")
    assert exc.value.code == "unknown_format"
    assert "'carnatik' is not a format Orin knows." == exc.value.message
    assert exc.value.hint == "Did you mean: carnatic?"

    with pytest.raises(UnknownFormat) as exc:
        registry.get("zzzz")
    assert exc.value.hint.startswith("Known formats: western_classical, carnatic")
    with pytest.raises(UnknownFormat):
        registry.get(None)  # type: ignore[arg-type]


# --- every entry is well formed ----------------------------------------------

@pytest.mark.parametrize("meta", METAS, ids=[m.id for m in METAS])
def test_registry_entry_fields(meta):
    assert meta.display_name and meta.description and meta.aliases
    assert meta.review_status == "unreviewed"  # only a practitioner may change this
    assert meta.reviewer_note and meta.caveats
    assert meta.monophonic_only is True
    assert meta.renderers and set(meta.renderers) <= set(RENDER_KINDS)
    assert meta.primary_renderer in meta.renderers
    assert meta.input_parsers and set(meta.input_parsers) <= set(PARSER_KINDS)
    assert {"midi", "describe"} <= set(meta.renderers)
    assert meta.mode_table and meta.rhythm_templates and meta.ornament_table
    assert len({m.id for m in meta.mode_table}) == len(meta.mode_table)
    assert len({t.id for t in meta.rhythm_templates}) == len(meta.rhythm_templates)
    if meta.default_template_id is not None:
        assert meta.template(meta.default_template_id) is not None
    assert meta.template(None) is None and meta.template("nope") is None
    assert meta.mode(None) is None and meta.mode("nope") is None


@pytest.mark.parametrize("meta", METAS, ids=[m.id for m in METAS])
def test_pitch_grid(meta):
    cents = [g.cents for g in meta.grid]
    assert cents == sorted(cents) and len(set(cents)) == len(cents)
    assert cents[0] == 0.0 and all(0 <= c < 1200 for c in cents)
    assert len(cents) == {"12tet": 12, "just_intonation_srutis": 12}.get(meta.pitch_system, 24)
    for g in meta.grid:
        assert g.name and 1 <= g.degree <= 7 and g.tol >= 0
        assert all(name and 1 <= degree <= 7 for name, degree in g.alt)
    # Windows of neighbouring positions never overlap by more than they are apart.
    for a, b in zip(meta.grid, meta.grid[1:]):
        assert a.tol <= b.cents - a.cents and b.tol <= b.cents - a.cents
    assert meta.nearest_grid(0.0).cents == 0.0 and meta.nearest_grid(1199.0).cents == 0.0
    assert meta.nearest_grid(701.0).degree == 5


@pytest.mark.parametrize("meta, mode", MODES, ids=ids(MODES))
def test_every_mode_has_a_source_and_valid_degrees(meta, mode):
    assert mode.source.strip() and len(mode.source) > 20
    assert mode.confidence in ("high", "medium", "low")
    assert mode.label and mode.id == mode.id.lower() and " " not in mode.id
    assert meta.mode(mode.id) is mode and meta.mode(mode.label) is mode
    assert all(meta.mode(alias) is mode for alias in mode.aliases)

    assert 5 <= len(mode.ascending) <= 7
    assert mode.ascending[0].cents == 0 and mode.ascending[0].degree == 1
    for group in (mode.ascending, mode.descending or (), mode.variable or ()):
        for d in group:
            assert d.name and 1 <= d.degree <= 7
            assert 0 <= d.cents < 1200
            assert 0 <= d.tol_lo <= 60 and 0 <= d.tol_hi <= 60
    rising = [d.cents for d in mode.ascending]
    assert rising == sorted(rising) and len(set(rising)) == len(rising)
    steps = [d.degree for d in mode.ascending]
    assert steps == sorted(steps)

    union = mode.degrees()
    assert [d.cents for d in union] == sorted(d.cents for d in union)
    assert set(mode.pitch_set()) == {d.cents for d in union}
    assert {d.cents for d in mode.ascending} <= set(mode.pitch_set())
    # Every degree can be written: it is within a quarter tone step of a grid position.
    for d in union:
        assert abs(((d.cents - meta.nearest_grid(d.cents).cents + 600) % 1200) - 600) <= 25
    # A degree's own nominal pitch is inside its own window.
    assert all(d.outside(d.cents) == 0 for d in union)
    if meta.written_tonic_policy == "conventional":
        assert mode.conventional_tonic is not None
    if meta.pitch_system == "12tet":
        assert mode.key_signature_parent in ("major", "minor")


@pytest.mark.parametrize("meta, template", TEMPLATES, ids=ids(TEMPLATES))
def test_every_rhythm_template_has_consistent_shapes(meta, template):
    assert template.label and template.source.strip()
    assert template.cycle_beats >= 1 and template.subdivision >= 1
    assert template.length_pulses == template.cycle_beats * template.subdivision
    # structure: one entry per beat or one per pulse
    assert len(template.structure) in (template.cycle_beats, template.length_pulses)
    assert all(isinstance(s, str) and s for s in template.structure)
    assert len(template.accent_pattern) == len(template.structure)
    assert all(0.0 <= a <= 1.0 for a in template.accent_pattern)
    assert max(template.accent_pattern) == 1.0 and template.accent_pattern[0] == 1.0  # the cycle starts strong
    if template.groups:
        assert sum(template.groups) == template.cycle_beats and all(g >= 1 for g in template.groups)
    numerator, denominator = template.time_signature
    assert numerator >= 1 and denominator in (2, 4, 8, 16)
    assert template.pulse_quarters == pytest.approx((numerator * 4 / denominator) / template.length_pulses)
    assert template.pulse_quarters in (0.25, 0.5, 1 / 3, 1 / 6, 1.0)
    assert template.swing_ratio is None or template.swing_ratio >= 1.0


@pytest.mark.parametrize("meta", METAS, ids=[m.id for m in METAS])
def test_ornament_table_covers_every_neutral_kind(meta):
    for kind in ("oscillation", "vibrato", "glide", "grace"):
        for amplitude in (0.0, 20.0, 80.0, 120.0, 121.0, 250.0, 251.0, 900.0):
            rule = meta.ornament_rule(kind, amplitude)
            assert rule is not None, (kind, amplitude)
            assert rule.neutral_kind == kind and rule.local_name
            if rule.action == "approximate":
                assert rule.approximation
    assert meta.ornament_rule("trill", 50.0) is None
    # No format turns an oscillation into a trill: a trill names two notes.
    assert all("trill" not in r.local_name.lower() and "trill" not in (r.staff_mark or "") for r in meta.ornament_table)


def test_tradition_names_are_hedged():
    carnatic = registry.get("carnatic").META
    assert carnatic.ornament_rule("oscillation", 80).local_name == "kampita-like oscillation"
    assert carnatic.ornament_rule("glide", 200).local_name == "jaru-like slide"
    rock = registry.get("rock").META
    assert rock.ornament_rule("glide", 200).local_name == "bend"
    assert rock.ornament_rule("glide", 300).local_name == "slide"


# --- adding a format is one file ----------------------------------------------

NEW_ADAPTER = textwrap.dedent('''
    """A made-up format, to prove a new tradition is one file."""
    from orin.formats import _tet12 as tet
    from orin.formats.base import Adapter, FormatMeta, OrnamentRule, export

    ADAPTER = Adapter(FormatMeta(
        id="test_blues",
        display_name="Test Blues",
        description="Twelve pitches with bends, in 12/8.",
        aliases=("tb",),
        family="blues_derived",
        order=70,
        pitch_system="blues_inflected",
        tonic_model="fixed_key_signature",
        naming="absolute",
        grid=tet.GRID,
        mode_table=tet.modes("blues", "minor_pentatonic"),
        rhythm_model="swing_grid",
        rhythm_templates=tet.meters("12_8"),
        default_template_id="12_8",
        ornament_table=(
            OrnamentRule(neutral_kind="glide", local_name="bend", action="keep", symbol="b"),
            OrnamentRule(neutral_kind="oscillation", local_name="shake", action="approximate", symbol="~",
                         approximation="a shake mark"),
        ),
        renderers=("names", "midi", "describe"),
        primary_renderer="names",
        input_parsers=("note_names",),
    ))

    META, parse_notes, ir_to_format, render, score_fit = export(ADAPTER)
''')


def test_adding_a_format_is_one_file(tmp_path):
    path = tmp_path / "test_blues.py"
    path.write_text(NEW_ADAPTER)
    try:
        found = registry.discover(extra_paths=[path])
        assert list(found) == SIX + ["test_blues"]
        assert registry.has("test_blues") and registry.get("tb").id == "test_blues"
        assert registry.families()[-1] == ("blues_derived", "Blues-derived", ["test_blues"])
        assert "test_blues:minor_pentatonic" in registry.lookalikes("rock", "minor_pentatonic")

        # It converts through the same engine with no other code.
        ir = make_ir([0, 300, 550, 700], source_format="rock", pulse_s=0.125, subdivision=4, cycle_pulses=16)
        result = registry.get("test_blues").ir_to_format(ir)
        assert result.loss.target_format == "test_blues"
        assert [n.name for n in result.score.notes] == ["1", "b3", "4", "5"]
        assert [i.event_id for i in result.loss.items if i.code == "MICROTONE_ROUNDED"] == ["e3"]
        assert result.score.cycle.id == "12_8"
    finally:
        restored = registry.discover()
    assert list(restored) == SIX and not registry.has("test_blues")
    assert all("test_blues" not in ref for ref in registry.lookalikes("rock", "minor_pentatonic"))


def test_discover_ignores_files_without_an_adapter(tmp_path):
    path = tmp_path / "not_a_format.py"
    path.write_text("VALUE = 1\n")
    try:
        assert list(registry.discover(extra_paths=[path])) == SIX
    finally:
        registry.discover()


# --- lookalikes ----------------------------------------------------------------

def exact(format_id: str, mode_id: str) -> set[str]:
    return {ref for ref in registry.lookalikes(format_id, mode_id) if not ref.endswith("~")}


def rotations(format_id: str, mode_id: str) -> set[str]:
    return {ref[:-1] for ref in registry.lookalikes(format_id, mode_id) if ref.endswith("~")}


def test_major_scale_family_shares_its_pitches():
    family = {"arabic_maqam:ajam", "western_classical:major", "western_pop:major", "rock:major",
              "carnatic:shankarabharanam", "persian_dastgah:mahur"}
    for ref in family:
        format_id, mode_id = ref.split(":")
        assert exact(format_id, mode_id) == family - {ref}
    # Its church-mode rotations are flagged as rotations, never as the same thing.
    assert {"western_classical:dorian", "western_classical:natural_minor", "carnatic:kharaharapriya",
            "carnatic:kalyani", "arabic_maqam:kurd"} <= rotations("arabic_maqam", "ajam")


def test_shur_and_bayati_share_their_pitches():
    assert exact("persian_dastgah", "shur") == {"arabic_maqam:bayati"}
    assert exact("arabic_maqam", "bayati") == {"persian_dastgah:shur"}


def test_mohanam_and_its_rotations():
    assert exact("carnatic", "mohanam") == {"western_classical:major_pentatonic", "western_pop:major_pentatonic",
                                            "rock:major_pentatonic"}
    assert {"carnatic:hindolam", "carnatic:shuddha_saveri", "carnatic:madhyamavati",
            "rock:minor_pentatonic", "western_pop:minor_pentatonic"} <= rotations("carnatic", "mohanam")
    assert "carnatic:mohanam" in rotations("carnatic", "hindolam")
    assert "carnatic:mohanam" not in registry.lookalikes("carnatic", "mohanam")  # never its own lookalike


def test_neutral_modes_do_not_match_twelve_tone_ones():
    for mode_id in ("rast", "huzam", "saba"):
        refs = registry.lookalikes("arabic_maqam", mode_id)
        assert not any(ref.split(":")[0] in ("western_classical", "western_pop", "rock", "carnatic") for ref in refs)
    assert exact("arabic_maqam", "rast") == set()
    assert registry.lookalikes("arabic_maqam", "no_such_maqam") == ()


def test_find_mode():
    meta, mode = registry.find_mode("carnatic:mohanam")
    assert (meta.id, mode.id) == ("carnatic", "mohanam")
    assert registry.find_mode("carnatic:hindolam~")[1].id == "hindolam"
    assert registry.find_mode("major")[0].id == "western_classical"
    assert registry.find_mode("major", default_format="rock")[0].id == "rock"
    assert registry.find_mode("Shankarabharanam")[1].id == "shankarabharanam"
    assert registry.find_mode("carnatic:nope") is None and registry.find_mode("nope:major") is None
    assert registry.find_mode("nope") is None
