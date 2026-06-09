"""Compose rounds into a deliberation protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from quorum.adapters import Model
from quorum.rounds import (
    adversarial_round,
    blind_round,
    consensus_map_round,
    critique_round,
    synthesis_round,
)
from quorum.transcript import Transcript


@dataclass
class Verdict:
    answer: str
    transcript: Transcript


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
    ) -> None:
        if not members:
            raise ValueError("a council needs at least one member")
        if max_workers is not None and max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self.members = list(members)
        self.skeptic = skeptic
        self.synthesizer = synthesizer or self.members[0]
        self.distiller = distiller
        self.max_workers = max_workers

    def ask(self, question: str) -> Verdict:
        t = Transcript(question=question)
        blind_round(self.members, question, t, max_workers=self.max_workers)
        if len(self.members) > 1:
            critique_round(self.members, question, t, max_workers=self.max_workers)
            consensus_map_round(question, t, distiller=self.distiller)
        if self.skeptic is not None:
            adversarial_round(self.skeptic, question, t)
        answer = synthesis_round(self.synthesizer, question, t)
        return Verdict(answer=answer, transcript=t)
