"""Run labeled council asks into a RecordStore and print agreement stats.

    python examples/03_measured_council.py

This stays offline: EchoModel replies are fixed strings that include the
required vote block. Use a durable RecordStore path when you want records to
accumulate across sessions.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from quorum import Council, EchoModel, RecordStore, summary


LABELS = ["insertion", "timsort"]


def voter(name: str, label: str, confidence: int) -> EchoModel:
    return EchoModel(name, f"VERDICT: {label}\nCONFIDENCE: {confidence}")


questions = [
    (
        "Best sort for a nearly-sorted list of 10,000 elements?",
        [
            voter("alice", "insertion", 4),
            voter("bob", "timsort", 5),
            voter("carol", "timsort", 4),
        ],
    ),
    (
        "Best Python default for partially ordered real-world records?",
        [
            voter("alice", "timsort", 5),
            voter("bob", "timsort", 5),
            voter("carol", "timsort", 4),
        ],
    ),
]


with TemporaryDirectory() as tmp:
    store = RecordStore(Path(tmp) / "quorum-votes.jsonl")
    synthesizer = EchoModel("synthesizer", "Use Timsort unless constraints say otherwise.")

    for idx, (question, members) in enumerate(questions, start=1):
        verdict = Council(members, synthesizer=synthesizer, store=store).ask(
            question,
            labels=LABELS,
            question_id=f"demo-{idx}",
        )
        print(f"{verdict.majority=}, {verdict.tally=}")

    report = summary(store.votes_by_item(), labels=LABELS)
    print("\nAGREEMENT SUMMARY")
    print("-----------------")
    print(f"raw_agreement={report.raw_agreement:.3f}")
    print(f"fleiss_kappa={report.fleiss_kappa:.3f}")
    print(f"gwet_ac1={report.gwet_ac1:.3f}")
    print(f"n_effective={report.n_effective:.3f}")
    print(f"redundancy={report.redundancy}")
