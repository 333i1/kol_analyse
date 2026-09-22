
from app.services.url_parser import parse_youtube_video_id

VID = "dQw4w9WgXcQ"


def test_watch_v():
    assert parse_youtube_video_id(f"https://www.youtube.com/watch?v={VID}") == VID


def test_youtu_be():
    assert parse_youtube_video_id(f"https://youtu.be/{VID}") == VID


def test_extra_query():
    assert parse_youtube_video_id(f"https://www.youtube.com/watch?v={VID}&t=12&list=PLxx") == VID


def test_illegal_domain():
    assert parse_youtube_video_id("https://example.com/watch?v=" + VID) is None


def test_bad_length():
    assert parse_youtube_video_id("https://www.youtube.com/watch?v=short") is None


def test_bare_id_not_url():
    assert parse_youtube_video_id(VID) is None


def test_empty():
    assert parse_youtube_video_id("  ") is None
    assert parse_youtube_video_id(None) is None
