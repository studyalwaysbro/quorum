"""Pluggable retrieval. The MVP default reads the WHOLE source (with chunk IDs)
so there's no hidden retrieval miss masquerading as truth — the right default
for a novice with no infra. Lexical / embedding retrievers are later, opt-in
strategies for larger corpora; none is a core dependency.
"""

from __future__ import annotations

from typing import Protocol

from quorum.research.schema import SourceChunk


class Retriever(Protocol):
    def packet(self, chunks: list[SourceChunk], question: str) -> str:
        ...


class WholeContextRetriever:
    """Return every chunk, ID-tagged, as the grounding packet."""

    def packet(self, chunks: list[SourceChunk], question: str = "") -> str:
        return "\n\n".join(f"[{c.id}] {c.text}" for c in chunks)
