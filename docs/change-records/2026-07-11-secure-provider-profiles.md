# Secure Provider Profiles

Date: 2026-07-11
Status: implemented and release-verified

## Changed

- Added declarative model profiles for audited OpenAI, DeepSeek, xAI, Kimi,
  Z.AI, OpenRouter, and loopback-only Ollama adapters.
- Added CLI profile management, explicit remote seats for normal councils, GUI
  remote-profile selection/consent, and profile-aware attachment research.
- Added exact provider-reported model equality and version-2 research manifests that
  bind the complete safe provider snapshot and recipient rounds.

## Why

Quorum users do not share one provider or model setup. They need portable model
choice without turning configuration into executable code, exposing credentials,
or letting an uploaded document redirect traffic/tools. The safe first release
therefore supports arbitrary exact model IDs only on reviewed fixed adapters;
arbitrary endpoints remain out of scope.

## Default Behavior And Controls

- Existing local/demo behavior is unchanged.
- Built-in remote profiles are visible but make no call until explicitly used.
- Normal remote councils require `--allow-remote-egress` or the GUI's exact
  recipient confirmation. Research retains prepare/review/hash approval.
- Profiles are owner-only mode `0600`; keys stay in environment variables and
  are never accepted by profile commands or returned as capability metadata.
- OpenRouter requires one upstream and requests no fallback, parameter support,
  denied data collection, and ZDR; provider compliance is labeled unverified.

## Security Boundaries

- No arbitrary endpoints, key slots, headers, templates, commands, imports,
  callbacks, tools, plugins, provider file IDs, conversations, or discovery.
- Compiled origin/protocol/auth policy; redirects and ambient proxies disabled;
  bounded JSON-only responses and secret-safe failures.
- Exact requested/reported model equality; mutable aliases rejected.
- CLI resolves once, compares the immutable resolved profile/policy/recipient
  snapshot, then constructs from that same object. GUI stores and uses the
  immutable reviewed objects. Config swaps cannot alter approved egress.
- Loopback Ollama remains available for ordinary councils but is code-blocked
  from attachments because port identity cannot prove a safe inference process.
- Local agentic CLIs remain forbidden for attachment research. Prompt-injection
  scanning remains advisory; safety comes from tool-free, filesystem-free APIs
  and deterministic citation enforcement.

## Verification

- Unit/API adversarial tests cover strict parsing, duplicate keys, unsafe fields,
  permissions/symlinks, shadowing, credential isolation, routing payloads, model
  spoofing, proxy disablement, JSON enforcement, TOCTOU, GUI consent/CSRF/Host,
  and safe errors.
- Full suite, package build, independent architecture review, independent
  security review, and final diff review are release gates.
- Final gate result: 200 tests passed; JavaScript syntax, bytecode compilation,
  wheel build, clean-venv install, packaged CLI smoke, and three independent
  security/product/adversarial reviews passed. The only warning was the existing
  Starlette/httpx TestClient deprecation warning.

## Risk / Rollback

- Provider APIs can change dialect or model naming; exact reported equality will fail
  closed instead of silently accepting drift.
- OpenRouter routing controls are provider-enforced requests, not remote
  proof of the physical host; the UI labels model/reasoning/routing honestly.
- Roll back by reverting `profiles.py`, the expanded audited remote catalog,
  CLI/web remote-profile paths, manifest v2, docs, and related tests. Existing
  local/demo councils and attachment demo mode remain the safe fallback.

## Files And Systems Touched

- `quorum/providers/profiles.py`, `quorum/providers/remote.py`
- `quorum/cli.py`, `quorum/web/server.py`, `quorum/web/static/index.html`
- `quorum/research/manifest.py`, provider/CLI/web/research/security tests
- `README.md`, `SECURITY.md`, `CHANGELOG.md`
- `~/shared-memory/skills/infra/council-operations.md`
