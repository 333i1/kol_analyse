# -*- coding: utf-8 -*-
from app.services.blogger_tier import classify_blogger_tier, parse_breaks, DEFAULT_BREAKS


def test_default_breaks():
    assert parse_breaks(None) == DEFAULT_BREAKS
    assert parse_breaks("10000,100000,1000000") == (10000, 100000, 1000000)
    assert parse_breaks("bad") == DEFAULT_BREAKS


def test_tiers():
    assert classify_blogger_tier(None) == ""
    assert classify_blogger_tier(0) == "素人"
    assert classify_blogger_tier(9999) == "素人"
    assert classify_blogger_tier(10000) == "小博主"
    assert classify_blogger_tier(99999) == "小博主"
    assert classify_blogger_tier(100000) == "腰部博主"
    assert classify_blogger_tier(999999) == "腰部博主"
    assert classify_blogger_tier(1000000) == "头部博主"
    assert classify_blogger_tier(5_000_000) == "头部博主"
