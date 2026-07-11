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
import threading
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
from quorum.providers.remote import (
    annotate_remote_transcript, build_remote_model_from_profile,
    ensure_attachment_eligible, get_provider_profile, provider_snapshot,
    remote_capabilities, remote_identity,
)
from quorum.rounds import (
    adversarial_round,
    blind_round,
    consensus_map_round,
    critique_round,
    synthesis_round,
)
from quorum.personas import get_persona, persona_catalog
from quorum.research.attachments import (
    MAX_FILE_BYTES as ATTACHMENT_FILE_BYTES,
    MAX_FILES as ATTACHMENT_MAX_FILES,
    MAX_TOTAL_BYTES as ATTACHMENT_TOTAL_BYTES,
    combine_chunks,
    ingest_attachment_bytes,
    redact_text,
    scan_advisories,
)
from quorum.research.ledger import ClaimLedger
from quorum.research.manifest import build_manifest, manifest_preview
from quorum.research.pipeline import pool_claims_bounded
from quorum.research.rounds import fact_check, grounded_blind
from quorum.scorecard import agreement_delta, delta_to_dict, stage_agreement
from quorum.transcript import Transcript
from quorum.votes import normalize_labels, revote_round, tally_votes, vote_round
from quorum.web.demo_research import demo_research_member
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


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

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
    host_value = request.headers.get("host", "")
    host_url = urlparse(f"http://{host_value}")
    if not host_value or host_url.username or host_url.password \
            or host_url.hostname not in LOCAL_HOSTS:
        raise HTTPException(status_code=403, detail="untrusted Host header")
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin is not None:
        parsed_origin = urlparse(origin)
        if origin == "null" or parsed_origin.scheme not in {"http", "https"} \
                or parsed_origin.username or parsed_origin.password \
                or parsed_origin.hostname not in LOCAL_HOSTS \
                or parsed_origin.netloc.lower() != host_value.lower():
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
    return {"demo": default_demo_roster(), "local": local,
            "remote": remote_capabilities(), "skeptic_name": SKEPTIC_NAME,
            "personas": persona_catalog()}


class RunRequest(BaseModel):
    question: str
    mode: str = "demo"                 # "demo" | "local"
    members: list[str] = Field(default_factory=list)
    skeptic: Optional[str] = None
    allow_agentic: bool = False
    allow_remote: bool = False
    remote_fingerprints: dict[str, str] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)   # decision mode → scorecard
    revote: bool = True
    persona: Optional[str] = None                     # adversary persona id


