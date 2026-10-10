"""FormatScore -> a few plain sentences a screen reader can read (spec 13.5).

Positions are counted in scale steps from the format's reference note (Sa, tonic,
finalis), so the description needs no staff and no note names. A mode is mentioned only
when one was chosen, and then as the chosen reading, not as an identification.
"""
from __future__ import annotations

from typing import Any

from ..formats.base import FormatMeta, FormatScore, ScoreNote
from . import RenderResult, result
from .spell import is_measured, tempo_bpm, timeline

_NOMINAL = (0, 200, 400, 500, 700, 900, 1100)


def _steps(note: ScoreNote) -> int:
    """Scale steps above the reference note (negative below it); 7 is the octave."""
    degree = max(1, min(7, note.degree))
    return (degree - 1) + 7 * round((note.cents - _NOMINAL[degree - 1]) / 1200.0)


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _place(steps: int, word: str) -> str:
    if steps == 0:
        return f"on the {word}"
    side = "above" if steps > 0 else "below"
    octaves, rest = divmod(abs(steps), 7)
    parts = []
    if octaves:
        parts.append("one octave" if octaves == 1 else f"{octaves} octaves")
    if rest:
        parts.append(_count(rest, "step"))
    return f"{' and '.join(parts)} {side} the {word}"


def _when(index: int, total: int) -> str:
    if total <= 1 or index == 0:
        return "at the start"
    if index == total - 1:
        return "at the end"
    share = index / (total - 1)
    return "early on" if share < 1 / 3 else "around the middle" if share < 2 / 3 else "near the end"


def render(score: FormatScore, meta: FormatMeta, **opts: Any) -> RenderResult:
    word = meta.tonic_word
    options = score.options
    notes = [note for _, _, note in timeline(score)]
    if not notes:
        return result("describe", text=f"This score has no notes. It is written as {meta.display_name}.\n")

    sentences: list[str] = []
    seconds = max(n.onset_s + n.duration_s for n in notes) - min(n.onset_s for n in notes)
    sentences.append(f"This melody has {_count(len(notes), 'note')} and lasts about "
                     f"{_count(max(1, round(seconds)), 'second')}, written as {meta.display_name}.")
    sentences.append(f"The {word} sounds at about {options.tonic_hz:.0f} hertz.")

    steps = [_steps(n) for n in notes]
    low, high = min(steps), max(steps)
    if low == high:
        sentences.append(f"Every note is {_place(low, word)}.")
    else:
        sentences.append(f"Its lowest note is {_place(low, word)} and its highest is {_place(high, word)}, "
                         f"a range of {_count(high - low, 'step')}.")
        peak = steps.index(high)
        sentences.append(f"It starts {_place(steps[0], word)}, reaches its highest point {_when(peak, len(steps))}, "
                         f"and ends {_place(steps[-1], word)}.")

    if is_measured(score):
        cycle = score.cycle
        bpm = tempo_bpm(score)
        pace = f", at about {round(bpm)} beats per minute" if bpm else ""
        sentences.append(f"The rhythm is in cycles of {_count(cycle.cycle_beats, 'beat')} "  # type: ignore[union-attr]
                         f"({cycle.label}){pace}.")  # type: ignore[union-attr]
    else:
        sentences.append("It is in free rhythm, with no bar lines.")

    kept: dict[str, int] = {}
    dropped = 0
    for note in notes:
        names = set()
        for orn in note.ornaments:
            if orn.action == "drop":
                dropped += 1
            else:
                names.add(orn.local_name or orn.kind)
        for name in names:
            kept[name] = kept.get(name, 0) + 1
    if kept:
        parts = [f"{_count(n, 'note')} with {'an' if name[:1].lower() in 'aeiou' else 'a'} {name}"
                 for name, n in sorted(kept.items(), key=lambda kv: -kv[1])]
        sentences.append("Ornaments marked: " + ", ".join(parts) + ".")
    elif not dropped:
        sentences.append("No ornaments are marked.")
    if dropped:
        sentences.append(f"{_count(dropped, 'ornament')} in the source could not be written in this notation "
                         f"and {'is' if dropped == 1 else 'are'} left out.")

    mode = meta.mode(options.mode_id)
    label = mode.label if mode else options.mode_label
    if label:
        outside = sum(1 for n in notes if not n.in_mode)
        fit = "All of its notes fit that mode" if not outside else \
            f"{outside} of its {_count(len(notes), 'note')} {'falls' if outside == 1 else 'fall'} outside that mode"
        sentences.append(f"The notes are read against {label}, the mode that was chosen. {fit}. "
                         "Orin compares pitches only, so this is a reading, not an identification.")
    return result("describe", text=" ".join(sentences) + "\n")

