"""Stem separation with Demucs (spec 7.3). Optional: every failure returns None and a reason.

The model is only ever loaded from weights already on this machine. A missing package
or missing weights is a warning, never an error and never a silent download (set
``ORIN_DEMUCS_DOWNLOAD=1`` to let Demucs fetch them). Audio goes in and out as arrays:
``torchaudio.load`` and ``save`` are not used.

A tanpura or other drone is not subtracted; it is a tonic cue (see ``orin.tonic.drone``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np

DEFAULT_MODEL = "htdemucs"
STEMS = ("drums", "bass", "other", "vocals")


def model_name() -> str:
    return os.environ.get("ORIN_DEMUCS_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _hf_files(name: str) -> Optional[tuple[dict, list[str]]]:
    """(bag definition, weight files) when every file of the model is already in the
    HuggingFace cache (where Demucs 4.1 and later keeps them); None otherwise. Looks at
    the disk only."""
    try:
        import yaml
        from demucs.hf import DEFAULT_NAMESPACE, hf_repo_name
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return None
    repo_id = f"{DEFAULT_NAMESPACE}/{hf_repo_name(name)}"
    bag_path = try_to_load_from_cache(repo_id, f"{name}.yaml")
    if not isinstance(bag_path, str):
        return None
    try:
        with open(bag_path, "r", encoding="utf-8") as f:
            bag = yaml.safe_load(f)
        paths = [try_to_load_from_cache(repo_id, f"{sig}.safetensors") for sig in bag["models"]]
    except Exception:
        return None
    if not paths or not all(isinstance(p, str) for p in paths):
        return None
    return bag, [str(p) for p in paths]


def _hf_cached(name: str) -> bool:
    return _hf_files(name) is not None


def _load_model(name: str):
    """The model, built from the cached files without contacting the network when it can be.
    ``demucs.pretrained.get_model`` is the general route (it checks the hub first)."""
    local = _hf_files(name)
    if local is not None:
        from demucs.apply import BagOfModels
        from demucs.hf import load_safetensors_model

        bag, paths = local
        return BagOfModels([load_safetensors_model(p) for p in paths], bag.get("weights"), bag.get("segment"))
    from demucs.pretrained import get_model

    return get_model(name)


def _torch_hub_cached(name: str) -> bool:
    """All checkpoints of the model are in the torch hub cache (the older download location)."""
    try:
        import torch
        import yaml
        from demucs.pretrained import REMOTE_ROOT
    except Exception:
        return False
    try:
        with open(REMOTE_ROOT / f"{name}.yaml", "r", encoding="utf-8") as f:
            signatures = yaml.safe_load(f)["models"]
        listed = [line.strip() for line in (REMOTE_ROOT / "files.txt").read_text().splitlines()]
        checkpoints = Path(torch.hub.get_dir()) / "checkpoints"
        for sig in signatures:
            files = [Path(line).name for line in listed if Path(line).name.startswith(f"{sig}-")]
            if not files or not (checkpoints / files[0]).exists():
                return False
        return bool(signatures)
    except Exception:
        return False


def weights_available(name: Optional[str] = None) -> bool:
    name = name or model_name()
    if name == "demucs_unittest":  # Demucs's own tiny random model; needs no weights
        return True
    return _hf_cached(name) or _torch_hub_cached(name)


def separate_with_reason(audio_44k: np.ndarray, sr: int, *, model: Optional[str] = None,
                         allow_download: Optional[bool] = None) -> tuple[Optional[dict[str, np.ndarray]], Optional[str]]:
    """(stems, None) or (None, why not). Stems are float32 arrays shaped (channels, samples)
    at the input's sample rate, keyed drums / bass / other / vocals."""
    name = model or model_name()
    try:
        import torch
        from demucs.apply import apply_model
    except ImportError as exc:
        return None, f"Stem separation is not installed ({exc.name or exc}); the full mix was analysed instead."

    if allow_download is None:
        allow_download = os.environ.get("ORIN_DEMUCS_DOWNLOAD", "") == "1"
    if not allow_download and not weights_available(name):
        return None, (f"The '{name}' separation model has not been downloaded on this machine; "
                      "the full mix was analysed instead.")

    x = np.asarray(audio_44k, dtype=np.float32)
    if x.ndim == 1:
        x = np.stack([x, x])
    if x.shape[-1] == 0:
        return None, "There is no audio to separate."
    try:
        net = _load_model(name)
        net.eval()
        target_sr, channels = int(net.samplerate), int(net.audio_channels)
        if sr != target_sr:
            from ..ingest.audio import _resample

            x = _resample(x, sr, target_sr)
        if x.shape[0] != channels:
            x = np.repeat(x.mean(axis=0, keepdims=True), channels, axis=0)
        mix = torch.from_numpy(np.ascontiguousarray(x))
        ref = mix.mean(0)
        mean, std = ref.mean(), ref.std().clamp(min=1e-8)
        with torch.no_grad():
            out = apply_model(net, ((mix - mean) / std)[None], device="cpu", shifts=1, split=True,
                              overlap=0.25, progress=False)[0]
        out = (out * std + mean).cpu().numpy().astype(np.float32)
        stems = {src: out[i] for i, src in enumerate(net.sources)}
        if sr != target_sr:
            from ..ingest.audio import _resample

            stems = {k: _resample(v, target_sr, sr) for k, v in stems.items()}
        return stems, None
    except Exception as exc:
        return None, f"Stem separation failed ({type(exc).__name__}: {exc}); the full mix was analysed instead."


def separate(audio_44k: np.ndarray, sr: int) -> Optional[dict[str, np.ndarray]]:
    """Stems, or None when Demucs or its weights are unavailable. ``separate_with_reason``
    also says why."""
    return separate_with_reason(audio_44k, sr)[0]
