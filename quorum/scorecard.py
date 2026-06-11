"""Agreement scorecard — quantify how the adversary moved the council.

For a single live question, the honest measure is *descriptive*: pairwise
agreement among the members' categorical votes, the tally, the plurality, and
mean confidence — measured after the blind vote and again after a post-adversary
revote, so you can show the movement and who flipped. We deliberately do NOT
headline Fleiss kappa here: kappa is an inferential statistic over many items,
and a single question is one item. (Issue-level kappa across a frozen issue map
is the rigorous multi-item path — a later increment.)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Sequence

from quorum.agreement import raw_agreement
from quorum.votes import majority_label, normalize_labels, tally_votes

SINGLE_ITEM_NOTE = (
    "Single question: pairwise agreement is descriptive, not inferential κ — "
    "true κ needs multiple items (e.g. issue-level voting across a frozen map)."
)


@dataclass(frozen=True)
class StageAgreement:
    stage: str                       # "blind" | "final"
    labels: list[str]
    tally: dict[str, int]
    majority: Optional[str]
    raw_agreement: Optional[float]   # pairwise agreement among non-null votes
    mean_confidence: Optional[float]
    n_voted: int


@dataclass(frozen=True)
class AgreementDelta:
    before: StageAgreement
    after: Optional[StageAgreement]
    flips: dict[str, tuple[Optional[str], Optional[str]]]
    raw_agreement_change: Optional[float]
    confidence_change: Optional[float]
    note: str = SINGLE_ITEM_NOTE


def _mean_confidence(confidences: dict[str, Optional[int]]) -> Optional[float]:
    vals = [c for c in confidences.values() if c is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def stage_agreement(
    stage: str,
    votes: dict[str, Optional[str]],
    confidences: dict[str, Optional[int]],
    labels: Sequence[str],
) -> StageAgreement:
    labels = normalize_labels(labels)
    tally = tally_votes(votes, labels)
    ra = raw_agreement([votes]) if votes else None
    return StageAgreement(
        stage=stage,
        labels=list(labels),
        tally=tally,
        majority=majority_label(tally, labels),
        raw_agreement=(round(ra, 3) if ra is not None else None),
        mean_confidence=_mean_confidence(confidences),
        n_voted=sum(1 for v in votes.values() if v is not None),
    )


def agreement_delta(
    blind_votes: dict[str, Optional[str]],
    blind_confidences: dict[str, Optional[int]],
    revotes: dict[str, Optional[str]],
    revote_confidences: dict[str, Optional[int]],
    labels: Sequence[str],
) -> AgreementDelta:
    before = stage_agreement("blind", blind_votes, blind_confidences, labels)
    after = (
        stage_agreement("final", revotes, revote_confidences, labels)
        if revotes else None
    )
    flips = {
        name: (blind_votes.get(name), revotes.get(name))
        for name in blind_votes
        if name in revotes and blind_votes.get(name) != revotes.get(name)
    }
    ra_change = (
        round(after.raw_agreement - before.raw_agreement, 3)
        if after and after.raw_agreement is not None and before.raw_agreement is not None
        else None
    )
    cf_change = (
        round(after.mean_confidence - before.mean_confidence, 2)
        if after and after.mean_confidence is not None and before.mean_confidence is not None
        else None
    )
    return AgreementDelta(before, after, flips, ra_change, cf_change)


def delta_to_dict(delta: AgreementDelta) -> dict:
    return {
        "before": asdict(delta.before),
        "after": asdict(delta.after) if delta.after else None,
        "flips": {k: list(v) for k, v in delta.flips.items()},
        "raw_agreement_change": delta.raw_agreement_change,
        "confidence_change": delta.confidence_change,
        "note": delta.note,
    }
