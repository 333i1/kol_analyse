from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FRONTEND = REPO / "frontend"
ADAPTER_JS = FRONTEND / "result-adapter.js"
INDEX_HTML = FRONTEND / "index.html"
NODE_TEST = FRONTEND / "result-adapter.test.js"


def test_node_result_adapter():
    assert NODE_TEST.is_file()
    proc = subprocess.run(
        ["node", str(NODE_TEST)],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "all passed" in proc.stdout


def test_adapter_null_guard():
    src = ADAPTER_JS.read_text(encoding="utf-8")
    assert "function adapt(result)" in src
    assert "if (!result) return null" in src


def test_index_html_cache_hit_null_checks_result():
    html = INDEX_HTML.read_text(encoding="utf-8")
    first = html.index("body.cache_hit")
    cache = html[first : html.index("if (res.status === 202)", first)]
    assert "adaptAnalysisResult(body.result)" in cache
    assert "if (!adapted)" in cache