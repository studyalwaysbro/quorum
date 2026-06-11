"""Research-mode data model + deterministic citation validation.

The trust core: a model returns claims as strict JSON with ``{chunk_id, quote}``
citations. Quorum does NOT take the model's word for it — it checks that the
quoted text literally appears in the cited chunk. A citation whose quote isn't
found is marked invalid, and a claim with no valid citation cannot be reported
as "supported." That deterministic check is what separates real grounding from
citation-shaped decoration.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

VERDICTS = ("Supported", "PartiallySupported", "Unsupported", "Contradicted")


@dataclass(frozen=True)
class SourceChunk:
    id: str          # stable, e.g. "C1"
    text: str
    source: str      # display filename / label


@dataclass
class Citation:
    chunk_id: str
    quote: str
    valid: bool = False
    start: Optional[int] = None     # char offset of the quote within the chunk


@dataclass
class Claim:
    id: str
    text: str
    citations: list[Citation] = field(default_factory=list)
    confidence: Optional[int] = None
    asserted_by: list[str] = field(default_factory=list)
    disputed_by: list[str] = field(default_factory=list)
    verdict: Optional[str] = None
    disposition: Optional[str] = None   # "kept" | "qualified" | "dropped"

    @property
    def has_valid_citation(self) -> bool:
        return any(c.valid for c in self.citations)

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(text: str) -> str:
    """Collapse whitespace so quote-matching tolerates re-wrapping."""
    return re.sub(r"\s+", " ", text).strip()


def validate_citations(claims: list[Claim], chunks: list[SourceChunk]) -> list[Claim]:
    """Mark each citation valid iff its quote appears in the cited chunk.

    Matching is whitespace-normalized and case-insensitive; ``start`` is the
    offset in the normalized chunk text. Mutates and returns ``claims``.
    """
    by_id = {c.id: c for c in chunks}
    for claim in claims:
        for cite in claim.citations:
            chunk = by_id.get(cite.chunk_id)
            cite.valid = False
            cite.start = None
            if chunk is None or not cite.quote.strip():
                continue
            hay = _norm(chunk.text).casefold()
            needle = _norm(cite.quote).casefold()
            idx = hay.find(needle)
            if idx != -1:
                cite.valid = True
                cite.start = idx
    return claims


class ClaimParseError(ValueError):
    """Raised when a model response is not a valid claim object."""


def parse_claims(text: str, prefix: str = "K") -> list[Claim]:
    """Parse a model's JSON claim response into Claim objects.

    Accepts ``{"claims": [...]}`` or a bare ``[...]``. Tolerates the JSON being
    wrapped in prose / code fences by extracting the first JSON array/object.
    Each claim needs ``text`` and a list of ``citations`` with ``chunk_id`` +
    ``quote``. Raises :class:`ClaimParseError` on anything unusable.
    """
    payload = _extract_json(text)
    if payload is None:
        raise ClaimParseError("no JSON found in response")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ClaimParseError(f"invalid JSON: {exc}") from exc

    raw_claims = data.get("claims") if isinstance(data, dict) else data
    if not isinstance(raw_claims, list):
        raise ClaimParseError("expected a list of claims")

    claims: list[Claim] = []
    for i, raw in enumerate(raw_claims, start=1):
        if not isinstance(raw, dict):
            continue
        text_val = str(raw.get("text", "")).strip()
        if not text_val:
            continue
        citations = []
        for c in raw.get("citations", []) or []:
            if not isinstance(c, dict):
                continue
            chunk_id = str(c.get("chunk_id", "")).strip()
            quote = str(c.get("quote", "")).strip()
            if chunk_id and quote:
                citations.append(Citation(chunk_id=chunk_id, quote=quote))
        confidence = raw.get("confidence")
        confidence = int(confidence) if isinstance(confidence, (int, float)) else None
        claims.append(Claim(
            id=f"{prefix}{i}", text=text_val,
            citations=citations, confidence=confidence,
        ))
    if not claims:
        raise ClaimParseError("no usable claims in response")
    return claims


def _extract_json(text: str) -> Optional[str]:
    text = text.strip()
    # fenced ```json ... ``` block
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    # outermost JSON value = whichever of {...} / [...] opens earliest
    candidates = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            candidates.append((start, text[start:end + 1]))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]
