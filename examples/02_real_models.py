"""Wire up real models — two ways.

Quorum never imports a provider SDK. You bring the model; Quorum runs the
protocol. Two common adapters:

1. CLIModel  — wrap a command-line tool that reads stdin, prints a reply.
2. CallableModel — wrap an HTTP client you've already written.

Edit the argv / functions below to match your setup, then run:

    python examples/02_real_models.py
"""

from quorum import CallableModel, CLIModel, Council

# --- Option 1: CLI tools (each reads the prompt on stdin) ---------------
deepseek = CLIModel("deepseek", ["deepseek"])
gpt = CLIModel("gpt-5.5", ["codex", "--quiet"])
gemini = CLIModel("gemini", ["gemini", "-p"])


# --- Option 2: your own HTTP client wrapped as a callable ---------------
def my_http_client(prompt: str) -> str:
    # replace with a real request to whatever endpoint you use
    raise NotImplementedError("plug in your HTTP client here")


custom = CallableModel("my-api", my_http_client)


if __name__ == "__main__":
    council = Council(
        members=[deepseek, gpt, gemini],
        skeptic=None,          # add an adversarial-only model here if you want one
        synthesizer=deepseek,
    )
    verdict = council.ask(
        "We need to dedupe 50M records by a fuzzy key. What approach, and what "
        "breaks first at scale?"
    )
    print(verdict.answer)
