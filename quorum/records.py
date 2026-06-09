"""Append-only vote records for council measurement."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence


@dataclass
class VoteRecord:
    question_id: str
    question: str
    labels: list[str]
    votes: dict[str, str | None]
    confidences: dict[str, int | None]
    truth: str | None = None
    revotes: dict[str, str | None] = field(default_factory=dict)
    revote_confidences: dict[str, int | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict) -> "VoteRecord":
        return cls(
            question_id=str(data["question_id"]),
            question=str(data["question"]),
            labels=[str(label) for label in data["labels"]],
            votes={str(k): v for k, v in data["votes"].items()},
            confidences={str(k): v for k, v in data["confidences"].items()},
            truth=data.get("truth"),
            revotes={str(k): v for k, v in data.get("revotes", {}).items()},
            revote_confidences={
                str(k): v for k, v in data.get("revote_confidences", {}).items()
            },
        )

    @classmethod
    def from_json(cls, text: str) -> "VoteRecord":
        return cls.from_dict(json.loads(text))


class RecordStore:
    """Append-only JSONL store for labeled council decisions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def add(self, record: VoteRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(record.to_json())
            fh.write("\n")

    def load(self) -> list[VoteRecord]:
        if not self.path.exists():
            return []
        records: list[VoteRecord] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(VoteRecord.from_json(line))
        return records

    def votes_by_item(self, use_revotes: bool = False) -> list[dict[str, str | None]]:
        rows = []
        for record in self.load():
            if use_revotes and record.revotes:
                rows.append(record.revotes)
            else:
                rows.append(record.votes)
        return rows


def records_to_votes(records: Sequence[VoteRecord], use_revotes: bool = False) -> list[dict[str, str | None]]:
    return [
        record.revotes if use_revotes and record.revotes else record.votes
        for record in records
    ]
