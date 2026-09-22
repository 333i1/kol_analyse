
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "docs" / "02-施工" / "examples"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs" / "02-施工" / "schema" / "video-analysis-result.schema.json"


def test_three_examples_pass_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    files = sorted(f for f in EXAMPLES_DIR.glob("*.json") if not f.name.startswith("channel-"))
    assert len(files) == 3
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        jsonschema.validate(data, schema)
        assert "selling_points" not in json.dumps(data, ensure_ascii=False)
