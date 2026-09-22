"""Injectable worker dependencies. Tests use set_deps(); lifespan uses ensure_production_deps()."""
from __future__ import annotations

from typing import Callable

from app.services.audio_fetch import fetch_audio
from app.services.llm_client import LLMClient
from app.services.transcript_client import TranscriptClient
from app.services.whisper_client import WhisperClient
from app.services.youtube_client import YoutubeClient


class WorkerDeps:
    def __init__(
        self,
        youtube: YoutubeClient | None = None,
        transcript: TranscriptClient | None = None,
        whisper: WhisperClient | None = None,
        llm: LLMClient | None = None,
        audio_fetch_fn: Callable | None = None,
        content_llm_mock: dict | None = None,
        comments_llm_mock: dict | None = None,
        skip_audio: bool = True,
    ):
        self.youtube = youtube or YoutubeClient()
        self.transcript = transcript or TranscriptClient()
        self.whisper = whisper or WhisperClient()
        self.llm = llm or LLMClient()
        self.audio_fetch_fn = audio_fetch_fn or fetch_audio
        self.content_llm_mock = content_llm_mock
        self.comments_llm_mock = comments_llm_mock
        self.skip_audio = skip_audio


_default_deps: WorkerDeps | None = None


def set_deps(deps: WorkerDeps | None) -> None:
    global _default_deps
    _default_deps = deps


def get_deps() -> WorkerDeps:
    return _default_deps or WorkerDeps()


def make_production_deps() -> WorkerDeps:
    """Production: captions fail → yt-dlp audio_fetch → whisper.transcribe(path). Never transcribe("")."""
    return WorkerDeps(skip_audio=False)


def ensure_production_deps() -> None:
    """Lifespan wiring. Do not clobber fakes tests injected via set_deps()."""
    global _default_deps
    if _default_deps is None:
        _default_deps = make_production_deps()