@app.post("/api/runs")
def create_run(req: RunRequest, request: Request) -> dict:
    _require_local_post(request)
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="empty question")
    if req.mode not in ("demo", "local", "remote"):
        raise HTTPException(status_code=400, detail="mode must be 'demo', 'local', or 'remote'")

    members = req.members or (default_demo_roster()["members"] if req.mode == "demo" else [])
    if not members:
        raise HTTPException(status_code=400, detail="pick at least one member")

    if req.mode == "local":   # allowlist enforcement — no arbitrary PATH exec
        _validate_local_selection(members, req.skeptic, req.allow_agentic)
    remote_snapshots = None
    remote_profiles = None
    remote_skeptic = None
    if req.mode == "remote":
        if not req.allow_remote:
            raise HTTPException(status_code=403, detail="remote provider egress requires explicit consent")
        selected = members + ([req.skeptic] if req.skeptic else [])
        if len(selected) != len(set(selected)):
            raise HTTPException(status_code=400, detail="remote provider profiles must be unique")
        configured = {item["id"]: item["configured"] for item in remote_capabilities()}
        resolved = []
        for profile_id in selected:
            try:
                profile = get_provider_profile(profile_id)
            except KeyError:
                raise HTTPException(status_code=400, detail=f"unknown remote provider profile: {profile_id}")
            if not configured.get(profile_id):
                raise HTTPException(status_code=400, detail=f"remote provider is not configured: {profile_id}")
            if req.remote_fingerprints.get(profile_id) != profile.fingerprint:
                raise HTTPException(status_code=409, detail="remote profile changed since review; refresh and consent again")
            resolved.append(profile)
        if set(req.remote_fingerprints) != set(selected):
            raise HTTPException(status_code=400, detail="remote consent fingerprints do not match the roster")
        remote_profiles = resolved[:len(members)]
        remote_skeptic = resolved[-1] if req.skeptic else None

    labels = None
    if req.labels:
        try:
            labels = normalize_labels(req.labels)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"bad labels: {exc}")
        for label in labels:   # defense in depth vs HTML injection into the scorecard
            if len(label) > 40 or any(c in label for c in "<>"):
                raise HTTPException(
                    status_code=400,
                    detail="labels must be short and contain no angle brackets",
                )

    if req.persona is not None:
        try:
            get_persona(req.persona)
        except KeyError:
            raise HTTPException(status_code=400, detail=f"unknown adversary persona: {req.persona}")

    if req.mode == "remote":
        remote_snapshots = _regular_provider_snapshots(
            remote_profiles, remote_skeptic,
            decision=bool(labels) and len(members) > 1,
            revote=req.revote,
        )

    run_id = secrets.token_urlsafe(18)
    _RUNS[run_id] = {
        "question": req.question, "mode": req.mode,
        "members": members, "skeptic": req.skeptic,
        "remote_snapshots": remote_snapshots,
        "remote_profiles": remote_profiles, "remote_skeptic": remote_skeptic,
        "labels": labels, "revote": req.revote, "persona": req.persona,
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
        persona=spec.get("persona"), remote_snapshots=spec.get("remote_snapshots"),
        remote_profiles=spec.get("remote_profiles"), remote_skeptic=spec.get("remote_skeptic"),
    )
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------- engine
def _build_models(
    mode: str, member_ids: list[str], skeptic_id: Optional[str],
    remote_snapshots: Optional[list[dict]] = None,
    remote_profiles=None, remote_skeptic=None,
    decision: bool = False, revote: bool = True,
):
    if mode == "local":
        members: list[Model] = [build_local_model(mid) for mid in member_ids]
        skeptic = build_local_model(skeptic_id) if skeptic_id else None
        return members, skeptic
    if mode == "remote":
        profiles = list(remote_profiles) if remote_profiles is not None else [
            get_provider_profile(profile_id) for profile_id in member_ids
        ]
        skeptic_profile = remote_skeptic if remote_skeptic is not None else (
            get_provider_profile(skeptic_id) if skeptic_id else None
        )
        current = _regular_provider_snapshots(
            profiles, skeptic_profile, decision=decision, revote=revote,
        )
        if remote_snapshots is not None and current != remote_snapshots:
            raise ValueError("remote provider profile changed after consent")
        members = [build_remote_model_from_profile(profile) for profile in profiles]
        skeptic = build_remote_model_from_profile(skeptic_profile) if skeptic_profile else None
        return members, skeptic
    members = [demo_member(name) for name in member_ids]
    skeptic = demo_skeptic(skeptic_id) if skeptic_id else None
    return members, skeptic


def _regular_provider_snapshots(
    profiles, skeptic_profile=None, *, decision: bool = False, revote: bool = True,
) -> list[dict]:
    snapshots = []
    multi = len(profiles) > 1
    for index, profile in enumerate(profiles):
        receives = ["blind"]
        if decision:
            receives.append("vote")
        if multi:
            receives.append("critique")
            if index == 0:
                receives.append("consensus_map")
        if decision and revote:
            receives.append("revote")
        if index == 0:
            receives.append("synthesis")
        snapshots.append(provider_snapshot(profile, receives=tuple(receives)))
    if skeptic_profile:
        snapshots.append(provider_snapshot(
            skeptic_profile, receives=("adversarial",),
        ))
    return snapshots


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
    persona: Optional[str] = None,
    remote_snapshots: Optional[list[dict]] = None,
    remote_profiles=None, remote_skeptic=None,
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

    member_models, skeptic = _build_models(
        mode, members, skeptic_name, remote_snapshots, remote_profiles, remote_skeptic,
        decision, revote,
    )
    remote_models = member_models + ([skeptic] if skeptic is not None else []) \
        if mode == "remote" else []
    identity_by_profile = {}
    if mode == "remote" and remote_snapshots:
        identity_by_profile = {
            model.name: (model, snapshot)
            for model, snapshot in zip(remote_models, remote_snapshots)
        }
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
            identity_pair = identity_by_profile.get(turn.model)
            identity = None
            if identity_pair is not None:
                identity = remote_identity(*identity_pair)
                turn.meta["remote_identity"] = identity
            yield _sse({"type": "turn", "round": turn.round, "model": turn.model,
                        "response": turn.response, "remote_identity": identity})
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
                "decision": decision, "labels": list(labels) if labels else None,
                "remote_profiles": remote_snapshots if mode == "remote" else None})

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
            adversarial_round(skeptic, question, t, persona=persona)
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
        if remote_models and remote_snapshots:
            annotate_remote_transcript(t, remote_models, remote_snapshots)
        yield _sse({"type": "transcript", "transcript": t.to_dict()})
        yield _sse({"type": "done"})
    except Exception:  # never expose subprocess/provider errors mid-stream
        yield _sse({"type": "error", "message": "council run failed safely; inspect local diagnostics"})


