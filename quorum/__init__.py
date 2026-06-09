"""Quorum — a multi-model deliberation engine.

Ask a panel of models a question and run them through a structured
deliberation — blind answers, cross-critique, a consensus map, an adversarial
round, and a synthesis — instead of a naive fan-out or majority vote.
"""

from quorum.adapters import CallableModel, CLIModel, EchoModel, Model
from quorum.agreement import (
    AgreementSummary,
    FleissKappaResult,
    bootstrap_ci,
    cohen_kappa,
    fleiss_kappa,
    gwet_ac1,
    krippendorff_alpha,
    pairwise_kappa_matrix,
    raw_agreement,
    summary,
)
from quorum.council import Council, Verdict
from quorum.records import RecordStore, VoteRecord, records_to_votes
from quorum.transcript import Transcript, Turn
from quorum.votes import (
    VoteParseError,
    VoteRoundResult,
    majority_label,
    normalize_labels,
    parse_vote,
    tally_votes,
)

__version__ = "0.1.0"
__all__ = [
    "Council",
    "Verdict",
    "Transcript",
    "Turn",
    "VoteRecord",
    "RecordStore",
    "Model",
    "CallableModel",
    "CLIModel",
    "EchoModel",
    "VoteParseError",
    "VoteRoundResult",
    "AgreementSummary",
    "FleissKappaResult",
    "parse_vote",
    "tally_votes",
    "majority_label",
    "normalize_labels",
    "records_to_votes",
    "raw_agreement",
    "cohen_kappa",
    "pairwise_kappa_matrix",
    "fleiss_kappa",
    "krippendorff_alpha",
    "gwet_ac1",
    "bootstrap_ci",
    "summary",
]
