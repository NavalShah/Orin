"""FormatScore -> six-string guitar tab as ASCII (rock).

Standard tuning, one column per pulse, bar lines per cycle. Tab shows where a note
starts, not how long it lasts. The fingering is one playable option (frets 0 to 15,
staying close to the previous fret), not a claim about how the player fingered it.
"""
from __future__ import annotations

from typing import Any, Optional

from ..formats.base import FormatMeta, FormatScore
from . import RenderResult, result
from .spell import TextBuilder, bar_starts, is_measured, ornament_symbol, spell_note, timeline

# (label, MIDI number of the open string), top line first: e' B G D A E.
STRINGS = (("e", 64), ("B", 59), ("G", 55), ("D", 50), ("A", 45), ("E", 40))
MAX_FRET = 15
LOWEST, HIGHEST = 40, 64 + MAX_FRET
_LINE_WIDTH = 78
_FREE_PULSES_PER_LINE = 16
_SYMBOLS = {"b": "bend", "r": "release", "/": "slide up", "\\": "slide down", "~": "vibrato",
            "h": "hammer-on", "p": "pull-off"}


def place(midi: int, previous_fret: Optional[int]) -> tuple[int, int]:
    """(string index, fret) for a pitch in range: the fret closest to the previous one,
    or the lowest position for the first note."""
    options = [(i, midi - open_midi) for i, (_, open_midi) in enumerate(STRINGS) if 0 <= midi - open_midi <= MAX_FRET]
    if previous_fret is None:
        return min(options, key=lambda o: (o[1], -o[0]))
    return min(options, key=lambda o: (abs(o[1] - previous_fret), o[1]))


def fold(midi: int) -> int:
    """Move a pitch by octaves into the guitar's range."""
    while midi < LOWEST:
        midi += 12
    while midi > HIGHEST:
        midi -= 12
    return midi


def render(score: FormatScore, meta: FormatMeta, **opts: Any) -> RenderResult:
    warnings: list[str] = []
    measured = is_measured(score)
    tl = timeline(score)

    # pulse -> (string, fret text, note id)
    cells: dict[int, tuple[int, str, str]] = {}
    previous: Optional[int] = None
    moved = 0
    used: list[str] = []
    for start, _, note in tl:
        midi = int(round(spell_note(score, meta, note).midi))
        folded = fold(midi)
        moved += folded != midi
        string, fret = place(folded, previous)
        previous = fret
        symbol = ornament_symbol(note)
        if symbol and symbol not in used:
            used.append(symbol)
        cells[start] = (string, f"{fret}{symbol}", note.id)
    if moved:
        warnings.append(f"{moved} note{'s' if moved != 1 else ''} outside the guitar's range "
                        f"(E2 to fret {MAX_FRET} on the top string) {'were' if moved != 1 else 'was'} moved by octaves.")

    width = max([len(text) for _, text, _ in cells.values()] + [1]) + 1  # every column is this wide
    end = (tl[-1][0] + tl[-1][1]) if tl else 0
    if measured:
        bars = bar_starts(score, end)
        segments = list(zip(bars, bars[1:]))
        longest = max(b1 - b0 for b0, b1 in segments)
        per_line = max(1, (_LINE_WIDTH - 2) // (longest * width + 1))
    else:
        segments = [(p, min(p + _FREE_PULSES_PER_LINE, end)) for p in range(0, max(end, 1), _FREE_PULSES_PER_LINE)]
        per_line = 1

    out = TextBuilder()
    out.line("Guitar tab, standard tuning (E A D G B E). The top line is the highest string.")
    out.line(f"Each column is one pulse ({'bar lines mark the cycle' if measured else 'free rhythm, no bar lines'}). "
             "The fingering is one playable option.")
    if used:
        out.line("Marks: " + ", ".join(f"{s} = {_SYMBOLS.get(s, 'ornament')}" for s in used) + ".")
    for first in range(0, len(segments), per_line):
        out.line()
        system = segments[first:first + per_line]
        for index, (label, _) in enumerate(STRINGS):
            out.add(f"{label}|")
            for b0, b1 in system:
                for pulse in range(b0, b1):
                    cell = cells.get(pulse)
                    if cell is not None and cell[0] == index:
                        out.add(cell[1], cell[2])
                        out.add("-" * (width - len(cell[1])))
                    else:
                        out.add("-" * width)
                if measured:
                    out.add("|")
            out.line()
    order = {note.id: i for i, (_, _, note) in enumerate(tl)}
    tokens = tuple(sorted(out.tokens(), key=lambda t: order[t.note_id]))  # in note order, not string order
    return result("tab", text=out.text, tokens=tokens, warnings=tuple(warnings))