# ============================ research mode (uploads) ============================
# A LOCAL-app surface: 127.0.0.1 + CSRF header is a local-app trust model, not
# internet auth. The #1 risk is a hostile uploaded document prompt-injecting an
# AGENTIC local CLI into reading files / running tools, so research runs use
# demo or NON-AGENTIC local models only — agentic models are banned here.

_RESEARCH_RUNS: dict[str, dict] = {}
_RESEARCH_TTL = 600                  # seconds a pending run lives
_MAX_PENDING_RESEARCH = 8
_GLOBAL_SOURCE_CHAR_CAP = 600_000    # across all stored research runs
_QUESTION_CHAR_CAP = 2_000


_research_claim_lock = threading.Lock()


def _evict_expired_research(now: float) -> None:
    for rid in [r for r, v in _RESEARCH_RUNS.items() if v["expires_at"] < now]:
        _RESEARCH_RUNS.pop(rid, None)


def _stored_source_chars() -> int:
    return sum(v["source_chars"] for v in _RESEARCH_RUNS.values())


async def _read_capped(upload, per_file_cap: int, remaining_total: int) -> bytes:
    """Read an UploadFile in bounded chunks, rejecting BEFORE buffering past the
    per-file or remaining-total cap. Starlette's ``max_part_size`` does not bound
    file parts in all versions, so we enforce it here."""
    buf = bytearray()
    while True:
        chunk = await upload.read(65536)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > per_file_cap:
            raise HTTPException(status_code=413, detail=f"a file exceeds {per_file_cap} bytes")
        if len(buf) > remaining_total:
            raise HTTPException(status_code=413, detail="upload exceeds the total size cap")
    return bytes(buf)


