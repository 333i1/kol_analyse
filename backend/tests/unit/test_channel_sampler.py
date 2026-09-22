from app.services.channel_sampler import SampleVideo, sample_recent_and_hot


def test_recent3_hot2_dedupe():
    items = []
    for i in range(10):
        vid = f"id{i:09d}"
        items.append(SampleVideo(video_id=vid, view_count=10 + i * 100))
    res = sample_recent_and_hot(items, window_size=10)
    assert [v.video_id for v in res.recent] == ["id000000000", "id000000001", "id000000002"]
    assert [v.video_id for v in res.hot] == ["id000000009", "id000000008"]
    assert len(res.video_ids) == 5


def test_short_channel_gaps():
    items = [
        SampleVideo(video_id="id000000000", view_count=1),
        SampleVideo(video_id="id000000001", view_count=2),
    ]
    res = sample_recent_and_hot(items, window_size=50)
    assert len(res.recent) == 2
    assert len(res.hot) == 0
    assert any("最热" in g for g in res.gap_notes)


def test_empty():
    res = sample_recent_and_hot([])
    assert res.video_ids == []
    assert res.gap_notes
