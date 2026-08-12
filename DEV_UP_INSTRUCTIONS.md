# DEV_UP_INSTRUCTIONS — implementation record

**Repository:** `GlacierEQ/cursor-patch-intent-twin`  
**Independent company lens:** Cursor / Anysphere  
**Innovation:** Patch Intent Twin

## Mission

Maintain a machine-readable twin of a requested code change and continuously compare the observed patch with that intent so increasing agent authority does not erase reviewability, architectural constraints, or developer trust.

## Implemented mechanism

`src/patch_intent_twin.py` now implements a deterministic intent-to-patch comparison engine.

The intent contract can bind:

- required paths;
- allowed and forbidden repository surfaces;
- required tests;
- required symbols by path;
- forbidden dependencies;
- maximum files changed;
- deletion budget;
- whether file deletion is permitted.

The observed patch carries changed-file status, additions/deletions, observed symbols, test outcomes, and added dependencies.

The engine emits a structured receipt containing:

- `ALLOW` or `REFUSE`;
- exact reasons;
- alignment and drift scores;
- hard/soft violation counts;
- intent digest;
- patch digest;
- total additions/deletions and changed-file count.

Hard boundaries fail closed. Missing required evidence contributes deterministic drift and cannot be hidden by prose.

## Runtime surface

- `patch-intent-twin <input.json>` — installed CLI
- `examples/compliant_patch.json` — reproducible demonstration
- `scripts/operate.py` — existing cold-start mechanism probe

## Verification contract

- behavioral tests cover compliant changes, forbidden surfaces, required symbols, required tests, file deletion, change-budget overruns, forbidden dependencies, malformed duplicate evidence, and digest sensitivity;
- generic adversarial tests remain in place;
- CI runs pytest and cold-start operation;
- CI also builds and installs a wheel, executes the installed CLI, and asserts an `ALLOW` decision on the reproducible example.

## Authority and truth boundaries

- No Cursor / Anysphere affiliation, endorsement, employment, proprietary access, or production deployment is claimed.
- The engine is an independent reference implementation.
- Existing promotion authority and estate control-plane surfaces remain intact.
- Promotion state must still be earned from exact-source verification; this file does not declare promotion by itself.

## Next depth gates

The mechanism is usable now. Further depth should come from real patch adapters rather than another abstraction layer:

1. adapter from `git diff --numstat` / changed symbols into the observed-patch schema;
2. adapter from issue/PR requirements into the intent contract;
3. optional streaming comparison as a coding-agent patch evolves;
4. measured review outcomes on an independently labeled patch corpus.

No implementation work should regress the current fail-closed contract merely to simplify integration.
