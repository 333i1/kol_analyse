from pathlib import Path

from app.config import get_settings

PROMPT_PATH = Path(__file__).resolve().parents[2] / "app" / "prompts" / "comments_v1.txt"


def test_comments_prompt_version_is_v1_2():
    get_settings.cache_clear()
    assert get_settings().PROMPT_VERSION_COMMENTS == "comments-v1.2"


def test_comments_v1_contains_decision_rules():
    text = PROMPT_PATH.read_text(encoding="utf-8")
    assert "拿不准" in text
    assert "明确敌意" in text
    assert "提问" in text
    assert "复述" in text
    assert "轻抱怨" in text
    assert "玩梗" in text
    assert "讽刺" in text
    assert "RIP" in text
    assert "悲伤" in text
    assert "话题回声" in text
    assert "时长" in text
    assert "混合歧义" in text
    assert "正面" in text and "中性" in text and "负面" in text
    # still schema polarity only — no invented labels
    for banned_label in ("偏负", "弱负", "混合", "未知"):
        assert f'"{banned_label}"' not in text
    assert "老师" in text
    assert "学校" in text
    assert "制度" in text
    assert "时事" in text
    assert "没看懂" in text
    assert "still dont get it" in text
    assert "this explained nothing" in text
    assert "还是不会" in text
    assert "方言" in text
    assert "恰烂钱" in text
    assert "前途" in text
