
from app.services.pipelines.comments import aggregate_items, round_sentiment


def test_low_confidence_excluded_from_sentiment():
    comments = {
        "a": {"text": "好", "like_count": 1, "reply_count": 0},
        "b": {"text": "差", "like_count": 1, "reply_count": 0},
        "c": {"text": "看不清？", "like_count": 0, "reply_count": 0},
    }
    items = [
        {"comment_id": "a", "sentiment": "正面", "reaction_type": "共鸣", "themes": ["x"], "confidence": 0.9, "ambiguous": False},
        {"comment_id": "b", "sentiment": "负面", "reaction_type": "反驳", "themes": ["x"], "confidence": 0.8, "ambiguous": False},
        {"comment_id": "c", "sentiment": "负面", "reaction_type": "提问", "themes": ["y"], "confidence": 0.4, "ambiguous": False},
    ]
    agg = aggregate_items(items, comments, sample_fetched=3, platform_total=10)
    assert len(agg["low_confidence_items"]) == 1
    assert agg["low_confidence_items"][0]["confidence"] < 0.6
    s = agg["sentiment"]
    assert s["positive_pct"] + s["neutral_pct"] + s["negative_pct"] in (99, 100, 101)
    # denominator is 2 (a,b), not 3
    assert s["positive_pct"] == 50
    assert s["negative_pct"] == 50
    theme_names = [t["name"] for t in agg["open_themes"]]
    assert "y" not in theme_names


def test_replies_not_in_denominator():
    comments = {
        "a": {"text": "顶层", "like_count": 1, "reply_count": 9},
    }
    items = [
        {"comment_id": "a", "sentiment": "正面", "reaction_type": "共鸣", "themes": ["t"], "confidence": 0.9, "ambiguous": False},
        {"comment_id": "reply1", "sentiment": "负面", "reaction_type": "反驳", "themes": ["t"], "confidence": 0.9, "ambiguous": False},
    ]
    agg = aggregate_items(items, comments, sample_fetched=1, platform_total=1)
    # reply1 not in comments_by_id usable? it is in items but comments_by_id missing still counted if conf>=0.6
    # Worker only sends top-level to LLM. Aggregation still must not treat reply_heat as sentiment.
    assert agg["reply_heat"]["included_in_sentiment"] is False


def test_human_review_status_const():
    comments = {"a": {"text": "反讽?", "like_count": 0, "reply_count": 0}}
    items = [
        {"comment_id": "a", "sentiment": "负面", "reaction_type": "其他", "themes": [], "confidence": 0.9, "ambiguous": True},
    ]
    agg = aggregate_items(items, comments, sample_fetched=1, platform_total=1)
    assert agg["human_review_items"][0]["status"] == "待确认"
    # ambiguous excluded from sentiment
    assert agg["sentiment"]["positive_pct"] == 0


def test_round_100():
    s = round_sentiment(1, 1, 1)
    assert s["positive_pct"] + s["neutral_pct"] + s["negative_pct"] == 100

def test_confidence_equal_threshold_not_in_low_list():
    comments = {"a": {"text": "边缘", "like_count": 0, "reply_count": 0}}
    items = [
        {"comment_id": "a", "sentiment": "中性", "reaction_type": "其他", "themes": [], "confidence": 0.6, "ambiguous": False},
    ]
    agg = aggregate_items(items, comments, sample_fetched=1, platform_total=1)
    assert agg["low_confidence_items"] == []


