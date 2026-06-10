"""Council construction and member diagnostics.

The routines here operate on ``VoteRecord`` artifacts so they can be used with
stored historical votes as well as synthetic offline experiments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from quorum.agreement import pairwise_kappa_matrix
from quorum.records import VoteRecord, records_to_votes


_DS_SMOOTHING = 0.1


@dataclass(frozen=True)
class DawidSkeneResult:
    """Estimated latent labels and member confusion matrices.

    ``confusion[member][voted][true]`` is ``P(member votes voted | true label)``.
    The implementation uses add-0.1 Laplace smoothing: enough to keep zero cells
    from locking the EM loop, but not so much that tiny councils become uniform.
    """

    labels: list[str]
    posteriors: dict[str, dict[str, float]]
    confusion: dict[str, dict[str, dict[str, float]]]
    skill: dict[str, float]
    priors: dict[str, float]
    iterations: int
    converged: bool

    def weighted_verdict(self, votes: Mapping[str, str | None]) -> dict[str, float]:
        """Score a new item with the learned priors and member confusions."""
        if not self.labels:
            return {}

        weights = dict(self.priors)
        for member, vote in votes.items():
            if vote is None:
                continue
            member_confusion = self.confusion.get(member)
            if member_confusion is None or vote not in member_confusion:
                continue
            for label in self.labels:
                weights[label] *= member_confusion[vote][label]
        return _normalize(weights, self.labels)


@dataclass(frozen=True)
class MemberAccuracy:
    accuracy: float | None
    n: int
    weight: float | None


@dataclass(frozen=True)
class RosterCandidateScore:
    member: str
    accuracy: float
    mean_kappa: float
    diversity_penalty: float
    score: float
    kappa_by_selected: dict[str, float | None]


@dataclass(frozen=True)
class RosterStep:
    selected: str
    scores: list[RosterCandidateScore]


@dataclass(frozen=True)
class RosterResult:
    picks: list[str]
    steps: list[RosterStep]
    accuracy_source: str

    def __iter__(self):
        return iter(self.picks)

    def __len__(self) -> int:
        return len(self.picks)

    def __getitem__(self, index: int) -> str:
        return self.picks[index]


def dawid_skene(
    records: Sequence[VoteRecord],
    *,
    max_iter: int = 50,
    tol: float = 1e-6,
) -> DawidSkeneResult:
    """Run classic Dawid-Skene EM over categorical vote records."""
    if max_iter < 1:
        raise ValueError("max_iter must be at least 1")
    if tol <= 0:
        raise ValueError("tol must be positive")

    records = list(records)
    labels = _labels_from_records(records)
    if not records or not labels:
        return DawidSkeneResult(
            labels=labels,
            posteriors={},
            confusion={},
            skill={},
            priors={},
            iterations=0,
            converged=True,
        )

    _validate_unique_question_ids(records)
    members = _members(records)
    posteriors = _initial_posteriors(records, labels)
    priors: dict[str, float] = {}
    confusion: dict[str, dict[str, dict[str, float]]] = {}
    converged = False
    iterations = 0

    for iteration in range(1, max_iter + 1):
        priors, confusion = _m_step(records, labels, members, posteriors)
        next_posteriors = _e_step(records, labels, priors, confusion)
        delta = _posterior_delta(posteriors, next_posteriors, labels)
        posteriors = next_posteriors
        iterations = iteration
        if delta < tol:
            converged = True
            break

    priors, confusion = _m_step(records, labels, members, posteriors)
    skill = {
        member: sum(priors[label] * confusion[member][label][label] for label in labels)
        for member in members
    }
    return DawidSkeneResult(
        labels=labels,
        posteriors=posteriors,
        confusion=confusion,
        skill=skill,
        priors=priors,
        iterations=iterations,
        converged=converged,
    )


def member_accuracy(records: Sequence[VoteRecord]) -> dict[str, MemberAccuracy]:
    """Return truth-set accuracy, count, and Nitzan-Paroush log-odds weight."""
    records = list(records)
    truth_records = [record for record in records if record.truth is not None]
    if not truth_records:
        return {}

    labels = _labels_from_records(truth_records)
    k = max(2, len(labels))
    counts = {member: [0, 0] for member in _members(records)}
    for record in truth_records:
        if record.truth not in labels:
            raise ValueError(f"truth label not in labels: {record.truth!r}")
        for member, vote in record.votes.items():
            if vote is None:
                continue
            counts.setdefault(member, [0, 0])
            counts[member][1] += 1
            if vote == record.truth:
                counts[member][0] += 1

    result: dict[str, MemberAccuracy] = {}
    for member in sorted(counts):
        correct, total = counts[member]
        if total == 0:
            result[member] = MemberAccuracy(accuracy=None, n=0, weight=None)
            continue
        accuracy = correct / total
        result[member] = MemberAccuracy(
            accuracy=accuracy,
            n=total,
            weight=_nitzan_paroush_weight(accuracy, k),
        )
    return result


def build_roster(
    records: Sequence[VoteRecord],
    pool: Sequence[object],
    size: int,
    lam: float = 0.5,
) -> RosterResult:
    """Greedily select accurate members while penalizing redundant agreement.

    If any truth labels exist, the accuracy term is supervised member accuracy.
    If no truth exists, it falls back to Dawid-Skene skill; that estimate is
    self-referential because it is learned from the same council votes.
    """
    if size < 0:
        raise ValueError("size must be non-negative")
    if lam < 0:
        raise ValueError("lam must be non-negative")
    names = _pool_names(pool)
    if size > len(names):
        raise ValueError("size cannot exceed the pool size")

    records = list(records)
    has_truth = any(record.truth is not None for record in records)
    if has_truth:
        accuracy_source = "truth"
        truth_accuracy = member_accuracy(records)
        accuracy = {
            name: (
                truth_accuracy[name].accuracy
                if name in truth_accuracy and truth_accuracy[name].accuracy is not None
                else 0.0
            )
            for name in names
        }
    else:
        accuracy_source = "dawid_skene"
        ds = dawid_skene(records) if records else None
        accuracy = {name: (ds.skill.get(name, 0.0) if ds else 0.0) for name in names}

    labels = _labels_from_records(records)
    kappa = (
        pairwise_kappa_matrix(records_to_votes(records), labels=labels)
        if records and len(labels) >= 2
        else {}
    )

    selected: list[str] = []
    steps: list[RosterStep] = []
    order = {name: index for index, name in enumerate(names)}
    for _ in range(size):
        candidate_scores: list[RosterCandidateScore] = []
        for candidate in names:
            if candidate in selected:
                continue
            kappa_by_selected: dict[str, float | None] = {}
            kappa_values: list[float] = []
            for chosen in selected:
                value = kappa.get(candidate, {}).get(chosen)
                kappa_by_selected[chosen] = value
                if value is not None:
                    kappa_values.append(value)
            mean_kappa = sum(kappa_values) / len(kappa_values) if kappa_values else 0.0
            diversity_penalty = lam * mean_kappa if selected else 0.0
            score = accuracy[candidate] - diversity_penalty
            candidate_scores.append(
                RosterCandidateScore(
                    member=candidate,
                    accuracy=accuracy[candidate],
                    mean_kappa=mean_kappa,
                    diversity_penalty=diversity_penalty,
                    score=score,
                    kappa_by_selected=kappa_by_selected,
                )
            )

        best = max(candidate_scores, key=lambda item: (item.score, -order[item.member]))
        selected.append(best.member)
        steps.append(
            RosterStep(
                selected=best.member,
                scores=sorted(
                    candidate_scores,
                    key=lambda item: (-item.score, order[item.member]),
                ),
            )
        )

    return RosterResult(
        picks=selected,
        steps=steps,
        accuracy_source=accuracy_source,
    )


def drop_one(
    records: Sequence[VoteRecord],
    members: Sequence[object],
) -> dict[str, float | None]:
    """Return leave-one-out majority accuracy delta for each member.

    Deltas are ``leave_one_out_accuracy - full_council_accuracy``. Negative
    means removing the member hurt accuracy; positive means removing it helped.
    """
    records = list(records)
    names = _pool_names(members)
    truth_records = [record for record in records if record.truth is not None]
    if not truth_records:
        return {name: None for name in names}

    full_accuracy = _majority_accuracy(truth_records, names)
    return {
        name: _majority_accuracy(
            truth_records,
            [candidate for candidate in names if candidate != name],
        )
        - full_accuracy
        for name in names
    }


def _m_step(
    records: Sequence[VoteRecord],
    labels: Sequence[str],
    members: Sequence[str],
    posteriors: Mapping[str, Mapping[str, float]],
) -> tuple[dict[str, float], dict[str, dict[str, dict[str, float]]]]:
    priors = {
        label: sum(posteriors[record.question_id][label] for record in records)
        / len(records)
        for label in labels
    }
    confusion: dict[str, dict[str, dict[str, float]]] = {}
    for member in members:
        counts = {
            voted: {true: _DS_SMOOTHING for true in labels}
            for voted in labels
        }
        for record in records:
            vote = record.votes.get(member)
            if vote not in labels:
                continue
            posterior = posteriors[record.question_id]
            for true in labels:
                counts[vote][true] += posterior[true]

        member_confusion = {voted: {} for voted in labels}
        for true in labels:
            denominator = sum(counts[voted][true] for voted in labels)
            for voted in labels:
                member_confusion[voted][true] = counts[voted][true] / denominator
        confusion[member] = member_confusion
    return priors, confusion


def _e_step(
    records: Sequence[VoteRecord],
    labels: Sequence[str],
    priors: Mapping[str, float],
    confusion: Mapping[str, Mapping[str, Mapping[str, float]]],
) -> dict[str, dict[str, float]]:
    posteriors: dict[str, dict[str, float]] = {}
    for record in records:
        weights = dict(priors)
        for member, vote in record.votes.items():
            if vote is None:
                continue
            member_confusion = confusion.get(member)
            if member_confusion is None or vote not in member_confusion:
                continue
            for label in labels:
                weights[label] *= member_confusion[vote][label]
        posteriors[record.question_id] = _normalize(weights, labels)
    return posteriors


def _initial_posteriors(
    records: Sequence[VoteRecord],
    labels: Sequence[str],
) -> dict[str, dict[str, float]]:
    posteriors: dict[str, dict[str, float]] = {}
    for record in records:
        counts = {label: 0 for label in labels}
        for vote in record.votes.values():
            if vote in counts:
                counts[vote] += 1
        best = max(counts.values())
        if best == 0:
            posteriors[record.question_id] = _uniform(labels)
            continue
        winners = [label for label, count in counts.items() if count == best]
        posteriors[record.question_id] = {
            label: (1 / len(winners) if label in winners else 0.0)
            for label in labels
        }
    return posteriors


def _posterior_delta(
    previous: Mapping[str, Mapping[str, float]],
    current: Mapping[str, Mapping[str, float]],
    labels: Sequence[str],
) -> float:
    deltas = [
        abs(previous[question_id][label] - current[question_id][label])
        for question_id in previous
        for label in labels
    ]
    return max(deltas) if deltas else 0.0


def _majority_accuracy(records: Sequence[VoteRecord], members: Sequence[str]) -> float:
    if not records:
        return 0.0
    correct = 0
    for record in records:
        winner = _majority(record, members)
        if winner is not None and winner == record.truth:
            correct += 1
    return correct / len(records)


def _majority(record: VoteRecord, members: Sequence[str]) -> str | None:
    labels = _labels_from_records([record])
    counts = {label: 0 for label in labels}
    for member in members:
        vote = record.votes.get(member)
        if vote in counts:
            counts[vote] += 1
    best = max(counts.values()) if counts else 0
    if best == 0:
        return None
    winners = [label for label, count in counts.items() if count == best]
    return winners[0] if len(winners) == 1 else None


def _labels_from_records(records: Sequence[VoteRecord]) -> list[str]:
    labels: list[str] = []
    folded: set[str] = set()
    for record in records:
        for label in [*record.labels, *record.votes.values(), record.truth]:
            if label is None:
                continue
            normalized = str(label).strip()
            if not normalized:
                raise ValueError("labels must be non-empty")
            key = normalized.casefold()
            if key not in folded:
                labels.append(normalized)
                folded.add(key)
    if labels and len(labels) < 2:
        raise ValueError("at least two labels are required")
    return labels


def _members(records: Sequence[VoteRecord]) -> list[str]:
    return sorted({member for record in records for member in record.votes})


def _pool_names(pool: Sequence[object]) -> list[str]:
    names = [item if isinstance(item, str) else getattr(item, "name") for item in pool]
    names = [str(name) for name in names]
    if len(set(names)) != len(names):
        raise ValueError("pool member names must be unique")
    return names


def _normalize(weights: Mapping[str, float], labels: Sequence[str]) -> dict[str, float]:
    total = sum(weights.get(label, 0.0) for label in labels)
    if total <= 0 or not math.isfinite(total):
        return _uniform(labels)
    return {label: weights.get(label, 0.0) / total for label in labels}


def _uniform(labels: Sequence[str]) -> dict[str, float]:
    if not labels:
        return {}
    return {label: 1 / len(labels) for label in labels}


def _nitzan_paroush_weight(accuracy: float, k: int) -> float:
    lower = 1 / k + 1e-6
    upper = 1 - 1e-6
    p = min(max(accuracy, lower), upper)
    return math.log((p * (k - 1)) / (1 - p))


def _validate_unique_question_ids(records: Sequence[VoteRecord]) -> None:
    seen: set[str] = set()
    for record in records:
        if record.question_id in seen:
            raise ValueError(f"duplicate question_id: {record.question_id!r}")
        seen.add(record.question_id)
