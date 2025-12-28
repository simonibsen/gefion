"""
ML device detection and configuration.

Detects available compute devices (CPU, CUDA GPU) for ML training.
"""
from __future__ import annotations

from typing import Dict, Literal, Optional, Union


def detect_device(
    force_device: Optional[str] = None,
    return_info: bool = False,
) -> Union[str, Dict]:
    """
    Detect available compute device for ML training.

    Checks for CUDA availability via PyTorch (if installed).
    Falls back to CPU if CUDA is not available.

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
            - cuda_device_count: int (if CUDA available)
            - cuda_device_name: str (if CUDA available)
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

        # Add CUDA details if available
        if cuda_available:
            try:
                import torch

                info["cuda_device_count"] = torch.cuda.device_count()
                if info["cuda_device_count"] > 0:
                    info["cuda_device_name"] = torch.cuda.get_device_name(0)
            except Exception:
                pass

        return info

    return device


def _check_cuda_available() -> bool:
    """Check if CUDA is available via PyTorch."""
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        return False
    except Exception:
        return False


def get_xgboost_device_params(device: str) -> Dict:
    """
    Get XGBoost parameters for specified device.

    Args:
        device: 'cpu' or 'cuda'

    Returns:
        Dict of XGBoost parameters for the device
    """
    if device == "cuda":
        return {
            "tree_method": "gpu_hist",
            "device": "cuda",
        }
    else:
        return {
            "tree_method": "hist",
            "device": "cpu",
        }


def get_lightgbm_device_params(device: str) -> Dict:
    """
    Get LightGBM parameters for specified device.

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
