# SPDX-License-Identifier: MIT
"""Canonical encoding for hashing flat sequences of fields.

This package deliberately runs **two** hashing conventions, because it hashes
two different shapes of thing, and conflating them would mean contorting one
shape to fit the other's encoding:

* **Structured objects** are hashed as canonical JSON: sorted keys, sets
  emitted as value-sorted lists, no reliance on field order. The only user
  today is ``governance.mandate.mandate_hash``, which hashes a ``Mandate``.
  That function does not use this module; it goes to ``hashlib`` directly
  over its JSON form.
* **Flat field sequences** are hashed with the separator-joined encoding
  below. Users: ``governance.signature`` (D2) and, from D3, the ledger entry
  chain.

The two are not interchangeable and are not meant to converge. What they do
share is the output contract, one lowercase SHA-256 hex digest, validated by
``require_sha256_hex`` here.

Why the separator, for the second convention
--------------------------------------------
ARCHITECTURE 3.2 specifies the ledger entry hash as
``SHA256(seq || timestamp || agent_id || mandate_hash || payload_digest ||
prev_hash)``. Read literally, ``||`` is bare concatenation, and bare
concatenation of variable-length fields is ambiguous: different field splits
can produce identical byte strings, so two materially different records can
collide on a digest that is supposed to distinguish them. That is a real
weakness in a structure whose entire job is tamper-evidence.

This module fixes it with an unambiguous encoding, recorded as an
ARCHITECTURE erratum rather than a silent implementation preference: fields
are joined with ``\\x1f`` (ASCII Unit Separator), and ``join_fields`` rejects
any field containing it. The separator cannot appear in the values this
codebase actually hashes - they are hex digests, ISO 8601 timestamps, StrEnum
values, and identifiers - and the rejection is what turns that from an
assumption into a checked property.

Canonical JSON has no equivalent exposure, which is why it is left alone
rather than rebuilt on top of this: its delimiters are structural, so field
boundaries are already unambiguous.
"""

import hashlib
import re

FIELD_SEPARATOR = "\x1f"
"""ASCII Unit Separator (U+001F). Chosen because it is a non-printing control
character with no legitimate place in any identifier, digest, or timestamp
this system hashes."""

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def require_sha256_hex(value: str, field_name: str) -> None:
    """Raise ``ValueError`` unless ``value`` is a lowercase 64-char hex digest.

    Uppercase hex is rejected rather than normalized: two spellings of the
    same digest would otherwise hash differently downstream, which is exactly
    the class of ambiguity this module exists to remove.
    """
    if _SHA256_HEX_PATTERN.match(value) is None:
        raise ValueError(
            f"{field_name} must be a lowercase 64-character SHA-256 hex digest; got {value!r}"
        )


def join_fields(*fields: str) -> str:
    """Join ``fields`` into one unambiguously decodable string.

    Rejects any field containing the separator, so the "no field can contain
    it" premise is enforced at every call rather than assumed once in a
    docstring.
    """
    for index, field in enumerate(fields):
        if FIELD_SEPARATOR in field:
            raise ValueError(
                f"field {index} contains the reserved field separator (U+001F), "
                "which would make the encoding ambiguous"
            )
    return FIELD_SEPARATOR.join(fields)


def digest_fields(*fields: str) -> str:
    """SHA-256 hex digest over the canonical encoding of ``fields``."""
    return hashlib.sha256(join_fields(*fields).encode("utf-8")).hexdigest()
