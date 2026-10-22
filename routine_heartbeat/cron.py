"""A small, dependency-free 5-field cron matcher.

Supports ``*``, numbers, ranges (``1-5``), lists (``1,3,5``), steps (``*/15``,
``9-17/2``) and month/day names (``jan``, ``mon``). Day 7 is Sunday, like 0.
When both day-of-month and day-of-week are restricted, a day matches if
either one matches (classic cron behaviour).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

_RANGES = {"minute": (0, 59), "hour": (0, 23), "dom": (1, 31), "month": (1, 12), "dow": (0, 7)}
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DAYS = "sun mon tue wed thu fri sat".split()
_NAMES = {
    "month": {m: i + 1 for i, m in enumerate(_MONTHS)},
    "dow": {d: i for i, d in enumerate(_DAYS)},
}
_FIELDS = ("minute", "hour", "dom", "month", "dow")


class CronError(ValueError):
    """Raised for an invalid cron expression."""


def _value(token: str, kind: str) -> int:
    token = token.strip().lower()
    names = _NAMES.get(kind, {})
    if token in names:
        return names[token]
    if not token.isdigit():
        raise CronError(f"bad {kind} value {token!r}")
    v = int(token)
    lo, hi = _RANGES[kind]
    if not lo <= v <= hi:
        raise CronError(f"{kind} value {v} out of range {lo}-{hi}")
    return v


def parse_field(text: str, kind: str) -> frozenset[int]:
    lo, hi = _RANGES[kind]
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"empty item in {kind} field {text!r}")
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronError(f"bad step {step_s!r} in {kind}")
            step = int(step_s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _value(a, kind), _value(b, kind)
            if start > end:
                raise CronError(f"reversed range {part!r} in {kind}")
        else:
            start = _value(part, kind)
            end = hi if step > 1 else start
        out.update(range(start, end + 1, step))
    if kind == "dow" and 7 in out:
        out.discard(7)
        out.add(0)
    return frozenset(out)


@dataclass(frozen=True)
class Cron:
    expr: str
    minute: frozenset[int]
    hour: frozenset[int]
    dom: frozenset[int]
    month: frozenset[int]
    dow: frozenset[int]
    dom_star: bool
    dow_star: bool

    @classmethod
    def parse(cls, expr: str) -> "Cron":
        fields = expr.split()
        if len(fields) != 5:
            raise CronError(f"expected 5 fields, got {len(fields)}: {expr!r}")
        parsed = {k: parse_field(f, k) for k, f in zip(_FIELDS, fields)}
        return cls(expr=expr, dom_star=fields[2] == "*", dow_star=fields[4] == "*", **parsed)

    def day_matches(self, d: date) -> bool:
        if d.month not in self.month:
            return False
        dom_ok = d.day in self.dom
        dow_ok = (d.isoweekday() % 7) in self.dow
        if self.dom_star and self.dow_star:
            return True
        if self.dom_star:
            return dow_ok
        if self.dow_star:
            return dom_ok
        return dom_ok or dow_ok

    def previous_fire(self, at: datetime, tz: ZoneInfo, lookback_days: int = 400) -> datetime | None:
        """Latest fire time F with F <= ``at`` (aware datetime), in zone ``tz``."""
        local = at.astimezone(tz).replace(second=0, microsecond=0, tzinfo=None)
        day = local.date()
        hours = sorted(self.hour, reverse=True)
        minutes = sorted(self.minute, reverse=True)
        for _ in range(lookback_days + 1):
            if self.day_matches(day):
                for h in hours:
                    for m in minutes:
                        wall = datetime(day.year, day.month, day.day, h, m)
                        if wall > local:
                            continue
                        aware = wall.replace(tzinfo=tz)
                        if aware <= at:
                            return aware
            day -= timedelta(days=1)
        return None


def previous_fire(exprs: str | list[str], at: datetime, tz: ZoneInfo) -> datetime | None:
    """Latest fire across one or more cron expressions (union)."""
    if isinstance(exprs, str):
        exprs = [exprs]
    fires = [f for f in (Cron.parse(e).previous_fire(at, tz) for e in exprs) if f]
    return max(fires) if fires else None
