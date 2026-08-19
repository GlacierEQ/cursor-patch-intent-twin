"""Evaluate Patch Intent Twin against independently labeled review cases."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from patch_intent_twin import PatchIntentTwin, PatchIntentTwinRequest


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReviewCase:
    case_id: str
    source_uri: str
    expected_decision: str
    subject_id: str
    intent: Mapping[str, Any]
    patch: Mapping[str, Any]
    budget: float = 0.0

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ReviewCase":
        if not isinstance(raw, Mapping):
            raise ValueError("review_case_invalid")
        expected = raw.get("expected_decision")
        if expected not in {"ALLOW", "REFUSE"}:
            raise ValueError("expected_decision_invalid")
        for key in ("case_id", "source_uri", "subject_id"):
            if not isinstance(raw.get(key), str) or not str(raw[key]).strip():
                raise ValueError(f"{key}_missing")
        intent = raw.get("intent")
        patch = raw.get("patch")
        if not isinstance(intent, Mapping) or not isinstance(patch, Mapping):
            raise ValueError("review_case_payload_invalid")
        return cls(
            case_id=str(raw["case_id"]),
            source_uri=str(raw["source_uri"]),
            expected_decision=expected,
            subject_id=str(raw["subject_id"]),
            intent=dict(intent),
            patch=dict(patch),
            budget=float(raw.get("budget", 0.0)),
        )


@dataclass(frozen=True)
class BenchmarkResult:
    total: int
    correct: int
    false_allows: int
    false_refuses: int
    accuracy: float
    false_allow_rate: float
    false_refuse_rate: float
    cases: tuple[dict[str, Any], ...]
    dataset_digest: str
    benchmark_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "glaciereq.patch-intent-review-benchmark.v1",
            "total": self.total,
            "correct": self.correct,
            "false_allows": self.false_allows,
            "false_refuses": self.false_refuses,
            "accuracy": self.accuracy,
            "false_allow_rate": self.false_allow_rate,
            "false_refuse_rate": self.false_refuse_rate,
            "cases": [dict(row) for row in self.cases],
            "dataset_digest": self.dataset_digest,
            "benchmark_digest": self.benchmark_digest,
        }


def load_review_cases(path: str | Path) -> tuple[ReviewCase, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("review_dataset_invalid")
    cases = tuple(ReviewCase.from_dict(row) for row in payload["cases"])
    if not cases:
        raise ValueError("review_dataset_empty")
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_review_case_id")
    return cases


def _historical_decision_label(decision: str) -> str:
    """Map active continuation receipts to historical benchmark labels without reviving refusal behavior."""
    return {"ALIGNED": "ALLOW", "CONTINUATION_REQUIRED": "REFUSE"}.get(decision, decision)


def run_review_benchmark(cases: Iterable[ReviewCase]) -> BenchmarkResult:
    case_list = list(cases)
    if not case_list:
        raise ValueError("review_dataset_empty")
    engine = PatchIntentTwin()
    rows: list[dict[str, Any]] = []
    false_allows = 0
    false_refuses = 0
    correct = 0
    canonical_dataset: list[dict[str, Any]] = []
    for case in case_list:
        receipt = engine.evaluate(
            PatchIntentTwinRequest(
                subject_id=case.subject_id,
                payload={"intent": dict(case.intent), "patch": dict(case.patch)},
                budget=case.budget,
            )
        )
        observed = receipt.decision.value
        historical_observed = _historical_decision_label(observed)
        is_correct = historical_observed == case.expected_decision
        correct += int(is_correct)
        false_allows += int(historical_observed == "ALLOW" and case.expected_decision == "REFUSE")
        false_refuses += int(historical_observed == "REFUSE" and case.expected_decision == "ALLOW")
        row = {
            "case_id": case.case_id,
            "source_uri": case.source_uri,
            "expected_decision": case.expected_decision,
            "observed_decision": observed,
            "historical_comparison_decision": historical_observed,
            "continuation": receipt.continuation,
            "correct": is_correct,
            "reasons": list(receipt.reasons),
            "receipt_digest": receipt.digest,
        }
        rows.append(row)
        canonical_dataset.append({
            "case_id": case.case_id,
            "source_uri": case.source_uri,
            "expected_decision": case.expected_decision,
            "subject_id": case.subject_id,
            "intent": dict(case.intent),
            "patch": dict(case.patch),
            "budget": case.budget,
        })
    total = len(case_list)
    expected_refuse = sum(case.expected_decision == "REFUSE" for case in case_list)
    expected_allow = sum(case.expected_decision == "ALLOW" for case in case_list)
    dataset_digest = _digest(sorted(canonical_dataset, key=lambda row: row["case_id"]))
    core = {
        "total": total,
        "correct": correct,
        "false_allows": false_allows,
        "false_refuses": false_refuses,
        "cases": rows,
        "dataset_digest": dataset_digest,
    }
    return BenchmarkResult(
        total=total,
        correct=correct,
        false_allows=false_allows,
        false_refuses=false_refuses,
        accuracy=round(correct / total, 12),
        false_allow_rate=round(false_allows / expected_refuse, 12) if expected_refuse else 0.0,
        false_refuse_rate=round(false_refuses / expected_allow, 12) if expected_allow else 0.0,
        cases=tuple(rows),
        dataset_digest=dataset_digest,
        benchmark_digest=_digest(core),
    )
