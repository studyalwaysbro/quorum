# Security

Quorum minimizes secret exposure and fails closed at trust boundaries. No
pattern scanner can guarantee that arbitrary documents contain no secrets or
prompt injection; users must review sensitive inputs before authorizing remote
analysis.

## Trust model: Quorum is a LOCAL app

The web UI binds `127.0.0.1` and gates state-changing POSTs with a custom
`X-Quorum-CSRF` header + a localhost Origin check. That is a **local-application**
trust model — it stops cross-origin browser CSRF, **not** an authenticated public
service. **Do not expose the Quorum server to the internet** as-is; it has no user
auth. Run it locally. The launcher refuses non-loopback binding unless the
operator supplies `--insecure-public-bind` explicitly.

## Attachment research trust boundary

File uploads are **hostile, untrusted content**. Attachment mode never invokes
the local Codex, Grok, DeepSeek, Claude, or arbitrary command adapters: those
processes retain authentication and may have filesystem/tools, so environment
scrubbing alone is not a sandbox. Real attachment analysis instead uses
audited fixed-host HTTPS or exact-loopback adapters with no tool definitions,
no conversation/file IDs, no redirects, bounded requests/responses, and
explicit per-run egress consent.

Before egress, Quorum validates extension plus file signature, enforces byte,
file, character, row/column, page, pixel, frame, and timeout caps, rejects
archives/office/executables/RTF and active PDF constructs, normalizes supported
content to text, and redacts likely credentials locally. PDF/image parsing uses
optional third-party libraries inside a volume-free container with networking
disabled, a read-only root, no capabilities, no-new-privileges, an unprivileged
UID, and CPU/RSS/process/file/time caps. No host home or Docker socket is mounted.
The transient parser container uses Docker's `none` log driver so extracted
text is not retained in daemon container logs.
PDF/image support fails
closed when that sandbox is unavailable. Images are OCR-only and EXIF is not sent. Prompt-injection
language is flagged but intentionally not "removed": scanners are incomplete;
the actual control is that downstream models have no Quorum tools or host access.

The web registry holds redacted chunks in memory with a TTL/global cap and
one-shot run ID. Both surfaces require two phases: local preparation returns a
bounded preview plus a SHA-256 manifest binding the redacted chunks, exact
providers/models, and question; only a second approval of that hash enables
egress. The CLI rejects symlinks and non-regular files and does not send
original paths. Neither path accepts URLs, directories, globs, DOCX/XLSX, or
archives. Claim Ledgers validate quoted citations deterministically; that
protects output integrity, not provider confidentiality.

## Key handling

- **Local mode (default, zero keys).** Quorum drives your already-authenticated
  CLIs (`claude`, `deepseek`, `codex`, etc.). They handle their own auth (OAuth / config in
  `~/.claude` etc.). Quorum never sees, stores, or transmits a key in this mode.
- **Stateless attachment API mode.** Provider keys come **only** from environment variables
  (or an `.env` file, which is gitignored). Quorum:
  - never writes a key to a file inside the repo;
  - never returns a key to the browser;
  - never logs a key;
  - never puts a key (or a prompt) in a URL.
- `/api/capabilities` returns **booleans and metadata only** — which providers
  are configured, never their values.

## Declarative provider profiles

User model profiles select only a compiled provider adapter plus an exact model,
reasoning request, safe label, and—only for OpenRouter—one upstream slug. The
mode-`0600`, current-user-owned JSON file is bounded, duplicate-key rejecting,
strict-schema validated, non-symlink, and atomically replaced. Unknown fields
fail closed. It cannot contain endpoint URLs, credentials, credential-variable
names, headers, cookies, query parameters, templates, commands, imports, hooks,
tools, plugins, file IDs, storage flags, or arbitrary response parsers.

Compiled adapters currently cover direct OpenAI, DeepSeek, xAI, Kimi, and Z.AI
HTTPS origins; OpenRouter's fixed HTTPS origin; and the exact Ollama loopback
origin `http://127.0.0.1:11434` for ordinary councils only. Ollama/loopback is
code-blocked from attachment research because a port does not prove the server
is stock Ollama, tool-free, filesystem-free, or non-persistent. Arbitrary
public, LAN, private, link-local, or
metadata endpoints are not supported, so an installed profile cannot expand
the network allowlist. Ollama never receives an Authorization header. Remote
HTTPS calls explicitly disable ambient proxy handlers, reject redirects, require
JSON responses, and retain the existing time/request/response/output limits.

OpenRouter requests name one `only` upstream, disable fallbacks, require
parameter support, deny data collection, and require ZDR. These are requested
routing controls, not cryptographic proof of the physical inference host; the
GUI and manifest identify OpenRouter as a routed provider. Dynamic/automatic
model aliases are rejected. All adapters require a nonempty response `model`
that exactly matches the request before any response enters a council.

Regular remote councils require explicit CLI or GUI egress consent because the
protocol intentionally shares the question and model outputs between selected
members during critique/synthesis. Attachment manifests bind each recipient's
rounds, exact model, compiled endpoint/protocol/trust class, output and reasoning
policy, router policy, research-prompt policy, and a canonical profile
egress snapshot hash. CLI execution resolves once, compares that immutable
object with the approved manifest, and constructs from the same object. GUI
prepare/consent stores the reviewed immutable objects and constructs from those,
so a concurrent config replacement cannot change the approved route.

Reasoning settings are reported as requested but unverified. Quorum can prove
which JSON it sent, not that a provider actually honored the setting internally.
Provider-profile research uses manifest version 2; version-1 manifests must be
prepared and reviewed again because they lack the expanded egress snapshot.

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

- Binds `127.0.0.1` by default and refuses non-loopback without an explicit
  unsafe override.
- `POST /api/runs` requires a custom `X-Quorum-CSRF` header and a localhost
  `Origin`/`Referer`. A cross-origin page cannot set that header without a CORS
  preflight Quorum never grants, which blocks browser-driven CSRF.
- `GET /api/capabilities` returns static metadata only. It never probes live
  CLIs or spawns subprocesses.
- Prompts are sent in a POST body and streamed back via an unguessable,
  one-shot `run_id` — never in a query string (which would land in access logs,
browser history, and proxies).
- Research SSE responses use `Cache-Control: no-store`; remote research requires
  a configured allowlisted provider plus approval of the exact prepared hash;
  the full redacted manifest is displayed and downloadable before approval.

Some local CLIs only accept a single-turn prompt as a command-line argument.
For those catalog entries, the prompt can be visible to same-user local process
inspection while the CLI is running. Do not put secrets in prompts unless you
accept that local host limitation.

## Transcripts are sensitive

A replayable transcript contains the prompts and model answers. Treat exported
transcripts as you would the underlying content; don't paste them somewhere
public without reviewing them.

Attachment research returns/exports only the Claim Ledger by default. Its raw
source-bearing internal transcript is discarded with the in-memory run. Ledger
quotes may still contain document content, so ledger files are sensitive; CLI
exports are created with mode `0600`.

## Reporting

Found a leak vector? Open a minimal private report rather than a public issue
with a live key. Rotate any key you suspect was exposed immediately.
