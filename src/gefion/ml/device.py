"""
ML device detection and configuration.

Detects available compute devices (CPU, CUDA GPU) for ML training.

Torch-free by design (#146): the old probe imported torch, which this
stack deliberately never installs (constitution: no deep learning), so
detection silently returned cpu on GPU hosts. Detection now asks torch
only IF it happens to be present, otherwise probes nvidia-smi and
validates with an XGBoost micro-fit — a GPU that training can't actually
use falls back to cpu with a warning, never an error.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Dict, Literal, Optional, Union

logger = logging.getLogger(__name__)


def _torch_cuda_available() -> Optional[bool]:
    """Torch's answer when torch exists; None when it is not installed
    (absence of torch is NOT absence of a GPU — the #146 defect)."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except ImportError:
        return None
    except Exception:
        return None


def _nvidia_smi_ok() -> bool:
    """A CUDA driver answers nvidia-smi -L with at least one GPU line."""
    try:
        result = subprocess.run(["nvidia-smi", "-L"], capture_output=True,
                                text=True, timeout=10)
        return result.returncode == 0 and "GPU" in (result.stdout or "")
    except Exception:
        return False


def _validate_cuda_training() -> bool:
    """Prove the GPU is USABLE for training, not merely visible: a tiny
    XGBoost fit on cuda. Any failure means cpu (graceful, warned)."""
    try:
        import numpy as np
        import xgboost as xgb

        X = np.arange(20, dtype=float).reshape(10, 2)
        y = np.arange(10, dtype=float)
        model = xgb.XGBRegressor(n_estimators=2, max_depth=2,
                                 tree_method="hist", device="cuda",
                                 verbosity=0)
        model.fit(X, y)
        return True
    except Exception as exc:
        logger.warning(f"GPU visible but CUDA training validation failed "
                       f"({exc}) — falling back to cpu")
        return False


def _check_cuda_available() -> bool:
    """Torch-free CUDA availability: torch's answer if torch exists, else
    nvidia-smi presence validated by an actual cuda micro-fit."""
    torch_answer = _torch_cuda_available()
    if torch_answer is not None:
        return torch_answer
    if shutil.which("nvidia-smi") is None:
        return False
    if not _nvidia_smi_ok():
        return False
    return _validate_cuda_training()


def detect_device(
    force_device: Optional[str] = None,
    return_info: bool = False,
) -> Union[str, Dict]:
    """
    Detect available compute device for ML training.

    Torch-free (#146): consults torch only when installed; otherwise probes
    nvidia-smi and validates with a cuda micro-fit. Falls back to CPU when
    CUDA is absent or unusable.

    Args:
        force_device: Force a specific device ('cpu' or 'cuda').
                      If provided, skips auto-detection.
        return_info: If True, return dict with detailed device info.
                     If False, return just the device string.

    Returns:
        If return_info=False: 'cpu' or 'cuda'
        If return_info=True: Dict with keys:
            - device: 'cpu' or 'cuda'
            - cuda_available: bool
            - forced: bool
            - cuda_device_name: str (best effort, when available)
    """
    # Handle explicit override
    if force_device is not None:
        device = force_device.lower()
        if device not in ("cpu", "cuda"):
            raise ValueError(f"Invalid device: {device}. Must be 'cpu' or 'cuda'.")

        if return_info:
            return {
                "device": device,
                "cuda_available": _check_cuda_available(),
                "forced": True,
            }
        return device

    # Auto-detect device
    cuda_available = _check_cuda_available()
    device = "cuda" if cuda_available else "cpu"

    if return_info:
        info = {
            "device": device,
            "cuda_available": cuda_available,
            "forced": False,
        }
        if cuda_available:
            try:
                result = subprocess.run(["nvidia-smi", "-L"],
                                        capture_output=True, text=True,
                                        timeout=10)
                first = (result.stdout or "").splitlines()
                if first:
                    info["cuda_device_name"] = first[0].strip()
            except Exception:
                pass
        return info

    return device


def get_xgboost_device_params(device: str) -> Dict:
    """
    Get XGBoost parameters for specified device.

    XGBoost 2.0+: use device="cuda" with tree_method="hist" (gpu_hist is deprecated).

    Args:
        device: 'cpu' or 'cuda'

    Returns:
        Dict of XGBoost parameters for the device
    """
    return {
        "tree_method": "hist",
        "device": "cuda" if device == "cuda" else "cpu",
    }


def get_lightgbm_device_params(device: str) -> Dict:
    """
    Get LightGBM parameters for specified device.

    NOTE (#146): the pip LightGBM wheel has no GPU support — callers must
    not pass cuda through to LightGBM; models.py downgrades to cpu with a
    warning before reaching here.

    Args:
        device: 'cpu' or 'cuda'

    Returns:
        Dict of LightGBM parameters for the device
    """
    if device == "cuda":
        return {
            "device": "gpu",
        }
    else:
        return {
            "device": "cpu",
        }
