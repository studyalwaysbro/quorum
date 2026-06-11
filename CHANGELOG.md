# Changelog

## Unreleased

### Fixed

- Hardened scorecard UI honesty after the `83dcb3b` re-review: the decision
  control now describes blind-vs-post-debate agreement instead of attributing
  movement only to the adversary.
- Final scorecards now display parsed-vote denominators for both blind and
  post-debate stages, so abstentions or unparsed votes are visible on both sides
  of the comparison.
- Example question chips now dispatch the same input event as manual edits,
  clearing the prefilled demo labels before a different question can reuse them.

### Verification

- Ran `.venv/bin/pytest -q tests/test_web.py tests/test_scorecard.py`.
- Ran `.venv/bin/pytest -q`.

### Rollback

- Revert the `quorum/web/static/index.html` and `tests/test_web.py` changes in
  this entry to restore the prior scorecard copy and label behavior.
