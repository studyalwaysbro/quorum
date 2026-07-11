# Secure Attachment Research

Date: 2026-07-11
Status: implemented

## Changed

- Added bounded local extraction for text/Markdown, CSV/TSV, JSON, text PDFs,
  and OCR-ready PNG/JPEG/WebP images.
- Added CLI `quorum research` and GUI Research remote mode with explicit,
  per-run provider egress consent.
- Added fixed-host, stateless, tool-free OpenAI, DeepSeek, and xAI adapters.
- Added local credential redaction, advisory injection-language findings,
  attachment-safe Claim Ledger export, and public-bind refusal.

## Why

Users need document and data context, but the existing local CLI models retain
OAuth/home access and may run tools. A hostile document can prompt-inject those
agents; a warning or delimiter does not create a security boundary. Attachment
analysis therefore bypasses all agentic CLIs and uses only stateless API calls
that expose no Quorum tools or host filesystem.

## Default Behavior And Controls

- Demo research stays local.
- Real research is two-phase. Preparation performs local extraction/redaction
  and returns a preview plus SHA-256 manifest binding question, chunks, and
  exact providers/models. A separate approval of that exact hash enables egress.
- Likely secrets are redacted locally. CLI `--allow-sensitive` is the explicit
  dangerous one-run override; GUI has no override.
- PDFs/images require the optional `research` extra; images are OCR-only.
- Local agentic attachment analysis remains unavailable until a real OS sandbox
  and exfiltration test suite exist.

## Security Boundaries

- Fixed HTTPS endpoints, redirects disabled, no arbitrary base URL, tools,
  provider file IDs, or conversation storage.
- PDF/OCR parsers run in a volume-free container with networking disabled,
  read-only root, no capabilities, no-new-privileges, unprivileged UID, and
  CPU/RSS/process/file/time limits; support fails closed without it.
- Bounded input/output and sanitized errors that do not echo keys/prompts.
- Aggregate prepared UTF-8 input is capped before provider construction;
  provider output tokens and pooled fact-check calls are hard bounded.
- Extension+signature checks; regular-file/no-symlink CLI reads; no URLs,
  archives, office formats, active PDF constructs, or recursive extraction.
- Deterministic citation validation controls claim support. Injection/DLP
  matching is advisory and must never be described as comprehensive.
- GUI redacted chunks are memory-only, globally capped, TTL-bound, and one-shot.
  CLI ledger exports are mode `0600`; raw research transcripts are not exported.

## Verification

- Focused unit/API tests cover parsers and caps, spoofed/archive inputs,
  symlinks, active PDF markers, DLP/redaction, consent, fixed hosts, redirect/
  response limits, secret-safe errors, and remote/local trust-mode enforcement.
- Full project test suite and packaging smoke test are required before release.
- A live harmless-PDF prepare/approve smoke completed against DeepSeek V4 Pro
  and wrote the resulting Claim Ledger mode `0600`.
- Independent architecture and security reviews both required keeping local
  agentic CLIs fail-closed; that finding shaped the implementation.

## Rollback

- Revert the attachment/remote modules, CLI `research` subcommand, research GUI
  mode, optional dependency extra, related docs, and tests.
- Existing demo-only text research remains the safe fallback.
- Do not enable local CLI attachment analysis as a rollback shortcut.

## Files And Systems Touched

- `quorum/research/attachments.py`, `quorum/providers/remote.py`
- `quorum/cli.py`, `quorum/web/server.py`, `quorum/web/static/index.html`
- `quorum/web/__main__.py`, `quorum/research/__init__.py`, `pyproject.toml`
- attachment/remote/CLI/web tests, `README.md`, `SECURITY.md`, `CHANGELOG.md`
