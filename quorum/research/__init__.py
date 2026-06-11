"""Research mode — source-grounded deliberation with validated citations.

A separate pipeline from the open-ended council: the panel reads an uploaded
source, answers in *cited claims*, and the adversary fact-checks each atomic
claim against the source. The primary artifact is the Claim Ledger — an
auditable record of what survived, what was qualified, and what Quorum refused
to conclude. See ``quorum/research/schema.py`` for the data model.
"""

from quorum.research.schema import (
    Citation,
    Claim,
    SourceChunk,
    parse_claims,
    validate_citations,
)
from quorum.research.ingest import chunk_text
from quorum.research.retrieval import WholeContextRetriever

__all__ = [
    "SourceChunk",
    "Citation",
    "Claim",
    "parse_claims",
    "validate_citations",
    "chunk_text",
    "WholeContextRetriever",
]
