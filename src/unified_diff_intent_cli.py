"""CLI for the preserved unified-diff intent engine."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from unified_diff_intent import PatchIntentTwin, PatchIntentTwinRequest


def _load(path: str) -> dict:
    if path == "-":
        data = json.load(sys.stdin)
    else:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("input_must_be_object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a real unified diff against a machine-readable intent contract")
    parser.add_argument("input", help="JSON request path, or - for stdin")
    args = parser.parse_args(argv)
    try:
        data = _load(args.input)
        subject_id = str(data.get("subject_id") or "").strip()
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {
            key: data[key]
            for key in ("intent", "unified_diff", "tests", "receipts", "cost", "expected_intent_digest")
            if key in data
        }
        request = PatchIntentTwinRequest(
            subject_id=subject_id,
            payload=payload,
            budget=float(data.get("budget", 1.0)),
            grant_id=data.get("grant_id"),
            not_after=data.get("not_after"),
        )
        receipt = PatchIntentTwin().evaluate(request)
    except Exception as exc:
        print(json.dumps({"continuation": "enabled", "decision": "CONTINUATION_REQUIRED", "resolution_work": [f"resolve_unified_diff_cli_input:{type(exc).__name__}"]}, sort_keys=True))
        return 0
    rendered = receipt.as_dict()
    rendered["continuation"] = "enabled"
    rendered["resolution_work"] = [] if receipt.decision.value == "ALLOW" else list(receipt.reasons)
    print(json.dumps(rendered, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
