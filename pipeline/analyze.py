"""Audio -> tempo, note events (pitch class + octave), and tonic estimate.

Stages: load -> tuning -> tempo (onset/beat tracking) -> note activity
(CQT polyphonic, or pYIN monophonic) -> note segments -> key (Krumhansl-Schmuckler).
"""
from __future__ import annotations

import numpy as np
import librosa

SR = 22050
HOP = 512
MIDI_RANGE = 128
NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# CQT covers C2..B6 (5 octaves); MIDI 36 = C2.
CQT_MIDI_MIN = 36
CQT_BINS = 60

# Note segmentation
MIN_NOTE_FRAMES = 3      # ~70 ms at 22.05 kHz / hop 512
MERGE_GAP_FRAMES = 2     # join same-pitch runs separated by a short gap
CQT_THRESH_DB = -30.0    # relative to the loudest CQT bin in the file

# Krumhansl-Kessler key profiles, index 0 = tonic
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def midi_to_name(midi: int) -> tuple[str, int]:
    """MIDI number -> (pitch class name, octave) in scientific pitch notation (C4 = 60)."""
    return NAMES[midi % 12], midi // 12 - 1


def load_audio(path: str, sr: int = SR) -> tuple[np.ndarray, int]:
    y, sr = librosa.load(path, sr=sr, mono=True)
    peak = np.max(np.abs(y)) or 1.0
    return y / peak, sr


def estimate_tuning(y: np.ndarray, sr: int) -> float:
    """Deviation from A440 in semitones (positive = sharp)."""
    return float(librosa.estimate_tuning(y=y, sr=sr, bins_per_octave=12))


def estimate_tempo(y: np.ndarray, sr: int) -> dict:
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=env, sr=sr, hop_length=HOP, start_bpm=120, units='frames')
    bpm = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP)

    confidence = 0.0
    if len(beat_times) > 2:
        ibi = np.diff(beat_times)
        med = np.median(ibi)
        confidence = float(np.mean(np.abs(ibi - med) <= 0.1 * med))
        bpm = 60.0 / med  # tempo from the median inter-beat interval

    return {
        'bpm': round(bpm, 1),
        'confidence': round(confidence, 2),
        'alternatives': {'half': round(bpm / 2, 1), 'double': round(bpm * 2, 1)},
        'beat_times': np.round(beat_times, 3).tolist(),
    }


def cqt_activity(y: np.ndarray, sr: int, tuning: float) -> tuple[np.ndarray, np.ndarray]:
    """Polyphonic note activity: (active[128, T] bool, level_db[128, T])."""
    C = librosa.cqt(y, sr=sr, hop_length=HOP, fmin=librosa.midi_to_hz(CQT_MIDI_MIN),
                    n_bins=CQT_BINS, bins_per_octave=12, tuning=tuning)
    S = librosa.amplitude_to_db(np.abs(C), ref=np.max)  # 0 dB = loudest bin

    # Local peaks across frequency, above threshold
    padded = np.pad(S, ((1, 1), (0, 0)), constant_values=-120.0)
    peaks = (S >= padded[:-2]) & (S >= padded[2:]) & (S > CQT_THRESH_DB)

    # Harmonic suppression: drop a peak if a fundamental one octave/octave+fifth/two octaves
    # below is active at a comparable level (removes overtone false positives).
    active = peaks.copy()
    for off in (12, 19, 24):
        lower_active = np.zeros_like(peaks)
        lower_level = np.full_like(S, -120.0)
        lower_active[off:] = peaks[:-off]
        lower_level[off:] = S[:-off]
        active &= ~(lower_active & (lower_level >= S - 6))

    act = np.zeros((MIDI_RANGE, S.shape[1]), dtype=bool)
    level = np.full((MIDI_RANGE, S.shape[1]), -120.0)
    act[CQT_MIDI_MIN:CQT_MIDI_MIN + CQT_BINS] = active
    level[CQT_MIDI_MIN:CQT_MIDI_MIN + CQT_BINS] = S
    return act, level


