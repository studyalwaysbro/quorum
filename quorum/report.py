"""Static HTML report generators for transcripts and council health."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Mapping, Sequence

from quorum.agreement import AgreementSummary, summary as agreement_summary
from quorum.records import VoteRecord, records_to_votes
from quorum.roster import dawid_skene, drop_one, member_accuracy


def transcript_html(
    transcript,
    verdict=None,
    *,
    title: str = "Quorum transcript",
) -> str:
    """Render a self-contained deliberation replay."""
    if verdict is None and hasattr(transcript, "transcript"):
        verdict = transcript
        transcript = verdict.transcript

    votes, confidences = _vote_maps(transcript, verdict, "vote")
    revotes, revote_confidences = _vote_maps(transcript, verdict, "revote")
    flips = _flips(votes, revotes, verdict)

    sections = [
        _html_start(title),
        f"<header><h1>{_e(title)}</h1><p class=\"question\">{_e(transcript.question)}</p></header>",
        _blind_section(transcript, votes, confidences),
        _turn_section("Critiques", transcript.by_round("critique")),
        _turn_section("Consensus / Issue Map", transcript.by_round("consensus_map")),
        _turn_section(
            "Adversarial Objection",
            transcript.by_round("adversarial"),
            section_class="objection",
        ),
        _revote_section(transcript, votes, revotes, revote_confidences, flips),
        _turn_section("Synthesis", transcript.by_round("synthesis")),
        _permutation_notes(transcript),
        "</main></body></html>",
    ]
    return "\n".join(section for section in sections if section)


def council_health_html(
    records: Sequence[VoteRecord],
    summary: AgreementSummary | None = None,
    roster=None,
    *,
    title: str = "Quorum council health",
) -> str:
    """Render a self-contained architecture dashboard."""
    records = list(records)
    labels = _labels(records)
    summary = summary or agreement_summary(
        records_to_votes(records),
        labels=labels if labels else None,
    )
    accuracies = member_accuracy(records)
    ds = dawid_skene(records) if records and len(labels) >= 2 else None
    members = _health_members(summary, accuracies, ds.skill if ds else {}, roster)
    deltas = (
        drop_one(records, members)
        if any(record.truth is not None for record in records)
        else {member: None for member in members}
    )

    sections = [
        _html_start(title),
        f"<header><h1>{_e(title)}</h1><p class=\"question\">Architecture dashboard</p></header>",
        _warning(summary),
        _headline_cards(summary),
        _kappa_matrix(summary),
        _member_table(summary, members, accuracies, ds.skill if ds else {}, deltas, roster),
        _discussion(summary, accuracies, deltas),
        "</main></body></html>",
    ]
    return "\n".join(section for section in sections if section)


def save(html_text: str, path: str | Path) -> Path:
    """Write a report to disk and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")
    return path


