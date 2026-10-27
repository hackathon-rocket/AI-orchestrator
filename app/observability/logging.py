import json
import logging
import time

logger = logging.getLogger("egrant.metadata")


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # SDK debugging can contain document data. Application logs only metadata.
    for name in ["httpx", "httpcore", "openai", "pydantic_ai"]:
        logging.getLogger(name).setLevel(logging.CRITICAL)


def event(
    request_id: str,
    stage: str,
    *,
    document_id: str | None = None,
    provider: str | None = None,
    duration_ms: float | None = None,
    error: str | None = None,
) -> None:
    logger.info(
        json.dumps(
            {
                "timestamp": time.time(),
                "request_id": request_id,
                "document_id": document_id,
                "stage": stage,
                "duration_ms": duration_ms,
                "provider": provider,
                "error_category": error,
            },
            ensure_ascii=True,
        )
    )
