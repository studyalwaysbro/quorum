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
import math
import re
import unicodedata
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

    @property
    def effective_verdict(self) -> Optional[str]:
        """The enforced verdict: a claim can never be reported as support-like
        ('Supported' OR 'PartiallySupported') without a valid citation, no
        matter what a model (or caller) claimed."""
        if self.verdict in ("Supported", "PartiallySupported") and not self.has_valid_citation:
            return "Unsupported"
        return self.verdict

    def to_dict(self) -> dict:
        d = asdict(self)
        d["has_valid_citation"] = self.has_valid_citation
        d["effective_verdict"] = self.effective_verdict
        return d


def _norm(text: str) -> str:
    """Normalize for quote-matching: NFKC (folds accents/smart-punctuation
    composition differences) + collapsed whitespace. Offsets returned by
    :func:`validate_citations` are positions in this normalized text."""
    text = unicodedata.normalize("NFKC", text)
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
        text_val = raw.get("text")
        if not isinstance(text_val, str) or not text_val.strip():
            continue                                  # require a real string, no coercion
        citations = []
        raw_cites = raw.get("citations")
        if isinstance(raw_cites, list):               # non-list citations -> none (fail closed)
            for c in raw_cites:
                if not isinstance(c, dict):
                    continue
                chunk_id = c.get("chunk_id")
                quote = c.get("quote")
                if isinstance(chunk_id, str) and isinstance(quote, str) \
                        and chunk_id.strip() and quote.strip():
                    citations.append(Citation(chunk_id=chunk_id.strip(), quote=quote.strip()))
        claims.append(Claim(
            id=f"{prefix}{i}", text=text_val.strip(),
            citations=citations, confidence=_safe_confidence(raw.get("confidence")),
        ))
    if not claims:
        raise ClaimParseError("no usable claims in response")
    return claims


def _safe_confidence(value) -> Optional[int]:
    """Accept only a finite 1–5; reject bools, inf/nan, out-of-range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not (1 <= value <= 5):
        return None
    return int(value)


_DECODER = json.JSONDecoder()


def _extract_json(text: str) -> Optional[str]:
    """Find the first substring that actually parses as a JSON object/array.

    Prefers a fenced ```json block, then scans every ``{``/``[`` position and
    tries ``raw_decode`` there — so stray brackets in surrounding prose can't
    derail extraction the way a naive find/rfind does.
    """
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        found = _first_json_span(fence.group(1).strip())
        if found is not None:
            return found
    return _first_json_span(text)


def _first_json_span(text: str) -> Optional[str]:
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                _obj, end = _DECODER.raw_decode(text, i)
            except (json.JSONDecodeError, ValueError):
                continue
            return text[i:end]
    return None
