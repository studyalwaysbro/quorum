"""Security invariants — offline, no network, no real keys.

Proves the Stage-1 controls actually hold: env scrubbing, the allowlist,
the audition gate, and that no key-like value can surface in a deliberation.
"""

import os
import sys

from quorum.adapters import CLIModel, scrubbed_env
from quorum.providers import audit_output, build_local_model, get_spec


# ---- env scrubbing -----------------------------------------------------
def test_scrubbed_env_removes_secret_vars_keeps_path():
    base = {
        "PATH": "/usr/bin", "HOME": "/home/x",
        "ANTHROPIC_API_KEY": "sk-secret", "OPENAI_API_KEY": "sk-other",
        "MY_TOKEN": "t", "DB_PASSWORD": "p", "SOME_SECRET": "s",
        "DATABASE_URL": "postgres://u:p@localhost/db",
        "REDIS_URL": "redis://:p@localhost/0",
        "PIP_INDEX_URL": "https://u:p@example/simple",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "HARMLESS": "ok",
    }
    out = scrubbed_env(base)
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/home/x"
    assert "HARMLESS" not in out
    for leaky in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MY_TOKEN", "DB_PASSWORD",
        "SOME_SECRET", "DATABASE_URL", "REDIS_URL", "PIP_INDEX_URL",
        "SSH_AUTH_SOCK",
    ):
        assert leaky not in out, f"{leaky} should have been scrubbed"


def test_cli_subprocess_cannot_see_secret_env():
    """End-to-end: a real child process must not receive key-like vars."""
    os.environ["QUORUM_TEST_SECRET_KEY"] = "LEAKME-XYZ"
    os.environ["DATABASE_URL"] = "postgres://u:LEAKME-DB@localhost/db"
    try:
        dump = CLIModel(
            "envdump",
            [sys.executable, "-c",
             "import os;print('\\n'.join(f'{k}={v}' for k,v in os.environ.items()))"],
            prompt_transport="stdin",
        ).complete("ignored")
        assert "LEAKME-XYZ" not in dump
        assert "LEAKME-DB" not in dump
        assert "QUORUM_TEST_SECRET_KEY" not in dump
        assert "DATABASE_URL" not in dump
        assert "PATH=" in dump   # but the harmless env still passes through
    finally:
        del os.environ["QUORUM_TEST_SECRET_KEY"]
        del os.environ["DATABASE_URL"]


# ---- allowlist (no arbitrary PATH exec) --------------------------------
def test_unknown_local_model_is_rejected():
    for bad in ("rm", "ls", "totally-unknown", "../evil"):
        try:
            get_spec(bad)
        except KeyError:
            continue
        raise AssertionError(f"{bad} should not resolve")


def test_known_models_build():
    m = build_local_model("claude")
    assert m.prompt_transport == "arg"
    assert list(m.argv)[:2] == ["claude", "-p"]


# ---- audition gate -----------------------------------------------------
def test_audition_passes_canary():
    a = audit_output("good", "QUORUM_OK")
    assert a.ok


def test_audition_rejects_canary_with_extra_output():
    a = audit_output("agentic", "QUORUM_OK\nI can use tools in /home/x.")
    assert not a.ok
    assert "context" in a.reason.lower()


def test_audition_rejects_clean_but_wrong_canary():
    a = audit_output("chatty", "QUORUM_OK plus commentary")
    assert not a.ok
    assert "extra" in a.reason.lower()


def test_audition_quarantines_operator_context_leak():
    polluted = "Sure — I wrote the council log to /home/x/workspace/logs and await approval."
    a = audit_output("deepseek", polluted)
    assert not a.ok
    assert "context" in a.reason.lower()


def test_audition_quarantines_unauthenticated_cli():
    a = audit_output("grok", "Signing in with Grok... open this URL to sign in: https://auth.x.ai/...")
    assert not a.ok
    assert "auth" in a.reason.lower()


def test_audition_flags_cli_error_strings():
    a = audit_output("x", "[quorum CLIModel error: x command not found]")
    assert not a.ok
