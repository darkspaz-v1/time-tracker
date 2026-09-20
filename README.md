# Time Tracker

Where your time actually went, per project, with no manual start/stop.

## How it works

- Tray plus panel. Samples the foreground window and matches its process name and title against
  project buckets defined in `config.json`.
- **Idle-aware** — stops counting after an input-idle threshold (default 3 minutes), so lunch does
  not get billed to whatever happened to be on screen.
- Anything that matches no bucket is still recorded as `Other: <process>` rather than being dropped.
  That list is how you find out which bucket you are missing.
- Output is `time_log.json`, a plain `{date: {bucket: seconds}}` map — deliberately trivial to read
  from something else.

## Notes

Buckets match on **window title keywords as well as process name**, because most real work happens
inside a handful of processes (a browser, a terminal, an editor) where the process name tells you
nothing about the project. Renaming a bucket is safe: keep the old keyword in the list and historical
titles keep matching.

**Stack:** Python, Tkinter, `pystray`, Pillow.

## Part of a suite

One of seven small Windows tray utilities built as separate, self-contained apps: each has its own
folder, its own virtualenv and its own `run.bat`, with no shared runtime. They are deliberately not a
framework — the only thing they share is a set of conventions.

| Convention | Why |
|---|---|
| Single-instance guard via a `.singleton.lock` file | An earlier `.instance.lock` design could get stuck after a force-kill and leave the app permanently unlaunchable |
| Relaunch brings the existing window forward | Previously a second launch silently did nothing, which was indistinguishable from the app being broken |
| Config lives in `config.json`, read at startup | Edit it, then fully exit the tray icon and relaunch — a running process never re-reads it |
| Tray icon generated in code (`icon.py`) | No binary asset to keep in sync |

## Running it

```
run.bat
```

That creates the virtualenv on first run, installs `requirements.txt`, and starts the app. Windows
only — these use Win32 APIs and a system tray.

## License

MIT — see [LICENSE](LICENSE).
