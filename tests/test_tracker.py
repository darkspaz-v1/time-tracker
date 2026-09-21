import json

import pytest

from tracker import (
    IDLE_BUCKET,
    DayLog,
    bucket_for,
    cap_elapsed,
    format_hms,
    idle_seconds_from_ticks,
    is_idle,
)

CONFIG = {
    "buckets": [
        {"name": "Claude Code", "processes": ["claude.exe"], "title_keywords": ["claude code"]},
        {"name": "MotionVision", "processes": [], "title_keywords": ["gesture-app", "motionvision"]},
        {"name": "Design", "processes": ["figma.exe"], "title_keywords": []},
    ]
}


# --- idle detection -------------------------------------------------------

def test_not_idle_below_threshold():
    assert not is_idle(179.9, 3)


def test_idle_exactly_at_threshold():
    # The boundary counts as idle: the app treats `idle >= threshold` as away.
    assert is_idle(180, 3)


def test_idle_above_threshold():
    assert is_idle(3600, 3)


def test_zero_threshold_is_always_idle():
    assert is_idle(0, 0)


def test_idle_seconds_from_ticks_basic():
    assert idle_seconds_from_ticks(10_000, 7_500) == 2.5


def test_idle_seconds_survives_tick_counter_wraparound():
    # GetTickCount is a 32-bit millisecond counter that wraps every ~49.7 days.
    # Last input just before the wrap, "now" just after: 3 seconds idle, not 0.
    assert idle_seconds_from_ticks(1_000, 0xFFFFFFFF - 1_999) == pytest.approx(3.0)


def test_cap_elapsed_limits_suspend_gaps():
    assert cap_elapsed(5.2, 5) == 5.2
    assert cap_elapsed(4 * 3600, 5) == 15


# --- window title -> project bucket ----------------------------------------

def test_process_match_wins():
    assert bucket_for("claude.exe", "whatever", CONFIG) == "Claude Code"


def test_process_match_is_case_insensitive():
    assert bucket_for("Figma.EXE", "x", CONFIG) == "Design"


def test_title_keyword_match_in_browser():
    assert bucket_for("chrome.exe", "MotionVision - Pull requests - GitHub", CONFIG) == "MotionVision"


def test_first_matching_bucket_wins():
    # Title mentions both "claude code" and "motionvision"; the earlier bucket takes it.
    assert bucket_for("code.exe", "claude code in motionvision", CONFIG) == "Claude Code"


def test_unmatched_falls_back_to_per_process_bucket():
    assert bucket_for("spotify.exe", "Song", CONFIG) == "Other: spotify.exe"


def test_unknown_process_and_title():
    assert bucket_for(None, None, CONFIG) == "Other: unknown"


def test_empty_keyword_does_not_match_everything():
    cfg = {"buckets": [{"name": "Bad", "processes": [], "title_keywords": [""]}]}
    assert bucket_for("a.exe", "some title", cfg) == "Other: a.exe"


def test_no_buckets_configured():
    assert bucket_for("a.exe", "t", {}) == "Other: a.exe"


# --- DayLog persistence -----------------------------------------------------

def test_daylog_accumulates_and_round_trips(tmp_path):
    path = tmp_path / "log.json"
    log = DayLog(path)
    log.add_seconds("2026-01-01", "A", 5)
    log.add_seconds("2026-01-01", "A", 7)
    log.add_seconds("2026-01-01", IDLE_BUCKET, 3)
    log.flush()
    assert json.loads(path.read_text(encoding="utf-8")) == {"2026-01-01": {"A": 12, IDLE_BUCKET: 3}}
    assert DayLog(path).today("2026-01-01") == {"A": 12, IDLE_BUCKET: 3}


def test_daylog_ignores_non_positive_and_skips_clean_flush(tmp_path):
    path = tmp_path / "log.json"
    log = DayLog(path)
    log.add_seconds("d", "A", 0)
    log.add_seconds("d", "A", -4)
    log.flush()
    assert not path.exists()


def test_daylog_flush_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "log.json"
    log = DayLog(path)
    log.add_seconds("d", "A", 10)
    log.flush()
    log.add_seconds("d", "A", 5)

    def boom(*a, **k):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr("tracker.os.replace", boom)
    with pytest.raises(OSError):
        log.flush()
    # Original file still holds the last good state and is still valid JSON.
    assert json.loads(path.read_text(encoding="utf-8")) == {"d": {"A": 10}}


def test_daylog_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "log.json"
    path.write_text("{not json", encoding="utf-8")
    assert DayLog(path).today("d") == {}


def test_format_hms():
    assert format_hms(9) == "9s"
    assert format_hms(65) == "1m 05s"
    assert format_hms(3 * 3600 + 7 * 60 + 59) == "3h 07m"