def test_comment_conclusion_themes_list_coerced_to_string():
    from app.services.pipelines.comments import run_comments
    from tests.helpers import COMMENTS_MOCK, VID

    comments = [
        {"comment_id": "c1", "text": "中火在电磁炉上怎么判断？", "like_count": 12, "reply_count": 3, "is_top_level": True},
        {"comment_id": "c2", "text": "终于讲清楚出锅时机了。", "like_count": 80, "reply_count": 0, "is_top_level": True},
        {"comment_id": "c3", "text": "太慢了熟手会跳过。", "like_count": 5, "reply_count": 1, "is_top_level": True},
    ]
    mock = {
        "items": COMMENTS_MOCK["items"],
        "comment_conclusion": {
            "doing": "提问报错为主",
            "themes": ["火候", "节奏"],
            "alignment": "视频教步骤，评论要排障",
            "confidence": 1.4,
        },
    }
    out = run_comments(
        db=None,
        task_id="t",
        video_id=VID,
        comments=comments,
        platform_total=10,
        llm=None,
        mock_response=mock,
    )
    themes = out["comment_conclusion"]["themes"]
    assert isinstance(themes, str)
    assert "火候" in themes
    assert 0.0 <= out["comment_conclusion"]["confidence"] <= 1.0
    assert "危机" not in themes
    assert "适合合作" not in themes

def test_comments_llm_payload_capped_json_and_top_likes():
    import json
    from app.services.pipelines.comments import run_comments
    from tests.helpers import COMMENTS_MOCK, VID

    captured = {}

    class CaptureLLM:
        def complete(self, **kwargs):
            captured["user_prompt"] = kwargs["user_prompt"]
            captured["expected_input_tokens"] = kwargs["expected_input_tokens"]
            return {
                "items": COMMENTS_MOCK["items"],
                "comment_conclusion": COMMENTS_MOCK["comment_conclusion"],
            }

    comments = [
        {
            "comment_id": f"c{i}",
            "text": f"这是一条正常评论内容{i}",
            "like_count": i,
            "reply_count": 0,
            "is_top_level": True,
        }
        for i in range(90)
    ]
    out = run_comments(
        db=None,
        task_id="t",
        video_id=VID,
        comments=comments,
        platform_total=90,
        llm=CaptureLLM(),
        mock_response=None,
    )
    assert out["pipeline_status"] == "ok"
    prompt = captured["user_prompt"]
    assert prompt.startswith("评论列表 JSON：")
    payload = json.loads(prompt.split("\n", 1)[1])
    assert isinstance(payload, list)
    assert len(payload) == 30
    ids = [row["id"] for row in payload]
    assert "c89" in ids
    assert "c0" not in ids
    assert ids == [f"c{i}" for i in range(60, 90)]
    assert "LLM子集 30" in out["comment_conclusion"]["sample_label"]
    assert captured["expected_input_tokens"] == max(400, len(prompt) // 3)


def test_comments_llm_error_failed_schema_valid_no_skip_reason():
    import json
    from pathlib import Path

    import jsonschema

    from app.services.llm_client import LLMError
    from app.services.pipelines.comments import run_comments
    from tests.helpers import VID

    class BoomLLM:
        def complete(self, **kwargs):
            raise LLMError("ReadTimeout: The read operation timed out")

    comments = [
        {
            "comment_id": "c1",
            "text": "中火在电磁炉上怎么判断？",
            "like_count": 12,
            "reply_count": 0,
            "is_top_level": True,
        }
    ]
    out = run_comments(
        db=None,
        task_id="t",
        video_id=VID,
        comments=comments,
        platform_total=10,
        llm=BoomLLM(),
    )
    assert out["pipeline_status"] == "failed"
    assert "skip_reason" not in out
    assert "LLM失败" in out["noise"]["label"]
    repo = Path(__file__).resolve().parents[3]
    schema = json.loads(
        (repo / "docs" / "02-施工" / "schema" / "video-analysis-result.schema.json").read_text(
            encoding="utf-8"
        )
    )
    examples = sorted(
        f for f in (repo / "docs" / "02-施工" / "examples").glob("*.json")
        if not f.name.startswith("channel-")
    )
    example = json.loads(examples[0].read_text(encoding="utf-8"))
    example["comment_analysis"] = out
    jsonschema.validate(example, schema)
