"""The Library: uploads, generated tracks, exported notation (spec 3.4, 13.3, 13.4)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Optional

from sqlalchemy import func, select

from .. import schemas
from ..db import AssetRow, dumps, loads, new_id, now_iso, owned
from ..deps import AppCtx
from ..errors import ApiError, not_found
from ..storage import TooLarge

MAX_NOTES_BYTES = 2 * 1024 * 1024  # typed or exported notation is small; anything bigger is not notation
UPLOAD_SOURCES = ("upload", "bounce", "conversion")
EXTENSIONS = {"wav": "wav", "mp3": "mp3", "flac": "flac", "ogg": "ogg", "m4a": "m4a", "midi": "mid",
              "musicxml": "musicxml", "mxl": "mxl", "abc": "abc", "text": "txt"}


@dataclass(frozen=True)
class Sniffed:
    kind: str  # audio | notes | unknown
    subtype: str
    media_type: str


def _fallback_sniff(data: bytes) -> Sniffed:
    """Magic bytes only. Used when orin.ingest.sniff cannot be imported."""
    head = data[:16]
    table = (
        (head[:4] == b"RIFF" and head[8:12] == b"WAVE", "audio", "wav", "audio/wav"),
        (head[:4] == b"fLaC", "audio", "flac", "audio/flac"),
        (head[:4] == b"OggS", "audio", "ogg", "audio/ogg"),
        (head[4:8] == b"ftyp", "audio", "m4a", "audio/mp4"),
        (head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0), "audio", "mp3", "audio/mpeg"),
        (head[:4] == b"MThd", "notes", "midi", "audio/midi"),
    )
    for hit, kind, subtype, media_type in table:
        if hit:
            return Sniffed(kind, subtype, media_type)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Sniffed("unknown", "unknown", "application/octet-stream")
    if not text.strip() or any(ord(c) < 32 and c not in "\t\n\r\f" for c in text):
        return Sniffed("unknown", "unknown", "application/octet-stream")
    if "<score-partwise" in text or "<score-timewise" in text:
        return Sniffed("notes", "musicxml", "application/vnd.recordare.musicxml+xml")
    return Sniffed("notes", "text", "text/plain")


def sniff(data: bytes, filename: Optional[str] = None) -> Sniffed:
    try:
        from orin.ingest.sniff import sniff as core_sniff
    except ImportError:
        return _fallback_sniff(data)
    found = core_sniff(data, filename)
    return Sniffed(found.kind, found.subtype, found.media_type)


def clean_filename(name: Optional[str], default: str = "upload") -> str:
    """A display name: the last path component, printable, bounded. Never used as a path."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*').strip().lstrip(".").strip()
    return name[:120] or default


def probe_duration(path: Path, kind: str) -> Optional[float]:
    """Seconds, or None when the file cannot be decoded here."""
    try:
        if kind == "midi":
            import mido

            return round(float(mido.MidiFile(str(path)).length), 3)
        import soundfile as sf

        return round(float(sf.info(str(path)).duration), 3)
    except Exception:
        pass
    if kind != "audio":
        return None
    try:
        import warnings

        import librosa

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return round(float(librosa.get_duration(path=str(path))), 3)
    except Exception:
        return None


def public_meta(row: AssetRow) -> dict[str, Any]:
    return {k: v for k, v in loads(row.meta_json, {}).items() if not k.startswith("_")}


def asset_out(ctx: AppCtx, row: AssetRow) -> schemas.Asset:
    return schemas.Asset(
        id=row.id, kind=row.kind, source=row.source, filename=row.filename, media_type=row.media_type,  # type: ignore[arg-type]
        size_bytes=row.size_bytes, sha256=row.sha256, duration_s=row.duration_s, meta=public_meta(row),
        created_at=row.created_at, url=ctx.signer.url(row.id), standin=bool(row.standin),
    )


def get_row(ctx: AppCtx, user_id: str, asset_id: str) -> AssetRow:
    with ctx.db.session() as s:
        row = owned(s, AssetRow, user_id, asset_id)
    if row is None:
        raise not_found("That file")
    return row


def find_out(ctx: AppCtx, user_id: str, asset_id: Optional[str]) -> Optional[schemas.Asset]:
    """The user's asset, or None if it is gone. For cards and conversions that point at one."""
    if not asset_id:
        return None
    with ctx.db.session() as s:
        row = owned(s, AssetRow, user_id, asset_id)
    return asset_out(ctx, row) if row else None


