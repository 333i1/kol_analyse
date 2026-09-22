from app.services.llm_client import LLMError
from app.services.pipelines.comments import run_comments
from tests.helpers import VID


def test_comments_429_short_reason_in_noise_label_no_skip_reason():
    import json
    from pathlib import Path

    import jsonschema

    class BusyLLM:
        def complete(self, **kwargs):
            raise LLMError("智谱繁忙(429)")

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
        llm=BusyLLM(),
    )
    assert out["pipeline_status"] == "failed"
    assert "skip_reason" not in out
    label = out["noise"]["label"]
    assert "智谱繁忙(429)" in label
    assert "HTTPStatusError" not in label
    assert "open.bigmodel" not in label
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
