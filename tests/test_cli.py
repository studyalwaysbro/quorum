import json
import shlex
import subprocess
import sys

from quorum.records import RecordStore
from quorum.transcript import Transcript

import quorum.cli as cli


class ScriptedModel:
    def __init__(self, name, vote="yes", revote=None):
        self.name = name
        self.vote = vote
        self.revote = revote or vote

    def complete(self, prompt):
        if "You may revise your categorical vote once" in prompt:
            return f"VERDICT: {self.revote}\nCONFIDENCE: 3"
        if "Given your answer above" in prompt:
            return f"VERDICT: {self.vote}\nCONFIDENCE: 4"
        if "Below are independent answers" in prompt:
            return "shared critique"
        if "You are the synthesizer" in prompt:
            return f"final from {self.name}"
        return f"blind from {self.name}"


def test_ask_health_and_replay_happy_path(tmp_path, capsys):
    store = tmp_path / "votes.jsonl"
    transcript = tmp_path / "transcript.json"
    html = tmp_path / "transcript.html"

    def factory(name, argv, timeout):
        del argv, timeout
        return {
            "a": ScriptedModel("a", vote="yes", revote="no"),
            "b": ScriptedModel("b", vote="no"),
        }[name]

    code = cli.main(
        [
            "ask",
            "q?",
            "--member",
            "a=unused",
            "--member",
            "b=unused",
            "--labels",
            "yes,no",
            "--revote",
            "--truth",
            "yes",
            "--question-id",
            "q1",
            "--store",
            str(store),
            "--json",
            str(transcript),
            "--html",
            str(html),
        ],
        model_factory=factory,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "final from a\n"
    assert "majority: none" in captured.err
    assert "- a: yes -> no" in captured.err
    assert Transcript.from_json(transcript.read_text()).question == "q?"
    assert "final from a" in html.read_text()
    [record] = RecordStore(store).load()
    assert record.question_id == "q1"
    assert record.votes == {"a": "yes", "b": "no"}
    assert record.revotes == {"a": "no", "b": "no"}

    health_html = tmp_path / "health.html"
    assert cli.main(
        ["health", str(store), "--roster-size", "1", "--html", str(health_html)]
    ) == 0
    health_out = capsys.readouterr().out
    assert "raw: 0.000" in health_out
    assert "a: redundancy=0.000 accuracy=100.0% n=1" in health_out
    assert "roster (truth): a" in health_out
    assert "Quorum council health" in health_html.read_text()

    assert cli.main(["replay", str(transcript)]) == 0
    replay_out = capsys.readouterr().out
    assert "question: q?" in replay_out
    assert "[synthesis] a" in replay_out


def test_cli_error_paths_return_2(tmp_path, capsys):
    def factory(name, argv, timeout):
        del argv, timeout
        return ScriptedModel(name)

    cases = [
        ["ask", "q?", "--member", "bad"],
        ["ask", "q?", "--member", "a=unused", "--labels", "yes"],
        ["ask", "q?", "--member", "a=unused", "--synthesizer", "missing"],
        ["health", str(tmp_path / "missing.jsonl")],
        ["replay", str(tmp_path / "missing.json")],
    ]

    for argv in cases:
        assert cli.main(argv, model_factory=factory) == 2
        assert capsys.readouterr().err.startswith("quorum:")


def test_cli_uses_catalog_prompt_transport_for_known_arg_clis(capsys):
    seen = {}

    def factory(name, argv, timeout, prompt_transport="stdin"):
        del argv, timeout
        seen[name] = prompt_transport
        return ScriptedModel(name)

    assert cli.main(
        ["ask", "q?", "--member", "claude=claude -p"],
        model_factory=factory,
    ) == 0
    assert seen["claude"] == "arg"
    assert capsys.readouterr().out == "final from claude\n"


def test_cli_accepts_catalog_member_id_and_codex_skips_git_check(capsys):
    seen = {}

    def factory(name, argv, timeout, prompt_transport="stdin"):
        del timeout
        seen[name] = {"argv": argv, "prompt_transport": prompt_transport}
        return ScriptedModel(name)

    assert cli.main(
        ["ask", "q?", "--member", "gpt-5.5"],
        model_factory=factory,
    ) == 0
    assert seen["gpt-5.5"] == {
        "argv": ["codex", "exec", "--skip-git-repo-check"],
        "prompt_transport": "stdin",
    }
    assert capsys.readouterr().out == "final from gpt-5.5\n"


def test_remote_profile_seats_require_explicit_egress_consent(tmp_path, monkeypatch, capsys):
    import quorum.providers.remote as remote

    called = []
    def fake_remote(profile, **kwargs):
        model = ScriptedModel(profile.id)
        model.requested_model = profile.model
        model.reported_model = profile.model
        return model
    monkeypatch.setattr(
        remote, "build_remote_model_from_profile",
        lambda profile, **kwargs: called.append(profile.id) or fake_remote(profile),
    )
    assert cli.main(["ask", "private?", "--member", "remote:openai"]) == 2
    assert "--allow-remote-egress" in capsys.readouterr().err
    assert called == []
    transcript = tmp_path / "remote.json"
    html = tmp_path / "remote.html"
    assert cli.main([
        "ask", "private?", "--member", "remote:openai", "--allow-remote-egress",
        "--json", str(transcript), "--html", str(html),
    ]) == 0
    assert called == ["openai"]
    captured = capsys.readouterr()
    assert captured.out == "final from openai\n"
    assert "approved remote egress receipt" in captured.err
    document = json.loads(transcript.read_text())
    identity = document["turns"][0]["meta"]["remote_identity"]
    assert identity["requested_model"] == "gpt-5.6-sol"
    assert identity["provider_reported_model"] == "gpt-5.6-sol"
    assert identity["model_identity_verified"] is False
    assert "provider reported gpt-5.6-sol" in html.read_text()
    assert cli.main(["replay", str(transcript)]) == 0
    assert "identity: openai / requested gpt-5.6-sol" in capsys.readouterr().out


def test_remote_receipt_tracks_actual_custom_synthesizer(monkeypatch, capsys):
    import quorum.providers.remote as remote

    def fake_remote(profile, **kwargs):
        model = ScriptedModel(profile.id)
        model.requested_model = profile.model
        model.reported_model = profile.model
        return model
    monkeypatch.setattr(remote, "build_remote_model_from_profile", fake_remote)
    assert cli.main([
        "ask", "q", "--member", "remote:openai", "--member", "remote:deepseek",
        "--synthesizer", "deepseek", "--allow-remote-egress",
    ]) == 0
    receipt = json.loads(capsys.readouterr().err.splitlines()[-1])
    by_id = {item["id"]: item for item in receipt}
    assert by_id["openai"]["receives"] == ["blind", "critique"]
    assert by_id["deepseek"]["receives"] == ["blind", "critique", "synthesis"]

    def local_factory(name, argv, timeout):
        return ScriptedModel(name)
    assert cli.main([
        "ask", "q", "--member", "local=unused", "--member", "remote:deepseek",
        "--synthesizer", "deepseek", "--allow-remote-egress",
    ], model_factory=local_factory) == 0
    mixed = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert mixed[0]["id"] == "deepseek"
    assert mixed[0]["receives"] == ["blind", "critique", "synthesis"]


def test_real_cli_invocation_with_python_member(tmp_path):
    store = tmp_path / "votes.jsonl"
    script = """
import sys
prompt = sys.stdin.read()
if "Given your answer above" in prompt:
    print("VERDICT: yes\\nCONFIDENCE: 5")
elif "You are the synthesizer" in prompt:
    print("cli final")
else:
    print("blind yes")
""".strip()
    member = "a=" + shlex.join([sys.executable, "-c", script])

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "quorum.cli",
            "ask",
            "offline?",
            "--member",
            member,
            "--labels",
            "yes,no",
            "--store",
            str(store),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout == "cli final\n"
    assert "majority: yes" in result.stderr
    [record] = RecordStore(store).load()
    assert record.votes == {"a": "yes"}
