"""CLI for one-shot or bounded polling Patch Intent Twin monitoring."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from patch_monitor import PatchIntentMonitor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Continuously compare a real evolving git patch with machine intent")
    parser.add_argument("config", type=Path, help="JSON containing subject_id, intent, tests, and optional budget")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--iterations", type=int, default=1, help="1 = one-shot; >1 polls the evolving head")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.interval <= 0 or args.iterations <= 0:
        parser.error("interval and iterations must be positive")
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("monitor_config_invalid")
        monitor = PatchIntentMonitor(args.state)
        last = None
        for index in range(args.iterations):
            last = monitor.check(
                repo=args.repo,
                base_ref=args.base,
                head_ref=args.head,
                subject_id=config.get("subject_id"),
                intent=config.get("intent") or {},
                tests=config.get("tests") or [],
                budget=float(config.get("budget", 0.0)),
            )
            sys.stdout.write(json.dumps(last.as_dict(), sort_keys=True) + "\n")
            sys.stdout.flush()
            if index + 1 < args.iterations:
                time.sleep(args.interval)
        assert last is not None
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(last.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        sys.stdout.write(json.dumps({"continuation": "enabled", "status": "resolution_required", "resolution_work": [f"resolve_monitor_cli_input:{type(exc).__name__}"]}, sort_keys=True) + "\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
