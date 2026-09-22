import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "docs" / "02-施工" / "schema" / "channel-brief-result.schema.json"
FIXTURE = ROOT / "docs" / "02-施工" / "examples" / "channel-brief-demo.json"


def test_channel_brief_fixture_passes_schema():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema).validate(data)
