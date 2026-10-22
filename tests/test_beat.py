import json
import os
import stat
import threading
from datetime import datetime

from routine_heartbeat import beat
from routine_heartbeat.__main__ import main


def test_record_appends_json(tmp_path):
    now = datetime.fromisoformat("2026-03-05T08:30:00-05:00")
    assert beat.record(str(tmp_path), "daily-report", "ok", "5 items", now=now)
    rec = json.loads((tmp_path / "daily-report.jsonl").read_text())
    assert rec == {"ts": "2026-03-05T08:30:00-05:00", "routine": "daily-report", "result": "ok", "note": "5 items"}


def test_file_is_owner_only(tmp_path):
    beat.record(str(tmp_path), "x", "ok")
    mode = stat.S_IMODE(os.stat(tmp_path / "x.jsonl").st_mode)
    assert mode & 0o077 == 0


def test_slug_normalized(tmp_path, capsys):
    beat.record(str(tmp_path), "Daily Report!", "ok")
    assert (tmp_path / "daily-report.jsonl").exists()
    assert "normalized" in capsys.readouterr().err


def test_path_traversal_blocked(tmp_path):
    beat.record(str(tmp_path / "hb"), "../../etc/evil", "ok")
    assert not (tmp_path / "etc").exists()
    assert len(os.listdir(tmp_path / "hb")) == 1


def test_never_raises(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert beat.record(str(blocker / "sub"), "x", "ok") is False
    assert "not recorded" in capsys.readouterr().err


def test_nonstandard_result_warns(tmp_path, capsys):
    beat.record(str(tmp_path), "x", "WEIRD")
    assert "non-standard" in capsys.readouterr().err
    assert json.loads((tmp_path / "x.jsonl").read_text())["result"] == "weird"


def test_concurrent_appends_do_not_interleave(tmp_path):
    def worker(i):
        for j in range(50):
            beat.record(str(tmp_path), "busy", "ok", f"w{i}-{j}" + "x" * 200)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = (tmp_path / "busy.jsonl").read_text().splitlines()
    assert len(lines) == 400
    assert all(json.loads(line)["routine"] == "busy" for line in lines)


def test_cli_beat_always_exit_zero(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("")
    assert main(["beat", "x", "ok", "--dir", str(blocker / "d")]) == 0
