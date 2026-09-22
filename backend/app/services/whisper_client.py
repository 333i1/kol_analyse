
"""Local faster-whisper. device=auto does NOT auto-fallback; wrap construct+transcribe."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import get_settings
from app.services.transcript_client import Cue, TranscriptResult

logger = logging.getLogger(__name__)


@dataclass
class WhisperClient:
    settings: Any = None
    model_cls: Any = None
    _asr_model: Any = field(default=None, init=False, repr=False)
    transcribe_calls: int = 0

    def __post_init__(self):
        self.settings = self.settings or get_settings()

    def enabled(self) -> bool:
        return bool(self.settings.WHISPER_ENABLED)

    def transcribe(self, audio_file: str) -> TranscriptResult:
        if not self.enabled():
            return TranscriptResult(ok=False, error_msg="Whisper 兜底已按 WHISPER_ENABLED=0 关闭")
        self.transcribe_calls += 1
        try:
            segments, info = self._transcribe_with_fallback(audio_file)
            cues: list[Cue] = []
            for seg in segments:
                start = float(getattr(seg, "start", 0.0) or 0.0)
                end = float(getattr(seg, "end", 0.0) or start)
                text = (getattr(seg, "text", "") or "").replace("\n", " ").strip()
                if not text:
                    continue
                cues.append(Cue(start=start, duration=max(end - start, 0.0), text=text))
            if not cues:
                raise ValueError("Whisper 转写结果为空")
            return TranscriptResult(
                ok=True,
                cues=cues,
                text=" ".join(c.text for c in cues),
                language=str(getattr(info, "language", "unknown")),
            )
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            logger.warning("whisper failed: %s", msg)
            return TranscriptResult(ok=False, error_msg=msg)

    def _get_model_cls(self):
        if self.model_cls is not None:
            return self.model_cls
        from faster_whisper import WhisperModel

        return WhisperModel

    def _transcribe_with_fallback(self, audio_file: str) -> tuple[list, Any]:
        """Wrap construct + transcribe. CUDA auto does not fall back; retry cpu+int8."""
        cls = self._get_model_cls()
        device = self.settings.WHISPER_DEVICE
        compute = self.settings.WHISPER_COMPUTE_TYPE
        if self._asr_model is not None:
            return self._transcribe(self._asr_model, audio_file)

        model = cls(self.settings.WHISPER_MODEL, device=device, compute_type=compute)
        try:
            result = self._transcribe(model, audio_file)
        except Exception as e:
            if device == "cpu":
                raise
            logger.warning(
                "Whisper 按 device=%s 推理失败（%s: %s），回退 CPU 重建模型重试",
                device,
                type(e).__name__,
                e,
            )
            model = cls(self.settings.WHISPER_MODEL, device="cpu", compute_type="int8")
            result = self._transcribe(model, audio_file)
        self._asr_model = model
        return result

    def _transcribe(self, model: Any, audio_file: str) -> tuple[list, Any]:
        lang = self.settings.WHISPER_LANGUAGE or None
        segments, info = model.transcribe(
            audio_file, language=lang, beam_size=5, vad_filter=True
        )
        return list(segments), info