@app.post("/api/research-runs")
async def create_research_run(request: Request) -> dict:
    _require_local_post(request)                       # CSRF/Origin BEFORE parsing
    content_length = request.headers.get("content-length")
    if content_length is None:
        raise HTTPException(status_code=411, detail="Content-Length required")
    try:
        declared = int(content_length)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad Content-Length")
    if declared > ATTACHMENT_TOTAL_BYTES + 128 * 1024:  # body cap + form overhead
        raise HTTPException(status_code=413, detail="upload too large")
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "multipart/form-data":
        raise HTTPException(status_code=415, detail="expected multipart/form-data")

    now = time.monotonic()
    _evict_expired_research(now)
    if len(_RESEARCH_RUNS) >= _MAX_PENDING_RESEARCH:
        raise HTTPException(status_code=429, detail="too many pending research runs; retry shortly")

    form = await request.form(
        max_files=ATTACHMENT_MAX_FILES, max_fields=12,
        max_part_size=ATTACHMENT_FILE_BYTES,
    )
    question = str(form.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="a question is required")
    if len(question) > _QUESTION_CHAR_CAP:
        raise HTTPException(status_code=400, detail="question too long")
    question_advisories = scan_advisories(question)
    question = redact_text(question, question_advisories)
    mode = str(form.get("mode", "demo"))
    if mode not in ("demo", "remote"):
        raise HTTPException(
            status_code=400,
            detail="attachments support demo or stateless remote mode; local agentic CLIs are blocked",
        )
    provider_ids: list[str] = []
    resolved_provider_profiles = []
    if mode == "remote":
        provider_ids = [p.strip() for p in str(form.get("providers", "")).split(",") if p.strip()]
        if not provider_ids or len(provider_ids) != len(set(provider_ids)):
            raise HTTPException(status_code=400, detail="select one or more unique remote providers")
        configured = {item["id"]: item["configured"] for item in remote_capabilities()}
        for provider_id in provider_ids:
            try:
                profile = get_provider_profile(provider_id)
            except KeyError:
                raise HTTPException(status_code=400, detail=f"unknown remote provider profile: {provider_id}")
            if not configured.get(provider_id):
                raise HTTPException(status_code=400, detail=f"remote provider is not configured: {provider_id}")
            try:
                ensure_attachment_eligible(profile)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            resolved_provider_profiles.append(profile)

    files = []
    remaining = ATTACHMENT_TOTAL_BYTES
    for value in form.getlist("files"):
        if not hasattr(value, "read"):                 # ignore non-file form fields
            continue
        data = await _read_capped(value, ATTACHMENT_FILE_BYTES, remaining)
        remaining -= len(data)
        files.append((value.filename, data))
    if not files:
        raise HTTPException(status_code=400, detail="upload at least one supported attachment")

    try:
        results = [ingest_attachment_bytes(name, data) for name, data in files]
        chunks = combine_chunks(results)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    secret_findings = sum(
        advisory.kind != "prompt_injection_language"
        for result in results for advisory in result.advisories
    ) + sum(a.kind != "prompt_injection_language" for a in question_advisories)
    injection_hints = sum(
        advisory.kind == "prompt_injection_language"
        for result in results for advisory in result.advisories
    ) + sum(a.kind == "prompt_injection_language" for a in question_advisories)
    # Web research always redacts locally. There is intentionally no remembered
    # or blanket browser override for likely secrets.
    from quorum.research.schema import SourceChunk
    chunks = [SourceChunk(c.id, redact_text(c.text), c.source) for c in chunks]

    provider_manifest = [
        provider_snapshot(
            resolved_provider_profiles[index],
            receives=("grounded_blind", "fact_check") if index == 0 else ("grounded_blind",),
        )
        for index, provider_id in enumerate(provider_ids)
    ]
    manifest = build_manifest(
        question, chunks, provider_manifest, files=len(results),
        secret_findings=secret_findings, injection_hints=injection_hints,
    )

    source_chars = sum(len(c.text) for c in chunks)
    run_id = secrets.token_urlsafe(18)
    with _research_claim_lock:
        _evict_expired_research(now)
        if len(_RESEARCH_RUNS) >= _MAX_PENDING_RESEARCH:
            raise HTTPException(status_code=429, detail="too many pending research runs; retry shortly")
        if _stored_source_chars() + source_chars > _GLOBAL_SOURCE_CHAR_CAP:
            raise HTTPException(status_code=429, detail="server source memory busy; retry shortly")
        _RESEARCH_RUNS[run_id] = {
            "question": question, "mode": mode, "chunks": chunks,
            "providers": provider_ids,
            "resolved_provider_profiles": resolved_provider_profiles,
            "provider_snapshots": provider_manifest,
            "manifest_hash": manifest["manifest_hash"],
            "approved": mode == "demo",
            "source_chars": source_chars, "created_at": now,
            "expires_at": now + _RESEARCH_TTL, "claimed": False,
        }
    return {
        "run_id": run_id, "chunks": len(chunks), "source_chars": source_chars,
        "files": len(results), "secret_findings_redacted": secret_findings,
        "injection_hints": injection_hints,
        "providers": provider_manifest, "manifest_hash": manifest["manifest_hash"],
        "preview": manifest_preview(manifest), "manifest": manifest,
        "requires_approval": mode == "remote",
    }


class ResearchApproval(BaseModel):
    manifest_hash: str


@app.post("/api/research-runs/{run_id}/approve")
def approve_research_run(run_id: str, req: ResearchApproval, request: Request) -> dict:
    _require_local_post(request)
    now = time.monotonic()
    with _research_claim_lock:
        _evict_expired_research(now)
        spec = _RESEARCH_RUNS.get(run_id)
        if spec is None or spec["claimed"] or spec["expires_at"] < now:
            raise HTTPException(status_code=404, detail="unknown, expired, or consumed run")
        if spec["mode"] != "remote":
            raise HTTPException(status_code=400, detail="demo research does not require approval")
        if req.manifest_hash != spec["manifest_hash"]:
            raise HTTPException(status_code=400, detail="prepared manifest changed or hash mismatched")
        if spec["approved"]:
            raise HTTPException(status_code=409, detail="run was already approved")
        spec["approved"] = True
    return {"approved": True, "manifest_hash": req.manifest_hash}


