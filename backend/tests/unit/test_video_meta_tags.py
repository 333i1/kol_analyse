# -*- coding: utf-8 -*-
from app.services.video_meta_tags import (
    classify_format_kind,
    normalize_language,
    infer_commercial,
    extract_paid_placement,
)


def test_format_shorts_url():
    assert classify_format_kind(url="https://www.youtube.com/shorts/abcdefghijk") == "short"


def test_format_by_duration():
    assert classify_format_kind(duration_seconds=45) == "short"
    assert classify_format_kind(duration_seconds=61) == "video"
    assert classify_format_kind(url="https://youtu.be/abcdefghijk", duration_seconds=None) == "video"


def test_language_from_api_code():
    assert normalize_language(default_audio_language="en-US") == "英语"
    assert normalize_language(default_language="zh-Hans") == "中文"
    assert normalize_language(default_audio_language="ja") == "日语"


def test_language_from_text():
    assert normalize_language(title="今天教大家做菜", description="") == "中文"
    assert normalize_language(title="How to build a REST API in Python", description="tutorial") == "英语"


def test_commercial_paid_flag():
    assert infer_commercial(has_paid_product_placement=True) == "平台标记：含付费产品植入"


def test_commercial_keywords():
    s = infer_commercial(title="开箱", description="本视频含 #ad 合作，优惠码 SAVE20")
    assert s and "商单线索" in s


def test_commercial_none():
    assert infer_commercial(title="日常 vlog", description="今天去公园") is None


def test_extract_paid_placement():
    assert extract_paid_placement({"paidProductPlacementDetails": {"hasPaidProductPlacement": True}}) is True
    assert extract_paid_placement({}) is None
