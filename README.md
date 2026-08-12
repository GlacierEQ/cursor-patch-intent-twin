# Patch Intent Twin

Independent GlacierEQ portfolio exhibit aligned to **Cursor / Anysphere** operating themes.

> **Not affiliated.** This repository is not affiliated with, endorsed by, employed by, or deployed at Cursor / Anysphere. No proprietary access, production deployment, customer impact, or company partnership is claimed.

## Problem

Coding agents become more useful as they gain repository and tool authority, but that same authority makes silent intent drift more dangerous. A patch can be syntactically correct and fully tested while still touching forbidden surfaces, skipping required architectural work, deleting protected files, or changing more than the operator authorized.

## Working mechanism

**Patch Intent Twin** binds a machine-readable change contract to an observed patch and produces a deterministic allow/refuse receipt.

The contract can require:

- paths that must change;
- paths that are allowed to change;
- surfaces that are forbidden;
- tests that must pass;
- symbols that must appear in specific files;
- dependencies that must never be added;
- maximum changed-file and deletion budgets;
- explicit permission before file deletion.

The observed patch records changed files, file status, additions/deletions, observed symbols, test outcomes, and new dependencies. The engine evaluates those facts against the contract and reports alignment, drift, hard violations, soft evidence gaps, and content-addressed intent/patch digests.

A hard boundary always refuses. Missing expected evidence creates measurable drift and refuses when it exceeds the operator's configured tolerance.

## Example

```bash
python -m pytest -q
python scripts/operate.py

python -m pip install build
python -m build
python -m pip install dist/*.whl

patch-intent-twin examples/compliant_patch.json --budget 0.0 --output receipt.json
```

The installed CLI exits `0` only when the patch satisfies the contract. Refusal exits non-zero, so the same mechanism can gate CI, an agent loop, or a pull-request promotion workflow.

## Why this is technically useful

The mechanism does not ask an LLM to explain whether a patch "looks aligned." It converts intent into deterministic constraints that can be checked continuously as the patch evolves. The resulting receipt is reviewable, reproducible, and digest-bound to the exact intent and observed patch facts.

## Proof surface

| Surface | Path |
|---|---|
| Intent / drift engine | `src/patch_intent_twin.py` |
| Installed CLI | `src/patch_intent_cli.py` |
| Reproducible example | `examples/compliant_patch.json` |
| Behavioral tests | `tests/test_patch_intent_twin.py` |
| Adversarial tests | `tests/test_adversarial.py` |
| Cold-start operation | `scripts/operate.py` |
| Implementation record | `DEV_UP_INSTRUCTIONS.md` |
| Issue contract | `ISSUE_CONTRACT.md` |

## Current boundary

This is an independent reference implementation. It does not integrate proprietary Cursor APIs and does not claim production use or measured developer outcomes. The next meaningful depth gate is an adapter that derives the observed-patch facts from real git diffs and test runs, followed by evaluation on an independently labeled patch-review corpus.
