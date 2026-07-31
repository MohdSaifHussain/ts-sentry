# SPDX-License-Identifier: MIT
"""D2: the input firewall (STEP-03 D2, ARCHITECTURE 5.3).

Case content is data. It is never an instruction, never reaches the system
role, and never gets concatenated into one. This is the OWASP LLM01
mitigation, and it is structural rather than advisory.

Two copies, and why there are two
---------------------------------
STEP-03 D2 requires an instruction-stripping pass. STEP-03 3.2 requires that
every injection fixture be "preserved verbatim as data". Both are satisfied,
by keeping the two requirements on two different objects:

* ``FirewallResult.verbatim_block`` holds case text byte for byte. Nothing is
  removed, reordered, or normalized. This is what session artifacts store and
  what the evidence trail is built on. Redacting evidence would be a worse
  failure than the injection it defends against.
* ``FirewallResult.model_text`` is the copy that reaches a model, with each
  detected instruction-shaped span replaced by a marker naming the pattern
  that matched and where.

The ledger's ``PROMPT_SENT`` payload digests what was actually sent, so the
chain records the redacted copy and the artifact holds the verbatim one. A
reader can always tell which is which.

The fence, and why its nonce is derived from the content
--------------------------------------------------------
Delimiting only works if the delimiter cannot be closed from inside. A fixed
fence (``<data> ... </data>``) is trivially escaped: the attacker writes the
closing token in a comment. A random nonce fixes that but makes the output
irreproducible, which this project does not accept anywhere.

So the nonce is a digest *of the content it fences*. Closing the fence early
would require case text that contains its own digest, which is a SHA-256
preimage problem rather than a matter of guessing. The construction also
checks the derived nonce against the body and re-derives with a counter if it
somehow appears, so the property is enforced at every call rather than
asserted once here.

Records are emitted as one JSON object per line inside the fence. JSON string
escaping means a newline, a quote, or a fence-shaped line inside case text is
a character sequence in a value, not a structural token.

Honest limit
------------
Pattern-based instruction detection is not complete and cannot be. Any list
of phrasings is a list someone can paraphrase around, and this module makes no
claim to catch novel attacks. The load-bearing controls are the structural
ones: case text enters only as fenced JSON data, the system role is a
registered constant that no case content can reach, and the agent's output is
checked against a contract by the symbolic verifier rather than trusted. The
pattern pass is defense in depth on top of those, and its real product is a
*signal* that an injection attempt occurred, which is a governance event worth
ledgering whether or not the attempt would have worked.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ts_sentry.governance.canonical import FIELD_SEPARATOR, digest_fields, require_sha256_hex

__all__ = [
    "PATTERNS",
    "PATTERN_SET_VERSION",
    "CaseRecord",
    "FirewallError",
    "FirewallResult",
    "InertBlock",
    "InjectionSignal",
    "PatternId",
    "SystemPrompt",
    "apply_firewall",
    "compose_user_content",
    "parse_block_records",
    "pattern_set_hash",
    "redaction_marker",
    "scan",
    "system_prompt",
]

_FENCE_DOMAIN = "ts-sentry/case-data-fence/v1"
_PATTERN_SET_DOMAIN = "ts-sentry/firewall-pattern-set/v1"

PATTERN_SET_VERSION = "1.0.0"
"""SemVer of the detection pattern set. Recorded in the ``PROMPT_SENT``
payload alongside its hash, so a session states which rules ran rather than
leaving a reader to guess from the code's current state."""

_NONCE_LENGTH = 32

FENCE_PREFIXES = ("-----BEGIN TS-SENTRY CASE DATA", "-----END TS-SENTRY CASE DATA")
FENCE_OPEN = FENCE_PREFIXES[0] + " {nonce}-----"
FENCE_CLOSE = FENCE_PREFIXES[1] + " {nonce}-----"

_REDACTION_MARKER = "[ts-sentry: instruction-shaped text removed: {pattern}@{offset}+{length}]"


class FirewallError(Exception):
    """Raised when content cannot be wrapped safely.

    A refusal to build the block at all, rather than a block that might leak:
    the firewall's failure mode has to be "nothing was sent", because the
    alternative is sending something whose delimiting is not trustworthy.
    """


