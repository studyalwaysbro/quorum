"""Grounded research rounds.

``grounded_blind`` — each member answers the question from the SOURCE ONLY,
returning atomic claims each backed by a chunk id + verbatim quote. Quotes are
then validated against the source (``validate_citations``), so a fabricated
citation is caught deterministically.

``fact_check`` — a checker examines ONE atomic claim against the source text
only (no author names, no draft conclusion), returning a verdict. The schema's
``effective_verdict`` still refuses to call anything "Supported" without a valid
citation, so the model can't overrule the deterministic check.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from quorum.adapters import Model
from quorum.research.retrieval import WholeContextRetriever
from quorum.research.schema import (
    VERDICTS,
    Claim,
    ClaimParseError,
    SourceChunk,
    parse_claims,
    validate_citations,
)
from quorum.transcript import Transcript

_GROUNDED_SHAPE = (
    '{"claims":[{"text":"<one atomic claim>","citations":'
    '[{"chunk_id":"C1","quote":"<verbatim substring of that chunk>"}],'
    '"confidence":3}]}'
)


def _grounded_prompt(question: str, packet: str) -> str:
    return (
        "Answer the question using ONLY the source document below — no outside "
        "knowledge. Break your answer into atomic claims. Every claim MUST cite "
        "the source: give the chunk id (e.g. C1) and a quote that is an EXACT "
        "verbatim substring of that chunk. If the source does not support a "
        "claim, do not make it.\n\n"
        f"Return ONLY JSON in this shape and nothing else:\n{_GROUNDED_SHAPE}\n\n"
        f"QUESTION:\n{question}\n\n"
        f"SOURCE:\n{packet}"
    )


def _repair_prompt(error: str) -> str:
    return (
        "Your previous response was not valid claim JSON.\n"
        f"Error: {error}\n\n"
        f"Return ONLY this JSON shape, nothing else:\n{_GROUNDED_SHAPE}"
    )


def _claims_with_repair(model: Model, prompt: str, response: str) -> tuple[list[Claim], str]:
    try:
        return parse_claims(response), response
    except ClaimParseError as exc:
        repaired = model.complete(_repair_prompt(str(exc)))
        try:
            return parse_claims(repaired), repaired
        except ClaimParseError:
            return [], repaired   # honest: this member produced no usable claims


def grounded_blind(
    members: Sequence[Model],
    question: str,
    chunks: list[SourceChunk],
    t: Transcript,
    retriever: Optional[WholeContextRetriever] = None,
) -> dict[str, list[Claim]]:
    """Each member returns source-cited, validated claims."""
    retriever = retriever or WholeContextRetriever()
    packet = retriever.packet(chunks, question)
    prompt = _grounded_prompt(question, packet)
    out: dict[str, list[Claim]] = {}
    counter = 1                                  # global, stable ids across members
    for member in members:
        response = member.complete(prompt)
        claims, final = _claims_with_repair(member, prompt, response)
        validate_citations(claims, chunks)
        for claim in claims:
            claim.id = f"K{counter}"             # ledger id == transcript-meta id
            counter += 1
            claim.asserted_by = [member.name]
        t.record("grounded_blind", member.name, prompt, final,
                 meta={"claims": [c.to_dict() for c in claims]})
        out[member.name] = claims
    return out


def _fact_check_prompt(claim_text: str, source: str) -> str:
    return (
        "You are a fact-checker. Using ONLY the source text below — no outside "
        "knowledge — decide how well it supports the CLAIM. 'Unsupported' means "
        "the source does not establish it (NOT that it is false in general).\n\n"
        f"CLAIM:\n{claim_text}\n\n"
        f"SOURCE:\n{source}\n\n"
        "End with exactly one line:\n"
        "VERDICT: <Supported|PartiallySupported|Unsupported|Contradicted>"
    )


# The verdict must be the LAST non-empty line and an EXACT full-line match, so
# 'VERDICT: SupportedButActuallyUnsupported' or a verdict buried mid-text can't
# spoof it. Anything else fails closed (claim is treated as unverified).
_VERDICT_LINE_RE = re.compile(
    r"^VERDICT\s*:\s*(Supported|PartiallySupported|Unsupported|Contradicted)\.?$",
    re.IGNORECASE,
)
_CANON = {v.casefold(): v for v in VERDICTS}


def _parse_verdict(text: str) -> Optional[str]:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    match = _VERDICT_LINE_RE.match(lines[-1])
    if match is None:
        return None                       # unparsed -> fail closed (drops the claim)
    return _CANON[match.group(1).casefold()]


def fact_check(
    checker: Model,
    claim: Claim,
    chunks: list[SourceChunk],
    t: Transcript,
) -> Optional[str]:
    """Check one claim against the source it cites (or the whole source)."""
    cited = {c.chunk_id for c in claim.citations}
    relevant = [c for c in chunks if c.id in cited] or chunks
    source = "\n\n".join(f"[{c.id}] {c.text}" for c in relevant)
    prompt = _fact_check_prompt(claim.text, source)
    response = checker.complete(prompt)
    verdict = _parse_verdict(response)
    claim.verdict = verdict
    t.record("fact_check", checker.name, prompt, response,
             meta={"claim_id": claim.id, "verdict": verdict})
    return verdict
