import json
import urllib.error
from email.message import Message

import pytest

from quorum.providers.remote import (
    REMOTE_CATALOG,
    RemoteModelError,
    RemoteTextModel,
    build_remote_model,
    get_remote_spec,
    get_provider_profile,
    provider_snapshot,
    remote_capabilities,
    _stdlib_transport,
)


def test_catalog_is_https_and_fixed_to_expected_hosts():
    assert {s.id for s in REMOTE_CATALOG} == {
        "openai", "deepseek", "xai", "kimi", "zai", "openrouter", "ollama"
    }
    assert {s.endpoint.split("/", 3)[2] for s in REMOTE_CATALOG} == {
        "api.openai.com", "api.deepseek.com", "api.x.ai", "api.moonshot.ai",
        "api.z.ai", "openrouter.ai", "127.0.0.1:11434",
    }
    with pytest.raises(KeyError):
        get_remote_spec("https://evil.example/v1")


def test_capabilities_reveal_presence_not_key_value():
    secret = "sk-never-return-this"
    caps = remote_capabilities({"OPENAI_API_KEY": secret})
    assert next(x for x in caps if x["id"] == "openai")["configured"] is True
    assert secret not in json.dumps(caps)
    assert all("key" not in key.lower() for item in caps for key in item)


def test_missing_key_fails_cleanly():
    with pytest.raises(RemoteModelError, match="not configured") as caught:
        build_remote_model("deepseek", env={})
    assert "DEEPSEEK_API_KEY" not in str(caught.value)


@pytest.mark.parametrize("credential", ["short", "valid-key\r\nX-Evil: 1", "x" * 4097])
def test_invalid_credentials_cannot_reach_authorization_header(credential):
    with pytest.raises(RemoteModelError, match="credential is invalid"):
        RemoteTextModel("openai", credential)


@pytest.mark.parametrize("provider_id", ["openai", "deepseek", "xai", "kimi", "zai"])
def test_headers_payload_and_text_extraction(provider_id):
    seen = {}

    def transport(request, timeout, cap):
        seen.update(url=request.full_url, headers=dict(request.header_items()),
                    payload=json.loads(request.data), timeout=timeout, cap=cap)
        if provider_id == "openai":
            return json.dumps({"model": get_remote_spec(provider_id).default_model,
                               "output_text": "answer"}).encode()
        return json.dumps({"model": get_remote_spec(provider_id).default_model,
                           "choices": [{"message": {"content": "answer"}}]}).encode()

    model = RemoteTextModel(provider_id, "TOP-SECRET", timeout=7, transport=transport)
    assert model.complete("PRIVATE PROMPT") == "answer"
    assert seen["url"] == get_remote_spec(provider_id).endpoint
    assert seen["headers"]["Authorization"] == "Bearer TOP-SECRET"
    assert seen["headers"]["Content-type"] == "application/json"
    assert seen["timeout"] == 7
    assert "tools" not in seen["payload"]
    assert "tool_choice" not in seen["payload"]
    if provider_id == "openai":
        assert seen["payload"]["input"] == "PRIVATE PROMPT"
        assert seen["payload"]["store"] is False
        assert seen["payload"]["reasoning"] == {"effort": "xhigh"}
        assert seen["payload"]["max_output_tokens"] == 4096
    else:
        assert seen["payload"]["messages"] == [{"role": "user", "content": "PRIVATE PROMPT"}]
        assert seen["payload"]["stream"] is False
        token_key = "max_completion_tokens" if provider_id == "kimi" else "max_tokens"
        assert seen["payload"][token_key] == 4096
    if provider_id == "deepseek":
        assert seen["payload"]["thinking"] == {"type": "enabled"}
        assert seen["payload"]["reasoning_effort"] == "max"
    if provider_id == "xai":
        assert seen["payload"]["reasoning_effort"] == "high"
    if provider_id == "kimi":
        assert seen["payload"]["thinking"] == {"type": "enabled", "keep": "all"}
    if provider_id == "zai":
        assert seen["payload"]["thinking"] == {"type": "enabled"}


