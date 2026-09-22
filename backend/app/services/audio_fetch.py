
"""yt-dlp bestaudio for STT only. Delete file after use. Never fetch comments."""
from __future__ import annotations

import glob
import logging
import os
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from app.config import get_settings
except Exception:  # pragma: no cover
    get_settings = None  # type: ignore


class AudioFetchError(Exception):
    pass


def fetch_audio(video_id: str, *, ydl_cls=None, proxy: Optional[str] = None) -> str:
    """Download bestaudio to a temp file. Caller must unlink."""
    try:
        import yt_dlp
    except ImportError as e:
        raise AudioFetchError("yt-dlp not installed") from e

    tmpdir = tempfile.mkdtemp(prefix="stt_")
    outtmpl = os.path.join(tmpdir, "%(id)s.%(ext)s")
    if proxy is None and get_settings is not None:
        try:
            proxy = get_settings().proxy_url()
        except Exception:
            proxy = None
    opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "extract_audio": False,
    }
    if proxy:
        opts["proxy"] = proxy
    Ydl = ydl_cls or yt_dlp.YoutubeDL
    try:
        with Ydl(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        matches = glob.glob(os.path.join(tmpdir, f"{video_id}.*"))
        if not matches:
            matches = glob.glob(os.path.join(tmpdir, "*.*"))
        if not matches:
            raise AudioFetchError("yt-dlp 未产出音频文件")
        return matches[0]
    except AudioFetchError:
        raise
    except Exception as e:
        raise AudioFetchError(f"{type(e).__name__}: {e}") from e


def cleanup(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass
    parent = os.path.dirname(path)
    try:
        os.rmdir(parent)
    except OSError:
        pass
