import json
import os
import stat

import pytest

import quorum.cli as cli
from quorum.providers.profiles import (
    ProviderProfileError,
    load_user_profiles,
    make_user_profile,
    write_user_profiles,
)
from quorum.providers.remote import (
    RemoteModelError,
    build_remote_model,
    get_provider_profile,
    provider_profiles,
    provider_snapshot,
)


def _private_json(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)


def test_profile_config_is_private_strict_and_non_executable(tmp_path):
    path = tmp_path / "providers.json"
    _private_json(path, {
        "version": 1,
        "profiles": [{
            "id": "my-kimi", "label": "My Kimi", "provider": "kimi",
            "model": "kimi-k2.7-code", "reasoning": "max", "upstream": None,
        }],
    })
    profile = load_user_profiles(path)[0]
    assert profile.id == "my-kimi" and profile.model == "kimi-k2.7-code"
    assert "api_key" not in json.dumps(profile.canonical()).lower()

    document = json.loads(path.read_text())
    document["profiles"][0]["headers"] = {"Authorization": "steal-another-key"}
    _private_json(path, document)
    with pytest.raises(ProviderProfileError, match="unknown"):
        load_user_profiles(path)


@pytest.mark.parametrize("bad", [
    {"version": 1, "profiles": [{"id": "../evil", "provider": "kimi", "model": "x"}]},
    {"version": 1, "profiles": [{"id": "safe", "provider": "kimi", "model": "x\nHeader"}]},
    {"version": 1, "profiles": [{"id": "safe", "provider": "openrouter", "model": "x/y"}]},
    {"version": 1, "profiles": [{"id": "safe", "provider": "kimi", "model": "x", "upstream": "evil"}]},
])
def test_profile_schema_rejects_injection_and_unsafe_routes(tmp_path, bad):
    path = tmp_path / "providers.json"
    _private_json(path, bad)
    with pytest.raises(ProviderProfileError):
        load_user_profiles(path)


def test_profile_json_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text('{"version":1,"version":1,"profiles":[]}', encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ProviderProfileError, match="duplicate"):
        load_user_profiles(path)


def test_profile_file_rejects_symlink_and_loose_permissions(tmp_path):
    real = tmp_path / "real.json"
    _private_json(real, {"version": 1, "profiles": []})
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ProviderProfileError, match="safe regular|non-symlink"):
        load_user_profiles(link)
    real.chmod(0o644)
    with pytest.raises(ProviderProfileError, match="0600"):
        load_user_profiles(real)


def test_profile_read_uses_one_verified_descriptor_across_path_swap(tmp_path, monkeypatch):
    import quorum.providers.profiles as profiles_module

    target = tmp_path / "providers.json"
    replacement = tmp_path / "replacement.json"
    _private_json(target, {"version": 1, "profiles": [{
        "id": "approved", "provider": "ollama", "model": "model-a",
    }]})
    _private_json(replacement, {"version": 1, "profiles": [{
        "id": "swapped", "provider": "ollama", "model": "model-b",
    }]})
    real_open = os.open
    swapped = False
    def racing_open(path, flags, *args):
        nonlocal swapped
        fd = real_open(path, flags, *args)
        if not swapped and os.fspath(path) == os.fspath(target):
            swapped = True
            os.replace(replacement, target)
        return fd
    monkeypatch.setattr(profiles_module.os, "open", racing_open)
    loaded = load_user_profiles(target)
    assert [profile.id for profile in loaded] == ["approved"]


