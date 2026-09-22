# -*- coding: utf-8 -*-
from app.services import worker as w


def test_mark_consume_skip_captions():
    tid = "task-skip-1"
    assert w.consume_skip_captions(tid) is False
    w.mark_skip_captions(tid)
    assert w.consume_skip_captions(tid) is True
    assert w.consume_skip_captions(tid) is False
