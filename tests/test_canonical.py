# SPDX-License-Identifier: MIT
"""Canonical field encoding: the ARCHITECTURE 3.2 ``||`` erratum.

The headline test here is ``test_distinct_field_splits_do_not_collide``: it
demonstrates the concrete failure that bare concatenation permits and that
this encoding removes.
"""

import pytest

from ts_sentry.governance.canonical import (
    FIELD_SEPARATOR,
    digest_fields,
    join_fields,
    require_sha256_hex,
)

_VALID_DIGEST = "a" * 64


def test_distinct_field_splits_do_not_collide() -> None:
    """The erratum, made concrete.

    Under ARCHITECTURE 3.2's literal ``a || b``, the field lists ("ab", "c")
    and ("a", "bc") both concatenate to "abc" and would hash identically -
    two materially different ledger entries sharing one entry_hash, in the
    one structure whose whole job is telling entries apart. Separator-joined
    encoding keeps them distinct.
    """
    assert "ab" + "c" == "a" + "bc"  # the ambiguity being fixed
    assert digest_fields("ab", "c") != digest_fields("a", "bc")


def test_digest_is_deterministic() -> None:
    assert digest_fields("one", "two") == digest_fields("one", "two")


def test_digest_is_lowercase_sha256_hex() -> None:
    require_sha256_hex(digest_fields("x"), "digest")


def test_join_round_trips_by_splitting_on_the_separator() -> None:
    assert join_fields("a", "b", "c").split(FIELD_SEPARATOR) == ["a", "b", "c"]


def test_join_preserves_empty_fields() -> None:
    """An empty field is still a field, and stays distinguishable."""
    assert join_fields("a", "", "b").split(FIELD_SEPARATOR) == ["a", "", "b"]
    assert digest_fields("a", "", "b") != digest_fields("a", "b")


def test_join_rejects_a_field_containing_the_separator() -> None:
    """The "no field can contain it" premise is checked, not assumed."""
    with pytest.raises(ValueError, match="reserved field separator"):
        join_fields("clean", f"smug{FIELD_SEPARATOR}gled")


def test_digest_rejects_a_field_containing_the_separator() -> None:
    with pytest.raises(ValueError, match="reserved field separator"):
        digest_fields(f"{FIELD_SEPARATOR}")


def test_require_sha256_hex_accepts_a_valid_digest() -> None:
    require_sha256_hex(_VALID_DIGEST, "subject_hash")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * 63,
        "a" * 65,
        "A" * 64,  # uppercase rejected, not normalized
        "g" * 64,  # non-hex
        f"{'a' * 63} ",
    ],
)
def test_require_sha256_hex_rejects_malformed_digests(value: str) -> None:
    with pytest.raises(ValueError, match="subject_hash"):
        require_sha256_hex(value, "subject_hash")
