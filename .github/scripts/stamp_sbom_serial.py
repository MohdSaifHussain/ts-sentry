# SPDX-License-Identifier: MIT
"""Give a reproducible CycloneDX document a content-derived serial number.

`cyclonedx-py --output-reproducible` strips the serial number, because a
CycloneDX `serialNumber` is normally a fresh random UUID per document and a
random field is exactly what stops two runs of one environment producing the
same bytes.

`actions/attest` detects CycloneDX by requiring all three of `bomFormat`,
`serialNumber` and `specVersion` to be present, so a reproducible document is
not recognised as an SBOM at all and the release fails with "Unsupported SBOM
format. Must be valid SPDX or CycloneDX JSON."

Found by v1.0.0-rc.2, and the two requirements are only in tension if the
serial number has to be random. It does not: a **UUIDv5 over the document's own
content** is a valid RFC 4122 UUID, is stable for identical content, and
differs whenever the inventory differs, which is what a serial number is for.

That is the same construction this project already uses twice: the firewall's
fence token is a digest of the content it fences, and a dataset's identity is a
digest of its table hashes. A content-derived identifier beats a random one
wherever reproducibility matters.
"""

import json
import sys
import uuid
from pathlib import Path

# Stable namespace for this project's SBOM serial numbers. Any fixed UUID
# works; this one is derived from the repository URL so it is reproducible from
# something a reader can check rather than being a magic constant.
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/MohdSaifHussain/ts-sentry")


def main(path: Path) -> int:
    document = json.loads(path.read_text(encoding="utf-8"))

    if document.get("bomFormat") != "CycloneDX":
        print(f"{path} is not a CycloneDX document", file=sys.stderr)
        return 1
    if "serialNumber" in document:
        print(f"{path} already carries a serial number; refusing to overwrite it", file=sys.stderr)
        return 1

    # Over the whole document as generated, with sorted keys, so the identifier
    # covers the inventory rather than a subset of it that could change without
    # changing the serial.
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    document["serialNumber"] = f"urn:uuid:{uuid.uuid5(NAMESPACE, canonical)}"

    # Written in the field order CycloneDX documents conventionally use, which
    # costs nothing and keeps a diff between two generated documents readable.
    ordered = {
        key: document[key]
        for key in ("$schema", "bomFormat", "specVersion", "serialNumber", "version")
        if key in document
    }
    ordered.update({k: v for k, v in document.items() if k not in ordered})

    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{path}: serialNumber={ordered['serialNumber']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
