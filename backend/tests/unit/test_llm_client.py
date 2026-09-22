from types import SimpleNamespace

import httpx

from app.services.llm_client import (
    LLMClient,
    LLMError,
    _llm_http_client,
    _parse_message_json,
    _short_http_status_error,
    _zhipu_error_code,
)


def _settings(**kwargs):
    base = dict(
        LLM_API_KEY="test-key",
        LLM_BASE_URL="https://example.invalid/v1",
        LLM_MODEL="glm-test",
        LLM_TIMEOUT=90,
        LLM_TEMPERATURE=0.2,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _patch_budget(monkeypatch):
    monkeypatch.setattr("app.services.llm_client.budget_mod.check_or_stop", lambda *a, **k: None)
    monkeypatch.setattr("app.services.llm_client.budget_mod.estimate_llm", lambda *a, **k: 0.0)
    monkeypatch.setattr("app.services.llm_client.budget_mod.record", lambda *a, **k: None)


def test_llm_http_client_trust_env_false_and_timeout():
    client = _llm_http_client(_settings(LLM_TIMEOUT=90))
    try:
        assert client.trust_env is False
        assert float(client.timeout.read) == 90.0
        assert float(client.timeout.connect) == 10.0
        assert float(client.timeout.write) == 30.0
        assert float(client.timeout.pool) == 10.0
    finally:
        client.close()


def test_parse_reasoning_content_when_content_empty():
    data = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "reasoning_content": '{"ok": true, "n": 1}',
                }
            }
        ]
    }
    assert _parse_message_json(data) == {"ok": True, "n": 1}


def test_parse_list_content_parts():
    data = {
        "choices": [
            {
                "message": {
                    "content": [{"type": "text", "text": '{"ok": true}'}],
                }
            }
        ]
    }
    assert _parse_message_json(data) == {"ok": True}


def test_parse_empty_raises_clear_error():
    data = {"choices": [{"message": {"content": ""}}]}
    try:
        _parse_message_json(data)
        raise AssertionError("expected LLMError")
    except LLMError as e:
        assert "empty" in str(e).lower()


class _FakeHttp:
    def __init__(self, resp):
        self._resp = resp
        self.last_json = None
        self.calls = 0

    def post(self, url, headers=None, json=None):
        self.last_json = json
        self.calls += 1
        return self._resp


class _SeqHttp:
    def __init__(self, resps):
        self._resps = list(resps)
        self.calls = 0
        self.last_json = None

    def post(self, url, headers=None, json=None):
        self.last_json = json
        self.calls += 1
        return self._resps.pop(0)


def _ok_resp():
    class Resp:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"hello": 1}'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }

    return Resp()


def _status_resp(code: int, text: str, url: str = "https://example.invalid/v1/chat/completions"):
    req = httpx.Request("POST", url)
    real = httpx.Response(code, text=text, request=req)

    class Resp:
        status_code = code
        text = real.text

        def raise_for_status(self):
            raise httpx.HTTPStatusError(str(code), request=req, response=real)

        def json(self):
            return {}

    return Resp()


def _complete(client: LLMClient, stage: str = "llm_content"):
    return client.complete(
        db=None,
        task_id="t",
        video_id="dQw4w9WgXcQ",
        stage=stage,
        system_prompt="s",
        user_prompt="u",
    )


def test_complete_uses_temperature_and_injected_http(monkeypatch):
    _patch_budget(monkeypatch)

    http = _FakeHttp(_ok_resp())
    client = LLMClient(settings=_settings(LLM_TEMPERATURE=0.2), http=http)
    parsed = _complete(client)
    assert parsed == {"hello": 1}
    assert http.last_json["temperature"] == 0.2


def test_http_status_error_is_short_no_body_dump(monkeypatch):
    _patch_budget(monkeypatch)

    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    http = _FakeHttp(_status_resp(502, "upstream exploded " + ("x" * 400), url=url))
    client = LLMClient(settings=_settings(), http=http)
    try:
        _complete(client, stage="llm_comments")
        raise AssertionError("expected LLMError")
    except LLMError as e:
        msg = str(e)
        assert msg == "HTTP 502"
        assert "HTTPStatusError" not in msg
        assert "upstream exploded" not in msg
        assert "open.bigmodel" not in msg
        assert len(msg) < 80


def test_429_not_retried_even_if_200_queued(monkeypatch):
    _patch_budget(monkeypatch)
    body = '{"error":{"code":"1305","message":"该模型当前访问量过大，请您稍后再试"}}'
    http = _SeqHttp(
        [
            _status_resp(429, body, url="https://open.bigmodel.cn/api/paas/v4/chat/completions"),
            _ok_resp(),
        ]
    )
    client = LLMClient(settings=_settings(), http=http)
    try:
        _complete(client, stage="llm_comments")
        raise AssertionError("expected LLMError")
    except LLMError as e:
        msg = str(e)
        assert msg == "智谱繁忙(429)"
        assert "HTTPStatusError" not in msg
        assert "open.bigmodel" not in msg
        assert "1305" not in msg
    assert http.calls == 1


