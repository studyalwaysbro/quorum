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
from quorum.research.rounds import fact_check, grounded_blind
from quorum.research.ledger import ClaimLedger, disposition_for
from quorum.research.pipeline import ResearchVerdict, run_research
from quorum.research.attachments import (
    AttachmentResult,
    Advisory,
    combine_chunks,
    ingest_attachment_bytes,
    ingest_attachment_paths,
    redact_text,
    scan_advisories,
)

__all__ = [
    "SourceChunk",
    "Citation",
    "Claim",
    "parse_claims",
    "validate_citations",
    "chunk_text",
    "WholeContextRetriever",
    "grounded_blind",
    "fact_check",
    "ClaimLedger",
    "disposition_for",
    "ResearchVerdict",
    "run_research",
    "AttachmentResult",
    "Advisory",
    "ingest_attachment_bytes",
    "ingest_attachment_paths",
    "combine_chunks",
    "scan_advisories",
    "redact_text",
]
