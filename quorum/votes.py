"""Structured categorical vote rounds."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Sequence

from quorum.adapters import Model
from quorum.transcript import Transcript


_VOTE_RE = re.compile(
    r"(?:^|\n)\s*VERDICT\s*:\s*(?P<label>[^\n]+?)\s*"
    r"\n\s*CONFIDENCE\s*:\s*(?P<confidence>[1-5])\s*$",
    re.IGNORECASE,
)


class VoteParseError(ValueError):
    """Raised when a model response does not end in a valid vote block."""


@dataclass(frozen=True)
class VoteRoundResult:
    votes: dict[str, str | None]
    confidences: dict[str, int | None]


def normalize_labels(labels: Sequence[str]) -> list[str]:
    normalized = [label.strip() for label in labels]
    if len(normalized) < 2:
        raise ValueError("labels must contain at least two choices")
    if any(not label for label in normalized):
        raise ValueError("labels must be non-empty strings")
    folded = [label.casefold() for label in normalized]
    if len(set(folded)) != len(folded):
        raise ValueError("labels must be unique ignoring case")
    return normalized


def parse_vote(text: str, labels: Sequence[str]) -> tuple[str, int]:
    """Parse final VERDICT/CONFIDENCE lines, returning the canonical label."""
    labels = normalize_labels(labels)
    match = _VOTE_RE.search(text.strip())
    if match is None:
        raise VoteParseError("vote response must end with VERDICT and CONFIDENCE lines")

    by_folded = {label.casefold(): label for label in labels}
    raw_label = match.group("label").strip()
    label = by_folded.get(raw_label.casefold())
    if label is None:
        allowed = ", ".join(labels)
        raise VoteParseError(f"verdict must be one of: {allowed}")
    return label, int(match.group("confidence"))


def tally_votes(votes: dict[str, str | None], labels: Sequence[str]) -> dict[str, int]:
    labels = normalize_labels(labels)
    tally = {label: 0 for label in labels}
    for vote in votes.values():
        if vote in tally:
            tally[vote] += 1
    return tally


def majority_label(tally: dict[str, int], labels: Sequence[str]) -> str | None:
    labels = normalize_labels(labels)
    if not tally:
        return None
    best_count = max(tally.get(label, 0) for label in labels)
    if best_count == 0:
        return None
    winners = [label for label in labels if tally.get(label, 0) == best_count]
    return winners[0] if len(winners) == 1 else None


def format_tally(tally: dict[str, int], labels: Sequence[str]) -> str:
    labels = normalize_labels(labels)
    return "\n".join(f"- {label}: {tally.get(label, 0)}" for label in labels)


def vote_round(
    models: Sequence[Model],
    question: str,
    labels: Sequence[str],
    t: Transcript,
    max_workers: Optional[int] = None,
) -> VoteRoundResult:
    """Ask each member to classify its own blind answer."""
    labels = normalize_labels(labels)
    blind_by_model = {turn.model: turn.response for turn in t.by_round("blind")}

    def prompt_for(model: Model) -> str:
        blind_answer = blind_by_model.get(model.name, "")
        return (
            "Given your answer above, choose the best categorical verdict. "
            "You are voting on your own independent answer, not on any other "
            "member's answer.\n\n"
            f"QUESTION:\n{question}\n\n"
            f"YOUR BLIND ANSWER:\n{blind_answer}\n\n"
            f"LABELS:\n{_label_lines(labels)}\n\n"
            "End your response with exactly these two lines:\n"
            "VERDICT: <one label>\n"
            "CONFIDENCE: <1-5>"
        )

    return _run_vote_jobs(
        models=models,
        labels=labels,
        t=t,
        round_name="vote",
        prompt_for=prompt_for,
        max_workers=max_workers,
    )


def revote_round(
    models: Sequence[Model],
    question: str,
    labels: Sequence[str],
    t: Transcript,
    prior_votes: dict[str, str | None],
    prior_confidences: dict[str, int | None],
    blind_tally: dict[str, int],
    max_workers: Optional[int] = None,
) -> VoteRoundResult:
    """Allow one post-map vote revision while preserving the blind vote."""
    labels = normalize_labels(labels)
    blind_by_model = {turn.model: turn.response for turn in t.by_round("blind")}
    consensus = "\n\n".join(turn.response for turn in t.by_round("consensus_map"))
    if not consensus:
        consensus = "No consensus/issue map was recorded."
    adversarial = "\n\n".join(turn.response for turn in t.by_round("adversarial"))

    def prompt_for(model: Model) -> str:
        previous = prior_votes.get(model.name)
        confidence = prior_confidences.get(model.name)
        previous_line = (
            f"{previous} (confidence {confidence})"
            if previous is not None and confidence is not None
            else "ABSTAINED / unparsed"
        )
        adversarial_block = (
            f"\n\nANONYMIZED ADVERSARIAL OBJECTION:\n{adversarial}"
            if adversarial
            else ""
        )
        return (
            "You may revise your categorical vote once. Treat movement toward "
            "the current majority as a possible conformity risk; revise only "
            "if the anonymous discussion changed your judgment.\n\n"
            f"QUESTION:\n{question}\n\n"
            f"YOUR BLIND ANSWER:\n{blind_by_model.get(model.name, '')}\n\n"
            f"YOUR BLIND VOTE:\n{previous_line}\n\n"
            f"ANONYMIZED CONSENSUS / ISSUE MAP:\n{consensus}"
            f"{adversarial_block}\n\n"
            f"BLIND VOTE TALLY:\n{format_tally(blind_tally, labels)}\n\n"
            f"LABELS:\n{_label_lines(labels)}\n\n"
            "End your response with exactly these two lines:\n"
            "VERDICT: <one label>\n"
            "CONFIDENCE: <1-5>"
        )

    return _run_vote_jobs(
        models=models,
        labels=labels,
        t=t,
        round_name="revote",
        prompt_for=prompt_for,
        max_workers=max_workers,
        extra_meta={"blind_tally": dict(blind_tally)},
    )


def _run_vote_jobs(
    models: Sequence[Model],
    labels: Sequence[str],
    t: Transcript,
    round_name: str,
    prompt_for,
    max_workers: Optional[int] = None,
    extra_meta: dict | None = None,
) -> VoteRoundResult:
    labels = normalize_labels(labels)
    workers = _worker_count(len(models), max_workers)
    if workers == 0:
        return VoteRoundResult(votes={}, confidences={})

    def complete(model: Model) -> tuple[str, str, str, dict]:
        prompt = prompt_for(model)
        response = model.complete(prompt)
        retry_used = False
        initial_response = response
        try:
            vote, confidence = parse_vote(response, labels)
        except VoteParseError as exc:
            retry_used = True
            retry_prompt = _retry_prompt(labels, response, str(exc))
            response = model.complete(retry_prompt)
            prompt = retry_prompt
            try:
                vote, confidence = parse_vote(response, labels)
            except VoteParseError as retry_exc:
                vote, confidence = None, None
                parse_error = str(retry_exc)
            else:
                parse_error = None
        else:
            parse_error = None

        meta = {
            "labels": list(labels),
            "vote": vote,
            "confidence": confidence,
            "retry_used": retry_used,
        }
        if retry_used:
            meta["initial_response"] = initial_response
        if parse_error is not None:
            meta["parse_error"] = parse_error
        if extra_meta:
            meta.update(extra_meta)
        return model.name, prompt, response, meta

    if workers == 1:
        rows = [complete(model) for model in models]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            rows = list(executor.map(complete, models))

    votes: dict[str, str | None] = {}
    confidences: dict[str, int | None] = {}
    for name, prompt, response, meta in rows:
        votes[name] = meta["vote"]
        confidences[name] = meta["confidence"]
        t.record(round_name, name, prompt, response, meta=meta)
    return VoteRoundResult(votes=votes, confidences=confidences)


def _label_lines(labels: Sequence[str]) -> str:
    return "\n".join(f"- {label}" for label in labels)


def _retry_prompt(labels: Sequence[str], bad_response: str, error: str) -> str:
    return (
        "Your previous vote response could not be parsed.\n\n"
        f"Parse error: {error}\n\n"
        f"Previous response:\n{bad_response}\n\n"
        f"Allowed labels:\n{_label_lines(labels)}\n\n"
        "Return only these two lines, with one allowed label and a confidence "
        "integer from 1 to 5:\n"
        "VERDICT: <one label>\n"
        "CONFIDENCE: <1-5>"
    )


def _worker_count(model_count: int, max_workers: Optional[int]) -> int:
    if model_count == 0:
        return 0
    if max_workers is not None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        return min(max_workers, model_count)
    return min(32, model_count)
