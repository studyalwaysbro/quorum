"""Round-aware demo models, so the web UI works with zero API keys.

A real council wraps actual LLM CLIs/APIs. But a portfolio demo needs to *show*
the mechanic to someone who has no keys installed — so these stub models read
the round-marker phrases that ``quorum.rounds`` puts in each prompt and reply
with distinct, coherent text per role. The point isn't realism; it's that a
visitor can watch independent answers diverge, get critiqued, get attacked by
the skeptic, and get synthesized — the whole loop — instantly and offline.
"""

from __future__ import annotations

from dataclasses import dataclass


# Each persona argues from a different angle so the blind round genuinely
# diverges (which is the entire reason a council beats one model).
PERSONAS = {
    "Atlas": {
        "lens": "pragmatic systems engineering",
        "take": "favor the simplest thing that survives contact with scale",
    },
    "Beacon": {
        "lens": "first-principles analysis",
        "take": "reason from the invariants before reaching for a tool",
    },
    "Cypher": {
        "lens": "risk and edge-cases",
        "take": "assume the happy path is a trap and hunt the failure mode",
    },
    "Juno": {
        "lens": "empirical / measure-first",
        "take": "don't decide until the data says which option actually wins",
    },
}

SKEPTIC_NAME = "Vex"


def _short(question: str, limit: int = 140) -> str:
    q = " ".join(question.split())
    return q if len(q) <= limit else q[: limit - 1] + "…"


@dataclass
class DemoModel:
    """A keyless stub whose reply depends on which deliberation round it's in.

    It detects the round from marker phrases in the prompt (the same phrases
    ``quorum.rounds`` writes) and answers in character.
    """

    name: str
    lens: str = ""
    take: str = ""
    role: str = "member"   # "member" | "skeptic"
    order: int = 0         # stable index, used to split the demo vote

    def complete(self, prompt: str) -> str:
        q = _short(_extract_question(prompt))
        if "VERDICT:" in prompt and "CONFIDENCE:" in prompt:
            return self._vote(prompt)
        if "You are the synthesizer" in prompt:
            return self._synthesis(q)
        if "You are the adversary" in prompt:
            return self._adversarial(q)
        if "consensus/issue map" in prompt.lower() or "Distill the critique" in prompt:
            return self._consensus_map(q)
        if "Below are independent answers" in prompt:
            return self._critique(q)
        # default: the blind round ("You are answering independently")
        return self._blind(q)

    def _vote(self, prompt: str) -> str:
        """Cast a categorical vote. Blind votes split by persona; on the revote
        (after the adversary) everyone converges on the *actual* blind plurality
        (parsed from the tally in the revote prompt) — so the demo scorecard
        shows agreement rising and real flips."""
        labels = _labels_from_prompt(prompt) or ["yes", "no"]
        if "revise your categorical vote" in prompt:
            target = _plurality_from_tally(prompt, labels) or labels[0]
            return f"VERDICT: {target}\nCONFIDENCE: 4"
        return f"VERDICT: {labels[self.order % len(labels)]}\nCONFIDENCE: 3"

    def _consensus_map(self, q: str) -> str:
        return (
            "CONSENSUS / ISSUE MAP\n\n"
            "CRITIQUE AGREEMENTS\n"
            "- The framing matters more than the specific pick.\n"
            "- The right answer depends on how badly being wrong scales.\n"
            "- Favor options that stay cheap to reverse.\n\n"
            "UNRESOLVED DIVERGENCES / OPEN ASSUMPTIONS\n"
            f"- Risk appetite is genuinely split across the panel for “{q}”.\n"
            "- Whether the inputs are stable enough to decide on at all.\n"
            "- How much weight to give the worst-case vs the expected case."
        )

    # -- per-round voices -------------------------------------------------
    def _blind(self, q: str) -> str:
        return (
            f"Reading this through a {self.lens} lens, my instinct is to {self.take}. "
            f"For “{q}” the decision really turns on one variable — how much the "
            f"cost of being wrong scales — and I'd commit to the option that stays "
            f"cheap to reverse if the assumptions don't hold."
        )

    def _critique(self, q: str) -> str:
        return (
            f"The answers agree that the framing matters more than the specific pick; "
            f"where they diverge is risk appetite. The strongest line of reasoning is the "
            f"one that names a concrete failure mode rather than asserting a winner. The "
            f"weakest just restates the question as an answer. From my {self.lens} seat, the "
            f"unresolved assumption is whether the inputs are even stable enough to decide on."
        )

    def _adversarial(self, q: str) -> str:
        return (
            f"Objection. The emerging consensus quietly assumes the question is well-posed. "
            f"For “{q}” that's the flaw: if the constraints shift — scale, data quality, "
            f"or who actually consumes the result — the agreed answer inverts. Nobody has "
            f"shown the recommendation survives its own worst case, so I don't accept it yet."
        )

    def _synthesis(self, q: str) -> str:
        return (
            f"Synthesis: the council converges on *how* to decide more than on a single "
            f"answer. Net recommendation for “{q}” — pick the option that is cheapest "
            f"to reverse, gate it on the one assumption the skeptic flagged (that the problem "
            f"stays well-posed under changing scale/inputs), and instrument that assumption so "
            f"a wrong call surfaces early. Where the members genuinely split was risk appetite; "
            f"that's left explicit rather than averaged away."
        )


