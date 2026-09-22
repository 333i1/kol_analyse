# -*- coding: utf-8 -*-
from app.config import normalize_proxy_url


def test_socks5_becomes_socks5h():
    assert normalize_proxy_url("socks5://u:p@h:1") == "socks5h://u:p@h:1"


def test_socks5h_unchanged():
    assert normalize_proxy_url("socks5h://u:p@h:1") == "socks5h://u:p@h:1"


def test_empty():
    assert normalize_proxy_url("") is None
    assert normalize_proxy_url(None) is None
