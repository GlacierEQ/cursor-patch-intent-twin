from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from intent_recovery import IntentEvidence, recover_intent
from patch_monitor import PatchIntentMonitor
from review_benchmark import load_review_cases, run_review_benchmark


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "examples" / "review_benchmark.json"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "crystal@example.invalid")
    git(repo, "config", "user.name", "Crystallization Test")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "engine.py").write_text("def apply_patch(value):\n    return value\n", encoding="utf-8")
    (repo / "tests" / "test_engine.py").write_text("def test_unit():\n    assert True\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo, git(repo, "rev-parse", "HEAD")


def recovered_contract() -> dict:
    recovered = recover_intent(
        [
            IntentEvidence(
                source_id="issue-42",
                source_uri="https://example.invalid/issues/42",
                text="""
Required paths: src/engine.py
Allowed paths: src/**, tests/**
Forbidden paths: infra/prod/**
Required tests: unit
Required symbols src/engine.py: apply_patch
Max files changed: 3
Max deletions: 20
Allow file deletion: false
""",
            )
        ]
    )
    return recovered.contract


def test_recovers_intent_with_line_level_provenance() -> None:
    recovered = recover_intent(
        [
            IntentEvidence(
                source_id="pr-9",
                source_uri="https://example.invalid/pull/9",
                text="""
Required paths: src/engine.py
Allowed paths: src/**, tests/**
Forbidden paths: infra/prod/**
Required tests: unit, adversarial
Required symbols src/engine.py: apply_patch, validate_patch
Max files changed: 4
Max deletions: 12
Allow file deletion: false
""",
            )
        ]
    )
    assert recovered.contract["required_paths"] == ["src/engine.py"]
    assert recovered.contract["required_tests"] == ["adversarial", "unit"]
    assert recovered.contract["required_symbols"]["src/engine.py"] == ["apply_patch", "validate_patch"]
    assert recovered.contract["max_files_changed"] == 4
    assert recovered.contract["allow_file_deletion"] is False
    assert all(row["source_id"] == "pr-9" and row["line"] > 0 for row in recovered.provenance)
    assert len(recovered.contract_digest) == 64
    assert len(recovered.source_digest) == 64


def test_intent_recovery_refuses_contradictory_material_path() -> None:
    with pytest.raises(ValueError, match="intent_path_contradiction:src/engine.py"):
        recover_intent(
            [
                IntentEvidence(
                    source_id="issue",
                    source_uri="fixture://issue",
                    text="Required paths: src/engine.py\nForbidden paths: src/engine.py\n",
                )
            ]
        )


def test_intent_recovery_refuses_when_no_material_requirement_is_explicit() -> None:
    with pytest.raises(ValueError, match="intent_material_requirements_not_recovered"):
        recover_intent(
            [IntentEvidence(source_id="issue", source_uri="fixture://issue", text="Max files changed: 3\n")]
        )


def test_incremental_monitor_surfaces_new_violation_and_caches_only_identical_evaluation_input(tmp_path: Path) -> None:
    repo, base = make_repo(tmp_path)
    (repo / "src" / "engine.py").write_text(
        "def apply_patch(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "aligned")
    aligned_head = git(repo, "rev-parse", "HEAD")

    state = tmp_path / "monitor.json"
    monitor = PatchIntentMonitor(state)
    passing_tests = [{"name": "unit", "argv": [sys.executable, "-c", "raise SystemExit(0)"]}]
    first = monitor.check(
        repo=repo,
        base_ref=base,
        head_ref=aligned_head,
        subject_id="task",
        intent=recovered_contract(),
        tests=passing_tests,
        budget=0.0,
    )
    assert first.changed is True
    assert first.decision == "ALLOW"
    assert first.sequence == 1

    unchanged = monitor.check(
        repo=repo,
        base_ref=base,
        head_ref=aligned_head,
        subject_id="task",
        intent=recovered_contract(),
        tests=passing_tests,
        budget=0.0,
    )
    assert unchanged.changed is False
    assert unchanged.sequence == 1
    assert unchanged.decision == "ALLOW"

    changed_test_plan = monitor.check(
        repo=repo,
        base_ref=base,
        head_ref=aligned_head,
        subject_id="task",
        intent=recovered_contract(),
        tests=[{"name": "unit", "argv": [sys.executable, "-c", "raise SystemExit(99)"]}],
        budget=0.0,
    )
    assert changed_test_plan.changed is True
    assert changed_test_plan.sequence == 2
    assert changed_test_plan.decision == "REFUSE"
    assert "required_test_failed:unit" in changed_test_plan.introduced_reasons

    (repo / "infra" / "prod").mkdir(parents=True)
    (repo / "infra" / "prod" / "main.tf").write_text("resource = true\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "forbidden")
    forbidden_head = git(repo, "rev-parse", "HEAD")
    second = monitor.check(
        repo=repo,
        base_ref=base,
        head_ref=forbidden_head,
        subject_id="task",
        intent=recovered_contract(),
        tests=passing_tests,
        budget=0.0,
    )
    assert second.changed is True
    assert second.sequence == 3
    assert second.decision == "REFUSE"
    assert "forbidden_path_touched:infra/prod/main.tf" in second.introduced_reasons
    assert "required_test_failed:unit" in second.cleared_reasons
    assert len(second.transition_digest) == 64


def test_monitor_re_evaluates_unchanged_patch_when_intent_changes(tmp_path: Path) -> None:
    repo, base = make_repo(tmp_path)
    (repo / "src" / "engine.py").write_text("def apply_patch(value):\n    return value + 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "aligned")
    head = git(repo, "rev-parse", "HEAD")
    monitor = PatchIntentMonitor(tmp_path / "state.json")
    tests = [{"name": "unit", "argv": [sys.executable, "-c", "raise SystemExit(0)"]}]
    first = monitor.check(repo=repo, base_ref=base, head_ref=head, subject_id="task", intent=recovered_contract(), tests=tests)
    changed_intent = dict(recovered_contract())
    changed_intent["forbidden_paths"] = ["src/engine.py"]
    second = monitor.check(repo=repo, base_ref=base, head_ref=head, subject_id="task", intent=changed_intent, tests=tests)
    assert first.decision == "ALLOW"
    assert second.changed is True
    assert second.sequence == 2
    assert second.decision == "REFUSE"
    assert "forbidden_path_touched:src/engine.py" in second.introduced_reasons


def test_labeled_review_benchmark_has_zero_false_allows_and_full_fixture_accuracy() -> None:
    result = run_review_benchmark(load_review_cases(BENCHMARK))
    assert result.total == 8
    assert result.correct == 8
    assert result.accuracy == 1.0
    assert result.false_allows == 0
    assert result.false_refuses == 0
    assert result.false_allow_rate == 0.0
    assert len(result.dataset_digest) == 64
    assert len(result.benchmark_digest) == 64


def test_review_dataset_refuses_duplicate_case_identity(tmp_path: Path) -> None:
    payload = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    payload["cases"].append(dict(payload["cases"][0]))
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_review_case_id"):
        load_review_cases(path)
