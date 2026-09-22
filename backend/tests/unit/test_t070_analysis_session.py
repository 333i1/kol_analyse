from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FRONTEND = REPO / "frontend"
MAIN_PY = REPO / "backend" / "app" / "main.py"
SESSION_JS = FRONTEND / "analysis-session.js"
INDEX_HTML = FRONTEND / "index.html"
NODE_TEST = FRONTEND / "analysis-session.test.js"


def test_node_analysis_session():
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


def test_session_js_locked_numbers():
    src = SESSION_JS.read_text(encoding="utf-8")
    assert "var POLL_CAP_MS = 600000" in src
    assert "var TRANSIENT_RETRY_LIMIT = 3" in src
    assert "AbortController" in src
    assert "generation" in src
    assert "setInterval" not in src


def test_index_html_uses_session_no_second_poller():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert ('src="analysis-session.js"' in html) or ('src="/analysis-session.js"' in html)
    assert "createAnalysisSession" in html
    assert "analysisSession" in html
    assert "setInterval" not in html
    assert "analyzingTimer" not in html
    assert "goBtn.disabled" in html

    start = html.index("async function openHistoryRecord")
    end = html.index("async function refreshCurrent")
    hist = html[start:end]
    assert "analysisSession.abort" in hist
    assert "input.value" not in hist
    assert "urlInput" not in hist

    submit_start = html.index("async function submit")
    submit_end = html.index("function relativeTime")
    submit = html[submit_start:submit_end]
    assert "goBtn.disabled" in submit
    assert "done.cancelled" in submit
    assert "analysisSession.abort" in submit

    rstart = html.index("async function refreshCurrent")
    rend = html.index('document.getElementById("histBtn").addEventListener')
    refresh = html[rstart:rend]
    assert "analysisSession.abort" in refresh
    assert "done.cancelled" in refresh


def test_index_html_history_never_fills_url_anywhere_except_chips():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assigns = re.findall(r"input\.value\s*=", html)
    assert len(assigns) == 1
    chip_idx = html.index("input.value = ch.getAttribute")
    hist_idx = html.index("async function openHistoryRecord")
    assert chip_idx > hist_idx


def test_main_py_serves_analysis_session_js():
    src = MAIN_PY.read_text(encoding="utf-8")
    assert "/analysis-session.js" in src
    assert "analysis-session.js" in src