@app.get("/api/research-runs/{run_id}/events")
def research_run_events(run_id: str) -> StreamingResponse:
    now = time.monotonic()
    with _research_claim_lock:                          # atomic claim, no GET race
        _evict_expired_research(now)
        spec = _RESEARCH_RUNS.get(run_id)
        if spec is None or spec["claimed"] or spec["expires_at"] < now:
            raise HTTPException(status_code=404, detail="unknown, expired, or already-consumed run")
        if not spec.get("approved"):
            raise HTTPException(status_code=403, detail="review and approve the prepared manifest first")
        spec["claimed"] = True
    return StreamingResponse(
        research_events(spec, run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no",
                 "X-Content-Type-Options": "nosniff"},
    )


def _build_research_models(
    mode: str, provider_ids: list[str] | tuple[str, ...] = (),
    provider_snapshots: Optional[list[dict]] = None,
    resolved_profiles=None,
):
    """Build only demo stubs or stateless, tool-free fixed-host API models.

    Local CLI models remain fail-closed: scrubbed environment variables do not
    remove their filesystem/tool access and therefore are not an attachment
    sandbox.
    """
    if mode == "demo":
        members = [demo_research_member(n, i) for i, n in enumerate(["Atlas", "Beacon", "Cypher"])]
        return members, demo_research_member("Vex", 9)
    if mode == "remote" and provider_ids:
        profiles = list(resolved_profiles) if resolved_profiles is not None else [
            get_provider_profile(provider_id) for provider_id in provider_ids
        ]
        for profile in profiles:
            ensure_attachment_eligible(profile)
        current = [
            provider_snapshot(
                profile,
                receives=("grounded_blind", "fact_check") if index == 0 else ("grounded_blind",),
            )
            for index, profile in enumerate(profiles)
        ]
        if provider_snapshots is not None and current != provider_snapshots:
            raise ValueError("provider profile changed after approval")
        members = [build_remote_model_from_profile(profile) for profile in profiles]
        return members, None
    raise ValueError("local/agentic attachment research is disabled")


def research_events(spec: dict, run_id: str):
    try:
        chunks, question, mode = spec["chunks"], spec["question"], spec["mode"]
        pace = 0.25 if mode == "demo" else 0.0
        members, skeptic = _build_research_models(
            mode, spec.get("providers", ()), spec.get("provider_snapshots"),
            spec.get("resolved_provider_profiles"),
        )
        t = Transcript(question=question)

        yield _sse({"type": "start", "question": question, "mode": mode,
                    "chunks": len(chunks), "files": len({c.source for c in chunks}),
                    "providers": list(spec.get("providers", ()))})

        yield _sse({"type": "round", "round": "grounded_blind", "status": "running"})
        per_member = grounded_blind(members, question, chunks, t)
        pooled = pool_claims_bounded(per_member, members)
        for claim in pooled:
            yield _sse({"type": "claim", "claim": claim.to_dict()})
            if pace:
                time.sleep(pace)
        yield _sse({"type": "round", "round": "grounded_blind", "status": "done"})

        checker = skeptic or members[0]
        yield _sse({"type": "round", "round": "fact_check", "status": "running"})
        for claim in pooled:
            fact_check(checker, claim, chunks, t)
            yield _sse({"type": "verdict", "claim_id": claim.id,
                        "verdict": claim.verdict, "effective_verdict": claim.effective_verdict})
            if pace:
                time.sleep(pace)
        yield _sse({"type": "round", "round": "fact_check", "status": "done"})

        ledger = ClaimLedger(question, pooled).finalize()
        yield _sse({"type": "ledger", "ledger": ledger.to_dict()})
        yield _sse({"type": "done"})
    except Exception:
        yield _sse({"type": "error", "message": "research run failed safely; no provider details were exposed"})
    finally:
        _RESEARCH_RUNS.pop(run_id, None)               # free memory + reservation