class PatternId(StrEnum):
    """Families of instruction-shaped text.

    Grouped by what the attacker is trying to do rather than by the exact
    phrasing, so a signal count is readable as a threat picture: ten
    ``INSTRUCTION_OVERRIDE`` hits and one ``EXFILTRATION`` hit are different
    situations.
    """

    INSTRUCTION_OVERRIDE = "instruction_override"
    ROLE_MARKER = "role_marker"
    TOOL_CALL_MIMICRY = "tool_call_mimicry"
    DELIMITER_ESCAPE = "delimiter_escape"
    EXFILTRATION = "exfiltration"
    AUTHORITY_CLAIM = "authority_claim"
    ENCODED_PAYLOAD = "encoded_payload"


_PATTERN_SOURCES: tuple[tuple[PatternId, str], ...] = (
    (
        PatternId.INSTRUCTION_OVERRIDE,
        r"(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+|any\s+|the\s+|your\s+)*"
        r"(?:previous|prior|above|earlier|system|initial)?\s*"
        r"(?:instruction|instructions|prompt|prompts|rule|rules|direction|directions|guideline|guidelines)",
    ),
    (
        PatternId.ROLE_MARKER,
        r"(?:^|\n)\s*(?:system|assistant|user|developer)\s*:"
        r"|<\|(?:im_start|im_end|system|endoftext)\|>"
        r"|\[/?INST\]"
        r"|(?:^|\n)#{1,6}\s*(?:instruction|system prompt)",
    ),
    (
        PatternId.TOOL_CALL_MIMICRY,
        r"</?(?:function_calls|invoke|tool_use|tool_call|antml:\w+)\b"
        r"|\btool_call\s*[:=]"
        r"|\"(?:tool|tool_name|function|function_call)\"\s*:",
    ),
    (
        PatternId.DELIMITER_ESCAPE,
        r"-{3,}\s*(?:BEGIN|END)\b"
        r"|</?(?:data|document|case|content|context)\s*>"
        r"|```"
        r'|"""',
    ),
    (
        PatternId.EXFILTRATION,
        r"(?:reveal|print|repeat|show|output|dump|leak)\s+(?:me\s+|us\s+|back\s+)*"
        r"(?:your|the|all)?\s*"
        r"(?:system\s+prompt|instructions|prompt|configuration|api[_\s-]?key|credentials|secret)"
        r"|(?:send|post|upload|exfiltrate|forward)\s+(?:\S+\s+){0,6}?(?:to\s+)?https?://",
    ),
    (
        PatternId.AUTHORITY_CLAIM,
        r"(?:you\s+are\s+now|from\s+now\s+on|act\s+as|pretend\s+to\s+be|"
        r"new\s+instructions?|updated\s+instructions?|developer\s+mode|admin\s+override)"
        r"|(?:this\s+is\s+)?(?:an?\s+)?(?:official|authorized|approved)\s+"
        r"(?:request|instruction|override)\s+from",
    ),
    (
        PatternId.ENCODED_PAYLOAD,
        r"(?:base64|rot13|hex)\s*(?:decode|encoded|:)"
        r"|data:text/[a-z]+;base64,",
    ),
)

PATTERNS: tuple[tuple[PatternId, re.Pattern[str]], ...] = tuple(
    (pattern_id, re.compile(source, re.IGNORECASE)) for pattern_id, source in _PATTERN_SOURCES
)
"""The compiled detection set.

Deliberately a flat, inspectable tuple rather than a plugin registry: a
security control whose active rules cannot be read off in one screen is a
control nobody audits.
"""


def pattern_set_hash() -> str:
    """Digest over the pattern set, so a session can name the rules it ran."""
    return digest_fields(
        _PATTERN_SET_DOMAIN,
        PATTERN_SET_VERSION,
        *(f"{pattern_id.value}={source}" for pattern_id, source in _PATTERN_SOURCES),
    )


