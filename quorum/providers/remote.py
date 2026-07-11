"""Stateless, tool-free adapters for allowlisted text-completion APIs.

Only fixed HTTPS endpoints in :data:`REMOTE_CATALOG` are reachable.  The
adapter deliberately does not support arbitrary base URLs, tools, file IDs,
conversation IDs, or provider SDKs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlsplit


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
    key_env: str
    protocol: str


REMOTE_CATALOG: tuple[RemoteProviderSpec, ...] = (
    RemoteProviderSpec(
        "openai", "OpenAI", "https://api.openai.com/v1/responses",
        "gpt-5.6-sol", "OPENAI_API_KEY", "responses",
    ),
    RemoteProviderSpec(
        "deepseek", "DeepSeek", "https://api.deepseek.com/chat/completions",
        "deepseek-v4-pro", "DEEPSEEK_API_KEY", "chat",
    ),
    RemoteProviderSpec(
        "xai", "xAI", "https://api.x.ai/v1/chat/completions",
        "grok-4.5", "XAI_API_KEY", "chat",
    ),
)
_BY_ID = {spec.id: spec for spec in REMOTE_CATALOG}
_ALLOWED_HOSTS = frozenset(urlsplit(spec.endpoint).hostname for spec in REMOTE_CATALOG)


class RemoteModelError(RuntimeError):
    """A deliberately sanitized remote-completion failure."""


def get_remote_spec(provider_id: str) -> RemoteProviderSpec:
    try:
        return _BY_ID[provider_id]
    except KeyError:
        raise KeyError(f"unknown remote provider id: {provider_id!r}") from None


def remote_capabilities(env: Mapping[str, str] | None = None) -> list[dict]:
    """Return safe provider metadata and key-presence booleans, never keys."""
    source = os.environ if env is None else env
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "default_model": spec.default_model,
            "configured": bool(source.get(spec.key_env)),
        }
        for spec in REMOTE_CATALOG
    ]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _stdlib_transport(
    request: urllib.request.Request, timeout: float, response_cap: int
) -> bytes:
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=timeout) as response:
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

    def __post_init__(self) -> None:
        self.spec = get_remote_spec(self.provider_id)
        self.name = self.spec.default_model if self.model is None else self.model
        if not isinstance(self.name, str) or not self.name or len(self.name) > 120 \
                or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in self.name):
            raise RemoteModelError("remote model id is invalid")
        if not self.api_key:
            raise RemoteModelError(f"{self.spec.label} API key is not configured")
        if self.timeout <= 0 or self.response_cap <= 0:
            raise ValueError("timeout and response_cap must be positive")
        parsed = urlsplit(self.spec.endpoint)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
            raise RemoteModelError("remote endpoint is not allowlisted")

    def complete(self, prompt: str) -> str:
        payload = self._payload(prompt)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise RemoteModelError("remote request exceeded size limit")
        request = urllib.request.Request(
            self.spec.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "quorum-council/remote",
            },
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
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise RemoteModelError(f"{self.spec.label} returned an invalid response") from None

    def _payload(self, prompt: str) -> dict:
        if self.spec.protocol == "responses":
            return {
                "model": self.name, "input": prompt, "store": False,
                "reasoning": {"effort": "xhigh"},
                "max_output_tokens": MAX_OUTPUT_TOKENS,
            }
        payload = {
            "model": self.name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        if self.provider_id == "deepseek":
            payload.update(thinking={"type": "enabled"}, reasoning_effort="max")
        elif self.provider_id == "xai":
            payload["reasoning_effort"] = "high"
        return payload

    def _extract(self, document: dict) -> str:
        if self.spec.protocol == "responses":
            if isinstance(document.get("output_text"), str):
                return document["output_text"]
            for item in document["output"]:
                for part in item.get("content", []):
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        return part["text"]
            raise KeyError("output_text")
        text = document["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise TypeError("completion is not text")
        return text


def build_remote_model(
    provider_id: str,
    *,
    env: Mapping[str, str] | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    response_cap: int = MAX_RESPONSE_BYTES,
    transport: Transport = _stdlib_transport,
) -> RemoteTextModel:
    spec = get_remote_spec(provider_id)
    source = os.environ if env is None else env
    return RemoteTextModel(
        provider_id,
        source.get(spec.key_env, ""),
        model=model,
        timeout=timeout,
        response_cap=response_cap,
        transport=transport,
    )
