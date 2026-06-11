"""Adversary persona library.

The adversary's *role instruction* is the one thing that most changes what kind
of objection you get. A model told to "find the wrong assumption" behaves very
differently from one told to "argue the opposite" or "attack on compliance
grounds". Each persona swaps that instruction; the rest of the adversarial round
(attack the consensus map, cite the answers, concede honestly if it survives)
is constant.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdversaryPersona:
    id: str
    label: str
    description: str
    instruction: str


PERSONAS: dict[str, AdversaryPersona] = {
    "red_team": AdversaryPersona(
        "red_team", "Red team",
        "General attacker — hunts the strongest objection of any kind.",
        "You are the adversary. Your job is to REFUTE, not to help. Find the "
        "single strongest objection: a wrong assumption, a missed edge case, or "
        "a failure mode.",
    ),
    "steelman": AdversaryPersona(
        "steelman", "Steelman",
        "States the strongest version of the consensus, then attacks THAT.",
        "You are the steelman adversary. First restate the consensus in its "
        "strongest, most charitable form — no strawmen. Then attack that "
        "strongest version: find the objection that survives even the most "
        "generous reading.",
    ),
    "devils_advocate": AdversaryPersona(
        "devils_advocate", "Devil's advocate",
        "Argues the opposite conclusion as persuasively as possible.",
        "You are the devil's advocate. Argue for the OPPOSITE conclusion as "
        "persuasively as you can, regardless of your private view. Marshal the "
        "best possible case against the consensus.",
    ),
    "domain_skeptic": AdversaryPersona(
        "domain_skeptic", "Domain skeptic",
        "Attacks on technical / domain-expertise grounds.",
        "You are a domain-expert skeptic. Attack the consensus on technical "
        "grounds: assumptions that misread how the system or field actually "
        "works, overlooked constraints, misapplied heuristics. Name the specific "
        "domain reason it fails.",
    ),
    "compliance": AdversaryPersona(
        "compliance", "Compliance red-team",
        "Attacks on regulatory, legal, safety, and reputational risk.",
        "You are a compliance and risk red-team. Attack the consensus for "
        "regulatory, legal, safety, privacy, and reputational exposure. Identify "
        "what fails under audit, adversarial scrutiny, or worst-case misuse, and "
        "which claims would not survive review.",
    ),
}

DEFAULT_PERSONA_ID = "red_team"


def get_persona(persona: "AdversaryPersona | str | None") -> AdversaryPersona:
    if isinstance(persona, AdversaryPersona):
        return persona
    if persona is None:
        return PERSONAS[DEFAULT_PERSONA_ID]
    try:
        return PERSONAS[persona]
    except KeyError:
        raise KeyError(f"unknown adversary persona: {persona!r}") from None


def persona_catalog() -> list[dict]:
    return [
        {"id": p.id, "label": p.label, "description": p.description,
         "default": p.id == DEFAULT_PERSONA_ID}
        for p in PERSONAS.values()
    ]
