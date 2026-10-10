"""Audio ingest and preprocessing (spec 7.1, 7.2), on synthesised signals."""
import hashlib
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from orin.errors import IngestError
from orin.eval import synth
from orin.ingest import preprocess as pp
from orin.ingest.audio import audio_subtype, clipping_fraction, load_audio, load_cached_audio
from orin.options import AnalysisOptions


def write_stereo(path, seconds=1.0, sr=44100):
    left = synth.tone(220.0, seconds, sr=sr, amp=0.4)
    right = synth.tone(330.0, seconds, sr=sr, amp=0.2)
    sf.write(str(path), np.stack([left, right], axis=1), sr, subtype="PCM_16")
    return path


def code_of(path, cache, **options):
    with pytest.raises(IngestError) as info:
        load_audio(path, AnalysisOptions(**options), cache_dir=cache)
    return info.value.code


def test_wrong_type_is_rejected(tmp_path):
    notes = tmp_path / "tune.wav"  # the extension lies; the content decides
    notes.write_text("S R2 G3 P D2\n")
    assert code_of(notes, tmp_path / "c") == "unsupported_type"
    binary = tmp_path / "picture.mp3"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(200))
    assert code_of(binary, tmp_path / "c") == "unsupported_type"


def test_too_large_is_rejected(tmp_path):
    wav = write_stereo(tmp_path / "a.wav")
    assert code_of(wav, tmp_path / "c", max_upload_mb=0.01) == "too_large"


def test_too_long_is_rejected(tmp_path):
    wav = write_stereo(tmp_path / "a.wav", seconds=1.0)
    assert code_of(wav, tmp_path / "c", max_seconds=0.5) == "too_long"
    # ...also when the copies are already cached under a laxer limit
    load_audio(wav, cache_dir=tmp_path / "c")
    assert code_of(wav, tmp_path / "c", max_seconds=0.5) == "too_long"


def test_empty_and_undecodable_files(tmp_path):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    assert code_of(empty, tmp_path / "c") == "empty"
    silent = tmp_path / "silent.wav"
    sf.write(str(silent), np.zeros(8000), 16000, subtype="PCM_16")
    assert code_of(silent, tmp_path / "c") == "empty"
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + b"\x07" * 300)
    assert code_of(broken, tmp_path / "c") == "decode_failed"
    assert code_of(tmp_path / "missing.wav", tmp_path / "c") == "decode_failed"


def test_wav_round_trip_reports_the_file(tmp_path):
    wav = write_stereo(tmp_path / "a.wav", seconds=1.0, sr=44100)
    audio = load_audio(wav, cache_dir=tmp_path / "c")
    assert audio.sample_rate == 44100
    assert audio.channels == 2
    assert audio.duration_s == pytest.approx(1.0, abs=1e-3)
    assert audio.sha256 == hashlib.sha256(wav.read_bytes()).hexdigest()
    assert audio.subtype == "wav" and audio.filename == "a.wav"
    assert audio.y44k.shape == (2, 44100)
    assert len(audio.y22k) == 22050 and len(audio.y16k) == 16000
    # the stereo copy keeps the channels apart; the analysis copies are the mono mix
    assert np.std(audio.y44k[0]) > 1.5 * np.std(audio.y44k[1])
    mono = sf.read(str(wav))[0].mean(axis=1)
    assert audio.loudness_dbfs == pytest.approx(20 * np.log10(np.sqrt(np.mean(mono ** 2))), abs=0.1)
    assert audio.clipping_fraction == 0.0


def test_resampled_copies_are_cached_by_content_hash(tmp_path):
    wav = write_stereo(tmp_path / "a.wav")
    cache = tmp_path / "c"
    first = load_audio(wav, cache_dir=cache)
    assert first.from_cache is False
    stored = sorted(p.name for p in (cache / first.sha256).iterdir())
    assert stored == ["meta.json", "mono_16000.npy", "mono_22050.npy", "stereo_44100.npy"]

    copy = tmp_path / "renamed.wav"  # same bytes under another name: same hash, same cache entry
    shutil.copy(wav, copy)
    second = load_audio(copy, cache_dir=cache)
    assert second.from_cache is True and second.filename == "renamed.wav"
    assert second.sample_rate == first.sample_rate and second.duration_s == first.duration_s
    for name in ("y44k", "y22k", "y16k"):
        np.testing.assert_array_equal(getattr(first, name), getattr(second, name))
    by_hash = load_cached_audio(first.sha256, cache)
    assert by_hash is not None and len(by_hash.y16k) == len(first.y16k)
    assert load_cached_audio("0" * 64, cache) is None


