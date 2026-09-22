# -*- coding: utf-8 -*-
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compare_csv_zh import COMPARE_FIELDS, content_data_source, result_to_compare_row


def test_compare_fields_include_source():
    assert "数据来源" in COMPARE_FIELDS


def test_source_captions_human():
    r = {"health": {"transcript": {"mode": "captions"}, "notes": ["人工字幕"]}}
    assert content_data_source(r) == "公开字幕（人工）"


def test_source_captions_auto():
    r = {"health": {"transcript": {"mode": "captions"}, "notes": ["自动字幕（YouTube ASR）"]}}
    assert content_data_source(r) == "公开字幕（自动ASR）"


def test_source_whisper():
    r = {"health": {"transcript": {"mode": "speech_to_text"}, "notes": []}}
    assert content_data_source(r) == "语音转录（Whisper）"


def test_source_skip_captions_mode():
    r = {"health": {"transcript": {"mode": "none"}, "notes": []}}
    assert content_data_source(r, "off") == "标题/简介（跳过字幕）"


def test_source_skip_stt():
    r = {
        "health": {"transcript": {"mode": "none"}, "notes": ["运营选择跳过语音转录，内容仅基于标题/简介"]},
        "content_analysis": {"skip_reason": "user_skipped_stt"},
    }
    assert content_data_source(r, "on") == "标题/简介（跳过语音转录）"


def test_source_caption_unavailable():
    r = {
        "health": {
            "transcript": {"mode": "none"},
            "notes": ["无字幕：内容侧按标题/简介做简要结论，评论分析照常。"],
        },
        "content_analysis": {},
    }
    assert content_data_source(r, "on") == "标题/简介（字幕不可用）"


def test_row_has_source():
    row = result_to_compare_row(
        "abcdefghijk",
        "https://youtu.be/abcdefghijk",
        "completed",
        {
            "health": {"transcript": {"mode": "captions"}, "notes": ["人工字幕"]},
            "video": {},
            "content_analysis": {"content_conclusion": {}},
            "comment_analysis": {},
        },
        None,
        "run1",
        captions_mode="on",
        row_index=1,
    )
    assert row["数据来源"] == "公开字幕（人工）"
