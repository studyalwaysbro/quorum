"""Command-line interface for Quorum."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from quorum.adapters import CLIModel
from quorum.agreement import summary
from quorum.council import Council
from quorum.records import RecordStore, records_to_votes
from quorum.report import council_health_html, save, transcript_html
from quorum.roster import build_roster, member_accuracy
from quorum.transcript import Transcript
from quorum.votes import normalize_labels


def main(argv: list[str] | None = None, *, model_factory=CLIModel) -> int:
    try:
        args = _parser().parse_args(argv)
        return args.func(args, model_factory)
    except SystemExit as exc:
        return int(exc.code or 0)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"quorum: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quorum", description="Structured multi-model councils"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="ask a council")
    ask.add_argument("question")
    ask.add_argument("--member", action="append", default=[])
    for flag in (
        "--skeptic",
        "--synthesizer",
        "--labels",
        "--store",
        "--question-id",
        "--truth",
        "--html",
        "--json",
    ):
        ask.add_argument(flag)
    for flag in ("--revote", "--quiet"):
        ask.add_argument(flag, action="store_true")
    ask.add_argument("--seed", type=int, default=0)
    ask.add_argument("--timeout", type=float, default=600)
    ask.set_defaults(func=_ask)

    health = sub.add_parser("health", help="summarize a vote store")
    health.add_argument("store")
    health.add_argument("--html")
    health.add_argument("--roster-size", type=int)
    health.add_argument("--lam", type=float, default=0.5)
    health.set_defaults(func=_health)

    replay = sub.add_parser("replay", help="replay a transcript JSON file")
    replay.add_argument("transcript")
    replay.add_argument("--html")
    replay.set_defaults(func=_replay)

    auth = sub.add_parser("auth", help="inspect model availability & health")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    doctor = auth_sub.add_parser("doctor", help="audition local-CLI models")
    doctor.add_argument("--timeout", type=float, default=45)
    doctor.set_defaults(func=_auth_doctor)
    return parser


def _ask(args, model_factory) -> int:
    if not args.member:
        raise ValueError("at least one --member is required")
    members = [_model(spec, args.timeout, model_factory) for spec in args.member]
    skeptic = _model(args.skeptic, args.timeout, model_factory) if args.skeptic else None
    synthesizer = _by_name(members, args.synthesizer) if args.synthesizer else None
    labels = normalize_labels(args.labels.split(",")) if args.labels else None
    store = RecordStore(args.store) if args.store else None

    council = Council(
        members,
        skeptic=skeptic,
        synthesizer=synthesizer,
        seed=args.seed,
        store=store,
    )
    verdict = council.ask(
        args.question,
        labels=labels,
        revote=args.revote,
        truth=args.truth,
        question_id=args.question_id,
    )

    if args.json:
        save(verdict.transcript.to_json(), args.json)
    if args.html:
        save(transcript_html(verdict.transcript, verdict=verdict), args.html)

    print(verdict.answer)
    if labels and not args.quiet:
        print(_vote_block(verdict, labels), file=sys.stderr)
    return 0


def _health(args, _model_factory) -> int:
    path = Path(args.store)
    if not path.exists():
        raise ValueError(f"store not found: {path}")
    records = RecordStore(path).load()
    labels = _record_labels(records)
    stats = summary(records_to_votes(records), labels=labels or None)
    accuracies = member_accuracy(records)
    roster = None
    if args.roster_size is not None:
        roster = build_roster(records, stats.members, args.roster_size, lam=args.lam)
    if args.html:
        save(council_health_html(records, summary=stats, roster=roster), args.html)
    print(_health_text(stats, accuracies, roster))
    return 0


def _replay(args, _model_factory) -> int:
    path = Path(args.transcript)
    if not path.exists():
        raise ValueError(f"transcript not found: {path}")
    transcript = Transcript.from_json(path.read_text(encoding="utf-8"))
    if args.html:
        save(transcript_html(transcript), args.html)
    else:
        print(_plain_replay(transcript))
    return 0


def _auth_doctor(args, _model_factory) -> int:
    from quorum.providers import LOCAL_CATALOG, probe

    print("Local model audition (catalog + audition gate — same engine the UI uses):\n")
    for spec in LOCAL_CATALOG:
        if not spec.available:
            print(f"  -  {spec.id:<10} not installed ({spec.binary} not on PATH)")
            continue
        result = probe(spec, timeout=args.timeout)
        mark = "OK " if result.ok else "XX "
        lat = f"  [{result.latency_s}s]" if result.latency_s is not None else ""
        flag = " (agentic)" if spec.agentic else ""
        print(f"  {mark}{spec.id:<10} {result.reason}{lat}{flag}")
    print("\nXX = quarantined; not offered to a council until it passes.")
    return 0


def _model(spec: str, timeout: float, factory):
    if "=" not in spec:
        raise ValueError("member specs must look like name=argv")
    name, raw_argv = spec.split("=", 1)
    name = name.strip()
    if not name:
        raise ValueError("member name must be non-empty")
    try:
        argv = shlex.split(raw_argv)
    except ValueError as exc:
        raise ValueError(f"bad argv for {name}: {exc}") from exc
    if not argv:
        raise ValueError(f"{name} argv must be non-empty")
    return factory(name, argv, timeout)


def _by_name(models, name: str):
    for model in models:
        if model.name == name:
            return model
    raise ValueError(f"unknown synthesizer: {name}")


def _vote_block(verdict, labels: list[str]) -> str:
    lines = ["tally:"]
    lines.extend(f"- {label}: {verdict.tally.get(label, 0)}" for label in labels)
    lines.append(f"majority: {verdict.majority or 'none'}")
    if verdict.flips:
        lines.append("flips:")
        lines.extend(
            f"- {name}: {old or 'none'} -> {new or 'none'}"
            for name, (old, new) in verdict.flips.items()
        )
    else:
        lines.append("flips: none")
    return "\n".join(lines)


def _health_text(stats, accuracies, roster) -> str:
    lines = [
        f"items: {stats.n_items}",
        f"members: {stats.n_members}",
        f"raw: {_num(stats.raw_agreement)}",
        f"fleiss: {_num(stats.fleiss_kappa)}",
        f"ac1: {_num(stats.gwet_ac1)}",
        f"alpha: {_num(stats.krippendorff_alpha_nominal)}",
        f"n_effective: {stats.n_effective:.1f}",
        "members:",
    ]
    if not stats.members:
        lines.append("- none")
    for member in stats.members:
        line = f"- {member}: redundancy={_num(stats.redundancy.get(member))}"
        if member in accuracies:
            acc = accuracies[member]
            line += f" accuracy={_pct(acc.accuracy)} n={acc.n}"
        lines.append(line)
    if roster is not None:
        picks = ", ".join(roster.picks) if roster.picks else "none"
        lines.append(f"roster ({roster.accuracy_source}): {picks}")
    return "\n".join(lines)


def _plain_replay(transcript: Transcript) -> str:
    lines = [f"question: {transcript.question}"]
    for turn in transcript.turns:
        lines.extend(["", f"[{turn.round}] {turn.model}", turn.response])
    return "\n".join(lines)


def _record_labels(records) -> list[str]:
    labels = {}
    for record in records:
        for label in record.labels:
            labels.setdefault(label.casefold(), label)
    return list(labels.values())


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