def pyin_activity(y: np.ndarray, sr: int, tuning: float) -> tuple[np.ndarray, np.ndarray]:
    """Monophonic note activity from pYIN: one pitch per voiced frame."""
    f0, voiced, _ = librosa.pyin(
        y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C6'),
        sr=sr, frame_length=2048, hop_length=HOP)
    rms_db = librosa.amplitude_to_db(librosa.feature.rms(y=y, hop_length=HOP)[0], ref=1.0)
    T = min(len(f0), len(rms_db))

    act = np.zeros((MIDI_RANGE, T), dtype=bool)
    level = np.full((MIDI_RANGE, T), -120.0)
    frames = np.where(voiced[:T] & ~np.isnan(f0[:T]))[0]
    midi = np.round(librosa.hz_to_midi(f0[frames]) - tuning).astype(int)
    midi = np.clip(midi, 0, MIDI_RANGE - 1)
    act[midi, frames] = True
    level[midi, frames] = rms_db[frames]
    return act, level


def _runs(row: np.ndarray):
    """Yield (start, end_exclusive) for each run of True in a 1-D bool array."""
    padded = np.concatenate([[0], row.astype(np.int8), [0]])
    d = np.diff(padded)
    return zip(np.where(d == 1)[0], np.where(d == -1)[0])


def notes_from_activity(act: np.ndarray, level: np.ndarray, sr: int) -> list[dict]:
    """Turn per-frame activity into note events with onset/offset and velocity."""
    segments = []  # (midi, start_frame, end_frame, mean_level)
    for midi in range(MIDI_RANGE):
        if not act[midi].any():
            continue
        cur = None
        for s, e in _runs(act[midi]):
            if e - s < MIN_NOTE_FRAMES:
                continue
            mean_lvl = float(np.mean(level[midi, s:e]))
            if cur and s - cur[2] <= MERGE_GAP_FRAMES:
                cur = (midi, cur[1], e, max(cur[3], mean_lvl))
            else:
                if cur:
                    segments.append(cur)
                cur = (midi, s, e, mean_lvl)
        if cur:
            segments.append(cur)

    notes = []
    for midi, s, e, lvl in sorted(segments, key=lambda x: (x[1], x[0])):
        name, octave = midi_to_name(midi)
        notes.append({
            'onset': round(float(librosa.frames_to_time(s, sr=sr, hop_length=HOP)), 3),
            'offset': round(float(librosa.frames_to_time(e, sr=sr, hop_length=HOP)), 3),
            'midi': midi,
            'pitch_class': name,
            'octave': octave,
            # map -40 dB..0 dB (relative to the loudest content) onto MIDI velocity 20..127
            'velocity': int(np.clip(np.interp(lvl, [-40, 0], [20, 127]), 20, 127)),
        })
    return notes


def estimate_key(notes: list[dict]) -> dict | None:
    """Duration-weighted pitch-class histogram correlated with the 24 key profiles."""
    hist = np.zeros(12)
    for n in notes:
        hist[n['midi'] % 12] += n['offset'] - n['onset']
    if hist.sum() == 0:
        return None

    scores = []
    for tonic in range(12):
        for mode, profile in (('major', MAJOR_PROFILE), ('minor', MINOR_PROFILE)):
            r = np.corrcoef(hist, np.roll(profile, tonic))[0, 1]
            scores.append((r, tonic, mode))
    scores.sort(reverse=True)
    (best_r, tonic, mode), (second_r, _, _) = scores[0], scores[1]
    return {
        'tonic': NAMES[tonic],
        'mode': mode,
        'correlation': round(float(best_r), 3),
        'margin': round(float(best_r - second_r), 3),
    }


def analyze(path: str, backend: str = 'cqt', sr: int = SR) -> dict:
    if backend not in ('cqt', 'pyin'):
        raise ValueError(f"backend must be 'cqt' or 'pyin', got {backend!r}")

    y, sr = load_audio(path, sr)
    tuning = estimate_tuning(y, sr)
    tempo = estimate_tempo(y, sr)
    act, level = cqt_activity(y, sr, tuning) if backend == 'cqt' else pyin_activity(y, sr, tuning)
    notes = notes_from_activity(act, level, sr)

    return {
        'file': path,
        'backend': backend,
        'sample_rate': sr,
        'duration_s': round(len(y) / sr, 3),
        'tuning_semitones': round(tuning, 3),
        'tempo': tempo,
        'key': estimate_key(notes),
        'notes': notes,
    }
