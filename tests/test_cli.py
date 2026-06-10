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
