"""An embedded Settings page inside the application's existing Tk window."""

from __future__ import annotations

import queue
import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from threading import Thread
from tkinter import ttk
from typing import Any

from .config import MOUSE_BUTTONS, AppSettings, HotkeySettings


def describe_keyboard(value: str) -> str:
    aliases = {
        "ctrl": "Ctrl",
        "alt": "Alt",
        "shift": "Shift",
        "cmd": "Win",
        "space": "Space",
        "enter": "Enter",
        "esc": "Escape",
    }
    return (
        " + ".join(
            aliases.get(part.strip("<>"), part.strip("<>").upper()) for part in value.split("+")
        )
        if value
        else "Not set"
    )


def describe_mouse(value: str) -> str:
    return {
        "": "Not set",
        "left": "Left mouse button",
        "right": "Right mouse button",
        "middle": "Middle mouse button",
        "x1": "Mouse X1",
        "x2": "Mouse X2",
    }.get(value, value)


def describe_shortcuts(settings: HotkeySettings) -> str:
    choices = []
    if settings.keyboard:
        choices.append(describe_keyboard(settings.keyboard))
    if settings.mouse_button:
        choices.append(describe_mouse(settings.mouse_button))
    return " or ".join(choices) if choices else "Record button only"


def describe_paste_shortcut(settings: HotkeySettings) -> str:
    if not settings.paste_keyboard and not settings.paste_mouse_button:
        return "Not set"
    return describe_shortcuts(HotkeySettings(settings.paste_keyboard, settings.paste_mouse_button))


def input_device_options(query_devices: Callable[[], Any] | None = None) -> list[tuple[str, Any]]:
    """List input devices without opening a stream or recording."""
    if query_devices is None:
        import sounddevice

        try:
            devices = sounddevice.query_devices()
        except sounddevice.PortAudioError:
            raise RuntimeError("Windows could not list audio inputs.") from None
    else:
        devices = query_devices()
    return [("Default input", None)] + [
        (f"{device['name']} (device {index})", index)
        for index, device in enumerate(devices)
        if device.get("max_input_channels", 0) > 0
    ]


