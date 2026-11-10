"""Turn a check Report into metrics a dashboard can graph.

Two text formats, both stdlib only:
  * prometheus : exposition format, for node_exporter's textfile collector or any scraper
  * influx     : line protocol, for push endpoints that accept it (InfluxDB, Telegraf)

Three series per active routine:
  routine_heartbeat_up{routine,owner}                1 ok, 0 stale/error, -1 unknown/invalid
  routine_heartbeat_overdue_minutes{routine,owner}   minutes past the expected fire (0 unless stale or error)
  routine_heartbeat_last_seen_seconds{routine,owner} Unix time of the newest heartbeat (-1 if none)

Plus a count by status:
  routine_heartbeat_status_count{status}             how many routines are in each status

Nothing here sends anything. Pipe the output to your own push command or write it to a file.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from .check import Report, Status

UP = {"ok": 1, "stale": 0, "error": 0, "unknown": -1, "invalid": -1}
STATUSES = ("ok", "stale", "error", "unknown", "invalid")
_PROM_ESC = str.maketrans({"\\": r"\\", '"': r'\"', "\n": r"\n"})
_INFLUX_TAG = re.compile(r"([,= \\])")


def overdue_minutes(s: Status, checked_at: datetime) -> float:
    if s.status not in ("stale", "error") or not s.expected:
        return 0.0
    exp = datetime.fromisoformat(s.expected)
    return max(0.0, round((checked_at - exp).total_seconds() / 60, 1))


def _last_seen(s: Status) -> float:
    return datetime.fromisoformat(s.last_seen).timestamp() if s.last_seen else -1.0


def samples(rep: Report) -> list[tuple[str, dict, float]]:
    now = datetime.fromisoformat(rep.checked_at)
    out: list[tuple[str, dict, float]] = []
    for s in rep.statuses:
        labels = {"routine": s.routine, "owner": s.owner or ""}
        out.append(("routine_heartbeat_up", labels, float(UP.get(s.status, -1))))
        out.append(("routine_heartbeat_overdue_minutes", labels, overdue_minutes(s, now)))
        out.append(("routine_heartbeat_last_seen_seconds", labels, _last_seen(s)))
    for st in STATUSES:
        out.append(("routine_heartbeat_status_count", {"status": st}, float(len(rep.by(st)))))
    return out


def to_prometheus(rep: Report) -> str:
    helps = {
        "routine_heartbeat_up": "1 ok, 0 stale or error, -1 unknown or invalid",
        "routine_heartbeat_overdue_minutes": "Minutes past the expected fire (0 unless stale or error)",
        "routine_heartbeat_last_seen_seconds": "Unix time of the newest heartbeat (-1 if none)",
        "routine_heartbeat_status_count": "Routines in each status",
    }
    lines: list[str] = []
    seen: set[str] = set()
    for name, labels, value in samples(rep):
        if name not in seen:
            seen.add(name)
            lines += [f"# HELP {name} {helps[name]}", f"# TYPE {name} gauge"]
        lab = ",".join(f'{k}="{str(v).translate(_PROM_ESC)}"' for k, v in labels.items())
        lines.append(f"{name}{{{lab}}} {value:g}")
    return "\n".join(lines) + "\n"


def _influx_escape(v: str) -> str:
    return _INFLUX_TAG.sub(r"\\\1", v)


def to_influx(rep: Report) -> str:
    """One line per sample: <name>,<tag>=<v>,... value=<n>. Empty tags are dropped."""
    lines = []
    for name, labels, value in samples(rep):
        tags = "".join("," + k + "=" + _influx_escape(str(v)) for k, v in labels.items() if v != "")
        lines.append(f"{name}{tags} value={value:g}")
    return "\n".join(lines) + "\n"


def dashboard(title: str = "Routine heartbeats", stale_after_min: int = 360) -> str:
    """A small Grafana dashboard (JSON model) for the series above. Import it in Grafana;
    pick your Prometheus data source when asked. The red threshold marks routines that are
    more than `stale_after_min` minutes overdue."""
    ds = {"type": "prometheus", "uid": "${datasource}"}

    def panel(pid, kind, ptitle, expr, legend, x, y, w, h, extra=None):
        p = {"id": pid, "type": kind, "title": ptitle, "datasource": ds,
             "gridPos": {"x": x, "y": y, "w": w, "h": h},
             "targets": [{"refId": "A", "datasource": ds, "expr": expr, "legendFormat": legend}]}
        p.update(extra or {})
        return p

    red = {"fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [
        {"color": "green", "value": None}, {"color": "red", "value": stale_after_min}]}}}}
    model = {
        "title": title, "uid": "routine-heartbeat", "schemaVersion": 39, "refresh": "5m",
        "time": {"from": "now-24h", "to": "now"},
        "templating": {"list": [{"name": "datasource", "type": "datasource", "query": "prometheus"}]},
        "panels": [
            panel(1, "stat", "Routines by status", "routine_heartbeat_status_count", "{{status}}", 0, 0, 24, 4),
            panel(2, "bargauge", "Minutes overdue", "routine_heartbeat_overdue_minutes",
                  "{{owner}} / {{routine}}", 0, 4, 12, 10, red),
            panel(3, "timeseries", "Up (1 ok, 0 missed, -1 unknown)", "routine_heartbeat_up",
                  "{{routine}}", 12, 4, 12, 10),
        ],
    }
    return json.dumps(model, indent=2) + "\n"
