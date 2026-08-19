"""CLI for deterministic intent recovery from explicit issue/PR evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from intent_recovery import IntentEvidence, recover_intent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover a machine-readable patch intent contract from provenance-bound evidence")
    parser.add_argument("input", type=Path, help="JSON containing evidence[] with source_id, source_uri, and text")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("evidence"), list):
            raise ValueError("intent_input_invalid")
        recovered = recover_intent(IntentEvidence(**row) for row in payload["evidence"])
        rendered = json.dumps(recovered.as_dict(), indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        sys.stdout.write(rendered)
        return 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        sys.stdout.write(json.dumps({"continuation": "enabled", "status": "resolution_required", "resolution_work": [f"resolve_cli_input:{type(exc).__name__}"]}, sort_keys=True) + "\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
