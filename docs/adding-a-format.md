# Adding a format

A format is a musical system: a pitch grid, modes, rhythm cycles, an ornament vocabulary and a notation style. Adding one means adding **one file** to `orin/formats/`. The registry finds it, the conversion engine handles it, and the dropdowns show it, with no other code change. Spec sections 4 and 5.

## 1. Create the file

`orin/formats/<id>.py`. Any module there that does not start with `_` and defines `ADAPTER` is registered.

```python
from ._build import cycle, deg, mode, written
from .base import Adapter, FormatMeta, GridPosition, OrnamentRule, export

ADAPTER = Adapter(FormatMeta(
    id="hindustani",
    display_name="Hindustani",
    description="One line for the UI and for prompts.",
    family="indian_classical",
    pitch_system="just_intonation_srutis",
    tonic_model="movable_sa",
    naming="relative",
    tonic_word="Sa",
    grid=...,            # every pitch this notation can write, one octave
    mode_table=...,      # raags
    rhythm_model="tala_cycle",
    rhythm_templates=...,  # taals
    default_template_id=None,
    ornament_table=...,
    renderers=("sargam", "midi", "describe"),
    primary_renderer="sargam",
    input_parsers=("sargam", "midi"),
    review_status="unreviewed",
    reviewer_note="Who wrote this and from what.",
    caveats=("Shown to users next to the format.",),
    analysis_profile="carnatic",
))

META, parse_notes, ir_to_format, render, score_fit = export(ADAPTER)
```

Read `carnatic.py` or `arabic_maqam.py` for a complete example.

## 2. Fill the tables

**Pitch grid** (`GridPosition`): `name`, `cents` above the tonic, `degree` (1 to 7, which staff letter to use), and `tol`, how far from nominal still counts as that position. Use 0 for exact systems such as 12-TET and something wider where the values are nominal.

**Modes** (`ModeEntry`): each mode holds its own degrees inline, because the same grid position can be two notes (204 cents is R2 or G1) and the same name can be tuned differently per mode.

| Field | Use |
|---|---|
| `ascending` | the main pitches |
| `descending`, `variable` | pitches that appear only on the way down, or as alternates. The engine accepts the union |
| `conventional_tonic` | where the mode is conventionally written (Rast on C) |
| `source`, `confidence`, `caveats` | required. Say where each row comes from and how sure you are |

**Rhythm** (`RhythmTemplate`): `cycle_beats`, `subdivision` (pulses per beat), `structure` and `accent_pattern` (one entry per beat or per pulse), `groups` (where the cycle is divided), and `time_signature` for the staff approximation. Leave `default_template_id=None` when the music is unmetered by default.

**Ornaments** (`OrnamentRule`): for each neutral kind (`oscillation`, `vibrato`, `glide`, `grace`), optionally by width, say whether the notation keeps it, approximates it with a mark, or drops it, and what it is called. Names should be hedged ("kampita-like").

**Analysis** (`analysis_profile`, `detect_priors`): pick a segmentation profile from `orin/config/styles.toml` or add one, and give style auto-detect a range for each feature that fits this music.

## 3. Rules

- **Every row has a source.** If you cannot say where a value comes from, do not add it.
- **Ship unreviewed.** Only a practitioner of the tradition may change `review_status`, and `reviewer_note` must then say who.
- **Cents are nominal.** They decide which written note a performed pitch belongs to. They are not shown as measured tuning.
- **A mode is a set of pitches here, and usually much more in practice.** Say so in `caveats`.
- **Do not add behaviour to the adapter** unless the tables cannot express it. Subclass `Adapter` only then.

## 4. Check it

```bash
python -m orin formats                      # it is listed
python -m orin review > docs/review-checklist.md   # its tables appear for a practitioner to check
pytest tests/test_registry.py tests/test_matrix.py
```

Add one fixture phrase for the new format to the conversion-matrix test so every pair with it is covered.

## Renderers and parsers

`renderers` lists the output kinds the format offers: `svg` (staff), `sargam`, `names`, `abc`, `tab`, `musicxml`, `midi`, `describe`. They are shared modules in `orin/render/`, driven by the tables above. `input_parsers` lists the typed inputs it accepts. A new notation style needs a new renderer module with `render(score, meta, **opts) -> RenderResult` and an entry in `RENDER_INFO`.
