"""Synthetic-signal tests for the audio pipeline. No audio fixtures needed."""
import os
import sys
import tempfile

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pipeline.analyze import (  # noqa: E402
    HOP, SR, analyze, cqt_activity, estimate_key, estimate_tempo, estimate_tuning,
    midi_to_name, notes_from_activity, pyin_activity,
)


def tone(freq, dur, sr=SR, amp=0.6):
    t = np.arange(int(dur * sr)) / sr
    env = np.minimum(1, np.minimum(t, dur - t) / 0.01)  # 10 ms fades avoid clicks
    return amp * env * np.sin(2 * np.pi * freq * t)


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def test_midi_to_name():
    assert midi_to_name(60) == ('C', 4)
    assert midi_to_name(69) == ('A', 4)
    assert midi_to_name(21) == ('A', 0)


def test_sine_440_pyin_gives_a4():
    y = tone(440, 2.0)
    act, level = pyin_activity(y, SR, tuning=0.0)
    notes = notes_from_activity(act, level, SR)
    assert notes, 'no notes detected'
    midis = [n['midi'] for n in notes]
    assert max(set(midis), key=midis.count) == 69
    assert notes[0]['pitch_class'] == 'A' and notes[0]['octave'] == 4


def test_sine_440_cqt_gives_a4():
    y = tone(440, 2.0)
    act, level = cqt_activity(y, SR, tuning=0.0)
    notes = notes_from_activity(act, level, SR)
    assert any(n['midi'] == 69 for n in notes)


def test_tuning_detects_sharp_a():
    # A at 442 Hz is ~ +0.08 semitones sharp; estimate should be positive
    y = tone(442, 3.0)
    assert estimate_tuning(y, SR) > 0.03


def test_tempo_from_clicks_at_120_bpm():
    beat = 0.5  # 120 BPM
    dur = 10.0
    y = np.zeros(int(dur * SR))
    click = tone(1000, 0.03)
    for t in np.arange(0.0, dur - 0.1, beat):
        i = int(t * SR)
        y[i:i + len(click)] += click
    result = estimate_tempo(y, SR)
    assert abs(result['bpm'] - 120) <= 2, result


def test_c_major_scale_key_is_c_major():
    # C major scale, quarter notes at 0.5 s each, ascending and descending
    seq = [60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65, 64, 62, 60]
    y = np.concatenate([tone(midi_hz(m), 0.5) for m in seq])
    act, level = cqt_activity(y, SR, tuning=0.0)
    notes = notes_from_activity(act, level, SR)
    key = estimate_key(notes)
    assert key['tonic'] == 'C' and key['mode'] == 'major', key


def test_key_from_histogram_a_minor():
    # Notes weighted toward A minor's tonic and dominant (A, E) with C and F present
    pcs = [9, 9, 4, 4, 0, 5, 7, 9, 11, 2]
    notes = [{'midi': 60 + p, 'onset': i * 0.5, 'offset': i * 0.5 + 0.5} for i, p in enumerate(pcs)]
    key = estimate_key(notes)
    assert key['tonic'] == 'A' and key['mode'] == 'minor', key


def test_estimate_key_empty_returns_none():
    assert estimate_key([]) is None


def test_analyze_wav_round_trip(tmp_path):
    path = tmp_path / 'a440.wav'
    sf.write(path, tone(440, 2.0), SR)
    result = analyze(str(path), backend='pyin')
    assert result['sample_rate'] == SR
    assert result['tempo']['bpm'] > 0
    assert any(n['midi'] == 69 for n in result['notes'])


def test_invalid_backend_rejected(tmp_path):
    path = tmp_path / 'x.wav'
    sf.write(path, tone(440, 0.5), SR)
    with pytest.raises(ValueError):
        analyze(str(path), backend='nope')
