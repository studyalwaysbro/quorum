"""Research-mode trust core — ingest, claim parsing, citation validation."""

import pytest

from quorum.research import WholeContextRetriever, chunk_text, parse_claims, validate_citations
from quorum.research.schema import Citation, Claim, ClaimParseError, SourceChunk


# ---- ingest ------------------------------------------------------------
def test_chunking_gives_stable_ids():
    text = "First para about cats.\n\nSecond para about dogs.\n\n  \n\nThird."
    chunks = chunk_text(text, source="doc.txt")
    assert [c.id for c in chunks] == ["C1", "C2", "C3"]
    assert chunks[0].source == "doc.txt"
    assert "cats" in chunks[0].text


def test_chunking_rejects_oversized_source():
    with pytest.raises(ValueError):
        chunk_text("x" * 50, max_total=10)


def test_long_paragraph_splits_on_sentences():
    para = " ".join(f"Sentence number {i} here." for i in range(60))
    chunks = chunk_text(para)
    assert len(chunks) > 1
    assert all(c.text for c in chunks)


# ---- retrieval ---------------------------------------------------------
def test_whole_context_packet_tags_ids():
    chunks = [SourceChunk("C1", "alpha", "d"), SourceChunk("C2", "beta", "d")]
    pkt = WholeContextRetriever().packet(chunks, "q")
    assert "[C1] alpha" in pkt and "[C2] beta" in pkt


# ---- claim parsing -----------------------------------------------------
def test_parse_claims_json_object():
    text = '{"claims":[{"text":"Cats nap.","citations":[{"chunk_id":"C1","quote":"cats sleep"}],"confidence":4}]}'
    claims = parse_claims(text)
    assert len(claims) == 1 and claims[0].id == "K1"
    assert claims[0].confidence == 4
    assert claims[0].citations[0].chunk_id == "C1"


def test_parse_claims_handles_fenced_and_bare_array():
    fenced = 'Here:\n```json\n[{"text":"X","citations":[]}]\n```\nthanks'
    assert parse_claims(fenced)[0].text == "X"
    bare = '[{"text":"Y","citations":[{"chunk_id":"C2","quote":"q"}]}]'
    assert parse_claims(bare)[0].citations[0].chunk_id == "C2"


def test_parse_claims_rejects_garbage():
    with pytest.raises(ClaimParseError):
        parse_claims("no json here at all")
    with pytest.raises(ClaimParseError):
        parse_claims('{"claims":[]}')


# ---- citation validation (the trust core) ------------------------------
def test_validate_marks_present_quote_valid_and_others_invalid():
    chunks = [SourceChunk("C1", "The cat sat on the warm mat.", "d")]
    claims = [Claim(id="K1", text="cat claim", citations=[
        Citation("C1", "cat sat on the warm"),   # present -> valid
        Citation("C1", "dog flew to mars"),       # absent -> invalid
        Citation("C9", "cat sat"),                # unknown chunk -> invalid
    ])]
    validate_citations(claims, chunks)
    c = claims[0]
    assert c.citations[0].valid is True and c.citations[0].start is not None
    assert c.citations[1].valid is False
    assert c.citations[2].valid is False
    assert c.has_valid_citation is True


def test_validate_is_whitespace_and_case_insensitive():
    chunks = [SourceChunk("C1", "Machine   learning\nis  powerful.", "d")]
    claims = [Claim(id="K1", text="x", citations=[Citation("C1", "MACHINE LEARNING is powerful")])]
    validate_citations(claims, chunks)
    assert claims[0].citations[0].valid is True


def test_claim_without_valid_citation_cannot_be_supported():
    chunks = [SourceChunk("C1", "abc", "d")]
    claims = [Claim(id="K1", text="x", citations=[Citation("C1", "not present here")])]
    validate_citations(claims, chunks)
    assert claims[0].has_valid_citation is False
