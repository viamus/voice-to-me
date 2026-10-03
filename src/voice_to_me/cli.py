"""Windows entry point and explicit, visible setup/diagnostic commands."""

from __future__ import annotations

import argparse
import ctypes
import importlib
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import ConfigurationError, load_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Voice to Me for Windows (Python 3.12)")
    parser.add_argument("--version", action="version", version=f"Voice to Me {__version__}")
    parser.add_argument("--config", type=Path, help="Custom config.toml path")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Open Voice to Me and enable configured toggle shortcuts")
    sub.add_parser("devices", help="List input microphones without recording")
    sub.add_parser("check", help="Check dependencies, Codex CLI and local model; no transcription")
    sub.add_parser("download-model", help="Explicitly download the configured Whisper model")
    demo = sub.add_parser("demo", help="Preview Voice to Me using fake adapters, no mic/clipboard/Codex")
    demo.add_argument("--auto-close", type=float, default=0, help="Close preview after N seconds")
    return parser


def _check(settings) -> int:
    from .codex_status import check_codex_readiness

    failures = 0
    print(f"Voice to Me: {__version__}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Config: {settings.config_path}")
    print(f"Writing profile: {settings.profile_path}")
    for name in ("sounddevice", "faster_whisper", "pynput", "pystray", "PIL", "tkinter"):
        try:
            importlib.import_module(name)
            print(f"OK dependency: {name}")
        except Exception as exc:
            print(f"MISSING dependency: {name} ({type(exc).__name__})")
            failures += 1
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        print("OK Tcl/Tk: a hidden test window initialized and closed")
    except Exception as exc:
        print(f"Tcl/Tk could not initialize ({type(exc).__name__}); check runtime and file access")
        failures += 1
    if settings.codex.enabled:
        status = check_codex_readiness(settings.codex)
        if status.ready:
            print("OK Codex CLI: installed, compatible and signed in for refinement")
        else:
            print(f"Codex refinement unavailable: {status.message}")
            failures += 1
    else:
        print("OK local transcription: Codex refinement is off; Codex CLI/login are not required")
    try:
        from .performance import resolve_backend
        backend = resolve_backend(settings.whisper)
        print(f"OK speech backend: {backend.device} / {backend.compute_type}; "
              f"model={settings.whisper.model}; beam={settings.whisper.beam_size}")
    except Exception as exc:
        print(f"Speech backend unavailable: {exc}")
        failures += 1
    try:
        from faster_whisper.utils import download_model
        model = settings.whisper.model_directory or settings.whisper.model
        if settings.whisper.model_directory:
            if not (Path(model) / "model.bin").is_file():
                raise FileNotFoundError()
        else:
            download_model(model, local_files_only=True)
        print("OK Whisper model: available locally")
    except Exception:
        print("Whisper model missing: run the download-model command before recording")
        failures += 1
    print("No microphone recording, clipboard write or Codex refinement was performed.")
    return 1 if failures else 0


class _InstanceLock:
    def __init__(self):
        self.handle = None

    def __enter__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        self.handle = kernel.CreateMutexW(None, False, "Local\\VoiceToMe.VoiceUI")
        if not self.handle:
            raise ConfigurationError("Could not start the application.")
        if ctypes.get_last_error() == 183:
            self.__exit__(None, None, None)
            raise ConfigurationError("Voice to Me is already running. Check the Windows tray icon.")
        return self

    def __exit__(self, *_):
        if self.handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(self.handle)
            self.handle = None


def _hotkey_label(settings) -> str:
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win", "space": "Space"}
    parts = [part.strip("<>") for part in settings.hotkeys.keyboard.split("+") if part]
    keyboard = " + ".join(names.get(part, part.upper()) for part in parts)
    mouse = f"Mouse {settings.hotkeys.mouse_button.upper()}" if settings.hotkeys.mouse_button else ""
    return " / ".join(value for value in (keyboard, mouse) if value) or "Record button"


def _shutdown_app(runtime, *, user_quit: bool) -> None:
    """Finish cleanup, then end this CLI process if a native speech call is stuck."""
    try:
        runtime.shutdown()
    finally:
        if user_quit and runtime.has_active_worker:
            # Tray/UI and input hooks are already closed; CUDA cannot cancel a
            # running kernel. End only our process instead of retaining its GPU.
            logging.shutdown()
            os._exit(0)


