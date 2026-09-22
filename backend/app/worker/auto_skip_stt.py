"""In-process flag: channel-spawned child analyses auto-skip STT transcript choice."""
from __future__ import annotations

import threading

_lock = threading.Lock()
_auto_skip: set[str] = set()


def mark_auto_skip_stt(task_id: str) -> None:
    with _lock:
        _auto_skip.add(task_id)


def consume_auto_skip_stt(task_id: str) -> bool:
    """Return True once for a marked task_id, then clear it."""
    with _lock:
        try:
            _auto_skip.remove(task_id)
            return True
        except KeyError:
            return False


def clear_auto_skip_stt() -> None:
    with _lock:
        _auto_skip.clear()
