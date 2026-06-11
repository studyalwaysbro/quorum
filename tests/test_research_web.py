"""Research upload endpoint — offline, demo mode. Skips without fastapi/multipart."""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from quorum.web.server import app  # noqa: E402

client = TestClient(app)
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
