"""FormatScore -> absolute note names as plain text (Western, Arabic and Persian formats).

The output follows the grammar the note-names parser reads (``orin/ingest/notes_names.py``):
``D4  F#4:2  Ed4  Ep4:3~``, a rest as ``r``, bar lines for reading only. Notes are the
WRITTEN pitches. When a format writes its modes at a conventional pitch (Rast on C), the
comment lines say where the music actually sounds.

For formats that name notes in solfege, a comment line above each line of notes gives the
fixed-do names with the tradition's words for the accidentals (half-flat, koron, sori).
"""
from __future__ import annotations

from typing import Any, Optional

from ..formats.base import FormatMeta, FormatScore
from . import RenderResult, result
from .spell import (TextBuilder, bar_starts, beat_pulses, is_measured, ornament_symbol, spell_note, spell_tonic,
                    tempo_bpm, timeline)

_BARS_PER_LINE = 2
_FREE_NOTES_PER_LINE = 8
_LEGENDS = {
    "arabic": "# Accidentals: d = half-flat, + = half-sharp, b = flat, # = sharp. A half-flat is a name, not an exact tuning.",
    "persian": "# Accidentals: p = koron, > = sori, b = flat, # = sharp. Koron and sori are not exact quarter tones.",
}


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def render(score: FormatScore, meta: FormatMeta, **opts: Any) -> RenderResult:
    options = score.options
    measured = is_measured(score)
    # "tradition" (default) follows the format; "solfege" and "letters" are the reader's choice.
    labels = opts.get("labels") or "tradition"
    solfege = meta.note_names == "solfege" if labels == "tradition" else labels == "solfege"
    word = meta.tonic_word
    tonic = spell_tonic(score, meta)
    mode = meta.mode(options.mode_id)
    units_per_beat = beat_pulses(score)
    bpm = tempo_bpm(score)

    out = TextBuilder()
    out.line(f"# {meta.display_name}: note names. C4 is middle C. A number after a colon is the length in units.")
    out.line(f"tonic: {tonic.name}")
    if mode is not None:
        out.line(f"mode: {mode.id}")
    if measured:
        num, den = score.cycle.time_signature  # type: ignore[union-attr]
        out.line(f"meter: {num}/{den}")
    if bpm and 10.0 <= bpm <= 480.0:
        out.line(f"tempo: {_number(bpm)}")
    if units_per_beat <= 16:
        out.line(f"units_per_beat: {units_per_beat}")

    written = f"{tonic.name} ({tonic.solfege})" if solfege else tonic.name
    offset = options.transposition_cents  # sounding minus written
    if abs(offset) < 1.0:
        out.line(f"# The {word} is {written} and sounds at {options.tonic_hz:.2f} Hz.")
    elif meta.written_tonic_policy == "conventional" or abs(offset) > 50.0:
        out.line(f"# The {word} sounds at {options.tonic_hz:.2f} Hz. It is written here as {written}, a "
                 f"conventional pitch for this music, so the notes sound {abs(offset):.0f} cents "
                 f"{'higher' if offset > 0 else 'lower'} than written.")
    else:
        out.line(f"# The {word} is written as {written}, the nearest note name. It sounds at "
                 f"{options.tonic_hz:.2f} Hz, {abs(offset):.0f} cents {'above' if offset > 0 else 'below'} that note.")
    if mode is not None:
        out.line(f"# Mode as chosen: {mode.label}. Orin matches modes on pitches only.")
    if measured and score.cycle.label:  # type: ignore[union-attr]
        out.line(f"# Cycle: {score.cycle.label}.")  # type: ignore[union-attr]
    if not measured:
        out.line("# Free rhythm: lengths are shown, no bar lines.")
    if meta.accidental_style in _LEGENDS:
        out.line(_LEGENDS[meta.accidental_style])
    if any(ornament_symbol(n, allowed="~/\\") for n in score.notes):
        out.line("# After a note: ~ an oscillation, / a slide up, \\ a slide down. These are Orin's marks.")
    if meta.review_status == "unreviewed" and meta.pitch_system != "12tet":
        out.line("# The mode and tuning tables behind this are an unreviewed draft, not checked by a practitioner.")
    out.line()

    # Lines of (token, note id, spoken name). A note is never split: this syntax has no tie,
    # so a note that crosses a bar line is written whole and the bar line follows it.
    tl = timeline(score)
    spellings = {n.id: spell_note(score, meta, n) for n in score.notes}
    items: list[tuple[str, Optional[str], str]] = []
    cursor = 0
    end = (tl[-1][0] + tl[-1][1]) if tl else 0
    bars = bar_starts(score, end)[1:] if measured and tl else []

    def close_bars(upto: int) -> None:
        crossed = False
        while bars and bars[0] <= upto:
            bars.pop(0)
            crossed = True
        if crossed:
            items.append(("|", None, "|"))

    def rest(start: int, stop: int) -> None:
        while start < stop:  # rests are split at bar lines
            piece = min(stop, bars[0]) if bars and bars[0] > start else stop
            units = piece - start
            items.append(("r" if units == 1 else f"r:{units}", None, "rest"))
            close_bars(piece)
            start = piece

    for start, dur, note in tl:
        rest(cursor, start)
        sp = spellings[note.id]
        text = sp.name + ("" if dur == 1 else f":{dur}") + ornament_symbol(note, allowed="~/\\")
        items.append((text, note.id, sp.solfege))
        cursor = start + dur
        close_bars(cursor)
    if measured and tl:
        rest(cursor, bars[0] if bars else cursor)
        if items and items[-1][0] == "|":
            items[-1] = ("||", None, "||")

    # Wrap: a few bars per line when measured, a few notes per line in free time.
    lines: list[list[tuple[str, Optional[str], str]]] = [[]]
    count = 0
    for item in items:
        lines[-1].append(item)
        if measured:
            count += item[0] == "|"
            full = item[0] == "|" and count % _BARS_PER_LINE == 0
        else:
            count += item[1] is not None
            full = item[1] is not None and count % _FREE_NOTES_PER_LINE == 0
        if full:
            lines.append([])
    for line in lines:
        if not line:
            continue
        if solfege:
            out.line("# " + "  ".join(spoken for _, _, spoken in line))
        for i, (text, note_id, _) in enumerate(line):
            if i:
                out.add(" ")
            out.add(text, note_id)
        out.line()
    return result("names", text=out.text, tokens=out.tokens())