def find_by_ref(ctx: AppCtx, user_id: str, ref: str) -> Optional[AssetRow]:
    with ctx.db.session() as s:
        return s.execute(select(AssetRow).where(AssetRow.user_id == user_id, AssetRow.ref == ref)).scalars().first()


def list_assets(ctx: AppCtx, user_id: str, kind: Optional[str] = None, source: Optional[str] = None) -> list[schemas.Asset]:
    query = select(AssetRow).where(AssetRow.user_id == user_id)
    if kind:
        query = query.where(AssetRow.kind == kind)
    if source:
        query = query.where(AssetRow.source == source)
    with ctx.db.session() as s:
        rows = s.execute(query.order_by(AssetRow.created_at.desc()).limit(500)).scalars().all()
    return [asset_out(ctx, r) for r in rows]


def _blob_unused(ctx: AppCtx, sha256: str) -> bool:
    with ctx.db.session() as s:
        return not s.execute(select(func.count()).select_from(AssetRow).where(AssetRow.sha256 == sha256)).scalar_one()


def _register(ctx: AppCtx, user_id: str, sha: str, size: int, *, kind: str, source: str, filename: str,
              media_type: str, duration_s: Optional[float], meta: Optional[dict[str, Any]], standin: bool,
              ref: Optional[str]) -> AssetRow:
    """One row per (user, content): the same bytes uploaded twice are the same Library item."""
    with ctx.db.session() as s:
        row = s.execute(select(AssetRow).where(AssetRow.user_id == user_id, AssetRow.sha256 == sha)).scalars().first()
        if row is not None:
            if ref and not row.ref:
                row.ref = ref
            return row
        row = AssetRow(id=new_id(), user_id=user_id, kind=kind, source=source, filename=filename,
                       media_type=media_type, size_bytes=size, sha256=sha, duration_s=duration_s,
                       meta_json=dumps(meta or {}), standin=int(standin), ref=ref, created_at=now_iso())
        s.add(row)
        return row


def store_bytes(ctx: AppCtx, user_id: str, data: bytes, *, kind: str, source: str, filename: str, media_type: str,
                duration_s: Optional[float] = None, meta: Optional[dict[str, Any]] = None, standin: bool = False,
                ref: Optional[str] = None) -> AssetRow:
    """Store server-made bytes (renders, generated audio, narration) as an asset."""
    sha, size = ctx.storage.put_bytes(data)
    if duration_s is None and kind in ("audio", "midi"):
        duration_s = probe_duration(ctx.storage.path(sha), kind)
    return _register(ctx, user_id, sha, size, kind=kind, source=source, filename=clean_filename(filename, "orin"),
                     media_type=media_type, duration_s=duration_s, meta=meta, standin=standin, ref=ref)


def store_upload(ctx: AppCtx, user_id: str, stream: BinaryIO, filename: Optional[str], source: str) -> AssetRow:
    """Size-capped, content-sniffed upload (spec 13.4). The client filename is display text only."""
    settings = ctx.settings
    if source not in UPLOAD_SOURCES:
        raise ApiError(422, "validation_error", f"'source' must be one of: {', '.join(UPLOAD_SOURCES)}.")
    display = clean_filename(filename)
    try:
        sha, size = ctx.storage.put_stream(stream, max_bytes=settings.max_upload_bytes)
    except TooLarge:
        raise too_large(settings.max_upload_mb) from None
    path = ctx.storage.path(sha)

    def reject(error: ApiError) -> ApiError:
        if _blob_unused(ctx, sha):
            ctx.storage.delete(sha)
        return error

    if size == 0:
        raise reject(ApiError(400, "empty_file", "That file is empty.", hint="Choose a file with audio or notes in it."))
    with open(path, "rb") as f:
        head = f.read(MAX_NOTES_BYTES + 1)
    found = sniff(head if size <= MAX_NOTES_BYTES else head[:65536], display)
    if found.kind == "unknown":
        raise reject(ApiError(
            415, "unsupported_type", "Orin could not recognise that file.",
            hint="Use audio (WAV, MP3, M4A, FLAC, OGG) or notes (MIDI, MusicXML, ABC, or plain text).",
        ))
    if found.kind == "notes" and size > MAX_NOTES_BYTES:
        raise reject(ApiError(413, "too_large", "That notes file is larger than 2 MB.", hint="Split it into shorter pieces."))

    kind = "audio" if found.kind == "audio" else ("midi" if found.subtype == "midi" else "notation")
    meta: dict[str, Any] = {"subtype": found.subtype}
    duration = probe_duration(path, kind) if kind in ("audio", "midi") else None
    if kind == "audio":
        if duration is None:
            meta["note"] = "The server could not decode this file, so its length is unknown."
        elif duration > settings.max_audio_seconds:
            minutes = settings.max_audio_seconds / 60.0
            raise reject(ApiError(
                413, "too_long", f"That recording is {duration / 60.0:.1f} minutes long; the limit is {minutes:g} minutes.",
                hint="Trim it to the part you want to study.",
            ))
    return _register(ctx, user_id, sha, size, kind=kind, source=source, filename=display, media_type=found.media_type,
                     duration_s=duration, meta=meta, standin=False, ref=None)


