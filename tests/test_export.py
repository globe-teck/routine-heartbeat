import json
from datetime import datetime

from routine_heartbeat import export
from routine_heartbeat.__main__ import main
from routine_heartbeat.check import Report, Status

REP = Report(checked_at="2026-03-05T10:20:00-05:00", statuses=[
    Status("morning-report", "ok", "2026-03-05T08:30-05:00", "2026-03-05T08:31:02-05:00", "ok", "reports-agent"),
    Status("nightly-backup", "stale", "2026-03-05T02:15-05:00", "2026-03-04T02:16:00-05:00", "ok", "ops-agent",
           "missed"),
    Status("inbox-watch", "error", "2026-03-05T10:00-05:00", "2026-03-05T10:01:00-05:00", "error", None),
    Status("new-job", "unknown", None, None, None, 'team "a"'),
])


def test_overdue_minutes():
    now = datetime.fromisoformat(REP.checked_at)
    assert export.overdue_minutes(REP.statuses[0], now) == 0
    assert export.overdue_minutes(REP.statuses[1], now) == 485
    assert export.overdue_minutes(REP.statuses[2], now) == 20
    assert export.overdue_minutes(REP.statuses[3], now) == 0


def test_prometheus_format():
    out = export.to_prometheus(REP)
    assert out.count("# TYPE routine_heartbeat_up gauge") == 1
    assert 'routine_heartbeat_up{routine="morning-report",owner="reports-agent"} 1' in out
    assert 'routine_heartbeat_up{routine="nightly-backup",owner="ops-agent"} 0' in out
    assert 'routine_heartbeat_up{routine="new-job",owner="team \\"a\\""} -1' in out
    assert 'routine_heartbeat_overdue_minutes{routine="nightly-backup",owner="ops-agent"} 485' in out
    assert 'routine_heartbeat_last_seen_seconds{routine="new-job",owner="team \\"a\\""} -1' in out
    assert 'routine_heartbeat_status_count{status="stale"} 1' in out
    assert out.endswith("\n")


def test_influx_format_escapes_and_drops_empty_tags():
    lines = export.to_influx(REP).splitlines()
    assert "routine_heartbeat_up,routine=inbox-watch value=0" in lines
    assert 'routine_heartbeat_up,routine=new-job,owner=team\\ "a" value=-1' in lines
    assert "routine_heartbeat_status_count,status=ok value=1" in lines
    assert all(" value=" in ln for ln in lines)


def test_dashboard_is_valid_json_with_threshold():
    model = json.loads(export.dashboard(stale_after_min=90))
    assert model["uid"] == "routine-heartbeat"
    exprs = [p["targets"][0]["expr"] for p in model["panels"]]
    assert "routine_heartbeat_overdue_minutes" in exprs
    steps = model["panels"][1]["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert steps[-1]["value"] == 90


def test_cli_prometheus_on_example(capsys):
    rc = main(["check", "--registry", "examples/expected.json", "--dir", "examples/heartbeats",
               "--now", "2026-03-05T10:20:00-05:00", "--format", "prometheus"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "routine_heartbeat_status_count" in out
    assert "retired-sync" not in out


def test_cli_dashboard(capsys):
    assert main(["dashboard", "--title", "My jobs"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "My jobs"
