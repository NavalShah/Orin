"""Evaluation runner (spec 7.15).

``run_synthetic()`` renders a built-in set of cases with known ground truth, runs the
audio pipeline on each and returns the metrics; ``tests/test_eval.py`` compares them
with thresholds, so a pipeline change that makes them drop fails the suite.

``run_real(manifest)`` does the same for real recordings listed in a JSON manifest
(default ``orin/eval/real_clips.json``, which ships empty: no recording has been
cleared for inclusion yet). The manifest is a list of clips::

    {
      "path": "clips/varnam_excerpt.wav",   audio file, relative to the manifest
      "licence": "CC BY 4.0",               required: a clip without one is not evaluated
      "provenance": "who recorded it, where it came from, a URL",   required
      "style": "carnatic",                  the format id a musician would file it under
      "tonic_hz": 146.83,                   the reference tonic
      "notes": [[0.52, 0.91, 146.8], ...],  optional reference notes: onset_s, offset_s, hz
      "mode_id": "mohanam",                 optional
      "cycle_pulses": 32                    optional; null means free time
    }

Synthetic scores say the pipeline is wired correctly. They say little about real
recordings: only the real set can, and until it is filled nothing here should be
quoted as accuracy.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence, Union

import numpy as np

from ..cache import StageCache
from ..ir.models import IR
from ..options import AnalysisOptions
from . import metrics, synth

DEFAULT_MANIFEST = Path(__file__).with_name("real_clips.json")
LEAD_S = 0.25
GAP_S = 0.04


@dataclass
class Truth:
    style: str
    tonic_hz: float
    notes: list[metrics.Note] = field(default_factory=list)  # (onset_s, offset_s, hz)
    mode_id: Optional[str] = None
    cycle_pulses: Optional[int] = None
    has_cycle: bool = False  # whether ``cycle_pulses`` is a ground truth at all


@dataclass
class SynthCase:
    id: str
    style: str
    tonic_hz: float
    # (cents above the tonic, seconds) or (cents, seconds, oscillation half-extent, oscillation Hz)
    notes: Sequence[tuple]
    mode_id: Optional[str] = None
    drone_ratios: Sequence[float] = ()  # drone tones as ratios of ``drone_hz``
    drone_hz: Optional[float] = None

    def render(self) -> tuple[np.ndarray, Truth]:
        parts = [synth.silence(LEAD_S)]
        truth = Truth(style=self.style, tonic_hz=self.tonic_hz, mode_id=self.mode_id)
        now = LEAD_S
        for i, note in enumerate(self.notes):
            cents, dur = float(note[0]), float(note[1])
            if len(note) >= 4:
                parts.append(synth.oscillation(cents, float(note[2]), float(note[3]), dur))
            else:
                parts.append(synth.const(cents, dur))
            truth.notes.append((now, now + dur, self.tonic_hz * 2.0 ** (cents / 1200.0)))
            now += dur
            if i < len(self.notes) - 1:
                parts.append(synth.silence(GAP_S))
                now += GAP_S
        parts.append(synth.silence(LEAD_S))
        y = synth.render(synth.concat(*parts), self.tonic_hz)
        if self.drone_ratios:
            base = self.drone_hz or self.tonic_hz
            y = synth.mix(y, synth.drone([base * r for r in self.drone_ratios], len(y) / synth.SR))
        return y, truth


def _steps(cents: Sequence[float], note_s: float = 0.4, last_s: float = 1.0) -> list[tuple]:
    return [(c, last_s if i == len(cents) - 1 else note_s) for i, c in enumerate(cents)]


SYNTH_CASES: tuple[SynthCase, ...] = (
    SynthCase("western_major", "western_classical", 220.0,
              _steps([0, 200, 400, 500, 700, 900, 1100, 1200, 1100, 900, 700, 500, 400, 200, 0]), mode_id="major"),
    SynthCase("western_minor", "western_classical", 196.0,
              _steps([0, 200, 300, 500, 700, 800, 1000, 1200, 1000, 800, 700, 500, 300, 200, 0]),
              mode_id="natural_minor"),
    # Mohanam over a Sa-Pa drone an octave below, with one oscillated note (kampita-like).
    SynthCase("carnatic_mohanam_drone", "carnatic", 293.66,
              [(0, 0.5), (204, 0.4), (386, 0.8, 60.0, 5.0), (702, 0.4), (884, 0.5), (702, 0.4),
               (386, 0.8, 60.0, 5.0), (204, 0.4), (0, 0.5), (204, 0.4), (386, 0.4), (204, 0.4), (0, 1.0)],
              mode_id="mohanam", drone_ratios=(0.5, 0.75, 1.0)),
    # Rast: its third and seventh sit between the piano's keys.
    SynthCase("arabic_rast", "arabic_maqam", 180.0,
              _steps([0, 200, 350, 500, 700, 900, 1050, 1200, 1050, 900, 700, 500, 350, 200, 0]), mode_id="rast"),
    SynthCase("pop_minor_pentatonic", "western_pop", 246.94,
              _steps([0, 300, 500, 700, 1000, 1200, 1000, 700, 500, 300, 0, 300, 0]), mode_id="minor_pentatonic"),
)


def ir_notes(ir: IR) -> list[metrics.Note]:
    return [(e.onset_s, e.offset_s, ir.tonic.hz * 2.0 ** (e.pitch_cents / 1200.0)) for e in ir.events]


def evaluate_ir(ir: IR, truth: Truth, detected_style: Optional[str] = None) -> dict[str, Any]:
    """Per-clip scores. Keys whose ground truth is missing are None."""
    est = ir_notes(ir)
    ranked = [c.id for c in ir.mode.candidates]
    if ir.mode.mode_id and ir.mode.mode_id not in ranked:
        ranked.insert(0, ir.mode.mode_id)
    row: dict[str, Any] = {
        "tonic_hz": ir.tonic.hz, "tonic_error_cents": metrics.tonic_error_cents(ir.tonic.hz, truth.tonic_hz),
        "tonic_correct": metrics.tonic_correct(ir.tonic.hz, truth.tonic_hz),
        "tonic_method": ir.tonic.method, "n_events": len(ir.events),
        "onset_f1": None, "note_f1": None, "pitch_error_cents": None,
        "mode_ranked": ranked, "mode_top1": None, "mode_top3": None,
        "cycle_pulses": ir.cycle.length_pulses, "cycle_correct": None,
        "style": truth.style, "detected_style": detected_style,
        "warnings": [w.code for w in ir.meta.warnings],
    }
    if truth.notes:
        row["onset_f1"] = metrics.onset_f1([n[0] for n in truth.notes], [n[0] for n in est])
        row["note_f1"] = metrics.note_f1(truth.notes, est)
        row["pitch_error_cents"] = metrics.median_pitch_error_cents(truth.notes, est)
    if truth.mode_id:
        row["mode_top1"] = truth.mode_id in ranked[:1]
        row["mode_top3"] = truth.mode_id in ranked[:3]
    if truth.has_cycle:
        row["cycle_correct"] = ir.cycle.length_pulses == truth.cycle_pulses
    return row


def _mean(rows: Sequence[dict], key: str) -> Optional[float]:
    values = [float(r[key]) for r in rows if r.get(key) is not None]
    return float(np.mean(values)) if values else None


def summarise(rows: Sequence[dict]) -> dict[str, Any]:
    """Aggregate per-clip rows into the spec's metric list. None = no ground truth for it."""
    errors = [r["pitch_error_cents"] for r in rows if r.get("pitch_error_cents") is not None]
    styled = [r for r in rows if r.get("detected_style") is not None]
    return {
        "n": len(rows),
        "tonic_accuracy": _mean(rows, "tonic_correct"),
        "onset_f1": _mean(rows, "onset_f1"),
        "note_f1": _mean(rows, "note_f1"),
        "median_pitch_error_cents": float(np.median(errors)) if errors else None,
        "mode_top1": _mean(rows, "mode_top1"),
        "mode_top3": _mean(rows, "mode_top3"),
        "cycle_accuracy": _mean(rows, "cycle_correct"),
        "style_accuracy": metrics.accuracy([r["style"] for r in styled], [r["detected_style"] for r in styled]) if styled else None,
        "style_confusion": metrics.confusion_matrix([r["style"] for r in styled], [r["detected_style"] for r in styled]),
    }


