"""CLI for measuring Patch Intent Twin review decision quality."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from review_benchmark import load_review_cases, run_review_benchmark


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark Patch Intent Twin against labeled aligned/misaligned patch cases")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--max-false-allow-rate", type=float, default=0.0)
    parser.add_argument("--min-accuracy", type=float, default=0.95)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not 0.0 <= args.max_false_allow_rate <= 1.0 or not 0.0 <= args.min_accuracy <= 1.0:
        parser.error("rates must be in [0,1]")
    try:
        result = run_review_benchmark(load_review_cases(args.dataset))
        rendered = json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        sys.stdout.write(rendered)
        return 0 if result.false_allow_rate <= args.max_false_allow_rate and result.accuracy >= args.min_accuracy else 2
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(json.dumps({"status": "ERROR", "reason": str(exc)}, sort_keys=True) + "\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
