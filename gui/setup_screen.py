"""
Setup / auth wizard screen.

States:
  needs_credentials  → user must supply credentials.json
  needs_auth         → credentials.json present, OAuth not yet done
  waiting            → OAuth flow in progress (browser open)
"""
import queue
import shutil
import threading
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import webbrowser
from pathlib import Path

import customtkinter as ctk

from .settings import Settings

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDS_HOWTO_URL = "https://developers.google.com/gmail/api/quickstart/python#authorize_credentials_for_a_desktop_application"


class SetupScreen(ctk.CTkFrame):
    def __init__(self, parent, settings: Settings, on_complete) -> None:
        super().__init__(parent, fg_color="transparent")
        self.settings = settings
        self.on_complete = on_complete
        self._auth_queue: queue.Queue = queue.Queue()
        self._auth_thread: threading.Thread | None = None
        self._polling = False

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Outer centred column
        col = ctk.CTkFrame(self, fg_color="transparent")
        col.place(relx=0.5, rely=0.5, anchor="center")

        # Icon + title
        icon_frame = ctk.CTkFrame(col, width=60, height=60, corner_radius=30,
                                  fg_color=("gray85", "gray20"))
        icon_frame.pack(pady=(0, 16))
        icon_frame.pack_propagate(False)
        ctk.CTkLabel(icon_frame, text="✉", font=ctk.CTkFont(size=28)).pack(
            expand=True
        )

        ctk.CTkLabel(col, text="Connect your Gmail",
                     font=ctk.CTkFont(size=20, weight="bold")).pack()
        self._subtitle = ctk.CTkLabel(
            col,
            text="A one-time setup using a Google Cloud OAuth credential.\nYour email stays on your machine.",
            font=ctk.CTkFont(size=13),
            text_color=("gray40", "gray65"),
            justify="center",
        )
        self._subtitle.pack(pady=(6, 24))

        # Steps container (replaced when state changes)
        self._steps_frame = ctk.CTkFrame(col, fg_color="transparent", width=420)
        self._steps_frame.pack()

        # Status / error label
        self._status_label = ctk.CTkLabel(
            col, text="", font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray65"), wraplength=400, justify="center",
        )
        self._status_label.pack(pady=(12, 0))

    def _clear_steps(self) -> None:
        for w in self._steps_frame.winfo_children():
            w.destroy()

    # ── state machine ─────────────────────────────────────────────────────────

    def set_state(self, state: str) -> None:
        self._clear_steps()
        self._status_label.configure(text="")
        if state == "needs_credentials":
            self._build_needs_credentials()
        elif state == "needs_auth":
            self._build_needs_auth()
        elif state == "waiting":
            self._build_waiting()

    def _build_needs_credentials(self) -> None:
        f = self._steps_frame

        # Step 1
        self._step_row(f, 1,
            "Get credentials.json from Google Cloud Console",
            sub="APIs & Services → Credentials → OAuth 2.0 Client ID → Desktop app → Download JSON",
            link=CREDS_HOWTO_URL,
        )

        # Step 2
        self._step_row(f, 2, "Select the downloaded file below")

        # File picker row
        picker = ctk.CTkFrame(f, fg_color=("gray90", "gray17"), corner_radius=8)
        picker.pack(fill="x", pady=(12, 0))

        self._file_label = ctk.CTkLabel(
            picker, text="No file selected",
            text_color=("gray50", "gray55"), font=ctk.CTkFont(size=12),
        )
        self._file_label.pack(side="left", padx=12, pady=10, expand=True, anchor="w")

        ctk.CTkButton(
            picker, text="Browse…", width=90,
            command=self._browse_credentials,
        ).pack(side="right", padx=10, pady=8)

        # Sign-in button (disabled until file chosen)
        self._signin_btn = ctk.CTkButton(
            f, text="Sign in with Google", state="disabled",
            command=self._start_auth,
        )
        self._signin_btn.pack(fill="x", pady=(14, 0))

    def _build_needs_auth(self) -> None:
        f = self._steps_frame

        creds_path = self.settings.config_dir() / "credentials.json"
        status = f"credentials.json found at:\n{creds_path}" if creds_path.exists() else \
                 "credentials.json not found — go back and add it."

        ctk.CTkLabel(f, text=status, font=ctk.CTkFont(size=12),
                     text_color=("gray40", "gray65"), justify="center",
                     wraplength=400).pack(pady=(0, 16))

        self._signin_btn = ctk.CTkButton(
            f, text="Sign in with Google",
            state="normal" if creds_path.exists() else "disabled",
            command=self._start_auth,
        )
        self._signin_btn.pack(fill="x")

    def _build_waiting(self) -> None:
        f = self._steps_frame
        ctk.CTkLabel(f, text="Waiting for browser…",
                     font=ctk.CTkFont(size=14)).pack(pady=(0, 12))
        bar = ctk.CTkProgressBar(f, width=320, mode="indeterminate")
        bar.pack()
        bar.start()

        ctk.CTkButton(
            f, text="Cancel", fg_color="transparent",
            border_width=1, text_color=("gray40", "gray65"),
            command=self._cancel_auth,
        ).pack(pady=(14, 0))

    # ── step helper ───────────────────────────────────────────────────────────

    def _step_row(self, parent, number: int, text: str,
                  sub: str = "", link: str = "") -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)

        num_frame = ctk.CTkFrame(row, width=24, height=24, corner_radius=12,
                                 fg_color=("gray85", "gray25"))
        num_frame.pack(side="left", anchor="n", padx=(0, 10), pady=2)
        num_frame.pack_propagate(False)
        ctk.CTkLabel(num_frame, text=str(number),
                     font=ctk.CTkFont(size=11, weight="bold")).pack(expand=True)

        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True)

        main_label = ctk.CTkLabel(text_col, text=text, font=ctk.CTkFont(size=13),
                                  anchor="w", justify="left")
        main_label.pack(anchor="w")

        if link:
            link_label = ctk.CTkLabel(
                text_col, text="How to get credentials.json →",
                font=ctk.CTkFont(size=12, underline=True),
                text_color=("blue", "#5ac8fa"), cursor="hand2",
                anchor="w",
            )
            link_label.pack(anchor="w")
            link_label.bind("<Button-1>", lambda _e, u=link: webbrowser.open(u))

        if sub:
            ctk.CTkLabel(text_col, text=sub, font=ctk.CTkFont(size=11),
                         text_color=("gray50", "gray55"), anchor="w",
                         justify="left", wraplength=350).pack(anchor="w")

    # ── actions ───────────────────────────────────────────────────────────────

    def _browse_credentials(self) -> None:
        path = fd.askopenfilename(
            title="Select credentials.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        dest = self.settings.config_dir()
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, dest / "credentials.json")
        self._file_label.configure(text=Path(path).name,
                                   text_color=("gray20", "gray90"))
        self._signin_btn.configure(state="normal")
        self._status_label.configure(text="")

    def _start_auth(self) -> None:
        self.set_state("waiting")
        self._polling = True

        config_dir = self.settings.config_dir()

        def _do_auth() -> None:
            try:
                from google_auth_oauthlib.flow import InstalledAppFlow
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(config_dir / "credentials.json"), SCOPES
                )
                creds = flow.run_local_server(port=0, timeout_seconds=180)
                (config_dir / "token.json").write_text(creds.to_json(), encoding="utf-8")

                # Fetch email for the header
                from googleapiclient.discovery import build
                svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
                profile = svc.users().getProfile(userId="me").execute()
                email = profile.get("emailAddress", "")

                self._auth_queue.put({"ok": True, "email": email})
            except Exception as exc:
                self._auth_queue.put({"ok": False, "error": str(exc)})

        self._auth_thread = threading.Thread(target=_do_auth, daemon=True)
        self._auth_thread.start()
        self._poll_auth()

    def _poll_auth(self) -> None:
        if not self._polling:
            return
        try:
            result = self._auth_queue.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_auth)
            return

        self._polling = False
        if result["ok"]:
            self.settings.email = result["email"]
            self.settings.save()
            self.on_complete()
        else:
            self.set_state("needs_auth")
            self._status_label.configure(
                text=f"Authentication failed: {result['error']}",
                text_color=("red", "#ff453a"),
            )

    def _cancel_auth(self) -> None:
        self._polling = False
        self.set_state("needs_auth")
        self._status_label.configure(
            text="Authentication cancelled.",
            text_color=("gray40", "gray65"),
        )
