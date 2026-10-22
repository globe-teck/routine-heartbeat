"""Compare the expected-fires registry with recorded heartbeats.

For each active routine:
  * F = the latest scheduled fire with F <= now - grace_min
  * last = the newest heartbeat timestamp
  * ok      : last >= F - slack (and the last result was not "error")
  * error   : on time, but the newest record says result=error
  * unknown : no heartbeat yet and the registry's monitor_start is after F
  * stale   : otherwise
Alerts are de-duplicated by (routine, expected fire) in a small state file, so
one missed run alerts once, and the next missed run alerts again.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .cron import previous_fire, CronError

SLACK = timedelta(minutes=2)
STATE_KEEP = timedelta(days=14)


@dataclass
class Status:
    routine: str
    status: str
    expected: str | None
    last_seen: str | None
    last_result: str | None
    owner: str | None = None
    detail: str = ""


@dataclass
class Report:
    checked_at: str
    statuses: list[Status] = field(default_factory=list)
    new_alerts: list[Status] = field(default_factory=list)

    def by(self, status: str) -> list[Status]:
        return [s for s in self.statuses if s.status == status]

    @property
    def exit_code(self) -> int:
        return 2 if self.by("stale") or self.by("error") else 0

    def to_json(self) -> str:
        d = {"checked_at": self.checked_at,
             "counts": {k: len(self.by(k)) for k in ("ok", "stale", "error", "unknown", "invalid")},
             "statuses": [asdict(s) for s in self.statuses],
             "new_alerts": [asdict(s) for s in self.new_alerts]}
        return json.dumps(d, indent=2)

    def to_text(self) -> str:
        lines = [f"checked {self.checked_at}"]
        for s in self.statuses:
            lines.append(f"{s.status.upper():8} {s.routine:28} expected={s.expected or '-'} "
                         f"last={s.last_seen or '-'} {s.detail}".rstrip())
        return "\n".join(lines)


def _parse_ts(s: str | None, tz: ZoneInfo) -> datetime | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=tz)


def newest(path: str, tz: ZoneInfo) -> tuple[datetime | None, str | None]:
    best, result = None, None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                t = _parse_ts(rec.get("ts"), tz)
                if t and (best is None or t >= best):
                    best, result = t, rec.get("result")
    except FileNotFoundError:
        pass
    return best, result


def load_registry(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        reg = json.load(f)
    if not isinstance(reg.get("routines"), dict):
        raise ValueError("registry needs a 'routines' object")
    return reg


def evaluate(reg: dict, hb_dir: str, now: datetime) -> Report:
    tz = ZoneInfo(reg.get("timezone", "UTC"))
    now = now.astimezone(tz)
    monitor_start = _parse_ts(reg.get("monitor_start"), tz)
    rep = Report(checked_at=now.isoformat(timespec="seconds"))
    for slug, spec in sorted(reg["routines"].items()):
        if not spec.get("active", True):
            continue
        grace = timedelta(minutes=int(spec.get("grace_min", 15)))
        owner = spec.get("owner")
        try:
            fire = previous_fire(spec["cron"], now - grace, tz)
        except (CronError, KeyError) as e:
            rep.statuses.append(Status(slug, "invalid", None, None, None, owner, f"bad cron: {e}"))
            continue
        last, result = newest(os.path.join(hb_dir, slug + ".jsonl"), tz)
        since = _parse_ts(spec.get("since"), tz) or monitor_start
        exp = fire.isoformat(timespec="minutes") if fire else None
        seen = last.isoformat(timespec="seconds") if last else None
        if fire is None or (since and fire < since):
            st, detail = ("unknown" if last is None else "ok"), "no expected fire since monitoring began"
        elif last is not None and last >= fire - SLACK:
            st, detail = ("error", "last run reported error") if result == "error" else ("ok", "")
        else:
            late = now - fire
            st, detail = "stale", f"missed, {int(late.total_seconds() // 60)} min past expected fire"
        rep.statuses.append(Status(slug, st, exp, seen, result, owner, detail))
    return rep


def dedupe(rep: Report, state_path: str | None, now: datetime) -> None:
    alerts = rep.by("stale") + rep.by("error")
    if not state_path:
        rep.new_alerts = alerts
        return
    try:
        with open(state_path, encoding="utf-8") as f:
            seen = json.load(f)
    except (FileNotFoundError, ValueError):
        seen = {}
    cutoff = now - STATE_KEEP
    seen = {k: v for k, v in seen.items() if datetime.fromisoformat(v) >= cutoff}
    for s in alerts:
        key = f"{s.routine}|{s.status}|{s.expected}"
        if key not in seen:
            rep.new_alerts.append(s)
            seen[key] = now.isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=1, sort_keys=True)
    os.replace(tmp, state_path)


def alert_text(rep: Report) -> str:
    lines = [f"{len(rep.new_alerts)} routine(s) need attention ({rep.checked_at}):"]
    for s in rep.new_alerts:
        who = f" owner={s.owner}" if s.owner else ""
        lines.append(f"- {s.routine}: {s.status.upper()} expected {s.expected}, "
                     f"last seen {s.last_seen or 'never'}{who}")
    return "\n".join(lines) + "\n"


def notify(cmd: list[str], text: str) -> int:
    """Pipe the alert text to a user-supplied command (no shell)."""
    return subprocess.run(cmd, input=text, text=True, timeout=60).returncode