def test_mono_low_rate_file_is_resampled_to_all_three_rates(tmp_path):
    wav = tmp_path / "m.wav"
    sf.write(str(wav), synth.tone(220.0, 0.5, sr=8000), 8000, subtype="PCM_16")
    audio = load_audio(wav, cache_dir=tmp_path / "c")
    assert (audio.sample_rate, audio.channels) == (8000, 1)
    assert audio.y44k.shape == (2, 22050) and len(audio.y22k) == 11025 and len(audio.y16k) == 8000
    spectrum = np.abs(np.fft.rfft(audio.y16k))
    assert abs(np.argmax(spectrum) * 16000 / len(audio.y16k) - 220.0) < 3.0  # resampling kept the pitch


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_m4a_decodes_through_ffmpeg(tmp_path):
    wav = write_stereo(tmp_path / "a.wav")
    m4a = tmp_path / "a.m4a"
    made = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(wav), "-c:a", "aac", str(m4a)],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("this ffmpeg cannot write AAC")
    audio = load_audio(m4a, cache_dir=tmp_path / "c")
    assert audio.subtype == "m4a" and audio.decoder == "ffmpeg"
    assert audio.channels == 2 and audio.duration_s == pytest.approx(1.0, abs=0.1)
    assert not list((tmp_path / "c" / audio.sha256).glob("decoded*"))  # the scratch WAV is not left behind


def test_own_sniff_matches_magic_bytes():
    assert audio_subtype(b"RIFF\x24\x00\x00\x00WAVEfmt ") == "wav"
    assert audio_subtype(b"fLaC\x00\x00\x00\x22") == "flac"
    assert audio_subtype(b"X: 1\nK: C\nCDEF|\n") is None


# --- preprocess -------------------------------------------------------------------

def test_trim_returns_the_offset_in_the_original_file():
    sr = 22050
    y = synth.render(synth.concat(synth.silence(0.5), synth.const(0, 1.0), synth.silence(0.7)), 220.0, sr=sr)
    pre = pp.preprocess(y, sr)
    assert pre.trim_offset_s == pytest.approx(0.4, abs=0.05)  # 0.5 s of silence minus the 100 ms pad
    assert pre.trim_end_s == pytest.approx(1.6, abs=0.05)
    assert len(pre.y) / sr == pytest.approx(pre.trim_end_s - pre.trim_offset_s, abs=1e-3)
    # another copy of the recording gets the same cut, so both share one time origin
    y16 = synth.render(synth.concat(synth.silence(0.5), synth.const(0, 1.0), synth.silence(0.7)), 220.0, sr=16000)
    assert len(pp.apply_like(y16, 16000, pre)) / 16000 == pytest.approx(len(pre.y) / sr, abs=1e-3)


def test_normalise_reaches_the_target_without_clipping():
    quiet = synth.tone(220.0, 1.0, amp=0.02)
    pre = pp.preprocess(quiet, 22050)
    assert np.max(np.abs(pre.y)) == pytest.approx(pp.TARGET_PEAK, abs=1e-3)
    assert pre.gain > 10
    loud = synth.tone(220.0, 1.0, amp=0.99)
    assert np.max(np.abs(pp.preprocess(loud, 22050).y)) <= pp.TARGET_PEAK + 1e-6
    silent, gain = pp.normalise(np.zeros(100, dtype=np.float32))
    assert gain == 1.0 and not silent.any()


def test_highpass_removes_rumble_and_keeps_the_note():
    sr = 22050
    t = np.arange(sr * 2) / sr
    y = (0.5 * np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 12 * t)).astype(np.float32)
    out = pp.highpass(y, sr, 50.0)
    spectrum = np.abs(np.fft.rfft(out))
    freqs = np.fft.rfftfreq(len(out), 1 / sr)
    assert spectrum[np.argmin(np.abs(freqs - 12))] < 0.1 * spectrum[np.argmin(np.abs(freqs - 220))]


def test_clipping_is_measured_on_decoded_samples_and_warned_about():
    clean = synth.tone(220.0, 1.0, amp=0.9)
    clipped = np.clip(clean * 4, -1, 1)
    assert clipping_fraction(clean) == 0.0
    assert clipping_fraction(clipped) > 0.2
    codes = [code for code, _ in pp.preprocess(clipped, 22050, clipping=clipping_fraction(clipped)).warnings]
    assert "clipping" in codes
    assert pp.preprocess(clean, 22050, clipping=0.0).warnings == []


def test_low_snr_is_warned_about():
    noise = synth.noise(2.0, amp=0.05)
    assert "low_snr" in [code for code, _ in pp.preprocess(noise, 22050).warnings]
    phrase = synth.render(synth.phrase([0, 200, 400], 0.4, gap_s=0.2), 220.0)
    buried = synth.mix(phrase * 0.05, synth.noise(len(phrase) / 22050, amp=0.03))
    assert "low_snr" in [code for code, _ in pp.preprocess(buried, 22050).warnings]
    assert pp.preprocess(phrase, 22050).warnings == []


def test_polyphony_score_decides_whether_separation_can_be_skipped():
    phrase = synth.render(synth.phrase([0, 200, 400, 500, 700], 0.3), 220.0)
    solo = pp.preprocess(phrase, 22050)
    assert solo.polyphony < 0.15 and solo.needs_separation is False
    # a quiet drone under the melody is still one melodic line: it is a tonic cue, not accompaniment
    with_drone = pp.preprocess(synth.mix(phrase, synth.drone([110.0, 165.0], len(phrase) / 22050)), 22050)
    assert with_drone.needs_separation is False
    band = synth.mix(phrase, synth.noise(len(phrase) / 22050, amp=0.4),
                     synth.clicks(np.arange(0, 1.5, 0.25), len(phrase) / 22050))
    busy = pp.preprocess(band, 22050)
    assert busy.polyphony > 0.4 and busy.needs_separation is True
