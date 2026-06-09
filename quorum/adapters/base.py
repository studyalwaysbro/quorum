"""Model adapters.

An adapter is anything that turns a prompt into a text completion. The
interface is deliberately tiny so you can wrap an HTTP API, a local model,
or a CLI subprocess in a few lines. Quorum never imports a provider SDK
itself — you bring your own.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol


class Model(Protocol):
    """The only contract Quorum depends on."""

    name: str

    def complete(self, prompt: str) -> str:
        ...


@dataclass
class CallableModel:
    """Wrap any ``str -> str`` function as a model.

    Useful for API clients you've already built::

        gpt = CallableModel("gpt", lambda p: my_openai_client(p))
    """

    name: str
    fn: Callable[[str], str]

    def complete(self, prompt: str) -> str:
        return self.fn(prompt)


@dataclass
class CLIModel:
    """Wrap a command-line tool that reads a prompt on stdin and prints a reply.

    Matches the shape of CLIs like ``deepseek``, ``codex``, ``gemini``::

        CLIModel("deepseek", ["deepseek"])
        CLIModel("gpt-5.5", ["codex", "--quiet"])
    """

    name: str
    argv: list[str]
    timeout: int = 600

    def complete(self, prompt: str) -> str:
        result = subprocess.run(
            self.argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{self.name} exited {result.returncode}: {result.stderr.strip()[:500]}"
            )
        return result.stdout.strip()


@dataclass
class EchoModel:
    """Deterministic stub for tests and offline examples — no keys, no network."""

    name: str
    reply: str

    def complete(self, prompt: str) -> str:
        return self.reply
