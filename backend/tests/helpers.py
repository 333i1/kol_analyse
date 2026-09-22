
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.transcript_client import Cue, TranscriptResult
from app.services.youtube_client import VideoUnavailable


VID = "dQw4w9WgXcQ"


class FakeYoutube:
    def __init__(self, *, missing=False, comments=None, meta=None):
        self.missing = missing
        self._comments = comments if comments is not None else [
            {
                "comment_id": "c1",
                "text": "中火在电磁炉上怎么判断？",
                "like_count": 12,
                "reply_count": 3,
                "is_top_level": True,
            },
            {
                "comment_id": "c2",
                "text": "终于讲清楚出锅时机了。",
                "like_count": 80,
                "reply_count": 0,
                "is_top_level": True,
            },
            {
                "comment_id": "c3",
                "text": "太慢了熟手会跳过。",
                "like_count": 5,
                "reply_count": 1,
                "is_top_level": True,
            },
        ]
        self._meta = meta or {
            "id": VID,
            "snippet": {
                "title": "家常番茄炒蛋",
                "description": "把番茄炒蛋拆成可跟做的步骤。",
                "channelTitle": "厨房日记",
                "publishedAt": "2026-08-26T00:00:00Z",
            },
            "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "10"},
            "contentDetails": {"duration": "PT11M24S"},
        }
        self.videos_list_calls = 0
        self.comment_calls = 0

    def videos_list(self, video_id: str):
        self.videos_list_calls += 1
        if self.missing:
            raise VideoUnavailable("not found")
        return self._meta

    def comment_threads(self, video_id: str, *, max_comments: int = 200):
        self.comment_calls += 1
        return list(self._comments)


class FakeTranscript:
    def __init__(self, ok=True, error="ParseError: boom"):
        self.ok = ok
        self.error = error
        self.calls = 0

    def fetch(self, video_id: str) -> TranscriptResult:
        self.calls += 1
        if self.ok:
            cues = [
                Cue(0, 8, "今天只做番茄炒蛋"),
                Cue(80, 10, "番茄先改刀"),
                Cue(640, 8, "下一集做青椒肉丝"),
            ]
            return TranscriptResult(ok=True, cues=cues, text=" ".join(c.text for c in cues))
        return TranscriptResult(ok=False, error_msg=self.error)


class FakeWhisper:
    def __init__(self, ok=False, enabled=True):
        self._ok = ok
        self._enabled = enabled
        self.transcribe_calls = 0

    def enabled(self):
        return self._enabled

    def transcribe(self, audio_file: str) -> TranscriptResult:
        self.transcribe_calls += 1
        if self._ok:
            cues = [Cue(0, 10, "语音转录文本一二三四五六七八九十")]
            return TranscriptResult(ok=True, cues=cues, text=cues[0].text, language="zh")
        return TranscriptResult(ok=False, error_msg="stt fail")


CONTENT_MOCK = {
    "segments": [
        {
            "kind": "开场",
            "confidence": 0.9,
            "summary": "说明今天只做一道家常菜",
            "cta_type": None,
            "ad_overlay": None,
            "caption_start": "00:00",
            "caption_end": "01:00",
        },
        {
            "kind": "展开",
            "confidence": 0.9,
            "summary": "按切配热锅炒蛋合炒讲解",
            "cta_type": None,
            "ad_overlay": None,
            "caption_start": "01:00",
            "caption_end": "10:00",
        },
        {
            "kind": "收束",
            "confidence": 0.8,
            "summary": "成品特写后邀请收藏",
            "cta_type": "下一集",
            "ad_overlay": None,
            "caption_start": "10:00",
            "caption_end": "11:24",
        },
    ],
    "content_conclusion": {
        "video_types": ["教程"],
        "topics": ["做饭"],
        "thesis": "把番茄炒蛋拆成可跟做的步骤",
        "tone": "解说",
        "tone_description": "耐心解说",
        "commercial": None,
        "confidence": 0.9,
    },
}

COMMENTS_MOCK = {
    "items": [
        {
            "comment_id": "c1",
            "sentiment": "中性",
            "reaction_type": "提问",
            "display_label": "提问",
            "themes": ["火候"],
            "confidence": 0.82,
            "ambiguous": False,
        },
        {
            "comment_id": "c2",
            "sentiment": "正面",
            "reaction_type": "共鸣",
            "display_label": "共鸣",
            "themes": ["出锅时机"],
            "confidence": 0.9,
            "ambiguous": False,
        },
        {
            "comment_id": "c3",
            "sentiment": "负面",
            "reaction_type": "反驳",
            "display_label": "反驳",
            "themes": ["节奏"],
            "confidence": 0.7,
            "ambiguous": False,
        },
    ],
    "comment_conclusion": {
        "doing": "提问报错为主",
        "themes": "火候与节奏",
        "alignment": "视频教步骤，评论要排障",
        "confidence": 0.8,
    },
}
