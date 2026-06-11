# Security

Quorum is designed so that **no secret can ever reach a place git, a browser, a
URL, or a log file can see it.**

## Trust model: Quorum is a LOCAL app

The web UI binds `127.0.0.1` and gates state-changing POSTs with a custom
`X-Quorum-CSRF` header + a localhost Origin check. That is a **local-application**
trust model — it stops cross-origin browser CSRF, **not** an authenticated public
service. **Do not expose the Quorum server to the internet** as-is; it has no user
auth. Run it locally.

## Research uploads are demo-only (for now)

File uploads are treated as **hostile, untrusted content**. To keep that safe,
research-mode uploads run **only the keyless demo models** — local CLIs are *not*
used over uploaded documents, because a malicious file could otherwise prompt-
inject a tool-capable agent. (Normal deliberation mode still uses your local
models; the upload path does not.) Uploads are validated by content sniffing
— not by extension or `Content-Type` — held in memory only, capped, and evicted
on a TTL; nothing is written to disk.

## Key handling

- **Local mode (default, zero keys).** Quorum drives your already-authenticated
  CLIs (`claude`, `gemini`, etc.). They handle their own auth (OAuth / config in
  `~/.claude` etc.). Quorum never sees, stores, or transmits a key in this mode.
- **API mode (Stage 2).** Provider keys come **only** from environment variables
  (or an `.env` file, which is gitignored). Quorum:
  - never writes a key to a file inside the repo;
  - never returns a key to the browser;
  - never logs a key;
  - never puts a key (or a prompt) in a URL.
- `/api/capabilities` returns **booleans and metadata only** — which providers
  are configured, never their values.

## Subprocess isolation

Every local CLI is spawned with a **minimal allowlisted environment**
(`quorum.adapters.scrubbed_env`): only basic process variables such as `PATH`,
`HOME`, locale, temp dirs, and XDG config/cache paths are retained. Secret-like
names and common credential carriers (`*_API_KEY`, `*_TOKEN`, `*_SECRET`,
`*_URL`, `*_DSN`, `SSH_AUTH_SOCK`, package-index URLs, provider prefixes, …)
are removed before the child process starts. This closes the subprocess
env-inheritance leak — a local tool cannot read keys configured for Quorum's API
providers or credentials embedded in local service URLs.

## Allowlisting

Local mode runs **only** the models in `quorum/providers/catalog.py`. There is
no arbitrary-PATH fallback, so a crafted request cannot make the local server
execute an unexpected binary.

Before a local model can run from the web UI, Quorum probes it with the same
audition gate exposed by `quorum auth doctor`. The model must reply with the
exact canary and no extra text. Agentic CLIs are listed but disabled by default
and require explicit opt-in for a run.

## Web server

- Binds `127.0.0.1` by default.
- `POST /api/runs` requires a custom `X-Quorum-CSRF` header and a localhost
  `Origin`/`Referer`. A cross-origin page cannot set that header without a CORS
  preflight Quorum never grants, which blocks browser-driven CSRF.
- `GET /api/capabilities` returns static metadata only. It never probes live
  CLIs or spawns subprocesses.
- Prompts are sent in a POST body and streamed back via an unguessable,
  one-shot `run_id` — never in a query string (which would land in access logs,
  browser history, and proxies).

Some local CLIs only accept a single-turn prompt as a command-line argument.
For those catalog entries, the prompt can be visible to same-user local process
inspection while the CLI is running. Do not put secrets in prompts unless you
accept that local host limitation.

## Transcripts are sensitive

A replayable transcript contains the prompts and model answers. Treat exported
transcripts as you would the underlying content; don't paste them somewhere
public without reviewing them.

## Reporting

Found a leak vector? Open a minimal private report rather than a public issue
with a live key. Rotate any key you suspect was exposed immediately.
