\
from app.services.channel_aggregate import aggregate_channel_brief


def _child(vid, cohort, *, comments_ok=True, views=1000, comments=100, banned=False):
    result = {
        "metrics": {
            "view_count": views,
            "like_count": 50,
            "comment_count": comments,
            "duration_seconds": 600,
        },
        "content_analysis": {
            "pipeline_status": "ok",
            "content_conclusion": {
                "video_types": ["测评"],
                "tone": "冷静",
                "thesis": "这是论题" if not banned else "建议合作赶紧投",
            },
            "segments": [
                {"segment_kind": "开场", "commercial": None, "cta_type": None, "ad_overlay": False},
                {"segment_kind": "展开", "commercial": None, "cta_type": None, "ad_overlay": False},
                {
                    "segment_kind": "收束",
                    "commercial": "链接在描述栏",
                    "cta_type": "购买",
                    "ad_overlay": True,
                },
            ],
        },
        "comment_analysis": {
            "pipeline_status": "ok" if comments_ok else "failed",
            "sentiment": {"positive": 60, "neutral": 30, "negative": 10},
            "open_themes": ["价格 / 性价比", "续航"],
            "reaction_types": [{"label": "共鸣", "share": 0.5}],
            "negative_examples": [{"text": "太贵了", "like_count": 10}],
            "positive_examples": [{"text": "有用", "like_count": 3}],
            "human_review_items": [],
        },
        "contrast_summary": {
            "contrast_sentence": "内容说性价比，评论嫌贵",
        },
        "health": {"notes": []},
    }
    return {
        "video_id": vid,
        "cohort": cohort,
        "title": vid,
        "view_count": views,
        "comment_count": comments,
        "pipeline_status": "completed" if comments_ok else "degraded",
        "result": result,
    }


def test_weighted_sentiment_skips_failed_comments():
    children = [
        _child("id000000001", "recent", comments_ok=True, comments=100),
        _child("id000000002", "recent", comments_ok=False, comments=999),
        _child("id000000003", "hot", comments_ok=True, comments=100),
    ]
    brief = aggregate_channel_brief(
        channel={"channel_id": "UC" + "a" * 22, "title": "T"},
        children=children,
    )
    assert brief["audience_habits"]["weighted_video_count"] == 2
    assert any("评论管线失败" in n for n in brief["health"]["notes"])
    assert "建议合作" not in json_dumps(brief)


def test_partial_averages_use_completed_only():
    children = [
        _child("id000000001", "recent", views=100),
        _child("id000000002", "recent", views=300),
        {
            "video_id": "id000000003",
            "cohort": "hot",
            "view_count": 999999,
            "pipeline_status": "analyzing",
            "result": None,
        },
    ]
    brief = aggregate_channel_brief(
        channel={"channel_id": "UC" + "a" * 22, "title": "T"},
        children=children,
    )
    assert brief["metrics"]["sample_count_used"] == 2
    assert brief["metrics"]["avg_view_count"] == 200


def test_commercial_omitted_when_empty():
    child = _child("id000000001", "recent")
    # strip commercial signals
    for seg in child["result"]["content_analysis"]["segments"]:
        seg["commercial"] = None
        seg["cta_type"] = None
        seg["ad_overlay"] = False
    brief = aggregate_channel_brief(
        channel={"channel_id": "UC" + "a" * 22, "title": "T"},
        children=[child],
    )
    # still has commercial from empty? ad/cta zero → commercial key may still appear if rate logic
    # force no excerpts and zeros
    assert brief.get("commercial") is None or (
        not brief["commercial"].get("excerpts")
        and (brief["commercial"].get("ad_overlay_count") or 0) == 0
    )


def test_bans_suggestion_words():
    children = [_child("id000000001", "recent", banned=True)]
    brief = aggregate_channel_brief(
        channel={"channel_id": "UC" + "a" * 22, "title": "T"},
        children=children,
    )
    blob = json_dumps(brief)
    assert "建议合作" not in blob
    assert "危机" not in blob


def json_dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)
