"""IR invariants (spec 6.6) and readable validation messages."""
from __future__ import annotations

import json
from typing import Any, Union

from pydantic import ValidationError

from .models import IR, Model

EPS_S = 1e-6


class IRProblem(Model):
    code: str
    message: str  # one plain sentence a user can act on
    path: str = ""


class IRInvalid(ValueError):
    def __init__(self, problems: list[IRProblem]):
        self.problems = problems
        super().__init__("; ".join(p.message for p in problems))


def validate_ir(ir: IR) -> list[IRProblem]:
    """Check the invariants a model schema cannot express. Empty list means valid."""
    out: list[IRProblem] = []

    def add(code: str, message: str, path: str = "") -> None:
        out.append(IRProblem(code=code, message=message, path=path))

    if ir.tonic.hz <= 0:
        add("tonic_not_positive", f"The tonic must be a positive frequency, got {ir.tonic.hz} Hz.", "tonic.hz")

    seen: set[str] = set()
    prev = None
    for i, e in enumerate(ir.events):
        path = f"events[{i}]"
        if e.id in seen:
            add("duplicate_event_id", f"Two notes share the id '{e.id}'.", path)
        seen.add(e.id)
        if e.offset_s <= e.onset_s:
            add("event_not_positive", f"Note {i + 1} ends at {e.offset_s:.3f} s, not after it starts ({e.onset_s:.3f} s).", path)
        if prev is not None:
            if e.onset_s < prev.onset_s:
                add("events_unsorted", f"Note {i + 1} starts at {e.onset_s:.3f} s, before note {i} ({prev.onset_s:.3f} s). Notes must be in time order.", path)
            elif e.onset_s < prev.offset_s - EPS_S:
                add("events_overlap", f"Note {i + 1} starts at {e.onset_s:.3f} s, before note {i} ends ({prev.offset_s:.3f} s). A single melody line cannot overlap.", path)
        if not (e.pitch_cents_min - EPS_S <= e.pitch_cents_median <= e.pitch_cents_max + EPS_S):
            add("event_pitch_range", f"Note {i + 1} has a median pitch outside its own minimum and maximum.", path)
        for name, value in (("confidence", e.confidence), ("voiced_fraction", e.voiced_fraction)):
            if not 0.0 <= value <= 1.0:
                add("out_of_range", f"Note {i + 1} has {name} {value}, which must be between 0 and 1.", f"{path}.{name}")
        for o in (e.ornament, *e.extra_ornaments):
            if o.contour_ref is not None:
                a, b = o.contour_ref
                if not (0 <= a <= b <= len(ir.contour)):
                    add("contour_ref_range", f"Note {i + 1} points at contour frames {a}-{b}, but the contour has {len(ir.contour)} frames.", f"{path}.ornament.contour_ref")
        prev = e

    last_t = None
    for i, p in enumerate(ir.contour):
        if last_t is not None and p.t_s <= last_t:
            add("contour_not_monotonic", f"Contour frame {i} is at {p.t_s:.3f} s, not after the previous frame ({last_t:.3f} s).", f"contour[{i}]")
            break
        last_t = p.t_s

    if ir.cycle.length_pulses is not None and ir.cycle.length_pulses <= 0:
        add("cycle_length", "The cycle length must be a positive number of pulses.", "cycle.length_pulses")
    if ir.cycle.subdivision <= 0:
        add("cycle_subdivision", "The subdivision must be a positive number of pulses per beat.", "cycle.subdivision")
    if ir.tempo.free_time and ir.tempo.bpm is not None:
        add("free_time_with_bpm", "Free-time music cannot also carry a tempo in beats per minute.", "tempo.bpm")
    return out


def assert_valid(ir: IR) -> IR:
    problems = validate_ir(ir)
    if problems:
        raise IRInvalid(problems)
    return ir


def readable_errors(exc: ValidationError) -> list[IRProblem]:
    """Turn pydantic's errors into sentences (spec 6.6: readable messages)."""
    out = []
    for err in exc.errors():
        path = ".".join(f"[{p}]" if isinstance(p, int) else str(p) for p in err["loc"]).replace(".[", "[")
        kind = err["type"]
        if kind == "missing":
            msg = f"'{path}' is required but missing."
        elif kind == "extra_forbidden":
            msg = f"'{path}' is not a field Orin knows."
        elif kind == "finite_number":
            msg = f"'{path}' must be a finite number (not NaN or infinity)."
        else:
            msg = f"'{path}': {err['msg']}."
        out.append(IRProblem(code=f"schema.{kind}", message=msg, path=path))
    return out


def load_ir(data: Union[str, bytes, dict[str, Any]]) -> IR:
    """Parse and fully validate an IR. Raises IRInvalid with readable problems."""
    try:
        if isinstance(data, (str, bytes)):
            ir = IR.model_validate_json(data)
        else:
            ir = IR.model_validate(data)
    except ValidationError as exc:
        raise IRInvalid(readable_errors(exc)) from exc
    except json.JSONDecodeError as exc:  # pragma: no cover - pydantic wraps this
        raise IRInvalid([IRProblem(code="json", message=f"The file is not valid JSON: {exc.msg}.")]) from exc
    return assert_valid(ir)


def dump_ir(ir: IR, indent: int | None = None) -> str:
    return ir.model_dump_json(indent=indent)
