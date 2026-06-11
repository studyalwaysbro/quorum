"""Agreement scorecard math — pure, offline."""

from quorum.scorecard import agreement_delta, delta_to_dict, stage_agreement


def test_stage_agreement_descriptive_metrics():
    votes = {"a": "yes", "b": "yes", "c": "no"}
    conf = {"a": 4, "b": 3, "c": 5}
    s = stage_agreement("blind", votes, conf, ["yes", "no"])
    assert s.tally == {"yes": 2, "no": 1}
    assert s.majority == "yes"
    assert s.raw_agreement == round(1 / 3, 3)   # pairs: yy, yn, yn -> 1/3
    assert s.mean_confidence == 4.0
    assert s.n_voted == 3


def test_agreement_delta_tracks_flips_and_movement():
    blind = {"a": "yes", "b": "no", "c": "yes"}
    bconf = {"a": 3, "b": 3, "c": 3}
    revote = {"a": "yes", "b": "yes", "c": "yes"}
    rconf = {"a": 4, "b": 4, "c": 4}
    d = agreement_delta(blind, bconf, revote, rconf, ["yes", "no"])

    assert d.before.raw_agreement < d.after.raw_agreement
    assert d.after.raw_agreement == 1.0
    assert d.flips == {"b": ("no", "yes")}
    assert d.raw_agreement_change == round(1.0 - 1 / 3, 3)
    assert d.confidence_change == 1.0
    assert "descriptive" in d.note and "κ" in d.note

    dd = delta_to_dict(d)
    assert dd["flips"]["b"] == ["no", "yes"]
    assert dd["after"]["tally"] == {"yes": 3, "no": 0}


def test_no_after_when_no_revote():
    d = agreement_delta({"a": "yes"}, {"a": 3}, {}, {}, ["yes", "no"])
    assert d.after is None
    assert d.raw_agreement_change is None
    assert delta_to_dict(d)["after"] is None
