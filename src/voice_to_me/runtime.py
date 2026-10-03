"""Live adapter updates and shortcut suspension for the in-app Settings editor."""

import threading
from dataclasses import replace

from .clipboard import WindowsClipboard
from .config import AppSettings, ConfigurationError
from .controller import AppController
from .hotkeys import HotkeyManager
from .mouse_guard import MouseShortcutGuard
from .paste import PasteMacro
from .recorder import AudioRecorder
from .refiner import CodexRefiner
from .settings import SettingsService
from .transcriber import WhisperTranscriber


class AppRuntime:
    def __init__(self, settings: AppSettings):
        self._closed = False
        self._started = False
        self._prepare_pending = True
        self._shortcuts_suspended = False
        self._lock = threading.RLock()
        self.controller = AppController(settings, AudioRecorder(settings.audio),
                                        WhisperTranscriber(settings.whisper),
                                        CodexRefiner(settings.codex) if settings.codex.enabled else None,
                                        WindowsClipboard())
        self.mouse_guard = MouseShortcutGuard(lambda: self.controller.settings.hotkeys)
        self.paste_mouse_guard = MouseShortcutGuard(lambda: replace(
            self.controller.settings.hotkeys,
            mouse_button=self.controller.settings.hotkeys.paste_mouse_button,
        ))
        self.paste_macro = PasteMacro()
        self.hotkeys = HotkeyManager(
            settings.hotkeys, self.controller.toggle, mouse_filter=self.mouse_guard,
            on_paste=self._paste, paste_mouse_filter=self.paste_mouse_guard,
        )
        self.settings_service = SettingsService(
            settings, apply=self._apply_settings, suspend=self._suspend_shortcuts,
            resume=self._resume_shortcuts, is_busy=lambda: self.controller.is_busy,
        )

    def _paste(self) -> None:
        with self._lock:
            if self._closed or self.controller._closed or self._shortcuts_suspended:
                return
            self.paste_macro.paste()

    def _suspend_shortcuts(self) -> None:
        with self._lock:
            if self._closed or self.controller._closed:
                raise ConfigurationError("The app is closing. Settings cannot be changed.")
            self.controller.suspend_input()
            self._shortcuts_suspended = True
            try:
                self.hotkeys.stop()
            except Exception:
                self.controller.resume_input()
                self._shortcuts_suspended = False
                raise

    def _resume_shortcuts(self) -> None:
        with self._lock:
            if self._closed or self.controller._closed:
                return
            self.hotkeys.start()
            if self._started and self._prepare_pending:
                self.controller.prepare()
                self._prepare_pending = False
            self.controller.resume_input()
            self._shortcuts_suspended = False

    def _apply_settings(self, settings: AppSettings) -> None:
        with self._lock:
            if self._closed or self.controller._closed:
                raise ConfigurationError("The app is closing. Settings cannot be changed.")
            current = self.controller.settings
            recorder = (self.controller.recorder if settings.audio == current.audio
                        else AudioRecorder(settings.audio))
            transcriber = (self.controller.transcriber if settings.whisper == current.whisper
                           else WhisperTranscriber(settings.whisper))
            refiner = (self.controller.refiner if settings.codex == current.codex
                       else CodexRefiner(settings.codex) if settings.codex.enabled else None)
            if settings.hotkeys != current.hotkeys:
                self.hotkeys.reconfigure(settings.hotkeys)
            self.controller.apply_settings(settings, recorder, transcriber, refiner)
            if settings.whisper != current.whisper:
                self._prepare_pending = True

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise ConfigurationError("The app is closed.")
            if self._started:
                return
            # Hooks may receive input as soon as they start. Suspend the controller
            # until the preparation worker is installed, eliminating a cold-start race.
            self.controller.suspend_input()
            self._shortcuts_suspended = True
            try:
                self.hotkeys.start()
                self._started = True
                self.controller.prepare()
                self._prepare_pending = False
            finally:
                self.controller.resume_input()
                self._shortcuts_suspended = False

    @property
    def has_active_worker(self) -> bool:
        return self.controller.has_active_worker

    def shutdown(self) -> bool:
        with self._lock:
            if self._closed:
                return not self.controller.is_busy
            self._closed = True
            self._shortcuts_suspended = True
            # Cancel paste before waiting for a native speech worker, and always
            # remove this process's input hooks after any cleanup failure.
            stopped = False
            try:
                self.paste_macro.close()
            finally:
                try:
                    stopped = self.controller.shutdown()
                finally:
                    self.hotkeys.stop()
            return stopped
