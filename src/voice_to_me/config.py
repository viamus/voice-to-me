"""Typed application settings without dependencies on Jarvis configuration."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

MOUSE_BUTTONS = ("left", "right", "middle", "x1", "x2")


@dataclass(frozen=True)
class AudioSettings:
    device: int | str | None = None
    sample_rate: int = 16000
    max_seconds: float = 180.0
    minimum_seconds: float = 0.3


@dataclass(frozen=True)
class WhisperSettings:
    model: str = "small"
    language: str = "pt"
    device: str = "auto"
    compute_type: str = "auto"
    model_directory: str = ""
    local_files_only: bool = True
    beam_size: int = 1


@dataclass(frozen=True)
class HotkeySettings:
    keyboard: str = "<ctrl>+<alt>+<space>"
    mouse_button: str = ""
    paste_keyboard: str = ""
    paste_mouse_button: str = ""


@dataclass(frozen=True)
class CodexSettings:
    executable: str = "codex"
    model: str = ""
    timeout_seconds: float = 120.0
    reasoning_effort: str = "low"
    enabled: bool = True


class ConfigurationError(RuntimeError):
    """Configuration needs to be corrected by the user."""


@dataclass(frozen=True)
class AppSettings:
    audio: AudioSettings
    whisper: WhisperSettings
    hotkeys: HotkeySettings
    codex: CodexSettings
    config_path: Path
    profile_path: Path


def default_config_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return base / "VoiceToMe" / "config.toml"


def initialize_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    defaults = files("voice_to_me").joinpath("defaults")
    if not path.exists():
        path.write_text(defaults.joinpath("config.toml").read_text(encoding="utf-8"),
                        encoding="utf-8")
    profile = path.parent / "writing-profile.md"
    if not profile.exists():
        profile.write_text(defaults.joinpath("writing-profile.md").read_text(encoding="utf-8"),
                           encoding="utf-8")


def load_settings(path: Path | None = None) -> AppSettings:
    path = (path or default_config_path()).expanduser().resolve()
    initialize_config(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        unknown = set(data) - {"audio", "whisper", "hotkeys", "codex", "profile"}
        if unknown:
            raise ValueError("unknown section: " + ", ".join(sorted(unknown)))
        audio = AudioSettings(**data.get("audio", {}))
        whisper = WhisperSettings(**data.get("whisper", {}))
        hotkeys = HotkeySettings(**data.get("hotkeys", {}))
        codex = CodexSettings(**data.get("codex", {}))
        _validate(audio, whisper, hotkeys, codex)
        profile_data = data.get("profile", {})
        if set(profile_data) - {"path"}:
            raise ValueError("unknown field in profile")
        profile_value = profile_data.get("path", "writing-profile.md")
        if not isinstance(profile_value, str) or not profile_value.strip():
            raise ValueError("profile.path must be a file path")
        profile_path = Path(os.path.expandvars(profile_value)).expanduser()
        if not profile_path.is_absolute():
            profile_path = path.parent / profile_path
        if profile_path.resolve() == path:
            raise ValueError("The writing style and configuration must use different files")
        return AppSettings(audio, whisper, hotkeys, codex, path, profile_path.resolve())
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigurationError(f"Review {path.name}: {exc}") from None


def _validate(audio, whisper, hotkeys, codex) -> None:
    if type(codex.enabled) is not bool:
        raise ValueError("codex.enabled must be true or false")
    if type(audio.sample_rate) is not int or audio.sample_rate != 16000:
        raise ValueError("audio.sample_rate must be 16000 (mono Whisper input)")
    for name, value, lower, upper in (
        ("audio.max_seconds", audio.max_seconds, 1, 1800),
        ("audio.minimum_seconds", audio.minimum_seconds, 0.1, 10),
        ("codex.timeout_seconds", codex.timeout_seconds, 1, 1800),
    ):
        if type(value) not in (int, float) or not lower <= value <= upper:
            raise ValueError(f"{name} must be between {lower} and {upper}")
    if audio.minimum_seconds >= audio.max_seconds:
        raise ValueError("minimum_seconds must be smaller than max_seconds")
    if audio.device is not None and type(audio.device) not in (int, str):
        raise ValueError("audio.device must be a microphone index or name")
    if isinstance(audio.device, int) and audio.device < 0:
        raise ValueError("audio.device must not be negative")
    for name, value in (
        ("whisper.model", whisper.model), ("whisper.language", whisper.language),
        ("whisper.device", whisper.device), ("whisper.compute_type", whisper.compute_type),
        ("whisper.model_directory", whisper.model_directory),
        ("hotkeys.keyboard", hotkeys.keyboard), ("hotkeys.mouse_button", hotkeys.mouse_button),
        ("hotkeys.paste_keyboard", hotkeys.paste_keyboard),
        ("hotkeys.paste_mouse_button", hotkeys.paste_mouse_button),
        ("codex.executable", codex.executable), ("codex.model", codex.model),
        ("codex.reasoning_effort", codex.reasoning_effort),
    ):
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text")
    if not whisper.model.strip():
        raise ValueError("whisper.model cannot be empty")
    if codex.enabled and not codex.executable.strip():
        raise ValueError("codex.executable cannot be empty when refinement is enabled")
    if whisper.device not in {"auto", "cpu", "cuda"}:
        raise ValueError("whisper.device must be auto, cpu, or cuda")
    if whisper.compute_type not in {
        "auto", "default", "int8", "int8_float16", "int8_float32", "int16",
        "float16", "float32", "bfloat16",
    }:
        raise ValueError("whisper.compute_type is not supported")
    if type(whisper.beam_size) is not int or not 1 <= whisper.beam_size <= 5:
        raise ValueError("whisper.beam_size must be between 1 and 5")
    if type(whisper.local_files_only) is not bool or not whisper.local_files_only:
        raise ValueError("Keep whisper.local_files_only = true; download the model before recording")
    for name, value in (("mouse_button", hotkeys.mouse_button),
                        ("paste_mouse_button", hotkeys.paste_mouse_button)):
        if value not in ("", *MOUSE_BUTTONS):
            raise ValueError(f"hotkeys.{name} must be empty, left, right, middle, x1 or x2")
    if hotkeys.mouse_button and hotkeys.mouse_button == hotkeys.paste_mouse_button:
        raise ValueError("Choose different buttons for recording and pasting.")
    recording_keys = {part.strip().lower() for part in hotkeys.keyboard.split("+") if part.strip()}
    paste_keys = {part.strip().lower() for part in hotkeys.paste_keyboard.split("+") if part.strip()}
    modifiers = {"ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr",
                 "shift", "shift_l", "shift_r", "cmd", "cmd_l", "cmd_r"}
    if paste_keys and all(part.strip("<>") in modifiers for part in paste_keys):
        raise ValueError("Choose a regular key, function key or mouse button for pasting.")
    if sum(part.strip("<>") not in modifiers for part in paste_keys) > 1:
        raise ValueError("Choose one paste key, optionally with Ctrl, Alt, Shift or Win.")
    if recording_keys and paste_keys and (
        recording_keys <= paste_keys or paste_keys <= recording_keys
    ):
        raise ValueError("Choose separate recording and paste shortcuts that do not overlap.")
    if codex.reasoning_effort not in ("minimal", "low", "medium", "high"):
        raise ValueError("codex.reasoning_effort must be minimal, low, medium or high")


def read_profile(path: Path) -> str:
    try:
        if path.stat().st_size > 64 * 1024:
            raise ConfigurationError("The writing style must be at most 64 KiB.")
        value = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        raise ConfigurationError("Could not read your writing style. Review it in Settings.") \
            from None
    if not value:
        raise ConfigurationError("Your writing style is empty.")
    return value
