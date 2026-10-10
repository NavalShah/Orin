"""Audio or notes -> IR (spec section 7). The public signatures are documented in ``orin/options.py``.

Every audio stage goes through ``_Run.stage``, which times it, reads and writes the stage
cache, reports progress, appends a ``StageLog`` and, when the stage raises, falls back,
adds a warning to the IR and carries on (spec 7.16). Only a file that cannot be ingested
aborts the run.

Cache keys chain: a stage's key covers its own parameters and those of every stage before
it. Changing the tonic, mode, style, sensitivity, cycle or tempo therefore re-runs from the
first stage that depends on it and reuses everything earlier, pitch tracking included.

Confidence policy (spec 6.5): an event's confidence is the segmenter's, which is
min(mean tracker confidence over the note, plateau stability). Tonic, mode, tempo and
cycle carry their own confidences and are not multiplied in: a doubtful tonic is one
doubt about the whole transcription, shown once and prominently, not a reason to dim
every note.
"""
from __future__ import annotations

import time
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

import numpy as np

from . import config
from .analysis_types import PitchTrack, RhythmResult, Segment
from .cache import StageCache, as_cache, params_hash
from .errors import IngestError, OrinError
from .ir.models import (IR, Analysis, ContourPoint, Cycle, FormatCandidate, IRWarning, Meta, Mode, NoteEvent,
                        Ornament, Source, StageLog, Tempo, Tonic)
from .ir.validate import validate_ir
from .options import STAGES, AnalysisOptions, Progress

PIPELINE_VERSION = "1"
SR_ANALYSIS = 22050
SR_PITCH = 16000
MIN_VOICED_FRACTION = 0.10  # under this the recording has no melody to transcribe
LOW_EVENT_CONFIDENCE = 0.4
UNCERTAIN_TONIC = 0.5
ASSUMED_TONIC_HZ = 220.0

_LABELS = {
    "ingest": "Reading the audio", "preprocess": "Cleaning up the audio", "separate": "Separating the melody",
    "pitch": "Tracking pitch", "voicing": "Finding the sung or played frames", "detect": "Detecting the style",
    "tonic": "Finding the tonic", "segment": "Finding notes", "ornament": "Measuring ornaments",
    "rhythm": "Finding the pulse", "mode": "Matching modes", "assemble": "Putting it together",
}


@lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """Changes whenever any analysis code or config file changes, so a cached stage result
    is never served by code that would now compute something else. Computed once per process."""
    here = Path(__file__).resolve().parent
    files = sorted([*here.rglob("*.py"), *here.rglob("*.toml"), *(here.parent / "pipeline").glob("*.py")])
    stamp = []
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        stamp.append((str(f.relative_to(here.parent)), st.st_size, st.st_mtime_ns))
    return params_hash(stamp)


# ---------------------------------------------------------------------------
# The stage helper
# ---------------------------------------------------------------------------

class _Run:
    def __init__(self, cache: StageCache, progress: Optional[Progress], content_hash: str = ""):
        self.cache = cache
        self.progress = progress
        self.hash = content_hash
        self.chain = ""  # hash of the parameters of every stage so far
        self.logs: list[StageLog] = []
        self.warnings: list[IRWarning] = []

    def warn(self, code: str, message: str, stage: Optional[str] = None) -> None:
        if not any(w.code == code and w.stage == stage for w in self.warnings):
            self.warnings.append(IRWarning(code=code, message=message, stage=stage))

    def _report(self, stage: str, fraction: float, message: str) -> None:
        if self.progress is None:
            return
        try:
            self.progress(stage, float(min(1.0, max(0.0, fraction))), message)
        except Exception:
            pass  # a broken progress callback must not stop the analysis

    def stage(self, name: str, params: dict[str, Any], compute: Callable[[], Any], *,
              fallback: Optional[Callable[[], Any]] = None,
              pack: Optional[Callable[[Any], tuple[Optional[dict], Any]]] = None,
              unpack: Optional[Callable[[dict, Any], Any]] = None,
              note: Optional[str] = None, hit_of: Optional[Callable[[Any], bool]] = None) -> Any:
        """Run one stage. ``pack``/``unpack`` turn its value into (arrays, JSON) and back;
        a stage without them is not cached. ``fallback`` makes the stage fail soft."""
        index = STAGES.index(name) if name in STAGES else 0
        label = _LABELS.get(name, name)
        self._report(name, index / len(STAGES), f"{label}...")
        started = time.perf_counter()
        key = {"v": PIPELINE_VERSION, "code": code_fingerprint(), "upstream": self.chain, "params": params}
        cacheable = pack is not None and unpack is not None and bool(self.hash)
        value: Any = None
        hit = False
        if cacheable:
            entry = self.cache.get(self.hash, name, key)
            if entry is not None:
                try:
                    value = unpack(entry.arrays, entry.data)
                    hit = True
                except Exception:
                    hit = False
        if not hit:
            try:
                value = compute()
            except Exception as exc:
                if fallback is None:
                    raise
                value = fallback()
                detail = exc.message if isinstance(exc, OrinError) else f"{type(exc).__name__}: {exc}"
                self.warn(f"{name}_failed", f"{label} failed ({detail}). Orin carried on with a simpler "
                                            "fallback, so this part of the result is less reliable.", name)
                note = f"failed ({type(exc).__name__}); fallback used"
            else:
                if cacheable:
                    arrays, data = pack(value)
                    self.cache.put(self.hash, name, key, arrays=arrays, data=data)
        if hit_of is not None:
            hit = bool(hit_of(value))
        self.chain = params_hash([self.chain, name, params])
        self.logs.append(StageLog(stage=name, version=PIPELINE_VERSION, params=params,
                                  duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
                                  cache_hit=hit, note=note))
        self._report(name, (index + 1) / len(STAGES), f"{label}: done")
        return value

    def skip(self, name: str, params: dict[str, Any], note: str) -> None:
        self.stage(name, params, lambda: None, note=note)

    def annotate(self, note: str) -> None:
        """Add a remark to the log entry of the stage that just ran."""
        last = self.logs[-1]
        self.logs[-1] = last.model_copy(update={"note": f"{last.note}; {note}" if last.note else note})


