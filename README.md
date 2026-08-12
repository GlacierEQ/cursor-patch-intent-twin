# Patch Intent Twin

Independent GlacierEQ portfolio implementation aligned to public Cursor / Anysphere operating themes. This repository is not affiliated with or endorsed by Cursor or Anysphere.

## Purpose

Keep an agent-generated patch aligned with the change a developer actually requested.

The twin compiles a machine-readable change contract and continuously compares an evolving patch against that intent. It can consume a normalized patch description or parse a real unified Git diff.

## Capabilities

The engine enforces:

- required surfaces that must be touched
- forbidden repository paths
- explicit allowed-path scope
- maximum changed-file count
- maximum total additions + deletions
- required tests
- required evidence receipts
- patch cost against request and intent budgets
- content rules for added code
- deterministic intent and patch digests
- expected-intent digest checks for silent scope mutation
- deterministic patch-drift receipts between snapshots

Content rules can require or forbid specific strings in **added** code for matching paths. Removed text does not magically become a new violation.

## Unified diff example

```diff
diff --git a/src/service.py b/src/service.py
--- a/src/service.py
+++ b/src/service.py
@@ -1 +1,2 @@
-old = True
+def run():
+    return "ok"
```

The parser records path, status, additions, deletions, hunks, added text, and removed text for each changed file.

## Run it

```bash
python scripts/operate.py
```

The built-in example evaluates a real two-file unified diff against path, test, receipt, size, and content constraints.

Use your own files:

```bash
python scripts/operate.py --input intent.json --diff change.diff --output receipt.json
```

Example intent:

```json
{
  "subject_id": "patch-42",
  "budget": 1.0,
  "intent": {
    "must_touch": ["src/*.py", "tests/*.py"],
    "forbidden_paths": [".github/**", "infra/**"],
    "allowed_paths": ["src/**", "tests/**"],
    "required_tests": ["unit"],
    "required_receipts": ["review"],
    "max_changed_files": 3,
    "max_lines_changed": 40,
    "content_rules": [
      {
        "id": "entrypoint",
        "path": "src/*.py",
        "must_contain": ["def run"],
        "must_not_contain": ["TODO"]
      }
    ]
  },
  "tests": {"unit": true},
  "receipts": {"review": "review-42"}
}
```

## Intent drift

`compile_intent()` produces a stable digest. Persist it with the task and pass it later as `expected_intent_digest`. If the allowed scope, constraints, or required proof changes silently, the evaluation blocks the patch.

## Patch drift

`PatchIntentTwin.drift(previous_patch, current_patch)` returns added, removed, and retained changed paths plus a deterministic digest. This makes scope expansion visible across agent iterations even before final merge evaluation.

## Verify behavior

```bash
python -m pytest -q
```

Tests cover unified-diff parsing, successful intent matching, forbidden and out-of-scope paths, required surfaces, tests, evidence receipts, budgets, content constraints, intent drift, duplicate paths/rules, line budgets, removed-vs-added content semantics, and patch-snapshot drift.

## Boundary

This is a vendor-neutral patch-intent library and CLI. It does not claim Cursor integration, proprietary agent access, or hosted deployment. It is designed to sit immediately before a merge or code-write boundary in any coding-agent control plane.
