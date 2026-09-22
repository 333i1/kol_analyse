
from app.services.banned import FALLBACK_CONTRAST, contains_banned, rewrite_if_banned
from app.services.pipelines.contrast import build_contrast


def test_detects_crisis_and_coop():
    assert contains_banned("这是危机信号")
    assert contains_banned("该达人适合合作")
    assert not contains_banned("视频在教步骤，评论在要排障。")


def test_rewrite():
    assert rewrite_if_banned("适合合作") == FALLBACK_CONTRAST
    assert "危机" not in FALLBACK_CONTRAST
    assert "适合合作" not in FALLBACK_CONTRAST


def test_contrast_templates_no_banned():
    c = build_contrast(
        content={"pipeline_status": "ok", "content_conclusion": {"thesis": "教炒蛋", "video_types": ["教程"]}},
        comments={"pipeline_status": "ok", "comment_conclusion": {"doing": "提问报错"}, "reaction_types": [{"type": "提问"}]},
        freshness_tags=["发布距今 5 天"],
        fetched_at_label="刚才",
    )
    for k in ("content_says", "comments_reply", "contrast_sentence"):
        assert "危机" not in c[k]
        assert "适合合作" not in c[k]


def test_synthetic_banned_rewritten():
    c = build_contrast(
        content={"pipeline_status": "ok", "content_conclusion": {"thesis": "存在危机", "video_types": ["观点"]}},
        comments={"pipeline_status": "ok", "comment_conclusion": {"doing": "适合合作"}, "reaction_types": []},
        freshness_tags=["x"],
        fetched_at_label="y",
    )
    assert c["content_says"] == FALLBACK_CONTRAST
    assert c["comments_reply"] == FALLBACK_CONTRAST
