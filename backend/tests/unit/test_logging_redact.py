
import logging

from app.logging_setup import SecretRedactFilter


def test_api_key_redacted(caplog):
    logger = logging.getLogger("redact-test")
    logger.addFilter(SecretRedactFilter(extra_secrets=["sk-secret-abc"]))
    with caplog.at_level(logging.INFO):
        logger.info("LLM_API_KEY=sk-secret-abc used")
        logger.info("Bearer sk-secret-abc")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "sk-secret-abc" not in text
    assert "***REDACTED***" in text
