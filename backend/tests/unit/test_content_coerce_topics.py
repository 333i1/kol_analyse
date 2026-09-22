# -*- coding: utf-8 -*-
"""D12: video_types 12-enum + topics coerce + compare CSV 主题列."""
from __future__ import annotations

import sys
from pathlib import Path

from app.services.pipelines.content import VIDEO_TYPES, _coerce_topics, _coerce_video_types

_TOOLS = Path(__file__).resolve().parents[3] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from compare_csv_zh import COMPARE_FIELDS, result_to_compare_row  # noqa: E402


def test_video_types_exactly_12():
    assert len(VIDEO_TYPES) == 12
    assert VIDEO_TYPES == (
        "教程",
        "评测",
        "日常",
        "观点",
        "资讯",
        "娱乐",
        "游戏",
        "音乐",
        "影视解说",
        "体育",
        "喜剧",
        "其他",
    )


def test_coerce_video_types_accepts_new():
    assert _coerce_video_types(["音乐", "体育", "美妆"]) == ["音乐", "体育", "其他"]


def test_coerce_topics_rules():
    assert _coerce_topics(None) == []
    assert _coerce_topics("美妆") == []
    assert _coerce_topics([" 美妆 ", "", "球鞋", "开箱", "多余"]) == ["美妆", "球鞋", "开箱"]
    long = "啊" * 30
    assert _coerce_topics([long]) == ["啊" * 20]
    assert _coerce_topics(["美妆", "美妆"]) == ["美妆"]


def test_compare_fields_theme_after_category():
    assert COMPARE_FIELDS.index("主题") == COMPARE_FIELDS.index("类目") + 1
    assert COMPARE_FIELDS.index("人工测评") == COMPARE_FIELDS.index("商业状态") + 1
    row = result_to_compare_row(
        "abcdefghijk",
        "https://youtu.be/abcdefghijk",
        "completed",
        {
            "video": {"title": "t", "channel_name": "c"},
            "content_analysis": {
                "content_conclusion": {
                    "video_types": ["评测"],
                    "topics": ["美妆", "开箱"],
                    "thesis": "x",
                    "tone": "解说",
                    "commercial": None,
                }
            },
            "comment_analysis": {},
            "health": {"transcript": {"mode": "none"}, "notes": []},
        },
        None,
        "run1",
        captions_mode="off",
        row_index=1,
    )
    assert row["类目"] == "评测"
    assert row["主题"] == "美妆|开箱"