def _run_clip(path: Union[str, Path], truth: Truth, cache: StageCache, trackers: str) -> dict[str, Any]:
    """Two passes over one file: the style given (what the note, tonic and mode scores are
    about) and auto-detect (for the style confusion matrix). The second reuses the cached
    pitch track."""
    from ..pipeline import audio_to_ir

    ir = audio_to_ir(path, AnalysisOptions(style=truth.style, trackers=trackers), cache=cache)  # type: ignore[arg-type]
    auto = audio_to_ir(path, AnalysisOptions(style="auto", trackers=trackers), cache=cache)  # type: ignore[arg-type]
    return evaluate_ir(ir, truth, detected_style=auto.source.detected_format)


def run_synthetic(*, trackers: str = "auto", cases: Optional[Sequence[SynthCase]] = None,
                  work_dir: Union[str, Path, None] = None) -> dict[str, Any]:
    """Metrics over the synthetic cases. ``trackers`` is passed to the pipeline ("pyin" is
    the fast choice for CI). Returns the aggregate under its metric names and the
    per-case rows under "cases"."""
    cases = SYNTH_CASES if cases is None else cases
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="orin-eval-") as tmp:
        root = Path(work_dir) if work_dir is not None else Path(tmp)
        root.mkdir(parents=True, exist_ok=True)
        cache = StageCache(root / "cache")
        for case in cases:
            y, truth = case.render()
            wav = synth.write_wav(str(root / f"{case.id}.wav"), y)
            row = _run_clip(wav, truth, cache, trackers)
            rows.append({"id": case.id, **row})
    return {**summarise(rows), "cases": rows}


