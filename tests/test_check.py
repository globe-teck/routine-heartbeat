import json
import os
from datetime import datetime

from routine_heartbeat import check
from routine_heartbeat.__main__ import main

REG = {
    "timezone": "America/New_York",
    "monitor_start": "2026-03-01T00:00:00-05:00",
    "routines": {
        "report": {"cron": "30 8 * * 1-5", "grace_min": 20, "owner": "a"},
        "backup": {"cron": "15 2 * * *", "grace_min": 45},
        "off": {"cron": "* * * * *", "active": False},
    },
}
NOW = datetime.fromisoformat("2026-03-05T10:00:00-05:00")


def status(rep, slug):
    return next(s for s in rep.statuses if s.routine == slug)


def test_ok_and_stale(write_hb):
    write_hb("report", ("2026-03-05T08:31:00-05:00", "ok"))
    hb = write_hb("backup", ("2026-03-04T02:16:00-05:00", "ok"))
    rep = check.evaluate(REG, str(hb), NOW)
    assert status(rep, "report").status == "ok"
    assert status(rep, "backup").status == "stale"
    assert rep.exit_code == 2


def test_inactive_routines_skipped(write_hb):
    rep = check.evaluate(REG, str(write_hb.dir), NOW)
    assert "off" not in {s.routine for s in rep.statuses}


def test_grace_window_not_yet_due(write_hb):
    hb = write_hb("report", ("2026-03-04T08:30:30-05:00", "ok"))
    rep = check.evaluate(REG, str(hb), datetime.fromisoformat("2026-03-05T08:45:00-05:00"))
    assert status(rep, "report").status == "ok"  # today's 08:30 is still inside its 20 min grace


def test_slack_allows_slightly_early_heartbeat(write_hb):
    hb = write_hb("report", ("2026-03-05T08:29:00-05:00", "ok"))
    assert status(check.evaluate(REG, str(hb), NOW), "report").status == "ok"


def test_error_result_flags(write_hb):
    hb = write_hb("report", ("2026-03-05T08:31:00-05:00", "error"))
    rep = check.evaluate(REG, str(hb), NOW)
    assert status(rep, "report").status == "error"
    assert rep.exit_code == 2


def test_newest_record_wins_regardless_of_order(write_hb):
    hb = write_hb("report", ("2026-03-05T08:31:00-05:00", "ok"), ("2026-03-04T08:31:00-05:00", "error"))
    assert status(check.evaluate(REG, str(hb), NOW), "report").status == "ok"


def test_bootstrap_unknown_before_monitor_start(write_hb):
    reg = dict(REG, monitor_start="2026-03-05T09:00:00-05:00")
    rep = check.evaluate(reg, str(write_hb.dir), NOW)
    assert status(rep, "report").status == "unknown"
    assert status(rep, "backup").status == "unknown"


def test_missing_file_is_stale_after_start(write_hb):
    rep = check.evaluate(REG, str(write_hb.dir), NOW)
    assert status(rep, "report").status == "stale"
    assert status(rep, "report").last_seen is None


def test_bad_json_lines_ignored(write_hb):
    hb = write_hb.dir
    (hb / "report.jsonl").write_text('not json\n{"ts": "garbage"}\n'
                                     '{"ts": "2026-03-05T08:31:00-05:00", "result": "ok"}\n')
    assert status(check.evaluate(REG, str(hb), NOW), "report").status == "ok"


def test_invalid_cron_reported_not_raised(write_hb):
    reg = {"timezone": "UTC", "routines": {"x": {"cron": "bad"}}}
    rep = check.evaluate(reg, str(write_hb.dir), NOW)
    assert rep.statuses[0].status == "invalid"


def test_dedupe_alerts_once_per_missed_fire(write_hb, tmp_path):
    state = str(tmp_path / "state" / "alerted.json")
    hb = write_hb("report", ("2026-03-05T08:31:00-05:00", "ok"))
    rep = check.evaluate(REG, str(hb), NOW)
    check.dedupe(rep, state, NOW)
    assert [s.routine for s in rep.new_alerts] == ["backup"]
    rep2 = check.evaluate(REG, str(hb), NOW)
    check.dedupe(rep2, state, NOW)
    assert rep2.new_alerts == []
    later = datetime.fromisoformat("2026-03-06T10:00:00-05:00")
    rep3 = check.evaluate(REG, str(hb), later)
    check.dedupe(rep3, state, later)
    assert {s.routine for s in rep3.new_alerts} == {"backup", "report"}


def test_dedupe_prunes_old_keys(write_hb, tmp_path):
    state = tmp_path / "alerted.json"
    state.write_text(json.dumps({"old|stale|x": "2026-01-01T00:00:00-05:00"}))
    rep = check.evaluate(REG, str(write_hb.dir), NOW)
    check.dedupe(rep, str(state), NOW)
    assert "old|stale|x" not in json.loads(state.read_text())


def test_alert_text_mentions_owner(write_hb):
    rep = check.evaluate(REG, str(write_hb.dir), NOW)
    check.dedupe(rep, None, NOW)
    text = check.alert_text(rep)
    assert "report: STALE" in text and "owner=a" in text


def test_cli_json_and_exit_codes(write_hb, tmp_path, capsys):
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps(REG))
    write_hb("report", ("2026-03-05T08:31:00-05:00", "ok"))
    hb = write_hb("backup", ("2026-03-05T02:16:00-05:00", "ok"))
    rc = main(["check", "--registry", str(reg), "--dir", str(hb), "--now", NOW.isoformat(), "--format", "json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["counts"]["ok"] == 2


def test_cli_missing_registry_exit_1(tmp_path):
    assert main(["check", "--registry", str(tmp_path / "nope.json")]) == 1


def test_cli_notify_cmd_receives_alerts(write_hb, tmp_path):
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps(REG))
    out = tmp_path / "alert.txt"
    script = tmp_path / "sink.sh"
    script.write_text(f"#!/bin/sh\ncat > {out}\n")
    os.chmod(script, 0o755)
    rc = main(["check", "--registry", str(reg), "--dir", str(write_hb.dir), "--now", NOW.isoformat(),
               "--notify-cmd", str(script)])
    assert rc == 2
    assert "backup: STALE" in out.read_text()


def test_examples_run(capsys):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rc = main(["check", "--registry", os.path.join(root, "examples", "expected.json"),
               "--dir", os.path.join(root, "examples", "heartbeats"), "--now", "2026-03-05T10:20:00-05:00"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "STALE    nightly-backup" in out and "ERROR    weekly-review" in out
