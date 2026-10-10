"""Audio ingest (spec 7.1): sniff, enforce limits, decode, hash, resample and cache.

Three copies are kept, each cached on disk by the file's SHA-256:
  y44k  44.1 kHz stereo, shape (2, n)   playback and stem separation
  y22k  22.05 kHz mono                  rhythm, drone and tonic analysis
  y16k  16 kHz mono                     pitch tracking (CREPE-family models and pYIN)

Decoding uses soundfile and falls back to an ffmpeg subprocess (m4a and anything
libsndfile cannot read). ``torchaudio.load`` is never used.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Optional, Union

import numpy as np

from ..cache import default_root
from ..errors import IngestError
from ..options import AnalysisOptions

SR_PLAYBACK = 44100
SR_ANALYSIS = 22050
SR_PITCH = 16000
ACCEPTED = ("wav", "mp3", "m4a", "flac", "ogg")
_COPIES = {"y44k": "stereo_44100.npy", "y22k": "mono_22050.npy", "y16k": "mono_16000.npy"}
_META = "meta.json"
_FFMPEG_TIMEOUT_S = 180


@dataclass
class AudioInput:
    sha256: str
    filename: Optional[str]
    subtype: str  # wav | mp3 | m4a | flac | ogg
    sample_rate: int  # of the file as decoded
    channels: int
    duration_s: float
    loudness_dbfs: float  # RMS of the mono mix
    clipping_fraction: float  # share of samples sitting on a flat top
    y44k: np.ndarray
    y22k: np.ndarray
    y16k: np.ndarray
    from_cache: bool = False
    decoder: str = "soundfile"
    path: Optional[str] = None


# ---------------------------------------------------------------------------
# Sniffing and limits
# ---------------------------------------------------------------------------

def _own_sniff(head: bytes) -> Optional[str]:
    """Minimal magic-byte check, used only when orin.ingest.sniff cannot be imported."""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    return None


def audio_subtype(head: bytes, filename: Optional[str] = None) -> Optional[str]:
    """The audio container found in ``head`` (the first bytes of a file), or None."""
    try:
        from .sniff import sniff
    except ImportError:
        return _own_sniff(head)
    found = sniff(head, filename)
    return found.subtype if found.kind == "audio" else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _too_long(duration_s: float, max_seconds: float) -> IngestError:
    return IngestError(
        f"This recording is {duration_s / 60:.1f} minutes long; the limit is {max_seconds / 60:.0f} minutes.",
        code="too_long", hint="Trim it to a shorter excerpt and try again.")


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def _decode_soundfile(path: Path, max_seconds: float) -> tuple[np.ndarray, int]:
    import soundfile as sf

    info = sf.info(str(path))
    if info.samplerate > 0 and info.frames / info.samplerate > max_seconds:
        raise _too_long(info.frames / info.samplerate, max_seconds)
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data, int(sr)


def _decode_ffmpeg(path: Path, work_dir: Path, max_seconds: float) -> tuple[np.ndarray, int]:
    import soundfile as sf

    exe = shutil.which("ffmpeg")
    if exe is None:
        raise IngestError("This file needs ffmpeg to be decoded, and ffmpeg is not installed.",
                          code="decode_failed", hint="Convert the file to WAV and try again.")
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / f"decoded.{uuid.uuid4().hex[:12]}.wav"
    # One second more than the limit is decoded, so an over-long file is noticed without reading all of it.
    cmd = [exe, "-nostdin", "-v", "error", "-y", "-i", str(path), "-t", f"{max_seconds + 1.0:.3f}",
           "-vn", "-c:a", "pcm_f32le", str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT_S)
        if proc.returncode != 0 or not out.exists():
            detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
            raise IngestError(f"The audio could not be decoded ({detail[0][:200]}).", code="decode_failed",
                              hint="Check that the file plays, or convert it to WAV.")
        data, sr = sf.read(str(out), dtype="float32", always_2d=True)
    except subprocess.TimeoutExpired as exc:
        raise IngestError("Decoding the audio took too long.", code="decode_failed", retryable=True) from exc
    finally:
        out.unlink(missing_ok=True)
    return data, int(sr)


def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Polyphase resampling along the last axis."""
    if sr_in == sr_out or x.shape[-1] == 0:
        return x.astype(np.float32, copy=False)
    from scipy.signal import resample_poly

    ratio = Fraction(sr_out, sr_in)
    return resample_poly(x.astype(np.float64), ratio.numerator, ratio.denominator, axis=-1).astype(np.float32)


def clipping_fraction(samples: np.ndarray) -> float:
    """Share of samples in flat runs (four or more) at the peak level. Must be measured on
    the decoded samples: resampling turns a flat top into ripple."""
    x = np.abs(np.asarray(samples, dtype=np.float32)).reshape(-1)
    if x.size < 8:
        return 0.0
    peak = float(x.max())
    if peak < 0.5:
        return 0.0
    top = x >= peak * 0.9999
    run = top[:-3] & top[1:-2] & top[2:-1] & top[3:]
    return float(run.sum() * 4.0 / x.size) if run.any() else 0.0


def _dbfs(y: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(y, dtype=np.float64)))) if y.size else 0.0
    return float(max(-120.0, 20.0 * np.log10(max(rms, 1e-6))))


# ---------------------------------------------------------------------------
# Cache of the resampled copies
# ---------------------------------------------------------------------------

