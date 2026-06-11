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


def test_council_research_method_runs_end_to_end():
    from quorum import Council
    chunks = chunk_text(SOURCE)
    council = Council([_model("m1")], skeptic=_model("checker"))
    verdict = council.research("cats?", chunks)
    assert verdict.ledger.kept
    assert verdict.ledger.dropped
