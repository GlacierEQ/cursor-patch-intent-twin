# Issue contract — Patch Intent Twin

## Problem
giving coding agents increasing repository/tool authority without sacrificing reviewability, intent alignment, or developer trust

## Desired outcome
A bounded, open, testable implementation of **Patch Intent Twin** that demonstrates Maintain a machine-readable twin of the requested change—requirements, forbidden surfaces, architectural constraints, expected tests—and continuously compare the evolving patch against that intent.

## Non-goals
- Cursor / Anysphere affiliation or proprietary integration
- Portfolio-wide scale/performance claims
- UI marketing site

## Acceptance
1. Mechanism module implements allow + refuse with structured receipts
2. pytest behavioral suite green
3. operate.py cold-start produces JSON receipt
4. Non-affiliation disclaimer preserved
