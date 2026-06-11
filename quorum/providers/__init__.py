"""Provider catalog + audition gate.

Quorum only ever runs models from an explicit allowlist (:mod:`catalog`),
never an arbitrary PATH binary, and every web-triggered local model run is
probed for clean, single-turn behavior first (:mod:`audition`).
"""

from quorum.providers.catalog import (
    LOCAL_CATALOG,
    LocalModelSpec,
    build_local_model,
    get_spec,
)
from quorum.providers.audition import Audition, audit_output, probe

__all__ = [
    "LOCAL_CATALOG",
    "LocalModelSpec",
    "build_local_model",
    "get_spec",
    "Audition",
    "audit_output",
    "probe",
]
