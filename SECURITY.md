# Security

Quorum is designed so that **no secret can ever reach a place git, a browser, a
URL, or a log file can see it.**

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

Every local CLI is spawned with a **scrubbed environment**
(`quorum.adapters.scrubbed_env`): variables whose names look secret-bearing
(`*_API_KEY`, `*_TOKEN`, `*_SECRET`, provider prefixes, …) are removed before
the child process starts. This closes the subprocess env-inheritance leak — a
local tool can never read keys configured for Quorum's API providers.

## Allowlisting

Local mode runs **only** the models in `quorum/providers/catalog.py`. There is
no arbitrary-PATH fallback, so a crafted request cannot make the local server
execute an unexpected binary.

## Web server

- Binds `127.0.0.1` by default.
- `POST /api/runs` requires a custom `X-Quorum-CSRF` header and a localhost
  `Origin`/`Referer`. A cross-origin page cannot set that header without a CORS
  preflight Quorum never grants, which blocks browser-driven CSRF.
- Prompts are sent in a POST body and streamed back via an unguessable,
  one-shot `run_id` — never in a query string (which would land in access logs,
  browser history, and proxies).

## Transcripts are sensitive

A replayable transcript contains the prompts and model answers. Treat exported
transcripts as you would the underlying content; don't paste them somewhere
public without reviewing them.

## Reporting

Found a leak vector? Open a minimal private report rather than a public issue
with a live key. Rotate any key you suspect was exposed immediately.