# --------------------------------------------------------------------------
# Case content
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """One piece of case content, with the provenance it came in under.

    ``source`` names the table and column (``comment.text``,
    ``channel.description``), so an analyst reading a session artifact can see
    where a string came from without re-querying. ``text`` is never touched.
    """

    record_id: str
    source: str
    text: str

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("record_id must be non-empty")
        if not self.source.strip():
            raise ValueError("source must be non-empty")

    def to_json_object(self) -> dict[str, str]:
        return {"record_id": self.record_id, "source": self.source, "text": self.text}


@dataclass(frozen=True, slots=True)
class InjectionSignal:
    """One instruction-shaped span found in case content.

    Carries the matched text as well as its position. Truncating it would
    make the signal unauditable, and the whole point of recording an attempt
    is that a human can later read what was attempted.
    """

    pattern_id: PatternId
    record_id: str
    offset: int
    length: int
    matched_text: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "pattern_id": self.pattern_id.value,
            "record_id": self.record_id,
            "offset": self.offset,
            "length": self.length,
            "matched_text": self.matched_text,
        }


def scan(records: Sequence[CaseRecord]) -> tuple[InjectionSignal, ...]:
    """Report every instruction-shaped span, in record then position order.

    Pure: no I/O, no ledger write, no mutation of the records. Overlapping
    matches from different patterns are all reported, because two families
    firing on one span is a more interesting fact than either alone.
    """
    signals: list[InjectionSignal] = []
    for record in records:
        for pattern_id, pattern in PATTERNS:
            for match in pattern.finditer(record.text):
                if not match.group(0):
                    continue
                signals.append(
                    InjectionSignal(
                        pattern_id=pattern_id,
                        record_id=record.record_id,
                        offset=match.start(),
                        length=match.end() - match.start(),
                        matched_text=match.group(0),
                    )
                )
    return tuple(sorted(signals, key=lambda s: (s.record_id, s.offset, s.pattern_id.value)))


# --------------------------------------------------------------------------
# The inert block
# --------------------------------------------------------------------------


_LINE_BREAK_ESCAPES: Mapping[str, str] = {
    "\x0b": "\\u000b",  # VT
    "\x0c": "\\u000c",  # FF
    "\x1c": "\\u001c",  # FS
    "\x1d": "\\u001d",  # GS
    "\x1e": "\\u001e",  # RS
    "\x85": "\\u0085",  # NEL
    "\u2028": "\\u2028",  # LINE SEPARATOR
    "\u2029": "\\u2029",  # PARAGRAPH SEPARATOR
}
"""Characters that split lines but that JSON does not escape.

Found by the hypothesis property in ``tests/test_firewall.py``, not by
inspection, and it was a real fence-escape vector rather than a formatting
nit. ``json.dumps`` escapes ``\\n`` and ``\\r``, so a one-object-per-line
encoding looks safe. It is not: Python's ``str.splitlines`` (and most other
line-splitting implementations, including the ones inside a model's tokenizer
pipeline) also break on VT, FF, FS, GS, RS, NEL, U+2028 and U+2029. A comment
containing U+2028 followed by a forged JSON object would therefore appear as
two records inside one fenced block.

Escaping them keeps the encoding's central promise, that a record occupies
exactly one line, true for every possible input. The escapes are JSON's own,
so a parser decodes them back to the original character and the content is
still preserved verbatim.
"""


def _escape_line_breaks(encoded: str) -> str:
    """Neutralize residual line-breaking characters in an encoded record.

    Safe to apply to the whole JSON string: these characters cannot appear
    outside a string value, because keys and structural tokens are ASCII.
    """
    if not any(char in encoded for char in _LINE_BREAK_ESCAPES):
        return encoded
    return "".join(_LINE_BREAK_ESCAPES.get(char, char) for char in encoded)


def _body(records: Sequence[CaseRecord]) -> str:
    """One JSON object per line. Field order is fixed for reproducibility."""
    return "\n".join(
        _escape_line_breaks(json.dumps(record.to_json_object(), sort_keys=True, ensure_ascii=False))
        for record in records
    )


