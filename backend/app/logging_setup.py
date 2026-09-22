
"""Logging filter that redacts *_API_KEY values."""
from __future__ import annotations

import logging
import re
from typing import Iterable

_KEY_RE = re.compile(r"(?i)((?:YOUTUBE|LLM|WHISPER|OPENAI|API)[_-]?API[_-]?KEY)[=:\s]+(\S+)")
_BEARER_RE = re.compile(r"(?i)(Bearer\s+)(\S+)")


class SecretRedactFilter(logging.Filter):
    def __init__(self, extra_secrets: Iterable[str] | None = None):
        super().__init__()
        self._secrets = [s for s in (extra_secrets or []) if s]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = _KEY_RE.sub(lambda m: f"{m.group(1)}=***REDACTED***", msg)
        redacted = _BEARER_RE.sub(r"\1***REDACTED***", redacted)
        for s in self._secrets:
            if s and s in redacted:
                redacted = redacted.replace(s, "***REDACTED***")
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def install_redact_filter(extra_secrets: Iterable[str] | None = None) -> None:
    filt = SecretRedactFilter(extra_secrets)
    root = logging.getLogger()
    if not any(isinstance(f, SecretRedactFilter) for f in root.filters):
        root.addFilter(filt)
    for name in logging.root.manager.loggerDict:
        lg = logging.getLogger(name)
        if not any(isinstance(f, SecretRedactFilter) for f in lg.filters):
            lg.addFilter(filt)
