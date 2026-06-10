"""Web UI tests — offline, demo mode, no API keys.

Skips cleanly if FastAPI isn't installed (it's an optional [web] extra).
"""

import json
import os

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from quorum.web.server import app  # noqa: E402

client = TestClient(app)
CSRF = {"X-Quorum-CSRF": "1"}


def _run(question="Test question?", mode="demo", members=("Atlas", "Beacon"), skeptic="Vex"):
    body = {"question": question, "mode": mode, "members": list(members), "skeptic": skeptic}
    r = client.post("/api/runs", json=body, headers=CSRF)
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def _events(**kw):
    run_id = _run(**kw)
    events = []
    with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200 and "Quorum" in r.text


def test_capabilities_lists_models_no_keys():
    r = client.get("/api/capabilities").json()
    assert "Atlas" in r["demo"]["all_members"]
    ids = {m["id"] for m in r["local"]}
    assert {"claude", "gemini", "grok"} <= ids
    # never leak a key-shaped field
    assert "key" not in json.dumps(r).lower().replace("skeptic", "")


def test_full_protocol_and_synthesis():
    ev = _events()
    types = [e["type"] for e in ev]
    assert types[0] == "start" and types[-1] == "done"
    done = {e["round"] for e in ev if e["type"] == "round" and e["status"] == "done"}
    assert {"blind", "critique", "consensus_map", "adversarial", "synthesis"} <= done
    answer = next(e for e in ev if e["type"] == "answer")
    assert answer["answer"].strip()
    assert next(e for e in ev if e["type"] == "transcript")["transcript"]["turns"]


def test_consensus_map_is_clean_not_exploded():
    ev = _events()
    cmap = next(e for e in ev if e["type"] == "turn" and e["round"] == "consensus_map")
    # the demo distiller returns a tidy map, not dozens of 'raised by' fragments
    assert cmap["response"].count("raised by") < 3
    assert "CONSENSUS / ISSUE MAP" in cmap["response"]


def test_adversary_after_consensus_map():
    ev = _events()
    seq = [e["round"] for e in ev if e["type"] == "turn"]
    assert seq.index("consensus_map") < seq.index("adversarial")


def test_single_member_skips_critique():
    ev = _events(members=("Atlas",), skeptic="")
    rounds = {e["round"] for e in ev if e["type"] == "round"}
    assert "critique" not in rounds and "consensus_map" not in rounds
    assert any(e["type"] == "answer" for e in ev)


# ---- security of the endpoints ----------------------------------------
def test_post_requires_csrf_header():
    r = client.post("/api/runs", json={"question": "x", "mode": "demo"})
    assert r.status_code == 403


def test_post_rejects_cross_origin():
    r = client.post("/api/runs", json={"question": "x", "mode": "demo"},
                    headers={**CSRF, "Origin": "http://evil.example"})
    assert r.status_code == 403


def test_unknown_local_model_rejected_at_post():
    r = client.post("/api/runs", json={"question": "x", "mode": "local", "members": ["rm"]},
                    headers=CSRF)
    assert r.status_code == 400


def test_run_id_is_one_shot():
    run_id = _run()
    with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
        list(resp.iter_lines())
    second = client.get(f"/api/runs/{run_id}/events")
    assert second.status_code == 404


def test_no_key_value_leaks_into_a_demo_run():
    os.environ["ANTHROPIC_API_KEY"] = "sk-LEAKCANARY-123"
    try:
        ev = _events()
        blob = json.dumps(ev)
        assert "sk-LEAKCANARY-123" not in blob
        assert "ANTHROPIC_API_KEY" not in blob
    finally:
        del os.environ["ANTHROPIC_API_KEY"]
