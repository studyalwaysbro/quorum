"""Stateless, tool-free adapters for audited remote completion APIs.

Network destinations, credential slots, protocols, and request dialects are
compiled here. User profiles may select only an exact model/reasoning policy;
they cannot add an endpoint, header, secret, tool, or executable hook.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

from quorum.providers.profiles import (
    ProviderProfile,
    ProviderProfileError,
    load_user_profiles,
)


MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_TIMEOUT = 120.0
MAX_OUTPUT_TOKENS = 4096


@dataclass(frozen=True)
class RemoteProviderSpec:
    id: str
    label: str
    endpoint: str
    default_model: str
    key_env: str | None
    protocol: str
    trust: str
    default_reasoning: str


REMOTE_CATALOG: tuple[RemoteProviderSpec, ...] = (
    RemoteProviderSpec(
        "openai", "OpenAI", "https://api.openai.com/v1/responses",
        "gpt-5.6-sol", "OPENAI_API_KEY", "responses_v1", "curated_https", "xhigh",
    ),
    RemoteProviderSpec(
        "deepseek", "DeepSeek", "https://api.deepseek.com/chat/completions",
        "deepseek-v4-pro", "DEEPSEEK_API_KEY", "chat_v1", "curated_https", "max",
    ),
    RemoteProviderSpec(
        "xai", "xAI", "https://api.x.ai/v1/chat/completions",
        "grok-4.5", "XAI_API_KEY", "chat_v1", "curated_https", "high",
    ),
    RemoteProviderSpec(
        "kimi", "Kimi", "https://api.moonshot.ai/v1/chat/completions",
        "kimi-k2.7-code", "MOONSHOT_API_KEY", "kimi_chat_v1", "curated_https", "enabled",
    ),
    RemoteProviderSpec(
        "zai", "Z.AI", "https://api.z.ai/api/paas/v4/chat/completions",
        "glm-5.1", "ZAI_API_KEY", "zai_chat_v1", "curated_https", "enabled",
    ),
    RemoteProviderSpec(
        "openrouter", "OpenRouter", "https://openrouter.ai/api/v1/chat/completions",
        "", "OPENROUTER_API_KEY", "openrouter_chat_v1", "curated_router_https", "high",
    ),
    RemoteProviderSpec(
        "ollama", "Ollama", "http://127.0.0.1:11434/v1/chat/completions",
        "gpt-oss:20b", None, "ollama_chat_v1", "loopback_http", "high",
    ),
)
_BY_ID = {spec.id: spec for spec in REMOTE_CATALOG}
_BUILTIN_PROFILE_IDS = frozenset({"openai", "deepseek", "xai", "kimi", "zai", "ollama"})


class RemoteModelError(RuntimeError):
    """A deliberately sanitized remote-completion failure."""


def get_remote_spec(provider_id: str) -> RemoteProviderSpec:
    try:
        return _BY_ID[provider_id]
    except KeyError:
        raise KeyError(f"unknown remote provider id: {provider_id!r}") from None


def builtin_profiles() -> tuple[ProviderProfile, ...]:
    return tuple(
        ProviderProfile(
            spec.id, spec.label, spec.id, spec.default_model,
            "provider_default", None, True,
        )
        for spec in REMOTE_CATALOG if spec.id in _BUILTIN_PROFILE_IDS
    )


def provider_profiles(config: Path | None = None) -> tuple[ProviderProfile, ...]:
    builtins = builtin_profiles()
    users = load_user_profiles(config)
    builtin_ids = {profile.id for profile in builtins}
    for profile in users:
        if profile.id in builtin_ids:
            raise ProviderProfileError("user provider profiles cannot shadow built-ins")
        if profile.provider_id not in _BY_ID:
            raise ProviderProfileError(
                f"unknown audited provider adapter: {profile.provider_id}"
            )
        if profile.provider_id == "kimi" and profile.reasoning != "provider_default":
            raise ProviderProfileError("Kimi's audited model uses mandatory provider reasoning")
        if profile.provider_id == "zai" and profile.reasoning not in {"provider_default", "none"}:
            raise ProviderProfileError("Z.AI profiles support provider-default or disabled reasoning")
        if profile.model in {"openrouter/auto", "openrouter/free"} or profile.model.startswith("~"):
            raise ProviderProfileError("mutable or automatic model aliases are not allowed")
        if profile.provider_id == "openrouter" and "/" not in profile.model:
            raise ProviderProfileError("OpenRouter model ids require an organization prefix")
    return builtins + users


def get_provider_profile(profile_id: str, config: Path | None = None) -> ProviderProfile:
    for profile in provider_profiles(config):
        if profile.id == profile_id:
            return profile
    raise KeyError(f"unknown remote provider profile: {profile_id!r}")


def provider_snapshot(
    profile: ProviderProfile, *, model: str | None = None,
    receives: tuple[str, ...] = ("grounded_blind", "fact_check"),
) -> dict:
    spec = get_remote_spec(profile.provider_id)
    chosen_model = profile.model if model is None else _validate_model(model)
    chosen_model = _validate_model(chosen_model)
    reasoning = (
        spec.default_reasoning
        if profile.reasoning == "provider_default" else profile.reasoning
    )
    if spec.id == "kimi" and reasoning != "enabled":
        raise ProviderProfileError("Kimi's audited model uses mandatory provider reasoning")
    if spec.id == "zai" and reasoning not in {"enabled", "none"}:
        raise ProviderProfileError("Z.AI profiles support provider-default or disabled reasoning")
    if spec.id == "openrouter" and "/" not in chosen_model:
        raise ProviderProfileError("OpenRouter model ids require an organization prefix")
    routing = None
    if spec.id == "openrouter":
        if not profile.upstream:
            raise ProviderProfileError("OpenRouter profiles require an exact upstream route")
        routing = {
            "only": [profile.upstream], "allow_fallbacks": False,
            "require_parameters": True, "data_collection": "deny", "zdr": True,
        }
    core = {
        "id": profile.id,
        "label": profile.label,
        "provider": spec.id,
        "model": chosen_model,
        "model_identity_verified": False,
        "endpoint": spec.endpoint,
        "protocol": spec.protocol,
        "trust": spec.trust,
        "reasoning_requested": reasoning,
        "reasoning_verified": False,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "routing_requested": routing,
        "routing_verified": False,
        "receives": list(receives),
    }
    digest = hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    return {**core, "egress_snapshot_hash": digest}


def remote_capabilities(
    env: Mapping[str, str] | None = None, *, config: Path | None = None
) -> list[dict]:
    """Return safe profile metadata and key-presence booleans, never secrets."""
    source = os.environ if env is None else env
    result = []
    for profile in provider_profiles(config):
        spec = get_remote_spec(profile.provider_id)
        snap = provider_snapshot(profile)
        result.append({
            "id": profile.id,
            "label": profile.label,
            "provider": spec.id,
            "default_model": profile.model,
            "model": profile.model,
            "configured": spec.key_env is None or bool(source.get(spec.key_env)),
            "trust": spec.trust,
            "reasoning_requested": snap["reasoning_requested"],
            "reasoning_verified": False,
            "routed": spec.id == "openrouter",
            "upstream": profile.upstream,
            "profile_fingerprint": profile.fingerprint,
        })
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _stdlib_transport(
    request: urllib.request.Request, timeout: float, response_cap: int
) -> bytes:
    # Explicitly ignore ambient proxy and netrc-style handlers. Authorization
    # may only travel directly to the compiled request destination.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        media = response.headers.get_content_type().lower()
        if media != "application/json" and not media.endswith("+json"):
            raise RemoteModelError("remote response was not JSON")
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > response_cap:
                    raise RemoteModelError("remote response exceeded size limit")
            except ValueError:
                pass
        data = response.read(response_cap + 1)
    if len(data) > response_cap:
        raise RemoteModelError("remote response exceeded size limit")
    return data


Transport = Callable[[urllib.request.Request, float, int], bytes]


@dataclass
class RemoteTextModel:
    provider_id: str
    api_key: str
    model: str | None = None
    timeout: float = DEFAULT_TIMEOUT
    response_cap: int = MAX_RESPONSE_BYTES
    transport: Transport = _stdlib_transport
    reasoning: str | None = None
    upstream: str | None = None
    profile_label: str | None = None

    def __post_init__(self) -> None:
        self.spec = get_remote_spec(self.provider_id)
        default_model = self.spec.default_model
        self.requested_model = _validate_model(default_model if self.model is None else self.model)
        self.name = self.requested_model
        self.reasoning = self.reasoning or self.spec.default_reasoning
        if self.reasoning not in {"none", "enabled", "high", "max", "xhigh"}:
            raise RemoteModelError("remote reasoning level is invalid")
        if self.spec.key_env is not None and not self.api_key:
            raise RemoteModelError(f"{self.spec.label} API key is not configured")
        if self.spec.key_env is not None and (
            len(self.api_key) < 8 or len(self.api_key) > 4096
            or any(ord(char) < 0x20 or ord(char) == 0x7f for char in self.api_key)
        ):
            raise RemoteModelError(f"{self.spec.label} API credential is invalid")
        if self.spec.key_env is None and self.api_key:
            raise RemoteModelError("loopback providers do not accept an API key")
        if self.timeout <= 0 or self.response_cap <= 0:
            raise ValueError("timeout and response_cap must be positive")
        self._validate_compiled_endpoint()
        if self.spec.id == "openrouter" and not self.upstream:
            raise RemoteModelError("OpenRouter requires an exact upstream route")
        self.reported_model: str | None = None

    def _validate_compiled_endpoint(self) -> None:
        parsed = urlsplit(self.spec.endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RemoteModelError("remote endpoint policy is invalid")
        if self.spec.trust == "loopback_http":
            if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 11434:
                raise RemoteModelError("loopback endpoint policy is invalid")
            return
        expected = {
            "openai": "api.openai.com", "deepseek": "api.deepseek.com",
            "xai": "api.x.ai", "kimi": "api.moonshot.ai",
            "zai": "api.z.ai", "openrouter": "openrouter.ai",
        }
        if parsed.scheme != "https" or parsed.hostname != expected.get(self.spec.id) \
                or parsed.port not in (None, 443):
            raise RemoteModelError("remote endpoint is not allowlisted")

    def complete(self, prompt: str) -> str:
        payload = self._payload(prompt)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise RemoteModelError("remote request exceeded size limit")
        headers = {
            "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "quorum-council/remote",
        }
        if self.spec.key_env is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.spec.endpoint, data=body, method="POST", headers=headers,
        )
        try:
            raw = self.transport(request, self.timeout, self.response_cap)
            if len(raw) > self.response_cap:
                raise RemoteModelError("remote response exceeded size limit")
            document = json.loads(raw.decode("utf-8"))
            return self._extract(document)
        except RemoteModelError:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            raise RemoteModelError(f"{self.spec.label} request failed") from None
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError,
                TypeError, RecursionError):
            raise RemoteModelError(f"{self.spec.label} returned an invalid response") from None

    def _payload(self, prompt: str) -> dict:
        if self.spec.protocol == "responses_v1":
            payload = {
                "model": self.requested_model, "input": prompt, "store": False,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
            }
            if self.reasoning != "none":
                payload["reasoning"] = {"effort": self.reasoning}
            return payload
        payload = {
            "model": self.requested_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        token_key = "max_completion_tokens" if self.spec.id == "kimi" else "max_tokens"
        payload[token_key] = MAX_OUTPUT_TOKENS
        if self.reasoning != "none":
            if self.spec.id == "deepseek":
                payload.update(thinking={"type": "enabled"}, reasoning_effort=self.reasoning)
            elif self.spec.id == "xai":
                payload["reasoning_effort"] = self.reasoning
            elif self.spec.id == "kimi":
                payload["thinking"] = {"type": "enabled", "keep": "all"}
            elif self.spec.id == "zai":
                payload["thinking"] = {"type": "enabled"}
            elif self.spec.id in {"openrouter", "ollama"}:
                payload["reasoning"] = {"effort": self.reasoning}
        if self.spec.id == "openrouter":
            payload["provider"] = {
                "only": [self.upstream], "allow_fallbacks": False,
                "require_parameters": True, "data_collection": "deny", "zdr": True,
            }
        return payload

    def _extract(self, document: dict) -> str:
        reported = document["model"]
        if not isinstance(reported, str) or reported != self.requested_model:
            raise RemoteModelError("remote provider reported a different model")
        self.reported_model = reported
        if self.spec.protocol == "responses_v1":
            if isinstance(document.get("output_text"), str):
                return self._safe_output(document["output_text"])
            for item in document["output"]:
                for part in item.get("content", []):
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        return self._safe_output(part["text"])
            raise KeyError("output_text")
        text = document["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise TypeError("completion is not text")
        return self._safe_output(text)

    def _safe_output(self, text: str) -> str:
        # A compromised provider may echo the Authorization value in an
        # otherwise valid completion. Strip the exact active credential first,
        # then apply the same deterministic credential-pattern redaction used
        # for attachment egress before any caller can log/export the output.
        if self.api_key:
            text = text.replace(self.api_key, "[REDACTED:ACTIVE_PROVIDER_CREDENTIAL]")
        from quorum.research.attachments import redact_text
        return redact_text(text)


def build_remote_model(
    profile_id: str,
    *,
    env: Mapping[str, str] | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    response_cap: int = MAX_RESPONSE_BYTES,
    transport: Transport = _stdlib_transport,
    config: Path | None = None,
) -> RemoteTextModel:
    profile = get_provider_profile(profile_id, config)
    return build_remote_model_from_profile(
        profile, env=env, model=model, timeout=timeout, response_cap=response_cap,
        transport=transport,
    )


def build_remote_model_from_profile(
    profile: ProviderProfile,
    *,
    env: Mapping[str, str] | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    response_cap: int = MAX_RESPONSE_BYTES,
    transport: Transport = _stdlib_transport,
) -> RemoteTextModel:
    """Construct from one already-resolved immutable profile; never reload it."""
    provider_snapshot(profile, model=model)
    spec = get_remote_spec(profile.provider_id)
    source = os.environ if env is None else env
    reasoning = spec.default_reasoning if profile.reasoning == "provider_default" else profile.reasoning
    remote = RemoteTextModel(
        spec.id,
        "" if spec.key_env is None else source.get(spec.key_env, ""),
        model=profile.model if model is None else model,
        timeout=timeout,
        response_cap=response_cap,
        transport=transport,
        reasoning=reasoning,
        upstream=profile.upstream,
        profile_label=profile.label,
    )
    remote.name = profile.id
    return remote


def ensure_attachment_eligible(profile: ProviderProfile) -> None:
    """Code-owned attachment eligibility; profiles cannot grant themselves trust."""
    spec = get_remote_spec(profile.provider_id)
    if spec.trust == "loopback_http":
        raise ProviderProfileError(
            "loopback model services are not eligible for attachment research"
        )


def remote_identity(model: RemoteTextModel, snapshot: dict) -> dict:
    return {
        "profile_id": model.name,
        "provider": snapshot["provider"],
        "requested_model": model.requested_model,
        "provider_reported_model": model.reported_model,
        "model_identity_verified": False,
        "endpoint": snapshot["endpoint"],
        "protocol": snapshot["protocol"],
        "trust": snapshot["trust"],
        "reasoning_requested": snapshot["reasoning_requested"],
        "reasoning_verified": False,
        "routing_requested": snapshot["routing_requested"],
        "routing_verified": False,
        "egress_snapshot_hash": snapshot["egress_snapshot_hash"],
    }


def annotate_remote_transcript(transcript, models, snapshots: list[dict]) -> None:
    identities = [
        remote_identity(model, snapshot) for model, snapshot in zip(models, snapshots)
    ]
    by_profile = {identity["profile_id"]: identity for identity in identities}
    transcript.meta["remote_profiles"] = identities
    for turn in transcript.turns:
        identity = by_profile.get(turn.model)
        if identity is not None:
            turn.meta["remote_identity"] = identity


def _validate_model(model: str) -> str:
    from quorum.providers.profiles import MODEL_RE
    if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
        raise RemoteModelError("remote model id is invalid")
    if model in {"openrouter/auto", "openrouter/free"} or model.startswith("~"):
        raise RemoteModelError("mutable or automatic model aliases are not allowed")
    return model
