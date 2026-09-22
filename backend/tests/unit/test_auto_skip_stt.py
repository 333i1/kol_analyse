from __future__ import annotations

from app.worker.auto_skip_stt import (
    clear_auto_skip_stt,
    consume_auto_skip_stt,
    mark_auto_skip_stt,
)


def test_mark_consume_once_then_false():
    clear_auto_skip_stt()
    mark_auto_skip_stt("t1")
    assert consume_auto_skip_stt("t1") is True
    assert consume_auto_skip_stt("t1") is False


def test_consume_unmarked_false():
    clear_auto_skip_stt()
    assert consume_auto_skip_stt("missing") is False


def test_clear_wipes_all():
    clear_auto_skip_stt()
    mark_auto_skip_stt("a")
    mark_auto_skip_stt("b")
    clear_auto_skip_stt()
    assert consume_auto_skip_stt("a") is False
    assert consume_auto_skip_stt("b") is False


def test_mark_idempotent():
    clear_auto_skip_stt()
    mark_auto_skip_stt("t")
    mark_auto_skip_stt("t")
    assert consume_auto_skip_stt("t") is True
    assert consume_auto_skip_stt("t") is False
