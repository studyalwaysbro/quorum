"""Model adapters.

An adapter is anything that turns a prompt into a text completion. The
interface is deliberately tiny so you can wrap an HTTP API, a local model,
or a CLI subprocess in a few lines. Quorum never imports a provider SDK
itself — you bring your own.
"""

from __future__ import annotations

import os
import re
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


# Env-var names whose VALUES are likely secrets. We strip these before running
# any local CLI subprocess so that keys configured for Quorum's API providers
# can NEVER leak into a child process's environment. This is the subprocess
# env-inheritance leak vector: without it, `os.environ` (including every
# provider key) is handed to every CLI we spawn.
_SECRET_ENV_RE = re.compile(
    r"(?i)(API[_-]?KEY|ACCESS[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|"
    r"CREDENTIAL|PRIVATE[_-]?KEY|"
    r"^(ANTHROPIC|OPENAI|GEMINI|GOOGLE_API|GOOGLE_GENAI|DEEPSEEK|XAI|GROK|"
    r"CLAUDE|MISTRAL|COHERE|PERPLEXITY|OPENROUTER|HF|HUGGINGFACE)_)"
)


def scrubbed_env(base: dict | None = None) -> dict:
    """Return a copy of the environment with secret-bearing vars removed.

    Local CLIs authenticate via their own OAuth/config (e.g. ``~/.claude``),
    not via Quorum-managed API keys — so removing key-like vars is safe for
    them and closes the leak path.
    """
    source = os.environ if base is None else base
    return {k: v for k, v in source.items() if not _SECRET_ENV_RE.search(k)}


@dataclass
class CLIModel:
    """Wrap a command-line tool as a council model.

    ``prompt_transport`` controls how the prompt reaches the tool:
      * ``"stdin"`` — piped to stdin (e.g. ``deepseek``, ``codex exec``)
      * ``"arg"``   — appended as the final CLI argument (e.g. ``gemini -p``,
        ``claude -p``, ``grok -p``)

    The subprocess always runs with a :func:`scrubbed_env`, so provider keys
    set for Quorum's API mode never reach the child process.
    """

    name: str
    argv: Sequence[str]
    timeout: float = 600
    prompt_transport: str = "stdin"

    def complete(self, prompt: str) -> str:
        if not self.argv:
            return f"[quorum CLIModel error: {self.name} has no command argv]"
        argv = list(self.argv)
        run_kwargs = dict(
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=scrubbed_env(),
        )
        if self.prompt_transport == "arg":
            argv = argv + [prompt]
        else:
            run_kwargs["input"] = prompt

        try:
            result = subprocess.run(argv, **run_kwargs)
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
                f"{self.timeout}s running {shlex.join(list(self.argv))}{suffix}]"
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
