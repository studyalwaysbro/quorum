import pytest

from quorum.records import VoteRecord
from quorum.roster import build_roster, dawid_skene, drop_one, member_accuracy


LABELS = ["yes", "no"]


def other(label):
    return "no" if label == "yes" else "yes"


def test_dawid_skene_recovers_adversarial_flipper_and_scores_new_items():
    records = []
    for index in range(20):
        truth = "yes" if index % 2 == 0 else "no"
        records.append(
            VoteRecord(
                question_id=f"q{index}",
                question="q?",
                labels=LABELS,
                truth=truth,
                votes={
                    "good1": truth,
                    "good2": truth,
                    "good3": truth,
                    "flipper": other(truth),
                },
                confidences={},
            )
        )

    result = dawid_skene(records)

    assert result.converged is True
    assert result.skill["good1"] > 0.95
    assert result.skill["flipper"] < 0.05
    assert result.confusion["flipper"]["no"]["yes"] > 0.95
    assert result.confusion["flipper"]["yes"]["no"] > 0.95
    posterior = result.weighted_verdict(
        {"good1": "yes", "good2": "yes", "good3": "yes", "flipper": "no"}
    )
    assert posterior["yes"] > 0.99


def test_member_accuracy_reports_counts_and_log_odds_weight():
    records = [
        VoteRecord(
            question_id="q1",
            question="q?",
            labels=LABELS,
            truth="yes",
            votes={"alice": "yes", "bob": "no"},
            confidences={},
        ),
        VoteRecord(
            question_id="q2",
            question="q?",
            labels=LABELS,
            truth="no",
            votes={"alice": "yes", "bob": "no"},
            confidences={},
        ),
    ]

    stats = member_accuracy(records)

    assert stats["alice"].accuracy == pytest.approx(0.5)
    assert stats["alice"].n == 2
    assert stats["alice"].weight > 0
    assert stats["bob"].accuracy == pytest.approx(0.5)


def test_build_roster_prefers_decorrelated_member_over_clone_for_third_seat():
    records = []
    b_truth_flips = {0}
    diverse_truth_flips = {1, 2, 3, 4, 5}
    for index in range(20):
        truth = "yes" if index % 2 == 0 else "no"
        records.append(
            VoteRecord(
                question_id=f"truth-{index}",
                question="q?",
                labels=LABELS,
                truth=truth,
                votes={
                    "a": truth,
                    "clone": truth,
                    "b": other(truth) if index in b_truth_flips else truth,
                    "diverse": (
                        other(truth) if index in diverse_truth_flips else truth
                    ),
                },
                confidences={},
            )
        )

    b_unlabeled_flips = {0, 6, 12, 18, 24}
    diverse_unlabeled_flips = {1, 4, 7, 10, 13, 16, 19, 22, 25, 28}
    for index in range(30):
        base = "yes" if index % 2 == 0 else "no"
        records.append(
            VoteRecord(
                question_id=f"unlabeled-{index}",
                question="q?",
                labels=LABELS,
                truth=None,
                votes={
                    "a": base,
                    "clone": base,
                    "b": other(base) if index in b_unlabeled_flips else base,
                    "diverse": (
                        other(base) if index in diverse_unlabeled_flips else base
                    ),
                },
                confidences={},
            )
        )

    roster = build_roster(records, ["a", "clone", "b", "diverse"], 3, lam=0.5)

    assert roster.accuracy_source == "truth"
    assert roster.picks == ["a", "b", "diverse"]
    third_scores = {score.member: score for score in roster.steps[2].scores}
    assert third_scores["diverse"].accuracy < third_scores["clone"].accuracy
    assert third_scores["diverse"].score > third_scores["clone"].score


def test_build_roster_falls_back_to_dawid_skene_without_truth():
    records = [
        VoteRecord(
            question_id="q1",
            question="q?",
            labels=LABELS,
            votes={"a": "yes", "b": "yes", "c": "no"},
            confidences={},
        ),
        VoteRecord(
            question_id="q2",
            question="q?",
            labels=LABELS,
            votes={"a": "no", "b": "no", "c": "yes"},
            confidences={},
        ),
    ]

    roster = build_roster(records, ["a", "b", "c"], 2)

    assert roster.accuracy_source == "dawid_skene"
    assert roster.picks[0] in {"a", "b"}


def test_drop_one_delta_signs_show_costly_and_harmful_removals():
    records = []
    for index in range(6):
        truth = "yes" if index % 2 == 0 else "no"
        records.append(
            VoteRecord(
                question_id=f"q{index}",
                question="q?",
                labels=LABELS,
                truth=truth,
                votes={
                    "expert": truth,
                    "supporter": truth,
                    "spoiler": other(truth),
                },
                confidences={},
            )
        )

    deltas = drop_one(records, ["expert", "supporter", "spoiler"])

    assert deltas["expert"] == pytest.approx(-1.0)
    assert deltas["supporter"] == pytest.approx(-1.0)
    assert deltas["spoiler"] == pytest.approx(0.0)
