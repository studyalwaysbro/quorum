"""FastAPI server that streams a Quorum deliberation live over SSE.

Security model (Stage 1):
  * No prompts or keys in URLs. A run is created with POST /api/runs (JSON
    body) which returns an unguessable run_id; the SSE stream is then read
    from GET /api/runs/{run_id}/events.
  * POST is gated by a custom CSRF header + a localhost Origin check; the
    server binds 127.0.0.1 by default.
  * Local mode runs ONLY allowlisted catalog CLIs — never an arbitrary PATH
    binary — each with a scrubbed subprocess env so provider keys can't leak.
  * /api/capabilities returns booleans/metadata only, never a key.

Run:  python -m quorum.web      (then open http://127.0.0.1:8000)
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from quorum.adapters import Model
from quorum.providers import LOCAL_CATALOG, Audition, build_local_model, get_spec, probe
from quorum.providers.catalog import LocalModelSpec
from quorum.rounds import (
    adversarial_round,
    blind_round,
    consensus_map_round,
    critique_round,
    synthesis_round,
)
from quorum.scorecard import agreement_delta, delta_to_dict, stage_agreement
from quorum.transcript import Transcript
from quorum.votes import normalize_labels, revote_round, tally_votes, vote_round
from quorum.web.demo import (
    SKEPTIC_NAME,
    default_demo_roster,
    demo_member,
    demo_skeptic,
)

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOCAL_PROBE_TIMEOUT = 45

app = FastAPI(title="Quorum", description="Watch a multi-model council deliberate")

# In-memory, one-shot run registry: run_id -> spec. Never persisted.
_RUNS: dict[str, dict] = {}


# --------------------------------------------------------------- security
def _require_local_post(request: Request) -> None:
    """CSRF defense for state-changing POSTs.

    A custom header is the real control: a cross-origin page cannot set
    ``X-Quorum-CSRF`` without a CORS preflight we never grant. The Origin
    check is belt-and-suspenders; non-browser clients (curl/tests) send no
    Origin and are allowed only because the header is still required.
    """
    if request.headers.get("x-quorum-csrf") != "1":
        raise HTTPException(status_code=403, detail="missing CSRF header")
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin is not None and urlparse(origin).hostname not in LOCAL_HOSTS:
        raise HTTPException(status_code=403, detail="cross-origin request blocked")


# --------------------------------------------------------------- routes
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/capabilities")
def capabilities() -> dict:
    """Model metadata only — never a key and never a subprocess probe."""
    local = []
    for spec in LOCAL_CATALOG:
        entry = {
            "id": spec.id, "label": spec.label, "agentic": spec.agentic,
            "enabled_by_default": spec.enabled_by_default,
            "available": spec.available, "note": spec.note,
        }
        local.append(entry)
    return {"demo": default_demo_roster(), "local": local, "skeptic_name": SKEPTIC_NAME}


class RunRequest(BaseModel):
    question: str
    mode: str = "demo"                 # "demo" | "local"
    members: list[str] = Field(default_factory=list)
    skeptic: Optional[str] = None
    allow_agentic: bool = False
    labels: list[str] = Field(default_factory=list)   # decision mode → scorecard
    revote: bool = True


@app.post("/api/runs")
def create_run(req: RunRequest, request: Request) -> dict:
    _require_local_post(request)
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="empty question")
    if req.mode not in ("demo", "local"):
        raise HTTPException(status_code=400, detail="mode must be 'demo' or 'local'")

    members = req.members or (default_demo_roster()["members"] if req.mode == "demo" else [])
    if not members:
        raise HTTPException(status_code=400, detail="pick at least one member")

    if req.mode == "local":   # allowlist enforcement — no arbitrary PATH exec
        _validate_local_selection(members, req.skeptic, req.allow_agentic)

    labels = None
    if req.labels:
        try:
            labels = normalize_labels(req.labels)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"bad labels: {exc}")

    run_id = secrets.token_urlsafe(18)
    _RUNS[run_id] = {
        "question": req.question, "mode": req.mode,
        "members": members, "skeptic": req.skeptic,
        "labels": labels, "revote": req.revote,
    }
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str) -> StreamingResponse:
    spec = _RUNS.pop(run_id, None)     # one-shot: consume on read
    if spec is None:
        raise HTTPException(status_code=404, detail="unknown or already-consumed run")
    gen = deliberation_events(
        spec["question"], spec["mode"], spec["members"], spec["skeptic"],
        labels=spec.get("labels"), revote=spec.get("revote", True),
    )
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------- engine
def _build_models(mode: str, member_ids: list[str], skeptic_id: Optional[str]):
    if mode == "local":
        members: list[Model] = [build_local_model(mid) for mid in member_ids]
        skeptic = build_local_model(skeptic_id) if skeptic_id else None
        return members, skeptic
    members = [demo_member(name) for name in member_ids]
    skeptic = demo_skeptic(skeptic_id) if skeptic_id else None
    return members, skeptic


def _validate_local_selection(
    member_ids: list[str], skeptic_id: Optional[str], allow_agentic: bool
) -> None:
    specs = _local_specs(member_ids, skeptic_id)
    unavailable = [spec.id for spec in specs if not spec.available]
    if unavailable:
        raise HTTPException(
            status_code=400,
            detail=f"local model not installed: {', '.join(unavailable)}",
        )

    agentic = [spec.id for spec in specs if spec.agentic]
    if agentic and not allow_agentic:
        raise HTTPException(
            status_code=403,
            detail=f"agentic local model requires explicit opt-in: {', '.join(agentic)}",
        )

    for spec in specs:
        audition = probe(spec, timeout=LOCAL_PROBE_TIMEOUT)
        if not audition.ok:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"local model failed audition: {spec.id} "
                    f"({_public_audition_reason(audition)})"
                ),
            )


def _local_specs(member_ids: list[str], skeptic_id: Optional[str]) -> list[LocalModelSpec]:
    ids = []
    for model_id in member_ids + ([skeptic_id] if skeptic_id else []):
        if model_id not in ids:
            ids.append(model_id)

    specs = []
    for model_id in ids:
        try:
            specs.append(get_spec(model_id))
        except KeyError:
            raise HTTPException(status_code=400, detail=f"unknown local model: {model_id}")
    return specs


def _public_audition_reason(audition: Audition) -> str:
    reason = audition.reason.lower()
    if "authenticated" in reason or "sign-in" in reason:
        return "not authenticated"
    if "context" in reason:
        return "leaks local or agent context"
    if "canary" in reason:
        return "did not pass canary"
    if "empty" in reason:
        return "empty response"
    if "verbose" in reason or "single-turn" in reason:
        return "not single-turn compliant"
    if "not installed" in reason:
        return "not installed"
    return "probe failed"


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def deliberation_events(
    question: str, mode: str, members: list[str], skeptic_name: Optional[str],
    labels: Optional[list[str]] = None, revote: bool = True,
) -> Iterator[str]:
    """Drive the rounds in council order, emitting an SSE event per turn.

    In decision mode (``labels`` set) the council also casts a categorical vote
    after the blind round and a revote after the adversary; the agreement
    scorecard is emitted as ``scorecard`` events. Vote turns drive the scorecard,
    not the visible timeline.
    """
    pace = 0.32 if mode == "demo" else 0.0
    multi = len(members) > 1
    decision = bool(labels) and multi

    member_models, skeptic = _build_models(mode, members, skeptic_name)
    distiller = member_models[0] if len(member_models) > 1 else None
    t = Transcript(question=question)
    seen = 0
    blind_votes: dict = {}
    blind_conf: dict = {}

    def flush_new():
        nonlocal seen
        for turn in t.turns[seen:]:
            if turn.round in ("vote", "revote"):   # votes feed the scorecard, not cards
                continue
            yield _sse({"type": "turn", "round": turn.round, "model": turn.model,
                        "response": turn.response})
            if pace:
                time.sleep(pace)
        seen = len(t.turns)

    stages = ["blind"]
    if multi:
        stages += ["critique", "consensus_map"]
    if skeptic is not None:
        stages.append("adversarial")
    stages.append("synthesis")

    yield _sse({"type": "start", "question": question, "mode": mode, "stages": stages,
                "members": [m.name for m in member_models],
                "skeptic": skeptic.name if skeptic else None,
                "decision": decision, "labels": list(labels) if labels else None})

    try:
        yield _sse({"type": "round", "round": "blind", "status": "running"})
        blind_round(member_models, question, t)
        yield from flush_new()
        if decision:
            vr = vote_round(member_models, question, labels, t)
            blind_votes, blind_conf = vr.votes, vr.confidences
            seen = len(t.turns)   # consume vote turns silently
            yield _sse({"type": "scorecard", "stage": "blind",
                        "snapshot": asdict(stage_agreement("blind", blind_votes, blind_conf, labels))})
        yield _sse({"type": "round", "round": "blind", "status": "done"})

        if multi:
            yield _sse({"type": "round", "round": "critique", "status": "running"})
            critique_round(member_models, question, t)
            yield from flush_new()
            yield _sse({"type": "round", "round": "critique", "status": "done"})

            yield _sse({"type": "round", "round": "consensus_map", "status": "running"})
            consensus_map_round(question, t, distiller=distiller)
            yield from flush_new()
            yield _sse({"type": "round", "round": "consensus_map", "status": "done"})

        if skeptic is not None:
            yield _sse({"type": "round", "round": "adversarial", "status": "running"})
            adversarial_round(skeptic, question, t)
            yield from flush_new()
            yield _sse({"type": "round", "round": "adversarial", "status": "done"})

        if decision and revote:
            rv = revote_round(member_models, question, labels, t,
                              blind_votes, blind_conf, tally_votes(blind_votes, labels))
            seen = len(t.turns)
            delta = agreement_delta(blind_votes, blind_conf, rv.votes, rv.confidences, labels)
            yield _sse({"type": "scorecard", "stage": "final", "delta": delta_to_dict(delta)})

        yield _sse({"type": "round", "round": "synthesis", "status": "running"})
        synthesizer = member_models[0]
        answer = synthesis_round(synthesizer, question, t)
        yield from flush_new()
        yield _sse({"type": "round", "round": "synthesis", "status": "done"})

        yield _sse({"type": "answer", "answer": answer, "by": synthesizer.name})
        yield _sse({"type": "transcript", "transcript": t.to_dict()})
        yield _sse({"type": "done"})
    except Exception as exc:  # never 500 mid-stream
        yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