def test_timeout_not_retried(monkeypatch):
    _patch_budget(monkeypatch)

    class TimeoutHttp:
        def __init__(self):
            self.calls = 0

        def post(self, url, headers=None, json=None):
            self.calls += 1
            raise httpx.ReadTimeout("The read operation timed out")

    http = TimeoutHttp()
    client = LLMClient(settings=_settings(), http=http)
    try:
        _complete(client)
        raise AssertionError("expected LLMError")
    except LLMError as e:
        msg = str(e)
        assert msg == "ReadTimeout"
        assert "HTTPStatusError" not in msg
    assert http.calls == 1


def test_inter_call_delay_only_on_second_complete(monkeypatch):
    _patch_budget(monkeypatch)
    sleeps = []
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda s: sleeps.append(s))
    http = _SeqHttp([_ok_resp(), _ok_resp()])
    client = LLMClient(settings=_settings(LLM_INTER_CALL_DELAY_S=1.5), http=http)
    _complete(client, stage="llm_content")
    _complete(client, stage="llm_comments")
    assert sleeps == [1.5]
    assert http.calls == 2


def test_no_inter_call_delay_when_setting_absent(monkeypatch):
    _patch_budget(monkeypatch)
    sleeps = []
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda s: sleeps.append(s))
    http = _SeqHttp([_ok_resp(), _ok_resp()])
    client = LLMClient(settings=_settings(), http=http)
    _complete(client, stage="llm_content")
    _complete(client, stage="llm_comments")
    assert sleeps == []
    assert http.calls == 2


def test_short_http_helpers():
    assert _zhipu_error_code('{"error":{"code":"1305","message":"busy"}}') == "1305"
    req = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    real = httpx.Response(429, text='{"error":{"code":"1305"}}', request=req)
    err = httpx.HTTPStatusError("429", request=req, response=real)
    assert _short_http_status_error(err, real.text) == "智谱繁忙(429)"

def test_qwen_hybrid_enable_thinking_false(monkeypatch):
    _patch_budget(monkeypatch)
    http = _FakeHttp(_ok_resp())
    client = LLMClient(
        settings=_settings(
            LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
            LLM_MODEL="qwen-plus",
        ),
        http=http,
    )
    _complete(client)
    assert http.last_json["enable_thinking"] is False
    assert "thinking" not in http.last_json


def test_qwen38_max_omits_enable_thinking(monkeypatch):
    _patch_budget(monkeypatch)
    http = _FakeHttp(_ok_resp())
    client = LLMClient(
        settings=_settings(
            LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
            LLM_MODEL="qwen3.8-max",
        ),
        http=http,
    )
    _complete(client)
    assert "enable_thinking" not in http.last_json
    assert "thinking" not in http.last_json


def test_glm_thinking_disabled(monkeypatch):
    _patch_budget(monkeypatch)
    http = _FakeHttp(_ok_resp())
    client = LLMClient(
        settings=_settings(
            LLM_BASE_URL="https://open.bigmodel.cn/api/paas/v4",
            LLM_MODEL="glm-4.7-flash",
        ),
        http=http,
    )
    _complete(client)
    assert http.last_json["thinking"] == {"type": "disabled"}
    assert "enable_thinking" not in http.last_json


def test_unknown_vendor_no_thinking_field(monkeypatch):
    _patch_budget(monkeypatch)
    http = _FakeHttp(_ok_resp())
    client = LLMClient(
        settings=_settings(LLM_BASE_URL="https://api.openai.com/v1", LLM_MODEL="gpt-4o-mini"),
        http=http,
    )
    _complete(client)
    assert "enable_thinking" not in http.last_json
    assert "thinking" not in http.last_json

def test_400_thinking_only_is_short():
    req = httpx.Request("POST", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
    body = '{"error":{"message":"The value of the enable_thinking parameter is restricted to True."}}'
    real = httpx.Response(400, text=body, request=req)
    err = httpx.HTTPStatusError("400", request=req, response=real)
    msg = _short_http_status_error(err, real.text)
    assert msg == "HTTP 400: 该模型不能关思考"
    assert "dashscope" not in msg
    assert len(msg) < 80

def test_inter_call_delay_skipped_for_dashscope_qwen(monkeypatch):
    _patch_budget(monkeypatch)
    sleeps = []
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda s: sleeps.append(s))
    http = _SeqHttp([_ok_resp(), _ok_resp()])
    client = LLMClient(
        settings=_settings(
            LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
            LLM_MODEL="qwen3.8-max",
            LLM_INTER_CALL_DELAY_S=1.5,
        ),
        http=http,
    )
    _complete(client, stage="llm_content")
    _complete(client, stage="llm_comments")
    assert sleeps == []
    assert http.calls == 2


def test_inter_call_delay_applied_for_bigmodel(monkeypatch):
    _patch_budget(monkeypatch)
    sleeps = []
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda s: sleeps.append(s))
    http = _SeqHttp([_ok_resp(), _ok_resp()])
    client = LLMClient(
        settings=_settings(
            LLM_BASE_URL="https://open.bigmodel.cn/api/paas/v4",
            LLM_MODEL="glm-4.7-flash",
            LLM_INTER_CALL_DELAY_S=1.5,
        ),
        http=http,
    )
    _complete(client, stage="llm_content")
    _complete(client, stage="llm_comments")
    assert sleeps == [1.5]
    assert http.calls == 2
