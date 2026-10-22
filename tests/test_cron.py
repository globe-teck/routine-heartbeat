from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from routine_heartbeat.cron import Cron, CronError, parse_field, previous_fire

NY = ZoneInfo("America/New_York")


def at(s):
    return datetime.fromisoformat(s)


@pytest.mark.parametrize("text,kind,expected", [
    ("*/15", "minute", {0, 15, 30, 45}),
    ("1-5", "dow", {1, 2, 3, 4, 5}),
    ("mon,wed,fri", "dow", {1, 3, 5}),
    ("7", "dow", {0}),
    ("9-17/4", "hour", {9, 13, 17}),
    ("jan,jul", "month", {1, 7}),
    ("5/20", "minute", {5, 25, 45}),
])
def test_parse_field(text, kind, expected):
    assert parse_field(text, kind) == expected


@pytest.mark.parametrize("bad", ["* * * *", "60 * * * *", "* 24 * * *", "*/0 * * * *",
                                 "5-1 * * * *", "x * * * *", "1,,2 * * * *"])
def test_bad_expressions(bad):
    with pytest.raises(CronError):
        Cron.parse(bad)


def test_weekday_previous_fire_skips_weekend():
    # Monday 2026-03-09 07:00 -> previous weekday 08:30 is Friday 03-06
    f = previous_fire("30 8 * * 1-5", at("2026-03-09T07:00:00-04:00"), NY)
    assert f.isoformat() == "2026-03-06T08:30:00-05:00"


def test_exact_minute_counts_as_fired():
    f = previous_fire("30 8 * * *", at("2026-03-05T08:30:00-05:00"), NY)
    assert f.isoformat() == "2026-03-05T08:30:00-05:00"


def test_union_of_expressions():
    f = previous_fire(["0 10 * * sat", "0 16 * * fri"], at("2026-03-07T09:00:00-05:00"), NY)
    assert f.isoformat() == "2026-03-06T16:00:00-05:00"


def test_dom_or_dow_when_both_restricted():
    c = Cron.parse("0 9 1 * mon")
    # 2026-03-02 is a Monday; 2026-03-01 is the 1st (a Sunday) -> both match
    assert c.day_matches(datetime(2026, 3, 1).date())
    assert c.day_matches(datetime(2026, 3, 2).date())
    assert not c.day_matches(datetime(2026, 3, 3).date())


def test_dst_spring_forward_offsets():
    # US DST starts 2026-03-08; a 09:00 job keeps local wall time
    f = previous_fire("0 9 * * *", at("2026-03-08T12:00:00-04:00"), NY)
    assert f.isoformat() == "2026-03-08T09:00:00-04:00"
    f = previous_fire("0 9 * * *", at("2026-03-08T08:00:00-04:00"), NY)
    assert f.isoformat() == "2026-03-07T09:00:00-05:00"


def test_utc_input_converted():
    f = previous_fire("0 9 * * *", at("2026-03-05T14:30:00+00:00"), NY)
    assert f.isoformat() == "2026-03-05T09:00:00-05:00"


def test_monthly_lookback():
    f = previous_fire("0 0 1 * *", at("2026-03-31T23:00:00-04:00"), NY)
    assert f.isoformat() == "2026-03-01T00:00:00-05:00"


def test_impossible_date_returns_none():
    assert previous_fire("0 0 31 2 *", at("2026-03-05T00:00:00-05:00"), NY) is None
