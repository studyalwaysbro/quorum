"""Adapter audition gate.

Before a local model is offered to a council, it's probed with a tiny canary
prompt and graded: did the CLI run, return clean completion text, stay
single-turn, and NOT leak operator/agent context? Models that fail are
quarantined with a human-readable reason instead of silently poisoning a
deliberation. This is what auto-catches a mis-wired transport, an
unauthenticated CLI, or an agentic wrapper that rambles about local tasks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from quorum.adapters import Model
from quorum.providers.catalog import LocalModelSpec, build_local_model

CANARY = "QUORUM_OK"
CANARY_PROMPT = (
    f"This is a connectivity check. Reply with exactly this token and nothing "
    f"else: {CANARY}"
)

# If a completion contains any of these, the model is leaking local/agent
# context (e.g. the agentic deepseek wrapper talking about logging) and must
# not sit on a council.
OPERATOR_MARKERS = (
    "permission dialog",
    "council log",
    "workspace/logs",
    "research inbox",
    "/home/",
    "awaiting your approval",
    "i'll flag",
    "operator memory",
)

# Markers that mean the CLI needs interactive auth rather than answering.
AUTH_MARKERS = ("sign in", "signing in", "oauth", "log in to", "authenticate", "auth.x.ai")


@dataclass
class Audition:
    model_id: str
    ok: bool
    reason: str
    latency_s: Optional[float] = None
    sample: str = ""


def audit_output(model_id: str, output: str, latency_s: Optional[float] = None) -> Audition:
    """Pure classifier: grade a completion string. Easy to unit-test."""
    stripped = output.strip()
    sample = stripped[:200]
    if output.startswith("[quorum CLIModel error"):
        return Audition(model_id, False, output.strip("[]"), latency_s, sample)

    low = stripped.lower()
    for marker in AUTH_MARKERS:
        if marker in low:
            return Audition(model_id, False, "not authenticated (CLI wants sign-in)", latency_s, sample)
    for marker in OPERATOR_MARKERS:
        if marker in low:
            return Audition(model_id, False, f"leaks local/agent context ({marker!r})", latency_s, sample)

    if stripped == CANARY:
        return Audition(model_id, True, "ok", latency_s, sample)
    if CANARY.lower() in low:
        return Audition(model_id, False, "canary plus extra output", latency_s, sample)
    if len(sample) == 0:
        return Audition(model_id, False, "empty response", latency_s, sample)
    if len(output.strip()) > 400:
        return Audition(model_id, False, "verbose / not single-turn-compliant", latency_s, sample)
    return Audition(model_id, False, "did not echo audition canary exactly", latency_s, sample)


def probe(spec: LocalModelSpec, timeout: float = 45) -> Audition:
    """Run the real CLI once and grade it. Returns a quarantine reason on failure."""
    if not spec.available:
        return Audition(spec.id, False, f"not installed ({spec.binary} not on PATH)")
    model = build_local_model(spec.id, timeout=timeout)
    return probe_model(spec.id, model)


def probe_model(model_id: str, model: Model) -> Audition:
    start = time.monotonic()
    try:
        output = model.complete(CANARY_PROMPT)
    except Exception as exc:  # never let a probe crash the caller
        return Audition(model_id, False, f"probe raised {type(exc).__name__}: {exc}")
    return audit_output(model_id, output, latency_s=round(time.monotonic() - start, 2))
