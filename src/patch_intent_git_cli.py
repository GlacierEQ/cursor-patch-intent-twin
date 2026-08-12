"""End-to-end Patch Intent Twin CLI for a real git commit range.

This command resolves exact git SHAs, observes the actual patch, executes named
required tests, then feeds those facts into PatchIntentTwin. Caller-supplied
changed-file or test-status claims are not accepted on this path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from git_patch_adapter import GitObservationError, observe_git_patch
from patch_intent_twin import Decision, PatchIntentTwin, PatchIntentTwinRequest
from test_run_adapter import run_test_matrix


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("config_must_be_object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a real git patch against machine-readable change intent"
    )
    parser.add_argument("config", type=Path, help="JSON containing subject_id, intent, and test command definitions")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True, help="base git ref/commit")
    parser.add_argument("--head", default="HEAD", help="head git ref/commit")
    parser.add_argument("--budget", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        config = _load(args.config)
        subject_id = config.get("subject_id")
        intent = config.get("intent")
        tests = config.get("tests") or []
        if not isinstance(tests, list):
            raise ValueError("tests_must_be_list")

        observation = observe_git_patch(args.repo, args.base, args.head)
        test_receipts = run_test_matrix(tests, cwd=args.repo)
        patch = observation.as_patch()
        patch["tests"] = [receipt.as_patch_test() for receipt in test_receipts]
        patch["test_receipts"] = [receipt.as_dict() for receipt in test_receipts]

        receipt = PatchIntentTwin().evaluate(
            PatchIntentTwinRequest(
                subject_id=subject_id,
                payload={"intent": intent, "patch": patch},
                budget=args.budget,
            )
        )
        rendered = {
            "decision": receipt.decision.value,
            "reasons": list(receipt.reasons),
            "digest": receipt.digest,
            "metrics": receipt.metrics,
            "git_observation": {
                "base_sha": observation.base_sha,
                "head_sha": observation.head_sha,
                "observation_digest": observation.observation_digest,
            },
            "test_receipts": [item.as_dict() for item in test_receipts],
        }
        text = json.dumps(rendered, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        sys.stdout.write(text)
        return 0 if receipt.decision is Decision.ALLOW else 2
    except (OSError, TypeError, ValueError, json.JSONDecodeError, GitObservationError) as exc:
        sys.stderr.write(json.dumps({"decision": "ERROR", "reason": str(exc)}, sort_keys=True) + "\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
