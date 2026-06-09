"""Round primitives.

Each round is a pure-ish function: given the models, the question, and the
transcript so far, it runs one stage of deliberation and appends turns. You
compose rounds into a protocol in ``council.py``.

Design choices that are easy to get wrong and matter a lot:

* **Blind first.** Models answer with NO sight of each other. This is the
  whole ballgame — if model B sees model A's answer first, B anchors to it
  and you've destroyed the independence that makes a council worth more than
  one model. Sycophancy and herd-following enter exactly here.
* **Disagreement is the signal.** The critique round surfaces where models
  diverge instead of averaging it away. The consensus map preserves that signal
  before the skeptic attacks it.
* **Adversarial roles are separate.** A dedicated skeptic is told to *refute*,
  not to help. Don't fold this into the critique round — a model asked to
  "improve" an answer behaves differently from one asked to "break" it.
"""

from __future__ import annotations

import re
import random
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Sequence

from quorum.adapters import Model
from quorum.transcript import Transcript, Turn


def _worker_count(model_count: int, max_workers: Optional[int]) -> int:
    if model_count == 0:
        return 0
    if max_workers is not None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        return min(max_workers, model_count)
    return min(32, model_count)


def _gather(
    models: Sequence[Model],
    prompt: str,
    max_workers: Optional[int] = None,
) -> list[tuple[str, str]]:
    """Run one prompt across models concurrently, preserving model order."""
    workers = _worker_count(len(models), max_workers)
    if workers == 0:
        return []
    if workers == 1:
        return [(m.name, m.complete(prompt)) for m in models]

    def complete(model: Model) -> tuple[str, str]:
        return model.name, model.complete(prompt)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(complete, models))


def _stable_seed(seed: int, *parts: str) -> int:
    blob = ":".join([str(seed), *parts]).encode("utf-8")
    return int.from_bytes(sha256(blob).digest()[:8], "big")


def _shuffled_turns(turns: Sequence[Turn], seed: int, role: str, critic: str) -> tuple[list[Turn], list[int]]:
    order = list(range(len(turns)))
    random.Random(_stable_seed(seed, role, critic)).shuffle(order)
    return [turns[i] for i in order], [i + 1 for i in order]


def _answer_pool(turns: Sequence[Turn]) -> str:
    return "\n\n".join(
        f"ANSWER {i + 1}:\n{turn.response}" for i, turn in enumerate(turns)
    )


def _gather_prepared(
    jobs: Sequence[tuple[Model, str, dict]],
    max_workers: Optional[int] = None,
) -> list[tuple[str, str, str, dict]]:
    """Run per-model prompts concurrently, preserving input job order."""
    workers = _worker_count(len(jobs), max_workers)
    if workers == 0:
        return []
    if workers == 1:
        return [
            (model.name, prompt, model.complete(prompt), meta)
            for model, prompt, meta in jobs
        ]

    def complete(job: tuple[Model, str, dict]) -> tuple[str, str, str, dict]:
        model, prompt, meta = job
        return model.name, prompt, model.complete(prompt), meta

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(complete, jobs))


def blind_round(
    models: Sequence[Model],
    question: str,
    t: Transcript,
    max_workers: Optional[int] = None,
) -> None:
    """Every model answers independently, blind to the others."""
    prompt = (
        "Answer the following question as well as you can. You are answering "
        "independently; you cannot see anyone else's answer.\n\n"
        f"QUESTION:\n{question}"
    )
    for name, resp in _gather(models, prompt, max_workers=max_workers):
        t.record("blind", name, prompt, resp)


def critique_round(
    models: Sequence[Model],
    question: str,
    t: Transcript,
    max_workers: Optional[int] = None,
    seed: int = 0,
) -> None:
    """Each model sees all blind answers (anonymized) and critiques them —
    where they agree, where they diverge, and which reasoning is strongest."""
    blind = t.by_round("blind")
    jobs = []
    for model in models:
        shuffled, permutation = _shuffled_turns(blind, seed, "critique", model.name)
        prompt = (
            "Below are independent answers to the question. They are "
            "anonymized and their order has been randomized for you. Identify "
            "where they AGREE and where they DIVERGE. Point to the single "
            "strongest line of reasoning and the single weakest. Do not just "
            "average them.\n\n"
            f"QUESTION:\n{question}\n\n{_answer_pool(shuffled)}"
        )
        jobs.append(
            (
                model,
                prompt,
                {
                    "answer_permutation": permutation,
                    "answer_models": [turn.model for turn in shuffled],
                },
            )
        )
    for name, prompt, resp, meta in _gather_prepared(jobs, max_workers=max_workers):
        t.record("critique", name, prompt, resp, meta=meta)


def _split_claims(text: str) -> list[str]:
    chunks = re.split(r"(?:\n+|(?<=[.!?])\s+)", text.strip())
    return [re.sub(r"\s+", " ", chunk).strip() for chunk in chunks if chunk.strip()]


def _normalize_claim(claim: str) -> str:
    return re.sub(r"\W+", " ", claim.casefold()).strip()


