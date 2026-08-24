"""Canonical JSON encoding for operational integrity contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _string(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("canonical strings must be valid Unicode") from None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _encode(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("canonical integers exceed the I-JSON safe range")
        return str(value)
    if type(value) is float:
        raise ValueError("floats are not allowed in canonical JSON data")
    if type(value) is str:
        return _string(value)
    if type(value) is list:
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("canonical mapping keys must be strings")
        for key in value:
            try:
                key.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("canonical mapping keys must be valid Unicode") from None
        keys = sorted(value, key=lambda key: key.encode("utf-16be"))
        return "{" + ",".join(f"{_string(key)}:{_encode(value[key])}" for key in keys) + "}"
    raise ValueError("value is outside the canonical JSON profile")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a JSON-compatible value deterministically as canonical UTF-8 bytes."""
    return _encode(value).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 digest of canonical JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
