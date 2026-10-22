"""CLI: python -m routine_heartbeat beat|check ..."""
from __future__ import annotations

import argparse
import shlex
import sys
from datetime import datetime, timezone

from . import beat, check


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="routine_heartbeat")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("beat", help="record one run (always exits 0)")
    b.add_argument("routine")
    b.add_argument("result", help="ok | noop | skipped | error | started")
    b.add_argument("note", nargs="*")
    b.add_argument("--dir", default="heartbeats")
    b.add_argument("--tz", default="UTC")
    c = sub.add_parser("check", help="find stale or failed routines")
    c.add_argument("--registry", default="expected.json")
    c.add_argument("--dir", default="heartbeats")
    c.add_argument("--state", default=None, help="alert de-dupe state file (omit to disable)")
    c.add_argument("--now", default=None, help="ISO-8601 time to evaluate at (testing)")
    c.add_argument("--format", choices=("text", "json"), default="text")
    c.add_argument("--notify-cmd", default=None,
                   help="command that receives new alerts on stdin, e.g. ./examples/notify-stdout.sh")
    a = p.parse_args(argv)

    if a.cmd == "beat":
        beat.record(a.dir, a.routine, a.result, " ".join(a.note), tz=a.tz)
        return 0
    try:
        reg = check.load_registry(a.registry)
        now = datetime.fromisoformat(a.now) if a.now else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        rep = check.evaluate(reg, a.dir, now)
        check.dedupe(rep, a.state, now)
    except Exception as e:
        print(f"check failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(rep.to_json() if a.format == "json" else rep.to_text())
    if rep.new_alerts and a.notify_cmd:
        check.notify(shlex.split(a.notify_cmd), check.alert_text(rep))
    return rep.exit_code


if __name__ == "__main__":
    sys.exit(main())
