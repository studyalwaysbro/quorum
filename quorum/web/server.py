"""FastAPI server that streams a Quorum deliberation live over SSE.

The deliberation runs round by round; each model answer is pushed to the
browser the moment it lands, so you *watch* the council think — blind answers
diverge, get critiqued, get distilled into a consensus map, get attacked by the
skeptic, then synthesized. Demo mode needs no API keys; live mode drives any
CLI models detected on the machine.

Run:  python -m quorum.web      (then open http://127.0.0.1:8000)
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Iterator, Optional

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse

from quorum.adapters import CLIModel, Model
from quorum.rounds import (
    adversarial_round,
    blind_round,
    consensus_map_round,
    critique_round,
    synthesis_round,
)
from quorum.transcript import Transcript
from quorum.web.demo import (
    SKEPTIC_NAME,
    default_demo_roster,
    demo_member,
    demo_skeptic,
)

STATIC = Path(__file__).parent / "static"

# CLI models we know how to wrap if they're on PATH. argv is best-effort;
# CLIModel turns any failure into a non-fatal error string the UI just shows.
LIVE_CLIS = {
    "deepseek": ["deepseek"],
    "gemini": ["gemini", "-p"],
    "gpt-5.5": ["codex", "exec"],
    "grok": ["grok"],
}
_CLI_BINARY = {"deepseek": "deepseek", "gemini": "gemini", "gpt-5.5": "codex", "grok": "grok"}

app = FastAPI(title="Quorum", description="Watch a multi-model council deliberate")


def detected_live_models() -> list[str]:
    return [name for name, binary in _CLI_BINARY.items() if shutil.which(binary)]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/roster")
def roster() -> dict:
    demo = default_demo_roster()
    return {
        "demo": demo,
        "live": detected_live_models(),
        "skeptic_name": SKEPTIC_NAME,
    }


def _build_models(mode: str, members: list[str], skeptic_name: Optional[str]):
    """Return (members, skeptic) model objects for the chosen mode."""
    if mode == "live":
        member_models: list[Model] = [
            CLIModel(name, LIVE_CLIS.get(name, [name])) for name in members
        ]
        skeptic = CLIModel(skeptic_name, LIVE_CLIS.get(skeptic_name, [skeptic_name])) if skeptic_name else None
        return member_models, skeptic
    member_models = [demo_member(name) for name in members]
    skeptic = demo_skeptic(skeptic_name) if skeptic_name else None
    return member_models, skeptic


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def deliberation_events(
    question: str,
    mode: str,
    members: list[str],
    skeptic_name: Optional[str],
) -> Iterator[str]:
    """Drive the rounds in council order, emitting an SSE event per turn."""
    pace = 0.32 if mode == "demo" else 0.0   # demo: simulate think-time so it feels live

    member_models, skeptic = _build_models(mode, members, skeptic_name)
    t = Transcript(question=question)
    seen = 0

    def flush_new():
        nonlocal seen
        for turn in t.turns[seen:]:
            yield _sse({
                "type": "turn",
                "round": turn.round,
                "model": turn.model,
                "response": turn.response,
            })
            if pace:
                time.sleep(pace)
        seen = len(t.turns)

    # plan the stages so the UI can render the timeline up front
    stages = ["blind"]
    if len(member_models) > 1:
        stages += ["critique", "consensus_map"]
    if skeptic is not None:
        stages.append("adversarial")
    stages.append("synthesis")

    yield _sse({"type": "start", "question": question, "mode": mode, "stages": stages,
                "members": [m.name for m in member_models],
                "skeptic": skeptic.name if skeptic else None})

    try:
        # blind
        yield _sse({"type": "round", "round": "blind", "status": "running"})
        blind_round(member_models, question, t)
        yield from flush_new()
        yield _sse({"type": "round", "round": "blind", "status": "done"})

        if len(member_models) > 1:
            yield _sse({"type": "round", "round": "critique", "status": "running"})
            critique_round(member_models, question, t)
            yield from flush_new()
            yield _sse({"type": "round", "round": "critique", "status": "done"})

            yield _sse({"type": "round", "round": "consensus_map", "status": "running"})
            consensus_map_round(question, t)   # deterministic, no model
            yield from flush_new()
            yield _sse({"type": "round", "round": "consensus_map", "status": "done"})

        if skeptic is not None:
            yield _sse({"type": "round", "round": "adversarial", "status": "running"})
            adversarial_round(skeptic, question, t)
            yield from flush_new()
            yield _sse({"type": "round", "round": "adversarial", "status": "done"})

        yield _sse({"type": "round", "round": "synthesis", "status": "running"})
        synthesizer = member_models[0]
        answer = synthesis_round(synthesizer, question, t)
        yield from flush_new()
        yield _sse({"type": "round", "round": "synthesis", "status": "done"})

        yield _sse({"type": "answer", "answer": answer, "by": synthesizer.name})
        yield _sse({"type": "transcript", "transcript": t.to_dict()})
        yield _sse({"type": "done"})
    except Exception as exc:  # never 500 mid-stream; surface it in the UI
        yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


@app.get("/api/stream")
def stream(
    question: str = Query(..., min_length=1),
    mode: str = Query("demo"),
    members: str = Query(""),
    skeptic: str = Query(""),
) -> StreamingResponse:
    member_list = [m for m in (members.split(",") if members else []) if m.strip()]
    if not member_list:
        member_list = default_demo_roster()["members"]
    skeptic_name = skeptic.strip() or None

    gen = deliberation_events(question, mode, member_list, skeptic_name)
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
