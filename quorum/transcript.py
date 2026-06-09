"""Replayable, structured record of a deliberation.

Naive multi-model tools throw away everything except the final answer. The
whole point of a council is the *disagreement* — so we keep every turn,
tagged by round and model, and make it serializable. A transcript you can
replay is a transcript you can audit.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class Turn:
    round: str          # "blind", "critique", "consensus_map", etc.
    model: str
    prompt: str
    response: str


@dataclass
class Transcript:
    question: str
    turns: list[Turn] = field(default_factory=list)

    def record(self, round: str, model: str, prompt: str, response: str) -> None:
        self.turns.append(Turn(round, model, prompt, response))

    def by_round(self, round: str) -> list[Turn]:
        return [t for t in self.turns if t.round == round]

    def to_dict(self) -> dict:
        return {"question": self.question, "turns": [asdict(t) for t in self.turns]}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "Transcript":
        data = json.loads(text)
        t = cls(question=data["question"])
        t.turns = [Turn(**turn) for turn in data["turns"]]
        return t
