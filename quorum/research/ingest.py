"""Turn uploaded text into stable, citable source chunks.

Chunks are paragraph-ish so a model can cite a specific, findable span. IDs are
stable within a run (``C1``, ``C2``, …) so citations resolve deterministically.
MVP handles plain text / markdown; PDF extraction lives behind the optional
``[research]`` extra (see ``ingest_pdf``).
"""

from __future__ import annotations

import re

from quorum.research.schema import SourceChunk

MAX_TOTAL_CHARS = 200_000      # MVP guard; fail honestly above this
_TARGET_CHARS = 900           # soft cap per chunk before splitting a long paragraph


def chunk_text(text: str, source: str = "upload", max_total: int = MAX_TOTAL_CHARS) -> list[SourceChunk]:
    """Split text into paragraph chunks with stable IDs.

    Raises ``ValueError`` if the extracted text exceeds ``max_total`` — we fail
    honestly rather than silently truncate evidence.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > max_total:
        raise ValueError(
            f"source too large ({len(text)} chars > {max_total}); split it or "
            "use a retrieval-limited mode (not in the MVP)"
        )

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[SourceChunk] = []
    for para in paragraphs:
        for piece in _split_long(para, _TARGET_CHARS):
            chunks.append(SourceChunk(id=f"C{len(chunks) + 1}", text=piece, source=source))
    if not chunks and text.strip():
        chunks.append(SourceChunk(id="C1", text=text.strip(), source=source))
    return chunks


def _split_long(paragraph: str, target: int) -> list[str]:
    """Split an over-long paragraph: sentence-pack first, then hard-window any
    piece with no usable boundaries so a degenerate input (e.g. one 200k-char
    token) can't become a single giant chunk."""
    if len(paragraph) <= target:
        return [paragraph]
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    pieces, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > target:
            pieces.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current.strip():
        pieces.append(current.strip())

    out: list[str] = []
    for piece in pieces or [paragraph]:
        if len(piece) <= target * 2:
            out.append(piece)
        else:                                   # no boundaries — deterministic windows
            out.extend(piece[i:i + target] for i in range(0, len(piece), target))
    return out
