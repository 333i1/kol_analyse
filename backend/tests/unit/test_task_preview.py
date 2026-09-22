# -*- coding: utf-8 -*-
from app.api.routes import _fmt_count, _fmt_duration


def test_fmt_count():
    assert _fmt_count(None) == "—"
    assert _fmt_count(12000) == "1.2万"
    assert _fmt_count(500) == "500"


def test_fmt_duration():
    assert _fmt_duration(None) == "—"
    assert _fmt_duration(65) == "1:05"
    assert _fmt_duration(3661) == "1:01:01"
