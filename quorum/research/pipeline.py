"""Research pipeline: grounded claims -> pooled ledger -> fact-check.

Separate from ``Council.ask`` — the deliberation path is untouched. Each member
answers from the source with cited claims; the claims are pooled into a single
ledger (each tagged with who asserted it), then a checker fact-checks each one
against the source. The result is a :class:`ResearchVerdict` carrying the
finalized :class:`ClaimLedger` and the full replayable transcript.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from quorum.adapters import Model
from quorum.research.ledger import ClaimLedger
from quorum.research.rounds import fact_check, grounded_blind
from quorum.research.schema import SourceChunk
from quorum.transcript import Transcript

MAX_POOLED_CLAIMS = 24


def pool_claims_bounded(per_member: dict, members: Sequence[Model]) -> list:
    """Round-robin claims under the shared remote fact-check call budget."""
    queues = [list(per_member.get(member.name, [])) for member in members]
    pooled = []
    while len(pooled) < MAX_POOLED_CLAIMS and any(queues):
        for queue in queues:
            if queue and len(pooled) < MAX_POOLED_CLAIMS:
                pooled.append(queue.pop(0))
    return pooled


@dataclass
class ResearchVerdict:
    ledger: ClaimLedger
    transcript: Transcript


def run_research(
    members: Sequence[Model],
    question: str,
    chunks: list[SourceChunk],
    skeptic: Optional[Model] = None,
    retriever=None,
) -> ResearchVerdict:
    if not members:
        raise ValueError("research needs at least one member")
    if not chunks:
        raise ValueError("research needs at least one source chunk")

    t = Transcript(question=question)
    per_member = grounded_blind(members, question, chunks, t, retriever=retriever)

    # grounded_blind already assigned global, stable ids (K1, K2, …) that match
    # the transcript metadata; just pool them in member order.
    # Bound fact-check amplification and interleave members fairly so one
    # injection-driven verbose member cannot consume the entire remote-call
    # budget before another member contributes.
    pooled = pool_claims_bounded(per_member, members)

    checker = skeptic or members[0]
    for claim in pooled:
        fact_check(checker, claim, chunks, t)

    ledger = ClaimLedger(question, pooled).finalize()
    return ResearchVerdict(ledger=ledger, transcript=t)
