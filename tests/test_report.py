from quorum.records import VoteRecord
from quorum.report import council_health_html, save, transcript_html
from quorum.transcript import Transcript


def test_transcript_html_escapes_model_output_and_renders_flips():
    transcript = Transcript(question="Sort <quickly>?")
    transcript.record("blind", "alice", "prompt", "<script>alert(1)</script>")
    transcript.record(
        "vote",
        "alice",
        "prompt",
        "VERDICT: timsort\nCONFIDENCE: 4",
        meta={"vote": "timsort", "confidence": 4},
    )
    transcript.record(
        "critique",
        "bob",
        "prompt",
        "Looks fine <script>bad()</script>",
        meta={"answer_permutation": [3, 1, 2]},
    )
    transcript.record("consensus_map", "quorum", "prompt", "Map\nLine two")
    transcript.record("adversarial", "skeptic", "prompt", "Break <b>this</b>")
    transcript.record(
        "revote",
        "alice",
        "prompt",
        "VERDICT: insertion\nCONFIDENCE: 3",
        meta={"vote": "insertion", "confidence": 3},
    )
    transcript.record("synthesis", "synth", "prompt", "Final <answer>")

    rendered = transcript_html(transcript, title="Replay <report>")

    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "Replay &lt;report&gt;" in rendered
    assert "alice: timsort -&gt; insertion" in rendered
    assert "answer order seen by bob: 3,1,2" in rendered
    assert "Break &lt;b&gt;this&lt;/b&gt;" in rendered


def test_council_health_html_formats_numbers_and_warning_banner():
    labels = ["yes", "no"]
    records = []
    for index in range(18):
        records.append(
            VoteRecord(
                question_id=f"agree-{index}",
                question="q?",
                labels=labels,
                votes={"a": "yes", "b": "yes"},
                confidences={},
            )
        )
    records.extend(
        [
            VoteRecord(
                question_id="split-1",
                question="q?",
                labels=labels,
                votes={"a": "yes", "b": "no"},
                confidences={},
            ),
            VoteRecord(
                question_id="split-2",
                question="q?",
                labels=labels,
                votes={"a": "no", "b": "yes"},
                confidences={},
            ),
        ]
    )

    rendered = council_health_html(records, title="Health")

    assert "Kappa paradox warning" in rendered
    assert "90.0%" in rendered
    assert "Discussion" in rendered
    assert "your 2-member council behaves like" in rendered
    assert "green means low kappa/diverse signal" in rendered


def test_council_health_html_omits_warning_when_not_flagged_and_save_writes(tmp_path):
    records = [
        VoteRecord(
            question_id="q1",
            question="q?",
            labels=["yes", "no"],
            truth="yes",
            votes={"a": "yes", "b": "yes"},
            confidences={},
        ),
        VoteRecord(
            question_id="q2",
            question="q?",
            labels=["yes", "no"],
            truth="no",
            votes={"a": "yes", "b": "no"},
            confidences={},
        ),
        VoteRecord(
            question_id="q3",
            question="q?",
            labels=["yes", "no"],
            truth="no",
            votes={"a": "no", "b": "no"},
            confidences={},
        ),
        VoteRecord(
            question_id="q4",
            question="q?",
            labels=["yes", "no"],
            truth="no",
            votes={"a": "no", "b": "no"},
            confidences={},
        ),
    ]

    rendered = council_health_html(records, roster=["b", "a"])
    path = save(rendered, tmp_path / "reports" / "health.html")

    assert "Kappa paradox warning" not in rendered
    assert "75.0%" in rendered
    assert "Dawid-Skene skill" in rendered
    assert path.read_text(encoding="utf-8") == rendered
