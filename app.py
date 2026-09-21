import json
import logging
import msvcrt
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path

import pystray
from PIL import ImageTk

from applog import setup_logging
from foreground import get_foreground_info, get_idle_seconds
from icon import app_icon
from tracker import IDLE_BUCKET, DayLog, bucket_for, cap_elapsed, format_hms, is_idle

APP_DIR = Path(__file__).parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "time_log.json"
LOCK_PATH = APP_DIR / ".singleton.lock"
SHOW_SIGNAL_PATH = APP_DIR / ".show_signal"
_lock_file = None
log = logging.getLogger("time-tracker")

INK = "#12131C"
PANEL = "#1B1D2B"
HAIRLINE = "#2E3044"
TEXT = "#EDEEF7"
MUTED = "#8688A6"
ACCENT = "#E85D8A"

DEFAULT_CONFIG = {
    "idle_threshold_minutes": 3,
    "poll_interval_seconds": 5,
    "flush_interval_seconds": 60,
    "buckets": [],
}


def _acquire_single_instance_lock():
    """Before exiting on a blocked second launch, drops a signal file so the
    already-running instance shows itself instead of silently doing nothing."""
    global _lock_file
    f = open(LOCK_PATH, "a+b")
    if f.tell() == 0:
        f.write(b"0")
        f.flush()
    f.seek(0)
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        try:
            SHOW_SIGNAL_PATH.touch()
        except OSError:
            # Best effort: the second launch is exiting anyway; the only loss is
            # that the running instance doesn't raise its window.
            log.debug("could not write show-signal file", exc_info=True)
        return False
    _lock_file = f
    return True


def load_config():
    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config.update(json.load(f))
    except FileNotFoundError:
        pass
    return config


def _pick_mono_font():
    families = set(tkfont.families())
    for name in ("Cascadia Mono", "Cascadia Code", "Consolas"):
        if name in families:
            return name
    return "Consolas"


