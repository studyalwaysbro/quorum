"""Command-line interface for Quorum."""

from __future__ import annotations

import argparse
import inspect
import json
import os
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
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
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
        "--persona",
    ):
        ask.add_argument(flag)
    for flag in ("--revote", "--quiet", "--allow-remote-egress"):
        ask.add_argument(flag, action="store_true")
    ask.add_argument("--seed", type=int, default=0)
    ask.add_argument("--timeout", type=float, default=600)
    ask.set_defaults(func=_ask)

    research = sub.add_parser(
        "research", help="analyze local attachments with stateless, tool-free APIs"
    )
    research.add_argument("question")
    research.add_argument("--file", action="append", default=[], dest="files")
    research.add_argument(
        "--provider", action="append", required=True,
        help="installed audited provider profile id (repeatable)",
    )
    research.add_argument("--prepare", metavar="MANIFEST", help="extract/redact locally and write a review manifest; no provider call")
    research.add_argument("--manifest", help="reviewed manifest to execute")
    research.add_argument("--approve", metavar="SHA256", help="exact hash printed by --prepare after review")
    research.add_argument(
        "--allow-sensitive", action="store_true",
        help="send detected secrets without automatic redaction (dangerous, per-run only)",
    )
    research.add_argument(
        "--model", action="append", default=[], metavar="PROVIDER=MODEL",
        help="override a provider's allowlisted default model",
    )
    research.add_argument("--json", help="write the attachment-safe Claim Ledger JSON (mode 0600)")
    research.add_argument("--timeout", type=float, default=120)
    research.set_defaults(func=_research)

    provider = sub.add_parser("provider", help="manage declarative remote model profiles")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)
    provider_list = provider_sub.add_parser("list", help="list safe provider metadata")
    provider_list.set_defaults(func=_provider_list)
    provider_path = provider_sub.add_parser("path", help="print the local profile config path")
    provider_path.set_defaults(func=_provider_path)
    provider_add = provider_sub.add_parser("add", help="install a model profile on an audited adapter")
    provider_add.add_argument("profile_id")
    provider_add.add_argument("--provider", required=True,
                              choices=("openai", "deepseek", "xai", "kimi", "zai", "openrouter", "ollama"))
    provider_add.add_argument("--model", required=True)
    provider_add.add_argument("--label")
    provider_add.add_argument("--reasoning", default="provider_default",
                              choices=("provider_default", "none", "high", "max", "xhigh"))
    provider_add.add_argument("--upstream",
                              help="required exact OpenRouter upstream slug; forbidden otherwise")
    provider_add.set_defaults(func=_provider_add)
    provider_remove = provider_sub.add_parser("remove", help="remove a user-installed profile")
    provider_remove.add_argument("profile_id")
    provider_remove.set_defaults(func=_provider_remove)

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
    remote_specs = [
        spec for spec in [*args.member, *([args.skeptic] if args.skeptic else [])]
        if spec.startswith("remote:")
    ]
    if remote_specs and not args.allow_remote_egress:
        raise ValueError(
            "remote profiles require --allow-remote-egress; the question and council outputs "
            "will be shared across the selected providers"
        )
    from quorum.providers.remote import (
        annotate_remote_transcript, build_remote_model_from_profile,
        get_provider_profile, provider_snapshot,
    )
    resolved = {
        _remote_profile_id(spec): get_provider_profile(_remote_profile_id(spec))
        for spec in remote_specs
    }
    members = [
        build_remote_model_from_profile(resolved[_remote_profile_id(spec)], timeout=args.timeout)
        if spec.startswith("remote:") else _model(spec, args.timeout, model_factory)
        for spec in args.member
    ]
    skeptic = (
        build_remote_model_from_profile(resolved[_remote_profile_id(args.skeptic)], timeout=args.timeout)
        if args.skeptic and args.skeptic.startswith("remote:")
        else _model(args.skeptic, args.timeout, model_factory) if args.skeptic else None
    )
    synthesizer = _by_name(members, args.synthesizer) if args.synthesizer else None
    labels = normalize_labels(args.labels.split(",")) if args.labels else None
    remote_models = []
    remote_snapshots = []
    selected_synthesizer = synthesizer.name if synthesizer is not None else members[0].name
    multi = len(members) > 1
    has_labels = labels is not None
    for index, (raw, model) in enumerate(zip(args.member, members)):
        if raw.startswith("remote:"):
            receives = ["blind"]
            if has_labels:
                receives.append("vote")
            if multi:
                receives.append("critique")
            if has_labels and args.revote:
                receives.append("revote")
            if model.name == selected_synthesizer:
                receives.append("synthesis")
            remote_models.append(model)
            remote_snapshots.append(provider_snapshot(
                resolved[_remote_profile_id(raw)], receives=tuple(receives),
            ))
    if args.skeptic and args.skeptic.startswith("remote:"):
        remote_models.append(skeptic)
        remote_snapshots.append(provider_snapshot(
            resolved[_remote_profile_id(args.skeptic)], receives=("adversarial",),
        ))
    if remote_snapshots:
        print("quorum: approved remote egress receipt", file=sys.stderr)
        print(json.dumps(remote_snapshots, ensure_ascii=False), file=sys.stderr)
    store = RecordStore(args.store) if args.store else None

    council = Council(
        members,
        skeptic=skeptic,
        synthesizer=synthesizer,
        seed=args.seed,
        store=store,
        adversary_persona=args.persona,
    )
    verdict = council.ask(
        args.question,
        labels=labels,
        revote=args.revote,
        truth=args.truth,
        question_id=args.question_id,
    )
    if remote_models:
        annotate_remote_transcript(verdict.transcript, remote_models, remote_snapshots)

    if args.json:
        save(verdict.transcript.to_json(), args.json)
    if args.html:
        save(transcript_html(verdict.transcript, verdict=verdict), args.html)

    print(verdict.answer)
    if labels and not args.quiet:
        print(_vote_block(verdict, labels), file=sys.stderr)
    return 0


