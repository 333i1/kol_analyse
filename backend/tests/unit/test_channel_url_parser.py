from app.services.channel_url_parser import parse_youtube_channel_ref


def test_channel_id_bare():
    cid = "UC" + ("x" * 22)
    r = parse_youtube_channel_ref(cid)
    assert r and r.channel_id == cid


def test_handle_url_and_at():
    r = parse_youtube_channel_ref("https://www.youtube.com/@ApertureLab")
    assert r and r.handle == "ApertureLab"
    r2 = parse_youtube_channel_ref("@ApertureLab")
    assert r2 and r2.handle == "ApertureLab"


def test_channel_path():
    cid = "UC" + ("a" * 22)
    r = parse_youtube_channel_ref(f"https://youtube.com/channel/{cid}")
    assert r and r.channel_id == cid


def test_invalid_video_url_not_channel():
    assert parse_youtube_channel_ref("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None


def test_empty():
    assert parse_youtube_channel_ref("") is None
    assert parse_youtube_channel_ref(None) is None