def _extract_question(prompt: str) -> str:
    """Pull the QUESTION block out of a round prompt for echoing back."""
    marker = "QUESTION:\n"
    if marker in prompt:
        rest = prompt.split(marker, 1)[1]
        # question ends at the next blank line / section header
        for stop in ("\n\n", "\nANSWER", "\nCRITIQUE", "\nCONSENSUS", "\nINDEPENDENT"):
            if stop in rest:
                rest = rest.split(stop, 1)[0]
        return rest.strip()
    return prompt.strip()[:140]


def _labels_from_prompt(prompt: str) -> list[str]:
    """Pull the LABELS bullet list out of a vote prompt."""
    if "LABELS:" not in prompt:
        return []
    block = prompt.split("LABELS:", 1)[1]
    labels: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            labels.append(stripped[2:].strip())
        elif labels and not stripped:
            break
    return labels


def _plurality_from_tally(prompt: str, labels: list[str]) -> str | None:
    """Parse the 'BLIND VOTE TALLY:' block in a revote prompt and return the
    label with the most votes (None on a tie or if absent)."""
    if "BLIND VOTE TALLY:" not in prompt:
        return None
    block = prompt.split("BLIND VOTE TALLY:", 1)[1]
    counts: dict[str, int] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ") or ":" not in stripped:
            if counts and not stripped:
                break
            continue
        label, _, count = stripped[2:].rpartition(":")
        label = label.strip()
        try:
            counts[label] = int(count.strip())
        except ValueError:
            continue
    if not counts:
        return None
    top = max(counts.values())
    winners = [label for label, c in counts.items() if c == top]
    return winners[0] if len(winners) == 1 else None


def demo_member(name: str) -> DemoModel:
    spec = PERSONAS.get(name) or {"lens": "general reasoning", "take": "weigh the trade-offs plainly"}
    order = list(PERSONAS).index(name) if name in PERSONAS else (abs(hash(name)) % 7)
    return DemoModel(name=name, lens=spec["lens"], take=spec["take"], role="member", order=order)


def demo_skeptic(name: str = SKEPTIC_NAME) -> DemoModel:
    return DemoModel(name=name, lens="adversarial", take="break the consensus", role="skeptic")


def default_demo_roster() -> dict:
    """Members + skeptic the UI offers by default."""
    return {
        "members": list(PERSONAS.keys())[:3],   # Atlas, Beacon, Cypher
        "skeptic": SKEPTIC_NAME,
        "all_members": list(PERSONAS.keys()),
    }
