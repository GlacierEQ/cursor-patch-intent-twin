"""Incrementally monitor a real git patch against recovered intent.

Each check resolves the current git head, executes named tests, evaluates the
real observed patch, compares the decision with the prior observation, and
atomically persists a transition receipt. The monitor surfaces newly introduced
and cleared violations instead of waiting for a final patch review.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from git_patch_adapter import observe_git_patch
from patch_intent_twin import PatchIntentTwin, PatchIntentTwinRequest
from test_run_adapter import run_test_matrix


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("monitor_state_invalid")
    return data


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


@dataclass(frozen=True)
class PatchTransition:
    sequence: int
    base_sha: str
    head_sha: str
    observation_digest: str
    decision: str
    reasons: tuple[str, ...]
    introduced_reasons: tuple[str, ...]
    cleared_reasons: tuple[str, ...]
    changed: bool
    transition_digest: str
    test_receipts: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "observation_digest": self.observation_digest,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "introduced_reasons": list(self.introduced_reasons),
            "cleared_reasons": list(self.cleared_reasons),
            "changed": self.changed,
            "transition_digest": self.transition_digest,
            "test_receipts": [dict(row) for row in self.test_receipts],
        }


class PatchIntentMonitor:
    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path)

    def check(
        self,
        *,
        repo: str | Path,
        base_ref: str,
        head_ref: str,
        subject_id: str,
        intent: Mapping[str, Any],
        tests: Iterable[Mapping[str, Any]],
        budget: float = 0.0,
    ) -> PatchTransition:
        previous = _load_state(self.state_path)
        observation = observe_git_patch(repo, base_ref, head_ref)
        previous_digest = None if previous is None else previous.get("observation_digest")
        changed = previous_digest != observation.observation_digest

        if not changed and previous is not None:
            return PatchTransition(
                sequence=int(previous.get("sequence", 0)),
                base_sha=observation.base_sha,
                head_sha=observation.head_sha,
                observation_digest=observation.observation_digest,
                decision=str(previous.get("decision")),
                reasons=tuple(previous.get("reasons") or []),
                introduced_reasons=(),
                cleared_reasons=(),
                changed=False,
                transition_digest=str(previous.get("transition_digest")),
                test_receipts=tuple(previous.get("test_receipts") or []),
            )

        test_receipts = run_test_matrix(tests, cwd=repo)
        patch = observation.as_patch()
        patch["tests"] = [receipt.as_patch_test() for receipt in test_receipts]
        patch["test_receipts"] = [receipt.as_dict() for receipt in test_receipts]
        evaluation = PatchIntentTwin().evaluate(
            PatchIntentTwinRequest(
                subject_id=subject_id,
                payload={"intent": dict(intent), "patch": patch},
                budget=budget,
            )
        )
        reasons = tuple(evaluation.reasons)
        previous_reasons = set(previous.get("reasons") or []) if previous else set()
        current_reasons = set(reasons)
        introduced = tuple(sorted(current_reasons - previous_reasons))
        cleared = tuple(sorted(previous_reasons - current_reasons))
        sequence = int(previous.get("sequence", 0)) + 1 if previous else 1
        core = {
            "sequence": sequence,
            "base_sha": observation.base_sha,
            "head_sha": observation.head_sha,
            "observation_digest": observation.observation_digest,
            "decision": evaluation.decision.value,
            "reasons": list(reasons),
            "introduced_reasons": list(introduced),
            "cleared_reasons": list(cleared),
            "test_receipt_digests": [receipt.receipt_digest for receipt in test_receipts],
            "evaluation_digest": evaluation.digest,
        }
        transition_digest = _digest(core)
        state = {
            **core,
            "transition_digest": transition_digest,
            "test_receipts": [receipt.as_dict() for receipt in test_receipts],
        }
        _atomic_json(self.state_path, state)
        return PatchTransition(
            sequence=sequence,
            base_sha=observation.base_sha,
            head_sha=observation.head_sha,
            observation_digest=observation.observation_digest,
            decision=evaluation.decision.value,
            reasons=reasons,
            introduced_reasons=introduced,
            cleared_reasons=cleared,
            changed=True,
            transition_digest=transition_digest,
            test_receipts=tuple(receipt.as_dict() for receipt in test_receipts),
        )
