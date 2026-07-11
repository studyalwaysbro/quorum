"""Research upload endpoint — offline, demo mode. Skips without fastapi/multipart."""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from quorum.web.server import app  # noqa: E402

client = TestClient(app, base_url="http://127.0.0.1:8000")
CSRF = {"X-Quorum-CSRF": "1"}
SOURCE = (b"Photosynthesis converts light into chemical energy in plants.\n\n"
          b"Mitochondria are the powerhouse of the cell.")


def _txt(name="bio.txt", data=SOURCE):
    return {"files": (name, data, "text/plain")}


def _events(run_id):
    ev = []
    with client.stream("GET", f"/api/research-runs/{run_id}/events") as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: "):
                ev.append(json.loads(line[len("data: "):]))
    return ev


def test_index_serves_research_ui():
    html = client.get("/").text
    assert 'id="research-view"' in html and 'id="dropzone"' in html
    assert "/api/research-runs" in html and "renderLedger" in html
    # the ledger UI renders the ENFORCED verdict, not the raw one (carry-forward)
    assert "effective_verdict" in html
    assert "refused_to_conclude" in html or "refused to conclude" in html.lower()


def test_upload_happy_path_yields_ledger():
    r = client.post("/api/research-runs", headers=CSRF,
                    data={"question": "summarize the biology", "mode": "demo"}, files=_txt())
    assert r.status_code == 200, r.text
    ev = _events(r.json()["run_id"])
    assert ev[0]["type"] == "start" and ev[-1]["type"] == "done"
    ledger = next(e for e in ev if e["type"] == "ledger")["ledger"]
    assert ledger["counts"]["kept"] >= 1
    assert ledger["counts"]["dropped"] >= 1                 # fabricated claim refused
    assert len(ledger["refused_to_conclude"]) >= 1
    # every kept claim carries a validated citation
    kept = [c for c in ledger["claims"] if c["effective_verdict"] == "Supported"]
    assert kept and all(c["has_valid_citation"] for c in kept)


def test_upload_requires_csrf():
    r = client.post("/api/research-runs", data={"question": "q"}, files=_txt())
    assert r.status_code == 403


def test_upload_rejects_binary_magic():
    r = client.post("/api/research-runs", headers=CSRF,
                    data={"question": "q", "mode": "demo"},
                    files={"files": ("doc.txt", b"PK\x03\x04 totally a zip", "text/plain")})
    assert r.status_code == 400


def test_upload_rejects_bad_extension():
    r = client.post("/api/research-runs", headers=CSRF,
                    data={"question": "q", "mode": "demo"},
                    files={"files": ("payload.exe", b"hello there", "application/octet-stream")})
    assert r.status_code == 400


def test_upload_rejects_html_document():
    r = client.post("/api/research-runs", headers=CSRF,
                    data={"question": "q", "mode": "demo"},
                    files={"files": ("p.txt", b"<!DOCTYPE html><script>alert(1)</script>", "text/plain")})
    assert r.status_code == 400


def test_upload_requires_multipart_and_question():
    # no file part -> urlencoded body -> rejected as non-multipart (415)
    assert client.post("/api/research-runs", headers=CSRF,
                       data={"question": "q", "mode": "demo"}).status_code in (400, 415)
    # multipart with a file but empty question -> 400
    assert client.post("/api/research-runs", headers=CSRF,
                       data={"question": "", "mode": "demo"}, files=_txt()).status_code == 400


def test_research_run_is_one_shot():
    rid = client.post("/api/research-runs", headers=CSRF,
                      data={"question": "q", "mode": "demo"}, files=_txt()).json()["run_id"]
    _events(rid)
    assert client.get(f"/api/research-runs/{rid}/events").status_code == 404


def test_research_is_demo_only_local_rejected():
    # Codex finding #1: fail closed — no local model from an untrusted upload
    r = client.post("/api/research-runs", headers=CSRF,
                    data={"question": "q", "mode": "local"}, files=_txt())
    assert r.status_code == 400
    from quorum.web.server import _build_research_models
    with pytest.raises(ValueError):
        _build_research_models("local")


def test_expired_research_run_is_not_consumable():
    # Codex finding #2: TTL enforced on GET, not only on POST eviction
    rid = client.post("/api/research-runs", headers=CSRF,
                      data={"question": "q", "mode": "demo"}, files=_txt()).json()["run_id"]
    from quorum.web.server import _RESEARCH_RUNS
    _RESEARCH_RUNS[rid]["expires_at"] = 0
    assert client.get(f"/api/research-runs/{rid}/events").status_code == 404


def test_oversize_file_rejected():
    # Per-file cap (bounded read or parser) rejects input above 5 MiB.
    big = b"a" * (6 * 1024 * 1024)
    r = client.post("/api/research-runs", headers=CSRF,
                    data={"question": "q", "mode": "demo"},
                    files={"files": ("big.txt", big, "text/plain")})
    assert r.status_code in (400, 413)


def test_sniffing_catches_bom_comment_html_and_rtf():
    # Codex finding #4
    for payload in (
        b"\xef\xbb\xbf<!doctype html><b>x</b>",          # BOM-prefixed HTML
        b"<!-- hi -->\n<html><script>x</script></html>", # comment-prefixed HTML
        b"{\\rtf1 some rtf content}",                     # RTF
        b"plain\x7f\x80\x81\x82 lots of controls",        # DEL + C1 controls
    ):
        r = client.post("/api/research-runs", headers=CSRF,
                        data={"question": "q", "mode": "demo"},
                        files={"files": ("a.txt", payload, "text/plain")})
        assert r.status_code == 400, payload[:20]


