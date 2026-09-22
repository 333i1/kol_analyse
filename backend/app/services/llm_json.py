# -*- coding: utf-8 -*-
"""Lenient JSON extraction for LLM chat content (trailing Extra data, fences)."""
from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\r?\n([\s\S]*?)\r?\n```\s*",
    re.MULTILINE,
)


def loads_json_lenient(text: str) -> Any:
    """Parse the first JSON value from a model string.

    Handles common LLM messiness that plain json.loads rejects:
    - trailing prose / second object after a valid JSON value (Extra data)
    - optional ```json ... ``` fences
    - leading junk before the first { or [
    """
    if not isinstance(text, str):
        raise TypeError("loads_json_lenient expects str")
    s = text.strip()
    if not s:
        raise json.JSONDecodeError("Expecting value", s, 0)

    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()
    else:
        if s.startswith("```"):
            nl = s.find("\n")
            if nl >= 0:
                s = s[nl + 1 :]
            if s.rstrip().endswith("```"):
                s = s.rstrip()[:-3].rstrip()

    try:
        return json.loads(s)
    except json.JSONDecodeError as first_err:
        decoder = json.JSONDecoder()
        for start_char in ("{", "["):
            idx = s.find(start_char)
            if idx < 0:
                continue
            try:
                obj, _end = decoder.raw_decode(s, idx)
                return obj
            except json.JSONDecodeError:
                continue
        raise first_err
