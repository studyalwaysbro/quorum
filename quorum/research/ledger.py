"""The Claim Ledger — research mode's primary artifact.

Not a pretty cited answer: an auditable record of every claim's disposition.
Each claim is *kept*, *qualified*, or *dropped* based on its effective verdict
(which already refuses 'Supported' without a valid citation). The dropped pile
is surfaced explicitly as "what Quorum refused to conclude" — the differentiator
no research tool ships.
"""

from __future__ import annotations

from dataclasses import dataclass

from quorum.research.schema import Claim


def disposition_for(claim: Claim) -> str:
    verdict = claim.effective_verdict        # demotes uncited 'Supported'
    if verdict == "Supported":
        return "kept"
    if verdict == "PartiallySupported":
        return "qualified"
    return "dropped"                         # Unsupported / Contradicted / unverified


@dataclass
class ClaimLedger:
    question: str
    claims: list[Claim]

    def finalize(self) -> "ClaimLedger":
        for claim in self.claims:
            claim.disposition = disposition_for(claim)
        return self

    def _by(self, disposition: str) -> list[Claim]:
        return [c for c in self.claims if disposition_for(c) == disposition]

    @property
    def kept(self) -> list[Claim]:
        return self._by("kept")

    @property
    def qualified(self) -> list[Claim]:
        return self._by("qualified")

    @property
    def dropped(self) -> list[Claim]:
        """What Quorum refused to conclude."""
        return self._by("dropped")

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "counts": {
                "kept": len(self.kept),
                "qualified": len(self.qualified),
                "dropped": len(self.dropped),
                "total": len(self.claims),
            },
            "claims": [c.to_dict() for c in self.claims],
            "refused_to_conclude": [c.to_dict() for c in self.dropped],
        }
