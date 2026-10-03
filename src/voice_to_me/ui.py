"""Voice to Me: a focused Windows interface and optional system tray controls."""

from __future__ import annotations

import queue
import tkinter as tk
from pathlib import Path
from threading import Lock
from tkinter import ttk
from typing import Any

from .config import ConfigurationError
from .history_ui import HistoryPage
from .settings_dialog import SettingsDialog, describe_paste_shortcut, describe_shortcuts
from .sounds import SoundCues

ASSETS = Path(__file__).parent / "assets"
PROCESSING_STATES = frozenset({"preparing", "loading_model", "transcribing", "refining"})
COLORS = {
    "background": "#F3F7F6",
    "surface": "#FFFFFF",
    "ink": "#163D45",
    "muted": "#49646A",
    "line": "#D5E2DF",
    "pine": "#123B42",
    "coral": "#EF896B",
    "teal": "#167C80",
    "error": "#B3383A",
    "success": "#16725D",
}

STATE_PRESENTATION = {
    "ready": ("Ready to dictate", "Your next message starts with your voice.", "teal", "01"),
    "recording": (
        "Recording your voice",
        "Speak at your own pace. Press again to finish.",
        "coral",
        "●",
    ),
    "preparing": (
        "Loading the speech model",
        "Preparing local transcription before you dictate.",
        "teal",
        "02",
    ),
    "loading_model": (
        "Loading the speech model",
        "Preparing local transcription before you dictate.",
        "teal",
        "02",
    ),
    "transcribing": (
        "Transcribing",
        "Local Whisper is turning your recording into text.",
        "teal",
        "02",
    ),
    "refining": (
        "Refining your writing",
        "Codex is polishing the text using your writing style.",
        "teal",
        "02",
    ),
    "copied": (
        "Copied to clipboard",
        "Paste into any app when you're ready with Ctrl + V.",
        "success",
        "✓",
    ),
    "error": ("Let's try again", "The last attempt did not finish.", "error", "!"),
}

NOTIFICATIONS = {
    "preparing": "Loading the local speech model. Your recording stays on this computer.",
    "loading_model": "Loading the local speech model. Your recording stays on this computer.",
    "transcribing": "Turning your recording into text locally.",
    "refining": "Polishing your text with your writing style.",
    "copied": "Your final text is on the clipboard. Paste whenever you're ready.",
    "error": "The attempt did not finish. Open Voice to Me to try again.",
}


