from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from git_patch_adapter import GitObservationError, observe_git_patch
from test_run_adapter import execute_named_test, run_test_matrix


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def make_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "crystal@example.invalid")
    git(repo, "config", "user.name", "Crystallization Test")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "engine.py").write_text("def old():\n    return 1\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("pytest==8.0.0\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")

    (repo / "src" / "engine.py").write_text(
        "def old():\n    return 1\n\ndef apply_patch(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_engine.py").write_text(
        "from src.engine import apply_patch\n\ndef test_apply_patch():\n    assert apply_patch(1) == 2\n",
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("pytest==8.0.0\npackaging==24.0\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "implement")
    head = git(repo, "rev-parse", "HEAD")
    return repo, base, head


def test_observes_exact_git_patch_and_symbols(tmp_path: Path) -> None:
    repo, base, head = make_repo(tmp_path)
    observation = observe_git_patch(repo, base, head)

    assert observation.base_sha == base
    assert observation.head_sha == head
    assert len(observation.observation_digest) == 64
    by_path = {row["path"]: row for row in observation.changed_files}
    assert by_path["src/engine.py"]["status"] == "modified"
    assert "apply_patch" in by_path["src/engine.py"]["symbols"]
    assert by_path["tests/test_engine.py"]["status"] == "added"
    assert any(item.startswith("requirements.txt:") for item in observation.dependencies_added)


def test_refuses_empty_commit_range(tmp_path: Path) -> None:
    repo, _, head = make_repo(tmp_path)
    with pytest.raises(GitObservationError, match="empty_commit_range"):
        observe_git_patch(repo, head, head)


def test_real_test_receipt_binds_pass_output_and_command(tmp_path: Path) -> None:
    receipt = execute_named_test(
        "unit",
        [sys.executable, "-c", "print('verified'); raise SystemExit(0)"],
        cwd=tmp_path,
    )
    assert receipt.status == "passed"
    assert receipt.returncode == 0
    assert "verified" in receipt.stdout_tail
    assert len(receipt.stdout_sha256) == 64
    assert len(receipt.receipt_digest) == 64


def test_real_test_receipt_records_failure(tmp_path: Path) -> None:
    receipt = execute_named_test(
        "adversarial",
        [sys.executable, "-c", "import sys; print('failed-path'); sys.exit(7)"],
        cwd=tmp_path,
    )
    assert receipt.status == "failed"
    assert receipt.returncode == 7
    assert "failed-path" in receipt.stdout_tail


def test_test_matrix_rejects_duplicate_names(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "raise SystemExit(0)"]
    with pytest.raises(ValueError, match="duplicate_test_name"):
        run_test_matrix(
            [{"name": "unit", "argv": command}, {"name": "unit", "argv": command}],
            cwd=tmp_path,
        )


def test_test_matrix_produces_patch_compatible_status(tmp_path: Path) -> None:
    receipts = run_test_matrix(
        [
            {"name": "unit", "argv": [sys.executable, "-c", "raise SystemExit(0)"]},
            {"name": "adversarial", "argv": [sys.executable, "-c", "raise SystemExit(0)"]},
        ],
        cwd=tmp_path,
    )
    assert [receipt.as_patch_test() for receipt in receipts] == [
        {"name": "unit", "status": "passed"},
        {"name": "adversarial", "status": "passed"},
    ]
