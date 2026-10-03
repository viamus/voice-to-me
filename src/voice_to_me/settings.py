"""Persist and apply settings from the built-in editor without launching other apps."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path

from .config import AppSettings, ConfigurationError, _validate


@dataclass(frozen=True)
class SettingsDraft:
    settings: AppSettings
    profile_text: str


def _serialize(settings: AppSettings) -> str:
    sections = (
        ("hotkeys", settings.hotkeys), ("audio", settings.audio),
        ("whisper", settings.whisper), ("codex", settings.codex),
    )
    lines = ["# Voice to Me. Editable in the built-in Settings menu.", ""]
    for section, values in sections:
        lines.append(f"[{section}]")
        for key, value in asdict(values).items():
            if value is None:
                continue
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            lines.append(f"{key} = {encoded}")
        lines.append("")
    lines += ["[profile]", "path = " + json.dumps(str(settings.profile_path), ensure_ascii=False), ""]
    return "\n".join(lines)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _validate_draft(draft: SettingsDraft) -> None:
    if not isinstance(draft, SettingsDraft) or not isinstance(draft.settings, AppSettings):
        raise ConfigurationError("Invalid settings. Review the fields and try again.")
    settings = draft.settings
    try:
        if settings.profile_path.resolve() == settings.config_path.resolve():
            raise ValueError("The writing style and configuration must use different files.")
        _validate(settings.audio, settings.whisper, settings.hotkeys, settings.codex)
        if settings.hotkeys.keyboard.strip() or settings.hotkeys.paste_keyboard.strip():
            from pynput.keyboard import HotKey
            shortcuts = []
            for value in (settings.hotkeys.keyboard, settings.hotkeys.paste_keyboard):
                keys = HotKey.parse(value) if value.strip() else []
                if value.strip() and (not keys or len(keys) != len(set(keys))):
                    raise ValueError("Choose a key or a combination without repeated keys.")
                shortcuts.append(set(keys))
            recording_keys, paste_keys = shortcuts
            if recording_keys and paste_keys and (
                recording_keys <= paste_keys or paste_keys <= recording_keys
            ):
                raise ValueError("Choose separate recording and paste shortcuts that do not overlap.")
        if not isinstance(draft.profile_text, str) or not draft.profile_text.strip():
            raise ValueError("Your writing style cannot be empty.")
        if len(draft.profile_text.encode("utf-8")) > 64 * 1024:
            raise ValueError("Your writing style must be at most 64 KiB.")
        if "\x00" in draft.profile_text:
            raise ValueError("Your writing style contains an invalid character.")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ConfigurationError(str(exc)) from None


class SettingsService:
    def __init__(
        self, settings: AppSettings, *,
        apply: Callable[[AppSettings], None] | None = None,
        suspend: Callable[[], None] | None = None,
        resume: Callable[[], None] | None = None,
        is_busy: Callable[[], bool] | None = None,
    ):
        self._settings = settings
        self._apply = apply or (lambda _: None)
        self._suspend = suspend or (lambda: None)
        self._resume = resume or (lambda: None)
        self._is_busy = is_busy or (lambda: False)
        self._suspended = False
        self._lock = threading.RLock()

    @property
    def current_settings(self) -> AppSettings:
        with self._lock:
            return self._settings

    @property
    def settings(self) -> AppSettings:
        return self.current_settings

    def read_draft(self) -> SettingsDraft:
        settings = self.current_settings
        try:
            if settings.profile_path.stat().st_size > 64 * 1024:
                raise ConfigurationError("Your writing style must be at most 64 KiB.")
            text = settings.profile_path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            text = files("voice_to_me").joinpath("defaults/writing-profile.md").read_text(
                encoding="utf-8"
            )
        except (OSError, UnicodeError):
            raise ConfigurationError("Could not read your writing style. Check file permissions.") \
                from None
        return SettingsDraft(settings, text)

    def suspend_shortcuts(self) -> None:
        with self._lock:
            if self._suspended:
                return
            if self._is_busy():
                raise ConfigurationError("Finish or cancel the current recording before changing settings.")
            self._suspend()
            self._suspended = True

    def resume_shortcuts(self) -> None:
        with self._lock:
            if self._suspended:
                self._resume()
                self._suspended = False

    def save(self, draft: SettingsDraft, *, resume_after_save: bool = False) -> AppSettings:
        with self._lock:
            _validate_draft(draft)
            current, updated = self._settings, draft.settings
            if (updated.config_path != current.config_path or
                    updated.profile_path != current.profile_path):
                raise ConfigurationError("The Settings editor cannot change configuration file locations.")
            if self._is_busy():
                raise ConfigurationError("Finish or cancel the current recording before saving settings.")
            was_suspended = self._suspended
            should_resume = not was_suspended or resume_after_save
            if not was_suspended:
                self.suspend_shortcuts()
            old_files: dict[Path, bytes | None] = {}
            written: list[Path] = []
            apply_attempted = False
            try:
                for path in (current.profile_path, current.config_path):
                    old_files[path] = path.read_bytes() if path.exists() else None
                _atomic_write(current.profile_path, (draft.profile_text.strip() + "\n").encode("utf-8"))
                written.append(current.profile_path)
                _atomic_write(current.config_path, _serialize(updated).encode("utf-8"))
                written.append(current.config_path)
                apply_attempted = True
                self._apply(updated)
                if should_resume:
                    self.resume_shortcuts()
                self._settings = updated
                return updated
            except Exception as exc:
                rollback_failed = False
                for path in reversed(written):
                    try:
                        if old_files[path] is None:
                            path.unlink(missing_ok=True)
                        else:
                            _atomic_write(path, old_files[path])
                    except OSError:
                        rollback_failed = True
                if apply_attempted:
                    try:
                        self._apply(current)
                    except Exception:
                        rollback_failed = True
                resume_failed = False
                if should_resume:
                    try:
                        self.resume_shortcuts()
                    except Exception:
                        resume_failed = True
                if rollback_failed:
                    raise ConfigurationError(
                        "Saving failed and previous files or settings could not be fully restored. "
                        "Review Settings before recording."
                    ) from None
                if resume_failed:
                    raise ConfigurationError(
                        "Saving failed. Previous settings were restored, but shortcuts could not "
                        "resume. Reopen Settings or restart the app."
                    ) from None
                if isinstance(exc, RuntimeError):
                    raise ConfigurationError(str(exc)) from None
                raise ConfigurationError("Could not save settings. Check file permissions and try again.") \
                    from None

    def download_model(self, model: str) -> str:
        if not isinstance(model, str) or not model.strip():
            raise ConfigurationError("Choose a Whisper model before downloading.")
        try:
            from faster_whisper.utils import download_model
            return str(download_model(model.strip()))
        except Exception:
            raise ConfigurationError("Model download failed. Check your connection and try again.") \
                from None
