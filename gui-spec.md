# Gmail Scraper GUI — Implementation Spec

## Purpose

A self-contained desktop application that wraps the gmail_scraper package in a
simple GUI. The target user has downloaded a single `.exe`, has (or will create)
a Google Cloud project, and wants to sync their Gmail without touching a
terminal or Docker.

This spec is the source of truth for the GUI implementation. Anything not
specified here is the implementer's call; flag non-obvious decisions in comments.

## Scope

**In scope:**
- A native desktop window (Windows primary target, macOS/Linux best-effort).
- Setup flow: detect missing/invalid credentials, guide the user through dropping
  in `credentials.json` and completing OAuth.
- Sync flow: run `enumerate` then `fetch` from the gmail_scraper package,
  streaming progress and log output into the GUI.
- Persistent settings: data directory, concurrency, batch size.
- A single-file `.exe` produced by PyInstaller, distributed via GitHub Releases.
- A GitHub Actions workflow that builds and publishes the release artifact.

**Out of scope:**
- Viewing or searching email inside the app. This is a sync tool only.
- Multiple account support.
- macOS `.app` bundle or Linux AppImage (the build pipeline should leave room
  for them, but only Windows is required in this iteration).
- An installer. The `.exe` is portable — run it in place.

## Framework

**CustomTkinter.** Rationale: modern-looking widgets built on tkinter (stdlib),
no external C extensions, bundles cleanly with PyInstaller, cross-platform.
Acceptable alternatives are PyQt6/PySide6 (heavier, LGPL concerns) or plain
tkinter (uglier but zero extra deps). Flag the choice in the README.

Dependency: `customtkinter`. No other GUI dep.

## Project layout

```
gmail-scraper/              ← existing repo root
├── gmail_scraper/          ← existing package (unchanged)
├── gui/
│   ├── __init__.py
│   ├── app.py              ← main window, screen router
│   ├── setup_screen.py     ← auth/setup wizard
│   ├── sync_screen.py      ← main sync UI
│   ├── settings.py         ← persistent settings (JSON, platformdirs)
│   ├── worker.py           ← threading bridge between scraper and GUI
│   ├── log_handler.py      ← logging.Handler that feeds a queue the GUI reads
│   └── assets/
│       └── icon.ico        ← app icon for Windows
├── gmail_scraper_gui.spec  ← PyInstaller spec file (covers release + debug via env var)
├── build.py                ← optional: helper script to invoke PyInstaller locally
└── .github/
    └── workflows/
        ├── release.yml     ← triggered by v* tags
        └── debug-build.yml ← manual workflow_dispatch, sets DEBUG_BUILD=1
```

`gui/` is a package inside the same repo so it can import `gmail_scraper`
directly. No separate package or install step needed for the GUI.

## Data directory

**Default:** a `data/` directory next to the executable. When running as a
frozen PyInstaller bundle, "next to the exe" is `Path(sys.executable).parent / "data"`.
When running from source (dev), fall back to `Path(__file__).parent.parent / "data"`
(repo root). Detect frozen vs. source via `getattr(sys, "frozen", False)`.

This makes the tool fully portable: unzip the exe anywhere, run it, everything
lands in `data/` beside it. No registry, no AppData, no surprises.

The user can override the data root in Settings (stored in `settings.json`,
see below). The settings file itself always lives next to the exe at
`GmailScraper.settings.json` so it moves with the exe.

Default layout:

```
GmailScraper.exe            ← the portable exe
GmailScraper.settings.json  ← user settings (batch size, data dir override, etc.)
data/
├── config/
│   ├── credentials.json    ← user drops this in (or the app copies it)
│   └── token.json          ← written by OAuth flow
├── db/
│   └── gmail.sqlite
├── raw/                    ← sharded .eml files
└── logs/
```

The entire dataset is in `data/` — back up or move by copying that folder.

## Screens

### Screen 1 — Setup

