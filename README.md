# Quorum

**A multi-model deliberation engine.** Ask a panel of language models a
question and run them through a *structured deliberation* — not a naive
fan-out, not a majority vote.

```python
from quorum import Council
from quorum.providers import build_local_model

deepseek = build_local_model("deepseek")
gpt = build_local_model("gpt-5.5")
grok = build_local_model("grok")

council = Council(
    members=[deepseek, gpt],
    skeptic=grok,          # adversarial-only member
    synthesizer=deepseek,
    distiller=None,        # optional semantic consensus-map model
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
instead of collapsing it, and a consensus/issue map records what the critiques
agreed on versus what remains unresolved.

### 3. A skeptic must be told to break things, not help
A model asked to "improve" an answer behaves completely differently from one
asked to "refute" it. Folding skepticism into a general critique gets you
neither. Quorum runs the adversary as its own role with one job: attack the
specific claims and assumptions in the consensus/issue map, find the wrong
assumption, the missed edge case, the failure mode — and only concede if the
answer genuinely survives.

### 4. Synthesis reads the whole transcript, including the objection
The final answer isn't the most popular blind answer. One synthesizer reads
every turn — answers, critiques, the consensus/issue map, and the adversary's
objection — and must either incorporate the objection or explain why it doesn't
hold. Where the council truly disagreed, the synthesis says so rather than
papering over it.

By default, the synthesizer is the first council member for compatibility.
That is convenient, but it can favor the member's own blind answer; prefer a
non-member synthesizer, or the strongest available model, when you can.

---

## The protocol

```
                  ┌─────────── blind round ───────────┐
   question ───►  │  each model answers independently  │
                  │      (no sight of the others)      │
                  └──────────────────┬─────────────────┘
                                     ▼
                  ┌──── optional categorical vote ─────┐
                  │  each member classifies its own     │
                  │  blind answer + confidence          │
                  └──────────────────┬─────────────────┘
                                     ▼
                  ┌────────── critique round ──────────┐
                  │  each model sees all answers        │
                  │  (anonymized) and names where they  │
                  │  agree / diverge / who's strongest  │
                  └──────────────────┬─────────────────┘
                                     ▼
                  ┌────── consensus / issue map ───────┐
                  │  records critique agreement and     │
                  │  unresolved assumptions / splits    │
                  └──────────────────┬─────────────────┘
                                     ▼
                  ┌──────── adversarial round ─────────┐
                  │  a dedicated skeptic tries to       │
                  │  attack map claims / assumptions    │
                  └──────────────────┬─────────────────┘
                                     ▼
                  ┌──── optional revote / flip log ────┐
                  │  members see anonymous map + tally  │
                  │  and may revise once                │
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

By default, the consensus map is deterministic and conservative: only repeated
verbatim critique claims are treated as agreements. Pass `distiller=...` to
`Council` when you want a model to build a semantic map across paraphrases.
Blind and critique fan-out run concurrently by default while transcript turns
are still recorded in member order; pass `max_workers=...` to cap that fan-out.
Critique and adversarial prompts shuffle blind-answer order deterministically
from `seed=...` and record the permutation in transcript metadata.

---

## Web UI — watch it deliberate live

A streaming single-page UI shows each round arrive in real time: blind answers
diverge, get critiqued, get distilled into a consensus map, get attacked by the
skeptic, then synthesized into the final answer.

```bash
pip install -e ".[web]"      # adds fastapi + uvicorn
python -m quorum.web          # then open http://127.0.0.1:8000
```

- **Demo mode** needs no API keys — keyless, round-aware stub models so the whole
  mechanic runs instantly for anyone who clones the repo.
- **Local CLI mode** detects the allowlisted CLIs on the host (`claude`, `grok`,
  `codex`, `deepseek`, plus retired/quarantined entries such as `gemini`) and
  lets you pick which sit on the council and which is the adversary. Each
  selected CLI must pass the audition gate before it can run; agentic CLIs are
  off by default and require explicit opt-in.

The backend creates a run with CSRF-protected `POST /api/runs` and then streams
over Server-Sent Events from `GET /api/runs/{run_id}/events`; prompts are not
placed in URLs. Download the full replayable transcript from the result panel.

---

## Research mode — ground a council in your document

Open the **📄 Research** tab, drop bounded text/Markdown, CSV/TSV, JSON, PDF, or
PNG/JPEG/WebP files, and ask a question. PDFs are text-extracted; images use
OCR, not general visual reasoning. Install optional parsers with
`pip install -e ".[web,research]"` (OCR also needs the `tesseract` executable).
Secure PDF/OCR extraction runs in a volume-free, no-network parser container.
Build it once with `docker build -f docker/parser.Dockerfile -t
quorum-parser:0.1.0 .`; those formats fail closed if the image/runtime is
unavailable rather than parsing inside the credential-bearing Quorum process.
The
council reads the source, answers in **cited claims**, and an adversary
**fact-checks each claim against the source**. The result is a **Claim Ledger**:

- **Kept** — supported by a citation whose quote literally appears in the source
- **Qualified** — partially supported
- **What Quorum refused to conclude** — claims with no citation that checks out

A citation is "valid" only if its quote is a verbatim substring of the cited
chunk, checked deterministically — so a claim can **never** be reported as
supported on a fabricated quote, no matter what the model (or the fact-checker)
says. That auditable refusal to overclaim is the point.