def load_manifest(manifest_path: Union[str, Path, None] = None) -> list[dict]:
    path = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST
    with path.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    clips = doc.get("clips", []) if isinstance(doc, dict) else doc
    if not isinstance(clips, list):
        raise ValueError("The manifest must be a list of clips.")
    return clips


def run_real(manifest_path: Union[str, Path, None] = None, *, trackers: str = "auto",
             cache: Union[StageCache, str, Path, None] = None) -> dict[str, Any]:
    """Metrics over the real recordings in the manifest. Clips without a licence and a
    provenance, or whose file is missing, are listed under "skipped" and not evaluated."""
    path = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST
    clips = load_manifest(path)
    rows: list[dict] = []
    skipped: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="orin-eval-") as tmp:
        store = cache if isinstance(cache, StageCache) else StageCache(cache if cache is not None else Path(tmp) / "cache")
        for clip in clips:
            name = str(clip.get("path", ""))
            missing = [k for k in ("path", "licence", "provenance", "style", "tonic_hz") if not clip.get(k)]
            audio = (path.parent / name) if name and not Path(name).is_absolute() else Path(name)
            if missing:
                skipped.append({"path": name, "reason": f"missing {', '.join(missing)}"})
                continue
            if not audio.is_file():
                skipped.append({"path": name, "reason": "file not found"})
                continue
            truth = Truth(style=clip["style"], tonic_hz=float(clip["tonic_hz"]),
                          notes=[(float(a), float(b), float(hz)) for a, b, hz in clip.get("notes") or []],
                          mode_id=clip.get("mode_id"), cycle_pulses=clip.get("cycle_pulses"),
                          has_cycle="cycle_pulses" in clip)
            try:
                row = _run_clip(audio, truth, store, trackers)
            except Exception as exc:  # one unreadable clip must not lose the rest of the report
                skipped.append({"path": name, "reason": f"{type(exc).__name__}: {exc}"})
                continue
            rows.append({"id": name, "licence": clip["licence"], "provenance": clip["provenance"], **row})
    return {**summarise(rows), "clips": len(clips), "evaluated": len(rows), "skipped": skipped, "cases": rows}
