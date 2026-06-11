"""Adversary persona library — offline."""

import pytest

from quorum.adapters import EchoModel
from quorum.personas import (
    DEFAULT_PERSONA_ID,
    PERSONAS,
    get_persona,
    persona_catalog,
)
from quorum.rounds import adversarial_round
from quorum.transcript import Transcript


def test_default_and_lookup():
    assert get_persona(None).id == DEFAULT_PERSONA_ID
    assert get_persona("compliance").id == "compliance"
    p = PERSONAS["steelman"]
    assert get_persona(p) is p


def test_unknown_persona_raises():
    with pytest.raises(KeyError):
        get_persona("nope")


def test_catalog_shape_one_default():
    cat = persona_catalog()
    ids = {c["id"] for c in cat}
    assert {"red_team", "steelman", "devils_advocate", "domain_skeptic", "compliance"} <= ids
    assert sum(1 for c in cat if c["default"]) == 1


def test_adversarial_round_applies_persona_instruction_and_meta():
    t = Transcript("q")
    t.record("blind", "a", "p", "answer A")
    t.record("consensus_map", "quorum", "p", "CONSENSUS / ISSUE MAP\n- claim")
    adversarial_round(EchoModel("vex", "objection"), "q", t, persona="compliance")
    turn = t.by_round("adversarial")[0]
    assert turn.meta["persona"] == "compliance"
    assert "compliance and risk red-team" in turn.prompt
    # the constant attack mechanics are still present
    assert "Attack specific claims and assumptions in the consensus" in turn.prompt


def test_default_persona_preserves_red_team_wording():
    t = Transcript("q")
    t.record("blind", "a", "p", "answer A")
    adversarial_round(EchoModel("vex", "x"), "q", t)
    turn = t.by_round("adversarial")[0]
    assert turn.meta["persona"] == "red_team"
    assert "Your job is to REFUTE" in turn.prompt
