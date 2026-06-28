"""
Suylios Downloader - FFmpeg Manager.

Locates the embedded or system FFmpeg binary, queries its version,
and probes hardware-accelerated encoding capabilities.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from src.config import config

logger = logging.getLogger(__name__)


def locate_ffmpeg() -> Optional[str]:
    """Return the absolute path to a usable ``ffmpeg`` binary.

    Search order:
    1. ``./bin/ffmpeg.exe`` relative to the application root.
    2. The system ``PATH``.

    Returns ``None`` when FFmpeg cannot be found.
    """
    return config.get_ffmpeg_path()


def get_ffmpeg_version(path: Optional[str] = None) -> Optional[str]:
    """Run ``ffmpeg -version`` and return the first line (version string).

    Parameters
    ----------
    path:
        Explicit path to the ``ffmpeg`` binary.  Falls back to
        :func:`locate_ffmpeg` when *None*.
    """
    path = path or locate_ffmpeg()
    if not path:
        return None
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout.splitlines()[0].strip()
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Could not determine FFmpeg version: %s", exc)
    return None


def is_available() -> bool:
    """Return ``True`` when a working FFmpeg binary is reachable."""
    return locate_ffmpeg() is not None


def get_hardware_accel() -> str:
    """Detect the best available hardware-accelerated H.264 encoder.

    Returns one of:
    * ``"h264_nvenc"``  – NVIDIA NVENC
    * ``"h264_amf"``    – AMD AMF
    * ``"h264_qsv"``    – Intel Quick Sync Video
    * ``"libx264"``     – software fallback

    The detection works by asking FFmpeg to list its encoders and checking
    for known hardware encoder names.
    """
    ffmpeg = locate_ffmpeg()
    if not ffmpeg:
        return "libx264"

    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output = result.stdout if result.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return "libx264"

    # Order matters – prefer NVIDIA, then AMD, then Intel.
    for encoder in ("h264_nvenc", "h264_amf", "h264_qsv"):
        if encoder in output:
            logger.info("Hardware encoder detected: %s", encoder)
            return encoder

    logger.info("No hardware encoder found – falling back to libx264.")
    return "libx264"