class TimeTrackerApp:
    def __init__(self):
        self.config = load_config()
        self.log = DayLog(LOG_PATH)
        self._paused = False
        self._last_poll_time = time.time()
        self._polls_since_flush = 0

        self.root = tk.Tk()
        self.root.title("Time Tracker")
        self.root.configure(bg=INK)
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self._mono = _pick_mono_font()
        self._icon_photo = ImageTk.PhotoImage(app_icon())
        self.root.iconphoto(True, self._icon_photo)

        w, h = 300, 380
        x = self.root.winfo_screenwidth() - w - 20
        self.root.geometry(f"{w}x{h}+{x}+40")
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        self._stop = threading.Event()
        self.icon = None
        self._ui_queue = queue.Queue()
        self.root.after(50, self._drain_ui_queue)

        self._build_ui()
        self._refresh_ui()
        self.root.after(self.config["poll_interval_seconds"] * 1000, self._poll_loop)

    def _drain_ui_queue(self):
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                fn()
        except queue.Empty:
            pass
        if SHOW_SIGNAL_PATH.exists():
            try:
                SHOW_SIGNAL_PATH.unlink()
            except OSError:
                # Best effort: worst case the window is raised again on the next tick.
                log.debug("could not remove show-signal file", exc_info=True)
            self.root.deiconify()
            self.root.lift()
        self.root.after(50, self._drain_ui_queue)

    def _post(self, fn):
        self._ui_queue.put(fn)

    def _build_ui(self):
        tk.Frame(self.root, bg=ACCENT, height=2).pack(fill="x")

        header = tk.Frame(self.root, bg=INK)
        header.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(header, text="TIME TRACKER", bg=INK, fg=ACCENT, font=(self._mono, 10, "bold")).pack(side="left")

        self.summary_label = tk.Label(self.root, text="", bg=INK, fg=MUTED, font=(self._mono, 9), justify="left")
        self.summary_label.pack(fill="x", padx=14, pady=(2, 8), anchor="w")

        canvas_frame = tk.Frame(self.root, bg=INK)
        canvas_frame.pack(fill="both", expand=True, padx=10)
        self.canvas = tk.Canvas(canvas_frame, bg=INK, highlightthickness=0)
        scrollbar = tk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self.rows_frame = tk.Frame(self.canvas, bg=INK)
        self.rows_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.root.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

    def _today_key(self):
        return datetime.now().strftime("%Y-%m-%d")

    def _poll_loop(self):
        if self._stop.is_set():
            return
        now = time.time()
        elapsed = now - self._last_poll_time
        self._last_poll_time = now
        # Cap so a suspend/resume gap isn't credited to whatever bucket is current on wake.
        elapsed = cap_elapsed(elapsed, self.config["poll_interval_seconds"])

        if not self._paused:
            idle_seconds = get_idle_seconds()
            if not is_idle(idle_seconds, self.config.get("idle_threshold_minutes", 3)):
                process_name, title = get_foreground_info()
                bucket = bucket_for(process_name, title, self.config)
                self.log.add_seconds(self._today_key(), bucket, elapsed)
            else:
                self.log.add_seconds(self._today_key(), IDLE_BUCKET, elapsed)

        self._polls_since_flush += 1
        flush_every = max(1, self.config.get("flush_interval_seconds", 60) // self.config["poll_interval_seconds"])
        if self._polls_since_flush >= flush_every:
            self.log.flush()
            self._polls_since_flush = 0

        self._refresh_ui()
        self.root.after(self.config["poll_interval_seconds"] * 1000, self._poll_loop)

    def _refresh_ui(self):
        today = self.log.today(self._today_key())
        idle_seconds = today.pop(IDLE_BUCKET, 0)
        active_seconds = sum(today.values())

        status = "paused" if self._paused else "tracking"
        self.summary_label.config(
            text=f"active {format_hms(active_seconds)}  ·  idle {format_hms(idle_seconds)}  ·  {status}"
        )

        for w in self.rows_frame.winfo_children():
            w.destroy()

        if not today:
            tk.Label(self.rows_frame, text="No activity logged yet today.", bg=INK, fg=MUTED, font=("Segoe UI", 9), pady=20).pack(
                fill="x"
            )
            return

        max_seconds = max(today.values()) if today else 1
        for bucket, seconds in sorted(today.items(), key=lambda kv: kv[1], reverse=True):
            row = tk.Frame(self.rows_frame, bg=INK)
            row.pack(fill="x", pady=4)

            top = tk.Frame(row, bg=INK)
            top.pack(fill="x")
            tk.Label(top, text=bucket, bg=INK, fg=TEXT, font=("Segoe UI", 9), anchor="w").pack(side="left")
            tk.Label(top, text=format_hms(seconds), bg=INK, fg=MUTED, font=(self._mono, 8)).pack(side="right")

            bar_bg = tk.Frame(row, bg=PANEL, height=6, width=264)
            bar_bg.pack(fill="x", pady=(3, 0))
            bar_bg.pack_propagate(False)
            fraction = seconds / max_seconds if max_seconds else 0
            fill_width = max(2, int(264 * fraction))
            bar_fill = tk.Frame(bar_bg, bg=ACCENT, height=6, width=fill_width)
            bar_fill.place(x=0, y=0)

    def toggle_pause(self, icon=None, item=None):
        self._paused = not self._paused
        self._refresh_ui()

    def hide_window(self):
        self.root.withdraw()

    def show_window(self, icon=None, item=None):
        self._post(self.root.deiconify)

    def quit_app(self, icon=None, item=None):
        self._stop.set()
        self.log.flush()
        if self.icon:
            self.icon.stop()
        self._post(self.root.destroy)

    def run(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show Today", self.show_window, default=True),
            pystray.MenuItem(
                "Pause Tracking",
                lambda icon, item: self._post(self.toggle_pause),
                checked=lambda item: self._paused,
            ),
            pystray.MenuItem("Quit", self.quit_app),
        )
        self.icon = pystray.Icon("time-tracker", app_icon(), "Time Tracker", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()
        self.root.mainloop()


def main():
    setup_logging("time-tracker")
    if not _acquire_single_instance_lock():
        return
    app = TimeTrackerApp()
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        log.exception("fatal error")

        with open(APP_DIR / "app_error.log", "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.ctime()} ---\n")
            f.write(traceback.format_exc())
        raise