def _copies_dir(cache_dir: Union[str, Path, None], sha256: str) -> Path:
    root = Path(cache_dir) if cache_dir is not None else default_root() / "audio"
    return root / sha256


def load_cached_audio(sha256: str, cache_dir: Union[str, Path, None] = None) -> Optional[AudioInput]:
    """The resampled copies of an already-ingested file, by content hash, or None.

    This is how ``reanalyze`` works from an IR alone, with the upload long gone."""
    d = _copies_dir(cache_dir, sha256)
    try:
        with (d / _META).open("r", encoding="utf-8") as f:
            meta = json.load(f)
        arrays = {name: np.load(d / fname, allow_pickle=False) for name, fname in _COPIES.items()}
    except Exception:
        return None
    return AudioInput(
        sha256=sha256, filename=meta.get("filename"), subtype=meta.get("subtype", "wav"),
        sample_rate=int(meta["sample_rate"]), channels=int(meta["channels"]),
        duration_s=float(meta["duration_s"]), loudness_dbfs=float(meta["loudness_dbfs"]),
        clipping_fraction=float(meta.get("clipping_fraction", 0.0)), from_cache=True,
        decoder=meta.get("decoder", "soundfile"), **arrays)


def _store(d: Path, audio: AudioInput) -> None:
    try:
        d.mkdir(parents=True, exist_ok=True)
        unique = uuid.uuid4().hex[:12]  # two uploads of the same file may land here at once
        for name, fname in _COPIES.items():
            tmp = d / f"{fname}.{unique}.tmp.npy"
            np.save(tmp, getattr(audio, name))
            os.replace(tmp, d / fname)
        tmp = d / f"{_META}.{unique}.tmp"
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({"filename": audio.filename, "subtype": audio.subtype, "sample_rate": audio.sample_rate,
                       "channels": audio.channels, "duration_s": audio.duration_s,
                       "loudness_dbfs": audio.loudness_dbfs, "clipping_fraction": audio.clipping_fraction,
                       "decoder": audio.decoder}, f)
        os.replace(tmp, d / _META)  # written last: it marks the entry complete
    except Exception:
        return  # a cache that cannot be written must not fail the upload


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def load_audio(path: Union[str, Path], options: Optional[AnalysisOptions] = None, *,
               cache_dir: Union[str, Path, None] = None, filename: Optional[str] = None) -> AudioInput:
    """Validate and decode an audio file. Raises ``IngestError`` with one of the codes
    unsupported_type, too_large, too_long, decode_failed, empty; never anything else
    for a bad file."""
    options = options or AnalysisOptions()
    p = Path(path)
    name = filename or p.name
    if not p.is_file():
        raise IngestError(f"The file '{name}' could not be found.", code="decode_failed")

    size = p.stat().st_size
    if size == 0:
        raise IngestError("The file is empty.", code="empty", hint="Choose a recording with sound in it.")
    if size > options.max_upload_mb * 1024 * 1024:
        raise IngestError(
            f"The file is {size / 1024 / 1024:.1f} MB; the limit is {options.max_upload_mb:g} MB.",
            code="too_large", hint="Use a shorter excerpt or a compressed format such as MP3.")

    with p.open("rb") as f:
        head = f.read(65536)
    subtype = audio_subtype(head, name)
    if subtype is None:
        raise IngestError(
            f"'{name}' is not an audio file Orin can read.", code="unsupported_type",
            hint=f"Accepted audio: {', '.join(ACCEPTED)}. Typed notes go in the notes box instead.")

    sha = _sha256(p)
    d = _copies_dir(cache_dir, sha)
    cached = load_cached_audio(sha, cache_dir)
    if cached is not None:
        if cached.duration_s > options.max_seconds:
            raise _too_long(cached.duration_s, options.max_seconds)
        cached.filename, cached.path = name, str(p)
        return cached

    decoder = "soundfile"
    try:
        data, sr = _decode_soundfile(p, options.max_seconds)
    except IngestError:
        raise
    except Exception:
        decoder = "ffmpeg"
        try:
            data, sr = _decode_ffmpeg(p, d, options.max_seconds)
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError(f"The audio could not be decoded ({type(exc).__name__}).", code="decode_failed",
                              hint="Check that the file plays, or convert it to WAV.") from exc

    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    if data.shape[0] == 0 or sr <= 0:
        raise IngestError("The file holds no audio.", code="empty", hint="Choose a recording with sound in it.")
    duration = data.shape[0] / sr
    if duration > options.max_seconds:
        raise _too_long(duration, options.max_seconds)
    if not np.any(data):
        raise IngestError("The recording is completely silent.", code="empty",
                          hint="Check the microphone or choose another file.")

    channels = int(data.shape[1])
    mono = data.mean(axis=1)
    stereo = data[:, :2].T if channels >= 2 else np.stack([data[:, 0], data[:, 0]])
    audio = AudioInput(
        sha256=sha, filename=name, subtype=subtype, sample_rate=sr, channels=channels,
        duration_s=float(duration), loudness_dbfs=_dbfs(mono), clipping_fraction=clipping_fraction(data),
        y44k=np.ascontiguousarray(_resample(stereo, sr, SR_PLAYBACK)),
        y22k=_resample(mono, sr, SR_ANALYSIS), y16k=_resample(mono, sr, SR_PITCH),
        from_cache=False, decoder=decoder, path=str(p))
    _store(d, audio)
    return audio
