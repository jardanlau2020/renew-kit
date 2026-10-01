import time

from renewkit.timeutil import days_left, format_expiry

NOW = 1_800_000_000.0  # 固定基准时间，避免测试随真实时间漂移


def test_days_left_number_is_days():
    assert days_left(30) == 30
    assert days_left("7") == 7


def test_days_left_seconds_timestamp():
    ts = NOW + 5 * 86400
    assert days_left(ts, now=NOW) == 5


def test_days_left_milliseconds_timestamp():
    ts = int((NOW + 3 * 86400) * 1000)
    assert days_left(ts, now=NOW) == 3


def test_days_left_iso_string():
    target = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(NOW + 2 * 86400))
    assert days_left(target, now=NOW) == 2


def test_days_left_unknown_returns_none():
    assert days_left(None) is None
    assert days_left("") is None
    assert days_left("not-a-date") is None


def test_format_expiry_number_means_days_not_date():
    assert format_expiry(30) == "30 天后"


def test_format_expiry_iso():
    assert format_expiry("2026-10-31T12:00:00") == "10-31 12:00"
    assert format_expiry("2026-10-31") == "10-31"
