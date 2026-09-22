# -*- coding: utf-8 -*-
import json

import pytest

from app.services.llm_client import LLMError, _parse_message_json
from app.services.llm_json import loads_json_lenient


def test_extra_data_trailing_prose():
    raw = '{\n  "a": 1,\n  "b": "x"\n}\n这是说明文字\n更多尾巴'
    # plain loads fails
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    assert loads_json_lenient(raw) == {"a": 1, "b": "x"}


def test_extra_data_second_object():
    raw = '{"ok": true}\n{"extra": 2}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    assert loads_json_lenient(raw) == {"ok": True}


def test_markdown_fence():
    raw = '```json\n{"thesis": "hello"}\n```\n'
    assert loads_json_lenient(raw) == {"thesis": "hello"}


def test_leading_junk_then_object():
    raw = 'Here is the JSON:\n{"n": 3}\n'
    assert loads_json_lenient(raw) == {"n": 3}


def test_array_with_trailing():
    raw = '[1, 2, 3]\nnote'
    assert loads_json_lenient(raw) == [1, 2, 3]


def test_parse_message_json_uses_lenient():
    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"video_types":["评测"],"thesis":"t"}\nExtra line 43 stuff'
                }
            }
        ]
    }
    out = _parse_message_json(payload)
    assert out["thesis"] == "t"
    assert out["video_types"] == ["评测"]


def test_parse_message_json_still_fails_on_garbage():
    payload = {"choices": [{"message": {"content": "not json at all"}}]}
    with pytest.raises(LLMError) as ei:
        _parse_message_json(payload)
    assert "LLM JSON parse failed" in str(ei.value)
