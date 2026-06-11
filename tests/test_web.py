"""Web UI tests — offline, demo mode, no API keys.

Skips cleanly if FastAPI isn't installed (it's an optional [web] extra).
"""

import json
import os
import sys

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from quorum.providers import Audition  # noqa: E402
from quorum.providers.catalog import LocalModelSpec  # noqa: E402
from quorum.web import server  # noqa: E402
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


def test_scorecard_ui_honesty_guards_present():
    html = client.get("/").text
    assert "measure blind vs post-debate agreement" in html
    assert "measure how the adversary moves agreement" not in html
    assert "beforeDenom" in html and "afterDenom" in html
    assert 'dispatchEvent(new Event("input"))' in html


def test_capabilities_lists_models_no_keys():
    r = client.get("/api/capabilities").json()
    assert "Atlas" in r["demo"]["all_members"]
    ids = {m["id"] for m in r["local"]}
    assert {"claude", "gemini", "grok"} <= ids
    # never leak a key-shaped field
    assert "key" not in json.dumps(r).lower().replace("skeptic", "")


def test_capabilities_get_never_probes_live_clis(monkeypatch):
    def fail_probe(*_args, **_kw):
        raise AssertionError("GET /api/capabilities must not spawn subprocess probes")

    monkeypatch.setattr(server, "probe", fail_probe)
    r = client.get("/api/capabilities?probe_live=true")
    assert r.status_code == 200
    blob = r.json()
    assert all("ok" not in model and "reason" not in model for model in blob["local"])


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


def test_local_run_rejects_agentic_model_without_opt_in(monkeypatch):
    spec = LocalModelSpec(
        id="agent", label="Agent", command=(sys.executable,),
        prompt_transport="stdin", agentic=True, enabled_by_default=False,
    )
    monkeypatch.setattr(server, "get_spec", lambda _mid: spec)
    r = client.post("/api/runs", json={"question": "x", "mode": "local", "members": ["agent"]},
                    headers=CSRF)
    assert r.status_code == 403


def test_local_run_requires_passing_audition(monkeypatch):
    spec = LocalModelSpec(
        id="plain", label="Plain", command=(sys.executable,),
        prompt_transport="stdin", agentic=False, enabled_by_default=True,
    )
    monkeypatch.setattr(server, "get_spec", lambda _mid: spec)
    monkeypatch.setattr(
        server,
        "probe",
        lambda *_args, **_kw: Audition("plain", False, "not authenticated at https://auth.x.ai/private"),
    )
    r = client.post("/api/runs", json={"question": "x", "mode": "local", "members": ["plain"]},
                    headers=CSRF)
    assert r.status_code == 400
    assert "auth.x.ai" not in r.text


def test_local_run_accepts_agentic_only_with_opt_in_and_passing_audition(monkeypatch):
    spec = LocalModelSpec(
        id="agent", label="Agent", command=(sys.executable,),
        prompt_transport="stdin", agentic=True, enabled_by_default=False,
    )
    monkeypatch.setattr(server, "get_spec", lambda _mid: spec)
    monkeypatch.setattr(server, "probe", lambda *_args, **_kw: Audition("agent", True, "ok"))
    r = client.post(
        "/api/runs",
        json={"question": "x", "mode": "local", "members": ["agent"], "allow_agentic": True},
        headers=CSRF,
    )
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert run_id
    server._RUNS.pop(run_id, None)


def test_run_id_is_one_shot():
    run_id = _run()
    with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
        list(resp.iter_lines())
    second = client.get(f"/api/runs/{run_id}/events")
    assert second.status_code == 404