Shown on first launch and whenever `token.json` is missing or expired and
cannot be refreshed.

**State machine:**

```
NEEDS_CREDENTIALS → NEEDS_AUTH → READY (→ switch to Sync screen)
```

**NEEDS_CREDENTIALS** (no `credentials.json` present):
- Brief one-paragraph explanation: what a Google Cloud project is, why it's
  needed, and that it's a one-time step.
- A "How to get credentials.json →" link to the Google Cloud Console docs
  (hardcoded URL in the spec; implementer should confirm it's current).
- A "Browse…" button to select `credentials.json` from disk. On selection,
  the app copies it into the config dir and advances to NEEDS_AUTH.

**NEEDS_AUTH** (credentials.json present, no valid token):
- Show the authenticated email from credentials.json (the `client_id` suffix
  gives the project; the actual email comes after auth — show "not yet signed in").
- A "Sign in with Google" button. Clicking it:
  1. Calls `InstalledAppFlow.run_local_server(port=0)` in a thread.
  2. Opens the system browser to the auth URL automatically.
  3. Shows a spinner and "Waiting for browser…" label.
  4. On success, writes `token.json`, shows a brief "Signed in as
     <email>" confirmation, then transitions to the Sync screen.
  5. On failure, shows the error inline and stays on this screen.

**Notes:**
- The browser open must be non-blocking (the tkinter mainloop cannot block).
  Use `threading.Thread` for the flow; communicate back via a `queue.Queue`
  that the main loop polls with `after()`.
- `InstalledAppFlow.run_local_server` handles the redirect listener and browser
  open automatically. The thread just calls it and puts the result on the queue.

### Screen 2 — Sync

The main screen, shown whenever a valid token exists.

**Layout (top to bottom):**

1. **Header bar** — app name left, "Settings" gear icon right, authenticated
   email displayed as muted text center-right.
2. **Stats row** — four metric cards: Total in queue, Done, Pending, Errors.
   Refreshed every 5 seconds while idle; after each batch while running.
3. **Action buttons** — "Enumerate" and "Sync" (runs enumerate + fetch) as
   primary buttons, side by side. A "Fetch only" secondary button for when
   enumeration was already done. Buttons disable while a job is running.
4. **Progress bar** — indeterminate while enumerating (count unknown),
   switches to determinate (0–100%) once `fetch_queue` total is known.
   Hidden when idle.
5. **Rate indicator** — "≈ 42 msg/sec · ETA 12 min" updated from progress
   log lines. Hidden when idle.
6. **Log panel** — scrollable text area, last ~500 lines, auto-scrolls to
   bottom. Each line is a rendered JSON log entry: timestamp (short), level
   badge (color-coded), message text. Not raw JSON — format it readably.
7. **Stop button** — appears only while running. Sends SIGTERM equivalent
   (sets the worker's stop flag) so the in-flight batch completes then exits.

**Settings panel** (opens as a top-level modal window from the gear icon):
- Data directory (text field + "Browse…"). Changing this and clicking Save
  restarts the app's effective paths; warn that existing data at the old
  location is not moved.
- Batch size (int spinner, default 100, range 1–100).
- Max concurrency (int spinner, default 5, range 1–20).
- Include spam/trash (checkbox).
- "Open data folder" button (opens in Explorer/Finder).
- "Sign out" button — deletes `token.json`, returns to Setup screen.

## Threading / progress bridge

The gmail_scraper package must NOT be called on the tkinter main thread (it
would block the UI). Architecture:

```
Main thread (tkinter event loop)
  │  after(100, poll)          ← polls a queue every 100ms
  │
Worker thread
  │  calls gmail_scraper.enumerate.run_enumerate(...)
  │  calls gmail_scraper.fetch.run_fetch(...)
  │  ↓ progress events → queue.Queue → main thread
```

**`gui/log_handler.py`** — a `logging.Handler` subclass that puts
`LogRecord`s on a `queue.Queue`. Installed as a root logger handler at worker
start; removed at worker end.

**`gui/worker.py`** — a `Worker` class that:
- Accepts a `queue.Queue` for outbound events.
- Runs in a `threading.Thread`.
- Accepts a stop event (`threading.Event`) — checked between batches. The
  fetch loop in `gmail_scraper/fetch.py` already checks `_SHUTDOWN`; the
  worker sets that module-level flag via the stop event.
- Posts typed events to the queue: `{"type": "log", "record": record}`,
  `{"type": "progress", "done": N, "total": M, "rate": r, "eta": t}`,
  `{"type": "done", "success": N, "errors": N}`,
  `{"type": "error", "exc": str}`.

**Progress extraction:** Parse the structured log records for the fields
`done`, `rate_per_sec`, `pending`, `eta_sec` that `fetch.py` already emits
in its progress log lines. No changes to the scraper needed.

**Caveat:** `gmail_scraper/fetch.py` uses a module-level `_SHUTDOWN` flag set
by signal handlers. Since we're not in Docker and signal handling differs,
the worker should set this flag directly via the module attribute rather than
sending SIGTERM to itself. Import the module and set
`gmail_scraper.fetch._SHUTDOWN = True` from the stop path.

## Persistent settings

`gui/settings.py` reads/writes `GmailScraper.settings.json` in the same
directory as the executable (frozen) or repo root (source). Keeping it next to
the exe means the whole tool — exe + settings + data — is relocatable as a
folder.

```json
{
  "data_dir": null,
  "batch_size": 100,
  "max_concurrency": 5,
  "include_spam_trash": false
}
```

`null` for `data_dir` means use the default (`exe_dir / "data"`). The settings
module exposes a `Settings` dataclass with `load()` / `save()` methods, accessed
as a singleton. `exe_dir` resolution:

```python
import sys
from pathlib import Path

def exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent  # repo root when running from source
```

## PyInstaller spec

File: `gmail_scraper_gui.spec`.

Key requirements:
- `onefile=True` — single `.exe`.
- `console=False` (`windowed=True`) always in release builds. No exceptions —
  see crash log note below for how tracebacks are preserved.
- A separate DEBUG build (see GitHub Actions below) passes `console=True` via
  an environment variable read inside the spec: `bool(os.environ.get("DEBUG_BUILD"))`.
  The spec file reads this at build time so the same `.spec` file covers both
  modes.
- Hidden imports: `googleapiclient`, `google.auth`, `google.auth.transport.requests`,
  `google_auth_oauthlib`, `customtkinter`. PyInstaller may not discover all of
  these automatically; list them explicitly.
- Include `customtkinter`'s data files (themes, images). Use
  `collect_data_files("customtkinter")` in the spec.
- The icon: `gui/assets/icon.ico`.
- Output name: `GmailScraper.exe` (release) / `GmailScraper-debug.exe` (debug,
  so they are distinguishable as release artifacts).

The spec file is checked in and used exclusively by CI and local builds. Do not
rely on `pyinstaller` CLI flags for build configuration — everything is in the spec.

## GitHub Actions workflows

### Release — `.github/workflows/release.yml`

**Trigger:** push of a tag matching `v*` (e.g. `v1`, `v1.0`, `v1.0.0` — any
`v`-prefixed tag).

**Steps:**
1. `actions/checkout`
2. `actions/setup-python` with Python 3.12
3. `pip install pyinstaller customtkinter google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2`
4. `pyinstaller gmail_scraper_gui.spec` (no `DEBUG_BUILD` env var set)
5. `actions/upload-artifact` — upload `dist/GmailScraper.exe` for inspection
6. `softworks-ai/action-gh-release` (or `gh release create`) — attach
   `dist/GmailScraper.exe` to a GitHub Release named after the tag, with
   `generate_release_notes: true`

Runs on `windows-latest` only. PyInstaller produces platform-native binaries;
a Windows exe must be built on Windows.

Optionally add a second job (same workflow, `continue-on-error: true`) that
builds on `ubuntu-latest` and `macos-latest` and uploads `GmailScraper-linux`
and `GmailScraper-mac` as additional release assets.

### Debug build — `.github/workflows/debug-build.yml`

**Trigger:** `workflow_dispatch` only (manual trigger from the Actions tab).
No tag required. Accepts no inputs — the debug flag is unconditional.

**Steps:** same as release, but sets `DEBUG_BUILD=1` in the environment before
calling PyInstaller. The spec reads this and sets `console=True`, producing
`GmailScraper-debug.exe`. Uploads as a workflow artifact (not a release asset)
so it's easy to download for a debugging session without cluttering releases.

## Dependencies (GUI only)

```
customtkinter       # brings in darkdetect (~15 KB) and packaging as transitive deps
```

`platformdirs` is no longer needed — the data directory is resolved relative to
the exe, not via platform conventions. All other deps come from the existing
`gmail_scraper` package (`pyproject.toml`). Add the GUI extra there:

```toml
[project.optional-dependencies]
gui = ["customtkinter"]
dev = ["pytest"]
```

CustomTkinter vs. plain tkinter in the final bundle: ~5–8 MB larger, dominated
by bundled theme JSON files and the `darkdetect` helper. Total exe size
expectation: 50–80 MB either way (Python runtime + google-api deps are the bulk).

## Acceptance criteria

1. Running `GmailScraper.exe` on a Windows machine with no Python installed
   opens a GUI window within 3 seconds.
2. On first launch (no `credentials.json`), Setup screen is shown with clear
   instructions; browsing for and selecting a valid `credentials.json` advances
   the flow.
3. Clicking "Sign in with Google" opens the system browser, completing the
   OAuth flow writes `token.json`, and the app transitions to the Sync screen
   without requiring a restart.
4. On subsequent launches (valid `token.json`), the Sync screen opens directly
   (no Setup screen).
5. Clicking "Sync" starts enumerate + fetch; the progress bar updates
   continuously; the log panel streams readable log lines.
6. Clicking "Stop" while syncing finishes the in-flight batch and halts;
   re-clicking "Sync" resumes from where it stopped (no re-downloads).
7. The stats cards reflect accurate DB counts after each run.
8. Settings changes (batch size, concurrency) are persisted across app restarts.
9. "Sign out" deletes `token.json` and returns to the Setup screen.
10. The GitHub Actions release job produces a `GmailScraper.exe` that satisfies
    criteria 1–9 on a clean Windows machine.

## Notes for the implementer

- CustomTkinter's `CTkTextbox` does not support per-line coloring. For the
  log panel, either use a `tk.Text` widget with tags for color (acceptable),
  or keep it monochrome. Do not block on this.
- The `InstalledAppFlow.run_local_server` call opens a browser and blocks
  until the redirect completes. It must run in a thread. If the user closes
  the browser without completing auth, the thread may hang; add a timeout
  parameter if the library supports it, otherwise expose a "Cancel" button
  that forcibly joins and discards the thread (acceptable data loss for auth).
- Pass `cache_discovery=False` to every `googleapiclient.discovery.build()`
  call in the worker. In a onefile PyInstaller bundle the library resolves its
  discovery document cache to `sys._MEIPASS`, a temp directory that is wiped on
  each launch, so the cache is always a miss and can occasionally error on
  write. Disabling it causes one extra network fetch per `build()` call
  (milliseconds, happens once per sync run) and is otherwise harmless.
- The `.exe` embeds all Python code and deps. It will be large (~50–80 MB is
  typical for this dep set). That is acceptable.
- On Windows, `windowed=True` suppresses the console, which also suppresses
  unhandled exception tracebacks. Wrap the top-level `app.mainloop()` call
  in a try/except that writes the traceback to a crash log in the data dir
  and shows a `messagebox.showerror` to the user.
