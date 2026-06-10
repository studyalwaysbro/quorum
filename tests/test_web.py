"""Web UI tests — offline, demo mode, no API keys.

Skips cleanly if FastAPI isn't installed (it's an optional [web] extra).
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from quorum.web.server import app  # noqa: E402

client = TestClient(app)


def _events(question="Test question?", mode="demo", members="Atlas,Beacon", skeptic="Vex"):
    """Collect parsed SSE events from a streamed deliberation."""
    import json
    with client.stream("GET", "/api/stream", params={
        "question": question, "mode": mode, "members": members, "skeptic": skeptic,
    }) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        events = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
        return events


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "Quorum" in r.text


def test_roster_lists_demo_members():
    r = client.get("/api/roster").json()
    assert "Atlas" in r["demo"]["all_members"]
    assert r["skeptic_name"] == "Vex"


def test_stream_runs_full_protocol_and_synthesizes():
    ev = _events()
    types = [e["type"] for e in ev]
    assert types[0] == "start"
    assert types[-1] == "done"

    rounds_done = {e["round"] for e in ev if e["type"] == "round" and e["status"] == "done"}
    assert {"blind", "critique", "consensus_map", "adversarial", "synthesis"} <= rounds_done

    # an answer was produced and a replayable transcript shipped
    answer = next(e for e in ev if e["type"] == "answer")
    assert answer["answer"].strip()
    transcript = next(e for e in ev if e["type"] == "transcript")
    assert transcript["transcript"]["turns"]


def test_adversary_turn_attacks_the_consensus_map():
    ev = _events()
    turns = [e for e in ev if e["type"] == "turn"]
    # the consensus map must be emitted before the adversarial turn
    rounds_seq = [t["round"] for t in turns]
    assert rounds_seq.index("consensus_map") < rounds_seq.index("adversarial")


def test_single_member_skips_critique_and_consensus():
    ev = _events(members="Atlas", skeptic="")
    rounds = {e["round"] for e in ev if e["type"] == "round"}
    assert "critique" not in rounds
    assert "consensus_map" not in rounds
    assert "adversarial" not in rounds
    assert any(e["type"] == "answer" for e in ev)
