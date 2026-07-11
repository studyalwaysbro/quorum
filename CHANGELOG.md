# Changelog

## Unreleased

### Added

- Added secure declarative bring-your-own-model profiles for audited OpenAI,
  DeepSeek, xAI, Kimi, Z.AI/GLM, OpenRouter, and loopback-only Ollama adapters.
  Profiles are mode-`0600`, strict-schema, non-executable configuration with no
  key values, endpoints, headers, hooks, tools, or automatic discovery.
- Added `quorum provider list|path|add|remove`, `remote:PROFILE` seats for normal
  CLI councils behind `--allow-remote-egress`, and GUI Remote Profiles mode with
  exact-recipient disclosure and confirmation. OpenRouter profiles require a
  pinned upstream and request no fallback, parameter support, denied data
  collection, and ZDR; these provider controls are labeled unverified.
- Added exact provider-reported model equality checks, compiled endpoint/trust/
  protocol snapshots, recipient-round disclosure, reasoning-honesty metadata,
  and immutable egress snapshot hashes bound into version-2 attachment manifests.

- Added secure attachment research for CLI and the local GUI. Supported inputs
  are bounded TXT/Markdown, CSV/TSV, JSON, text PDFs, and OCR-ready
  PNG/JPEG/WebP images. Local extraction performs strict type/cap checks,
  rejects active PDF/archive/office content, flags injection-like language,
  and redacts likely secrets by default.
- Added fixed-host stateless API adapters for OpenAI GPT-5.6 Sol (`xhigh`
  reasoning), DeepSeek V4 Pro (max thinking), and Grok 4.5 (high reasoning).
  Attachment content is never sent to current local agentic CLIs and remote
  egress requires prepare/review/approve consent bound to an exact manifest
  hash. CLI exports contain only a Claim
  Ledger and are written mode `0600`; web source state is in-memory/TTL-bound.
- Added an optional `research` dependency extra for PDF and OCR extraction and
  isolated those higher-risk parsers in a volume-free, no-network, read-only,
  unprivileged Docker container with CPU/RSS/process/file/time limits. Added
  aggregate UTF-8 request,
  output-token, and pooled fact-check budgets. Also
  made the web launcher refuse non-loopback binding without the explicit
  `--insecure-public-bind` escape hatch.
- Added `logs_*.json` to the secret/artifact ignore rules so ad hoc council
  transcript dumps cannot hitchhike into a broad commit.

### Fixed

- Disabled ambient HTTP proxy handling for credential-bearing remote calls,
  required JSON response content types, hardened local Host/Origin validation,
  and replaced raw SSE exception strings with secret-safe public failures.

- Pinned the reflexive `~/.local/bin/quorum` Codex council seat to
  `gpt-5.6-sol` with `model_reasoning_effort=\"xhigh\"` and corrected the
  transcript member label from stale `gpt-5.5` to `gpt-5.6-sol`. This affects
  only reflexive `quorum \"<question>\"` / `council` runs; the user's global
  Codex default remains unchanged. The controlling path is the explicit Codex
  member command in `~/.local/bin/quorum`; rollback is to remove its `--model`
  and `-c` overrides and restore the old member label. Verified with Bash
  syntax validation, Codex CLI option parsing, and static launcher assertions.
- Fixed local Quorum launcher/roster drift: `quorum ask` now accepts either
  catalog IDs such as `--member deepseek` / `--member gpt-5.5` or explicit
  `name=argv` specs, Codex catalog and shell-launcher paths include
  `--skip-git-repo-check`, and Gemini is disabled by default as a retired
  local CLI that the audition gate quarantines.
- Updated real-model examples and CLI docs away from stale `codex --quiet` and
  Gemini-member snippets; the documented local policy roster is now DeepSeek +
  Codex/GPT-5.5 with Grok adversarial-only.
- Hardened scorecard UI honesty after the `83dcb3b` re-review: the decision
  control now describes blind-vs-post-debate agreement instead of attributing
  movement only to the adversary.
- Final scorecards now display parsed-vote denominators for both blind and
  post-debate stages, so abstentions or unparsed votes are visible on both sides
  of the comparison.
- Example question chips now dispatch the same input event as manual edits,
  clearing the prefilled demo labels before a different question can reuse them.

### Verification

- Ran the full project suite: `200 passed`; the only warning is the existing
  Starlette/httpx TestClient deprecation warning.
- Validated browser JavaScript syntax, compiled the package, built a wheel with
  `uv build`, installed it into a clean temporary virtual environment, and ran
  the packaged `quorum provider list` / CLI help smoke tests.
- Ran three independent post-implementation security/product/adversarial review
  passes. Their blockers drove loopback attachment exclusion, immutable-profile
  construction, descriptor-bound config reads, cached-consent fingerprints,
  active-key output redaction, honest requested/unverified identity language,
  and exact recipient-round receipts. Final verdicts were release-approved.
- Added adversarial profile and remote-routing tests covering schema/duplicate
  key/control-character attacks, mode/owner/symlink enforcement, built-in
  shadowing, credential canaries, response-model spoofing, OpenRouter fallback
  and privacy policy, Ollama Authorization exclusion, proxy poisoning defense,
  profile/manifest TOCTOU, regular-council consent, DNS-rebinding Host headers,
  and secret-safe streamed errors.

- Ran focused attachment, remote-adapter, CLI, and web abuse tests, including
  spoofed types, symlinks, active PDFs, secret redaction, consent gates,
  hash-tamper rejection, parser isolation, request/call/output caps, safe
  errors, and local-agent refusal.
- Ran a live harmless-PDF smoke through the isolated parser and stateless
  DeepSeek V4 Pro prepare/approve path; it produced a validated Claim Ledger
  written mode `0600` without exposing the API key or raw source transcript.

- Ran `.venv/bin/pytest -q tests/test_cli.py tests/test_web.py`.
- Ran `.venv/bin/pytest -q`.
- Ran `quorum auth doctor --timeout 45`; Claude, Grok, GPT-5.5/Codex, and
  DeepSeek passed, while Gemini remained quarantined with `IneligibleTierError`.
- Ran `quorum ask "Reply exactly: CATALOG_ID_OK" --member gpt-5.5 --timeout 45
  --quiet`; it returned `CATALOG_ID_OK`, proving catalog-ID member syntax works
  with the Codex git-check fix.
- Ran `timeout 150 council "What is 2+2? Answer exactly one line, no explanation."`;
  final answer was `4`, with transcript
  `~/.local/state/council-logs/quorum-20260626-153233.json` showing DeepSeek and
  Codex blind/critique rounds, Grok adversary, and DeepSeek synthesis.
- Ran `.venv/bin/pytest -q tests/test_web.py tests/test_scorecard.py`.
- Ran `.venv/bin/pytest -q`.

### Rollback

- Revert `quorum/cli.py`, `quorum/providers/catalog.py`, docs, examples, and
  tests from this entry; restore `~/.local/bin/quorum` and
  `~/.local/bin/council-classic` by removing `--skip-git-repo-check` from their
  Codex invocations.
- Revert the `quorum/web/static/index.html` and `tests/test_web.py` changes in
  this entry to restore the prior scorecard copy and label behavior.
