import copy

import pytest

from quorum.research.manifest import build_manifest, manifest_chunks, validate_manifest
from quorum.research.schema import SourceChunk


def test_manifest_hash_binds_chunks_question_and_providers():
    manifest = build_manifest(
        "q", [SourceChunk("C1", "redacted text", "a.txt")],
        [{"id": "openai", "model": "gpt-5.6-sol"}],
        files=1, secret_findings=1, injection_hints=0,
    )
    assert validate_manifest(manifest) == manifest
    assert manifest_chunks(manifest)[0].text == "redacted text"
    tampered = copy.deepcopy(manifest)
    tampered["chunks"][0]["text"] = "changed"
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_manifest(tampered)


def test_manifest_enforces_aggregate_utf8_egress_cap():
    huge = SourceChunk("C1", "😀" * 200_000, "huge.txt")
    with pytest.raises(ValueError, match="too large for remote"):
        build_manifest(
            "q", [huge], [{"id": "xai", "model": "grok-4.5"}],
            files=1, secret_findings=0, injection_hints=0,
        )
