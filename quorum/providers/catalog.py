"""Allowlisted local-CLI catalog.

Local mode runs ONLY the CLIs listed here — never an arbitrary PATH binary —
and each entry records exactly how to drive that tool. CLIs that are agentic
(carry local context, run tools/hooks) are flagged and disabled by default so
they can't silently pollute a council with operator chatter.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Sequence

from quorum.adapters import CLIModel


@dataclass(frozen=True)
class LocalModelSpec:
    id: str
    label: str
    command: tuple[str, ...]
    prompt_transport: str            # "arg" | "stdin"
    agentic: bool                    # carries local context / runs tools?
    enabled_by_default: bool
    note: str = ""
    roles: tuple[str, ...] = ("member", "skeptic", "synthesizer", "distiller")

    @property
    def binary(self) -> str:
        return self.command[0]

    @property
    def available(self) -> bool:
        """Is the CLI actually installed on this machine?"""
        return shutil.which(self.binary) is not None


# The allowlist. Transport verified by hand:
#   claude -p "<prompt>" / gemini -p "<prompt>" / grok -p "<prompt>"  -> arg
#   deepseek / codex exec --skip-git-repo-check                      -> stdin
LOCAL_CATALOG: list[LocalModelSpec] = [
    LocalModelSpec(
        id="claude", label="Claude", command=("claude", "-p"),
        prompt_transport="arg", agentic=False, enabled_by_default=True,
        note="Anthropic Claude Code in print mode; OAuth via ~/.claude.",
    ),
    LocalModelSpec(
        id="gemini", label="Gemini", command=("gemini", "-p"),
        prompt_transport="arg", agentic=False, enabled_by_default=False,
        note="Retired locally: Gemini CLI returns IneligibleTierError; kept only so audition can quarantine it.",
    ),
    LocalModelSpec(
        id="grok", label="Grok", command=("grok", "-p"),
        prompt_transport="arg", agentic=True, enabled_by_default=False,
        note="xAI Grok single-turn (-p); needs `grok` OAuth sign-in; agentic.",
    ),
    LocalModelSpec(
        id="gpt-5.5", label="GPT-5.5 (codex)", command=("codex", "exec", "--skip-git-repo-check"),
        prompt_transport="stdin", agentic=True, enabled_by_default=False,
        note="OpenAI codex agent; runs tools and may run outside git repos via --skip-git-repo-check.",
    ),
    LocalModelSpec(
        id="deepseek", label="DeepSeek", command=("deepseek",),
        prompt_transport="stdin", agentic=True, enabled_by_default=False,
        note="Local wrapper may carry operator context — opt in knowingly.",
    ),
]

_BY_ID = {spec.id: spec for spec in LOCAL_CATALOG}


def get_spec(model_id: str) -> LocalModelSpec:
    """Look up an allowlisted spec, or raise (no arbitrary-PATH fallback)."""
    try:
        return _BY_ID[model_id]
    except KeyError:
        raise KeyError(f"unknown local model id: {model_id!r}") from None


def build_local_model(model_id: str, timeout: float = 600) -> CLIModel:
    spec = get_spec(model_id)
    return CLIModel(
        spec.id, list(spec.command),
        timeout=timeout, prompt_transport=spec.prompt_transport,
    )
