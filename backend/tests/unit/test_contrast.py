
from app.services.pipelines.contrast import build_contrast


def test_both_failed():
    c = build_contrast(
        content={"pipeline_status": "failed"},
        comments={"pipeline_status": "failed"},
        freshness_tags=["x"],
        fetched_at_label="y",
    )
    assert c["contrast_sentence"] == "内容与评论均不可用，无法对照。"
    assert c["content_says"] == "无转录，无法概括内容。"
    assert c["comments_reply"] == "无评论样本。"


def test_content_failed_only():
    c = build_contrast(
        content={"pipeline_status": "failed"},
        comments={"pipeline_status": "ok", "comment_conclusion": {"doing": "提问"}},
        freshness_tags=["x"],
        fetched_at_label="y",
    )
    assert c["content_says"] == "无转录，无法概括内容。"
    assert c["comments_reply"] == "提问"


def test_comments_failed_only():
    c = build_contrast(
        content={"pipeline_status": "ok", "content_conclusion": {"thesis": "教步骤", "video_types": ["教程"]}},
        comments={"pipeline_status": "failed"},
        freshness_tags=["x"],
        fetched_at_label="y",
    )
    assert c["comments_reply"] == "无评论样本。"

def test_content_llm_fail_uses_skip_reason():
    c = build_contrast(
        content={"pipeline_status": "failed", "skip_reason": "LLM失败: ReadTimeout: timed out"},
        comments={"pipeline_status": "ok", "comment_conclusion": {"doing": "提问"}},
        freshness_tags=["x"],
        fetched_at_label="y",
    )
    assert c["content_says"].startswith("LLM失败")
    assert "危机" not in c["content_says"]
    assert "适合合作" not in c["content_says"]


def test_comments_llm_fail_uses_noise_label():
    c = build_contrast(
        content={"pipeline_status": "ok", "content_conclusion": {"thesis": "教步骤", "video_types": ["教程"]}},
        comments={
            "pipeline_status": "failed",
            "noise": {
                "filtered_count": 7,
                "filtered_ratio": 0.03,
                "label": "噪音已过滤 7 条（约 3%）；LLM失败: ReadTimeout",
            },
        },
        freshness_tags=["x"],
        fetched_at_label="y",
    )
    assert "LLM失败" in c["comments_reply"]
    assert "危机" not in c["comments_reply"]
    assert "适合合作" not in c["comments_reply"]
