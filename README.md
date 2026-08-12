# Patch Intent Twin

Independent GlacierEQ portfolio exhibit aligned to **Cursor / Anysphere** operating themes.

> **Not affiliated.** This repository is not affiliated with, endorsed by, employed by, or deployed at Cursor / Anysphere. No proprietary access, production deployment, customer impact, or company partnership is claimed.

## Problem

Coding agents become more useful as they gain repository and tool authority, but that same authority makes silent intent drift more dangerous. A patch can compile and pass tests while still violating requested scope, touching forbidden surfaces, skipping required architectural work, deleting protected files, or exceeding the developer's authorized change budget.

## System

**Patch Intent Twin** turns change intent into deterministic constraints and continuously compares those constraints with the real evolving repository patch.

The implemented system now includes five connected execution surfaces:

1. **Intent recovery** parses explicit issue/PR constraints into a versioned contract while retaining source URI, line number, line digest, source digest, and contract digest. Contradictory material instructions fail closed.
2. **Real git observation** resolves exact base/head commits, reads rename-aware name-status and numstat data, extracts changed symbols from exact head blobs, observes dependency-manifest additions, and emits a content-addressed patch observation.
3. **Real test receipts** execute named test commands without a shell and bind argv, exit status, stdout/stderr digests, and evidence tails into receipts. Caller-supplied test status is not trusted on the real-git path.
4. **Intent drift evaluation** checks required/allowed/forbidden surfaces, required tests and symbols, forbidden dependencies, deletion authority, file-count/deletion budgets, and drift tolerance. Hard violations always refuse.
5. **Incremental monitoring + benchmark** persists evaluation state, re-evaluates whenever patch/intent/test-plan/budget changes, reports introduced/cleared violations, and measures false-allow/false-refuse behavior on a labeled review corpus.

## Install and run

```bash
python -m pytest -q
python -m pip install build
python -m build
python -m pip install dist/*.whl
```

Direct machine-envelope evaluation:

```bash
patch-intent-twin examples/compliant_patch.json --budget 0.0 --output receipt.json
```

Recover intent from provenance-bound evidence:

```bash
patch-intent-recover examples/intent_evidence.json --output recovered-intent.json
```

Evaluate a real git commit range and execute the required tests:

```bash
patch-intent-git config.json --repo . --base <BASE_SHA> --head HEAD --output real-patch-receipt.json
```

Monitor an evolving patch:

```bash
patch-intent-monitor config.json --repo . --base <BASE_SHA> --head HEAD \
  --state .patch-intent-state.json --iterations 1 --output transition.json
```

Benchmark decision behavior:

```bash
patch-intent-benchmark examples/review_benchmark.json \
  --max-false-allow-rate 0 --min-accuracy 1.0 --output benchmark.json
```

All execution commands use non-zero refusal/error exits where appropriate, so they can participate directly in agent loops and CI workflows.

## Proof surface

| Capability | Implementation | Behavioral proof |
|---|---|---|
| Intent contract + drift engine | `src/patch_intent_twin.py` | `tests/test_patch_intent_twin.py` |
| Real git patch observation | `src/git_patch_adapter.py` | `tests/test_real_patch_adapters.py` |
| Real test execution receipts | `src/test_run_adapter.py` | `tests/test_real_patch_adapters.py` |
| Provenance-bound intent recovery | `src/intent_recovery.py` | `tests/test_intent_monitor_benchmark.py` |
| Incremental monitor | `src/patch_monitor.py` | `tests/test_intent_monitor_benchmark.py` |
| Labeled review benchmark | `src/review_benchmark.py` | `tests/test_intent_monitor_benchmark.py` |
| Installed command surfaces | `src/*_cli.py`, `pyproject.toml` | `.github/workflows/tests.yml` |

## Current boundary

This is an independent, local-first developer tool. It does not integrate proprietary Cursor APIs and makes no claim of production deployment or Cursor-measured developer outcomes. Its labeled benchmark is repository-owned reference evidence, not an external Cursor dataset. The crystallization manifests define the complete material capability model; terminal `CRYSTALLIZED` status is earned only when exact-head build, behavior, runtime, and documentation proof are green.
