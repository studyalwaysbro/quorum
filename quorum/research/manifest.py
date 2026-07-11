"""Reviewable, hash-bound manifests for attachment egress approval."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Iterable

from quorum.research.attachments import enforce_remote_source_cap
from quorum.research.schema import SourceChunk

MANIFEST_VERSION = 2
PREVIEW_CHARS = 8_000
PROVIDER_SNAPSHOT_KEYS = frozenset({
    "id", "label", "provider", "model", "model_identity_verified",
    "endpoint", "protocol", "trust",
    "reasoning_requested", "reasoning_verified", "max_output_tokens",
    "routing_requested", "routing_verified", "egress_snapshot_hash", "receives",
})
RESEARCH_POLICY_VERSION = "grounded-claims-v1"


def build_manifest(
    question: str,
    chunks: Iterable[SourceChunk],
    providers: list[dict],
    *,
    files: int,
    secret_findings: int,
    injection_hints: int,
) -> dict:
    chunks = list(chunks)
    enforce_remote_source_cap(chunks, question)
    core = {
        "version": MANIFEST_VERSION,
        "research_policy": RESEARCH_POLICY_VERSION,
        "question": question,
        "providers": providers,
        "files": files,
        "secret_findings_redacted": secret_findings,
        "injection_hints": injection_hints,
        "chunks": [
            {"id": chunk.id, "source": chunk.source, "text": chunk.text}
            for chunk in chunks
        ],
    }
    digest = hashlib.sha256(_canonical(core)).hexdigest()
    return {**core, "manifest_hash": digest}


def validate_manifest(document: dict) -> dict:
    if not isinstance(document, dict) or document.get("version") != MANIFEST_VERSION:
        raise ValueError("unsupported attachment manifest")
    if document.get("research_policy") != RESEARCH_POLICY_VERSION:
        raise ValueError("unsupported attachment research policy")
    supplied = document.get("manifest_hash")
    core = {key: value for key, value in document.items() if key != "manifest_hash"}
    expected = hashlib.sha256(_canonical(core)).hexdigest()
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
        raise ValueError("attachment manifest hash mismatch")
    chunks = manifest_chunks(document)
    enforce_remote_source_cap(chunks, str(document.get("question", "")))
    providers = document.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ValueError("attachment manifest has no providers")
    validate_provider_snapshots(providers)
    return document


def validate_provider_snapshots(providers: list[dict]) -> None:
    """Reject hash-valid but semantically loose provider declarations."""
    seen = set()
    for item in providers:
        if not isinstance(item, dict) or set(item) != PROVIDER_SNAPSHOT_KEYS:
            raise ValueError("attachment manifest has an invalid provider snapshot")
        strings = ("id", "label", "provider", "model", "endpoint", "protocol", "trust",
                   "reasoning_requested", "egress_snapshot_hash")
        if not all(isinstance(item.get(key), str) and item[key] for key in strings):
            raise ValueError("attachment manifest has an invalid provider snapshot")
        if item["id"] in seen:
            raise ValueError("attachment manifest has duplicate provider profiles")
        seen.add(item["id"])
        if item["model_identity_verified"] is not False \
                or item["reasoning_verified"] is not False \
                or item["max_output_tokens"] != 4096:
            raise ValueError("attachment manifest has an invalid provider policy")
        fingerprint = item["egress_snapshot_hash"]
        if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
            raise ValueError("attachment manifest has an invalid provider fingerprint")
        fingerprint_core = {key: value for key, value in item.items() if key != "egress_snapshot_hash"}
        expected_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_core, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("ascii")
        ).hexdigest()
        if not hmac.compare_digest(fingerprint, expected_fingerprint):
            raise ValueError("attachment manifest provider fingerprint mismatch")
        if item["routing_verified"] is not False:
            raise ValueError("attachment manifest has an invalid routing claim")
        routing = item["routing_requested"]
        receives = item["receives"]
        if not isinstance(receives, list) or not receives \
                or any(round_id not in {"grounded_blind", "fact_check"} for round_id in receives) \
                or len(receives) != len(set(receives)):
            raise ValueError("attachment manifest has invalid provider recipients")
        if routing is not None:
            if not isinstance(routing, dict) or set(routing) != {
                "only", "allow_fallbacks", "require_parameters", "data_collection", "zdr"
            }:
                raise ValueError("attachment manifest has an invalid provider route")
            if not isinstance(routing["only"], list) or len(routing["only"]) != 1 \
                    or not isinstance(routing["only"][0], str) \
                    or routing["allow_fallbacks"] is not False \
                    or routing["require_parameters"] is not True \
                    or routing["data_collection"] != "deny" or routing["zdr"] is not True:
                raise ValueError("attachment manifest has an unsafe provider route")


def manifest_chunks(document: dict) -> list[SourceChunk]:
    raw = document.get("chunks")
    if not isinstance(raw, list) or not raw:
        raise ValueError("attachment manifest has no chunks")
    chunks = []
    for item in raw:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) for key in ("id", "source", "text")
        ):
            raise ValueError("invalid attachment manifest chunk")
        chunks.append(SourceChunk(item["id"], item["text"], item["source"]))
    return chunks


def manifest_preview(document: dict, limit: int = PREVIEW_CHARS) -> str:
    text = "\n\n".join(
        f"[{item['id']}] ({item['source']})\n{item['text']}"
        for item in document.get("chunks", [])
    )
    return text[:limit]


def _canonical(document: dict) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
