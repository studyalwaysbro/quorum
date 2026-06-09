"""Compose rounds into a deliberation protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from quorum.adapters import Model
from quorum.rounds import (
    adversarial_round,
    blind_round,
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
    ):
        if not members:
            raise ValueError("a council needs at least one member")
        self.members = list(members)
        self.skeptic = skeptic
        self.synthesizer = synthesizer or self.members[0]

    def ask(self, question: str) -> Verdict:
        t = Transcript(question=question)
        blind_round(self.members, question, t)
        if len(self.members) > 1:
            critique_round(self.members, question, t)
        if self.skeptic is not None:
            adversarial_round(self.skeptic, question, t)
        answer = synthesis_round(self.synthesizer, question, t)
        return Verdict(answer=answer, transcript=t)
