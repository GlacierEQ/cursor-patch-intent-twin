from __future__ import annotations

import pytest

from patch_intent_twin import (
    Decision,
    IntentSchemaError,
    PatchIntentTwin,
    PatchIntentTwinRequest,
)


def _evaluate(intent, *, patch=None, diff=None, tests=None, receipts=None):
    payload = {
        "intent": intent,
        "tests": tests or {},
        "receipts": receipts or {},
        "cost": 0,
    }
    if diff is not None:
        payload["unified_diff"] = diff
    else:
        payload["patch"] = patch
    return PatchIntentTwin().evaluate(
        PatchIntentTwinRequest(subject_id="patch", payload=payload, budget=1.0)
    )


def test_empty_intent_cannot_approve_patch() -> None:
    receipt = _evaluate(
        {},
        patch={"files": [{"path": "a.py", "additions": 1, "deletions": 0}]},
    )
    assert receipt.decision is Decision.REFUSE
    assert "intent_has_no_constraints" in receipt.reasons


def test_empty_unified_diff_fails_closed() -> None:
    receipt = _evaluate({"must_touch": ["src/**"]}, diff="   ")
    assert receipt.decision is Decision.REFUSE
    assert "unified_diff_empty" in receipt.reasons


def test_patch_with_duplicate_path_is_rejected() -> None:
    receipt = _evaluate(
        {"must_touch": ["src/**"]},
        patch={
            "files": [
                {"path": "src/a.py", "additions": 1, "deletions": 0},
                {"path": "src/a.py", "additions": 1, "deletions": 0},
            ]
        },
    )
    assert receipt.decision is Decision.REFUSE
    assert "patch_file_duplicate:src/a.py" in receipt.reasons


def test_allowed_scope_blocks_unlisted_path_even_without_forbidden_pattern() -> None:
    receipt = _evaluate(
        {"allowed_paths": ["src/**"]},
        patch={"files": [{"path": "docs/secret.md", "additions": 2, "deletions": 0}]},
    )
    assert receipt.decision is Decision.REFUSE
    assert "path_outside_allowed_scope:docs/secret.md" in receipt.reasons


def test_line_budget_counts_additions_and_deletions() -> None:
    receipt = _evaluate(
        {"max_lines_changed": 2},
        patch={"files": [{"path": "a.py", "additions": 2, "deletions": 1}]},
    )
    assert receipt.decision is Decision.REFUSE
    assert "line_change_budget_exceeded" in receipt.reasons


def test_removed_forbidden_token_does_not_count_as_added_violation() -> None:
    diff = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-TODO insecure
+done = True
"""
    receipt = _evaluate(
        {
            "must_touch": ["src/**"],
            "content_rules": [
                {"id": "no-todo", "path": "src/**", "must_not_contain": ["TODO"]}
            ],
        },
        diff=diff,
    )
    assert receipt.decision is Decision.ALLOW


def test_content_rule_requires_matching_path() -> None:
    receipt = _evaluate(
        {
            "content_rules": [
                {"id": "entry", "path": "src/*.py", "must_contain": ["run"]}
            ]
        },
        patch={"files": [{"path": "tests/test_a.py", "additions": 1, "deletions": 0}]},
    )
    assert receipt.decision is Decision.REFUSE
    assert "content_rule_path_missing:entry" in receipt.reasons


def test_duplicate_content_rule_ids_are_rejected() -> None:
    with pytest.raises(IntentSchemaError, match="duplicate"):
        PatchIntentTwin.compile_intent(
            {
                "content_rules": [
                    {"id": "same", "path": "a", "must_contain": ["x"]},
                    {"id": "same", "path": "b", "must_contain": ["y"]},
                ]
            }
        )


def test_drift_receipt_is_deterministic() -> None:
    before = {"files": [{"path": "src/a.py", "additions": 1, "deletions": 0}]}
    after = {
        "files": [
            {"path": "src/a.py", "additions": 1, "deletions": 0},
            {"path": "tests/test_a.py", "additions": 1, "deletions": 0},
        ]
    }
    first = PatchIntentTwin.drift(before, after)
    second = PatchIntentTwin.drift(before, after)

    assert first == second
    assert first["added_paths"] == ["tests/test_a.py"]
    assert first["retained_paths"] == ["src/a.py"]
    assert len(first["digest"]) == 64
