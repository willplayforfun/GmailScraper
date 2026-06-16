"""Main application window and screen router."""
import tkinter.messagebox as _mb
import traceback

import customtkinter as ctk

from .settings import Settings, exe_dir


def _check_auth_state(settings: Settings) -> str:
    """
    Return one of: 'needs_credentials' | 'needs_auth' | 'ready'
    Does NOT hit the network — file-existence + credential parse only.
    """
    creds_path = settings.config_dir() / "credentials.json"
    token_path = settings.config_dir() / "token.json"

    if not creds_path.exists():
        return "needs_credentials"
    if not token_path.exists():
        return "needs_auth"

    # Try loading the token to catch obviously invalid JSON
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(
            str(token_path),
            ["https://www.googleapis.com/auth/gmail.readonly"],
        )
        if not creds.refresh_token:
            return "needs_auth"
    except Exception:
        return "needs_auth"

    return "ready"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Gmail Scraper")
        self.geometry("700x620")
        self.minsize(620, 520)

        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.settings = Settings.load()
        self._screen: ctk.CTkFrame | None = None

        self._route()

    def _route(self) -> None:
        state = _check_auth_state(self.settings)
        if state == "ready":
            self.show_sync()
        else:
            self.show_setup(initial_state=state)

    def show_setup(self, initial_state: str = "needs_credentials") -> None:
        from .setup_screen import SetupScreen
        self._swap(SetupScreen(self, self.settings, on_complete=self.show_sync))
        self._screen.set_state(initial_state)  # type: ignore[attr-defined]

    def show_sync(self) -> None:
        from .sync_screen import SyncScreen
        self._swap(
            SyncScreen(
                self,
                self.settings,
                on_sign_out=lambda: self.show_setup("needs_auth"),
            )
        )

    def _swap(self, new_screen: ctk.CTkFrame) -> None:
        if self._screen is not None:
            self._screen.destroy()
        new_screen.pack(fill="both", expand=True)
        self._screen = new_screen


def main() -> None:
    try:
        app = App()
        app.mainloop()
    except Exception:
        tb = traceback.format_exc()
        crash_log = exe_dir() / "crash.log"
        try:
            crash_log.write_text(tb, encoding="utf-8")
            _mb.showerror(
                "Gmail Scraper — unexpected error",
                f"An unexpected error occurred.\nDetails written to:\n{crash_log}",
            )
        except Exception:
            pass
