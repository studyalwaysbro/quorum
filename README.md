# Quorum

**A multi-model deliberation engine.** Ask a panel of language models a
question and run them through a *structured deliberation* — not a naive
fan-out, not a majority vote.

```python
from quorum import Council, CLIModel

council = Council(
    members=[CLIModel("deepseek", ["deepseek"]),
             CLIModel("gpt-5.5", ["codex", "--quiet"]),
             CLIModel("gemini",  ["gemini", "-p"])],
    skeptic=None,          # optional adversarial-only member
)
verdict = council.ask("Which sort fits a nearly-sorted 10k-element list?")
print(verdict.answer)
print(verdict.transcript.to_json())   # every turn, replayable
```

No API keys to try it — the offline demo runs on stub models:

```bash
python examples/01_offline_demo.py
```

---

## Why not just ask three models and take the majority?

Because naive multi-model voting is often *worse* than a single good model,
and it took running a lot of these to see why. Quorum bakes in four lessons:

### 1. Answer blind first, or you've already lost
If model B sees model A's answer before responding, B **anchors** to it. The
independence that makes a panel worth more than one model evaporates — you get
herd-following and sycophancy dressed up as agreement. Quorum's first round is
strictly blind: every model answers alone, seeing nothing.

### 2. Disagreement is the signal — don't average it away
A 3-to-1 split isn't noise to be smoothed into a bland mean. It's the most
valuable thing the council produces: it marks exactly where the problem is
genuinely hard or underspecified. The critique round **surfaces** divergence
instead of collapsing it.

### 3. A skeptic must be told to break things, not help
A model asked to "improve" an answer behaves completely differently from one
asked to "refute" it. Folding skepticism into a general critique gets you
neither. Quorum runs the adversary as its own role with one job: find the
wrong assumption, the missed edge case, the failure mode — and only concede if
the answer genuinely survives.

### 4. Synthesis reads the whole transcript, including the objection
The final answer isn't the most popular blind answer. One synthesizer reads
every turn — answers, critiques, and the adversary's objection — and must
either incorporate the objection or explain why it doesn't hold. Where the
council truly disagreed, the synthesis says so rather than papering over it.

---

## The protocol

```
                  ┌─────────── blind round ───────────┐
   question ───►  │  each model answers independently  │
                  │      (no sight of the others)      │
                  └──────────────────┬─────────────────┘
                                     ▼
                  ┌────────── critique round ──────────┐
                  │  each model sees all answers        │
                  │  (anonymized) and names where they  │
                  │  agree / diverge / who's strongest  │
                  └──────────────────┬─────────────────┘
                                     ▼
                  ┌──────── adversarial round ─────────┐
                  │  a dedicated skeptic tries to       │
                  │  refute the emerging consensus      │
                  └──────────────────┬─────────────────┘
                                     ▼
                  ┌────────── synthesis round ─────────┐
                  │  one model reads the FULL           │
                  │  transcript → final answer          │
                  └──────────────────┬─────────────────┘
                                     ▼
                              Verdict(answer, transcript)
```

Rounds are just functions (`quorum/rounds.py`). Compose your own protocol if
this one doesn't fit your problem.

---

## Bring your own models

Quorum never imports a provider SDK. An adapter is anything that turns a
string into a string:

| Adapter | Use it for |
|---|---|
| `CLIModel(name, argv)` | command-line tools that read stdin (`deepseek`, `codex`, `gemini`, `ollama run …`) |
| `CallableModel(name, fn)` | an HTTP client you've already written |
| `EchoModel(name, reply)` | deterministic stub for tests / offline demos |

Writing a new one is ~5 lines — see `quorum/adapters/base.py`.

---

## Install & test

```bash
pip install -e .
python -m pytest          # offline, no keys needed
```

## Status

v0.1 — core protocol, adapters, replayable transcripts, offline tests.
Rounds run sequentially; parallel fan-out is a one-function change in
`rounds.py`. Issues and PRs welcome.

## License

MIT
