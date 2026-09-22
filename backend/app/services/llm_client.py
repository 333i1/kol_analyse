
"""OpenAI-compatible chat/completions. Fail at call site if no key. Retry 0.

Spec: 失败重试 0 次 — docs/01-设计/03-技术选型.md R6; docs/02-施工/04-实现计划.md
LLM contracts; docs/02-施工/06-tasks.md T011 (do not bring old max_retries=6).
HTTP 429 / Zhipu 1305 is included: no retry. User-facing errors stay short
(e.g. 智谱繁忙(429)); wait ~30s and re-POST. Failed tasks are not 24h
completed-cache hits; degraded (one pipeline ok) results are.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import budget as budget_mod
from app.services.llm_json import loads_json_lenient

logger = logging.getLogger(__name__)

BILLED_STAGES = frozenset({"llm_content", "llm_comments"})


class LLMError(Exception):
    pass


def _llm_http_client(settings) -> httpx.Client:
    """GLM client: never inherit process HTTP(S)_PROXY (those are for YouTube)."""
    timeout_s = float(getattr(settings, "LLM_TIMEOUT", 120) or 120)
    return httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=timeout_s, write=30.0, pool=10.0),
        trust_env=False,
    )


def _zhipu_error_code(body: str) -> str | None:
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:
        data = None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("code") is not None:
            return str(err.get("code"))
    if "1305" in body:
        return "1305"
    return None


def _provider_error_message(body: str) -> str | None:
    """JSON error.message only. Never return raw body / URLs."""
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    msg = None
    if isinstance(err, dict):
        msg = err.get("message")
    elif isinstance(err, str):
        msg = err
    if not isinstance(msg, str) or not msg.strip():
        return None
    low = msg.lower()
    if "restricted to true" in low or "enable_thinking parameter is restricted" in low:
        return "该模型不能关思考"
    if "must contain the word" in low and "json" in low:
        return "提示词需含 json"
    if "json mode" in low and "enable_thinking" in low:
        return "思考模式不支持 json_object"
    cleaned = " ".join(msg.split())
    if "http://" in cleaned.lower() or "https://" in cleaned.lower():
        return None
    return cleaned[:48]


def _short_http_status_error(exc: httpx.HTTPStatusError, body: str = "") -> str:
    """User-facing LLM HTTP failure. No URL dump, no HTTPStatusError repr."""
    status = None
    try:
        status = exc.response.status_code
    except Exception:
        status = None
    zhipu = _zhipu_error_code(body)
    if status == 429 or zhipu == "1305":
        return "智谱繁忙(429)"
    snippet = _provider_error_message(body)
    if status == 400 and snippet:
        return f"HTTP 400: {snippet}"
    if status is not None:
        return f"HTTP {status}"
    return "HTTP 错误"


def _message_json_candidates(message: dict[str, Any]) -> list[Any]:
    """Collect JSON-bearing fields from a chat message (content / reasoning_content / parts)."""
    out: list[Any] = []
    content = message.get("content")
    if isinstance(content, dict):
        out.append(content)
    elif isinstance(content, str) and content.strip():
        out.append(content)
    elif isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content") or ""
                if isinstance(text, dict):
                    out.append(text)
                elif isinstance(text, str) and text.strip():
                    parts.append(text)
            elif isinstance(part, str) and part.strip():
                parts.append(part)
        if parts:
            out.append("\n".join(parts))
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, dict):
        out.append(reasoning)
    elif isinstance(reasoning, str) and reasoning.strip():
        out.append(reasoning)
    return out


def _parse_message_json(data: dict[str, Any]) -> Any:
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("LLM empty choices")
    message = (choices[0] or {}).get("message") or {}
    if not isinstance(message, dict):
        message = {}
    candidates = _message_json_candidates(message)
    if not candidates:
        raise LLMError("LLM empty message.content (no JSON in content or reasoning_content)")
    last_err: Exception | None = None
    for cand in candidates:
        if isinstance(cand, (dict, list)):
            return cand
        if not isinstance(cand, str):
            continue
        try:
            return loads_json_lenient(cand)
        except Exception as e:
            last_err = e
    raise LLMError(
        f"LLM JSON parse failed: {type(last_err).__name__}: {last_err}"
    ) from last_err



def _space_zhipu_calls(settings) -> bool:
    """Sleep LLM_INTER_CALL_DELAY_S only for Zhipu/GLM (bigmodel URL or glm model).

    DashScope/Qwen should not sleep. Sequential Zhipu tests still space billed calls.
    """
    base = (getattr(settings, "LLM_BASE_URL", None) or "").lower()
    model = (getattr(settings, "LLM_MODEL", None) or "").lower()
    return "bigmodel" in base or "zhipu" in base or model.startswith("glm")


def _thinking_only_qwen(model: str) -> bool:
    """qwen3.8-max family rejects enable_thinking=false (HTTP 400 restricted to True)."""
    m = (model or "").lower().strip()
    return m.startswith("qwen3.8-max")


def _disable_thinking(payload: dict[str, Any], settings) -> None:
    """Turn thinking off on hybrid models. Skip thinking-only ids (qwen3.8-max).

    DashScope compatible-mode: enable_thinking=false (not OpenAI SDK extra_body).
    Zhipu GLM-4.7: thinking.type=disabled. Unknown vendors: leave payload alone
    so OpenAI-strict endpoints do not 400 on extra fields.
    """
    base = (getattr(settings, "LLM_BASE_URL", None) or "").lower()
    model = (getattr(settings, "LLM_MODEL", None) or "").lower()
    if _thinking_only_qwen(model):
        return
    if "dashscope" in base or "aliyun" in base or "qwen" in model:
        payload["enable_thinking"] = False
    elif "bigmodel" in base or "zhipu" in base or model.startswith("glm"):
        payload["thinking"] = {"type": "disabled"}

class LLMClient:
    def __init__(self, settings=None, http: Optional[httpx.Client] = None):
        self.settings = settings or get_settings()
        self._http = http
        self.call_count = 0
        self._db_lock = threading.Lock()
        # In-flight estimates so two parallel complete() calls cannot both see spent=0.
        self._reserved_usd: dict[str, float] = {}

    def complete(
        self,
        *,
        db: Session,
        task_id: str,
        video_id: str,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        expected_input_tokens: int = 800,
        expected_output_tokens: int = 800,
    ) -> dict[str, Any]:
        if stage not in BILLED_STAGES:
            raise LLMError(f"stage {stage} is not a billed LLM stage")
        if not self.settings.LLM_API_KEY:
            raise LLMError("LLM_API_KEY missing")

        estimate = budget_mod.estimate_llm(expected_input_tokens, expected_output_tokens)
        reserved_held = False
        with self._db_lock:
            reserved = float(self._reserved_usd.get(task_id, 0.0) or 0.0)
            budget_mod.check_or_stop(
                db,
                task_id=task_id,
                video_id=video_id,
                stage=stage,
                estimate_usd=estimate,
                extra_spent=reserved,
            )
            self._reserved_usd[task_id] = reserved + float(estimate)
            reserved_held = True
            # Increment under the lock so two parallel calls cannot both see count==0.
            prior = self.call_count
            self.call_count += 1

        url = self.settings.LLM_BASE_URL.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.LLM_API_KEY}",
            "Content-Type": "application/json",
        }
        temperature = float(getattr(self.settings, "LLM_TEMPERATURE", 0.2) or 0.0)
        payload = {
            "model": self.settings.LLM_MODEL,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        _disable_thinking(payload, self.settings)
        # Space billed calls on the same client for Zhipu/GLM free-tier 429.
        # DashScope/Qwen: no inter-call sleep. Spec still forbids retries.
        delay_s = float(getattr(self.settings, "LLM_INTER_CALL_DELAY_S", 0) or 0)
        if delay_s > 0 and prior > 0 and _space_zhipu_calls(self.settings):
            time.sleep(delay_s)
        client = self._http or _llm_http_client(self.settings)
        close_client = self._http is None
        try:
            try:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                body = ""
                try:
                    body = (e.response.text or "")[:300]
                except Exception:
                    body = ""
                status = getattr(e.response, "status_code", None)
                logger.warning("LLM %s HTTP %s: %s", stage, status, type(e).__name__)
                raise LLMError(_short_http_status_error(e, body)) from e
            except httpx.TimeoutException as e:
                logger.warning("LLM %s timeout: %s", stage, type(e).__name__)
                raise LLMError(type(e).__name__) from e
            except Exception as e:
                logger.warning("LLM %s failed: %s: %s", stage, type(e).__name__, e)
                raise LLMError(f"{type(e).__name__}: {e}") from e
            finally:
                if close_client:
                    client.close()

            parsed = _parse_message_json(data)
            if not isinstance(parsed, dict):
                raise LLMError("LLM JSON parse failed: expected object")

            usage = data.get("usage") or {}
            in_tok = int(usage.get("prompt_tokens") or expected_input_tokens)
            out_tok = int(usage.get("completion_tokens") or expected_output_tokens)
            usd = budget_mod.estimate_llm(in_tok, out_tok)
            with self._db_lock:
                budget_mod.record(
                    db,
                    task_id=task_id,
                    video_id=video_id,
                    stage=stage,
                    vendor="openai-compatible",
                    usd_amount=usd,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                )
                self._reserved_usd[task_id] = max(
                    0.0,
                    float(self._reserved_usd.get(task_id, 0.0) or 0.0) - float(estimate),
                )
                reserved_held = False
            return parsed
        finally:
            if reserved_held:
                with self._db_lock:
                    self._reserved_usd[task_id] = max(
                        0.0,
                        float(self._reserved_usd.get(task_id, 0.0) or 0.0) - float(estimate),
                    )
