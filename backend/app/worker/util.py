"""Shared worker clock helpers and hard-timeouts."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

# Locked by 07-鲁棒性施工.md / T075. Tests monkeypatch these on this module.
YDL_AUDIO_TIMEOUT_S = 120.0
WHISPER_TRANSCRIBE_TIMEOUT_S = 300.0
RUN_TASK_TIMEOUT_S = 720.0

T = TypeVar("T")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def call_with_timeout(fn: Callable[..., T], timeout_s: float, *args: Any, **kwargs: Any) -> T:
    """Run fn; raise TimeoutError if it exceeds timeout_s.

    Does not kill the worker thread (not possible in CPython). Caller must treat
    the operation as failed/degraded and must not wait for the leaked thread.
    """
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — propagate any failure
            box["error"] = exc

    thread = threading.Thread(target=runner, name="hard-timeout", daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    if thread.is_alive():
        raise TimeoutError(f"operation exceeded {timeout_s}s")
    if "error" in box:
        raise box["error"]
    return box["value"]
