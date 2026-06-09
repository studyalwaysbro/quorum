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
  diverge instead of averaging it away. A 3-1 split is information, not noise.
* **Adversarial roles are separate.** A dedicated skeptic is told to *refute*,
  not to help. Don't fold this into the critique round — a model asked to
  "improve" an answer behaves differently from one asked to "break" it.
"""

from __future__ import annotations

from typing import Sequence

from quorum.adapters import Model
from quorum.transcript import Transcript


def _gather(models: Sequence[Model], prompt: str) -> list[tuple[str, str]]:
    """Run a prompt across models. Sequential and simple by default; swap in
    a thread pool here if latency matters — the contract is unchanged."""
    return [(m.name, m.complete(prompt)) for m in models]


def blind_round(models: Sequence[Model], question: str, t: Transcript) -> None:
    """Every model answers independently, blind to the others."""
    prompt = (
        "Answer the following question as well as you can. You are answering "
        "independently; you cannot see anyone else's answer.\n\n"
        f"QUESTION:\n{question}"
    )
    for name, resp in _gather(models, prompt):
        t.record("blind", name, prompt, resp)


def critique_round(models: Sequence[Model], question: str, t: Transcript) -> None:
    """Each model sees all blind answers (anonymized) and critiques them —
    where they agree, where they diverge, and which reasoning is strongest."""
    blind = t.by_round("blind")
    pool = "\n\n".join(
        f"ANSWER {i + 1}:\n{turn.response}" for i, turn in enumerate(blind)
    )
    for m in models:
        prompt = (
            "Below are independent answers to the question. They are "
            "anonymized. Identify where they AGREE and where they DIVERGE. "
            "Point to the single strongest line of reasoning and the single "
            "weakest. Do not just average them.\n\n"
            f"QUESTION:\n{question}\n\n{pool}"
        )
        t.record("critique", m.name, prompt, m.complete(prompt))


def adversarial_round(skeptic: Model, question: str, t: Transcript) -> None:
    """A dedicated skeptic tries to refute the emerging consensus. Default to
    finding the flaw, not endorsing."""
    blind = t.by_round("blind")
    pool = "\n\n".join(
        f"ANSWER {i + 1}:\n{turn.response}" for i, turn in enumerate(blind)
    )
    prompt = (
        "You are the adversary. Your job is to REFUTE, not to help. Find the "
        "strongest objection to the consensus forming below: a wrong "
        "assumption, a missed edge case, a failure mode. If you genuinely "
        "cannot break it, say so explicitly and explain why it survives.\n\n"
        f"QUESTION:\n{question}\n\n{pool}"
    )
    t.record("adversarial", skeptic.name, prompt, skeptic.complete(prompt))


def synthesis_round(synthesizer: Model, question: str, t: Transcript) -> str:
    """One model reads the FULL transcript and writes the final answer,
    accounting for the critiques and the adversary's objection."""
    sections = []
    for label, key in [
        ("INDEPENDENT ANSWERS", "blind"),
        ("CRITIQUES", "critique"),
        ("ADVERSARIAL OBJECTION", "adversarial"),
    ]:
        turns = t.by_round(key)
        if turns:
            body = "\n\n".join(f"[{turn.model}]\n{turn.response}" for turn in turns)
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
