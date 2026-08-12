#!/usr/bin/env python3
"""Evaluate a real unified diff against executable patch intent."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from patch_intent_twin import Decision, PatchIntentTwin, PatchIntentTwinRequest


DEMO_DIFF = """diff --git a/src/service.py b/src/service.py
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

DEMO = {
    "subject_id": "patch-demo-42",
    "budget": 1.0,
    "intent": {
        "must_touch": ["src/*.py", "tests/*.py"],
        "forbidden_paths": [".github/**", "infra/**"],
        "allowed_paths": ["src/**", "tests/**"],
        "required_tests": ["unit"],
        "required_receipts": ["review"],
        "max_changed_files": 3,
        "max_lines_changed": 20,
        "content_rules": [
            {
                "id": "entrypoint",
                "path": "src/*.py",
                "must_contain": ["def run"],
                "must_not_contain": ["TODO"],
            }
        ],
    },
    "tests": {"unit": True},
    "receipts": {"review": "review-demo"},
    "cost": 0.2,
}


def load_json(path: str | None) -> dict:
    if path is None:
        return DEMO
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("input JSON must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare an evolving patch against machine-readable change intent"
    )
    parser.add_argument("--input", help="intent/tests/receipts JSON; demo when omitted")
    parser.add_argument("--diff", help="unified Git diff; demo diff when omitted")
    parser.add_argument("--output", help="optional receipt output path")
    args = parser.parse_args()

    data = load_json(args.input)
    diff_text = Path(args.diff).read_text(encoding="utf-8") if args.diff else DEMO_DIFF
    payload = {
        "intent": data.get("intent", {}),
        "unified_diff": diff_text,
        "tests": data.get("tests", {}),
        "receipts": data.get("receipts", {}),
        "cost": data.get("cost", 0.0),
    }
    if data.get("expected_intent_digest"):
        payload["expected_intent_digest"] = data["expected_intent_digest"]
    request = PatchIntentTwinRequest(
        subject_id=str(data.get("subject_id", "")),
        payload=payload,
        budget=float(data.get("budget", 1.0)),
        grant_id=data.get("grant_id"),
        not_after=data.get("not_after"),
    )
    receipt = PatchIntentTwin().evaluate(request)
    rendered = json.dumps(receipt.as_dict(), indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if receipt.decision is Decision.ALLOW else 2


if __name__ == "__main__":
    raise SystemExit(main())
