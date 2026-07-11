import json
import urllib.error

import pytest

from quorum.providers.remote import (
    REMOTE_CATALOG,
    RemoteModelError,
    RemoteTextModel,
    build_remote_model,
    get_remote_spec,
    remote_capabilities,
)


def test_catalog_is_https_and_fixed_to_expected_hosts():
    assert {s.id for s in REMOTE_CATALOG} == {"openai", "deepseek", "xai"}
    assert {s.endpoint.split("/", 3)[2] for s in REMOTE_CATALOG} == {
        "api.openai.com", "api.deepseek.com", "api.x.ai"
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


@pytest.mark.parametrize("provider_id", ["openai", "deepseek", "xai"])
def test_headers_payload_and_text_extraction(provider_id):
    seen = {}

    def transport(request, timeout, cap):
        seen.update(url=request.full_url, headers=dict(request.header_items()),
                    payload=json.loads(request.data), timeout=timeout, cap=cap)
        if provider_id == "openai":
            return json.dumps({"output_text": "answer"}).encode()
        return json.dumps({"choices": [{"message": {"content": "answer"}}]}).encode()

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
        assert seen["payload"]["max_tokens"] == 4096
        assert seen["payload"]["reasoning_effort"] == ("max" if provider_id == "deepseek" else "high")
    if provider_id == "deepseek":
        assert seen["payload"]["thinking"] == {"type": "enabled"}


def test_transport_cannot_return_over_configured_response_cap():
    model = RemoteTextModel("openai", "secret", response_cap=8,
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
    model = RemoteTextModel("deepseek", "secret", transport=lambda *args: b"not-json")
    with pytest.raises(RemoteModelError, match="invalid response"):
        model.complete("private prompt")


@pytest.mark.parametrize("model", ["", "x\nheader", "x" * 121])
def test_model_override_is_bounded_and_has_no_controls(model):
    with pytest.raises(RemoteModelError, match="model id"):
        RemoteTextModel("openai", "secret", model=model)
