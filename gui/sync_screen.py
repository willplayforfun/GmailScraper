"""
Main sync screen: stats, action buttons, progress bar, log panel.
Also hosts the Settings modal (opened from the gear button).
"""
import logging
import os
import queue
import sqlite3
import subprocess
import sys
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
from pathlib import Path

import customtkinter as ctk

from .settings import Settings
from .worker import Worker

# How often (ms) to poll the worker queue and refresh stats
_POLL_MS = 120
_STATS_MS = 5000
_MAX_LOG_LINES = 500


class _Tooltip:
    """Simple hover tooltip for any tkinter widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text = text
        self._tw: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event=None) -> None:
        x = self._widget.winfo_rootx() + 0
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tw = tk.Toplevel(self._widget)
        self._tw.wm_overrideredirect(True)
        self._tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            self._tw, text=self._text, justify="left",
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("Segoe UI", 9), padx=6, pady=3,
        )
        lbl.pack()

    def _hide(self, _event=None) -> None:
        if self._tw:
            self._tw.destroy()
            self._tw = None


def _log_colors() -> dict:
    dark = ctk.get_appearance_mode() == "Dark"
    return {
        "bg":      "#1c1c1e" if dark else "#f2f2f7",
        "fg":      "#e5e5e5" if dark else "#1c1c1e",
        "ts":      "#636366" if dark else "#8e8e93",
        "INFO":    "#63a4f5" if dark else "#0071e3",
        "WARNING": "#ffd60a" if dark else "#9a6700",
        "ERROR":   "#ff453a" if dark else "#d70015",
        "DEBUG":   "#636366" if dark else "#8e8e93",
    }


class SyncScreen(ctk.CTkFrame):
    def __init__(self, parent, settings: Settings, on_sign_out) -> None:
        super().__init__(parent, fg_color="transparent")
        self.settings = settings
        self.on_sign_out = on_sign_out

        self._worker: Worker | None = None
        self._event_queue: queue.Queue = queue.Queue(maxsize=2000)
        self._log_line_count = 0
        self._total_queued = 0
        self._done_before = 0

        self._build_ui()
        self._poll_queue()
        self._refresh_stats()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Header bar
        hdr = ctk.CTkFrame(self, fg_color=("gray90", "gray13"), corner_radius=0,
                           height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="Gmail Scraper",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
            side="left", padx=16
        )
        self._email_label = ctk.CTkLabel(
            hdr, text=self.settings.email or "",
            font=ctk.CTkFont(size=12), text_color=("gray45", "gray60"),
        )
        self._email_label.pack(side="left", expand=True)

        gear_btn = ctk.CTkButton(
            hdr, text="⚙", width=36, height=28,
            fg_color="transparent", hover_color=("gray80", "gray25"),
            command=self._open_settings,
        )
        gear_btn.pack(side="right", padx=8)

        # Body padding frame
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=16)

        # Stats row
        stats_row = ctk.CTkFrame(body, fg_color="transparent")
        stats_row.pack(fill="x", pady=(0, 14))
        stats_row.columnconfigure((0, 1, 2, 3), weight=1)

        self._stat_total  = self._stat_card(stats_row, "Total",   col=0)
        self._stat_done   = self._stat_card(stats_row, "Done",    col=1, color=("green", "#32d74b"))
        self._stat_pending = self._stat_card(stats_row, "Pending", col=2)
        self._stat_errors = self._stat_card(stats_row, "Errors",  col=3, color=("red", "#ff453a"))

        # Action buttons
        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 12))

        self._sync_btn = ctk.CTkButton(
            btn_row, text="▶  Sync", command=lambda: self._start("run")
        )
        self._sync_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))
        _Tooltip(self._sync_btn, "Enumerate and then download all emails.")

        self._enum_btn = ctk.CTkButton(
            btn_row, text="Enumerate",
            fg_color="transparent", border_width=1,
            text_color=("gray10", "gray90"),
            command=lambda: self._start("enumerate"),
        )
        self._enum_btn.pack(side="left", padx=(0, 6))
        _Tooltip(self._enum_btn, "Retrieve a list of all emails that can be downloaded.")

        self._fetch_btn = ctk.CTkButton(
            btn_row, text="Download",
            fg_color="transparent", border_width=1,
            text_color=("gray10", "gray90"),
            command=lambda: self._start("fetch"),
        )
        self._fetch_btn.pack(side="left", padx=(0, 6))
        _Tooltip(self._fetch_btn, "Download all enumerated emails.")

        # Progress area (hidden while idle)
        self._progress_frame = ctk.CTkFrame(body, fg_color="transparent")

        self._progress_bar = ctk.CTkProgressBar(self._progress_frame)
        self._progress_bar.pack(fill="x")
        self._progress_bar.set(0)

        status_row = ctk.CTkFrame(self._progress_frame, fg_color="transparent")
        status_row.pack(fill="x", pady=(4, 0))

        self._stop_btn = ctk.CTkButton(
            status_row, text="⏹", width=32, height=32,
            fg_color="transparent", hover_color=("gray80", "gray25"),
            text_color=("red", "#ff453a"),
            font=ctk.CTkFont(size=16),
            command=self._stop,
        )
        self._stop_btn.pack(side="left")

        self._rate_label = ctk.CTkLabel(
            status_row, text="",
            font=ctk.CTkFont(size=12), text_color=("gray45", "gray60"),
            anchor="e",
        )
        self._rate_label.pack(side="left", fill="x", expand=True)

        # Log panel — use tk.Text for per-line colour support
        log_frame = ctk.CTkFrame(body, fg_color="transparent")
        log_frame.pack(fill="both", expand=True, pady=(4, 0))

        self._log_text = tk.Text(
            log_frame,
            state="disabled",
            wrap="none",
            font=("Courier New", 10),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        self._log_text.pack(side="left", fill="both", expand=True)
        self._apply_log_colors()

        sb = ctk.CTkScrollbar(log_frame, command=self._log_text.yview)
        sb.pack(side="right", fill="y")
        self._log_text.configure(yscrollcommand=sb.set)

        # Configure colour tags
        colors = _log_colors()
        self._log_text.tag_configure("ts",      foreground=colors["ts"])
        self._log_text.tag_configure("INFO",    foreground=colors["INFO"])
        self._log_text.tag_configure("WARNING", foreground=colors["WARNING"])
        self._log_text.tag_configure("ERROR",   foreground=colors["ERROR"])
        self._log_text.tag_configure("DEBUG",   foreground=colors["DEBUG"])

    def _apply_log_colors(self) -> None:
        colors = _log_colors()
        self._log_text.configure(bg=colors["bg"], fg=colors["fg"],
                                 insertbackground=colors["fg"])

    def _stat_card(self, parent, label: str, col: int,
                   color: tuple | None = None) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, fg_color=("gray88", "gray17"), corner_radius=8)
        card.grid(row=0, column=col, padx=(0, 8), sticky="ew")
        ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=11),
                     text_color=("gray45", "gray55")).pack(pady=(10, 0))
        val_label = ctk.CTkLabel(card, text="—",
                                 font=ctk.CTkFont(size=22, weight="bold"),
                                 text_color=color or ("gray10", "gray90"))
        val_label.pack(pady=(2, 10))
        return val_label

    # ── worker control ────────────────────────────────────────────────────────

    def _start(self, mode: str) -> None:
        if self._worker and self._worker.is_running():
            return
        self._done_before = self._query_done_count()
        self._set_running(True, mode)
        self._worker = Worker(self._event_queue, self.settings, mode=mode)
        self._worker.start()

    def _stop(self) -> None:
        if self._worker:
            self._worker.stop()
        self._stop_btn.configure(state="disabled", text="⏳")

    def _set_running(self, running: bool, mode: str = "") -> None:
        state = "disabled" if running else "normal"
        self._sync_btn.configure(state=state,
                                 text="Syncing…" if running and mode == "run" else "▶  Sync")
        self._enum_btn.configure(state=state)
        self._fetch_btn.configure(state=state)

        if running:
            self._stop_btn.configure(state="normal", text="⏹")
            self._progress_frame.pack(fill="x", pady=(0, 8))
            self._progress_bar.configure(mode="indeterminate")
            self._progress_bar.start()
            self._rate_label.configure(text="")
        else:
            self._progress_frame.pack_forget()
            self._progress_bar.stop()
            self._rate_label.configure(text="")

    # ── queue polling ─────────────────────────────────────────────────────────

    def _poll_queue(self) -> None:
        try:
            while True:
                event = self._event_queue.get_nowait()
                self._dispatch(event)
        except queue.Empty:
            pass
        self.after(_POLL_MS, self._poll_queue)

    def _dispatch(self, event: dict) -> None:
        t = event["type"]
        if t == "log":
            self._on_log(event["record"])
        elif t == "done":
            self._set_running(False)
            self._refresh_stats()
            new_count = self._query_done_count() - getattr(self, "_done_before", 0)
            if event.get("stopped"):
                self._append_log_line(
                    f"Sync stopped. {new_count:,} new emails fetched.", level="WARNING"
                )
            else:
                self._append_log_line(
                    f"✓ Sync finished. {new_count:,} new emails fetched.", level="INFO"
                )
        elif t == "error":
            self._set_running(False)
            self._refresh_stats()
            self._append_log_line(f"Worker error:\n{event['exc']}", level="ERROR")

    def _on_log(self, record: logging.LogRecord) -> None:
        # Enumeration progress
        enumerated = getattr(record, "enumerated", None)
        total_estimate = getattr(record, "total_estimate", None)
        if enumerated is not None and total_estimate:
            self._update_enum_progress(enumerated, total_estimate)

        # Fetch progress
        done = getattr(record, "done", None)
        pending = getattr(record, "pending", None)
        if done is not None and pending is not None:
            total = done + pending
            self._total_queued = max(self._total_queued, total)
            rate = getattr(record, "rate_per_sec", 0)
            eta = getattr(record, "eta_sec", 0)
            self._update_progress(done, self._total_queued, rate, eta)

        # Format and append to log panel
        import time
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        level = record.levelname
        msg = record.getMessage()
        self._append_log_line(f"{ts}  [{level:<7}]  {msg}", level=level, ts_end=10)

    def _update_enum_progress(self, enumerated: int, total_estimate: int) -> None:
        if total_estimate > 0:
            self._progress_bar.stop()
            self._progress_bar.configure(mode="determinate")
            self._progress_bar.set(min(enumerated / total_estimate, 1.0))
        self._rate_label.configure(
            text=f"Enumerating… {enumerated:,} / ~{total_estimate:,} IDs"
        )

    def _update_progress(self, done: int, total: int, rate: float, eta: int) -> None:
        if total > 0:
            self._progress_bar.stop()
            self._progress_bar.configure(mode="determinate")
            self._progress_bar.set(done / total)
        pct = f"{done / total * 100:.0f}%" if total else "?"
        rate_str = f"≈ {rate:.0f} msg/sec" if rate else ""
        eta_str = ""
        if eta > 0:
            m, s = divmod(int(eta), 60)
            h, m = divmod(m, 60)
            eta_str = f" · ETA {h}h {m}m" if h else f" · ETA {m}m {s}s"
        self._rate_label.configure(
            text=f"{done:,} / {total:,} — {pct}   {rate_str}{eta_str}"
        )

    def _append_log_line(self, text: str, level: str = "INFO",
                         ts_end: int = 0) -> None:
        self._log_text.configure(state="normal")

        # Trim to max lines
        if self._log_line_count >= _MAX_LOG_LINES:
            self._log_text.delete("1.0", "2.0")
        else:
            self._log_line_count += 1

        line = text + "\n"
        start = self._log_text.index("end-1c")

        self._log_text.insert("end", line)

        # Apply colour tags
        end = self._log_text.index("end-1c")
        if ts_end and level:
            ts_start = f"{start}"
            ts_stop = f"{start}+{ts_end}c"
            self._log_text.tag_add("ts", ts_start, ts_stop)
            self._log_text.tag_add(level, ts_stop, end)
        elif level in ("WARNING", "ERROR"):
            self._log_text.tag_add(level, start, end)

        self._log_text.configure(state="disabled")
        self._log_text.see("end")

    # ── stats polling ─────────────────────────────────────────────────────────

    def _query_done_count(self) -> int:
        db = self.settings.db_path()
        if not db.exists():
            return 0
        try:
            conn = sqlite3.connect(str(db))
            row = conn.execute(
                "SELECT COUNT(*) FROM fetch_queue WHERE status = 'done'"
            ).fetchone()
            conn.close()
            return row[0] if row else 0
        except Exception:
            return 0

    def _refresh_stats(self) -> None:
        db = self.settings.db_path()
        if not db.exists():
            for lbl in (self._stat_total, self._stat_done,
                        self._stat_pending, self._stat_errors):
                lbl.configure(text="—")
            self.after(_STATS_MS, self._refresh_stats)
            return
        try:
            conn = sqlite3.connect(str(db))
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM fetch_queue GROUP BY status"
            ).fetchall()
            conn.close()
            counts = {r[0]: r[1] for r in rows}
            total = sum(counts.values())
            self._stat_total.configure(text=f"{total:,}")
            self._stat_done.configure(text=f"{counts.get('done', 0):,}")
            self._stat_pending.configure(text=f"{counts.get('pending', 0):,}")
            self._stat_errors.configure(text=f"{counts.get('error', 0):,}")
            if total:
                self._total_queued = max(self._total_queued, total)
        except Exception:
            pass
        self.after(_STATS_MS, self._refresh_stats)

    # ── settings modal ────────────────────────────────────────────────────────

    def _open_settings(self) -> None:
        SettingsDialog(self, self.settings, on_sign_out=self.on_sign_out)


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, settings: Settings, on_sign_out) -> None:
        super().__init__(parent)
        self.title("Settings")
        self.geometry("480x540")
        self.resizable(False, False)
        self.grab_set()

        self.settings = settings
        self.on_sign_out = on_sign_out
        self._dirty = False

        self._build_ui()
        self._snapshot()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── dirty tracking ────────────────────────────────────────────────────────

    def _snapshot(self) -> None:
        self._saved = {
            "dir":   self._dir_var.get(),
            "batch": self._batch_var.get(),
            "conc":  self._conc_var.get(),
            "spam":  self._spam_var.get(),
        }
        self._dirty = False
        self._update_banner()

    def _check_dirty(self, *_) -> None:
        dirty = (
            self._dir_var.get()  != self._saved["dir"]   or
            self._batch_var.get() != self._saved["batch"] or
            self._conc_var.get()  != self._saved["conc"]  or
            self._spam_var.get()  != self._saved["spam"]
        )
        if dirty != self._dirty:
            self._dirty = dirty
            self._update_banner()

    def _update_banner(self) -> None:
        if self._dirty:
            self._banner_inner.place(relx=0, rely=0, relwidth=1, relheight=1)
        else:
            self._banner_inner.place_forget()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 20, "pady": 6}

        # ── Save banner (always reserves 44px; content shown only when dirty) ──
        banner_slot = ctk.CTkFrame(self, height=44, corner_radius=0,
                                   fg_color="transparent")
        banner_slot.pack(fill="x")
        banner_slot.pack_propagate(False)

        self._banner_inner = ctk.CTkFrame(
            banner_slot, corner_radius=0,
            fg_color=("#dbeafe", "#1e3a5f"),
        )
        ctk.CTkLabel(
            self._banner_inner, text="You have unsaved changes",
            font=ctk.CTkFont(size=12), text_color=("gray25", "gray80"),
        ).pack(side="left", padx=16)
        ctk.CTkButton(
            self._banner_inner, text="Save", width=80,
            command=self._save,
        ).pack(side="right", padx=10, pady=6)
        # Not placed until dirty

        # ── Data directory ────────────────────────────────────────────────────
        ctk.CTkLabel(self, text="Data directory", font=ctk.CTkFont(weight="bold"),
                     anchor="w").pack(fill="x", **pad)

        dir_row = ctk.CTkFrame(self, fg_color="transparent")
        dir_row.pack(fill="x", padx=20, pady=(0, 6))

        self._dir_var = ctk.StringVar(value=self.settings.data_dir or "")
        self._dir_var.trace_add("write", self._check_dirty)
        self._dir_entry = ctk.CTkEntry(dir_row, textvariable=self._dir_var,
                                       placeholder_text="default (data/ next to exe)")
        self._dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(dir_row, text="Browse…", width=90,
                      command=self._browse_dir).pack(side="left")

        ctk.CTkLabel(self, text="Leave blank to use the default location (data/ next to the exe).",
                     font=ctk.CTkFont(size=11), text_color=("gray45", "gray55"),
                     anchor="w").pack(fill="x", padx=20, pady=(0, 12))

        ctk.CTkFrame(self, height=1, fg_color=("gray80", "gray25")).pack(
            fill="x", padx=20, pady=4)

        # ── Batch size ────────────────────────────────────────────────────────
        ctk.CTkLabel(self, text="Batch size (1–100)", anchor="w").pack(fill="x", **pad)
        self._batch_var = ctk.StringVar(value=str(self.settings.batch_size))
        self._batch_var.trace_add("write", self._check_dirty)
        ctk.CTkEntry(self, textvariable=self._batch_var, width=80).pack(
            anchor="w", padx=20, pady=(0, 6))

        # ── Concurrency ───────────────────────────────────────────────────────
        ctk.CTkLabel(self, text="Max concurrency (1–20)", anchor="w").pack(fill="x", **pad)
        self._conc_var = ctk.StringVar(value=str(self.settings.max_concurrency))
        self._conc_var.trace_add("write", self._check_dirty)
        ctk.CTkEntry(self, textvariable=self._conc_var, width=80).pack(
            anchor="w", padx=20, pady=(0, 6))

        # ── Include spam/trash ────────────────────────────────────────────────
        self._spam_var = ctk.BooleanVar(value=self.settings.include_spam_trash)
        self._spam_var.trace_add("write", self._check_dirty)
        ctk.CTkCheckBox(self, text="Include spam and trash",
                        variable=self._spam_var).pack(anchor="w", padx=20, pady=6)

        ctk.CTkFrame(self, height=1, fg_color=("gray80", "gray25")).pack(
            fill="x", padx=20, pady=8)

        # ── Actions ───────────────────────────────────────────────────────────
        ctk.CTkButton(self, text="Open data folder", fg_color="transparent",
                      border_width=1, text_color=("gray10", "gray90"),
                      command=self._open_data_folder).pack(fill="x", padx=20, pady=(0, 6))

        ctk.CTkButton(self, text="Sign out",
                      fg_color="transparent", border_width=1,
                      text_color=("red", "#ff453a"), border_color=("red", "#ff453a"),
                      command=self._sign_out).pack(fill="x", padx=20, pady=(0, 20))

    # ── actions ───────────────────────────────────────────────────────────────

    def _browse_dir(self) -> None:
        path = fd.askdirectory(title="Select data directory")
        if path:
            self._dir_var.set(path)

    def _open_data_folder(self) -> None:
        folder = self.settings.data_root()
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(folder))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def _on_close(self) -> None:
        if not self._dirty:
            self.destroy()
            return
        answer = mb.askyesnocancel(
            "Unsaved changes",
            "You have unsaved changes. Save before closing?",
            parent=self,
        )
        if answer is None:   # Cancel — stay open
            return
        if answer:           # Yes — save then close
            self._save()
        else:                # No — discard and close
            self.destroy()

    def _save(self) -> None:
        dir_val = self._dir_var.get().strip() or None

        try:
            batch = int(self._batch_var.get())
            assert 1 <= batch <= 100
        except (ValueError, AssertionError):
            mb.showerror("Invalid value", "Batch size must be between 1 and 100.", parent=self)
            return

        try:
            conc = int(self._conc_var.get())
            assert 1 <= conc <= 20
        except (ValueError, AssertionError):
            mb.showerror("Invalid value", "Max concurrency must be between 1 and 20.", parent=self)
            return

        if dir_val and dir_val != self.settings.data_dir:
            mb.showinfo(
                "Data directory changed",
                "The new data directory will be used on the next sync.\n"
                "Existing data at the old location is not moved.",
                parent=self,
            )

        self.settings.data_dir = dir_val
        self.settings.batch_size = batch
        self.settings.max_concurrency = conc
        self.settings.include_spam_trash = self._spam_var.get()
        self.settings.save()
        self.destroy()

    def _sign_out(self) -> None:
        if not mb.askyesno("Sign out",
                           "Delete token.json and sign out?\n"
                           "You will need to sign in again before syncing.",
                           parent=self):
            return
        token = self.settings.config_dir() / "token.json"
        if token.exists():
            token.unlink()
        self.destroy()
        self.on_sign_out()
