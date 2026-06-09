"""Offline tests — no API keys, no network. Uses EchoModel stubs."""

from quorum import Council, EchoModel, Transcript


def test_blind_then_synthesis_runs_full_protocol():
    a = EchoModel("alice", "Use insertion sort; the list is nearly sorted.")
    b = EchoModel("bob", "Use Timsort; it exploits existing runs.")
    skeptic = EchoModel("sk", "Both ignore memory limits, but the answer holds.")

    council = Council(members=[a, b], skeptic=skeptic, synthesizer=a)
    verdict = council.ask("Best sort for a nearly-sorted 10k list?")

    rounds = {t.round for t in verdict.transcript.turns}
    assert rounds == {"blind", "critique", "adversarial", "synthesis"}
    # two members -> two blind turns and two critique turns
    assert len(verdict.transcript.by_round("blind")) == 2
    assert len(verdict.transcript.by_round("critique")) == 2
    assert len(verdict.transcript.by_round("adversarial")) == 1
    assert verdict.answer == a.reply


def test_single_member_skips_critique():
    solo = EchoModel("solo", "42")
    verdict = Council(members=[solo]).ask("Meaning of life?")
    assert verdict.transcript.by_round("critique") == []
    assert verdict.answer == "42"


def test_transcript_roundtrips_through_json():
    solo = EchoModel("solo", "ok")
    verdict = Council(members=[solo]).ask("ping?")
    restored = Transcript.from_json(verdict.transcript.to_json())
    assert restored.question == "ping?"
    assert restored.turns[0].response == "ok"


def test_empty_council_is_rejected():
    try:
        Council(members=[])
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty council")
