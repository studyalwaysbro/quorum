"""Model adapters.

An adapter is anything that turns a prompt into a text completion. The
interface is deliberately tiny so you can wrap an HTTP API, a local model,
or a CLI subprocess in a few lines. Quorum never imports a provider SDK
itself — you bring your own.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence


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
    argv: Sequence[str]
    timeout: float = 600

    def complete(self, prompt: str) -> str:
        if not self.argv:
            return f"[quorum CLIModel error: {self.name} has no command argv]"
        try:
            result = subprocess.run(
                self.argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except FileNotFoundError:
            return (
                f"[quorum CLIModel error: {self.name} command not found: "
                f"{self.argv[0]!r}]"
            )
        except subprocess.TimeoutExpired as exc:
            details = _trim_output(exc.stderr or exc.stdout)
            suffix = f"; output: {details}" if details else ""
            return (
                f"[quorum CLIModel error: {self.name} timed out after "
                f"{self.timeout}s running {shlex.join(self.argv)}{suffix}]"
            )
        except OSError as exc:
            return f"[quorum CLIModel error: {self.name} failed to start: {exc}]"

        if result.returncode != 0:
            details = _trim_output(result.stderr or result.stdout)
            suffix = f": {details}" if details else ""
            return (
                f"[quorum CLIModel error: {self.name} exited "
                f"{result.returncode}{suffix}]"
            )
        return result.stdout.strip()


def _trim_output(output: str | bytes | None, limit: int = 500) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        output = output.decode(errors="replace")
    return output.strip()[:limit]


@dataclass
class EchoModel:
    """Deterministic stub for tests and offline examples — no keys, no network."""

    name: str
    reply: str

    def complete(self, prompt: str) -> str:
        return self.reply
