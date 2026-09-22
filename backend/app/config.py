"""Settings for the single-video analysis MVP. Import must not crash on missing keys."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent



def normalize_proxy_url(url: Optional[str]) -> Optional[str]:
    """Prefer socks5h (DNS via proxy). Empty -> None."""
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if u.lower().startswith("socks5://"):
        return "socks5h://" + u[len("socks5://") :]
    return u

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    YOUTUBE_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_INPUT_USD_PER_1K: float = 0.00015
    LLM_OUTPUT_USD_PER_1K: float = 0.0006
    LLM_TIMEOUT: float = 120
    LLM_TEMPERATURE: float = 0.2
    # Pause between sequential billed calls on one LLMClient. Not a retry.
    LLM_INTER_CALL_DELAY_S: float = 1.5

    WHISPER_ENABLED: bool = True
    # When False: do not call youtube-transcript / yt-dlp captions; content uses title+description only.
    CAPTIONS_ENABLED: bool = True
    WHISPER_MODEL: str = "small"
    WHISPER_DEVICE: str = "auto"
    WHISPER_COMPUTE_TYPE: str = "auto"
    WHISPER_LANGUAGE: str = ""
    STT_USD_PER_MINUTE: float = 0.0  # local STT

    ANALYSIS_FRESH_TTL_HOURS: int = 24
    ANALYSIS_BUDGET_USD: float = 0.1
    SQLITE_PATH: str = str(_BACKEND_ROOT / "data" / "app.db")

    PROMPT_VERSION_CONTENT: str = "content-v1"
    PROMPT_VERSION_COMMENTS: str = "comments-v1.2"
    YOUTUBE_USD_PER_UNIT: float = 0.0
    # 博主层级阈值（订阅数）：素人 / 小博主 / 腰部博主 / 头部博主
    # 默认：<1万 / <10万 / <100万 / ≥100万
    BLOGGER_TIER_BREAKS: str = "10000,100000,1000000"
    LOG_LEVEL: str = "INFO"

    HTTP_PROXY: str = ""
    HTTPS_PROXY: str = ""
    ALL_PROXY: str = ""
    # YouTube Data API (metadata/comments) only. Empty = direct (do not reuse ALL_PROXY).
    # Captions still use CAPTION_PROXIES or ALL_PROXY via egress (D-040).
    YOUTUBE_API_PROXY: str = ""

    # Caption egress: static = single IP/proxy + slow gate; pool = reserved (falls back to static).
    CAPTION_EGRESS: str = "static"
    # Comma/newline-separated proxy URLs for future pool; static uses first entry if set.
    CAPTION_PROXIES: str = ""
    CAPTION_CONCURRENCY: int = 1
    CAPTION_MIN_INTERVAL_S: float = 10.0
    CAPTION_BACKOFF_S: float = 90.0
    CAPTION_JITTER_S: float = 2.0

    def proxy_url(self) -> Optional[str]:
        """Shared/legacy proxy (ALL_PROXY / HTTP(S)_PROXY). Used by caption egress, not Data API."""
        return normalize_proxy_url(
            self.HTTPS_PROXY or self.HTTP_PROXY or self.ALL_PROXY or None
        )

    def youtube_api_proxy_url(self) -> Optional[str]:
        """Proxy for YouTube Data API only. Empty means direct; never falls back to ALL_PROXY."""
        return normalize_proxy_url(self.YOUTUBE_API_PROXY or None)

    def proxies_dict(self) -> Optional[dict]:
        p = self.proxy_url()
        if not p:
            return None
        return {"http": p, "https": p}


@lru_cache
def get_settings() -> Settings:
    return Settings()