def _html_start(title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>
:root {{
  color-scheme: light;
  --ink: #172026;
  --muted: #5b6770;
  --line: #d9e1e6;
  --panel: #f8fafb;
  --accent: #145c9e;
  --warn: #a4321f;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: #ffffff;
  color: var(--ink);
  font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
main {{ max-width: 1180px; margin: 0 auto; padding: 28px 18px 44px; }}
header {{ border-bottom: 1px solid var(--line); margin-bottom: 20px; padding-bottom: 14px; }}
h1 {{ font-size: 28px; margin: 0 0 8px; letter-spacing: 0; }}
h2 {{ font-size: 18px; margin: 24px 0 10px; letter-spacing: 0; }}
h3 {{ font-size: 15px; margin: 0 0 8px; letter-spacing: 0; }}
.question {{ margin: 0; color: var(--muted); white-space: pre-wrap; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }}
.card, .panel {{
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 12px;
}}
.pre {{
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
}}
.badges {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }}
.badge {{
  display: inline-block;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: #ffffff;
  color: var(--ink);
  padding: 2px 8px;
  font-size: 12px;
}}
.objection .panel {{ border-left: 4px solid var(--warn); background: #fff7f5; }}
.flip {{ border-left: 4px solid var(--accent); padding-left: 8px; font-weight: 650; }}
.footnotes {{ color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); margin-top: 24px; padding-top: 10px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; }}
.metric {{ font-size: 24px; font-weight: 700; }}
.hint {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
.warning {{ border: 1px solid #f0a99d; border-left: 4px solid var(--warn); background: #fff5f2; padding: 12px; border-radius: 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid var(--line); padding: 7px 8px; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ background: #eef3f6; }}
ul {{ margin-top: 8px; padding-left: 20px; }}
</style>
</head>
<body><main>"""


def _blind_section(transcript, votes, confidences) -> str:
    turns = transcript.by_round("blind")
    if not turns:
        return ""
    cards = []
    for turn in turns:
        badges = []
        if turn.meta.get("remote_identity"):
            badges.append(_badge(_identity_text(turn)))
        if turn.model in votes:
            badges.append(_badge(f"vote: {votes[turn.model]}"))
        if turn.model in confidences and confidences[turn.model] is not None:
            badges.append(_badge(f"confidence: {confidences[turn.model]}"))
        cards.append(
            "<article class=\"card\">"
            f"<h3>{_e(turn.model)}</h3>"
            f"<div class=\"badges\">{''.join(badges)}</div>"
            f"<div class=\"pre\">{_e(turn.response)}</div>"
            "</article>"
        )
    return f"<section><h2>Blind Answers</h2><div class=\"grid\">{''.join(cards)}</div></section>"


def _turn_section(title: str, turns, section_class: str = "") -> str:
    if not turns:
        return ""
    panels = []
    for turn in turns:
        identity = (
            f"<div class=\"badges\">{_badge(_identity_text(turn))}</div>"
            if turn.meta.get("remote_identity") else ""
        )
        panels.append(
            "<article class=\"panel\">"
            f"<h3>{_e(turn.model)}</h3>"
            f"{identity}"
            f"<div class=\"pre\">{_e(turn.response)}</div>"
            "</article>"
        )
    klass = f" class=\"{section_class}\"" if section_class else ""
    return f"<section{klass}><h2>{_e(title)}</h2>{''.join(panels)}</section>"


def _identity_text(turn) -> str:
    identity = turn.meta.get("remote_identity", {})
    route = identity.get("routing_requested")
    route_text = ""
    if route:
        only = ",".join(route.get("only", []))
        route_text = f"; upstream requested {only}; routing unverified"
    return (
        f"{identity.get('provider', 'remote')} / requested "
        f"{identity.get('requested_model', 'unknown')} / provider reported "
        f"{identity.get('provider_reported_model', 'unknown')} / identity unverified; "
        f"reasoning {identity.get('reasoning_requested', 'unknown')} requested/unverified"
        f"{route_text}"
    )


def _revote_section(transcript, votes, revotes, confidences, flips) -> str:
    turns = transcript.by_round("revote")
    if not turns and not revotes:
        return ""
    members = [turn.model for turn in turns] or sorted(revotes)
    items = []
    for member in members:
        old = votes.get(member)
        new = revotes.get(member)
        confidence = confidences.get(member)
        confidence_text = (
            f" <span class=\"badge\">confidence: {_e(confidence)}</span>"
            if confidence is not None
            else ""
        )
        if member in flips:
            items.append(
                "<li class=\"flip\">"
                f"{_e(member)}: {_e(old)} -&gt; {_e(new)}{confidence_text}"
                "</li>"
            )
        else:
            items.append(
                "<li>"
                f"{_e(member)}: {_e(new)}{confidence_text}"
                "</li>"
            )
    return f"<section><h2>Revotes</h2><ul>{''.join(items)}</ul></section>"


def _permutation_notes(transcript) -> str:
    notes = []
    for turn in transcript.turns:
        permutation = turn.meta.get("answer_permutation")
        if permutation:
            rendered = ",".join(str(item) for item in permutation)
            notes.append(f"answer order seen by {_e(turn.model)}: {_e(rendered)}")
    if not notes:
        return ""
    return (
        "<section class=\"footnotes\"><h2>Footnotes</h2>"
        + "".join(f"<p>{note}</p>" for note in notes)
        + "</section>"
    )


def _vote_maps(transcript, verdict, round_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if verdict is not None:
        vote_attr = "votes" if round_name == "vote" else "revotes"
        confidence_attr = "confidences" if round_name == "vote" else "revote_confidences"
        votes = dict(getattr(verdict, vote_attr, {}) or {})
        confidences = dict(getattr(verdict, confidence_attr, {}) or {})
        if votes or confidences:
            return votes, confidences

    votes = {}
    confidences = {}
    for turn in transcript.by_round(round_name):
        if "vote" in turn.meta:
            votes[turn.model] = turn.meta.get("vote")
        if "confidence" in turn.meta:
            confidences[turn.model] = turn.meta.get("confidence")
    return votes, confidences


def _flips(votes, revotes, verdict) -> dict[str, tuple[Any, Any]]:
    if verdict is not None and getattr(verdict, "flips", None):
        return dict(verdict.flips)
    return {
        member: (votes.get(member), revotes.get(member))
        for member in votes
        if member in revotes and votes.get(member) != revotes.get(member)
    }


def _warning(summary: AgreementSummary) -> str:
    if not summary.kappa_paradox_warning:
        return ""
    return (
        "<section class=\"warning\"><strong>Kappa paradox warning.</strong> "
        "Raw agreement is high while Fleiss kappa is low; inspect AC1 and "
        "label prevalence before treating the council as unreliable.</section>"
    )


def _headline_cards(summary: AgreementSummary) -> str:
    cards = [
        ("Items", str(summary.n_items), "vote records measured"),
        ("Members", str(summary.n_members), "members with at least one vote"),
        (
            "Raw agreement",
            _fmt_percent(summary.raw_agreement),
            _agreement_hint(summary.raw_agreement),
        ),
        ("Fleiss kappa", _fmt_number(summary.fleiss_kappa), _kappa_hint(summary.fleiss_kappa)),
        ("Gwet AC1", _fmt_number(summary.gwet_ac1), "prevalence-stable agreement check"),
        (
            "Alpha",
            _fmt_number(summary.krippendorff_alpha_nominal),
            "nominal Krippendorff alpha",
        ),
        (
            "n_effective",
            f"{summary.n_effective:.1f}",
            f"your {summary.n_members}-member council behaves like "
            f"{summary.n_effective:.1f} independent voters",
        ),
    ]
    rendered = "".join(
        "<article class=\"card\">"
        f"<h3>{_e(label)}</h3><div class=\"metric\">{_e(value)}</div>"
        f"<div class=\"hint\">{_e(hint)}</div></article>"
        for label, value, hint in cards
    )
    return f"<section><h2>Headline</h2><div class=\"cards\">{rendered}</div></section>"


def _kappa_matrix(summary: AgreementSummary) -> str:
    if not summary.members:
        return ""
    header = "<tr><th>member</th>" + "".join(
        f"<th>{_e(member)}</th>" for member in summary.members
    ) + "</tr>"
    rows = []
    for left in summary.members:
        cells = [f"<th>{_e(left)}</th>"]
        for right in summary.members:
            value = summary.pairwise_kappa.get(left, {}).get(right)
            cells.append(
                f"<td style=\"{_kappa_style(value)}\">{_e(_fmt_number(value))}</td>"
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    note = (
        "<p class=\"hint\">Color is intentionally inverted from usual heatmaps: "
        "green means low kappa/diverse signal, red means high kappa/clone alert.</p>"
    )
    return f"<section><h2>Pairwise Kappa</h2>{note}<table>{header}{''.join(rows)}</table></section>"


def _member_table(
    summary: AgreementSummary,
    members: Sequence[str],
    accuracies,
    skills: Mapping[str, float],
    deltas: Mapping[str, float | None],
    roster,
) -> str:
    if not members:
        return ""
    ranks = {member: index + 1 for index, member in enumerate(_roster_picks(roster))}
    rows = []
    for member in members:
        accuracy = accuracies.get(member)
        rows.append(
            "<tr>"
            f"<td>{_e(member)}</td>"
            f"<td>{_e(ranks.get(member, ''))}</td>"
            f"<td>{_e(_fmt_percent(accuracy.accuracy if accuracy else None))}</td>"
            f"<td>{_e(accuracy.n if accuracy else '')}</td>"
            f"<td>{_e(_fmt_number(summary.redundancy.get(member)))}</td>"
            f"<td>{_e(_fmt_number(skills.get(member)))}</td>"
            f"<td>{_e(_fmt_delta(deltas.get(member)))}</td>"
            "</tr>"
        )
    header = (
        "<tr><th>member</th><th>roster rank</th><th>truth accuracy</th>"
        "<th>n truth</th><th>redundancy</th><th>Dawid-Skene skill</th>"
        "<th>drop-one delta</th></tr>"
    )
    return f"<section><h2>Members</h2><table>{header}{''.join(rows)}</table></section>"


def _discussion(summary: AgreementSummary, accuracies, deltas) -> str:
    points = []
    if summary.n_items == 0:
        points.append("No vote records have been supplied yet.")
    if summary.kappa_paradox_warning:
        points.append(
            "The kappa-paradox banner is active: high raw agreement and low "
            "Fleiss kappa usually means label prevalence is distorting kappa."
        )
    if summary.raw_agreement is not None:
        if summary.raw_agreement >= 0.8:
            points.append("Raw agreement is high, so blind votes often land together.")
        elif summary.raw_agreement < 0.5:
            points.append("Raw agreement is low, so the council is exposing real splits.")
        else:
            points.append("Raw agreement is moderate; disagreement should be inspected item by item.")
    if summary.mean_pairwise_kappa is not None:
        if summary.mean_pairwise_kappa >= 0.75:
            points.append("Mean pairwise kappa is high enough to treat redundancy as a clone risk.")
        elif summary.mean_pairwise_kappa <= 0.2:
            points.append("Mean pairwise kappa is low, which is useful when you want diverse errors.")
        else:
            points.append("Mean pairwise kappa is neither clone-like nor fully independent.")
    if summary.n_members:
        points.append(
            f"Your {summary.n_members}-member council behaves like "
            f"{summary.n_effective:.1f} independent voters."
        )

    truth_values = [
        (member, stat.accuracy)
        for member, stat in accuracies.items()
        if stat.accuracy is not None
    ]
    if truth_values:
        best = max(truth_values, key=lambda item: item[1])
        worst = min(truth_values, key=lambda item: item[1])
        points.append(
            f"Truth-labeled records put {best[0]} highest at {_fmt_percent(best[1])} "
            f"and {worst[0]} lowest at {_fmt_percent(worst[1])}."
        )
    else:
        points.append(
            "No truth labels are present, so member skill estimates are "
            "self-referential Dawid-Skene signals rather than supervised accuracy."
        )

    numeric_deltas = [
        (member, delta) for member, delta in deltas.items() if delta is not None
    ]
    if numeric_deltas:
        costly = min(numeric_deltas, key=lambda item: item[1])
        helpful_to_remove = max(numeric_deltas, key=lambda item: item[1])
        if costly[1] < -0.005:
            points.append(
                f"Removing {costly[0]} costs {_fmt_percent(abs(costly[1]))} "
                "majority-vote accuracy."
            )
        if helpful_to_remove[1] > 0.005:
            points.append(
                f"Removing {helpful_to_remove[0]} improves majority-vote accuracy "
                f"by {_fmt_percent(helpful_to_remove[1])}."
            )

    rendered = "".join(f"<li>{_e(point)}</li>" for point in points)
    return f"<section><h2>Discussion</h2><ul>{rendered}</ul></section>"


def _health_members(summary, accuracies, skills, roster) -> list[str]:
    members = list(summary.members)
    seen = set(members)
    for source in [accuracies, skills]:
        for member in source:
            if member not in seen:
                members.append(member)
                seen.add(member)
    for member in _roster_picks(roster):
        if member not in seen:
            members.append(member)
            seen.add(member)
    return members


def _roster_picks(roster) -> list[str]:
    if roster is None:
        return []
    if hasattr(roster, "picks"):
        return [str(member) for member in roster.picks]
    return [str(member) for member in roster]


def _labels(records: Sequence[VoteRecord]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for record in records:
        for label in record.labels:
            key = label.casefold()
            if key not in seen:
                labels.append(label)
                seen.add(key)
    return labels


def _agreement_hint(value: float | None) -> str:
    if value is None:
        return "not enough overlapping votes"
    if value >= 0.8:
        return "high surface agreement"
    if value < 0.5:
        return "substantial disagreement"
    return "mixed agreement"


def _kappa_hint(value: float | None) -> str:
    if value is None:
        return "not enough complete items"
    if value >= 0.75:
        return "strong chance-corrected agreement"
    if value < 0.4:
        return "weak chance-corrected agreement"
    return "moderate chance-corrected agreement"


def _kappa_style(value: float | None) -> str:
    if value is None:
        return "background:#eef2f3;color:#5b6770;"
    clamped = min(1.0, max(0.0, value))
    hue = 125 * (1 - clamped)
    return f"background:hsl({hue:.0f} 70% 88%);"


def _badge(text: str) -> str:
    return f"<span class=\"badge\">{_e(text)}</span>"


def _fmt_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _fmt_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{100 * value:.1f}%"


def _fmt_delta(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    return f"{sign}{100 * value:.1f}%"


def _e(value: Any) -> str:
    if value is None:
        return "n/a"
    return html.escape(str(value), quote=True)
