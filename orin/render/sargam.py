"""FormatScore -> sargam text: the primary Carnatic view.

The output follows the one grammar the typed-sargam parser reads (``orin/ingest/notes_sargam.py``),
so what Orin prints can be pasted back in. One swara, comma or rest is one pulse.

With a raga set, notes inside it are bare letters, as Carnatic notation normally implies
the variant from the raga; notes outside it carry their number and a ``*``. With no raga
every R G M D N carries its number. The marks ``~ / \\`` are Orin's own and the legend at
the end of every output says so.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from ..formats.base import FormatMeta, FormatScore, ModeEntry, ScoreNote
from . import RenderResult, result
from .spell import TextBuilder, beat_pulses, is_measured, ornament_symbol, tempo_bpm, timeline

DOT_ABOVE, DOT_BELOW = "̇", "̣"
_SWARA_RE = re.compile(r"^([SRGMPDN])([123]?)$")
# Fallback names for a pitch that arrives without a swara name: the twelve positions.
_BY_SEMITONE = ("S", "R1", "R2", "G2", "G3", "M1", "M2", "P", "D1", "D2", "N2", "N3")
_FREE_LINE_UNITS = 16


def _swara(note: ScoreNote, meta: FormatMeta) -> tuple[str, str]:
    """(letter, variant) for a note; from its name, else from the nearest grid position."""
    for name in (note.name.strip().upper(), meta.nearest_grid(note.cents % 1200.0).name.upper(),
                 _BY_SEMITONE[int(round(note.cents / 100.0)) % 12]):
        m = _SWARA_RE.match(name)
        if m and not (m.group(1) in "SP" and m.group(2)):
            return m.group(1), m.group(2)
    return "S", ""


def _raga_names(raga: Optional[ModeEntry]) -> dict[str, set[str]]:
    """Swara letter -> the variants the raga uses (ascending, descending and variable)."""
    out: dict[str, set[str]] = {}
    if raga is not None:
        for degree in raga.degrees():
            m = _SWARA_RE.match(degree.name.upper())
            if m:
                out.setdefault(m.group(1), set()).add(degree.name.upper())
    return out


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def render(score: FormatScore, meta: FormatMeta, pretty: bool = True, **opts: Any) -> RenderResult:
    options = score.options
    measured = is_measured(score)
    cycle = score.cycle
    raga = meta.mode(options.mode_id)
    in_raga = _raga_names(raga)

    def token(note: ScoreNote) -> str:
        letter, variant = _swara(note, meta)
        name = letter + variant
        star = ""
        if raga is not None:
            if not note.in_mode or name not in in_raga.get(letter, set()):
                star = "*"
            elif len(in_raga[letter]) == 1:
                variant = ""  # the raga settles it
        octave = int(note.octave)
        orn = ornament_symbol(note, allowed="~/\\")
        if pretty:
            dots = (DOT_ABOVE if octave > 0 else DOT_BELOW) * abs(octave)
            return f"{letter}{dots}{variant}{orn}{star}"
        marks = ("'" if octave > 0 else ".") * abs(octave)
        return f"{letter}{variant}{marks}{orn}{star}"

    out = TextBuilder()
    out.line(f"tonic: {options.tonic_hz:.2f} Hz")
    if raga is not None:
        out.line(f"raga: {raga.id}")
    units_per_beat = beat_pulses(score)
    known_tala = measured and meta.template(cycle.id) == cycle and units_per_beat <= 16  # type: ignore[union-attr]
    if known_tala:
        out.line(f"tala: {cycle.id}")  # type: ignore[union-attr]
    bpm = tempo_bpm(score)
    if bpm and 10.0 <= bpm <= 480.0:
        out.line(f"tempo: {_number(bpm)}")
    if units_per_beat <= 16:
        out.line(f"units_per_beat: {units_per_beat}")
    if raga is not None:
        out.line(f"# Raga as chosen: {raga.label}. Orin matches ragas on pitches only, not on phrases or gamakas.")
    if measured and not known_tala:
        out.line(f"# Cycle: {cycle.label}, {cycle.length_pulses} units.")  # type: ignore[union-attr]
    if not measured:
        out.line("# Free rhythm: lengths are shown, no tala is implied.")
    out.line()

    # One symbol per pulse: a swara where a note starts, "," while it lasts, "_" for silence.
    cells: list[tuple[str, Optional[str]]] = []  # (text, note id)
    for start, dur, note in timeline(score):
        cells += [("_", None)] * (start - len(cells))
        cells.append((token(note), note.id))
        cells += [(",", None)] * (dur - 1)
    if measured:
        length = cycle.length_pulses  # type: ignore[union-attr]
        cells += [("_", None)] * (-len(cells) % length)  # rests that complete the last cycle
        groups = [g * units_per_beat for g in (cycle.groups or (cycle.cycle_beats,))]  # type: ignore[union-attr]
        if sum(groups) != length:
            groups = [length]
        bounds, total = set(), 0
        for g in groups[:-1]:
            total += g
            bounds.add(total)
        for i, (text, note_id) in enumerate(cells):
            at = i % length
            if at:
                out.add(" | " if at in bounds else "  " if at % units_per_beat == 0 else " ")
            out.add(text, note_id)
            if at == length - 1:
                out.line(" ||")
    else:
        for i, (text, note_id) in enumerate(cells):
            at = i % _FREE_LINE_UNITS
            if at:
                out.add("  " if at % units_per_beat == 0 else " ")
            out.add(text, note_id)
            if at == _FREE_LINE_UNITS - 1 or i == len(cells) - 1:
                out.line()

    out.line()
    out.line("# One swara, comma or _ is one unit. A comma holds the note before it; _ is a rest.")
    if pretty:
        out.line("# A dot above a letter is the upper octave, a dot below the lower octave.")
    else:
        out.line("# ' after a swara is the upper octave, . the lower octave.")
    if raga is not None:
        out.line("# * marks a note outside the chosen raga.")
    out.line("# ~ (oscillation), / (slide up) and \\ (slide down) are Orin's own marks, not standard Carnatic notation.")
    out.line("# Swara positions, ragas and talas come from an unreviewed draft table, not checked by a Carnatic musician.")
    return result("sargam", text=out.text, tokens=out.tokens())