# ---------------------------------------------------------------------------
# (arrays, JSON) forms of the stage values
# ---------------------------------------------------------------------------

def _pack_track(track: PitchTrack) -> tuple[dict, Any]:
    return ({"t": track.t, "f0": track.f0, "conf": track.conf},
            {"hop_s": track.hop_s, "tracker": track.tracker, "disagreements": int(track.disagreements),
             "octave_fixes": int(track.octave_fixes)})


def _unpack_track(arrays: dict, data: Any) -> PitchTrack:
    return PitchTrack(t=arrays["t"], f0=arrays["f0"], conf=arrays["conf"], hop_s=float(data["hop_s"]),
                      tracker=data["tracker"], disagreements=int(data["disagreements"]),
                      octave_fixes=int(data["octave_fixes"]))


_SEGMENT_FIELDS = ("start", "end", "anchor_cents", "median_cents", "min_cents", "max_cents",
                   "voiced_fraction", "confidence", "onset_split")


def _pack_segments(segments: Sequence[Segment]) -> tuple[dict, Any]:
    arrays = {f: np.array([getattr(s, f) for s in segments], dtype=float) for f in _SEGMENT_FIELDS}
    return arrays, {"n": len(segments), "ornaments": [[o.model_dump() for o in s.ornaments] for s in segments]}


def _unpack_segments(arrays: dict, data: Any) -> list[Segment]:
    out = []
    for i in range(int(data["n"])):
        out.append(Segment(
            start=int(arrays["start"][i]), end=int(arrays["end"][i]),
            anchor_cents=float(arrays["anchor_cents"][i]), median_cents=float(arrays["median_cents"][i]),
            min_cents=float(arrays["min_cents"][i]), max_cents=float(arrays["max_cents"][i]),
            voiced_fraction=float(arrays["voiced_fraction"][i]), confidence=float(arrays["confidence"][i]),
            onset_split=bool(arrays["onset_split"][i]),
            ornaments=[Ornament.model_validate(o) for o in data["ornaments"][i]]))
    return out


def _pack_rhythm(r: RhythmResult) -> tuple[Optional[dict], Any]:
    return None, {"tempo": r.tempo.model_dump(), "cycle": r.cycle.model_dump(),
                  "onset_pulses": list(r.onset_pulses), "duration_pulses": list(r.duration_pulses),
                  "cycle_candidates": [list(c) for c in r.cycle_candidates],
                  "half_bpm": r.half_bpm, "double_bpm": r.double_bpm}


def _unpack_rhythm(_arrays: dict, d: Any) -> RhythmResult:
    return RhythmResult(tempo=Tempo.model_validate(d["tempo"]), cycle=Cycle.model_validate(d["cycle"]),
                        onset_pulses=list(d["onset_pulses"]), duration_pulses=list(d["duration_pulses"]),
                        cycle_candidates=[(c[0], float(c[1])) for c in d["cycle_candidates"]],
                        half_bpm=d.get("half_bpm"), double_bpm=d.get("double_bpm"))


def _json(value: Any) -> tuple[Optional[dict], Any]:
    return None, value


def _unjson(_arrays: dict, data: Any) -> Any:
    if data is None:
        raise ValueError("empty cache entry")
    return data


# ---------------------------------------------------------------------------
# Stage bodies
# ---------------------------------------------------------------------------

def _free_rhythm(n: int) -> RhythmResult:
    return RhythmResult(tempo=Tempo(bpm=None, confidence=0.0, free_time=True), cycle=Cycle(),
                        onset_pulses=[None] * n, duration_pulses=[None] * n)


def _fallback_segments(cents: np.ndarray, conf: np.ndarray, profile, hop_s: float) -> list[Segment]:
    """Crude notes for when the segmenter fails: voiced runs, cut where the pitch leaves
    the run's median by more than the profile's plateau threshold."""
    from .pitch.voicing import runs

    min_frames = max(2, int(round(profile.min_note_ms / 1000.0 / hop_s)))
    out: list[Segment] = []
    for a, b in runs(np.isfinite(cents)):
        start = a
        for i in range(a + 1, b + 1):
            if i < b and abs(cents[i] - np.median(cents[start:i])) <= profile.plateau_enter_cents:
                continue
            if i - start >= min_frames:
                x = cents[start:i]
                med = float(np.median(x))
                out.append(Segment(start=start, end=i, anchor_cents=med, median_cents=med, min_cents=float(x.min()),
                                   max_cents=float(x.max()), voiced_fraction=1.0,
                                   confidence=float(min(0.5, np.mean(conf[start:i])))))
            start = i
    return out


def _without_drone_frames(track: PitchTrack, y22_original: np.ndarray) -> PitchTrack:
    """Unvoice the frames where the tracker followed a drone instead of the melody."""
    from .pitch.voicing import MIN_RUN_FRAMES, runs
    from .tonic.drone import drone_only_frames, find_drone

    try:
        drone = find_drone(y22_original, SR_ANALYSIS, track)
        mask = drone_only_frames(track, y22_original, SR_ANALYSIS, drone)
    except Exception:
        return track  # the drone is a refinement; the track stands without it
    if not mask.any():
        return track
    f0 = np.where(mask, np.nan, track.f0)
    for a, b in runs(np.isfinite(f0)):
        if b - a < MIN_RUN_FRAMES:
            f0[a:b] = np.nan
    return replace(track, f0=f0)


