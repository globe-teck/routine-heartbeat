"""Append one heartbeat record for a routine run.

A heartbeat writer must never break the routine that calls it, so ``record``
reports problems on stderr and returns ``False`` instead of raising.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

RESULTS = ("ok", "noop", "skipped", "error", "started")
_SLUG = re.compile(r"[^a-z0-9._-]+")


def normalize_slug(raw: str) -> str:
    return _SLUG.sub("-", raw.strip().lower()).strip("-.") or "unknown"


def record(hb_dir: str, slug: str, result: str, note: str = "", tz: str = "UTC",
           now: datetime | None = None) -> bool:
    try:
        clean = normalize_slug(slug)
        if clean != slug:
            print(f"warning: slug {slug!r} normalized to {clean!r}", file=sys.stderr)
        result = (result or "").strip().lower() or "unknown"
        if result not in RESULTS:
            print(f"warning: non-standard result {result!r} recorded", file=sys.stderr)
        ts = (now or datetime.now(ZoneInfo(tz))).isoformat(timespec="seconds")
        rec = {"ts": ts, "routine": clean, "result": result, "note": note.strip()}
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        os.makedirs(hb_dir, exist_ok=True)
        fd = os.open(os.path.join(hb_dir, clean + ".jsonl"),
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line)
        finally:
            os.close(fd)  # closing releases the lock
        return True
    except Exception as e:  # never propagate into the caller's routine
        print(f"warning: heartbeat not recorded ({type(e).__name__}: {e})", file=sys.stderr)
        return False
