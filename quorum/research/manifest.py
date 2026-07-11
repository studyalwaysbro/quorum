"""Reviewable, hash-bound manifests for attachment egress approval."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Iterable

from quorum.research.attachments import enforce_remote_source_cap
from quorum.research.schema import SourceChunk

MANIFEST_VERSION = 1
PREVIEW_CHARS = 8_000


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
    return document


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