def test_atomic_profile_write_is_0600(tmp_path):
    path = tmp_path / "nested" / "providers.json"
    profile = make_user_profile("glm-private", "zai", "glm-5.1")
    write_user_profiles([profile], path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert load_user_profiles(path) == (profile,)


def test_user_profile_cannot_shadow_builtins(tmp_path):
    path = tmp_path / "providers.json"
    write_user_profiles([make_user_profile("openai", "zai", "glm-5.1")], path)
    with pytest.raises(ProviderProfileError, match="shadow"):
        provider_profiles(path)


def test_openrouter_profile_is_pinned_no_fallback_and_private():
    profile = make_user_profile(
        "claude-router", "openrouter", "anthropic/claude-sonnet-4.5",
        reasoning="high", upstream="anthropic",
    )
    snap = provider_snapshot(profile)
    assert snap["routing_requested"] == {
        "only": ["anthropic"], "allow_fallbacks": False,
        "require_parameters": True, "data_collection": "deny", "zdr": True,
    }
    assert snap["routing_verified"] is False
    model = build_remote_model  # prove there is no arbitrary endpoint argument in profile data
    assert "endpoint" not in profile.canonical()
    assert model is not None


def test_each_profile_receives_only_its_credential(tmp_path):
    profile = make_user_profile("kimi-canary", "kimi", "kimi-k2.7-code")
    config = tmp_path / "providers.json"
    write_user_profiles([profile], config)
    seen = {}
    def transport(request, timeout, cap):
        seen.update(headers=dict(request.header_items()), payload=json.loads(request.data))
        return json.dumps({
            "model": "kimi-k2.7-code",
            "choices": [{"message": {"content": "ok"}}],
        }).encode()
    model = build_remote_model(
        "kimi-canary",
        env={"MOONSHOT_API_KEY": "KIMI-ONLY", "OPENAI_API_KEY": "MUST-NOT-LEAK"},
        config=config, transport=transport,
    )
    assert model.complete("document canary") == "ok"
    wire = json.dumps(seen)
    assert "Bearer KIMI-ONLY" in wire
    assert "MUST-NOT-LEAK" not in wire
    assert "tools" not in seen["payload"] and "plugins" not in seen["payload"]


def test_openrouter_wire_policy_is_exact_and_fallback_free(tmp_path):
    profile = make_user_profile(
        "router-secure", "openrouter", "anthropic/claude-sonnet-4.5",
        reasoning="high", upstream="anthropic",
    )
    config = tmp_path / "providers.json"
    write_user_profiles([profile], config)
    seen = {}
    def transport(request, timeout, cap):
        seen.update(payload=json.loads(request.data))
        return json.dumps({
            "model": "anthropic/claude-sonnet-4.5",
            "choices": [{"message": {"content": "ok"}}],
        }).encode()
    model = build_remote_model(
        "router-secure", env={"OPENROUTER_API_KEY": "router-key"},
        config=config, transport=transport,
    )
    assert model.complete("private") == "ok"
    assert seen["payload"]["provider"] == {
        "only": ["anthropic"], "allow_fallbacks": False,
        "require_parameters": True, "data_collection": "deny", "zdr": True,
    }
    assert "models" not in seen["payload"] and "plugins" not in seen["payload"]


@pytest.mark.parametrize("alias", ["openrouter/auto", "openrouter/free", "~openai/gpt-latest"])
def test_mutable_model_aliases_fail_closed(alias):
    from quorum.providers.remote import RemoteTextModel
    with pytest.raises(RemoteModelError, match="aliases|model id"):
        RemoteTextModel("openai", "secret-value", model=alias)


def test_cli_add_list_remove_never_accepts_or_writes_a_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    secret = "sk-test-never-write-me"
    monkeypatch.setenv("MOONSHOT_API_KEY", secret)
    assert cli.main([
        "provider", "add", "kimi-team", "--provider", "kimi",
        "--model", "kimi-k2.7-code",
    ]) == 0
    capsys.readouterr()
    assert cli.main(["provider", "list"]) == 0
    listed = capsys.readouterr().out
    config = tmp_path / "quorum" / "providers.json"
    assert "kimi-team" in listed
    assert secret not in listed and secret not in config.read_text()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert cli.main(["provider", "remove", "kimi-team"]) == 0
    assert "kimi-team" not in config.read_text()
