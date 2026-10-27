"""Content identifiers, not signatures or authorization decisions."""

import hashlib
import json

from app.schemas.mapping import InlineMapping


def json_fingerprint(payload: object) -> str:
    """Sorted object keys, UTF-8, compact JSON; array ordering remains significant."""
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def mapping_fingerprint(mapping: InlineMapping) -> str:
    # schema_file only locates a source. Its resolved content has a separate hash.
    # Keep every other validated field, including declared version and defaults.
    return json_fingerprint(mapping.model_dump(mode="json", exclude={"schema_file"}))