def test_research_bans_agentic_local_models():
    from quorum.providers import LOCAL_CATALOG
    from quorum.web.server import _build_research_models
    agentic_ids = {s.id for s in LOCAL_CATALOG if s.agentic}
    try:
        members, skeptic = _build_research_models("local")
    except ValueError:
        return                                             # no non-agentic available -> refuses
    used = {m.name for m in members} | ({skeptic.name} if skeptic else set())
    assert not (used & agentic_ids), "agentic models must never be used on the research endpoint"


def test_research_rejects_loopback_ollama_before_storing_source():
    from quorum.web import server
    before = set(server._RESEARCH_RUNS)
    response = client.post(
        "/api/research-runs", headers=CSRF,
        data={"question": "q", "mode": "remote", "providers": "ollama"}, files=_txt(),
    )
    assert response.status_code == 400
    assert "not eligible for attachment research" in response.text
    assert set(server._RESEARCH_RUNS) == before


def test_remote_research_requires_reviewed_hash_approval(monkeypatch):
    import quorum.web.server as server

    monkeypatch.setattr(server, "remote_capabilities", lambda: [
        {"id": "deepseek", "label": "DeepSeek", "default_model": "deepseek-v4-pro", "configured": True}
    ])
    prepared = client.post(
        "/api/research-runs", headers=CSRF,
        data={"question": "q", "mode": "remote", "providers": "deepseek"}, files=_txt(),
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["requires_approval"] is True and body["preview"]
    assert body["manifest"]["manifest_hash"] == body["manifest_hash"]
    assert body["manifest"]["chunks"]
    assert client.get(f"/api/research-runs/{body['run_id']}/events").status_code == 403
    wrong = client.post(
        f"/api/research-runs/{body['run_id']}/approve", headers=CSRF,
        json={"manifest_hash": "0" * 64},
    )
    assert wrong.status_code == 400

    monkeypatch.setattr(
        server, "build_remote_model_from_profile",
        lambda profile, **kwargs: server.demo_research_member(profile.id, 0),
    )
    approved = client.post(
        f"/api/research-runs/{body['run_id']}/approve", headers=CSRF,
        json={"manifest_hash": body["manifest_hash"]},
    )
    assert approved.status_code == 200, approved.text
    events = _events(body["run_id"])
    assert events[0]["mode"] == "remote"
    assert events[0]["providers"] == ["deepseek"]
    assert any(event["type"] == "ledger" for event in events)


def test_remote_upload_redacts_likely_secret_before_storage(monkeypatch):
    import quorum.web.server as server

    monkeypatch.setattr(server, "remote_capabilities", lambda: [
        {"id": "openai", "label": "OpenAI", "default_model": "gpt-5.6-sol", "configured": True}
    ])
    credential_name = "_".join(("OPENAI", "API", "KEY"))
    fake_secret = "sk" + chr(45) + "abcdefghijklmnopqr"
    response = client.post(
        "/api/research-runs", headers=CSRF,
        data={"question": "q", "mode": "remote", "providers": "openai",
              },
        files=_txt(data=f"{credential_name}={fake_secret}".encode()),
    )
    assert response.status_code == 200, response.text
    assert response.json()["secret_findings_redacted"] >= 1
    chunks = server._RESEARCH_RUNS[response.json()["run_id"]]["chunks"]
    packet = "\n".join(chunk.text for chunk in chunks)
    assert fake_secret not in packet
    assert "[REDACTED:" in packet


def test_gui_remote_fact_check_budget_matches_pipeline(monkeypatch):
    import quorum.web.server as server
    from quorum.adapters import CallableModel
    from quorum.research.pipeline import MAX_POOLED_CLAIMS
    from quorum.research.schema import Citation, Claim, SourceChunk

    members = [CallableModel(name, lambda prompt: "unused") for name in ("A", "B", "C")]
    per_member = {
        member.name: [
            Claim(f"{member.name}{i}", f"claim {i}", [Citation("C1", "source")], asserted_by=[member.name])
            for i in range(40)
        ]
        for member in members
    }
    checks = []
    monkeypatch.setattr(server, "_build_research_models", lambda *args, **kwargs: (members, None))
    monkeypatch.setattr(server, "grounded_blind", lambda *args, **kwargs: per_member)

    def checked(model, claim, chunks, transcript):
        checks.append(claim.id)
        claim.verdict = "Supported"
        claim.citations[0].valid = True

    monkeypatch.setattr(server, "fact_check", checked)
    events = list(server.research_events({
        "question": "q", "mode": "remote", "providers": ["openai"],
        "chunks": [SourceChunk("C1", "source", "x.txt")],
    }, "not-stored"))
    assert len(checks) == MAX_POOLED_CLAIMS == 24
    verdict_events = [line for line in events if '"type": "verdict"' in line]
    assert len(verdict_events) == MAX_POOLED_CLAIMS


def test_gui_rejects_profile_drift_before_model_construction(tmp_path, monkeypatch):
    import quorum.web.server as server
    from quorum.providers.profiles import make_user_profile, write_user_profiles
    from quorum.providers.remote import get_provider_profile, provider_snapshot

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = tmp_path / "quorum" / "providers.json"
    write_user_profiles([
        make_user_profile("glm-gui", "zai", "glm-5.1")
    ], config)
    prepared = [provider_snapshot(get_provider_profile("glm-gui"))]
    write_user_profiles([
        make_user_profile("glm-gui", "zai", "glm-5.1-new")
    ], config)
    called = []
    monkeypatch.setattr(server, "build_remote_model_from_profile", lambda *args, **kwargs: called.append(args))
    with pytest.raises(ValueError, match="changed after approval"):
        server._build_research_models("remote", ["glm-gui"], prepared)
    assert called == []
