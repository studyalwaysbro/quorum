---
title: Secure Model Layer Hardening After Public Review
projects:
  - quorum
kind: change-record
status: active
---

# Secure Model Layer Hardening After Public Review

## Changed

- Replaced subprocess environment inheritance with a minimal allowlisted env for local CLIs.
- Removed live CLI probing from `GET /api/capabilities`; it now returns metadata only.
- Enforced local-model audition during `POST /api/runs` before a run id is issued.
- Required explicit `allow_agentic` opt-in before agentic catalog models can run from the web UI.
- Tightened the audition canary so only exact `QUORUM_OK` passes.
- Updated the browser UI to default-select only non-agentic enabled local models and confirm agentic opt-in.
- Taught the CLI to infer catalog prompt transport for known commands such as `gemini -p`.
- Expanded gitignore coverage for local env and transcript/vote artifacts.
- Updated README and SECURITY public-facing claims.

## Why

- Public security review found that name-based env scrubbing missed secret-bearing values in `DATABASE_URL`, package-index URLs, and `SSH_AUTH_SOCK`.
- A GET endpoint could spawn local CLIs via `probe_live=true`, which made a read endpoint stateful and externally triggerable.
- Agentic catalog models were marked disabled-by-default but could still be run by a crafted local POST without an explicit opt-in.
- The canary accepted extra output, allowing a tool-like CLI to pass while still emitting context.

## Scope / Files

- `quorum/adapters/base.py`
- `quorum/providers/audition.py`
- `quorum/providers/__init__.py`
- `quorum/web/server.py`
- `quorum/web/static/index.html`
- `quorum/cli.py`
- `.gitignore`
- `README.md`
- `SECURITY.md`
- `tests/test_security.py`
- `tests/test_web.py`
- `tests/test_cli.py`

## Default Behavior / Controls

- Changed by default: yes.
- Local CLI subprocesses now receive only safe process env vars.
- `GET /api/capabilities` cannot probe or spawn local CLIs.
- Web local mode now requires successful audition before run creation.
- Agentic local models require `allow_agentic: true` in the CSRF-protected `POST /api/runs` request.
- Browser default selection excludes agentic and disabled-by-default models.

## Verification

- Run full offline suite with `.venv/bin/pytest -q`.
- Re-run explicit repros:
  - secret-bearing env names are absent from a child envdump;
  - `GET /api/capabilities?probe_live=true` does not call `probe`;
  - agentic local model POST without opt-in is rejected;
  - failed audition does not return raw provider/auth output to the browser;
  - exact canary is required.

## Risk / Rollback

- Risk: some local CLIs may depend on environment variables outside the minimal allowlist, especially custom proxy or package-manager settings.
- Rollback: revert this change set, or intentionally add narrowly reviewed safe env names to `_SAFE_ENV_NAMES` in `quorum/adapters/base.py`.

## Follow-Up

- Prefer stdin transport for `claude`, `gemini`, or `grok` if their CLIs gain a documented single-turn stdin mode, because arg transport can expose prompts to same-user process inspection.