class VoiceUI:
    """All Tk operations stay on the main thread; observers use a queue."""

    def __init__(
        self,
        controller: Any,
        *,
        config_path: Path,
        profile_path: Path,
        hotkey_label: str,
        paste_hotkey_label: str = "",
        settings_service: Any = None,
        on_shutdown: Any = None,
    ) -> None:
        self.controller = controller
        self._shutdown = on_shutdown or controller.shutdown
        self.config_path = Path(config_path)
        self.profile_path = Path(profile_path)
        self.hotkey_label = hotkey_label
        self.paste_hotkey_label = paste_hotkey_label
        self.settings_service = settings_service
        self._settings_dialog: SettingsDialog | None = None
        self._capture_action_lock = False
        self._last_message = ""
        self._events: queue.Queue[tuple[str, Any, str]] = queue.Queue()
        self._sounds = SoundCues()
        self._state = "ready"
        self._tray: Any = None
        self._tray_images: dict[str, Any] = {}
        self._tray_error = ""
        self._quitting = False
        self._root: tk.Tk | None = None
        self._page = "dictation"
        self._settings_return_page = "dictation"
        self._history_page: HistoryPage | None = None
        self._history_entries: tuple[Any, ...] = ()
        self._history_lock = Lock()
        self._nav_buttons: dict[str, Any] = {}
        self._progress_running = False

    def publish(self, state: Any, message: str) -> None:
        """May be called from recording, processing or hotkey threads."""
        if not self._quitting:
            self._events.put(("state", getattr(state, "value", str(state)).lower(), message))

    def publish_sound(self, cue: str) -> None:
        """Queue an actual capture/copy event independently of status redraws."""
        if not self._quitting:
            self._events.put(("sound", cue, ""))

    def publish_history(self, entries: Any) -> None:
        """Queue immutable session snapshots without exposing them in status or tray."""
        with self._history_lock:
            if not self._quitting:
                self._events.put(("history", tuple(entries)[:30], ""))

    @property
    def root(self) -> tk.Tk | None:
        """Available once run starts; callers must use it on the Tk thread."""
        return self._root

    def run(self, *, auto_close_seconds: float | None = None) -> None:
        self._root = tk.Tk()
        self._root.title("Voice to Me")
        height = min(720, self._root.winfo_screenheight() - 100)
        self._root.geometry(f"640x{max(500, height)}")
        self._root.minsize(520, 500)
        self._root.configure(bg=COLORS["background"])
        self._root.option_add("*Font", "{Segoe UI} 11")
        if (ASSETS / "voiceui.ico").exists():
            self._root.iconbitmap(str(ASSETS / "voiceui.ico"))
        self._root.protocol("WM_DELETE_WINDOW", self._quit)
        self._root.bind("<Alt-r>", lambda _event: self._toggle())
        self._root.bind("<Escape>", lambda _event: self._cancel())
        self._root.bind("<Control-s>", lambda _event: self._save_settings())
        self._root.bind("<Alt-d>", lambda _event: self._navigate("dictation"))
        self._root.bind("<Alt-h>", lambda _event: self._navigate("history"))
        self._root.bind("<Alt-s>", lambda _event: self._navigate("settings"))
        self._build_window()
        history_observer = getattr(self.controller, "set_history_observer", None)
        if callable(history_observer):
            history_observer(self.publish_history)
        self._start_tray()
        self._render_state("ready", "")
        self._root.after(80, self._drain_events)
        if auto_close_seconds is not None:
            self._root.after(max(1, int(auto_close_seconds * 1000)), self._quit)
        self._root.mainloop()

    def _build_window(self) -> None:
        assert self._root is not None
        style = ttk.Style(self._root)
        style.configure("Voice.Horizontal.TProgressbar", background=COLORS["teal"],
                        troughcolor=COLORS["line"], thickness=4, borderwidth=0)
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=29)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", COLORS["teal"])],
                  foreground=[("selected", "#FFFFFF")])
        style.configure("TButton", font=("Segoe UI", 10), padding=(11, 8))
        header = tk.Frame(self._root, bg=COLORS["pine"], padx=24, pady=15)
        header.pack(fill="x")
        brand = tk.Frame(header, bg=COLORS["pine"])
        brand.pack(fill="x")
        icon = tk.Canvas(brand, width=55, height=55, bg=COLORS["pine"], highlightthickness=0)
        icon.pack(side="left", padx=(0, 14))
        # The same waveform/speech symbol as the packaged vector mark.
        icon.create_oval(3, 3, 51, 51, fill=COLORS["coral"], outline="")
        for x, height in ((15, 12), (23, 25), (31, 34), (39, 18)):
            icon.create_line(
                x,
                27 - height / 2,
                x,
                27 + height / 2,
                fill=COLORS["pine"],
                width=4,
                capstyle="round",
            )
        icon.create_polygon(37, 41, 46, 48, 45, 35, fill=COLORS["coral"], outline="")
        titles = tk.Frame(brand, bg=COLORS["pine"])
        titles.pack(side="left")
        tk.Label(
            titles,
            text="Voice to Me",
            fg="#FFFFFF",
            bg=COLORS["pine"],
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            titles,
            text="YOUR VOICE. YOUR WORDS.",
            fg="#CBDEDD",
            bg=COLORS["pine"],
            font=("Segoe UI", 8),
        ).pack(anchor="w")

        navigation = tk.Frame(self._root, bg=COLORS["background"], padx=24, pady=12)
        navigation.pack(fill="x")
        for page, label in (("dictation", "Dictation"), ("history", "History"),
                            ("settings", "Settings")):
            button = self._secondary_button(navigation, label, lambda target=page: self._navigate(target))
            button.pack(side="left", fill="x", expand=True, padx=(0 if page == "dictation" else 6, 0))
            self._nav_buttons[page] = button

        self._page_host = tk.Frame(self._root, bg=COLORS["background"])
        self._page_host.pack(fill="both", expand=True)
        footer = tk.Frame(self._root, bg=COLORS["background"], padx=24, pady=10)
        footer.pack(fill="x", side="bottom")
        ttk.Button(footer, text="Minimize", command=self._hide_window).pack(side="left")
        ttk.Button(footer, text="Quit", command=self._quit).pack(side="right")
        self._main_page = tk.Frame(self._page_host, bg=COLORS["background"])
        self._main_page.pack(fill="both", expand=True)
        body = tk.Frame(self._main_page, bg=COLORS["background"])
        canvas = tk.Canvas(body, bg=COLORS["background"], highlightthickness=0)
        scrollbar = tk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(canvas, bg=COLORS["background"], padx=24, pady=8)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind(
            "<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<Configure>", lambda event: canvas.itemconfigure(content_window, width=event.width)
        )
        self._root.bind("<MouseWheel>", self._mouse_wheel)
        self._main_canvas = canvas
        tk.Label(
            content,
            text="Dictation",
            font=("Segoe UI", 18, "bold"),
            bg=COLORS["background"],
            fg=COLORS["ink"],
        ).pack(anchor="w")
        tk.Label(
            content,
            text="Speak naturally. Your text is copied when ready.",
            bg=COLORS["background"],
            fg=COLORS["muted"],
        ).pack(anchor="w", pady=(5, 16))

        status_card = tk.Frame(
            content,
            bg=COLORS["surface"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            padx=20,
            pady=20,
        )
        status_card.pack(fill="x")
        row = tk.Frame(status_card, bg=COLORS["surface"])
        row.pack(fill="x")
        self._status_symbol = tk.Label(
            row, width=3, font=("Segoe UI Symbol", 20), bg=COLORS["surface"], fg=COLORS["teal"]
        )
        self._status_symbol.pack(side="left", padx=(0, 12))
        self._status_title = tk.Label(
            row,
            text="",
            font=("Segoe UI", 16, "bold"),
            bg=COLORS["surface"],
            fg=COLORS["ink"],
            anchor="w",
        )
        self._status_title.pack(side="left", fill="x", expand=True)
        self._status_detail = tk.Label(
            status_card,
            text="",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            justify="left",
            anchor="w",
            wraplength=450,
        )
        self._status_detail.pack(fill="x", pady=(13, 0))
        self._progress = ttk.Progressbar(
            status_card, mode="indeterminate", style="Voice.Horizontal.TProgressbar",
        )

        self._record_button = tk.Button(
            content,
            text="Start recording",
            command=self._toggle,
            font=("Segoe UI", 15, "bold"),
            bg=COLORS["coral"],
            activebackground="#F7A58D",
            fg=COLORS["pine"],
            activeforeground=COLORS["pine"],
            disabledforeground="#617572",
            relief="flat",
            bd=0,
            pady=14,
            cursor="hand2",
            takefocus=True,
            highlightthickness=2,
            highlightcolor=COLORS["pine"],
            highlightbackground=COLORS["background"],
        )
        self._record_button.pack(fill="x", pady=(20, 6))
        self._hotkey_hint = tk.Label(
            content,
            text=self._shortcut_hint(),
            bg=COLORS["background"],
            fg=COLORS["muted"],
            wraplength=490,
        )
        self._hotkey_hint.pack(pady=(0, 12))

        actions = tk.Frame(content, bg=COLORS["background"])
        actions.pack(fill="x")
        self._cancel_button = self._secondary_button(actions, "Cancel", self._cancel)
        self._cancel_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._retry_button = self._secondary_button(actions, "Try again", self._retry)
        self._retry_button.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self._inline_notice = tk.Label(
            content,
            text="",
            fg=COLORS["error"],
            bg=COLORS["background"],
            wraplength=470,
            justify="left",
            anchor="w",
        )
        self._inline_notice.pack(fill="x", pady=(6, 0))
        tk.Label(
            content,
            text="Choose where to paste and send.\nFind your recent messages in History.",
            bg=COLORS["background"],
            fg=COLORS["muted"],
            justify="left",
            anchor="w",
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=(17, 8))
        body.pack(fill="both", expand=True)
        self._history_page = HistoryPage(
            self._page_host, colors=COLORS, on_copy=self._copy_history, on_clear=self._clear_history,
        )
        self._update_navigation()

        def keep_focus_visible(event: tk.Event) -> None:
            widget = event.widget
            visible_top = canvas.winfo_rooty()
            widget_top = widget.winfo_rooty()
            widget_bottom = widget_top + widget.winfo_height()
            visible_bottom = visible_top + canvas.winfo_height()
            if widget_top < visible_top or widget_bottom > visible_bottom:
                relative_top = widget_top - content.winfo_rooty()
                if widget_bottom > visible_bottom:
                    relative_top += widget.winfo_height() - canvas.winfo_height() + 12
                canvas.yview_moveto(max(0, relative_top - 12) / max(1, content.winfo_height()))

        pending = [content]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, tk.Button):
                widget.bind("<FocusIn>", keep_focus_visible)

    @staticmethod
    def _secondary_button(parent: tk.Widget, text: str, command: Any) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=COLORS["surface"],
            activebackground="#E4EEEB",
            fg=COLORS["ink"],
            disabledforeground="#91A4A0",
            relief="flat",
            bd=0,
            pady=11,
            cursor="hand2",
            takefocus=True,
            highlightthickness=1,
            highlightcolor=COLORS["pine"],
            highlightbackground=COLORS["line"],
        )

    def _render_state(self, state: str, message: str) -> None:
        if self._quitting:
            return
        previous_state = self._state
        self._state = state
        self._last_message = message
        title, default_detail, color_key, symbol = STATE_PRESENTATION.get(
            state, STATE_PRESENTATION["error"]
        )
        self._status_symbol.configure(text=symbol, fg=COLORS[color_key])
        self._status_title.configure(text=title)
        # Status/errors remain separate from the session's read-only history.
        # Tray tooltips and notifications never contain message text.
        self._status_detail.configure(text=message or default_detail)
        busy = state in PROCESSING_STATES
        self._record_button.configure(
            text="Finish recording"
            if state == "recording"
            else {
                "preparing": "Loading speech model…",
                "loading_model": "Loading speech model…",
                "transcribing": "Transcribing…",
                "refining": "Refining your writing…",
            }[state]
            if busy
            else "Start recording",
            state="disabled" if busy or self._settings_open() else "normal",
        )
        self._cancel_button.configure(
            state="normal"
            if state in {"recording", "preparing", "loading_model", "transcribing", "refining"}
            else "disabled"
        )
        self._retry_button.configure(
            state="normal" if state == "error" and not self._settings_open() else "disabled"
        )
        if hasattr(self, "_progress"):
            if busy and not self._progress_running:
                self._progress.pack(fill="x", pady=(14, 0))
                self._progress.start(15)
                self._progress_running = True
            elif not busy and self._progress_running:
                self._progress.stop()
                self._progress.pack_forget()
                self._progress_running = False
        self._update_navigation()
        self._refresh_history_actions()
        if self._tray is not None:
            try:
                self._tray.title = f"Voice to Me · {title}"
                if state in self._tray_images:
                    self._tray.icon = self._tray_images[state]
            except (OSError, RuntimeError):
                self._tray_error = "The tray icon could not be updated."
            try:
                self._tray.update_menu()
            except (OSError, RuntimeError):
                self._tray_error = "The tray menu could not be updated."
            if state != previous_state and state in NOTIFICATIONS:
                try:
                    self._tray.notify(NOTIFICATIONS[state], f"Voice to Me · {title}")
                except (OSError, RuntimeError):
                    self._tray_error = "Windows notifications are unavailable."

    def _drain_events(self) -> None:
        if self._quitting or self._root is None:
            return
        for _ in range(100):
            try:
                kind, value, message = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "state":
                self._render_state(value, message)
            elif kind == "sound":
                play = {
                    "recording_started": self._sounds.recording_started,
                    "recording_stopped": self._sounds.recording_stopped,
                    "ready": self._sounds.ready,
                }.get(value)
                if play is not None:
                    play()
            elif kind == "history":
                self._history_entries = value
                if self._history_page is not None:
                    self._history_page.render(value)
                self._refresh_history_actions()
            elif kind == "command":
                {
                    "show": self._show_window,
                    "toggle": self._toggle,
                    "cancel": self._cancel,
                    "settings": self._show_settings,
                    "history": self._show_history,
                    "quit": self._quit,
                }[value]()
                if self._quitting:
                    return
            elif kind == "tray_error":
                self._tray_error = str(value)
        if not self._quitting:
            capture_lock = self._settings_open()
            if capture_lock != self._capture_action_lock:
                self._capture_action_lock = capture_lock
                self._render_state(self._state, self._last_message)
            self._update_navigation()
            self._refresh_history_actions()
            self._root.after(80, self._drain_events)

    def _capture_active(self) -> bool:
        return bool(self._settings_dialog is not None and self._settings_dialog.capturing)

    def _settings_open(self) -> bool:
        return bool(self._settings_dialog is not None and self._settings_dialog.is_open)

    def _toggle(self) -> None:
        if not self._settings_open():
            self.controller.toggle()

    def _cancel(self) -> None:
        if self._settings_open():
            self._settings_dialog._escape()
        else:
            self.controller.cancel()

    def _retry(self) -> None:
        if not self._settings_open():
            self.controller.retry()

    def _history_available(self) -> bool:
        busy = getattr(self.controller, "is_busy", False)
        return (not self._quitting and not self._settings_open()
                and self._state not in PROCESSING_STATES | {"recording"}
                and busy is not True)

    def _refresh_history_actions(self) -> None:
        if self._history_page is not None:
            self._history_page.set_available(self._history_available())

    def _update_navigation(self) -> None:
        for name, button in self._nav_buttons.items():
            enabled = not self._quitting and not self._capture_active()
            if name == "settings" and not self._settings_open():
                enabled = enabled and self._state not in PROCESSING_STATES | {"recording"}
            selected = name == self._page
            button.configure(
                state="normal" if enabled else "disabled",
                bg=COLORS["pine"] if selected else COLORS["surface"],
                fg="#FFFFFF" if selected else COLORS["ink"],
                activebackground=COLORS["pine"] if selected else "#E4EEEB",
                activeforeground="#FFFFFF" if selected else COLORS["ink"],
            )

    def _show_page(self, page: str) -> None:
        self._main_page.pack_forget()
        if self._history_page is not None:
            self._history_page.frame.pack_forget()
        if page == "history" and self._history_page is not None:
            self._history_page.frame.pack(fill="both", expand=True)
        else:
            page = "dictation"
            self._main_page.pack(fill="both", expand=True)
        self._page = page
        self._update_navigation()
        self._refresh_history_actions()

    def _navigate(self, page: str) -> None:
        if self._quitting or self._capture_active():
            return
        if page == "settings":
            if self._state in PROCESSING_STATES | {"recording"}:
                return
            self._open_settings()
        elif page in {"dictation", "history"}:
            if self._settings_open() and not self._settings_dialog.close():
                return
            self._show_page(page)

    def _show_history(self) -> None:
        self._show_window()
        self._navigate("history")

    def _show_settings(self) -> None:
        self._show_window()
        self._navigate("settings")

    def _copy_history(self, entry_id: int) -> None:
        if self._history_page is None or not self._history_available():
            return
        try:
            copied = getattr(self.controller, "copy_history", lambda _id: False)(entry_id)
        except ConfigurationError as exc:
            self._history_page.set_notice(str(exc), error=True)
            return
        self._history_page.set_notice(
            "Copied to clipboard. Paste whenever you're ready." if copied
            else "This message is unavailable. Wait until the current task finishes.",
            error=not copied,
        )

    def _clear_history(self) -> None:
        if self._history_page is None or not self._history_available():
            return
        cleared = getattr(self.controller, "clear_history", lambda: False)()
        if not cleared:
            self._history_page.set_notice("Wait until the current task finishes before clearing history.",
                                          error=True)

    def _open_settings(self) -> None:
        if self.settings_service is None:
            if self._page == "history" and self._history_page is not None:
                self._history_page.set_notice("Settings are unavailable in this session.", error=True)
            else:
                self._inline_notice.configure(text="Settings are unavailable in this session.")
            return
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(
                self._page_host, self.settings_service, self._settings_applied, self._close_settings
            )
            self._settings_dialog.capture_exclusion_widgets = tuple(self._nav_buttons.values())
        if self._settings_dialog.show():
            self._inline_notice.configure(text="")
            if self._page != "settings":
                self._settings_return_page = self._page
            self._main_page.pack_forget()
            if self._history_page is not None:
                self._history_page.frame.pack_forget()
            self._settings_dialog.frame.pack(fill="both", expand=True)
            self._page = "settings"
            self._update_navigation()
            self._refresh_history_actions()
        else:
            if self._page == "history" and self._history_page is not None:
                self._history_page.set_notice(
                    "Settings could not open. Finish or cancel the current task and try again.",
                    error=True,
                )
            else:
                self._inline_notice.configure(text=self._settings_dialog.error_message)

    def _close_settings(self) -> None:
        if not self._quitting:
            self._show_page(self._settings_return_page)
            self._render_state(self._state, self._last_message)

    def _save_settings(self) -> None:
        if self._settings_open():
            self._settings_dialog._save()

    def _scroll_page(self, delta: int) -> None:
        if self._settings_open():
            self._settings_dialog.scroll(delta)
        elif self._page == "history" and self._history_page is not None:
            self._history_page.scroll(delta)
        else:
            self._main_canvas.yview_scroll(delta, "units")

    def _mouse_wheel(self, event: Any) -> None:
        if isinstance(event.widget, (tk.Text, ttk.Treeview)):
            return  # These widgets already scroll through their class binding.
        self._scroll_page(-int(event.delta / 120))

    def _settings_applied(self, settings: Any) -> None:
        self.config_path = settings.config_path
        self.profile_path = settings.profile_path
        self.hotkey_label = describe_shortcuts(settings.hotkeys)
        self.paste_hotkey_label = describe_paste_shortcut(settings.hotkeys)
        if hasattr(self, "_hotkey_hint"):
            self._hotkey_hint.configure(
                text=self._shortcut_hint()
            )

    def _shortcut_hint(self) -> str:
        hint = f"Record: {self.hotkey_label}  ·  in this window: Alt + R"
        if self.paste_hotkey_label and self.paste_hotkey_label != "Not set":
            hint += f"\nPaste: {self.paste_hotkey_label}"
        return hint

    def _show_window(self) -> None:
        assert self._root is not None
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def _hide_window(self) -> None:
        assert self._root is not None
        self._root.iconify()

    def _start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image

            def enqueue(command: str) -> Any:
                return lambda _icon, _item: self._events.put(("command", command, ""))

            def ready(icon: Any) -> None:
                if self._quitting:
                    # A stop before pystray enters its loop is a no-op. Stop
                    # again when delayed setup completes so Quit cannot leave
                    # an invisible backend thread alive.
                    icon.stop()
                else:
                    icon.visible = True

            with Image.open(ASSETS / "voiceui.png") as source:
                tray_image = source.copy()
            for state in STATE_PRESENTATION:
                icon_state = "transcribing" if state in {"preparing", "loading_model"} else state
                with Image.open(ASSETS / f"voiceui-{icon_state}.png") as source:
                    self._tray_images[state] = source.copy()
            self._tray = pystray.Icon(
                "voice-to-me",
                tray_image,
                "Voice to Me · Ready to dictate",
                menu=pystray.Menu(
                    pystray.MenuItem("Open Voice to Me", enqueue("show"), default=True),
                    pystray.MenuItem(
                        lambda _item: "Finish recording" if self._state == "recording"
                        else "Start recording", enqueue("toggle"),
                        enabled=lambda _item: not self._quitting and not self._settings_open()
                        and self._state not in PROCESSING_STATES,
                    ),
                    pystray.MenuItem(
                        "Cancel", enqueue("cancel"),
                        enabled=lambda _item: not self._quitting and not self._settings_open()
                        and self._state in PROCESSING_STATES | {"recording"},
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("History", enqueue("history"),
                                     enabled=lambda _item: not self._quitting and not self._capture_active()),
                    pystray.MenuItem(
                        "Settings", enqueue("settings"),
                        enabled=lambda _item: not self._quitting and not self._capture_active()
                        and self._state not in PROCESSING_STATES | {"recording"},
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Quit", enqueue("quit")),
                ),
            )
            self._tray.run_detached(setup=ready)
        except (ImportError, OSError, RuntimeError):
            self._tray = None
            self._tray_error = "The system tray is unavailable. Use the window to control the app."

    def _quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        with self._history_lock:
            self._history_entries = ()
            while True:
                try:
                    self._events.get_nowait()
                except queue.Empty:
                    break
        if self._history_page is not None:
            try:
                self._history_page.clear_memory()
            except (RuntimeError, tk.TclError):
                pass
            self._history_page = None
        tray, self._tray = self._tray, None
        try:
            self._sounds.close()
        finally:
            try:
                if tray is not None:
                    try:
                        tray.remove_notification()
                    finally:
                        try:
                            tray.visible = False
                        finally:
                            tray.stop()
            finally:
                try:
                    self._shutdown()
                finally:
                    try:
                        if self._settings_dialog is not None:
                            self._settings_dialog.close(force=True)
                    finally:
                        with self._history_lock:
                            while True:
                                try:
                                    self._events.get_nowait()
                                except queue.Empty:
                                    break
                        if self._root is not None:
                            self._root.destroy()