def _run(settings) -> None:
    from .runtime import AppRuntime
    from .settings_dialog import describe_paste_shortcut
    from .ui import VoiceUI

    with _InstanceLock():
        runtime = AppRuntime(settings)
        ui = VoiceUI(runtime.controller, config_path=settings.config_path,
                     profile_path=settings.profile_path,
                     hotkey_label=_hotkey_label(settings),
                     paste_hotkey_label=describe_paste_shortcut(settings.hotkeys),
                     on_shutdown=runtime.shutdown,
                     settings_service=runtime.settings_service)
        runtime.controller.set_observer(ui.publish)
        runtime.controller.set_sound_observer(ui.publish_sound)
        try:
            runtime.start()
            ui.run()
        finally:
            _shutdown_app(runtime, user_quit=ui._quitting)


def _demo(settings, auto_close: float) -> None:
    from .controller import AppState
    from .settings import SettingsService
    from .ui import VoiceUI

    class DemoController:
        state = AppState.READY
        observer = None
        input_suspended = False

        def toggle(self):
            if self.input_suspended:
                return
            states = [AppState.READY, AppState.RECORDING, AppState.TRANSCRIBING,
                      AppState.REFINING, AppState.COPIED]
            self.state = states[(states.index(self.state) + 1) % len(states)]
            self.observer(self.state, "Visual preview. No real audio, Codex or clipboard.")

        def cancel(self):
            self.state = AppState.READY
            self.observer(self.state, "Visual preview: ready.")

        def retry(self):
            self.cancel()

        def shutdown(self):
            pass

    controller = DemoController()
    service = SettingsService(settings,
                              suspend=lambda: setattr(controller, "input_suspended", True),
                              resume=lambda: setattr(controller, "input_suspended", False))
    ui = VoiceUI(controller, config_path=settings.config_path, profile_path=settings.profile_path,
                 hotkey_label=_hotkey_label(settings), settings_service=service)
    controller.observer = ui.publish
    ui.publish(controller.state, "Visual preview. Controls use simulated adapters.")
    ui.run(auto_close_seconds=auto_close if auto_close > 0 else None)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if sys.version_info[:2] != (3, 12):
        print("Voice to Me requires Python 3.12. Run setup.bat with a Python 3.12 interpreter.",
              file=sys.stderr)
        return 1
    if sys.platform != "win32":
        print("Voice to Me currently requires Windows.", file=sys.stderr)
        return 1
    try:
        settings = load_settings(args.config)
        logging.basicConfig(filename=settings.config_path.parent / "voice-to-me.log",
                            level=logging.WARNING, encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(message)s")
        logging.getLogger("voice_to_me").setLevel(logging.INFO)
        command = args.command or "run"
        if command == "devices":
            import sounddevice as sd
            for index, device in enumerate(sd.query_devices()):
                if device["max_input_channels"] > 0:
                    print(f"{index}: {device['name']} ({int(device['max_input_channels'])} inputs)")
        elif command == "check":
            return _check(settings)
        elif command == "download-model":
            from faster_whisper.utils import download_model
            if settings.whisper.model_directory:
                print("model_directory is set. Place the model there, or clear it to download.")
                return 1
            print(f"Downloading Whisper {settings.whisper.model}. No audio or text is uploaded.")
            path = download_model(settings.whisper.model)
            print(f"Model available at: {path}")
        elif command == "demo":
            _demo(settings, args.auto_close)
        else:
            _run(settings)
        return 0
    except Exception as exc:
        logging.getLogger(__name__).error("Startup/command failed (%s)", type(exc).__name__)
        message = str(exc) if isinstance(exc, RuntimeError) else (
            "Could not start. Run the check command in a terminal to diagnose the issue."
        )
        if sys.stderr:
            print(message, file=sys.stderr)
        if (args.command or "run") in ("run", "demo"):
            ctypes.windll.user32.MessageBoxW(None, message, "Voice to Me", 0x10)
        return 1