def _onset_envelope(y: np.ndarray, sr: int, offset_s: float) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Onset strength and its frame times in the original file's time, or (None, None)."""
    try:
        from .rhythm.onsets import onset_envelope

        env, env_t = onset_envelope(y, sr)
    except Exception:
        try:
            import librosa

            env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
            env_t = librosa.frames_to_time(np.arange(len(env)), sr=sr, hop_length=512)
        except Exception:
            return None, None
    if env is None or len(env) < 3:
        return None, None
    return np.asarray(env, dtype=float), np.asarray(env_t, dtype=float) + offset_s


class _Shared:
    """Things more than one stage needs, computed once per run and only when asked for."""

    def __init__(self, y22: np.ndarray, rhythm_y: np.ndarray, offset_s: float):
        self.y22, self.rhythm_y, self.offset_s = y22, rhythm_y, offset_s
        self._env: Optional[tuple] = None
        self._beats: Optional[tuple] = None

    def envelope(self) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if self._env is None:
            self._env = _onset_envelope(self.y22, SR_ANALYSIS, self.offset_s)
        return self._env

    def beats(self) -> tuple[Optional[np.ndarray], Optional[float]]:
        """Beat times (original file time) and the tracker's confidence, from the original
        pipeline's ``estimate_tempo``. (None, None) when it cannot run."""
        if self._beats is None:
            try:
                from pipeline.analyze import estimate_tempo

                if len(self.rhythm_y) < SR_ANALYSIS:  # under a second there is no beat to find
                    raise ValueError("too short")
                import warnings

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    found = estimate_tempo(np.asarray(self.rhythm_y, dtype=np.float32), SR_ANALYSIS)
                self._beats = (np.asarray(found["beat_times"], dtype=float) + self.offset_s, float(found["confidence"]))
            except Exception:
                self._beats = (None, None)
        return self._beats


def _notes_of(segments: Sequence[Segment], t: np.ndarray, hop_s: float) -> tuple[list[float], list[float]]:
    onsets = [float(t[min(s.start, len(t) - 1)]) for s in segments]
    return onsets, [float((s.end - s.start) * hop_s) for s in segments]


def _all_templates() -> list:
    from .formats import registry

    seen: dict[str, Any] = {}
    for meta in registry.list_meta():
        for tp in meta.rhythm_templates:
            seen.setdefault(tp.id, tp)
    return list(seen.values())


def _templates_for(meta) -> list:
    """The format's templates, its default first."""
    tps = list(meta.rhythm_templates)
    tps.sort(key=lambda tp: tp.id != meta.default_template_id)
    return tps


def _detect(track: PitchTrack, audio_y22: np.ndarray, shared: _Shared, options: AnalysisOptions,
            duration_s: float) -> dict:
    """Style auto-detect (spec 8): a first pass with the neutral profile, then the scorer."""
    from .detect.features import extract_features
    from .detect.score import decide, rank_formats
    from .rhythm import analyze as analyze_rhythm
    from .segment.notes import segment_contour
    from .segment.ornaments import annotate
    from .tonic.select import detect_tonic

    profile = config.profile("neutral", options.sensitivity)
    tonic, drone = detect_tonic(track, audio=audio_y22, sr=SR_ANALYSIS, style=None, tonic_hz=options.tonic_hz)
    cents = track.cents(tonic.hz)
    env, env_t = shared.envelope()
    segments = annotate(segment_contour(track.t, cents, track.conf, profile, onset_env=env, onset_env_t=env_t),
                        track.t, cents, profile)
    rhythm = None
    try:
        onsets, durations = _notes_of(segments, track.t, track.hop_s)
        beat_times, beat_conf = shared.beats()
        rhythm = analyze_rhythm(onsets, durations, templates=_all_templates(), beat_times=beat_times,
                                beat_confidence=beat_conf)
    except Exception:
        rhythm = None  # the features that need rhythm stay at their defaults
    features = extract_features(t=track.t, cents=cents, conf=track.conf, segments=segments, tonic=tonic,
                                rhythm=rhythm, audio=shared.y22, sr=SR_ANALYSIS, drone_confidence=drone)
    candidates = rank_formats(features)
    if not candidates:
        raise ValueError("no format could be scored")
    needs_choice, message = decide(candidates, duration_s)
    return {"style": candidates[0].format_id, "candidates": [c.model_dump() for c in candidates],
            "needs_choice": bool(needs_choice), "message": message}


def _separate(audio, pre: dict, options: AnalysisOptions) -> tuple[dict, dict]:
    """(arrays, info). Arrays are empty when separation did not run."""
    from .ingest.audio import SR_PLAYBACK, _resample
    from .ingest.preprocess import apply_like, normalise
    from .separate.demucs import separate_with_reason
    from .separate.select import select_melody_stem, to_mono

    stems, reason = separate_with_reason(audio.y44k, SR_PLAYBACK)
    if stems is None:
        return {}, {"ran": False, "stem": None, "reason": reason}
    name = select_melody_stem(stems, options.melody_stem)
    melody = apply_like(_resample(to_mono(stems[name]), SR_PLAYBACK, SR_PITCH), SR_PITCH, {**pre, "gain": 1.0})
    arrays = {"melody16": normalise(melody)[0]}
    if "drums" in stems:
        arrays["drums22"] = apply_like(_resample(to_mono(stems["drums"]), SR_PLAYBACK, SR_ANALYSIS), SR_ANALYSIS, pre)
    return arrays, {"ran": True, "stem": name, "reason": None}


