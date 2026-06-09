"""Compose rounds into a deliberation protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence
from uuid import uuid4

from quorum.adapters import Model
from quorum.records import RecordStore, VoteRecord
from quorum.rounds import (
    adversarial_round,
    blind_round,
    consensus_map_round,
    critique_round,
    synthesis_round,
)
from quorum.transcript import Transcript
from quorum.votes import (
    majority_label,
    normalize_labels,
    revote_round,
    tally_votes,
    vote_round,
)


@dataclass
class Verdict:
    answer: str
    transcript: Transcript
    votes: dict[str, str | None] = field(default_factory=dict)
    confidences: dict[str, int | None] = field(default_factory=dict)
    tally: dict[str, int] = field(default_factory=dict)
    majority: str | None = None
    revotes: dict[str, str | None] = field(default_factory=dict)
    revote_confidences: dict[str, int | None] = field(default_factory=dict)
    flips: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class Council:
    """A panel of models that deliberates instead of just voting.

    Example::

        council = Council(
            members=[deepseek, gemini, gpt],
            skeptic=grok,            # adversarial-only by design
            synthesizer=deepseek,    # defaults to the first member
            distiller=None,          # optional semantic consensus mapper
            max_workers=None,        # defaults to ordered parallel fan-out
        )
        verdict = council.ask("Which sort fits a nearly-sorted 10k-element list?")
        print(verdict.answer)
        print(verdict.transcript.to_json())
    """

    def __init__(
        self,
        members: Sequence[Model],
        skeptic: Optional[Model] = None,
        synthesizer: Optional[Model] = None,
        distiller: Optional[Model] = None,
        max_workers: Optional[int] = None,
        seed: int = 0,
        store: RecordStore | None = None,
    ) -> None:
        if not members:
            raise ValueError("a council needs at least one member")
        if max_workers is not None and max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        names = [member.name for member in members]
        if len(set(names)) != len(names):
            raise ValueError("council member names must be unique")
        self.members = list(members)
        self.skeptic = skeptic
        self.synthesizer = synthesizer or self.members[0]
        self.distiller = distiller
        self.max_workers = max_workers
        self.seed = seed
        self.store = store

    def ask(
        self,
        question: str,
        labels: Sequence[str] | None = None,
        *,
        revote: bool = False,
        truth: str | None = None,
        question_id: str | None = None,
    ) -> Verdict:
        if revote and labels is None:
            raise ValueError("revote requires labels")
        normalized_labels = normalize_labels(labels) if labels is not None else None
        if truth is not None and normalized_labels is not None and truth not in normalized_labels:
            raise ValueError("truth must be one of labels")

        t = Transcript(question=question)
        vote_result = None
        revote_result = None
        tally: dict[str, int] = {}
        majority = None

        blind_round(self.members, question, t, max_workers=self.max_workers)
        if normalized_labels is not None:
            vote_result = vote_round(
                self.members,
                question,
                normalized_labels,
                t,
                max_workers=self.max_workers,
            )
            tally = tally_votes(vote_result.votes, normalized_labels)
            majority = majority_label(tally, normalized_labels)
        if len(self.members) > 1:
            critique_round(
                self.members,
                question,
                t,
                max_workers=self.max_workers,
                seed=self.seed,
            )
            consensus_map_round(question, t, distiller=self.distiller)
        if self.skeptic is not None:
            adversarial_round(self.skeptic, question, t, seed=self.seed)
        if normalized_labels is not None and revote:
            revote_result = revote_round(
                self.members,
                question,
                normalized_labels,
                t,
                vote_result.votes,
                vote_result.confidences,
                tally,
                max_workers=self.max_workers,
            )
        answer = synthesis_round(self.synthesizer, question, t)
        votes = vote_result.votes if vote_result is not None else {}
        confidences = vote_result.confidences if vote_result is not None else {}
        revotes = revote_result.votes if revote_result is not None else {}
        revote_confidences = (
            revote_result.confidences if revote_result is not None else {}
        )
        flips = {
            name: (votes.get(name), revotes.get(name))
            for name in votes
            if name in revotes and votes.get(name) != revotes.get(name)
        }
        notes = self._notes()

        if normalized_labels is not None and self.store is not None:
            self.store.add(
                VoteRecord(
                    question_id=question_id or uuid4().hex,
                    question=question,
                    labels=list(normalized_labels),
                    votes=dict(votes),
                    confidences=dict(confidences),
                    truth=truth,
                    revotes=dict(revotes),
                    revote_confidences=dict(revote_confidences),
                )
            )

        return Verdict(
            answer=answer,
            transcript=t,
            votes=votes,
            confidences=confidences,
            tally=tally,
            majority=majority,
            revotes=revotes,
            revote_confidences=revote_confidences,
            flips=flips,
            notes=notes,
        )

    def _notes(self) -> list[str]:
        if any(member.name == self.synthesizer.name for member in self.members):
            return [
                "Synthesizer is also a council member; prefer a non-member "
                "synthesizer or the strongest available model when possible "
                "to reduce self-preference bias."
            ]
        return []