def test_ollama_is_exact_loopback_and_sends_no_authorization():
    seen = {}
    def transport(request, timeout, cap):
        seen.update(url=request.full_url, headers=dict(request.header_items()),
                    payload=json.loads(request.data))
        return json.dumps({"model": "gpt-oss:20b",
                           "choices": [{"message": {"content": "local"}}]}).encode()
    model = RemoteTextModel("ollama", "", transport=transport)
    assert model.complete("source") == "local"
    assert seen["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert "Authorization" not in seen["headers"]
    assert "tools" not in seen["payload"]


def test_response_model_identity_is_required_and_exact():
    model = RemoteTextModel(
        "deepseek", "secret-value",
        transport=lambda *args: json.dumps({
            "model": "different-model", "choices": [{"message": {"content": "x"}}]
        }).encode(),
    )
    with pytest.raises(RemoteModelError, match="different model"):
        model.complete("private")


def test_valid_provider_output_cannot_echo_active_credential():
    secret = "sk-synthetic-never-expose-123456"
    model = RemoteTextModel(
        "deepseek", secret,
        transport=lambda *args: json.dumps({
            "model": "deepseek-v4-pro",
            "choices": [{"message": {"content": f"debug auth: {secret}"}}],
        }).encode(),
    )
    output = model.complete("q")
    assert secret not in output
    assert "[REDACTED:" in output


def test_provider_snapshot_binds_compiled_route_without_key_metadata():
    snap = provider_snapshot(get_provider_profile("openai"))
    assert snap["endpoint"] == "https://api.openai.com/v1/responses"
    assert snap["reasoning_verified"] is False
    assert len(snap["egress_snapshot_hash"]) == 64
    assert "key" not in json.dumps(snap).lower()


def test_transport_cannot_return_over_configured_response_cap():
    model = RemoteTextModel("openai", "secret-value", response_cap=8,
                            transport=lambda request, timeout, cap: b"x" * 9)
    with pytest.raises(RemoteModelError, match="size limit"):
        model.complete("prompt")


def test_network_and_parse_errors_never_echo_secret_or_prompt():
    secret = "sk-sensitive-value"
    prompt = "private merger details"

    def failed(request, timeout, cap):
        raise urllib.error.URLError(f"failure involving {secret} and {prompt}")

    model = RemoteTextModel("xai", secret, transport=failed)
    with pytest.raises(RemoteModelError) as caught:
        model.complete(prompt)
    message = str(caught.value)
    assert secret not in message
    assert prompt not in message


def test_invalid_response_is_secret_safe():
    model = RemoteTextModel("deepseek", "secret-value", transport=lambda *args: b"not-json")
    with pytest.raises(RemoteModelError, match="invalid response"):
        model.complete("private prompt")


def test_stdlib_transport_disables_ambient_proxy_and_requires_json(monkeypatch):
    captured = {}
    class Response:
        def __init__(self, media):
            self.headers = Message()
            self.headers["Content-Type"] = media
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, cap): return b"{}"
    class Opener:
        def open(self, request, timeout): return Response(captured["media"])
    def fake_build(*handlers):
        captured["handlers"] = handlers
        return Opener()
    monkeypatch.setattr(urllib.request, "build_opener", fake_build)
    captured["media"] = "application/json"
    request = urllib.request.Request("https://api.openai.com/v1/responses")
    assert _stdlib_transport(request, 1, 100) == b"{}"
    proxies = [h for h in captured["handlers"] if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxies) == 1 and proxies[0].proxies == {}
    captured["media"] = "text/html"
    with pytest.raises(RemoteModelError, match="not JSON"):
        _stdlib_transport(request, 1, 100)


@pytest.mark.parametrize("model", ["", "x\nheader", "x" * 121])
def test_model_override_is_bounded_and_has_no_controls(model):
    with pytest.raises(RemoteModelError, match="model id"):
        RemoteTextModel("openai", "secret-value", model=model)