def _frame_db(y: np.ndarray, n_frames: int, hop: int) -> np.ndarray:
    """RMS level in dBFS per 10 ms frame."""
    frame = 2 * hop
    padded = np.pad(np.square(np.asarray(y, dtype=np.float64)), (frame // 2, frame + hop))
    csum = np.concatenate([[0.0], np.cumsum(padded)])
    starts = np.minimum(np.arange(n_frames) * hop, len(csum) - frame - 1)
    rms = np.sqrt(np.maximum(csum[starts + frame] - csum[starts], 0.0) / frame)
    return 20.0 * np.log10(np.maximum(rms, 1e-6))


def _assemble(*, source: Source, tonic: Tonic, mode: Mode, rhythm: RhythmResult, segments: Sequence[Segment],
              track: Optional[PitchTrack], level_db: Optional[np.ndarray]) -> IR:
    contour: tuple[ContourPoint, ...] = ()
    events: list[NoteEvent] = []
    hop = 0.01
    if track is not None and len(track.t) and segments:
        hop = float(track.hop_s)
        t = np.asarray(track.t, dtype=float)
        cents = track.cents(tonic.hz)
        conf = np.clip(np.nan_to_num(np.asarray(track.conf, dtype=float)), 0.0, 1.0)
        n = len(t)
        contour = tuple(
            ContourPoint(t_s=round(float(t[i]), 4), cents=round(float(cents[i]), 2) if np.isfinite(cents[i]) else None,
                         confidence=round(float(conf[i]), 3))
            for i in range(n))

        def ref(o: Ornament, seg: Segment) -> Ornament:
            if o.kind is None:
                return o
            a, b = o.contour_ref if o.contour_ref is not None else (seg.start, seg.end)
            a = int(min(max(a, 0), n))
            return o.model_copy(update={"contour_ref": (a, int(min(max(b, a), n)))})

        usable = [s for s in segments
                  if 0 <= s.start < s.end <= n and all(np.isfinite([s.anchor_cents, s.median_cents, s.min_cents, s.max_cents]))]
        usable.sort(key=lambda s: s.start)
        pulses = list(rhythm.onset_pulses) if len(rhythm.onset_pulses) == len(segments) else [None] * len(segments)
        lengths = list(rhythm.duration_pulses) if len(rhythm.duration_pulses) == len(segments) else [None] * len(segments)
        index = {id(s): i for i, s in enumerate(segments)}
        last_offset = -1.0
        for s in usable:
            onset = max(float(t[s.start]), last_offset)
            offset = float(t[s.end - 1]) + hop
            if offset <= onset:
                continue
            last_offset = offset
            ornaments = [ref(o, s) for o in s.ornaments]
            i = index[id(s)]
            lo, hi = min(s.min_cents, s.median_cents), max(s.max_cents, s.median_cents)
            events.append(NoteEvent(
                id=f"e{len(events) + 1}", onset_s=round(onset, 4), offset_s=round(offset, 4),
                onset_pulse=pulses[i], duration_pulses=lengths[i],
                pitch_cents=round(float(s.anchor_cents), 2), pitch_cents_median=round(float(s.median_cents), 2),
                pitch_cents_min=round(float(lo), 2), pitch_cents_max=round(float(hi), 2),
                loudness_db=round(float(np.mean(level_db[s.start:s.end])), 1) if level_db is not None else None,
                voiced_fraction=float(np.clip(s.voiced_fraction, 0.0, 1.0)),
                ornament=ornaments[0] if ornaments else Ornament(),
                extra_ornaments=tuple(ornaments[1:]),
                confidence=round(float(np.clip(s.confidence, 0.0, 1.0)), 3)))

    tempo = rhythm.tempo
    if tempo.free_time and tempo.bpm is not None:
        tempo = tempo.model_copy(update={"bpm": None})
    return IR(source=source, tonic=tonic, mode=mode, tempo=tempo, cycle=rhythm.cycle,
              events=tuple(events), contour=contour, contour_hop_s=hop)


def _user_overrides(options: AnalysisOptions) -> dict[str, Any]:
    """Every choice the user made that changes the analysis (spec 7.12, 7.13)."""
    defaults = AnalysisOptions()
    watched = ("style", "tonic_hz", "mode_id", "sensitivity", "cycle_template_id", "tempo_bpm",
               "voice", "separate", "melody_stem", "trackers")
    return {k: getattr(options, k) for k in watched if getattr(options, k) != getattr(defaults, k)}


def _finish(ir: IR, run: _Run, analysis: Analysis) -> IR:
    """Attach warnings, the processing log and the analysis record, and make sure the IR is valid."""
    low = [e.id for e in ir.events if e.confidence < LOW_EVENT_CONFIDENCE]
    if low:
        shown = ", ".join(low[:12]) + ("..." if len(low) > 12 else "")
        run.warn("low_confidence_events",
                 f"{len(low)} of {len(ir.events)} notes are uncertain and are shown dimmed ({shown}).", "assemble")

    def with_meta(x: IR) -> IR:
        return x.model_copy(update={"meta": Meta(warnings=tuple(run.warnings), processing_log=tuple(run.logs),
                                                 analysis=analysis)})

    out = with_meta(ir)
    problems = validate_ir(out)
    if problems:
        # Never hand back an invalid IR: drop what broke the invariants and say so.
        run.warn("ir_repaired", "Part of the transcription was inconsistent and was left out: "
                 + " ".join(p.message for p in problems[:3]), "assemble")
        out = with_meta(ir.model_copy(update={"events": (), "contour": (), "cycle": Cycle(),
                                              "tempo": Tempo(free_time=True)}))
    return out


# ---------------------------------------------------------------------------
# The audio pipeline
# ---------------------------------------------------------------------------

def _analyze(audio, options: AnalysisOptions, run: _Run) -> IR:
    from .formats import registry
    from .ingest import preprocess as pp
    from .pitch import crepe as crepe_mod
    from .pitch.ensemble import track_pitch
    from .pitch.voicing import clean_voicing
    from .separate.demucs import model_name as demucs_model

    style_known = options.style != "auto"
    meta = registry.get(options.style).META if style_known else None  # UnknownFormat is the caller's error
    hop = int(config.section("pitch")["hop_samples"])

    # --- 7.2 preprocess ------------------------------------------------------
    pre = run.stage(
        "preprocess", {"highpass_hz": pp.HIGHPASS_HZ, "target_peak": pp.TARGET_PEAK, "pad_s": pp.PAD_S},
        lambda: pp.preprocess(audio.y22k, SR_ANALYSIS, clipping=audio.clipping_fraction).summary(),
        fallback=lambda: {"trim_offset_s": 0.0, "trim_end_s": float(len(audio.y22k) / SR_ANALYSIS), "gain": 1.0,
                          "highpass_hz": None, "polyphony": 0.0, "needs_separation": False, "warnings": []},
        pack=_json, unpack=_unjson)
    for code, message in pre.get("warnings", []):
        run.warn(code, message, "preprocess")
    offset = float(pre["trim_offset_s"])
    y22 = pp.apply_like(audio.y22k, SR_ANALYSIS, pre)
    y16 = pp.apply_like(audio.y16k, SR_PITCH, pre)

    # --- 7.3 / 7.4 separation and melody stem --------------------------------
    wanted = options.separate == "on" or (options.separate == "auto" and bool(pre.get("needs_separation")))
    sep_params = {"mode": options.separate, "wanted": wanted, "melody_stem": options.melody_stem,
                  "model": demucs_model() if wanted else None}
    if wanted:
        sep_arrays, sep = run.stage(
            "separate", sep_params, lambda: _separate(audio, pre, options),
            fallback=lambda: ({}, {"ran": False, "stem": None, "reason": "Stem separation failed."}),
            pack=lambda v: (v[0], v[1]), unpack=lambda arrays, data: (arrays, data))
        if sep["ran"]:
            run.annotate(f"melody stem: {sep['stem']}")
        else:
            run.warn("separation_unavailable",
                     (sep.get("reason") or "Stem separation was not available.")
                     + " Orin transcribes one melody line, so accompaniment can cause wrong notes.", "separate")
    else:
        sep_arrays, sep = {}, {"ran": False, "stem": None, "reason": None}
        run.skip("separate", sep_params,
                 "off: chosen by the user" if options.separate == "off" else "skipped: one clean melodic line")
    melody16 = sep_arrays.get("melody16", y16)
    rhythm_y = sep_arrays.get("drums22", y22)
    if "drums22" in sep_arrays and not np.any(np.abs(rhythm_y) > 1e-4):
        rhythm_y = y22  # a silent drum stem says nothing about the pulse
    shared = _Shared(y22, rhythm_y, offset)

    # --- 7.5 pitch -------------------------------------------------------------
    failures: list[str] = []

    def do_pitch() -> PitchTrack:
        track = track_pitch(melody16, voice=options.voice, trackers=options.trackers, failures=failures)
        track.t = track.t + offset  # back to the original file's time
        return track

    def no_pitch() -> PitchTrack:
        n = 1 + len(melody16) // hop
        return PitchTrack(t=np.arange(n) * (hop / SR_PITCH) + offset, f0=np.full(n, np.nan), conf=np.zeros(n),
                          hop_s=hop / SR_PITCH, tracker="none")

    raw = run.stage("pitch", {"voice": options.voice, "trackers": options.trackers,
                              "crepe_model": crepe_mod.model_name(), "hop_ms": 1000.0 * hop / SR_PITCH,
                              "source": sep.get("stem") or "mix"},
                    do_pitch, fallback=no_pitch, pack=_pack_track, unpack=_unpack_track)
    run.annotate(f"tracker: {raw.tracker}" + (f"; trackers disagreed on {raw.disagreements} frames"
                                               if raw.tracker == "ensemble" else ""))
    if failures:
        run.annotate("skipped " + "; ".join(failures)[:300])
    if (options.trackers == "crepe" and raw.tracker == "pyin") or (options.trackers == "both" and raw.tracker == "pyin"):
        run.warn("tracker_fallback", "The CREPE pitch tracker was not available, so pYIN was used instead.", "pitch")

    # --- 7.6 voicing -----------------------------------------------------------
    def do_voicing(threshold: float) -> Callable[[], PitchTrack]:
        def run_it() -> PitchTrack:
            cleaned = clean_voicing(raw, threshold)
            # On a full mix the tracker follows a drone whenever the melody pauses.
            return cleaned if sep["ran"] else _without_drone_frames(cleaned, audio.y22k)

        return run_it

    def plain_voicing(threshold: float) -> Callable[[], PitchTrack]:
        return lambda: replace(raw, f0=np.where(np.asarray(raw.conf) >= threshold, raw.f0, np.nan))

    profile = config.profile(meta.analysis_profile if meta else "neutral", options.sensitivity)
    track = run.stage("voicing", {"threshold": profile.voicing_threshold, "profile": profile.id},
                      do_voicing(profile.voicing_threshold), fallback=plain_voicing(profile.voicing_threshold),
                      pack=_pack_track, unpack=_unpack_track)
    voiced_fraction = float(np.mean(np.isfinite(track.f0))) if len(track.f0) else 0.0
    run.annotate(f"{round(100 * voiced_fraction)}% of frames voiced; {track.octave_fixes} frames folded back an octave")
    pitched = voiced_fraction >= MIN_VOICED_FRACTION
    if not pitched:
        run.warn("little_pitch", "Little pitched melody found. Orin transcribes a sung or played melody line; "
                                 "for this recording only the rhythm is reported.", "voicing")

    # --- 8 style auto-detect -----------------------------------------------------
    detected: dict = {}
    if style_known:
        run.skip("detect", {"style": meta.id}, "skipped: the style was chosen by the user")
    elif not pitched:
        meta = registry.list_meta()[0]
        run.skip("detect", {"style": "auto", "pitched": False}, "skipped: no pitched melody to judge a style from")
    else:
        first = registry.list_meta()[0].id
        detected = run.stage(
            "detect", {"style": "auto", "tonic_hz": options.tonic_hz, "sensitivity": options.sensitivity},
            lambda: _detect(track, audio.y22k, shared, options, audio.duration_s),
            fallback=lambda: {"style": first, "candidates": [], "needs_choice": True,
                              "message": "Not sure which style this is - please choose."},
            pack=_json, unpack=_unjson)
        meta = registry.get(detected["style"]).META
        if detected.get("needs_choice"):
            run.warn("style_uncertain", detected.get("message") or "Not sure which style this is - please choose.",
                     "detect")
        chosen = config.profile(meta.analysis_profile, options.sensitivity)
        if chosen.voicing_threshold != profile.voicing_threshold:
            track = run.stage("voicing", {"threshold": chosen.voicing_threshold, "profile": chosen.id},
                              do_voicing(chosen.voicing_threshold), fallback=plain_voicing(chosen.voicing_threshold),
                              pack=_pack_track, unpack=_unpack_track,
                              note="run again with the detected style's threshold")
        profile = chosen
    style = meta.id
    sure_of_style = style_known or not detected.get("needs_choice", True)

    # --- 7.7 tonic -----------------------------------------------------------------
    def assumed_tonic() -> tuple[Tonic, float]:
        from .tonic.select import _build

        if options.tonic_hz:
            return _build(float(options.tonic_hz), 1.0, "user", []), 0.0
        return _build(ASSUMED_TONIC_HZ, 0.0, "assumed", []), 0.0

    def do_tonic() -> tuple[Tonic, float]:
        from .tonic.select import detect_tonic

        return detect_tonic(track, audio=audio.y22k, sr=SR_ANALYSIS, style=style if sure_of_style else None,
                            tonic_hz=options.tonic_hz)

    tonic_params = {"style": style if sure_of_style else None, "tonic_hz": options.tonic_hz}
    if pitched:
        tonic, _drone = run.stage(
            "tonic", tonic_params, do_tonic, fallback=assumed_tonic,
            pack=lambda v: (None, {"tonic": v[0].model_dump(), "drone": v[1]}),
            unpack=lambda _a, d: (Tonic.model_validate(d["tonic"]), float(d["drone"])))
        if tonic.method != "user" and tonic.confidence < UNCERTAIN_TONIC:
            run.warn("tonic_uncertain",
                     f"The tonic ({tonic.nearest_note}, {tonic.hz:.1f} Hz) is a guess with "
                     f"{round(100 * tonic.confidence)}% confidence. Every note is measured from it, "
                     "so check it with 'Set Sa / tonic'.", "tonic")
    else:
        tonic, _drone = assumed_tonic()
        run.skip("tonic", tonic_params, "skipped: no pitched melody")

    # --- 7.8 cents, 7.9 segmentation, 7.10 ornaments ---------------------------------
    cents = track.cents(tonic.hz)
    seg_params = {"profile": profile.id, "sensitivity": options.sensitivity,
                  "min_note_ms": profile.min_note_ms, "plateau_enter_cents": profile.plateau_enter_cents}
    if pitched:
        def do_segment() -> list[Segment]:
            from .segment.notes import segment_contour

            env, env_t = shared.envelope()
            return segment_contour(track.t, cents, track.conf, profile, onset_env=env, onset_env_t=env_t)

        def do_ornament() -> list[Segment]:
            from .segment.ornaments import annotate

            return annotate(segments, track.t, cents, profile)

        segments = run.stage("segment", seg_params, do_segment,
                             fallback=lambda: _fallback_segments(cents, track.conf, profile, track.hop_s),
                             pack=_pack_segments, unpack=_unpack_segments)
        segments = run.stage("ornament", {"profile": profile.id}, do_ornament, fallback=lambda: list(segments),
                             pack=_pack_segments, unpack=_unpack_segments)
    else:
        segments = []
        run.skip("segment", seg_params, "skipped: no pitched melody")
        run.skip("ornament", {"profile": profile.id}, "skipped: no pitched melody")

    # --- 7.11 rhythm -------------------------------------------------------------------
    def do_rhythm() -> RhythmResult:
        from .rhythm import analyze as analyze_rhythm

        if segments:
            onsets, durations = _notes_of(segments, track.t, track.hop_s)
        else:  # rhythm only: energy onsets stand in for notes
            import librosa

            onsets = (librosa.onset.onset_detect(y=np.asarray(rhythm_y, dtype=np.float32), sr=SR_ANALYSIS,
                                                 hop_length=512, units="time") + offset).tolist()
            durations = np.diff(onsets + [onsets[-1] + 0.1]).tolist() if onsets else []
        beat_times, beat_conf = shared.beats()
        found = analyze_rhythm(onsets, durations, templates=_templates_for(meta), beat_times=beat_times,
                               beat_confidence=beat_conf, template_id=options.cycle_template_id,
                               tempo_bpm=options.tempo_bpm)
        if not segments:  # the pulse positions belong to onsets that are not events
            found = replace(found, onset_pulses=[], duration_pulses=[])
        return found

    rhythm = run.stage("rhythm", {"style": style, "cycle_template_id": options.cycle_template_id,
                                  "tempo_bpm": options.tempo_bpm,
                                  "pulse_source": "drums" if rhythm_y is not y22 else "mix"},
                       do_rhythm, fallback=lambda: _free_rhythm(len(segments)), pack=_pack_rhythm, unpack=_unpack_rhythm)

    # --- 7.12 mode ---------------------------------------------------------------------
    mode_params = {"style": style, "mode_id": options.mode_id, "all_formats": not sure_of_style}
    if segments:
        def do_mode() -> Mode:
            from .mode.infer import infer_mode

            _, durations = _notes_of(segments, track.t, track.hop_s)
            return infer_mode([s.anchor_cents for s in segments], durations,
                              [any(o.kind == "oscillation" for o in s.ornaments) for s in segments],
                              metas=[meta] if sure_of_style else registry.list_meta(), style=style if sure_of_style else None,
                              clip_duration_s=audio.duration_s, mode_id=options.mode_id)

        mode = run.stage("mode", mode_params, do_mode, fallback=Mode,
                         pack=lambda m: (None, m.model_dump()), unpack=lambda _a, d: Mode.model_validate(d))
    else:
        mode = Mode()
        run.skip("mode", mode_params, "skipped: no notes")

    # --- 7.13 assemble -----------------------------------------------------------------
    candidates = tuple(FormatCandidate.model_validate(c) for c in detected.get("candidates", []))
    source = Source(
        kind="audio", filename=audio.filename, format_hint=meta.id if style_known else None,
        detected_format=candidates[0].format_id if candidates else None,
        detected_format_confidence=candidates[0].score if candidates else None,
        detected_candidates=candidates, duration_s=float(audio.duration_s), sample_rate=int(audio.sample_rate),
        channels=int(audio.channels), loudness_dbfs=float(audio.loudness_dbfs), sha256=audio.sha256)
    bare = IR(source=source, tonic=tonic, tempo=Tempo(free_time=True))

    def do_assemble() -> IR:
        level = _frame_db(melody16, len(track.t), hop) if segments else None
        return _assemble(source=source, tonic=tonic, mode=mode, rhythm=rhythm, segments=segments,
                         track=track if segments else None, level_db=level)

    ir = run.stage("assemble", {"events": len(segments)}, do_assemble, fallback=lambda: bare)
    analysis = Analysis(style=style, profile_id=profile.id, sensitivity=float(options.sensitivity),
                        trackers=(raw.tracker,), user_overrides=_user_overrides(options))
    return _finish(ir, run, analysis)


def audio_to_ir(path: Union[str, Path], options: Optional[AnalysisOptions] = None, *,
                cache: Union[StageCache, str, Path, None] = None, progress: Optional[Progress] = None) -> IR:
    """Analyse an audio file. Raises ``IngestError`` for a file that cannot be used and
    ``UnknownFormat`` for an unknown style; everything after ingest fails soft."""
    from .ingest.audio import load_audio

    options = options or AnalysisOptions()
    store = as_cache(cache)
    run = _Run(store, progress)
    audio = run.stage("ingest", {"max_upload_mb": options.max_upload_mb, "max_seconds": options.max_seconds},
                      lambda: load_audio(path, options, cache_dir=store.audio_dir),
                      hit_of=lambda a: bool(a.from_cache))
    run.hash = audio.sha256
    run.chain = ""  # the limits do not change any later result
    return _analyze(audio, options, run)


# ---------------------------------------------------------------------------
# Notes input (spec 7.14)
# ---------------------------------------------------------------------------

def _event_notes(ir: IR) -> tuple[list[float], list[float], list[bool]]:
    return ([e.pitch_cents for e in ir.events], [e.offset_s - e.onset_s for e in ir.events],
            [e.ornament.kind == "oscillation" for e in ir.events])


def _infer_mode_for(ir: IR, style: Optional[str], mode_id: Optional[str]) -> Mode:
    from .formats import registry
    from .mode.infer import infer_mode

    known = bool(style) and registry.has(style)
    metas = [registry.get(style).META] if known else registry.list_meta()
    anchors, durations, oscillating = _event_notes(ir)
    return infer_mode(anchors, durations, oscillating, metas=metas, style=style if known else None,
                      clip_duration_s=ir.duration_s, mode_id=mode_id)


def notes_to_ir(data: Union[str, bytes], *, format_id: Optional[str] = None, syntax: Optional[str] = None,
                filename: Optional[str] = None, options: Optional[AnalysisOptions] = None) -> IR:
    """Typed or uploaded notes -> IR. Parsing is ``orin.ingest.notes``'s; this adds the
    mode when the notes did not declare one."""
    from .ingest.notes import parse_notes

    options = options or AnalysisOptions()
    if format_id is None and options.style != "auto":
        format_id = options.style
    ir = parse_notes(data, format_id=format_id, syntax=syntax, filename=filename, tonic_hz=options.tonic_hz,
                     mode_id=options.mode_id, tempo_bpm=options.tempo_bpm)
    if ir.events and ir.mode.label is None and not ir.mode.candidates:
        started = time.perf_counter()
        style = ir.source.format_hint or ir.meta.analysis.style or format_id
        try:
            mode = _infer_mode_for(ir, style, options.mode_id)
        except Exception as exc:
            warning = IRWarning(code="mode_failed", stage="mode",
                                message=f"Matching modes failed ({type(exc).__name__}), so no mode is suggested.")
            return ir.model_copy(update={"meta": ir.meta.model_copy(update={"warnings": (*ir.meta.warnings, warning)})})
        log = StageLog(stage="mode", version=PIPELINE_VERSION, params={"style": style, "mode_id": options.mode_id},
                       duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
                       note="inferred: the notes did not name a mode")
        ir = ir.model_copy(update={"mode": mode, "meta": ir.meta.model_copy(
            update={"processing_log": (*ir.meta.processing_log, log)})})
    return ir


# ---------------------------------------------------------------------------
# Re-analysis (spec 8.2): the user changed the tonic, mode, style...
# ---------------------------------------------------------------------------

def _apply_overrides(ir: IR, options: AnalysisOptions, *, reason: Optional[str] = None) -> IR:
    """Apply a tonic or mode choice straight onto an IR, without any audio.

    A new tonic on sargam input is a transposition: sargam is relative, so the notes keep
    their cents and only the reference moves. On anything else the sounding pitch is the
    fact, so the cents are re-measured from the new tonic.
    """
    from .tonic.select import nearest_note

    started = time.perf_counter()
    update: dict[str, Any] = {}
    overrides = dict(ir.meta.analysis.user_overrides)
    warnings = list(ir.meta.warnings)
    if reason:
        warnings.append(IRWarning(code="reanalyze_without_audio", stage="assemble", message=reason))
    out = ir
    retuned = False

    if options.tonic_hz and abs(1200.0 * np.log2(options.tonic_hz / ir.tonic.hz)) > 1e-6:
        name, offset = nearest_note(float(options.tonic_hz))
        tonic = ir.tonic.model_copy(update={
            "hz": float(options.tonic_hz), "cents_ref_hz": float(options.tonic_hz), "confidence": 1.0,
            "method": "user", "tuning_offset_cents": offset, "nearest_note": name})
        update["tonic"] = tonic
        overrides["tonic_hz"] = float(options.tonic_hz)
        if not (ir.source.kind == "notes" and ir.source.notes_syntax == "sargam"):
            shift = -1200.0 * float(np.log2(options.tonic_hz / ir.tonic.hz))
            update["events"] = tuple(e.model_copy(update={
                "pitch_cents": e.pitch_cents + shift, "pitch_cents_median": e.pitch_cents_median + shift,
                "pitch_cents_min": e.pitch_cents_min + shift, "pitch_cents_max": e.pitch_cents_max + shift,
            }) for e in ir.events)
            update["contour"] = tuple(p if p.cents is None else p.model_copy(update={"cents": p.cents + shift})
                                      for p in ir.contour)
            retuned = True
        out = ir.model_copy(update=update)

    # A mode the user names is their choice even when it is the one Orin had inferred.
    wants_mode = options.mode_id is not None and (options.mode_id != ir.mode.mode_id or not ir.mode.user_selected)
    if options.mode_id is not None:
        overrides["mode_id"] = options.mode_id
    if out.events and (wants_mode or retuned):
        style = options.style if options.style != "auto" else (ir.source.format_hint or ir.meta.analysis.style)
        mode_id = options.mode_id if options.mode_id is not None else (ir.mode.mode_id if ir.mode.user_selected else None)
        try:
            update["mode"] = _infer_mode_for(out, style, mode_id)
        except Exception as exc:
            warnings.append(IRWarning(code="mode_failed", stage="mode",
                                      message=f"Matching modes failed ({type(exc).__name__}); the earlier mode was kept."))

    log = StageLog(stage="reanalyze", version=PIPELINE_VERSION,
                   params={"tonic_hz": options.tonic_hz, "mode_id": options.mode_id},
                   duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
                   note="overrides applied to the existing notes")
    update["meta"] = ir.meta.model_copy(update={
        "warnings": tuple(warnings), "processing_log": (*ir.meta.processing_log, log),
        "analysis": ir.meta.analysis.model_copy(update={"user_overrides": overrides})})
    return ir.model_copy(update=update)


def reanalyze(ir_or_path: Union[IR, str, Path], options: AnalysisOptions, *,
              cache: Union[StageCache, str, Path, None] = None, progress: Optional[Progress] = None) -> IR:
    """Re-run after the user changed an option.

    Audio (a path, or an IR whose audio is still in the cache): the pipeline runs again
    and the stage cache answers every stage up to the first one the change affects, so a
    new tonic, mode, style, sensitivity, cycle or tempo never re-runs pitch tracking.
    A notes IR, or an audio IR whose audio is gone: tonic and mode overrides are applied
    to the existing notes.
    """
    options = options or AnalysisOptions()
    if not isinstance(ir_or_path, IR):
        return audio_to_ir(ir_or_path, options, cache=cache, progress=progress)

    ir = ir_or_path
    if ir.source.kind != "audio":
        return _apply_overrides(ir, options)

    from .ingest.audio import load_cached_audio

    store = as_cache(cache)
    audio = load_cached_audio(ir.source.sha256, store.audio_dir) if ir.source.sha256 else None
    if audio is None:
        return _apply_overrides(ir, options, reason=(
            "The audio is no longer stored, so the change was applied to the existing notes "
            "instead of re-analysing the recording. Upload the file again for a full re-analysis."))
    if audio.duration_s > options.max_seconds:
        raise IngestError(f"This recording is longer than the limit of {options.max_seconds / 60:.0f} minutes.",
                          code="too_long")
    audio.filename = ir.source.filename or audio.filename
    run = _Run(store, progress, audio.sha256)
    run.stage("ingest", {"from": "cache"}, lambda: audio, hit_of=lambda _a: True)
    run.chain = ""
    return _analyze(audio, options, run)
