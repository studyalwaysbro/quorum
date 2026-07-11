"""Research pipeline — grounded claims, fact-check, Claim Ledger. Offline."""

from quorum.adapters import CallableModel
from quorum.research import chunk_text, grounded_blind, run_research
from quorum.research.rounds import _parse_verdict
from quorum.transcript import Transcript

# one valid-cited claim + one with a quote that ISN'T in the source
GROUNDED = (
    '{"claims":['
    '{"text":"Cats sleep a lot.","citations":[{"chunk_id":"C1",'
    '"quote":"Cats sleep 16 hours a day"}],"confidence":5},'
    '{"text":"Cats photosynthesize.","citations":[{"chunk_id":"C2",'
    '"quote":"cats use sunlight"}],"confidence":2}'
    ']}'
)
SOURCE = "Cats sleep 16 hours a day.\n\nThey are obligate carnivores."


def _model(name, grounded=GROUNDED, verdict="Supported"):
    def fn(prompt):
        return f"VERDICT: {verdict}" if "VERDICT:" in prompt else grounded
    return CallableModel(name, fn)


def test_uncited_claim_is_dropped_even_if_checker_says_supported():
    chunks = chunk_text(SOURCE)
    v = run_research([_model("m1")], "What about cats?", chunks, skeptic=_model("checker"))
    led = v.ledger
    assert len(led.claims) == 2
    assert len(led.kept) == 1 and "sleep" in led.kept[0].text.lower()
    dropped = led.dropped
    assert len(dropped) == 1 and "photosynth" in dropped[0].text.lower()
    # the deterministic guarantee: invalid citation -> can't be Supported
    assert dropped[0].has_valid_citation is False
    assert dropped[0].verdict == "Supported"          # the model claimed support
    assert dropped[0].effective_verdict == "Unsupported"   # ...but it's refused
    rounds = {turn.round for turn in v.transcript.turns}
    assert {"grounded_blind", "fact_check"} <= rounds


def test_grounded_blind_validates_and_attributes():
    chunks = chunk_text("Alpha fact here.\n\nBeta fact here.")
    t = Transcript("q")
    m = _model("m1", grounded='{"claims":[{"text":"a","citations":[{"chunk_id":"C1","quote":"Alpha fact"}]}]}')
    res = grounded_blind([m], "q", chunks, t)
    claim = res["m1"][0]
    assert claim.citations[0].valid is True
    assert claim.asserted_by == ["m1"]


def test_grounded_blind_gives_up_gracefully_on_garbage():
    chunks = chunk_text("Some source.")
    t = Transcript("q")
    res = grounded_blind([CallableModel("bad", lambda p: "not json at all")], "q", chunks, t)
    assert res["bad"] == []                            # no usable claims, no crash


def test_unparsed_verdict_fails_closed():
    assert _parse_verdict("hmm, no verdict here") is None
    assert _parse_verdict("VERDICT: Contradicted") == "Contradicted"
    assert _parse_verdict("VERDICT: supported") == "Supported"


def test_ledger_to_dict_exposes_refused_and_effective_verdict():
    chunks = chunk_text(SOURCE)
    v = run_research([_model("m1")], "q", chunks, skeptic=_model("c"))
    d = v.ledger.to_dict()
    assert d["counts"] == {"kept": 1, "qualified": 0, "dropped": 1, "total": 2}
    assert len(d["refused_to_conclude"]) == 1
    assert all("effective_verdict" in c for c in d["claims"])


def test_partially_supported_without_citation_is_also_dropped():
    # Codex finding #1: PartiallySupported must NOT escape the guarantee
    chunks = chunk_text(SOURCE)
    v = run_research([_model("m1")], "q", chunks, skeptic=_model("c", verdict="PartiallySupported"))
    led = v.ledger
    # the fabricated-quote claim: checker said PartiallySupported, but no valid cite
    bad = next(c for c in led.claims if "photosynth" in c.text.lower())
    assert bad.has_valid_citation is False
    assert bad.verdict == "PartiallySupported"
    assert bad.effective_verdict == "Unsupported"
    assert bad in led.dropped and bad not in led.qualified


def test_verdict_parser_rejects_spoofed_and_nonfinal():
    # Codex finding #2
    assert _parse_verdict("VERDICT: SupportedButActuallyUnsupported") is None
    assert _parse_verdict("explain\nVERDICT: Supported\nnot final") is None
    assert _parse_verdict("VERDICT: Supported") == "Supported"
    assert _parse_verdict("reasoning...\nVERDICT: Contradicted") == "Contradicted"
    assert _parse_verdict("VERDICT: Supported.") == "Supported"   # trailing period ok


def test_ledger_ids_match_transcript_metadata():
    # Codex finding #3: pooled ids must equal grounded-meta ids
    chunks = chunk_text(SOURCE)
    a = _model("A", grounded='{"claims":[{"text":"a","citations":[{"chunk_id":"C1","quote":"Cats sleep 16 hours a day"}]}]}')
    b = _model("B", grounded='{"claims":[{"text":"b","citations":[{"chunk_id":"C2","quote":"obligate carnivores"}]}]}')
    v = run_research([a, b], "q", chunks, skeptic=_model("c"))
    ledger_ids = {c.id for c in v.ledger.claims}
    meta_ids = {
        claim["id"]
        for turn in v.transcript.turns if turn.round == "grounded_blind"
        for claim in turn.meta["claims"]
    }
    assert ledger_ids == meta_ids == {"K1", "K2"}


def test_demo_research_models_yield_kept_and_refused():
    from quorum.web.demo_research import demo_research_member
    source = ("Photosynthesis converts light into chemical energy.\n\n"
              "Mitochondria are the powerhouse of the cell.")
    chunks = chunk_text(source)
    members = [demo_research_member("Atlas", 0), demo_research_member("Beacon", 1)]
    v = run_research(members, "summarize", chunks, skeptic=demo_research_member("Vex"))
    led = v.ledger
    assert led.kept and led.dropped
    assert all(c.has_valid_citation for c in led.kept)
    assert all(not c.has_valid_citation for c in led.dropped)


def test_council_research_method_runs_end_to_end():
    from quorum import Council
    chunks = chunk_text(SOURCE)
    council = Council([_model("m1")], skeptic=_model("checker"))
    verdict = council.research("cats?", chunks)
    assert verdict.ledger.kept
    assert verdict.ledger.dropped


def test_fact_check_call_budget_is_bounded_and_fair_across_members():
    import json
    from quorum.research.pipeline import MAX_POOLED_CLAIMS

    claims = [
        {"text": f"claim {i}", "citations": [{"chunk_id": "C1", "quote": "source fact"}]}
        for i in range(40)
    ]
    grounded = json.dumps({"claims": claims})
    calls = []
    checker = CallableModel("checker", lambda prompt: calls.append(prompt) or "VERDICT: Supported")
    verdict = run_research(
        [_model("A", grounded=grounded), _model("B", grounded=grounded)],
        "q", chunk_text("source fact"), skeptic=checker,
    )
    assert len(verdict.ledger.claims) == MAX_POOLED_CLAIMS == 24
    assert len(calls) == MAX_POOLED_CLAIMS
    assert {claim.asserted_by[0] for claim in verdict.ledger.claims} == {"A", "B"}
