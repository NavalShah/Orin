"""Small helpers that keep the adapter data files readable. Not an adapter."""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .base import Degree, GridPosition, ModeEntry, RhythmTemplate, WrittenPitch


def deg(name: str, degree: int, cents: float, tol: float = 30.0, *, tol_lo: Optional[float] = None,
        tol_hi: Optional[float] = None, role: Optional[str] = None) -> Degree:
    return Degree(name=name, degree=degree, cents=cents,
                  tol_lo=tol if tol_lo is None else tol_lo,
                  tol_hi=tol if tol_hi is None else tol_hi, role=role)


def written(step: str, alter_cents: float = 0.0) -> WrittenPitch:
    return WrittenPitch(step=step, alter_cents=alter_cents)  # type: ignore[arg-type]


def mode(id: str, label: str, ascending: Sequence[Degree], *, kind: str, source: str, confidence: str,
         descending: Optional[Sequence[Degree]] = None, variable: Optional[Sequence[Degree]] = None,
         tonic: Optional[WrittenPitch] = None, parent: Optional[str] = None,
         caveats: Iterable[str] = (), aliases: Iterable[str] = ()) -> ModeEntry:
    return ModeEntry(
        id=id, label=label, kind=kind, ascending=tuple(ascending),
        descending=tuple(descending) if descending else None,
        variable=tuple(variable) if variable else None,
        conventional_tonic=tonic, key_signature_parent=parent,  # type: ignore[arg-type]
        source=source, confidence=confidence, caveats=tuple(caveats), aliases=tuple(aliases),  # type: ignore[arg-type]
    )


def cycle(id: str, label: str, beats: int, subdivision: int, time_signature: tuple[int, int], *,
          structure: Sequence[str] = (), accents: Sequence[float] = (), groups: Sequence[int] = (),
          source: str, confidence: str = "medium", caveats: Iterable[str] = (),
          swing_ratio: Optional[float] = None) -> RhythmTemplate:
    return RhythmTemplate(
        id=id, label=label, cycle_beats=beats, subdivision=subdivision, time_signature=time_signature,
        structure=tuple(structure), accent_pattern=tuple(accents), groups=tuple(groups),
        source=source, confidence=confidence, caveats=tuple(caveats), swing_ratio=swing_ratio,  # type: ignore[arg-type]
    )


def strokes(pattern: str, dum: float = 1.0, tak: float = 0.6) -> tuple[tuple[str, ...], tuple[float, ...]]:
    """"D T - T D - T -" -> (structure, accents) for an iqa', one entry per pulse."""
    tokens = tuple(pattern.split())
    weight = {"D": dum, "T": tak}
    return tokens, tuple(weight.get(t, 0.0) for t in tokens)


def grid24(tol: float = 25.0) -> tuple[GridPosition, ...]:
    """The nominal 24-step grid used for naming in the Arabic and Persian adapters.

    Names are interval labels from the tonic: "3-" is a lowered (half-flat-like) third,
    "4+" a raised fourth. The 24 steps are for naming, not a claim about tuning.
    """
    rows = [
        ("1", 1), ("1+", 1), ("b2", 2), ("2-", 2), ("2", 2), ("2+", 2), ("b3", 3), ("3-", 3),
        ("3", 3), ("4-", 4), ("4", 4), ("4+", 4), ("#4", 4), ("5-", 5), ("5", 5), ("5+", 5),
        ("b6", 6), ("6-", 6), ("6", 6), ("6+", 6), ("b7", 7), ("7-", 7), ("7", 7), ("7+", 7),
    ]
    return tuple(GridPosition(name=name, cents=50.0 * i, degree=degree, tol=tol)
                 for i, (name, degree) in enumerate(rows))
