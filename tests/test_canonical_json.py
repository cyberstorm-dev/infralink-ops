from __future__ import annotations

import hashlib

import pytest


def test_encodes_canonical_json_and_sha256() -> None:
    from infralink_ops.canonical_json import canonical_json_bytes, canonical_sha256

    value = {"z": [True, None], "a": "value"}

    assert canonical_json_bytes(value) == b'{"a":"value","z":[true,null]}'
    assert canonical_sha256(value) == hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@pytest.mark.parametrize(
    "value",
    (
        {"number": 9_007_199_254_740_992},
        {"number": 1.5},
        {1: "not a string key"},
    ),
)
def test_rejects_values_outside_the_canonical_json_profile(value: object) -> None:
    from infralink_ops.canonical_json import canonical_json_bytes

    with pytest.raises(ValueError):
        canonical_json_bytes(value)