Uploads are treated as untrusted content: types are sniffed, sizes/pages/pixels
are capped, likely secrets are redacted locally, and active PDF content,
archives, office files, symlinks, and special files are rejected. Demo mode is
fully local. Real analysis uses only fixed-host, stateless, tool-free API calls
after explicit per-run provider consent; uploaded content never reaches local
agentic CLIs. Pattern scanning is defense-in-depth, not proof that a document is
benign. See [`SECURITY.md`](SECURITY.md).

The same secure path is available from the CLI:

```bash
# First set one provider credential in your environment:
# DEEPSEEK_API_KEY, OPENAI_API_KEY, or XAI_API_KEY
quorum research "Which claims does this evidence support?" \
  --file report.pdf --file metrics.csv \
  --provider deepseek --prepare review-manifest.json
# Read the printed preview and the mode-0600 manifest, then approve its exact hash:
quorum research "Which claims does this evidence support?" \
  --manifest review-manifest.json --provider deepseek \
  --approve <manifest-sha256> \
  --json claim-ledger.json
```

Detected secrets are redacted by default. `--allow-sensitive` is a dangerous,
prepare-time override. Preparation performs no provider call; execution refuses
any changed question, provider/model selection, chunks, or manifest hash. The
CLI output and optional JSON contain the Claim Ledger, not raw source-bearing
prompts.

---

## Measure your council

Pass labels to `ask()` when you want a measurable categorical decision:

```python
from quorum import Council, EchoModel, RecordStore, summary

store = RecordStore("quorum-votes.jsonl")
members = [
    EchoModel("alice", "VERDICT: insertion\nCONFIDENCE: 4"),
    EchoModel("bob", "VERDICT: timsort\nCONFIDENCE: 5"),
]
council = Council(members=members, store=store)

verdict = council.ask(
    "Best sort for a nearly-sorted 10k-element list?",
    labels=["insertion", "timsort"],
    revote=True,
)
print(verdict.tally, verdict.majority, verdict.flips)

report = summary(store.votes_by_item(), labels=["insertion", "timsort"])
print(report.fleiss_kappa, report.gwet_ac1, report.n_effective)
```

`VoteRecord` JSONL files are append-only artifacts: every labeled ask stores
the question, labels, votes, confidences, optional truth label, and optional
revotes. Feed `RecordStore.votes_by_item()` into `quorum.summary()` to track
raw agreement, pairwise Cohen kappas, Fleiss kappa, Krippendorff alpha, Gwet's
AC1, redundancy, and effective council size over time.

High raw agreement with low kappa is the kappa paradox: skewed label marginals
can make real agreement look weak after chance correction. In that case, check
Gwet's AC1 alongside kappa because AC1 is less brittle under heavy prevalence
skew.

Redundancy is a member's mean pairwise kappa against the rest of the council.
A member at kappa `0.97` against another member is probably a clone: cost
without much new signal. `n_effective` turns average pairwise kappa into an
independent-voter estimate, so a five-member council might only be worth 2.3
independent voters.

## Construct your council

`dawid_skene()` estimates member confusion matrices from stored votes,
`build_roster()` greedily trades off truth accuracy against clone-like kappa,
and `drop_one()` shows whether removing a member helps or hurts majority-vote
accuracy.

```python
ds = dawid_skene(records); roster = build_roster(records, ["alice", "bob", "carol"], size=2)
print(ds.skill, roster.picks, drop_one(records, roster.picks))
```

If no truth labels exist, `build_roster()` falls back to Dawid-Skene skill.
That is useful for bootstrapping, but weaker than supervised accuracy because
it is learned from the same votes it is ranking.

## Reports

`transcript_html()` renders the deliberation replay, while
`council_health_html()` renders the architecture dashboard with agreement,
redundancy, Dawid-Skene skill, drop-one deltas, and a generated discussion.

```python
save(council_health_html(records, roster=roster), "reports/health.html")
save(transcript_html(verdict.transcript, verdict=verdict), "reports/transcript.html")
```

---

## Bring your own models

Quorum never imports a provider SDK. An adapter is anything that turns a
string into a string:

| Adapter | Use it for |
|---|---|
| `CLIModel(name, argv)` | command-line tools that read stdin, or cataloged CLIs with explicit `prompt_transport` |
| `CallableModel(name, fn)` | an HTTP client you've already written |
| `EchoModel(name, reply)` | deterministic stub for tests / offline demos |

Writing a new one is ~5 lines — see `quorum/adapters/base.py`.

## CLI

Install locally and use the same adapter shape from a shell:

```bash
quorum ask "We need to dedupe 50M records by a fuzzy key. What approach, and what breaks first at scale?" --member deepseek --member gpt-5.5 --skeptic grok --persona domain_skeptic --synthesizer deepseek
quorum ask "Is timsort right here?" --member a="python3 -c 'import sys; sys.stdin.read(); print(\"VERDICT: yes\\nCONFIDENCE: 4\")'" --labels yes,no --store quorum-votes.jsonl
quorum health quorum-votes.jsonl --roster-size 2 --html reports/council_health.html
quorum auth doctor
```

Add `--json transcript.json` or `--html transcript.html` to `quorum ask`, and
use `quorum replay transcript.json` for an offline replay.

---

## Install & test

```bash
pip install -e .
python -m pytest          # offline, no keys needed
```

## Status

v0.1 — core protocol, adapters, replayable transcripts, optional semantic
distillation, ordered parallel fan-out, and offline tests.

## License

MIT
