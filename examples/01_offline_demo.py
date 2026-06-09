"""Run a full deliberation with zero API keys, using stub models.

    python examples/01_offline_demo.py

This shows the protocol end to end. Swap the EchoModels for CLIModel or
CallableModel adapters (see examples/02_real_models.py) to use real models.
"""

from quorum import Council, EchoModel

members = [
    EchoModel("alice", "Insertion sort — the list is nearly sorted, so it's near O(n)."),
    EchoModel("bob", "Timsort — it detects existing runs and is the practical default."),
    EchoModel("carol", "Insertion sort for tiny n, but Timsort generalizes better."),
]
skeptic = EchoModel(
    "skeptic",
    "Everyone assumed comparison sorts. If keys are small integers, a counting "
    "sort beats all of these. The consensus is only right under the stated "
    "comparison-sort framing.",
)

council = Council(members=members, skeptic=skeptic, synthesizer=members[1])
verdict = council.ask("Best sort for a nearly-sorted list of 10,000 elements?")

print("FINAL ANSWER\n------------")
print(verdict.answer)
print("\nFULL TRANSCRIPT (replayable)\n----------------------------")
print(verdict.transcript.to_json())
