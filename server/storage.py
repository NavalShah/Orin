"""File storage behind a small interface (spec 13.3), and signed asset links.

Blobs are content-addressed: the SHA-256 of the bytes is the only name a file ever gets
on disk. A client filename is display metadata and never reaches the filesystem, so
there is nothing to traverse (spec 13.4). Swap ``LocalStorage`` for S3/GCS later.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import uuid
from pathlib import Path
from typing import BinaryIO, Optional, Protocol

CHUNK = 1024 * 1024


class TooLarge(Exception):
    pass


class Storage(Protocol):
    def put_stream(self, stream: BinaryIO, max_bytes: Optional[int] = None) -> tuple[str, int]:
        """Store a stream; returns (sha256, size). Raises TooLarge past ``max_bytes``."""

    def put_bytes(self, data: bytes) -> tuple[str, int]: ...

    def path(self, sha256: str) -> Path:
        """A local path for the blob (a remote store would download to a cache here)."""

    def exists(self, sha256: str) -> bool: ...

    def delete(self, sha256: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.tmp = self.root / "tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)
        for stale in self.tmp.iterdir():  # half-written uploads from a previous process
            try:
                stale.unlink()
            except OSError:
                pass

    def path(self, sha256: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("not a content hash")
        return self.root / sha256[:2] / sha256

    def exists(self, sha256: str) -> bool:
        return self.path(sha256).is_file()

    def put_stream(self, stream: BinaryIO, max_bytes: Optional[int] = None) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        tmp = self.tmp / uuid.uuid4().hex  # server-chosen name
        try:
            with open(tmp, "wb") as out:
                while True:
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise TooLarge()
                    digest.update(chunk)
                    out.write(chunk)
            sha = digest.hexdigest()
            dest = self.path(sha)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dest)
            return sha, size
        finally:
            if tmp.exists():
                tmp.unlink()

    def put_bytes(self, data: bytes) -> tuple[str, int]:
        import io

        return self.put_stream(io.BytesIO(data))

    def delete(self, sha256: str) -> None:
        try:
            self.path(sha256).unlink()
        except FileNotFoundError:
            pass


class UrlSigner:
    """HMAC links so ``<audio src>`` and downloads work without an Authorization header.

    The signature covers the asset id and the expiry and nothing else; whoever holds a
    link can read that one file until it expires.
    """

    def __init__(self, secret: str, ttl_s: int, prefix: str):
        self._secret = secret.encode()
        self.ttl_s = ttl_s
        self.prefix = prefix

    def sign(self, asset_id: str, exp: int) -> str:
        return hmac.new(self._secret, f"{asset_id}:{exp}".encode(), hashlib.sha256).hexdigest()

    def url(self, asset_id: str, exp: Optional[int] = None) -> str:
        exp = int(time.time()) + self.ttl_s if exp is None else int(exp)
        return f"{self.prefix}/assets/{asset_id}/file?exp={exp}&sig={self.sign(asset_id, exp)}"

    def verify(self, asset_id: str, exp: object, sig: object) -> bool:
        try:
            exp_i = int(str(exp))
        except ValueError:
            return False
        if exp_i < time.time() or not isinstance(sig, str):
            return False
        return hmac.compare_digest(self.sign(asset_id, exp_i), sig)
