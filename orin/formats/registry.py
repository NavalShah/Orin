"""The format registry (spec section 4).

Adding a format = adding one adapter file to ``orin/formats/``. Any module there that
does not start with "_" and defines ``ADAPTER`` is picked up; nothing else changes.
"""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from difflib import get_close_matches
from pathlib import Path
from typing import Iterable, Optional

from ..errors import UnknownFormat
from .base import FAMILY_LABELS, Adapter, FormatMeta, ModeEntry

_SKIP = {"base", "registry", "engine"}
_adapters: Optional[dict[str, Adapter]] = None


def _load_from_package() -> dict[str, Adapter]:
    import orin.formats as pkg

    found: dict[str, Adapter] = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_") or info.name in _SKIP:
            continue
        module = importlib.import_module(f"orin.formats.{info.name}")
        adapter = getattr(module, "ADAPTER", None)
        if isinstance(adapter, Adapter):
            found[adapter.id] = adapter
    return found


def discover(extra_paths: Iterable[str | Path] = ()) -> dict[str, Adapter]:
    """(Re)build the registry. ``extra_paths`` are adapter files outside the package."""
    global _adapters
    found = _load_from_package()
    for path in extra_paths:
        path = Path(path)
        spec = importlib.util.spec_from_file_location(f"orin_ext_format_{path.stem}", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        adapter = getattr(module, "ADAPTER", None)
        if isinstance(adapter, Adapter):
            found[adapter.id] = adapter
    _adapters = dict(sorted(found.items(), key=lambda kv: (kv[1].META.order, kv[0])))
    _lookalike_cache.clear()
    return _adapters


def all_formats() -> dict[str, Adapter]:
    if _adapters is None:
        discover()
    assert _adapters is not None
    return _adapters


def get(format_id: str) -> Adapter:
    formats = all_formats()
    key = (format_id or "").strip().lower()
    if key in formats:
        return formats[key]
    for adapter in formats.values():
        if key in (a.lower() for a in adapter.META.aliases):
            return adapter
    close = get_close_matches(key, list(formats), n=3)
    raise UnknownFormat(
        f"'{format_id}' is not a format Orin knows.",
        hint=f"Did you mean: {', '.join(close)}?" if close else f"Known formats: {', '.join(formats)}.",
    )


def has(format_id: str) -> bool:
    try:
        get(format_id)
        return True
    except UnknownFormat:
        return False


def list_meta() -> list[FormatMeta]:
    return [a.META for a in all_formats().values()]


def families() -> list[tuple[str, str, list[str]]]:
    """(family id, label, format ids) in display order, for grouped dropdowns (spec 4.5)."""
    out: dict[str, list[str]] = {}
    for meta in list_meta():
        out.setdefault(meta.family, []).append(meta.id)
    return [(fam, FAMILY_LABELS.get(fam, fam), ids) for fam, ids in out.items()]


# ---------------------------------------------------------------------------
# Lookalikes: modes in any format with the same pitches, or a rotation of them.
# Computed, never hand-entered. Shown beside every mode guess so nobody reads a
# pitch-set match as "this is raga X".
# ---------------------------------------------------------------------------

_lookalike_cache: dict[str, tuple[str, ...]] = {}


def _signature(mode: ModeEntry) -> frozenset[int]:
    """The mode's main (ascending) pitches on a 50-cent grid, which is coarse enough to
    equate nominal tunings (386 and 400, 884 and 900) and fine enough to keep neutral
    degrees apart. Variable and descending alternates are left out."""
    return frozenset(int(round(d.cents / 50.0)) % 24 for d in mode.ascending)


def _rotations(sig: frozenset[int]) -> set[frozenset[int]]:
    return {frozenset((p - root) % 24 for p in sig) for root in sig}


def lookalikes(format_id: str, mode_id: str) -> tuple[str, ...]:
    """References like "arabic_maqam:ajam" (same pitches) or "carnatic:hindolam~" (a rotation)."""
    key = f"{format_id}:{mode_id}"
    if key in _lookalike_cache:
        return _lookalike_cache[key]
    mode = get(format_id).META.mode(mode_id)
    out: list[str] = []
    if mode is not None:
        sig = _signature(mode)
        rots = _rotations(sig)
        for other in list_meta():
            for m in other.mode_table:
                if other.id == format_id and m.id == mode.id:
                    continue
                osig = _signature(m)
                if osig == sig:
                    out.append(f"{other.id}:{m.id}")
                elif osig in rots:
                    out.append(f"{other.id}:{m.id}~")
    _lookalike_cache[key] = tuple(out)
    return _lookalike_cache[key]


def find_mode(ref: str, default_format: Optional[str] = None) -> Optional[tuple[FormatMeta, ModeEntry]]:
    """Resolve "format:mode" or a bare mode id (searched in ``default_format`` first)."""
    ref = ref.rstrip("~")
    if ":" in ref:
        fmt, mode_id = ref.split(":", 1)
        if has(fmt):
            meta = get(fmt).META
            mode = meta.mode(mode_id)
            return (meta, mode) if mode else None
        return None
    order = list_meta()
    if default_format and has(default_format):
        first = get(default_format).META
        order = [first] + [m for m in order if m.id != first.id]
    for meta in order:
        mode = meta.mode(ref)
        if mode:
            return meta, mode
    return None
