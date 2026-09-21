import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

IDLE_BUCKET = "_idle"


def is_idle(idle_seconds, threshold_minutes):
    """True once input has been idle for at least the configured threshold."""
    return idle_seconds >= threshold_minutes * 60


def idle_seconds_from_ticks(now_tick_ms, last_input_tick_ms):
    """Idle seconds from two GetTickCount-style millisecond timestamps."""
    # Mask to 32 bits: GetTickCount wraps every ~49.7 days, and a plain subtraction
    # across the wrap would go negative and read as "no idle time".
    return ((now_tick_ms - last_input_tick_ms) & 0xFFFFFFFF) / 1000.0


def cap_elapsed(elapsed, poll_interval_seconds):
    """Cap the time credited per poll at 3x the interval. Tk's `after` timers don't
    fire while the machine is suspended, so the next poll can see a multi-hour gap
    that must not be attributed to whatever bucket is current on wake."""
    return min(elapsed, poll_interval_seconds * 3)


def format_hms(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def bucket_for(process_name, title, config):
    """First matching configured bucket wins; otherwise falls back to a
    per-process bucket so unconfigured apps still show up individually
    rather than disappearing into one catch-all 'Other'."""
    process_l = (process_name or "").lower()
    title_l = (title or "").lower()

    for bucket in config.get("buckets", []):
        processes = {p.lower() for p in bucket.get("processes", [])}
        if process_l and process_l in processes:
            return bucket["name"]
        for kw in bucket.get("title_keywords", []):
            if kw and kw.lower() in title_l:
                return bucket["name"]

    if process_name:
        return f"Other: {process_name}"
    return "Other: unknown"


class DayLog:
    """Persists {date: {bucket: seconds}} to a JSON file. add_seconds() only
    updates in memory; call flush() periodically (and on quit) to persist -
    avoids writing to disk on every single poll."""

    def __init__(self, path):
        self.path = Path(path)
        self._data = self._load()
        self._dirty = False

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}  # first run: nothing logged yet
        except json.JSONDecodeError:
            log.warning("%s is not valid JSON; starting an empty log", self.path, exc_info=True)
            return {}

    def flush(self):
        if not self._dirty:
            return
        # Write to a temp file then os.replace: a crash mid-write used to leave a
        # truncated time_log.json, which _load reads as {} and the next flush then
        # overwrote - losing every previous day.
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = False

    def add_seconds(self, date_str, bucket, seconds):
        if seconds <= 0:
            return
        day = self._data.setdefault(date_str, {})
        day[bucket] = day.get(bucket, 0) + seconds
        self._dirty = True

    def today(self, date_str):
        return dict(self._data.get(date_str, {}))