def _derive_nonce(body: str) -> str:
    """A fence token the fenced content cannot contain.

    Derived from the body, so writing the closing token inside the data would
    take a preimage. The loop is not decoration: it turns "cannot contain its
    own digest" from an argument into a checked property, and it terminates
    because each attempt digests a different string.
    """
    for counter in range(64):
        nonce = digest_fields(_FENCE_DOMAIN, str(counter), body)[:_NONCE_LENGTH]
        if nonce not in body:
            return nonce
    raise FirewallError(  # pragma: no cover - requires 64 SHA-256 preimages
        "could not derive a fence nonce absent from the content"
    )


@dataclass(frozen=True, slots=True)
class InertBlock:
    """Case content, fenced and inert.

    ``records`` are the originals: this object never holds an altered copy of
    case text. Redaction happens on the way out, in ``render_redacted``, so
    there is exactly one place where the two copies diverge.
    """

    nonce: str
    records: tuple[CaseRecord, ...]

    @classmethod
    def wrap(cls, records: Sequence[CaseRecord]) -> "InertBlock":
        seen: set[str] = set()
        for record in records:
            if record.record_id in seen:
                raise FirewallError(
                    f"duplicate record_id {record.record_id!r}: a citation that resolves to two "
                    "records is not a citation"
                )
            seen.add(record.record_id)
        return cls(nonce=_derive_nonce(_body(records)), records=tuple(records))

    def _fenced(self, body: str) -> str:
        return "\n".join(
            (
                FENCE_OPEN.format(nonce=self.nonce),
                body,
                FENCE_CLOSE.format(nonce=self.nonce),
            )
        )

    def render(self) -> str:
        """The verbatim block. What artifacts store."""
        return self._fenced(_body(self.records))

    def render_redacted(self, signals: Iterable[InjectionSignal]) -> str:
        """The model-facing block, with detected spans replaced by markers."""
        by_record: dict[str, list[InjectionSignal]] = {}
        for signal in signals:
            by_record.setdefault(signal.record_id, []).append(signal)

        redacted = tuple(
            CaseRecord(
                record_id=record.record_id,
                source=record.source,
                text=_redact(record.text, by_record.get(record.record_id, ())),
            )
            for record in self.records
        )
        return self._fenced(_body(redacted))


def _merge_spans(signals: Sequence[InjectionSignal]) -> tuple[tuple[int, int, PatternId], ...]:
    """Collapse overlapping matches into disjoint spans, left to right.

    Two patterns firing on overlapping text must not produce two nested
    replacements: the second would be applied to offsets the first already
    moved. The merged span keeps the first pattern's id, which is the one a
    reader sees named in the marker.
    """
    ordered = sorted(signals, key=lambda s: (s.offset, -s.length))
    merged: list[tuple[int, int, PatternId]] = []
    for signal in ordered:
        start, end = signal.offset, signal.offset + signal.length
        if merged and start <= merged[-1][1]:
            previous_start, previous_end, pattern_id = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end), pattern_id)
            continue
        merged.append((start, end, signal.pattern_id))
    return tuple(merged)


def _redact(text: str, signals: Sequence[InjectionSignal]) -> str:
    if not signals:
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end, pattern_id in _merge_spans(signals):
        pieces.append(text[cursor:start])
        pieces.append(
            _REDACTION_MARKER.format(pattern=pattern_id.value, offset=start, length=end - start)
        )
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


@dataclass(frozen=True, slots=True)
class FirewallResult:
    """What the firewall produced for one batch of case content."""

    block: InertBlock
    signals: tuple[InjectionSignal, ...]
    pattern_set_version: str
    pattern_set_hash: str

    @property
    def verbatim_text(self) -> str:
        """The block as it is stored. Case text byte for byte."""
        return self.block.render()

    @property
    def model_text(self) -> str:
        """The block as it is sent. Detected spans replaced by markers."""
        return self.block.render_redacted(self.signals)

    @property
    def redacted(self) -> bool:
        return bool(self.signals)

    def signal_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for signal in self.signals:
            counts[signal.pattern_id.value] = counts.get(signal.pattern_id.value, 0) + 1
        return counts

    def to_ledger_payload(self) -> dict[str, object]:
        """The governance record of this firewall pass.

        Carries the signals and the rule set that found them, plus the nonce,
        which lets a reader re-derive the fence from the stored verbatim block
        and confirm the two agree.
        """
        return {
            "pattern_set_version": self.pattern_set_version,
            "pattern_set_hash": self.pattern_set_hash,
            "fence_nonce": self.block.nonce,
            "record_count": len(self.block.records),
            "signal_count": len(self.signals),
            "signal_counts": self.signal_counts(),
            "signals": [signal.to_json_object() for signal in self.signals],
        }