def consensus_map_round(
    question: str,
    t: Transcript,
    distiller: Optional[Model] = None,
) -> None:
    """Build a replayable map of critique agreement and unresolved issues."""
    critiques = t.by_round("critique")
    critique_pool = "\n\n".join(
        f"CRITIQUE {i + 1}:\n{turn.response}"
        for i, turn in enumerate(critiques)
    )
    if distiller is not None:
        prompt = (
            "Distill the critique round into a concise semantic consensus/issue "
            "map. Group claims that agree in meaning even when the wording "
            "differs. Preserve unresolved divergences, assumptions, and one-off "
            "concerns. Base the map only on the critiques below; do not add new "
            "facts. Refer to critiques only by CRITIQUE 1, CRITIQUE 2, and so "
            "on; do not use model or member names.\n\n"
            "Use this shape:\n"
            "CONSENSUS / ISSUE MAP\n\n"
            "CRITIQUE AGREEMENTS\n"
            "- ...\n\n"
            "UNRESOLVED DIVERGENCES / OPEN ASSUMPTIONS\n"
            "- ...\n\n"
            f"QUESTION:\n{question}\n\nCRITIQUES:\n{critique_pool}"
        )
        t.record(
            "consensus_map",
            distiller.name,
            prompt,
            distiller.complete(prompt),
            meta={"critique_models": [turn.model for turn in critiques]},
        )
        return

    prompt = "[deterministic distillation - no model called]"

    claim_texts: dict[str, str] = {}
    claim_models: dict[str, set[str]] = {}
    for turn in critiques:
        for claim in _split_claims(turn.response):
            key = _normalize_claim(claim)
            if not key:
                continue
            claim_texts.setdefault(key, claim)
            claim_models.setdefault(key, set()).add(turn.model)

    # Deterministic and conservative by design: this stays replayable with
    # EchoModel/offline runs and only treats verbatim repeated critique claims
    # as agreement, rather than asking another model to invent consensus.
    agreements = [
        (claim_texts[key], sorted(models))
        for key, models in claim_models.items()
        if len(models) > 1
    ]
    unresolved = [
        (claim_texts[key], sorted(models))
        for key, models in claim_models.items()
        if len(models) == 1
    ]

    lines = [
        "CONSENSUS / ISSUE MAP",
        "",
        "CRITIQUE AGREEMENTS",
    ]
    if agreements:
        lines.extend(
            f"- {claim} (raised by {len(models)} of {len(critiques)} critics)"
            for claim, models in agreements
        )
    elif critiques:
        lines.append(
            "- No critique claim was repeated verbatim across models; treat the "
            "emerging consensus as unconfirmed."
        )
    else:
        lines.append("- No critique turns were recorded.")

    lines.extend(["", "UNRESOLVED DIVERGENCES / OPEN ASSUMPTIONS"])
    if unresolved:
        lines.extend(
            f"- {claim} (raised by {len(models)} of {len(critiques)} critics)"
            for claim, models in unresolved
        )
    elif critiques:
        lines.append("- No single-critique issues were identified.")
    else:
        lines.append("- With no critique round, no cross-model assumptions were tested.")

    t.record(
        "consensus_map",
        "quorum",
        prompt,
        "\n".join(lines),
        meta={
            "agreements": [
                {"claim": claim, "models": models}
                for claim, models in agreements
            ],
            "unresolved": [
                {"claim": claim, "models": models}
                for claim, models in unresolved
            ],
        },
    )


def adversarial_round(skeptic: Model, question: str, t: Transcript, seed: int = 0) -> None:
    """A dedicated skeptic tries to refute the consensus/issue map."""
    blind = t.by_round("blind")
    shuffled, permutation = _shuffled_turns(blind, seed, "adversarial", skeptic.name)
    pool = _answer_pool(shuffled)
    maps = t.by_round("consensus_map")
    consensus = "\n\n".join(turn.response for turn in maps)
    if not consensus:
        consensus = "No consensus/issue map was recorded; attack the answers directly."
    prompt = (
        "You are the adversary. Your job is to REFUTE, not to help. Attack "
        "specific claims and assumptions in the consensus/issue map below, "
        "using the independent answers as evidence. Find the strongest "
        "objection: a wrong assumption, a missed edge case, a failure mode. If "
        "you genuinely cannot break it, say so explicitly and explain why it "
        "survives.\n\n"
        f"QUESTION:\n{question}\n\n"
        f"CONSENSUS / ISSUE MAP:\n{consensus}\n\n"
        f"INDEPENDENT ANSWERS:\n{pool}"
    )
    t.record(
        "adversarial",
        skeptic.name,
        prompt,
        skeptic.complete(prompt),
        meta={
            "answer_permutation": permutation,
            "answer_models": [turn.model for turn in shuffled],
        },
    )


def synthesis_round(synthesizer: Model, question: str, t: Transcript) -> str:
    """One model reads the FULL transcript and writes the final answer,
    accounting for the map, critiques, and the adversary's objection."""
    sections = []
    for label, key in [
        ("INDEPENDENT ANSWERS", "blind"),
        ("BLIND VOTES", "vote"),
        ("CRITIQUES", "critique"),
        ("CONSENSUS / ISSUE MAP", "consensus_map"),
        ("ADVERSARIAL OBJECTION", "adversarial"),
        ("REVISED VOTES", "revote"),
    ]:
        turns = t.by_round(key)
        if turns:
            body = "\n\n".join(
                f"{label.rstrip('S')} {i + 1}\n{turn.response}"
                for i, turn in enumerate(turns)
            )
            sections.append(f"=== {label} ===\n{body}")
    context = "\n\n".join(sections)
    prompt = (
        "You are the synthesizer. Read the full deliberation and write the "
        "final answer. Do not ignore the adversarial objection — either "
        "incorporate it or explain why it doesn't hold. Where the council "
        "genuinely disagreed, say so rather than papering over it.\n\n"
        f"QUESTION:\n{question}\n\n{context}"
    )
    final = synthesizer.complete(prompt)
    t.record("synthesis", synthesizer.name, prompt, final)
    return final
