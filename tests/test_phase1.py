import pytest

from quorum import Council
from quorum.agreement import (
    bootstrap_ci,
    cohen_kappa,
    fleiss_kappa,
    gwet_ac1,
    krippendorff_alpha,
    raw_agreement,
    summary,
)
from quorum.records import RecordStore, VoteRecord
from quorum.votes import parse_vote


class ScriptedModel:
    def __init__(
        self,
        name,
        *,
        blind="blind answer",
        vote="VERDICT: yes\nCONFIDENCE: 4",
        retry_vote="VERDICT: yes\nCONFIDENCE: 4",
        revote="VERDICT: yes\nCONFIDENCE: 4",
        critique="shared issue.",
        adversarial="no objection",
        synthesis="final answer",
    ):
        self.name = name
        self.blind = blind
        self.vote = vote
        self.retry_vote = retry_vote
        self.revote = revote
        self.critique = critique
        self.adversarial = adversarial
        self.synthesis = synthesis
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        if "Your previous vote response could not be parsed" in prompt:
            return self.retry_vote
        if "You may revise your categorical vote once" in prompt:
            return self.revote
        if "Given your answer above" in prompt:
            return self.vote
        if "Below are independent answers" in prompt:
            return self.critique
        if "You are the adversary" in prompt:
            return self.adversarial
        if "You are the synthesizer" in prompt:
            return self.synthesis
        return self.blind


def test_vote_parsing_clean_sloppy_retry_and_abstention():
    assert parse_vote("notes\nverdict: YES\nconfidence: 4", ["yes", "no"]) == (
        "yes",
        4,
    )

    retrying = ScriptedModel(
        "retrying",
        vote="not parseable",
        retry_vote="VERDICT: NO\nCONFIDENCE: 2",
    )
    verdict = Council([retrying]).ask("q?", labels=["yes", "no"])
    vote_turn = verdict.transcript.by_round("vote")[0]
    assert verdict.votes == {"retrying": "no"}
    assert verdict.confidences == {"retrying": 2}
    assert vote_turn.meta["retry_used"] is True
    assert vote_turn.meta["initial_response"] == "not parseable"

    abstaining = ScriptedModel(
        "abstaining",
        vote="not parseable",
        retry_vote="still not parseable",
    )
    abstained = Council([abstaining]).ask("q?", labels=["yes", "no"])
    assert abstained.votes == {"abstaining": None}
    assert abstained.confidences == {"abstaining": None}
    assert "parse_error" in abstained.transcript.by_round("vote")[0].meta


def test_majority_tally_tie_and_revote_flips():
    yes = ScriptedModel(
        "yes_member",
        vote="VERDICT: yes\nCONFIDENCE: 5",
        revote="VERDICT: no\nCONFIDENCE: 3",
    )
    no = ScriptedModel(
        "no_member",
        vote="VERDICT: no\nCONFIDENCE: 4",
        revote="VERDICT: no\nCONFIDENCE: 4",
    )

    verdict = Council([yes, no], synthesizer=yes).ask(
        "q?", labels=["yes", "no"], revote=True
    )

    assert verdict.tally == {"yes": 1, "no": 1}
    assert verdict.majority is None
    assert verdict.revotes == {"yes_member": "no", "no_member": "no"}
    assert verdict.flips == {"yes_member": ("yes", "no")}
    assert [turn.round for turn in verdict.transcript.by_round("revote")] == [
        "revote",
        "revote",
    ]


def test_shuffle_is_deterministic_and_feed_forward_is_deattributed():
    def make_council():
        members = [
            ScriptedModel("alice", blind="first answer"),
            ScriptedModel("bob", blind="second answer"),
            ScriptedModel("carol", blind="third answer"),
        ]
        skeptic = ScriptedModel("skeptic")
        return Council(members, skeptic=skeptic, synthesizer=members[0], seed=77)

    first = make_council().ask("Choose?")
    second = make_council().ask("Choose?")

    first_perms = [turn.meta["answer_permutation"] for turn in first.transcript.by_round("critique")]
    second_perms = [turn.meta["answer_permutation"] for turn in second.transcript.by_round("critique")]
    assert first_perms == second_perms
    assert all(sorted(perm) == [1, 2, 3] for perm in first_perms)
    assert sorted(first.transcript.by_round("adversarial")[0].meta["answer_permutation"]) == [
        1,
        2,
        3,
    ]

    consensus = first.transcript.by_round("consensus_map")[0]
    adversarial_prompt = first.transcript.by_round("adversarial")[0].prompt
    assert "raised by 3 of 3 critics" in consensus.response
    assert consensus.meta["agreements"][0]["models"] == ["alice", "bob", "carol"]
    for name in ["alice", "bob", "carol"]:
        assert name not in consensus.response
        assert name not in adversarial_prompt


def test_distiller_prompt_uses_indices_not_model_names():
    members = [
        ScriptedModel("alice", critique="claim one"),
        ScriptedModel("bob", critique="claim two"),
    ]
    distiller = ScriptedModel("distiller", synthesis="map")

    verdict = Council(members, synthesizer=members[0], distiller=distiller).ask("q?")
    turn = verdict.transcript.by_round("consensus_map")[0]

    assert "CRITIQUE 1:" in turn.prompt
    assert "CRITIQUE 2:" in turn.prompt
    assert "alice" not in turn.prompt
    assert "bob" not in turn.prompt
    assert turn.meta["critique_models"] == ["alice", "bob"]


