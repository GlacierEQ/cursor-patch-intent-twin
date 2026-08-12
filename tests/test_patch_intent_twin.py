from __future__ import annotations

from patch_intent_twin import Decision, PatchIntentTwin, PatchIntentTwinRequest


DIFF = """diff --git a/src/service.py b/src/service.py
--- a/src/service.py
+++ b/src/service.py
@@ -1 +1,2 @@
-old = True
+def run():
+    return \"ok\"
diff --git a/tests/test_service.py b/tests/test_service.py
--- a/tests/test_service.py
+++ b/tests/test_service.py
@@ -1 +1,2 @@
-assert False
+def test_run():
+    assert True
"""


def _intent(**overrides):
    value = {
        "must_touch": ["src/*.py", "tests/*.py"],
        "forbidden_paths": [".github/**", "infra/**"],
        "allowed_paths": ["src/**", "tests/**"],
        "required_tests": ["unit"],
        "required_receipts": ["review"],
        "max_changed_files": 3,
        "max_lines_changed": 20,
        "max_cost": 0.75,
        "content_rules": [
            {
                "id": "service-entry",
                "path": "src/*.py",
                "must_contain": ["def run"],
                "must_not_contain": ["TODO"],
            }
        ],
    }
    value.update(overrides)
    return value


def _request(diff=DIFF, intent=None, **payload_overrides):
    payload = {
        "intent": intent or _intent(),
        "unified_diff": diff,
        "tests": {"unit": True},
        "receipts": {"review": "review-42"},
        "cost": 0.2,
    }
    payload.update(payload_overrides)
    return PatchIntentTwinRequest(subject_id="patch-42", payload=payload, budget=1.0)


def test_unified_diff_is_parsed_into_real_patch_evidence() -> None:
    patch = PatchIntentTwin.parse_unified_diff(DIFF)

    assert [item["path"] for item in patch["files"]] == [
        "src/service.py",
        "tests/test_service.py",
    ]
    assert patch["files"][0]["additions"] == 2
    assert patch["files"][0]["deletions"] == 1
    assert "def run" in patch["files"][0]["added_text"]


def test_patch_matching_intent_allows_merge() -> None:
    receipt = PatchIntentTwin().evaluate(_request())

    assert receipt.decision is Decision.ALLOW
    assert receipt.merge_blocked is False
    assert receipt.reasons == ("patch_matches_intent",)
    assert receipt.changed_paths == ("src/service.py", "tests/test_service.py")
    assert receipt.metrics["changed_file_count"] == 2
    assert receipt.metrics["lines_changed"] == 6
    assert receipt.metrics["failed_check_count"] == 0
    assert len(receipt.intent_digest or "") == 64
    assert len(receipt.patch_digest or "") == 64


def test_forbidden_surface_blocks_merge() -> None:
    bad = DIFF + """diff --git a/.github/workflows/release.yml b/.github/workflows/release.yml
--- a/.github/workflows/release.yml
+++ b/.github/workflows/release.yml
@@ -1 +1 @@
-old
+new
"""
    receipt = PatchIntentTwin().evaluate(_request(diff=bad))

    assert receipt.decision is Decision.REFUSE
    assert "forbidden_path_changed:.github/workflows/release.yml" in receipt.reasons
    assert "path_outside_allowed_scope:.github/workflows/release.yml" in receipt.reasons


def test_required_surface_must_actually_be_touched() -> None:
    only_source = """diff --git a/src/service.py b/src/service.py
--- a/src/service.py
+++ b/src/service.py
@@ -1 +1 @@
-old
+def run(): pass
"""
    receipt = PatchIntentTwin().evaluate(_request(diff=only_source))

    assert receipt.decision is Decision.REFUSE
    assert "required_surface_untouched:tests/*.py" in receipt.reasons


def test_required_test_receipt_and_budget_are_enforced() -> None:
    receipt = PatchIntentTwin().evaluate(
        _request(
            tests={"unit": False},
            receipts={},
            cost=0.80,
        )
    )

    assert receipt.decision is Decision.REFUSE
    assert "test_failed_or_missing:unit" in receipt.reasons
    assert "receipt_missing:review" in receipt.reasons
    assert "budget_exceeded" in receipt.reasons


def test_content_rule_checks_added_code() -> None:
    bad = DIFF.replace("+def run():", "+# TODO implement\n+def run():")
    receipt = PatchIntentTwin().evaluate(_request(diff=bad))

    assert receipt.decision is Decision.REFUSE
    assert "content_forbidden_added:service-entry:TODO" in receipt.reasons


def test_expected_intent_digest_detects_silent_scope_change() -> None:
    twin = PatchIntentTwin()
    original_digest = twin.compile_intent(_intent())["intent_digest"]
    changed = _intent(allowed_paths=["src/**", "tests/**", "infra/**"])
    receipt = twin.evaluate(
        _request(
            intent=changed,
            expected_intent_digest=original_digest,
        )
    )

    assert receipt.decision is Decision.REFUSE
    assert "intent_digest_mismatch" in receipt.reasons
