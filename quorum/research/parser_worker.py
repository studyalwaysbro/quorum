"""Private container worker for risky PDF/image normalization."""

from __future__ import annotations

import json
import sys

from quorum.research.attachments import MAX_FILE_BYTES, _worker_extract, sanitize_name


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    kind, name = sys.argv[1], sanitize_name(sys.argv[2])
    data = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise SystemExit(2)
    try:
        text = _worker_extract(kind, data, name)
    except Exception:
        raise SystemExit(2) from None
    sys.stdout.write(json.dumps({"text": text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
