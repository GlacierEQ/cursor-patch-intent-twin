"""Patch Intent Twin — deterministic intent-to-patch drift engine.

Independent reference implementation inspired by the problem of giving coding
agents more repository authority without losing reviewability.  The engine
binds a machine-readable intent contract to an observed patch and fails closed
when the patch crosses forbidden surfaces, misses required tests or symbols,
exceeds change budgets, or drifts beyond the operator's tolerance.

No Cursor / Anysphere affiliation or proprietary integration is implied.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


def _digest(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _finite(value: float, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label}_not_numeric")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label}_not_finite")
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label}_must_be_list")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label}_invalid_item")
        out.append(item.strip())
    return tuple(out)


def _matches(path: str, patterns: Iterable[str]) -> bool:
    """Match git-style paths against simple glob patterns, including dir/**."""
    for pattern in patterns:
        if fnmatch.fnmatchcase(path, pattern):
            return True
        if pattern.endswith("/**") and (path == pattern[:-3].rstrip("/") or path.startswith(pattern[:-2])):
            return True
    return False


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REFUSE = "REFUSE"


@dataclass(frozen=True)
class PatchIntentTwinRequest:
    """Evaluation envelope.

    `payload` must contain `intent` and `patch` objects.  Keeping this envelope
    compatible with the earlier surface lets existing operate/proof machinery
    call the deeper mechanism without a second control plane.
    """

    subject_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    budget: float = 0.0
    grant_id: str | None = None
    not_after: float | None = None


@dataclass(frozen=True)
class PatchIntentTwinReceipt:
    decision: Decision
    reasons: tuple[str, ...]
    digest: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "digest": self.digest,
            "metrics": self.metrics,
        }


@dataclass(frozen=True)
class _IntentContract:
    required_paths: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    required_tests: tuple[str, ...]
    forbidden_dependencies: tuple[str, ...]
    required_symbols: Mapping[str, tuple[str, ...]]
    max_files_changed: int
    max_deletions: int
    allow_file_deletion: bool


@dataclass(frozen=True)
class _ChangedFile:
    path: str
    status: str
    additions: int
    deletions: int
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class _TestObservation:
    name: str
    status: str


class PatchIntentTwin:
    """Compare an evolving patch with the operator's machine-readable intent."""

    VALID_FILE_STATUS = frozenset({"added", "modified", "deleted", "renamed"})
    VALID_TEST_STATUS = frozenset({"passed", "failed", "skipped"})

    def _parse_intent(self, raw: Any) -> _IntentContract:
        if not isinstance(raw, dict):
            raise ValueError("intent_missing")
        required_symbols_raw = raw.get("required_symbols") or {}
        if not isinstance(required_symbols_raw, dict):
            raise ValueError("required_symbols_must_be_object")
        required_symbols: dict[str, tuple[str, ...]] = {}
        for path, symbols in required_symbols_raw.items():
            if not isinstance(path, str) or not path.strip():
                raise ValueError("required_symbol_path_invalid")
            required_symbols[path.strip()] = _strings(symbols, "required_symbols")

        max_files = raw.get("max_files_changed", 50)
        max_deletions = raw.get("max_deletions", 10_000)
        if not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 1:
            raise ValueError("max_files_changed_invalid")
        if not isinstance(max_deletions, int) or isinstance(max_deletions, bool) or max_deletions < 0:
            raise ValueError("max_deletions_invalid")
        allow_delete = raw.get("allow_file_deletion", False)
        if not isinstance(allow_delete, bool):
            raise ValueError("allow_file_deletion_invalid")

        return _IntentContract(
            required_paths=_strings(raw.get("required_paths"), "required_paths"),
            allowed_paths=_strings(raw.get("allowed_paths"), "allowed_paths"),
            forbidden_paths=_strings(raw.get("forbidden_paths"), "forbidden_paths"),
            required_tests=_strings(raw.get("required_tests"), "required_tests"),
            forbidden_dependencies=_strings(raw.get("forbidden_dependencies"), "forbidden_dependencies"),
            required_symbols=required_symbols,
            max_files_changed=max_files,
            max_deletions=max_deletions,
            allow_file_deletion=allow_delete,
        )

    def _parse_patch(self, raw: Any) -> tuple[tuple[_ChangedFile, ...], tuple[_TestObservation, ...], tuple[str, ...]]:
        if not isinstance(raw, dict):
            raise ValueError("patch_missing")
        changed_raw = raw.get("changed_files")
        if not isinstance(changed_raw, list) or not changed_raw:
            raise ValueError("changed_files_missing")
        changed: list[_ChangedFile] = []
        seen_paths: set[str] = set()
        for row in changed_raw:
            if not isinstance(row, dict):
                raise ValueError("changed_file_invalid")
            path = row.get("path")
            status = row.get("status")
            if not isinstance(path, str) or not path.strip() or path.startswith("/") or ".." in path.split("/"):
                raise ValueError("changed_file_path_invalid")
            path = path.strip()
            if path in seen_paths:
                raise ValueError("duplicate_changed_file")
            seen_paths.add(path)
            if status not in self.VALID_FILE_STATUS:
                raise ValueError("changed_file_status_invalid")
            additions = row.get("additions", 0)
            deletions = row.get("deletions", 0)
            for label, value in (("additions", additions), ("deletions", deletions)):
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"{label}_invalid")
            changed.append(
                _ChangedFile(
                    path=path,
                    status=status,
                    additions=additions,
                    deletions=deletions,
                    symbols=_strings(row.get("symbols"), "symbols"),
                )
            )

        tests_raw = raw.get("tests") or []
        if not isinstance(tests_raw, list):
            raise ValueError("tests_must_be_list")
        tests: list[_TestObservation] = []
        seen_tests: set[str] = set()
        for row in tests_raw:
            if not isinstance(row, dict):
                raise ValueError("test_observation_invalid")
            name = row.get("name")
            status = row.get("status")
            if not isinstance(name, str) or not name.strip() or status not in self.VALID_TEST_STATUS:
                raise ValueError("test_observation_invalid")
            if name in seen_tests:
                raise ValueError("duplicate_test_observation")
            seen_tests.add(name)
            tests.append(_TestObservation(name=name, status=status))

        dependencies_added = _strings(raw.get("dependencies_added"), "dependencies_added")
        return tuple(changed), tuple(tests), dependencies_added

    def evaluate(self, req: PatchIntentTwinRequest) -> PatchIntentTwinReceipt:
        reasons: list[str] = []
        if not isinstance(req.subject_id, str) or not req.subject_id.strip():
            reasons.append("subject_id_missing")
        try:
            drift_budget = _finite(req.budget, "budget")
        except ValueError as exc:
            reasons.append(str(exc))
            drift_budget = -1.0
        if not 0.0 <= drift_budget <= 1.0:
            reasons.append("budget_out_of_range")

        try:
            intent = self._parse_intent(req.payload.get("intent") if isinstance(req.payload, dict) else None)
            changed, tests, dependencies_added = self._parse_patch(
                req.payload.get("patch") if isinstance(req.payload, dict) else None
            )
        except ValueError as exc:
            reasons.append(str(exc))
            body = {"subject_id": req.subject_id, "decision": Decision.REFUSE.value, "reasons": reasons}
            return PatchIntentTwinReceipt(
                decision=Decision.REFUSE,
                reasons=tuple(dict.fromkeys(reasons)),
                digest=_digest(body),
                metrics={"alignment_score": 0.0, "drift_score": 1.0, "contract_valid": False},
            )

        changed_paths = {row.path for row in changed}
        deleted_paths = {row.path for row in changed if row.status == "deleted"}
        symbols_by_path = {row.path: set(row.symbols) for row in changed}
        test_status = {row.name: row.status for row in tests}

        checks_total = 0
        checks_satisfied = 0
        soft_gaps: list[str] = []
        hard_violations: list[str] = []

        for pattern in intent.required_paths:
            checks_total += 1
            if any(_matches(path, (pattern,)) for path in changed_paths):
                checks_satisfied += 1
            else:
                soft_gaps.append(f"required_path_missing:{pattern}")

        for path, required_symbols in intent.required_symbols.items():
            for symbol in required_symbols:
                checks_total += 1
                if symbol in symbols_by_path.get(path, set()):
                    checks_satisfied += 1
                else:
                    soft_gaps.append(f"required_symbol_missing:{path}:{symbol}")

        for test_name in intent.required_tests:
            checks_total += 1
            status = test_status.get(test_name)
            if status == "passed":
                checks_satisfied += 1
            elif status == "failed":
                hard_violations.append(f"required_test_failed:{test_name}")
            else:
                soft_gaps.append(f"required_test_missing:{test_name}")

        for row in changed:
            if intent.forbidden_paths and _matches(row.path, intent.forbidden_paths):
                hard_violations.append(f"forbidden_path_touched:{row.path}")
            if intent.allowed_paths and not _matches(row.path, intent.allowed_paths):
                hard_violations.append(f"path_outside_allowed_surface:{row.path}")

        if deleted_paths and not intent.allow_file_deletion:
            hard_violations.extend(f"file_deletion_forbidden:{path}" for path in sorted(deleted_paths))
        if len(changed) > intent.max_files_changed:
            hard_violations.append("max_files_changed_exceeded")
        total_deletions = sum(row.deletions for row in changed)
        if total_deletions > intent.max_deletions:
            hard_violations.append("max_deletions_exceeded")
        forbidden_deps = sorted(set(dependencies_added).intersection(intent.forbidden_dependencies))
        hard_violations.extend(f"forbidden_dependency_added:{dep}" for dep in forbidden_deps)
        failed_tests = sorted(row.name for row in tests if row.status == "failed")
        hard_violations.extend(f"test_failed:{name}" for name in failed_tests)

        alignment_score = 1.0 if checks_total == 0 else checks_satisfied / checks_total
        drift_score = round(1.0 - alignment_score, 12)
        if drift_score > drift_budget:
            soft_gaps.append("drift_budget_exceeded")

        reasons.extend(hard_violations)
        reasons.extend(soft_gaps)
        decision = Decision.REFUSE if reasons else Decision.ALLOW

        intent_body = {
            "required_paths": intent.required_paths,
            "allowed_paths": intent.allowed_paths,
            "forbidden_paths": intent.forbidden_paths,
            "required_tests": intent.required_tests,
            "forbidden_dependencies": intent.forbidden_dependencies,
            "required_symbols": {k: list(v) for k, v in sorted(intent.required_symbols.items())},
            "max_files_changed": intent.max_files_changed,
            "max_deletions": intent.max_deletions,
            "allow_file_deletion": intent.allow_file_deletion,
        }
        patch_body = {
            "changed_files": [row.__dict__ for row in changed],
            "tests": [row.__dict__ for row in tests],
            "dependencies_added": dependencies_added,
        }
        body = {
            "subject_id": req.subject_id,
            "intent_digest": _digest(intent_body),
            "patch_digest": _digest(patch_body),
            "decision": decision.value,
            "reasons": reasons,
            "drift_budget": drift_budget,
            "grant_id": req.grant_id,
        }
        return PatchIntentTwinReceipt(
            decision=decision,
            reasons=tuple(reasons or ["patch_matches_intent"]),
            digest=_digest(body),
            metrics={
                "contract_valid": True,
                "alignment_score": round(alignment_score, 12),
                "drift_score": drift_score,
                "drift_budget": drift_budget,
                "checks_total": checks_total,
                "checks_satisfied": checks_satisfied,
                "changed_files": len(changed),
                "total_additions": sum(row.additions for row in changed),
                "total_deletions": total_deletions,
                "hard_violation_count": len(hard_violations),
                "soft_gap_count": len(soft_gaps),
                "intent_digest": body["intent_digest"],
                "patch_digest": body["patch_digest"],
            },
        )


Mechanism = PatchIntentTwin
