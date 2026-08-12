"""Executable patch-intent twin.

The twin compiles requested-change intent into deterministic path, test, size,
and content constraints. It can consume either a normalized patch description
or a real unified diff and emits a receipt showing whether the evolving patch
still satisfies the requested change.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REFUSE = "REFUSE"


@dataclass(frozen=True)
class PatchIntentTwinRequest:
    subject_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    budget: float = 1.0
    grant_id: str | None = None
    not_after: float | None = None


@dataclass(frozen=True)
class PatchIntentTwinReceipt:
    decision: Decision
    reasons: tuple[str, ...]
    digest: str
    metrics: dict[str, Any] = field(default_factory=dict)
    intent_digest: str | None = None
    patch_digest: str | None = None
    merge_blocked: bool = True
    changed_paths: tuple[str, ...] = ()
    checks: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "digest": self.digest,
            "metrics": self.metrics,
            "intent_digest": self.intent_digest,
            "patch_digest": self.patch_digest,
            "merge_blocked": self.merge_blocked,
            "changed_paths": list(self.changed_paths),
            "checks": list(self.checks),
        }


class IntentSchemaError(ValueError):
    pass


class PatchIntentTwin:
    """Compile patch intent and continuously compare candidate patches to it."""

    MIN_BUDGET = 0.0

    @staticmethod
    def _patterns(value: Any, field_name: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise IntentSchemaError(f"{field_name}_not_list")
        patterns: set[str] = set()
        for raw in value:
            pattern = str(raw).strip()
            if not pattern:
                raise IntentSchemaError(f"{field_name}_contains_empty_pattern")
            patterns.add(pattern)
        return sorted(patterns)

    @staticmethod
    def _names(value: Any, field_name: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise IntentSchemaError(f"{field_name}_not_list")
        names: set[str] = set()
        for raw in value:
            name = str(raw).strip()
            if not name:
                raise IntentSchemaError(f"{field_name}_contains_empty_name")
            names.add(name)
        return sorted(names)

    @classmethod
    def _content_rules(cls, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise IntentSchemaError("content_rules_not_list")
        rules: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(value):
            if not isinstance(raw, dict):
                raise IntentSchemaError(f"content_rule_{index}_not_object")
            rule_id = str(raw.get("id", "")).strip()
            path = str(raw.get("path", "")).strip()
            if not rule_id:
                raise IntentSchemaError(f"content_rule_{index}_id_missing")
            if rule_id in seen:
                raise IntentSchemaError(f"content_rule_{rule_id}_duplicate")
            if not path:
                raise IntentSchemaError(f"content_rule_{rule_id}_path_missing")
            must_contain = cls._names(raw.get("must_contain"), "must_contain")
            must_not_contain = cls._names(
                raw.get("must_not_contain"),
                "must_not_contain",
            )
            if not must_contain and not must_not_contain:
                raise IntentSchemaError(f"content_rule_{rule_id}_empty")
            rules.append(
                {
                    "id": rule_id,
                    "path": path,
                    "must_contain": must_contain,
                    "must_not_contain": must_not_contain,
                }
            )
            seen.add(rule_id)
        return sorted(rules, key=lambda item: item["id"])

    @classmethod
    def compile_intent(cls, intent: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(intent, dict):
            raise IntentSchemaError("intent_not_object")
        compiled: dict[str, Any] = {
            "schema": "glaciereq.patch-intent.v1",
            "must_touch": cls._patterns(intent.get("must_touch"), "must_touch"),
            "forbidden_paths": cls._patterns(
                intent.get("forbidden_paths"),
                "forbidden_paths",
            ),
            "allowed_paths": cls._patterns(intent.get("allowed_paths"), "allowed_paths"),
            "required_tests": cls._names(
                intent.get("required_tests"),
                "required_tests",
            ),
            "required_receipts": cls._names(
                intent.get("required_receipts"),
                "required_receipts",
            ),
            "content_rules": cls._content_rules(intent.get("content_rules")),
        }
        for field_name in ("max_changed_files", "max_lines_changed"):
            raw = intent.get(field_name)
            if raw is None:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise IntentSchemaError(f"{field_name}_invalid") from exc
            if value < 0:
                raise IntentSchemaError(f"{field_name}_negative")
            compiled[field_name] = value
        if intent.get("max_cost") is not None:
            try:
                max_cost = float(intent["max_cost"])
            except (TypeError, ValueError) as exc:
                raise IntentSchemaError("max_cost_invalid") from exc
            if max_cost < 0:
                raise IntentSchemaError("max_cost_negative")
            compiled["max_cost"] = max_cost
        if not any(
            (
                compiled["must_touch"],
                compiled["forbidden_paths"],
                compiled["allowed_paths"],
                compiled["required_tests"],
                compiled["required_receipts"],
                compiled["content_rules"],
                "max_changed_files" in compiled,
                "max_lines_changed" in compiled,
            )
        ):
            raise IntentSchemaError("intent_has_no_constraints")
        compiled["intent_digest"] = _digest(compiled)
        return compiled

    @staticmethod
    def parse_unified_diff(diff_text: str) -> dict[str, Any]:
        """Parse changed paths and hunk content from a standard unified Git diff."""
        if not isinstance(diff_text, str) or not diff_text.strip():
            raise ValueError("unified_diff_empty")
        files: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None

        def finish() -> None:
            nonlocal current
            if current is not None and current.get("path"):
                current["added_text"] = "\n".join(current.pop("_added"))
                current["removed_text"] = "\n".join(current.pop("_removed"))
                files.append(current)
            current = None

        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                finish()
                parts = line.split(" ", 3)
                right = parts[3] if len(parts) >= 4 else ""
                path = right[2:] if right.startswith("b/") else right
                current = {
                    "path": path,
                    "status": "modified",
                    "additions": 0,
                    "deletions": 0,
                    "hunks": 0,
                    "_added": [],
                    "_removed": [],
                }
                continue
            if current is None:
                continue
            if line.startswith("new file mode "):
                current["status"] = "added"
                continue
            if line.startswith("deleted file mode "):
                current["status"] = "deleted"
                continue
            if line.startswith("+++ "):
                target = line[4:].strip()
                if target == "/dev/null":
                    current["status"] = "deleted"
                elif target.startswith("b/"):
                    current["path"] = target[2:]
                continue
            if line.startswith("--- "):
                source = line[4:].strip()
                if source == "/dev/null":
                    current["status"] = "added"
                continue
            if line.startswith("@@"):
                current["hunks"] += 1
                continue
            if line.startswith("+") and not line.startswith("+++"):
                current["additions"] += 1
                current["_added"].append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                current["deletions"] += 1
                current["_removed"].append(line[1:])
        finish()
        if not files:
            raise ValueError("unified_diff_has_no_files")
        return {"files": files}

    @staticmethod
    def _normalize_patch(patch: Any) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("patch_not_object")
        raw_files = patch.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise ValueError("patch_files_missing")
        files: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_files):
            if not isinstance(raw, dict):
                raise ValueError(f"patch_file_{index}_not_object")
            path = str(raw.get("path", "")).strip()
            if not path:
                raise ValueError(f"patch_file_{index}_path_missing")
            if path in seen:
                raise ValueError(f"patch_file_duplicate:{path}")
            try:
                additions = int(raw.get("additions", 0))
                deletions = int(raw.get("deletions", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"patch_file_counts_invalid:{path}") from exc
            if additions < 0 or deletions < 0:
                raise ValueError(f"patch_file_counts_negative:{path}")
            files.append(
                {
                    "path": path,
                    "status": str(raw.get("status", "modified")).strip().lower(),
                    "additions": additions,
                    "deletions": deletions,
                    "hunks": int(raw.get("hunks", 0)),
                    "added_text": str(raw.get("added_text", "")),
                    "removed_text": str(raw.get("removed_text", "")),
                }
            )
            seen.add(path)
        return {"files": sorted(files, key=lambda item: item["path"])}

    @staticmethod
    def _matches_any(path: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)

    def evaluate(self, req: PatchIntentTwinRequest) -> PatchIntentTwinReceipt:
        reasons: list[str] = []
        if not str(req.subject_id or "").strip():
            reasons.append("subject_id_missing")
        if req.budget <= self.MIN_BUDGET:
            reasons.append("budget_non_positive")
        payload = req.payload if isinstance(req.payload, dict) else {}
        if not isinstance(req.payload, dict):
            reasons.append("payload_not_object")

        compiled: dict[str, Any] | None = None
        try:
            compiled = self.compile_intent(payload.get("intent", {}))
        except IntentSchemaError as exc:
            reasons.append(str(exc))

        patch: dict[str, Any] | None = None
        try:
            if payload.get("unified_diff") is not None:
                parsed = self.parse_unified_diff(str(payload["unified_diff"]))
                patch = self._normalize_patch(parsed)
            else:
                patch = self._normalize_patch(payload.get("patch"))
        except ValueError as exc:
            reasons.append(str(exc))

        tests = payload.get("tests", {})
        if not isinstance(tests, dict):
            reasons.append("tests_not_object")
            tests = {}
        receipts = payload.get("receipts", {})
        if not isinstance(receipts, dict):
            reasons.append("receipts_not_object")
            receipts = {}
        try:
            cost = float(payload.get("cost", 0.0))
        except (TypeError, ValueError):
            cost = 0.0
            reasons.append("cost_invalid")
        if cost < 0:
            reasons.append("cost_negative")

        checks: list[dict[str, Any]] = []
        changed_paths: list[str] = []
        patch_digest = None
        additions = 0
        deletions = 0
        if patch is not None:
            patch_digest = _digest(patch)
            changed_paths = [item["path"] for item in patch["files"]]
            additions = sum(item["additions"] for item in patch["files"])
            deletions = sum(item["deletions"] for item in patch["files"])

        intent_digest = compiled["intent_digest"] if compiled else None
        if compiled is not None and patch is not None:
            expected = str(payload.get("expected_intent_digest", "")).strip()
            if expected and expected != intent_digest:
                reasons.append("intent_digest_mismatch")

            max_cost = min(req.budget, float(compiled.get("max_cost", req.budget)))
            if cost > max_cost:
                reasons.append("budget_exceeded")

            for pattern in compiled["must_touch"]:
                passed = any(fnmatch.fnmatchcase(path, pattern) for path in changed_paths)
                checks.append({"kind": "must_touch", "target": pattern, "passed": passed})
                if not passed:
                    reasons.append(f"required_surface_untouched:{pattern}")

            for path in changed_paths:
                if self._matches_any(path, compiled["forbidden_paths"]):
                    checks.append({"kind": "forbidden_path", "target": path, "passed": False})
                    reasons.append(f"forbidden_path_changed:{path}")
                if compiled["allowed_paths"] and not self._matches_any(
                    path,
                    compiled["allowed_paths"],
                ):
                    checks.append({"kind": "allowed_path", "target": path, "passed": False})
                    reasons.append(f"path_outside_allowed_scope:{path}")

            if "max_changed_files" in compiled:
                passed = len(changed_paths) <= compiled["max_changed_files"]
                checks.append(
                    {
                        "kind": "max_changed_files",
                        "target": compiled["max_changed_files"],
                        "passed": passed,
                    }
                )
                if not passed:
                    reasons.append("changed_file_budget_exceeded")
            lines_changed = additions + deletions
            if "max_lines_changed" in compiled:
                passed = lines_changed <= compiled["max_lines_changed"]
                checks.append(
                    {
                        "kind": "max_lines_changed",
                        "target": compiled["max_lines_changed"],
                        "passed": passed,
                    }
                )
                if not passed:
                    reasons.append("line_change_budget_exceeded")

            for test_name in compiled["required_tests"]:
                passed = tests.get(test_name) is True
                checks.append({"kind": "required_test", "target": test_name, "passed": passed})
                if not passed:
                    reasons.append(f"test_failed_or_missing:{test_name}")
            for receipt_name in compiled["required_receipts"]:
                passed = bool(receipts.get(receipt_name))
                checks.append(
                    {"kind": "required_receipt", "target": receipt_name, "passed": passed}
                )
                if not passed:
                    reasons.append(f"receipt_missing:{receipt_name}")

            by_path = {item["path"]: item for item in patch["files"]}
            for rule in compiled["content_rules"]:
                matching = [
                    item
                    for path, item in by_path.items()
                    if fnmatch.fnmatchcase(path, rule["path"])
                ]
                if not matching:
                    checks.append(
                        {"kind": "content_rule", "target": rule["id"], "passed": False}
                    )
                    reasons.append(f"content_rule_path_missing:{rule['id']}")
                    continue
                added = "\n".join(item["added_text"] for item in matching)
                passed = True
                for token in rule["must_contain"]:
                    if token not in added:
                        passed = False
                        reasons.append(f"content_required_missing:{rule['id']}:{token}")
                for token in rule["must_not_contain"]:
                    if token in added:
                        passed = False
                        reasons.append(f"content_forbidden_added:{rule['id']}:{token}")
                checks.append(
                    {"kind": "content_rule", "target": rule["id"], "passed": passed}
                )

        decision = Decision.REFUSE if reasons else Decision.ALLOW
        if not reasons:
            reasons = ["patch_matches_intent"]
        failed_checks = sum(1 for check in checks if check["passed"] is False)
        body = {
            "schema": "glaciereq.patch-intent-evaluation.v1",
            "subject_id": req.subject_id,
            "intent_digest": intent_digest,
            "patch_digest": patch_digest,
            "decision": decision.value,
            "reasons": reasons,
            "checks": checks,
        }
        return PatchIntentTwinReceipt(
            decision=decision,
            reasons=tuple(reasons),
            digest=_digest(body),
            metrics={
                "changed_file_count": len(changed_paths),
                "additions": additions,
                "deletions": deletions,
                "lines_changed": additions + deletions,
                "check_count": len(checks),
                "failed_check_count": failed_checks,
                "budget": req.budget,
            },
            intent_digest=intent_digest,
            patch_digest=patch_digest,
            merge_blocked=decision is Decision.REFUSE,
            changed_paths=tuple(changed_paths),
            checks=tuple(checks),
        )

    @staticmethod
    def drift(previous_patch: dict[str, Any], current_patch: dict[str, Any]) -> dict[str, Any]:
        """Describe changed-file drift between two normalized patch snapshots."""
        previous = {item["path"] for item in PatchIntentTwin._normalize_patch(previous_patch)["files"]}
        current = {item["path"] for item in PatchIntentTwin._normalize_patch(current_patch)["files"]}
        result = {
            "added_paths": sorted(current - previous),
            "removed_paths": sorted(previous - current),
            "retained_paths": sorted(previous & current),
        }
        result["digest"] = _digest(result)
        return result


Mechanism = PatchIntentTwin