def test_agreement_statistics_match_hand_computed_cases():
    votes = [
        {"a": "yes", "b": "yes"},
        {"a": "yes", "b": "no"},
        {"a": "no", "b": "no"},
        {"a": "no", "b": "no"},
    ]

    # Cohen: observed=3/4. Expected=(2/4*1/4)+(2/4*3/4)=1/2.
    # Kappa=(0.75-0.5)/(1-0.5)=0.5.
    assert cohen_kappa(
        ["yes", "yes", "no", "no"],
        ["yes", "no", "no", "no"],
        labels=["yes", "no"],
    ) == pytest.approx(0.5)
    assert raw_agreement(votes) == pytest.approx(0.75)

    # AC1: Pa=0.75. Category proportions are yes=3/8, no=5/8.
    # Pe=sum(p_k*(1-p_k))/(2-1)=0.46875.
    # AC1=(0.75-0.46875)/(1-0.46875)=0.52941176.
    assert gwet_ac1(votes, labels=["yes", "no"]) == pytest.approx(0.5294117647)

    # Alpha nominal coincidence matrix is [[2,1],[1,4]], so Do=2/8.
    # De=((3*5)+(5*3))/(8*7). Alpha=1-(0.25/0.535714...)=0.533333...
    assert krippendorff_alpha(votes, labels=["yes", "no"]) == pytest.approx(
        0.5333333333
    )
    assert krippendorff_alpha(votes, labels=["yes", "no"], level="ordinal") == pytest.approx(
        0.5333333333
    )


def test_fleiss_kappa_matches_published_worked_example():
    # Fleiss/Wikipedia 10-item, 14-rater, 5-category example.
    # Published arithmetic: Pbar=0.37857, Pe=0.21276, kappa=0.20993.
    counts_by_item = [
        [0, 0, 0, 0, 14],
        [0, 2, 6, 4, 2],
        [0, 0, 3, 5, 6],
        [0, 3, 9, 2, 0],
        [2, 2, 8, 1, 1],
        [7, 7, 0, 0, 0],
        [3, 2, 6, 3, 0],
        [2, 5, 3, 2, 2],
        [6, 5, 2, 1, 0],
        [0, 2, 2, 3, 7],
    ]
    labels = ["1", "2", "3", "4", "5"]
    votes = []
    for counts in counts_by_item:
        item = {}
        rater = 0
        for label, count in zip(labels, counts):
            for _ in range(count):
                item[f"r{rater}"] = label
                rater += 1
        votes.append(item)

    result = fleiss_kappa(votes, labels=labels)
    assert result.used == 10
    assert result.dropped == 0
    assert result.value == pytest.approx(0.20993, abs=1e-5)


def test_bootstrap_summary_redundancy_n_effective_and_kappa_warning():
    clone_votes = [
        {"a": "yes", "b": "yes", "c": "yes"},
        {"a": "no", "b": "no", "c": "no"},
    ]
    clone_summary = summary(clone_votes, labels=["yes", "no"])
    assert clone_summary.n_effective == pytest.approx(1.0)
    assert clone_summary.redundancy == {"a": 1.0, "b": 1.0, "c": 1.0}

    skewed = [{"a": "yes", "b": "yes"} for _ in range(18)]
    skewed.extend([
        {"a": "yes", "b": "no"},
        {"a": "no", "b": "yes"},
    ])
    skewed_summary = summary(skewed, labels=["yes", "no"])
    assert skewed_summary.raw_agreement == pytest.approx(0.9)
    assert skewed_summary.fleiss_kappa < 0.4
    assert skewed_summary.kappa_paradox_warning is True
    assert skewed_summary.gwet_ac1 > skewed_summary.fleiss_kappa

    ci = bootstrap_ci(lambda rows: raw_agreement(rows), skewed, n=100, seed=3)
    assert ci == bootstrap_ci(lambda rows: raw_agreement(rows), skewed, n=100, seed=3)
    assert 0 <= ci[0] <= ci[1] <= 1


def test_vote_record_jsonl_roundtrip_and_council_store_hook(tmp_path):
    path = tmp_path / "votes.jsonl"
    store = RecordStore(path)
    record = VoteRecord(
        question_id="q1",
        question="Question?",
        labels=["yes", "no"],
        votes={"a": "yes", "b": None},
        confidences={"a": 5, "b": None},
        truth="yes",
        revotes={"a": "no", "b": "no"},
        revote_confidences={"a": 2, "b": 3},
    )
    assert VoteRecord.from_json(record.to_json()) == record

    store.add(record)
    assert store.load() == [record]
    assert store.votes_by_item() == [{"a": "yes", "b": None}]
    assert store.votes_by_item(use_revotes=True) == [{"a": "no", "b": "no"}]

    council_store = RecordStore(tmp_path / "council.jsonl")
    member = ScriptedModel("member", vote="VERDICT: yes\nCONFIDENCE: 5")
    Council([member], store=council_store).ask(
        "Stored?", labels=["yes", "no"], truth="yes", question_id="stored-1"
    )
    [stored] = council_store.load()
    assert stored.question_id == "stored-1"
    assert stored.votes == {"member": "yes"}
    assert stored.confidences == {"member": 5}
