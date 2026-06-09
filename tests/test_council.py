"""Offline tests — no API keys, no network. Uses EchoModel stubs."""

import sys
import threading

from quorum import CLIModel, Council, EchoModel, Transcript


def test_blind_then_synthesis_runs_full_protocol():
    a = EchoModel("alice", "Use insertion sort; the list is nearly sorted.")
    b = EchoModel("bob", "Use Timsort; it exploits existing runs.")
    skeptic = EchoModel("sk", "Both ignore memory limits, but the answer holds.")

    council = Council(members=[a, b], skeptic=skeptic, synthesizer=a)
    verdict = council.ask("Best sort for a nearly-sorted 10k list?")

    rounds = [t.round for t in verdict.transcript.turns]
    assert rounds == [
        "blind",
        "blind",
        "critique",
        "critique",
        "consensus_map",
        "adversarial",
        "synthesis",
    ]
    # two members -> two blind turns, two critique turns, and one map
    assert len(verdict.transcript.by_round("blind")) == 2
    assert len(verdict.transcript.by_round("critique")) == 2
    assert len(verdict.transcript.by_round("consensus_map")) == 1
    assert len(verdict.transcript.by_round("adversarial")) == 1
    consensus_map = verdict.transcript.by_round("consensus_map")[0]
    adversarial_prompt = verdict.transcript.by_round("adversarial")[0].prompt
    assert consensus_map.model == "quorum"
    assert consensus_map.prompt == "[deterministic distillation - no model called]"
    assert "UNRESOLVED DIVERGENCES / OPEN ASSUMPTIONS" in consensus_map.response
    assert consensus_map.response in adversarial_prompt
    assert verdict.answer == a.reply


def test_distiller_model_builds_semantic_consensus_map():
    a = EchoModel("alice", "near-sorted favors insertion sort")
    b = EchoModel("bob", "existing runs favor Timsort")
    distiller = EchoModel("distiller", "SEMANTIC CONSENSUS MAP")

    verdict = Council(members=[a, b], synthesizer=a, distiller=distiller).ask("sort?")
    consensus_map = verdict.transcript.by_round("consensus_map")[0]

    assert consensus_map.model == "distiller"
    assert "semantic consensus/issue map" in consensus_map.prompt
    assert consensus_map.response == "SEMANTIC CONSENSUS MAP"


class BarrierModel:
    def __init__(self, name: str, reply: str, barrier: threading.Barrier) -> None:
        self.name = name
        self.reply = reply
        self.barrier = barrier

    def complete(self, prompt: str) -> str:
        if "independently" in prompt:
            self.barrier.wait(timeout=1)
        return self.reply


def test_parallel_rounds_preserve_member_order():
    barrier = threading.Barrier(2)
    first = BarrierModel("first", "first reply", barrier)
    second = BarrierModel("second", "second reply", barrier)

    verdict = Council(members=[first, second], max_workers=2).ask("order?")
    blind = verdict.transcript.by_round("blind")
    critique = verdict.transcript.by_round("critique")

    assert [(turn.model, turn.response) for turn in blind] == [
        ("first", "first reply"),
        ("second", "second reply"),
    ]
    assert [turn.model for turn in critique] == ["first", "second"]


def test_single_member_skips_critique():
    solo = EchoModel("solo", "42")
    verdict = Council(members=[solo]).ask("Meaning of life?")
    assert verdict.transcript.by_round("critique") == []
    assert verdict.transcript.by_round("consensus_map") == []
    assert verdict.answer == "42"


def test_single_member_with_skeptic_attacks_answers_directly():
    solo = EchoModel("solo", "42")
    skeptic = EchoModel("skeptic", "No objection.")
    verdict = Council(members=[solo], skeptic=skeptic).ask("Meaning of life?")
    adversarial_prompt = verdict.transcript.by_round("adversarial")[0].prompt

    assert "No consensus/issue map was recorded" in adversarial_prompt
    assert verdict.transcript.by_round("consensus_map") == []


def test_transcript_roundtrips_through_json():
    solo = EchoModel("solo", "ok – café")
    verdict = Council(members=[solo]).ask("ping? ✓")
    restored = Transcript.from_json(verdict.transcript.to_json())
    assert restored.question == "ping? ✓"
    assert restored.turns[0].response == "ok – café"


def test_empty_council_is_rejected():
    try:
        Council(members=[])
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty council")


def test_invalid_max_workers_is_rejected():
    solo = EchoModel("solo", "ok")
    try:
        Council(members=[solo], max_workers=0).ask("ping?")
    except ValueError as exc:
        assert "max_workers" in str(exc)
        return
    raise AssertionError("expected ValueError for max_workers=0")


def test_cli_model_failures_are_clear_responses():
    missing = CLIModel("missing", ["definitely-not-a-real-quorum-command"])
    failed = CLIModel(
        "failed",
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(7)"],
    )
    timed_out = CLIModel(
        "slow",
        [sys.executable, "-c", "import time; time.sleep(1)"],
        timeout=0.01,
    )

    assert "command not found" in missing.complete("prompt")
    failed_response = failed.complete("prompt")
    assert "exited 7" in failed_response
    assert "boom" in failed_response
    assert "timed out" in timed_out.complete("prompt")


if __name__ == "__main__":
    tests = [
        test_blind_then_synthesis_runs_full_protocol,
        test_distiller_model_builds_semantic_consensus_map,
        test_parallel_rounds_preserve_member_order,
        test_single_member_skips_critique,
        test_single_member_with_skeptic_attacks_answers_directly,
        test_transcript_roundtrips_through_json,
        test_empty_council_is_rejected,
        test_invalid_max_workers_is_rejected,
        test_cli_model_failures_are_clear_responses,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} tests passed")
