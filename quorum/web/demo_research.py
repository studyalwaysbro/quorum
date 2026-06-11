"""Keyless demo models for research mode.

So a visitor with no API keys can see the Claim Ledger work: the grounded model
quotes REAL substrings of the uploaded source (so those citations validate and
the claims are kept) plus one deliberately fabricated quote (which fails
validation and lands in "refused to conclude"). The fact-checker returns
Supported — proving the fabricated claim is dropped anyway, by the deterministic
citation check, not the checker's say-so.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


def _parse_packet(prompt: str) -> list[tuple[str, str]]:
    """Pull (chunk_id, text) pairs out of a grounded/fact-check SOURCE block."""
    if "SOURCE:" not in prompt:
        return []
    src = prompt.split("SOURCE:", 1)[1]
    parts = re.split(r"\[(C\d+)\]\s*", src)
    chunks = []
    for i in range(1, len(parts) - 1, 2):
        text = parts[i + 1].strip()
        if text:
            chunks.append((parts[i], text))
    return chunks


@dataclass
class DemoResearchModel:
    name: str
    order: int = 0          # so different members quote different chunks

    def complete(self, prompt: str) -> str:
        if "You are a fact-checker" in prompt:
            return "The cited passage supports the claim.\nVERDICT: Supported"

        chunks = _parse_packet(prompt)
        claims = []
        # two genuinely-cited claims (verbatim quotes -> validate -> kept)
        start = self.order % max(len(chunks), 1)
        for cid, text in (chunks[start:start + 2] or chunks[:2]):
            quote = " ".join(text.split()[:12])
            claims.append({
                "text": f"The source establishes a point regarding “{quote[:48]}…”.",
                "citations": [{"chunk_id": cid, "quote": quote}],
                "confidence": 4,
            })
        # one fabricated claim (quote not in source -> dropped / refused)
        if chunks:
            claims.append({
                "text": "A broader conclusion the source does not actually establish.",
                "citations": [{"chunk_id": chunks[0][0],
                               "quote": "a phrase that does not appear verbatim in the document"}],
                "confidence": 2,
            })
        return json.dumps({"claims": claims})


def demo_research_member(name: str, order: int = 0) -> DemoResearchModel:
    return DemoResearchModel(name=name, order=order)