def _research(args, _model_factory) -> int:
    """Attachment analysis that never invokes a local/tool-capable CLI.

    Extracted source text is redacted locally by default, then sent only to
    fixed-host stateless API adapters. The output is a Claim Ledger, not a raw
    transcript, so source-bearing prompts are not persisted.
    """
    if not args.question.strip():
        raise ValueError("research question must be non-empty")

    from quorum.providers.remote import (
        build_remote_model_from_profile, ensure_attachment_eligible,
        get_provider_profile, provider_snapshot,
    )
    from quorum.research.attachments import (
        combine_chunks,
        ingest_attachment_paths,
        read_attachment_path,
        redact_text,
        scan_advisories,
    )
    from quorum.research.manifest import (
        build_manifest,
        manifest_chunks,
        manifest_preview,
        validate_manifest,
    )
    from quorum.research.pipeline import run_research
    from quorum.research.schema import SourceChunk

    providers = list(dict.fromkeys(args.provider))
    if len(providers) != len(args.provider):
        raise ValueError("duplicate --provider values are not allowed")
    profiles = []
    for provider in providers:
        profile = get_provider_profile(provider)
        ensure_attachment_eligible(profile)
        profiles.append(profile)  # fail before reading any file or key
    model_overrides = _provider_models(args.model, providers)

    provider_manifest = [
        provider_snapshot(
            profile, model=model_overrides.get(profile.id),
            receives=("grounded_blind", "fact_check") if index == 0 else ("grounded_blind",),
        )
        for index, profile in enumerate(profiles)
    ]

    if args.prepare:
        if not args.files or args.manifest or args.approve:
            raise ValueError("--prepare requires --file and cannot be combined with --manifest/--approve")
        results = ingest_attachment_paths(args.files)
        question_findings = scan_advisories(args.question)
        secret_findings = sum(
            advisory.kind != "prompt_injection_language"
            for result in results for advisory in result.advisories
        ) + sum(a.kind != "prompt_injection_language" for a in question_findings)
        injection_hints = sum(
            advisory.kind == "prompt_injection_language"
            for result in results for advisory in result.advisories
        ) + sum(a.kind == "prompt_injection_language" for a in question_findings)
        raw_chunks = combine_chunks(results)
        chunks = raw_chunks if args.allow_sensitive else [
            SourceChunk(chunk.id, redact_text(chunk.text), chunk.source) for chunk in raw_chunks
        ]
        question = args.question if args.allow_sensitive else redact_text(args.question, question_findings)
        manifest = build_manifest(
            question, chunks, provider_manifest, files=len(results),
            secret_findings=secret_findings, injection_hints=injection_hints,
        )
        _write_private(args.prepare, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        summary = {
            key: manifest[key] for key in (
                "manifest_hash", "providers", "files", "secret_findings_redacted", "injection_hints"
            )
        }
        summary["chunks"] = len(manifest["chunks"])
        summary["preview"] = manifest_preview(manifest)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        print("quorum: no content was sent; review the manifest and preview before approval", file=sys.stderr)
        return 0

    if args.files or args.prepare or not args.manifest or not args.approve:
        raise ValueError("execution requires --manifest FILE and --approve SHA256, with no --file")
    _, manifest_bytes = read_attachment_path(args.manifest, max_bytes=1_500_000)
    manifest = validate_manifest(json.loads(manifest_bytes.decode("utf-8")))
    if args.approve != manifest["manifest_hash"]:
        raise ValueError("--approve does not match the reviewed manifest hash")
    if manifest["question"] != args.question or manifest["providers"] != provider_manifest:
        raise ValueError("question/provider/model selection does not match the reviewed manifest")
    chunks = manifest_chunks(manifest)
    question = manifest["question"]
    print(f"quorum: approved manifest {manifest['manifest_hash']} for {', '.join(providers)}", file=sys.stderr)
    members = [
        build_remote_model_from_profile(
            profile, model=model_overrides.get(profile.id), timeout=args.timeout
        )
        for profile in profiles
    ]
    verdict = run_research(members, question, chunks)
    rendered = json.dumps(verdict.ledger.to_dict(), indent=2, ensure_ascii=False)
    if args.json:
        _write_private(args.json, rendered + "\n")
    print(rendered)
    return 0


def _provider_models(values: list[str], providers: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--model must look like PROVIDER=MODEL")
        provider, model = (part.strip() for part in value.split("=", 1))
        if provider not in providers:
            raise ValueError(f"--model provider was not selected: {provider}")
        if not model or provider in overrides:
            raise ValueError(f"invalid or duplicate --model override for {provider}")
        overrides[provider] = model
    return overrides


def _provider_list(args, _model_factory) -> int:
    from quorum.providers.remote import remote_capabilities

    print(json.dumps(remote_capabilities(), indent=2, ensure_ascii=False))
    return 0


def _provider_path(args, _model_factory) -> int:
    from quorum.providers.profiles import config_path

    print(config_path())
    return 0


def _provider_add(args, _model_factory) -> int:
    from quorum.providers.profiles import add_user_profile, make_user_profile
    from quorum.providers.remote import get_provider_profile, get_remote_spec

    get_remote_spec(args.provider)
    try:
        get_provider_profile(args.profile_id)
    except KeyError:
        pass
    else:
        raise ValueError(f"provider profile already exists: {args.profile_id}")
    profile = make_user_profile(
        args.profile_id, args.provider, args.model, label=args.label,
        reasoning=args.reasoning, upstream=args.upstream,
    )
    target = add_user_profile(profile)
    print(json.dumps({
        "installed": profile.id, "provider": profile.provider_id,
        "model": profile.model, "config": str(target),
        "credential_stored": False,
    }, indent=2))
    return 0


def _provider_remove(args, _model_factory) -> int:
    from quorum.providers.remote import builtin_profiles
    from quorum.providers.profiles import remove_user_profile

    if args.profile_id in {profile.id for profile in builtin_profiles()}:
        raise ValueError("built-in provider profiles cannot be removed")
    target = remove_user_profile(args.profile_id)
    print(json.dumps({"removed": args.profile_id, "config": str(target)}, indent=2))
    return 0


def _write_private(path: str, text: str) -> None:
    target = Path(path)
    if target.exists() or target.is_symlink():
        info = target.lstat()
        if not target.is_file() or target.is_symlink():
            raise ValueError(f"refusing non-regular or symlink output: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp-{os.getpid()}-{os.urandom(6).hex()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


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
    if spec.startswith("remote:"):
        raise ValueError("remote profiles must use the immutable council resolution path")
    if "=" not in spec:
        return _catalog_model(spec, timeout, factory)

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
    prompt_transport = _catalog_prompt_transport(argv)
    return _call_model_factory(factory, name, argv, timeout, prompt_transport)


def _remote_profile_id(spec: str) -> str:
    profile_id = spec.removeprefix("remote:").strip()
    if not profile_id:
        raise ValueError("remote member must look like remote:PROFILE")
    return profile_id


def _catalog_model(model_id: str, timeout: float, factory):
    from quorum.providers import get_spec

    model_id = model_id.strip()
    if not model_id:
        raise ValueError("member specs must look like name=argv or a catalog id")
    try:
        spec = get_spec(model_id)
    except KeyError as exc:
        raise ValueError(
            f"unknown catalog model id: {model_id}; use name=argv for custom commands"
        ) from exc
    return _call_model_factory(
        factory, spec.id, list(spec.command), timeout, spec.prompt_transport
    )


def _catalog_prompt_transport(argv: list[str]) -> str:
    from quorum.providers import LOCAL_CATALOG

    for spec in LOCAL_CATALOG:
        command = list(spec.command)
        if argv[: len(command)] == command:
            return spec.prompt_transport
    return "stdin"


def _call_model_factory(factory, name: str, argv: list[str], timeout: float, prompt_transport: str):
    params = inspect.signature(factory).parameters
    accepts_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if "prompt_transport" in params or accepts_kw:
        return factory(name, argv, timeout, prompt_transport=prompt_transport)
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
        identity = turn.meta.get("remote_identity")
        if identity:
            lines.append(
                "identity: "
                f"{identity['provider']} / requested {identity['requested_model']} / "
                f"provider reported {identity['provider_reported_model']} / unverified"
            )
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
