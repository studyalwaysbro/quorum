"""Quorum — a multi-model deliberation engine.

Ask a panel of models a question and run them through a structured
deliberation — blind answers, cross-critique, a consensus map, an adversarial
round, and a synthesis — instead of a naive fan-out or majority vote.
"""

from quorum.adapters import CallableModel, CLIModel, EchoModel, Model
from quorum.council import Council, Verdict
from quorum.transcript import Transcript, Turn

__version__ = "0.1.0"
__all__ = [
    "Council",
    "Verdict",
    "Transcript",
    "Turn",
    "Model",
    "CallableModel",
    "CLIModel",
    "EchoModel",
]
