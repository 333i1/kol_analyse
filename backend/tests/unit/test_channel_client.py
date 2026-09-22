from app.services.channel_client import ChannelClient
from app.services.channel_sampler import sample_recent_and_hot


class FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        if "channels" in url:
            return FakeResp(self.routes["channels"])
        if "playlistItems" in url:
            return FakeResp(self.routes["playlistItems"])
        if "videos" in url:
            return FakeResp(self.routes["videos"])
        raise AssertionError(url)

    def close(self):
        return None


class Settings:
    YOUTUBE_API_KEY = "replace-me"

    def proxy_url(self):
        return None


def test_resolve_and_sample_pipeline():
    routes = {
        "channels": {
            "items": [
                {
                    "id": "UC" + "a" * 22,
                    "snippet": {"title": "Demo", "customUrl": "@Demo"},
                    "statistics": {
                        "subscriberCount": "10",
                        "viewCount": "100",
                        "videoCount": "5",
                    },
                    "contentDetails": {"relatedPlaylists": {"uploads": "UUaaaaaaaa"}},
                }
            ]
        },
        "playlistItems": {
            "items": [
                {
                    "contentDetails": {"videoId": f"id{i:09d}", "videoPublishedAt": "2026-01-01"},
                    "snippet": {"title": f"t{i}"},
                }
                for i in range(5)
            ]
        },
        "videos": {
            "items": [
                {
                    "id": f"id{i:09d}",
                    "statistics": {"viewCount": str(100 * (i + 1))},
                    "snippet": {"title": f"t{i}", "publishedAt": "2026-01-01"},
                    "contentDetails": {"duration": "PT1M"},
                }
                for i in range(5)
            ]
        },
    }
    http = FakeHttp(routes)
    client = ChannelClient(settings=Settings(), http=http)
    ch, samples = client.fetch_sample_candidates(handle="Demo", window_size=5)
    assert ch["channel_id"].startswith("UC")
    assert len(samples) == 5
    res = sample_recent_and_hot(samples, window_size=5)
    assert len(res.recent) == 3
    assert len(res.hot) == 2
    assert client.channels_list_calls == 1
