import json
import stat

import quorum.cli as cli
import quorum.providers.remote as remote


class GroundedModel:
    def __init__(self, name, seen):
        self.name = name
        self.seen = seen

    def complete(self, prompt):
        self.seen.append(prompt)
        if "End with exactly one line" in prompt:
            return "The cited text supports the claim.\nVERDICT: Supported"
        return json.dumps({
            "claims": [{
                "text": "The source was redacted.",
                "citations": [{"chunk_id": "C1", "quote": "[REDACTED:CREDENTIAL_ASSIGNMENT]"}],
                "confidence": 4,
            }]
        })


def test_research_cli_requires_prepare_then_hash_approval(tmp_path, capsys):
    source = tmp_path / "source.txt"
    source.write_text("ordinary source")
    assert cli.main([
        "research", "q", "--file", str(source), "--provider", "openai"
    ]) == 2
    assert "--manifest FILE and --approve" in capsys.readouterr().err


def test_research_cli_redacts_and_writes_private_ledger(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.txt"
    fake_secret = "sk" + chr(45) + "abcdefghijklmnopqr"
    credential_name = "_".join(("OPENAI", "API", "KEY"))
    source.write_text(f"{credential_name}={fake_secret}")
    output = tmp_path / "ledger.json"
    manifest = tmp_path / "manifest.json"
    seen = []
    monkeypatch.setattr(
        remote, "build_remote_model_from_profile",
        lambda profile, **kwargs: GroundedModel(profile.id, seen),
    )

    assert cli.main([
        "research", "what is present?", "--file", str(source),
        "--provider", "openai", "--prepare", str(manifest),
    ]) == 0
    prepared = capsys.readouterr()
    summary = json.loads(prepared.out)
    assert "no content was sent" in prepared.err
    assert seen == []
    assert summary["secret_findings_redacted"] >= 1
    assert fake_secret not in summary["preview"]

    assert cli.main([
        "research", "what is present?", "--manifest", str(manifest),
        "--provider", "openai", "--approve", summary["manifest_hash"],
        "--json", str(output),
    ]) == 0
    captured = capsys.readouterr()
    assert "approved manifest" in captured.err
    assert fake_secret not in "\n".join(seen)
    assert "[REDACTED:CREDENTIAL_ASSIGNMENT]" in "\n".join(seen)
    ledger = json.loads(captured.out)
    assert ledger["counts"]["kept"] == 1
    assert json.loads(output.read_text()) == ledger
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_research_cli_rejects_unknown_provider_before_model_call(tmp_path, capsys):
    source = tmp_path / "source.txt"
    source.write_text("source")
    assert cli.main([
        "research", "q", "--file", str(source), "--provider", "evil",
        "--prepare", str(tmp_path / "manifest.json"),
    ]) == 2
    assert "unknown remote provider" in capsys.readouterr().err


def test_research_cli_rejects_ollama_before_reading_files(tmp_path, capsys):
    missing = tmp_path / "must-not-read.txt"
    assert cli.main([
        "research", "q", "--file", str(missing), "--provider", "ollama",
        "--prepare", str(tmp_path / "manifest.json"),
    ]) == 2
    error = capsys.readouterr().err
    assert "not eligible for attachment research" in error
    assert "must-not-read" not in error


def test_research_cli_rejects_profile_change_after_prepare(tmp_path, monkeypatch, capsys):
    from quorum.providers.profiles import make_user_profile, write_user_profiles

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    config = tmp_path / "config" / "quorum" / "providers.json"
    write_user_profiles([
        make_user_profile("glm-team", "zai", "glm-5.1")
    ], config)
    source = tmp_path / "source.txt"
    source.write_text("evidence", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    assert cli.main([
        "research", "q", "--file", str(source), "--provider", "glm-team",
        "--prepare", str(manifest),
    ]) == 0
    prepared = json.loads(capsys.readouterr().out)
    write_user_profiles([
        make_user_profile("glm-team", "zai", "glm-5.1-new")
    ], config)
    assert cli.main([
        "research", "q", "--manifest", str(manifest), "--provider", "glm-team",
        "--approve", prepared["manifest_hash"],
    ]) == 2
    assert "does not match the reviewed manifest" in capsys.readouterr().err


def test_research_cli_uses_approved_immutable_profile_during_construction(
    tmp_path, monkeypatch, capsys
):
    from quorum.providers.profiles import make_user_profile, write_user_profiles

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    config = tmp_path / "config" / "quorum" / "providers.json"
    # Use direct Z.AI with a synthetic builder while retaining the
    # immutable-profile race shape.
    approved = make_user_profile("glm-safe", "zai", "glm-5.1")
    write_user_profiles([approved], config)
    source = tmp_path / "source.txt"
    source.write_text("evidence", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    assert cli.main([
        "research", "q", "--file", str(source), "--provider", approved.id,
        "--prepare", str(manifest),
    ]) == 0
    prepared = json.loads(capsys.readouterr().out)
    seen = []
    def racing_builder(profile, **kwargs):
        seen.append((profile.provider_id, profile.model))
        write_user_profiles([
            make_user_profile("glm-safe", "zai", "changed-after-compare")
        ], config)
        return GroundedModel(profile.id, [])
    monkeypatch.setattr(remote, "build_remote_model_from_profile", racing_builder)
    assert cli.main([
        "research", "q", "--manifest", str(manifest), "--provider", approved.id,
        "--approve", prepared["manifest_hash"],
    ]) == 0
    assert seen == [("zai", "glm-5.1")]