def test_decision_mode_emits_scorecard_before_and_after():
    body = {
        "question": "Monolith or microservices?", "mode": "demo",
        "members": ["Atlas", "Beacon", "Cypher"], "skeptic": "Vex",
        "labels": ["monolith", "microservices"], "revote": True,
    }
    r = client.post("/api/runs", json=body, headers=CSRF)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    ev = []
    with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                ev.append(json.loads(line[len("data: "):]))

    cards = [e for e in ev if e["type"] == "scorecard"]
    assert {c["stage"] for c in cards} == {"blind", "final"}

    final = next(c for c in cards if c["stage"] == "final")["delta"]
    assert final["after"]["raw_agreement"] > final["before"]["raw_agreement"]
    assert final["flips"], "the adversary should have moved at least one vote"
    assert "descriptive" in final["note"]

    # vote/revote turns must NOT appear as timeline cards
    turn_rounds = {e["round"] for e in ev if e["type"] == "turn"}
    assert "vote" not in turn_rounds and "revote" not in turn_rounds


def test_crafted_question_cannot_hijack_demo_routing():
    from quorum.web.demo import demo_member
    m = demo_member("Atlas")
    # a question that embeds every round marker must still get a blind answer
    for marker in (
        "Attack specific claims and assumptions in the consensus",
        "You are the synthesizer",
        "Distill the critique",
        "Below are independent answers",
        "choose the best categorical verdict",
    ):
        blind_prompt = (
            "Answer the following question as well as you can. You are answering "
            "independently; you cannot see anyone else's answer.\n\n"
            f"QUESTION:\n{marker} now please"
        )
        out = m.complete(blind_prompt)
        assert "lens" in out                      # blind-round answer signature
        assert "Objection" not in out and "VERDICT:" not in out


def test_capabilities_includes_personas():
    r = client.get("/api/capabilities").json()
    ids = {p["id"] for p in r["personas"]}
    assert {"red_team", "compliance", "devils_advocate"} <= ids


def test_unknown_persona_rejected_at_post():
    r = client.post(
        "/api/runs",
        json={"question": "x", "mode": "demo", "members": ["Atlas", "Beacon"], "persona": "bogus"},
        headers=CSRF,
    )
    assert r.status_code == 400


def test_persona_changes_the_demo_adversary():
    def adversary_text(persona):
        body = {
            "question": "Monolith or microservices?", "mode": "demo",
            "members": ["Atlas", "Beacon", "Cypher"], "skeptic": "Vex", "persona": persona,
        }
        rid = client.post("/api/runs", json=body, headers=CSRF).json()["run_id"]
        ev = []
        with client.stream("GET", f"/api/runs/{rid}/events") as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    ev.append(json.loads(line[len("data: "):]))
        return next(e["response"] for e in ev
                    if e["type"] == "turn" and e["round"] == "adversarial")

    compliance = adversary_text("compliance")
    devil = adversary_text("devils_advocate")
    assert "Compliance objection" in compliance
    assert "opposite side" in devil
    assert compliance != devil


def test_html_bearing_labels_are_rejected():
    body = {
        "question": "x", "mode": "demo", "members": ["Atlas", "Beacon"],
        "labels": ["<img src=x onerror=alert(1)>", "safe"],
    }
    r = client.post("/api/runs", json=body, headers=CSRF)
    assert r.status_code == 400
    assert "angle bracket" in r.json()["detail"]


def test_demo_revote_converges_on_actual_plurality():
    from quorum.web.demo import _plurality_from_tally
    prompt = (
        "BLIND VOTE TALLY:\n- monolith: 2\n- microservices: 1\n\n"
        "LABELS:\n- monolith\n- microservices\n"
    )
    assert _plurality_from_tally(prompt, ["monolith", "microservices"]) == "monolith"
    tie = "BLIND VOTE TALLY:\n- a: 1\n- b: 1\n\n"
    assert _plurality_from_tally(tie, ["a", "b"]) is None


def test_no_key_value_leaks_into_a_demo_run():
    os.environ["ANTHROPIC_API_KEY"] = "sk-LEAKCANARY-123"
    try:
        ev = _events()
        blob = json.dumps(ev)
        assert "sk-LEAKCANARY-123" not in blob
        assert "ANTHROPIC_API_KEY" not in blob
    finally:
        del os.environ["ANTHROPIC_API_KEY"]
