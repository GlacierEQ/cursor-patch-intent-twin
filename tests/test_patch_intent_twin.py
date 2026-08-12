from __future__ import annotations

from patch_intent_twin import Decision, PatchIntentTwin, PatchIntentTwinRequest


def request(*, patch: dict, intent: dict | None = None, budget: float = 0.0) -> PatchIntentTwinRequest:
    return PatchIntentTwinRequest(
        subject_id="change-123",
        budget=budget,
        payload={
            "intent": intent
            or {
                "required_paths": ["src/engine.py"],
                "allowed_paths": ["src/**", "tests/**"],
                "forbidden_paths": [".github/**", "infra/prod/**"],
                "required_tests": ["unit", "adversarial"],
                "required_symbols": {"src/engine.py": ["apply_patch"]},
                "max_files_changed": 4,
                "max_deletions": 50,
                "allow_file_deletion": False,
            },
            "patch": patch,
        },
    )


def compliant_patch() -> dict:
    return {
        "changed_files": [
            {"path": "src/engine.py", "status": "modified", "additions": 30, "deletions": 4, "symbols": ["apply_patch"]},
            {"path": "tests/test_engine.py", "status": "modified", "additions": 20, "deletions": 0, "symbols": []},
        ],
        "tests": [
            {"name": "unit", "status": "passed"},
            {"name": "adversarial", "status": "passed"},
        ],
        "dependencies_added": [],
    }


def test_allows_patch_that_matches_machine_readable_intent() -> None:
    receipt = PatchIntentTwin().evaluate(request(patch=compliant_patch()))
    assert receipt.decision is Decision.ALLOW
    assert receipt.reasons == ("patch_matches_intent",)
    assert receipt.metrics["alignment_score"] == 1.0
    assert receipt.metrics["drift_score"] == 0.0
    assert receipt.metrics["contract_valid"] is True
    assert len(receipt.metrics["intent_digest"]) == 64
    assert len(receipt.metrics["patch_digest"]) == 64


def test_refuses_forbidden_repository_surface_even_when_tests_pass() -> None:
    patch = compliant_patch()
    patch["changed_files"].append(
        {"path": ".github/workflows/deploy.yml", "status": "modified", "additions": 2, "deletions": 1, "symbols": []}
    )
    receipt = PatchIntentTwin().evaluate(request(patch=patch))
    assert receipt.decision is Decision.REFUSE
    assert "forbidden_path_touched:.github/workflows/deploy.yml" in receipt.reasons
    assert "path_outside_allowed_surface:.github/workflows/deploy.yml" in receipt.reasons


def test_refuses_missing_required_symbol_and_reports_drift() -> None:
    patch = compliant_patch()
    patch["changed_files"][0]["symbols"] = []
    receipt = PatchIntentTwin().evaluate(request(patch=patch, budget=0.0))
    assert receipt.decision is Decision.REFUSE
    assert "required_symbol_missing:src/engine.py:apply_patch" in receipt.reasons
    assert "drift_budget_exceeded" in receipt.reasons
    assert receipt.metrics["drift_score"] > 0


def test_refuses_failed_required_test() -> None:
    patch = compliant_patch()
    patch["tests"][1]["status"] = "failed"
    receipt = PatchIntentTwin().evaluate(request(patch=patch))
    assert receipt.decision is Decision.REFUSE
    assert "required_test_failed:adversarial" in receipt.reasons
    assert "test_failed:adversarial" in receipt.reasons


def test_refuses_file_deletion_and_change_budget_overrun() -> None:
    patch = compliant_patch()
    patch["changed_files"].extend(
        [
            {"path": "src/legacy.py", "status": "deleted", "additions": 0, "deletions": 10, "symbols": []},
            {"path": "src/a.py", "status": "added", "additions": 1, "deletions": 0, "symbols": []},
            {"path": "src/b.py", "status": "added", "additions": 1, "deletions": 0, "symbols": []},
        ]
    )
    receipt = PatchIntentTwin().evaluate(request(patch=patch))
    assert receipt.decision is Decision.REFUSE
    assert "file_deletion_forbidden:src/legacy.py" in receipt.reasons
    assert "max_files_changed_exceeded" in receipt.reasons


def test_refuses_forbidden_dependency_addition() -> None:
    patch = compliant_patch()
    patch["dependencies_added"] = ["unsafe-agent-root"]
    intent = request(patch=patch).payload["intent"]
    intent = {**intent, "forbidden_dependencies": ["unsafe-agent-root"]}
    receipt = PatchIntentTwin().evaluate(request(patch=patch, intent=intent))
    assert receipt.decision is Decision.REFUSE
    assert "forbidden_dependency_added:unsafe-agent-root" in receipt.reasons


def test_malformed_patch_fails_closed_instead_of_crashing_open() -> None:
    patch = compliant_patch()
    patch["changed_files"].append(dict(patch["changed_files"][0]))
    receipt = PatchIntentTwin().evaluate(request(patch=patch))
    assert receipt.decision is Decision.REFUSE
    assert "duplicate_changed_file" in receipt.reasons
    assert receipt.metrics["contract_valid"] is False


def test_patch_digest_changes_when_observed_patch_changes() -> None:
    first = PatchIntentTwin().evaluate(request(patch=compliant_patch()))
    patch = compliant_patch()
    patch["changed_files"][0]["additions"] = 31
    second = PatchIntentTwin().evaluate(request(patch=patch))
    assert first.metrics["patch_digest"] != second.metrics["patch_digest"]
