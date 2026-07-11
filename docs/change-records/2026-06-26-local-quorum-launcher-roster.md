# Local Quorum Launcher And Roster Repair

Date: 2026-06-26
Status: implemented

## What Changed

- `quorum ask` accepts catalog model IDs such as `--member deepseek`,
  `--member gpt-5.5`, and `--skeptic grok` in addition to explicit
  `name=argv` specs.
- The Codex/GPT-5.5 catalog command now includes `--skip-git-repo-check`, so it
  can run from `/home/yeeterson` and other non-git working directories.
- The installed `~/.local/bin/quorum` and `~/.local/bin/council-classic`
  launchers now pass `--skip-git-repo-check` to Codex.
- Gemini stays in the allowlist only as a detectable retired CLI, but is no
  longer enabled by default; the audition gate continues to quarantine it with
  `IneligibleTierError`.
- README, security notes, real-model examples, and tests were updated away from
  stale `codex --quiet` and Gemini-member examples.

## Why

The local council path was failing for three separate reasons: stale callers
could pass bare member IDs that the CLI rejected, Codex refused to run outside a
trusted git repository, and Gemini remained documented/default-enabled despite
the local CLI being retired. The result was a degraded or non-starting council
rather than a formal Quorum run.

## Default Behavior

The installed `council` command still delegates to Quorum. Its policy roster is
DeepSeek + Codex/GPT-5.5 as blind-round members, Grok as adversarial-only, and
DeepSeek as synthesizer. Gemini is intentionally absent from the launcher roster
and disabled in local UI defaults.

## Guard Or Flag

- Codex non-git execution is controlled by `--skip-git-repo-check`.
- Local web UI agentic models remain disabled by default and require explicit
  opt-in.
- Failed local CLIs remain blocked by `quorum auth doctor` / the audition gate.

## Verification

- `.venv/bin/pytest -q tests/test_cli.py tests/test_web.py` passed.
- `.venv/bin/pytest -q` passed with 111 tests.
- `quorum auth doctor --timeout 45` reported OK for Claude, Grok,
  GPT-5.5/Codex, and DeepSeek; Gemini remained quarantined with
  `IneligibleTierError`.
- `quorum ask "Reply exactly: CATALOG_ID_OK" --member gpt-5.5 --timeout 45
  --quiet` returned `CATALOG_ID_OK`, proving catalog-ID member syntax works with
  the Codex git-check fix.
- `timeout 150 council "What is 2+2? Answer exactly one line, no explanation."`
  returned `4`. The transcript at
  `~/.local/state/council-logs/quorum-20260626-153233.json` includes DeepSeek
  and Codex blind rounds, DeepSeek and Codex critique rounds, a deterministic
  consensus map, Grok adversary, and DeepSeek synthesis.

## Rollback

- Revert the source/docs/test edits in this change.
- Remove `--skip-git-repo-check` from `~/.local/bin/quorum` and
  `~/.local/bin/council-classic`.
- Re-enable Gemini defaults in `quorum/providers/catalog.py` only if a newer
  explicit decision says the local Gemini CLI is usable again.

## Files And Systems Touched

- `quorum/cli.py`
- `quorum/providers/catalog.py`
- `quorum/council.py`
- `README.md`
- `SECURITY.md`
- `examples/02_real_models.py`
- `tests/test_cli.py`
- `tests/test_web.py`
- `CHANGELOG.md`
- `docs/change-records/2026-06-26-local-quorum-launcher-roster.md`
- `~/.local/bin/quorum`
- `~/.local/bin/council-classic`
