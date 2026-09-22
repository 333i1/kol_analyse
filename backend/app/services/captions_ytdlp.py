"""yt-dlp subtitle-only download. skip_download; manual and auto are separate calls."""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import tempfile
from typing import Any, Optional

logger = logging.getLogger(__name__)

SUB_LANGS = ["zh-Hans", "zh-Hant", "zh.*", "en.*"]
_SUB_EXTS = (".vtt", ".srt", ".json3", ".json")
_PREFERRED_LANGS = ["zh-Hans", "zh-Hant", "zh", "en"]

_TS = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{3})"
)


def _hms(h: Optional[str], m: Optional[str], s: Optional[str], ms: Optional[str]) -> float:
    return int(h or 0) * 3600 + int(m or 0) * 60 + int(s or 0) + int(ms or 0) / 1000.0


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).replace("\n", " ").strip()


def parse_vtt(content: str) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        m = _TS.search(raw)
        if not m:
            i += 1
            continue
        start = _hms(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _hms(m.group(5), m.group(6), m.group(7), m.group(8))
        i += 1
        texts: list[str] = []
        while i < len(lines) and lines[i].strip():
            if _TS.search(lines[i].strip()):
                break
            t = _strip_tags(lines[i])
            if t and t.upper() != "WEBVTT" and not t.upper().startswith("NOTE"):
                texts.append(t)
            i += 1
        text = " ".join(texts).strip()
        if text:
            cues.append({"start": start, "duration": max(end - start, 0.0), "text": text})
    return cues


def parse_srt(content: str) -> list[dict[str, Any]]:
    return parse_vtt(content)


def parse_json3(content: str) -> list[dict[str, Any]]:
    data = json.loads(content)
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    cues: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        segs = ev.get("segs") or []
        if not isinstance(segs, list):
            continue
        text = _strip_tags("".join(str(s.get("utf8") or "") for s in segs if isinstance(s, dict)))
        if not text:
            continue
        start = float(ev.get("tStartMs") or 0) / 1000.0
        dur = float(ev.get("dDurationMs") or 0) / 1000.0
        cues.append({"start": start, "duration": max(dur, 0.0), "text": text})
    return cues


def parse_subtitle_file(path: str) -> list[dict[str, Any]]:
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, encoding="utf-8-sig") as f:
            content = f.read()
    except OSError:
        return []
    if ext in (".json3", ".json"):
        try:
            return parse_json3(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            return []
    if ext == ".srt":
        return parse_srt(content)
    return parse_vtt(content)


def _file_lang(path: str) -> str:
    name = os.path.basename(path)
    stem, _ext = os.path.splitext(name)
    parts = stem.split(".")
    if len(parts) >= 2:
        return parts[-1]
    return "unknown"


def _lang_rank(lang: str) -> tuple[int, str]:
    raw = (lang or "").replace("_", "-")
    low = raw.lower()
    for i, pref in enumerate(_PREFERRED_LANGS):
        if raw == pref or low == pref.lower():
            return (i, low)
    if low.startswith("zh"):
        return (len(_PREFERRED_LANGS) - 1, low)
    if low.startswith("en"):
        return (len(_PREFERRED_LANGS), low)
    return (100, low)


def _pick_sub_file(paths: list[str]) -> Optional[str]:
    if not paths:
        return None
    return sorted(paths, key=lambda p: _lang_rank(_file_lang(p)))[0]


def _collect_sub_files(tmpdir: str) -> list[str]:
    found: list[str] = []
    for ext in _SUB_EXTS:
        found.extend(glob.glob(os.path.join(tmpdir, f"*{ext}")))
    return found


def _ydl_opts(tmpdir: str, *, automatic: bool, proxy: Optional[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
        "subtitleslangs": list(SUB_LANGS),
        "subtitlesformat": "vtt",
    }
    if automatic:
        opts["writeautomaticsub"] = True
        opts["writesubtitles"] = False
    else:
        opts["writesubtitles"] = True
    if proxy:
        opts["proxy"] = proxy
    return opts


def fetch_subtitles(
    video_id: str,
    *,
    automatic: bool = False,
    ydl_cls: Any = None,
    proxy: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Download subtitles only. automatic=False → uploaded; True → YouTube ASR.

    Returns {cues, language, is_generated} or None. Never downloads the video.
    """
    Ydl = ydl_cls
    if Ydl is None:
        try:
            import yt_dlp
        except ImportError:
            logger.warning("yt-dlp not installed; skip subtitle download")
            return None
        Ydl = yt_dlp.YoutubeDL

    tmpdir = tempfile.mkdtemp(prefix="subs_")
    try:
        opts = _ydl_opts(tmpdir, automatic=automatic, proxy=proxy)
        with Ydl(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        paths = _collect_sub_files(tmpdir)
        chosen = _pick_sub_file(paths)
        if not chosen:
            return None
        cues = parse_subtitle_file(chosen)
        if not cues:
            return None
        return {
            "cues": cues,
            "language": _file_lang(chosen),
            "is_generated": bool(automatic),
        }
    except Exception as e:
        logger.warning("yt-dlp subtitle fetch failed for %s automatic=%s: %s", video_id, automatic, e)
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
