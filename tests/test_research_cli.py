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
        remote, "build_remote_model",
        lambda provider_id, **kwargs: GroundedModel(provider_id, seen),
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
