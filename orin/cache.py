"""On-disk stage cache (spec 7.16): results keyed by (content hash, stage, params hash).

Arrays are stored as ``.npz`` and everything else as ``.json``::

    <root>/stages/<hash[:2]>/<hash>/<stage>-<params hash>.npz
    <root>/stages/<hash[:2]>/<hash>/<stage>-<params hash>.json
    <root>/audio/<hash>/...            resampled copies, written by orin.ingest.audio

The default root is a temp directory; the server passes ``ORIN_DATA_DIR/cache``.
A damaged or half-written entry is a miss, never an error.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np


def default_root() -> Path:
    return Path(tempfile.gettempdir()) / "orin-cache"


def params_hash(params: Any) -> str:
    """A short, stable hash of JSON-like parameters."""
    text = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class CacheEntry:
    arrays: dict[str, np.ndarray] = field(default_factory=dict)
    data: Any = None


class StageCache:
    def __init__(self, root: Union[str, Path, None] = None):
        self.root = Path(root) if root is not None else default_root()
        self.hits = 0
        self.misses = 0

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    def _base(self, content_hash: str, stage: str, params: Any) -> Path:
        h = content_hash or "nohash"
        return self.root / "stages" / h[:2] / h / f"{stage}-{params_hash(params)}"

    def get(self, content_hash: str, stage: str, params: Any) -> Optional[CacheEntry]:
        base = self._base(content_hash, stage, params)
        json_path, npz_path = base.with_suffix(".json"), base.with_suffix(".npz")
        if not json_path.exists():
            self.misses += 1
            return None
        try:
            with json_path.open("r", encoding="utf-8") as f:
                doc = json.load(f)
            arrays: dict[str, np.ndarray] = {}
            if doc.get("has_arrays"):
                with np.load(npz_path, allow_pickle=False) as z:
                    arrays = {k: z[k] for k in z.files}
        except Exception:
            self.misses += 1
            return None
        self.hits += 1
        return CacheEntry(arrays=arrays, data=doc.get("data"))

    def put(self, content_hash: str, stage: str, params: Any, *,
            arrays: Optional[dict[str, np.ndarray]] = None, data: Any = None) -> None:
        """Best effort: a full disk or a read-only directory must not fail the analysis."""
        base = self._base(content_hash, stage, params)
        try:
            base.parent.mkdir(parents=True, exist_ok=True)
            unique = uuid.uuid4().hex[:12]  # two jobs may write the same entry at once
            if arrays:
                tmp = base.with_suffix(f".{unique}.tmp.npz")
                np.savez(tmp, **{k: np.asarray(v) for k, v in arrays.items()})
                os.replace(tmp, base.with_suffix(".npz"))
            tmp = base.with_suffix(f".{unique}.tmp.json")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump({"stage": stage, "has_arrays": bool(arrays), "data": data}, f)
            os.replace(tmp, base.with_suffix(".json"))  # the .json is written last: it marks the entry complete
        except Exception:
            return


def as_cache(cache: Union["StageCache", str, Path, None]) -> StageCache:
    if isinstance(cache, StageCache):
        return cache
    return StageCache(cache)