def apply_firewall(records: Sequence[CaseRecord]) -> FirewallResult:
    """Wrap case content and scan it. The only entry point dispatch uses."""
    block = InertBlock.wrap(records)
    return FirewallResult(
        block=block,
        signals=scan(block.records),
        pattern_set_version=PATTERN_SET_VERSION,
        pattern_set_hash=pattern_set_hash(),
    )


# --------------------------------------------------------------------------
# The system role
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SystemPrompt:
    """A system-role prompt, identified by the hash of its own text.

    The D4 adapter accepts this type and never a bare ``str``. That is what
    makes "case content never reaches the system role" structural rather than
    a habit: building a system prompt is a deliberate act with an id and a
    digest, not something a string concatenation can wander into.

    The versioned prompt *registry* with an evaluation gate is STEP-06 (the
    prompt-eval agent). This is the minimum that makes the invariant
    checkable now, and it is deliberately not that registry.
    """

    prompt_id: str
    text: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.prompt_id.strip():
            raise ValueError("prompt_id must be non-empty")
        if not self.text.strip():
            raise ValueError("system prompt text must be non-empty")
        require_sha256_hex(self.sha256, "sha256")
        if self.sha256 != _prompt_digest(self.prompt_id, self.text):
            raise ValueError(
                "system prompt sha256 does not recompute from (prompt_id, text); "
                "build it with system_prompt()"
            )


def _prompt_digest(prompt_id: str, text: str) -> str:
    return digest_fields("ts-sentry/system-prompt/v1", prompt_id, text)


def system_prompt(prompt_id: str, text: str) -> SystemPrompt:
    """The only factory that computes a valid system-prompt digest."""
    if FIELD_SEPARATOR in prompt_id or FIELD_SEPARATOR in text:
        raise ValueError("a system prompt must not contain the reserved field separator (U+001F)")
    return SystemPrompt(prompt_id=prompt_id, text=text, sha256=_prompt_digest(prompt_id, text))


def compose_user_content(instruction: str, result: FirewallResult) -> str:
    """Build the user-role message: instruction first, then fenced data.

    The instruction is the agent's task text, which comes from the code, and
    the block is data, which comes from the platform. They are concatenated
    only here, only in that order, and only into the *user* role. Nothing in
    this function can put case content into a system prompt, because it never
    receives one.

    Refuses an instruction carrying the fence markers or the nonce: an
    instruction that closes the fence would undo the delimiting from the one
    side the attacker is not supposed to control.
    """
    if result.block.nonce in instruction:
        raise FirewallError("instruction text contains the fence nonce")
    for prefix in FENCE_PREFIXES:
        if prefix in instruction:
            raise FirewallError(f"instruction text contains the fence marker {prefix!r}")
    return f"{instruction}\n\n{result.model_text}\n"


def redaction_marker(pattern_id: PatternId, offset: int, length: int) -> str:
    """The marker a redacted span is replaced by. Exposed for tests and for
    anyone reading a model-facing transcript who needs to recognize one."""
    return _REDACTION_MARKER.format(pattern=pattern_id.value, offset=offset, length=length)


def parse_block_records(rendered: str) -> tuple[Mapping[str, str], ...]:
    """Read the JSON records back out of a rendered block.

    Exists so tests can assert verbatim preservation by round-tripping rather
    than by substring search, which would pass on a block that had mangled the
    text in some way a substring still survived.
    """
    lines = rendered.splitlines()
    if len(lines) < 2:
        raise FirewallError("a rendered block has at least an opening and a closing fence")
    body = lines[1:-1]
    return tuple(json.loads(line) for line in body if line)