def too_large(limit_mb: float) -> ApiError:
    return ApiError(413, "too_large", f"That file is larger than the {limit_mb:g} MB limit.",
                    hint="Trim or compress it, then try again.")


def delete_asset(ctx: AppCtx, user_id: str, asset_id: str) -> None:
    """Delete on request (spec 13.3). The blob goes too once no Library item uses it."""
    with ctx.db.session() as s:
        row = owned(s, AssetRow, user_id, asset_id)
        if row is None:
            raise not_found("That file")
        sha = row.sha256
        s.delete(row)
    if _blob_unused(ctx, sha):
        ctx.storage.delete(sha)


def sweep_stems(ctx: AppCtx) -> int:
    """Temp stems have a TTL (spec 13.3). Run at startup."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ctx.settings.stem_ttl_s)).isoformat(timespec="microseconds")
    with ctx.db.session() as s:
        rows = s.execute(select(AssetRow).where(AssetRow.kind == "stem", AssetRow.created_at < cutoff)).scalars().all()
        shas = {r.sha256 for r in rows}
        for row in rows:
            s.delete(row)
    for sha in shas:
        if _blob_unused(ctx, sha):
            ctx.storage.delete(sha)
    return len(rows)


# --- peaks --------------------------------------------------------------------

def compute_peaks(path: Path, buckets: int) -> tuple[float, list[float]]:
    """(duration in seconds, absolute peak per bucket in 0..1)."""
    import numpy as np

    try:
        import soundfile as sf

        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        mono = np.abs(data).max(axis=1) if data.size else np.zeros(0, dtype="float32")
    except Exception:
        import warnings

        import librosa

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            y, sr = librosa.load(str(path), sr=None, mono=True)
        mono = np.abs(y)
    n = int(mono.shape[0])
    if n == 0:
        return 0.0, [0.0] * buckets
    starts = np.minimum(np.linspace(0, n, buckets, endpoint=False).astype(np.int64), n - 1)
    peaks = np.maximum.reduceat(mono, starts)
    return n / float(sr), [round(float(p), 4) for p in np.clip(np.nan_to_num(peaks), 0.0, 1.0)]


def peaks(ctx: AppCtx, user_id: str, asset_id: str, buckets: int) -> schemas.Peaks:
    row = get_row(ctx, user_id, asset_id)
    if row.kind not in ("audio", "stem"):
        raise ApiError(400, "not_audio", "Only audio files have a waveform.")
    buckets = max(16, min(4000, int(buckets)))
    cached = loads(row.meta_json, {}).get("_peaks")
    if cached and cached.get("buckets") == buckets:
        return schemas.Peaks(**cached)
    if not ctx.storage.exists(row.sha256):
        raise not_found("That file")
    try:
        duration, values = compute_peaks(ctx.storage.path(row.sha256), buckets)
    except Exception:
        raise ApiError(400, "decode_failed", "Orin could not read the audio in that file.",
                       hint="Convert it to WAV and upload it again.") from None
    result = {"duration_s": round(duration, 3), "buckets": buckets, "peaks": values}
    with ctx.db.session() as s:  # cached in the asset's meta under a server-side key
        fresh = owned(s, AssetRow, user_id, asset_id)
        if fresh is not None:
            meta = loads(fresh.meta_json, {})
            meta["_peaks"] = result
            fresh.meta_json = dumps(meta)
            if fresh.duration_s is None and duration > 0:
                fresh.duration_s = round(duration, 3)
    return schemas.Peaks(**result)
