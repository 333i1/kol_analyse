
from app.services.noise import (
    NOISE_DUPLICATE,
    NOISE_PURE_URL,
    NOISE_REPEATED_CHAR,
    NOISE_TOO_SHORT,
    preprocess_comment,
    preprocess_comments,
)


def test_pure_url():
    r = preprocess_comment({"comment_id": "1", "text": "https://spam.example/x"})
    assert r.is_noise and r.noise_reason == NOISE_PURE_URL


def test_too_short_keeps_two_chars():
    r = preprocess_comment({"comment_id": "1", "text": "好看"})
    assert not r.is_noise
    r2 = preprocess_comment({"comment_id": "2", "text": "好"})
    assert r2.is_noise and r2.noise_reason == NOISE_TOO_SHORT


def test_repeated_char():
    r = preprocess_comment({"comment_id": "1", "text": "啊" * 8})
    assert r.is_noise and r.noise_reason == NOISE_REPEATED_CHAR


def test_pure_symbol():
    r = preprocess_comment({"comment_id": "1", "text": "!!!!????"})
    assert r.is_noise


def test_duplicate_isolated_per_video():
    comments = [
        {"comment_id": "a", "text": "火候不对"},
        {"comment_id": "b", "text": "火候不对"},
    ]
    out = preprocess_comments(comments, video_id="vid1")
    assert out[0].is_noise is False
    assert out[1].is_noise and out[1].noise_reason == NOISE_DUPLICATE
    other = preprocess_comments(comments, video_id="vid2")
    assert other[0].is_noise is False


def test_no_buy_intent_whitelist():
    from app.services import noise as noise_mod

    text = open(noise_mod.__file__, encoding="utf-8").read()
    assert "BUY_INTENT" not in text
    assert "has_buy_intent" not in text
    assert "_BUY_" not in text