class SettingsDialog:
    """Compatibility name for an embedded Frame, never a secondary window.

    The app packs ``frame`` in its page host after ``show`` succeeds. Normal
    shortcuts stay paused until Save & Apply or Back/Cancel leaves the page.
    """

    def __init__(
        self,
        parent: tk.Misc,
        service: Any,
        on_applied: Callable[[AppSettings], None],
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.parent, self.service, self.on_applied = parent, service, on_applied
        self.on_close = on_close or (lambda: None)
        self._frame: ttk.Frame | None = None
        self._draft: Any = None
        self._events: queue.Queue[tuple[str, int, Any]] = queue.Queue()
        self._vars: dict[str, tk.Variable] = {}
        self._buttons: dict[str, Any] = {}
        self._device_lookup: dict[str, Any] = {}
        self._profile_lookup: dict[str, str] = {}
        self._downloaded_models: dict[str, str] = {}
        self._keyboard = self._mouse = ""
        self._paste_keyboard = self._paste_mouse = ""
        self._capture_target = "recording"
        self._capture: Any = None
        self._capturing = self._suspended = self._downloading = False
        self._capture_token = self._generation = 0
        self._capture_start_job: Any = None
        self._poll_job: Any = None
        self._closed = True
        self._codex_ready: bool | None = None
        self._codex_requested = True
        self._codex_message = ""
        self.error_message = ""

    @property
    def frame(self) -> ttk.Frame | None:
        return self._frame

    @property
    def window(self) -> ttk.Frame | None:
        """Compatibility alias; this is the embedded Frame."""
        return self._frame

    @property
    def capturing(self) -> bool:
        return self._capturing

    @property
    def is_open(self) -> bool:
        return not self._closed and self._frame is not None

    def show(self) -> bool:
        if self.is_open:
            return True
        try:
            draft = self.service.read_draft()
            self.service.suspend_shortcuts()
        except (OSError, ValueError, RuntimeError) as exc:
            self.error_message = str(exc)
            return False
        self._draft = draft
        self._suspended, self._closed = True, False
        self._generation += 1
        self._downloading = False
        self._codex_ready = None
        self._codex_requested = draft.settings.codex.enabled
        self._codex_message = ""
        self._keyboard, self._mouse = (
            draft.settings.hotkeys.keyboard,
            draft.settings.hotkeys.mouse_button,
        )
        self._paste_keyboard, self._paste_mouse = (
            draft.settings.hotkeys.paste_keyboard,
            draft.settings.hotkeys.paste_mouse_button,
        )
        self.error_message = ""
        try:
            self._frame = ttk.Frame(self.parent, padding=(22, 14))
            self._build_window()
            self._poll_job = self._frame.after(70, self._poll_events)
            self._start_backend_query()
            self._start_codex_query()
        except (OSError, ValueError, RuntimeError, ImportError, tk.TclError) as exc:
            self.error_message = f"Could not open Settings: {exc}"
            self.close()
            return False
        return True

    def _build_window(self) -> None:
        assert self._frame is not None
        from .performance import PROFILE_LABELS, profile_name

        frame = self._frame
        style = ttk.Style(frame)
        style.configure("VoiceSettings.TButton", padding=(10, 8), font=("Segoe UI", 10))
        self._buttons = {}
        top = ttk.Frame(frame)
        top.pack(fill="x", pady=(0, 12))
        self._buttons["back"] = ttk.Button(
            top, text="← Back", command=self.close, style="VoiceSettings.TButton"
        )
        self._buttons["back"].pack(side="left")
        ttk.Label(top, text="Settings", font=("Segoe UI", 18, "bold")).pack(side="left", padx=16)
        footer = ttk.Frame(frame)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        self._buttons["save"] = ttk.Button(
            footer, text="Save & Apply", command=self._save, style="VoiceSettings.TButton"
        )
        self._buttons["save"].pack(side="right", padx=(10, 0))
        self._buttons["cancel"] = ttk.Button(
            footer, text="Cancel", command=self.close, style="VoiceSettings.TButton"
        )
        self._buttons["cancel"].pack(side="right")
        self._status_label = ttk.Label(
            frame,
            text="Recording and paste shortcuts are paused while you edit.",
            foreground="#49646A",
            wraplength=490,
            justify="left",
        )
        self._status_label.pack(side="bottom", fill="x", pady=(10, 0))

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, highlightthickness=0, bg="#F3F7F6")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        content = ttk.Frame(canvas, padding=(0, 0, 12, 10))
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind(
            "<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<Configure>", lambda event: canvas.itemconfigure(content_window, width=event.width)
        )
        self._scroll_canvas, self._vars = canvas, {}

        shortcut = ttk.LabelFrame(content, text="Recording shortcut", padding=12)
        shortcut.pack(fill="x", pady=(0, 14))
        self._variable("shortcut", describe_shortcuts(HotkeySettings(self._keyboard, self._mouse)))
        ttk.Label(
            shortcut, textvariable=self._vars["shortcut"], font=("Segoe UI", 12, "bold")
        ).pack(anchor="w", pady=(0, 10))
        actions = ttk.Frame(shortcut)
        actions.pack(fill="x")
        self._buttons["capture"] = ttk.Button(
            actions,
            text="Capture shortcut",
            command=self._start_capture,
            style="VoiceSettings.TButton",
        )
        self._buttons["capture"].pack(side="left", padx=(0, 8))
        self._buttons["clear"] = ttk.Button(
            actions, text="Clear", command=self._clear_shortcut, style="VoiceSettings.TButton"
        )
        self._buttons["clear"].pack(side="left")
        self._buttons["stop_capture"] = ttk.Button(
            actions,
            text="Stop capture",
            command=self.cancel_capture,
            state="disabled",
            style="VoiceSettings.TButton",
        )
        self._buttons["stop_capture"].pack(side="right")
        ttk.Label(
            shortcut,
            text="Press a key or combination, or click any mouse button. Escape cancels. "
            "Press your chosen shortcut once to record, again to finish.",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        paste = ttk.LabelFrame(content, text="Paste shortcut (Ctrl + V)", padding=12)
        paste.pack(fill="x", pady=(0, 14))
        self._variable("paste_shortcut", describe_paste_shortcut(HotkeySettings(
            paste_keyboard=self._paste_keyboard, paste_mouse_button=self._paste_mouse,
        )))
        ttk.Label(
            paste, textvariable=self._vars["paste_shortcut"], font=("Segoe UI", 12, "bold")
        ).pack(anchor="w", pady=(0, 10))
        paste_actions = ttk.Frame(paste)
        paste_actions.pack(fill="x")
        self._buttons["capture_paste"] = ttk.Button(
            paste_actions, text="Capture paste shortcut",
            command=lambda: self._start_capture("paste"), style="VoiceSettings.TButton",
        )
        self._buttons["capture_paste"].pack(side="left", padx=(0, 8))
        self._buttons["clear_paste"] = ttk.Button(
            paste_actions, text="Clear", command=lambda: self._clear_shortcut("paste"),
            style="VoiceSettings.TButton",
        )
        self._buttons["clear_paste"].pack(side="left")
        ttk.Label(
            paste, text="Choose a different key or mouse button. Release it in the app "
            "where you want to paste the clipboard text.", wraplength=440, justify="left",
        ).pack(anchor="w", pady=(10, 0))

        writing = ttk.LabelFrame(content, text="Writing style", padding=12)
        writing.pack(fill="x", pady=(0, 14))
        editor = ttk.Frame(writing)
        editor.pack(fill="both", expand=True)
        editor_scroll = ttk.Scrollbar(editor, orient="vertical")
        editor_scroll.pack(side="right", fill="y")
        self._profile_text = tk.Text(
            editor,
            wrap="word",
            undo=True,
            font=("Segoe UI", 11),
            relief="solid",
            bd=1,
            padx=10,
            pady=10,
            height=6,
            yscrollcommand=editor_scroll.set,
        )
        self._profile_text.pack(side="left", fill="both", expand=True)
        editor_scroll.configure(command=self._profile_text.yview)
        self._profile_text.insert("1.0", self._draft.profile_text)
        self._profile_text.bind("<Control-a>", self._select_profile_text)

        audio = ttk.LabelFrame(content, text="Audio & transcription", padding=12)
        audio.pack(fill="x", pady=(0, 14))
        audio.columnconfigure(1, weight=1)
        self._device_lookup = {"Default input": None}
        self._microphone_combo = self._field(
            audio, 0, "Microphone", "microphone", "Default input", choices=("Default input",)
        )
        self._field(
            audio, 1, "Dictation language", "language", self._draft.settings.whisper.language
        )
        self._profile_lookup = {label: name for name, label in PROFILE_LABELS.items()}
        selected = profile_name(self._draft.settings.whisper)
        if selected == "custom":
            self._profile_lookup["Current setup"] = "custom"
        label = next(label for label, name in self._profile_lookup.items() if name == selected)
        self._profile_combo = self._field(
            audio,
            2,
            "Transcription profile",
            "transcription_profile",
            label,
            choices=tuple(self._profile_lookup),
        )
        self._buttons["download"] = ttk.Button(
            audio,
            text="Download model",
            command=self._download_model,
            style="VoiceSettings.TButton",
        )
        self._buttons["download"].grid(row=3, column=1, sticky="w", pady=(4, 10))
        self._variable("backend", "Checking local transcription hardware…")
        ttk.Label(
            audio,
            textvariable=self._vars["backend"],
            foreground="#49646A",
            wraplength=440,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w")

        codex = ttk.LabelFrame(content, text="Codex", padding=12)
        codex.pack(fill="x")
        self._vars["codex_enabled"] = tk.BooleanVar(frame, value=False)
        self._buttons["codex_enabled"] = ttk.Checkbutton(
            codex, text="Refine with Codex CLI", variable=self._vars["codex_enabled"],
            command=self._codex_toggle, state="disabled",
        )
        self._buttons["codex_enabled"].grid(row=0, column=0, columnspan=2, sticky="w")
        self._variable("codex_status", "Checking Codex CLI…")
        ttk.Label(
            codex, textvariable=self._vars["codex_status"], foreground="#49646A",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 4))
        self._codex_model_entry = self._field(
            codex, 2, "Model (optional)", "codex_model", self._draft.settings.codex.model,
        )
        self._codex_model_entry.configure(state="disabled")
        self._variable("codex_hint", "Checking local installation and sign-in status.")
        ttk.Label(
            codex, textvariable=self._vars["codex_hint"], foreground="#49646A",
            wraplength=440, justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._refresh_microphones(self._draft.settings.audio.device)

        def focus_visible(event: Any) -> None:
            widget = event.widget
            top, y = canvas.winfo_rooty(), widget.winfo_rooty()
            bottom = top + canvas.winfo_height()
            if y < top or y + widget.winfo_height() > bottom:
                offset = y - content.winfo_rooty()
                if y >= top:
                    offset += widget.winfo_height() - canvas.winfo_height() + 12
                canvas.yview_moveto(max(0, offset - 12) / max(1, content.winfo_height()))

        pending = [content]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Entry, ttk.Combobox, tk.Text)):
                widget.bind("<FocusIn>", focus_visible, add="+")

    def _variable(self, name: str, value: Any) -> tk.StringVar:
        self._vars[name] = tk.StringVar(self._frame, value=str(value))
        return self._vars[name]

    def _field(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        name: str,
        value: Any,
        *,
        choices: tuple[str, ...] | None = None,
    ) -> Any:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=7)
        variable = self._variable(name, value)
        field = (
            ttk.Entry(parent, textvariable=variable)
            if choices is None
            else ttk.Combobox(parent, textvariable=variable, values=choices, state="readonly")
        )
        field.grid(row=row, column=1, sticky="ew", pady=7)
        parent.columnconfigure(1, weight=1)
        return field

    def _select_profile_text(self, _event: Any) -> str:
        self._profile_text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def scroll(self, delta: int) -> None:
        if self.is_open and hasattr(self, "_scroll_canvas"):
            self._scroll_canvas.yview_scroll(delta, "units")

    def _set_status(self, message: str, *, error: bool = False) -> None:
        if not self._closed:
            try:
                self._status_label.configure(
                    text=message, foreground="#B3383A" if error else "#49646A"
                )
            except tk.TclError:
                return

    def _refresh_microphones(self, device: Any = None) -> None:
        try:
            choices = input_device_options()
        except (OSError, ValueError, RuntimeError) as exc:
            choices = [("Default input", None)]
            self._set_status(f"Could not list microphones: {exc}", error=True)
        if device is not None and device not in [value for _label, value in choices]:
            choices.append((f"{device} (configured input)", device))
        self._device_lookup = dict(choices)
        self._microphone_combo.configure(values=list(self._device_lookup))
        self._vars["microphone"].set(next(label for label, value in choices if value == device))

    def _update_controls(self) -> None:
        disabled = self._capturing or self._capture is not None or self._downloading
        for name in ("capture", "clear", "capture_paste", "clear_paste", "save", "download"):
            if name in self._buttons:
                self._buttons[name].configure(state="disabled" if disabled else "normal")
        if "stop_capture" in self._buttons:
            self._buttons["stop_capture"].configure(
                state="normal" if self._capturing or self._capture is not None else "disabled"
            )
        self._update_codex_controls()

    def _update_codex_controls(self) -> None:
        if "codex_enabled" not in self._vars:
            return
        busy = self._capturing or self._capture is not None or self._downloading
        ready = self._codex_ready is True
        if not ready:
            self._vars["codex_enabled"].set(False)
        if "codex_enabled" in self._buttons:
            self._buttons["codex_enabled"].configure(
                state="normal" if ready and not busy else "disabled",
            )
        if hasattr(self, "_codex_model_entry"):
            self._codex_model_entry.configure(
                state="normal" if ready and self._vars["codex_enabled"].get() and not busy
                else "disabled",
            )
        if ready:
            self._vars["codex_hint"].set(
                "Uses your writing style. Leave the model blank to use the default."
                if self._vars["codex_enabled"].get()
                else "Local transcription only. Your writing style and model are kept for later."
            )

    def _codex_toggle(self) -> None:
        self._update_codex_controls()

    def _clear_shortcut(self, target: str = "recording") -> None:
        if self._capturing or self._capture is not None:
            return
        if target == "paste":
            self._paste_keyboard = self._paste_mouse = ""
            self._vars["paste_shortcut"].set("Not set")
            self._set_status("Paste shortcut cleared. Save & Apply to disable the paste macro.")
        else:
            self._keyboard = self._mouse = ""
            self._vars["shortcut"].set("Record button only")
            self._set_status("Shortcut cleared. Save & Apply to use only the Record button.")

    def _make_capture(self, token: int) -> Any:
        from .hotkey_capture import ShortcutCapture

        excluded = []
        for name in ("stop_capture", "cancel", "back", "capture", "clear",
                     "capture_paste", "clear_paste"):
            button = self._buttons.get(name)
            if button is None:
                continue
            try:
                x, y = int(button.winfo_rootx()), int(button.winfo_rooty())
                width, height = int(button.winfo_width()), int(button.winfo_height())
            except (TypeError, ValueError, tk.TclError):
                continue
            excluded.append((x, y, x + width, y + height))
        bounds = tuple(excluded)
        return ShortcutCapture(
            on_capture=lambda result: self._events.put(("capture", token, result)),
            on_error=lambda error: self._events.put(("capture_error", token, error)),
            timeout_seconds=10,
            mouse_filter=lambda x, y: (
                not any(
                    left <= x < right and top <= y < bottom for left, top, right, bottom in bounds
                )
            ),
        )

    def _start_capture(self, target: str = "recording") -> None:
        if target not in {"recording", "paste"}:
            return
        if self._closed or self._capturing or self._downloading or self._frame is None:
            return
        if self._capture is not None:
            self._set_status(
                "The previous capture could not stop. Use Stop capture to try again.", error=True
            )
            return
        try:
            self._capture_target = target
            self._capturing = True
            self._capture_token += 1
            token = self._capture_token
            self._frame.focus_set()
            self._set_status(f"Choose your {target} shortcut: press a key or any mouse button. "
                             "Escape cancels.")
            self._update_controls()
            self._capture_start_job = self._frame.after(150, lambda: self._launch_capture(token))
        except (OSError, ValueError, RuntimeError, tk.TclError) as exc:
            self._finish_capture()
            self._set_status(f"Could not start shortcut capture: {exc}", error=True)

    def _launch_capture(self, token: int) -> None:
        self._capture_start_job = None
        if self._closed or not self._capturing or token != self._capture_token:
            return
        try:
            self._capture = self._make_capture(token)
            self._capture.start()
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            self._finish_capture()
            self._set_status(f"Could not start shortcut capture: {exc}", error=True)

    def _finish_capture(self) -> bool:
        self._capture_token += 1
        if self._capture_start_job is not None and self._frame is not None:
            try:
                self._frame.after_cancel(self._capture_start_job)
            except tk.TclError:
                self._capture_start_job = None
            self._capture_start_job = None
        capture, self._capture = self._capture, None
        stopped = True
        try:
            if capture is not None:
                capture.stop()
        except (OSError, RuntimeError):
            stopped, self._capture = False, capture
            self._set_status(
                "Capture could not stop. Use Stop capture or Back to try again.", error=True
            )
        finally:
            self._capturing = False
            if not self._closed:
                try:
                    self._update_controls()
                except tk.TclError:
                    self._buttons = {}
        return stopped

    def cancel_capture(self) -> None:
        if self._capturing or self._capture is not None:
            if self._finish_capture():
                self._set_status("Capture cancelled. Your saved shortcut is unchanged.")

    def _escape(self, _event: Any = None) -> str:
        if self._capturing:
            self.cancel_capture()
        else:
            self.close()
        return "break"

    def _selected_whisper(self) -> Any:
        from .performance import profile_to_settings

        name = self._profile_lookup.get(self._vars["transcription_profile"].get())
        if name is None:
            raise ValueError("Choose a transcription profile from the list.")
        settings = self._draft.settings.whisper
        return settings if name == "custom" else profile_to_settings(name, settings)

    def _start_backend_query(self) -> None:
        generation = self._generation

        def query() -> None:
            try:
                from .performance import detect_backend

                info = detect_backend()
                if info.device == "cuda" and info.runtime_ready is not False:
                    text = "GPU acceleration: Ready"
                elif info.cuda_available:
                    text = "GPU acceleration needs local runtime support."
                else:
                    text = "CPU transcription: Ready"
            except (OSError, ValueError, RuntimeError, ImportError):
                text = "Local transcription hardware could not be detected."
            self._events.put(("backend", generation, text))

        Thread(target=query, name="voice-to-me-hardware-status", daemon=True).start()

    def _start_codex_query(self) -> None:
        generation = self._generation
        settings = self._draft.settings.codex

        def query() -> None:
            try:
                from .codex_status import check_codex_readiness

                status = check_codex_readiness(settings)
                result = (bool(status.ready), str(status.message))
            except (OSError, ValueError, RuntimeError, ImportError):
                result = (False, "Install Codex CLI and run codex login, then reopen Settings.")
            self._events.put(("codex_status", generation, result))

        Thread(target=query, name="voice-to-me-codex-status", daemon=True).start()

    def _download_model(self) -> None:
        if self._closed or self._capturing or self._capture is not None or self._downloading:
            return
        try:
            model = self._selected_whisper().model
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            self._set_status(str(exc), error=True)
            return
        self._downloading = True
        self._update_controls()
        self._set_status("Downloading the selected speech model…")
        generation = self._generation

        def download() -> None:
            try:
                path = self.service.download_model(model)
            except (OSError, ValueError, RuntimeError) as exc:
                self._events.put(("download_error", generation, str(exc)))
            else:
                self._events.put(("download", generation, (model, str(path))))

        Thread(target=download, name="voice-to-me-model-download", daemon=True).start()

    def _poll_events(self) -> None:
        if self._closed or self._frame is None:
            return
        for _ in range(50):
            try:
                kind, token, value = self._events.get_nowait()
            except queue.Empty:
                break
            if kind.startswith("capture"):
                if not self._capturing or token != self._capture_token:
                    continue
                target = self._capture_target
                if not self._finish_capture():
                    continue
                if kind == "capture_error":
                    self._set_status(str(value), error=True)
                elif value.keyboard or value.mouse_button in MOUSE_BUTTONS:
                    keyboard, mouse = ((value.keyboard, "") if value.keyboard
                                       else ("", value.mouse_button))
                    label = describe_keyboard(keyboard) if keyboard else describe_mouse(mouse)
                    if target == "paste":
                        self._paste_keyboard, self._paste_mouse = keyboard, mouse
                        self._vars["paste_shortcut"].set(label)
                    else:
                        self._keyboard, self._mouse = keyboard, mouse
                        self._vars["shortcut"].set(label)
                    self._set_status(f"{target.capitalize()} shortcut captured. Save & Apply to use it.")
                else:
                    self._set_status(
                        "No usable shortcut was detected. Please try again.", error=True
                    )
            elif token == self._generation:
                if kind == "backend":
                    self._vars["backend"].set(str(value))
                elif kind == "codex_status":
                    self._codex_ready, self._codex_message = value
                    self._vars["codex_status"].set(
                        "Codex CLI: Ready" if self._codex_ready else "Codex CLI: Not configured",
                    )
                    self._vars["codex_enabled"].set(
                        bool(self._codex_ready and self._codex_requested),
                    )
                    self._vars["codex_hint"].set(self._codex_message)
                    self._update_codex_controls()
                elif kind.startswith("download"):
                    self._downloading = False
                    self._update_controls()
                    if kind == "download_error":
                        self._set_status(f"Could not download the model: {value}", error=True)
                    else:
                        model, path = value
                        self._downloaded_models[model] = path
                        self._set_status("Model downloaded. Save & Apply when ready.")
        if not self._closed:
            self._poll_job = self._frame.after(70, self._poll_events)

    def _gather_draft(self) -> Any:
        from .settings import SettingsDraft

        microphone = self._vars["microphone"].get()
        if microphone not in self._device_lookup:
            raise ValueError("Choose a microphone from the list.")
        whisper = self._selected_whisper()
        whisper = replace(
            whisper, language=self._vars["language"].get().strip(), local_files_only=True
        )
        if whisper.model in self._downloaded_models:
            whisper = replace(whisper, model_directory=self._downloaded_models[whisper.model])
        original = self._draft.settings
        settings = replace(
            original,
            hotkeys=HotkeySettings(self._keyboard, self._mouse,
                                  self._paste_keyboard, self._paste_mouse),
            audio=replace(original.audio, device=self._device_lookup[microphone]),
            whisper=whisper,
            codex=replace(
                original.codex, model=self._vars["codex_model"].get().strip(),
                enabled=bool(self._codex_ready and self._vars["codex_enabled"].get()),
            ),
        )
        return SettingsDraft(settings, self._profile_text.get("1.0", "end-1c"))

    def _save(self) -> None:
        if self._closed:
            return
        if self._capturing or self._capture is not None or self._downloading:
            self._set_status("Finish capture or the model download before saving.", error=True)
            return
        try:
            settings = self.service.save(self._gather_draft(), resume_after_save=True)
            self._suspended = False
            self.on_applied(settings)
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            try:
                self.service.suspend_shortcuts()
            except (OSError, ValueError, RuntimeError) as suspend_error:
                self._set_status(f"{exc} Could not pause shortcuts: {suspend_error}", error=True)
                return
            self._suspended = True
            self._set_status(str(exc), error=True)
            return
        self.close()

    def close(self, *, force: bool = False) -> bool:
        if self._closed:
            return True
        stopped = self._finish_capture()
        if not stopped and not force:
            return False
        if self._suspended and not force:
            try:
                self.service.resume_shortcuts()
            except (OSError, ValueError, RuntimeError) as exc:
                self._set_status(
                    f"Could not resume shortcuts: {exc} Press Back to try again.", error=True
                )
                return False
        self._suspended, self._closed = False, True
        self._generation += 1
        frame, self._frame = self._frame, None
        if frame is not None:
            if self._poll_job is not None:
                try:
                    frame.after_cancel(self._poll_job)
                except tk.TclError:
                    self._poll_job = None
            self._poll_job = None
            try:
                frame.destroy()
            except tk.TclError:
                self._frame = None
        if not force:
            self.on_close()
        return True
