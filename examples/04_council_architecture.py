"""Build an offline council architecture report from synthetic records.

    python examples/04_council_architecture.py

The record bank mimics EchoModel-style scripted votes: clones, a weaker but
less redundant member, a contrarian, an adversarial flipper, and partial truth
coverage. Reports are written to ./reports/.
"""

from pathlib import Path

from quorum import (
    Council,
    EchoModel,
    VoteRecord,
    build_roster,
    council_health_html,
    dawid_skene,
    drop_one,
    save,
    transcript_html,
)


LABELS = ["alpha", "beta"]
MEMBERS = ["atlas", "atlas_clone", "nova", "contrarian", "flipper"]


def opposite(label: str) -> str:
    return "beta" if label == "alpha" else "alpha"


def synthetic_records() -> list[VoteRecord]:
    records = []
    for index in range(30):
        latent = "alpha" if index % 2 == 0 else "beta"
        atlas = opposite(latent) if index in {7, 17, 26} else latent
        nova = opposite(latent) if index % 5 == 0 else latent
        contrarian = opposite(latent) if index % 3 == 0 else latent
        votes = {
            "atlas": atlas,
            "atlas_clone": atlas,
            "nova": nova,
            "contrarian": contrarian,
            "flipper": opposite(latent),
        }
        records.append(
            VoteRecord(
                question_id=f"synthetic-{index + 1:02d}",
                question=f"Synthetic architecture probe {index + 1}",
                labels=LABELS,
                votes=votes,
                confidences={member: 4 for member in MEMBERS},
                truth=latent if index < 20 else None,
            )
        )
    return records


def sample_transcript_report():
    members = [
        EchoModel("atlas", "Alpha is the best fit.\n\nVERDICT: alpha\nCONFIDENCE: 4"),
        EchoModel("nova", "Beta has one edge case.\n\nVERDICT: beta\nCONFIDENCE: 3"),
        EchoModel("flipper", "I object with beta.\n\nVERDICT: beta\nCONFIDENCE: 4"),
    ]
    skeptic = EchoModel("skeptic", "The alpha case depends on an unstated prior.")
    synthesizer = EchoModel("synth", "Final: alpha, with the prior made explicit.")
    return Council(members, skeptic=skeptic, synthesizer=synthesizer, seed=11).ask(
        "Which label should the architecture choose?",
        labels=LABELS,
        revote=True,
    )


def main() -> None:
    records = synthetic_records()
    ds = dawid_skene(records)
    roster = build_roster(records, MEMBERS, size=3, lam=0.5)
    deltas = drop_one(records, MEMBERS)
    verdict = sample_transcript_report()

    report_dir = Path("reports")
    health_path = save(
        council_health_html(records, roster=roster),
        report_dir / "council_health.html",
    )
    transcript_path = save(
        transcript_html(verdict.transcript, verdict=verdict),
        report_dir / "transcript.html",
    )

    print(f"Roster picks: {', '.join(roster.picks)}")
    print("Dawid-Skene skill:")
    for member in MEMBERS:
        print(f"  {member}: {ds.skill.get(member, 0):.3f}")
    print("Drop-one deltas:")
    for member in MEMBERS:
        delta = deltas[member]
        print(f"  {member}: {delta:+.1%}" if delta is not None else f"  {member}: n/a")
    print(f"Health report: {health_path}")
    print(f"Transcript report: {transcript_path}")


if __name__ == "__main__":
    main()
