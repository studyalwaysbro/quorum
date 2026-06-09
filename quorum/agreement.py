"""Agreement statistics for categorical council votes."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from itertools import combinations
from typing import Callable, Sequence

VoteItem = dict[str, str | None]


@dataclass(frozen=True)
class FleissKappaResult:
    value: float | None
    dropped: int
    used: int

    @property
    def kappa(self) -> float | None:
        return self.value


@dataclass(frozen=True)
class AgreementSummary:
    labels: list[str]
    members: list[str]
    n_items: int
    n_members: int
    raw_agreement: float | None
    pairwise_kappa: dict[str, dict[str, float | None]]
    mean_pairwise_kappa: float | None
    fleiss_kappa: float | None
    fleiss_dropped: int
    krippendorff_alpha_nominal: float | None
    krippendorff_alpha_ordinal: float | None
    gwet_ac1: float | None
    redundancy: dict[str, float | None]
    n_effective: float
    kappa_paradox_warning: bool


def raw_agreement(votes_by_item: Sequence[VoteItem]) -> float | None:
    agree = 0
    total = 0
    for item in votes_by_item:
        values = [vote for vote in item.values() if vote is not None]
        for left, right in combinations(values, 2):
            total += 1
            if left == right:
                agree += 1
    if total == 0:
        return None
    return agree / total


def cohen_kappa(
    a_votes: Sequence[str | None],
    b_votes: Sequence[str | None],
    labels: Sequence[str] | None = None,
) -> float | None:
    pairs = [
        (a, b)
        for a, b in zip(a_votes, b_votes)
        if a is not None and b is not None
    ]
    if not pairs:
        return None
    labels = _labels_from_pairs(pairs, labels)
    observed = sum(1 for a, b in pairs if a == b) / len(pairs)
    a_marginals = {label: 0 for label in labels}
    b_marginals = {label: 0 for label in labels}
    for a, b in pairs:
        a_marginals[a] += 1
        b_marginals[b] += 1
    expected = sum(
        (a_marginals[label] / len(pairs)) * (b_marginals[label] / len(pairs))
        for label in labels
    )
    return _chance_correct(observed, expected)


def pairwise_kappa_matrix(
    votes_by_item: Sequence[VoteItem],
    labels: Sequence[str] | None = None,
) -> dict[str, dict[str, float | None]]:
    labels = _labels(votes_by_item, labels)
    members = _members(votes_by_item)
    by_member = {
        member: [item.get(member) for item in votes_by_item]
        for member in members
    }
    matrix: dict[str, dict[str, float | None]] = {
        member: {other: None for other in members}
        for member in members
    }
    for member in members:
        matrix[member][member] = 1.0
    for left, right in combinations(members, 2):
        value = cohen_kappa(by_member[left], by_member[right], labels)
        matrix[left][right] = value
        matrix[right][left] = value
    return matrix


def fleiss_kappa(
    votes_by_item: Sequence[VoteItem],
    labels: Sequence[str] | None = None,
) -> FleissKappaResult:
    labels = _labels(votes_by_item, labels)
    members = _members(votes_by_item)
    if len(labels) < 2 or len(members) < 2:
        return FleissKappaResult(None, dropped=len(votes_by_item), used=0)

    complete_items: list[VoteItem] = []
    dropped = 0
    for item in votes_by_item:
        if all(item.get(member) is not None for member in members):
            complete_items.append(item)
        else:
            dropped += 1
    if not complete_items:
        return FleissKappaResult(None, dropped=dropped, used=0)

    n_raters = len(members)
    p_i_sum = 0.0
    category_totals = {label: 0 for label in labels}
    for item in complete_items:
        counts = _counts(item.values(), labels)
        for label, count in counts.items():
            category_totals[label] += count
        p_i_sum += (
            sum(count * count for count in counts.values()) - n_raters
        ) / (n_raters * (n_raters - 1))

    p_bar = p_i_sum / len(complete_items)
    total_ratings = len(complete_items) * n_raters
    p_e = sum(
        (category_totals[label] / total_ratings) ** 2
        for label in labels
    )
    return FleissKappaResult(
        _chance_correct(p_bar, p_e),
        dropped=dropped,
        used=len(complete_items),
    )


def krippendorff_alpha(
    votes_by_item: Sequence[VoteItem],
    labels: Sequence[str] | None = None,
    level: str = "nominal",
) -> float | None:
    if level not in {"nominal", "ordinal"}:
        raise ValueError("level must be 'nominal' or 'ordinal'")
    labels = _labels(votes_by_item, labels)
    if len(labels) < 2:
        return None

    label_index = {label: i for i, label in enumerate(labels)}
    coincidence = [[0.0 for _ in labels] for _ in labels]
    for item in votes_by_item:
        values = [vote for vote in item.values() if vote is not None]
        if len(values) < 2:
            continue
        counts = _counts(values, labels)
        r_i = len(values)
        for left in labels:
            for right in labels:
                if left == right:
                    contribution = counts[left] * (counts[left] - 1)
                else:
                    contribution = counts[left] * counts[right]
                coincidence[label_index[left]][label_index[right]] += (
                    contribution / (r_i - 1)
                )

    total = sum(sum(row) for row in coincidence)
    if total <= 1:
        return None
    marginals = [sum(row) for row in coincidence]

    observed = 0.0
    expected = 0.0
    for i, left in enumerate(labels):
        for j, right in enumerate(labels):
            distance = _distance(i, j, level)
            observed += coincidence[i][j] * distance
            expected += marginals[i] * marginals[j] * distance
    observed /= total
    expected /= total * (total - 1)
    return _chance_correct(1.0 - observed, 1.0 - expected)


def gwet_ac1(
    votes_by_item: Sequence[VoteItem],
    labels: Sequence[str] | None = None,
) -> float | None:
    labels = _labels(votes_by_item, labels)
    if len(labels) < 2:
        return None

    observed_sum = 0.0
    used_items = 0
    category_totals = {label: 0 for label in labels}
    total_ratings = 0
    for item in votes_by_item:
        values = [vote for vote in item.values() if vote is not None]
        r_i = len(values)
        if r_i < 2:
            continue
        counts = _counts(values, labels)
        observed_sum += sum(
            count * (count - 1) for count in counts.values()
        ) / (r_i * (r_i - 1))
        used_items += 1
        total_ratings += r_i
        for label, count in counts.items():
            category_totals[label] += count

    if used_items == 0 or total_ratings == 0:
        return None
    observed = observed_sum / used_items
    p_e = sum(
        (category_totals[label] / total_ratings)
        * (1 - category_totals[label] / total_ratings)
        for label in labels
    ) / (len(labels) - 1)
    return _chance_correct(observed, p_e)


def bootstrap_ci(
    stat_fn: Callable[[list[VoteItem]], float | FleissKappaResult | None],
    votes_by_item: Sequence[VoteItem],
    n: int = 1000,
    seed: int | None = None,
) -> tuple[float | None, float | None]:
    if n < 1:
        raise ValueError("n must be at least 1")
    if not votes_by_item:
        return None, None

    rng = random.Random(seed)
    values: list[float] = []
    items = list(votes_by_item)
    for _ in range(n):
        sample = [items[rng.randrange(len(items))] for _ in items]
        value = _stat_value(stat_fn(sample))
        if value is not None and not math.isnan(value):
            values.append(value)
    if not values:
        return None, None
    values.sort()
    return _quantile(values, 0.025), _quantile(values, 0.975)


def summary(
    votes_by_item: Sequence[VoteItem],
    labels: Sequence[str] | None = None,
) -> AgreementSummary:
    labels = _labels(votes_by_item, labels)
    members = _members(votes_by_item)
    raw = raw_agreement(votes_by_item)
    matrix = pairwise_kappa_matrix(votes_by_item, labels)
    pair_values = [
        matrix[left][right]
        for left, right in combinations(members, 2)
        if matrix[left][right] is not None
    ]
    mean_pairwise = (
        sum(value for value in pair_values if value is not None) / len(pair_values)
        if pair_values
        else None
    )
    fleiss = fleiss_kappa(votes_by_item, labels)
    redundancy = {
        member: _mean(
            matrix[member][other]
            for other in members
            if other != member and matrix[member][other] is not None
        )
        for member in members
    }
    n_effective = _n_effective(len(members), mean_pairwise)
    return AgreementSummary(
        labels=list(labels),
        members=members,
        n_items=len(votes_by_item),
        n_members=len(members),
        raw_agreement=raw,
        pairwise_kappa=matrix,
        mean_pairwise_kappa=mean_pairwise,
        fleiss_kappa=fleiss.value,
        fleiss_dropped=fleiss.dropped,
        krippendorff_alpha_nominal=krippendorff_alpha(votes_by_item, labels, "nominal"),
        krippendorff_alpha_ordinal=krippendorff_alpha(votes_by_item, labels, "ordinal"),
        gwet_ac1=gwet_ac1(votes_by_item, labels),
        redundancy=redundancy,
        n_effective=n_effective,
        kappa_paradox_warning=(
            raw is not None
            and raw >= 0.8
            and fleiss.value is not None
            and fleiss.value < 0.4
        ),
    )


def _labels(votes_by_item: Sequence[VoteItem], labels: Sequence[str] | None) -> list[str]:
    if labels is not None:
        normalized = [label.strip() for label in labels]
    else:
        seen = {
            vote
            for item in votes_by_item
            for vote in item.values()
            if vote is not None
        }
        normalized = sorted(seen)
    if len(normalized) != len(set(normalized)):
        raise ValueError("labels must be unique")
    if any(not label for label in normalized):
        raise ValueError("labels must be non-empty")
    outside = {
        vote
        for item in votes_by_item
        for vote in item.values()
        if vote is not None and vote not in normalized
    }
    if outside:
        raise ValueError(f"votes contain labels not in labels: {sorted(outside)}")
    return normalized


def _labels_from_pairs(
    pairs: Sequence[tuple[str, str]],
    labels: Sequence[str] | None,
) -> list[str]:
    if labels is not None:
        normalized = [label.strip() for label in labels]
    else:
        normalized = sorted({vote for pair in pairs for vote in pair})
    if len(normalized) < 2:
        return normalized
    outside = {vote for pair in pairs for vote in pair if vote not in normalized}
    if outside:
        raise ValueError(f"votes contain labels not in labels: {sorted(outside)}")
    return normalized


def _members(votes_by_item: Sequence[VoteItem]) -> list[str]:
    return sorted({member for item in votes_by_item for member in item})


def _counts(values, labels: Sequence[str]) -> dict[str, int]:
    counts = {label: 0 for label in labels}
    for value in values:
        if value is not None:
            counts[value] += 1
    return counts


def _distance(left: int, right: int, level: str) -> float:
    if left == right:
        return 0.0
    if level == "nominal":
        return 1.0
    return float((left - right) ** 2)


def _chance_correct(observed: float, expected: float) -> float:
    denominator = 1.0 - expected
    if abs(denominator) < 1e-12:
        return 1.0 if abs(observed - 1.0) < 1e-12 else 0.0
    return (observed - expected) / denominator


def _mean(values) -> float | None:
    collected = [value for value in values if value is not None]
    if not collected:
        return None
    return sum(collected) / len(collected)


def _n_effective(n_members: int, mean_pairwise_kappa: float | None) -> float:
    if n_members == 0:
        return 0.0
    if mean_pairwise_kappa is None or mean_pairwise_kappa <= 0:
        return float(n_members)
    value = n_members / (1 + (n_members - 1) * mean_pairwise_kappa)
    return max(1.0, value)


def _stat_value(value: float | FleissKappaResult | None) -> float | None:
    if isinstance(value, FleissKappaResult):
        return value.value
    return value


def _quantile(values: Sequence[float], p: float) -> float:
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * p
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return values[lower]
    weight = index - lower
    return values[lower] * (1 - weight) + values[upper] * weight
