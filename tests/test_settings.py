from dataclasses import replace
from unittest.mock import Mock

import pytest

from voice_to_me.config import ConfigurationError, HotkeySettings, load_settings
from voice_to_me.settings import SettingsDraft, SettingsService


@pytest.fixture
def settings(tmp_path):
    return load_settings(tmp_path / "config.toml")


def test_save_persists_style_unicode_and_updates_live_shortcuts(settings):
    events = []
    service = SettingsService(settings, suspend=lambda: events.append("suspend"),
                              resume=lambda: events.append("resume"),
                              apply=lambda _: events.append("apply"))
    updated = replace(settings, hotkeys=HotkeySettings("<f8>", "middle"))
    service.save(SettingsDraft(updated, "Use concise messages. Preserve names like João. 👋"))
    reloaded = load_settings(settings.config_path)
    assert reloaded.hotkeys == HotkeySettings("<f8>", "middle")
    assert "João. 👋" in service.read_draft().profile_text
    assert service.current_settings == updated
    assert events == ["suspend", "apply", "resume"]


def test_all_settings_roundtrip_without_launching_apps(settings):
    updated = replace(settings,
                      audio=replace(settings.audio, device="USB microphone", max_seconds=30.5),
                      whisper=replace(settings.whisper, language="en", model="base"),
                      codex=replace(settings.codex, executable=r"C:\Tools\codex.exe", model="model"))
    service = SettingsService(settings)
    service.save(SettingsDraft(updated, "Natural wording. Preserve the source language."))
    assert load_settings(settings.config_path) == updated
    assert not list(settings.config_path.parent.glob("*.tmp"))


@pytest.mark.parametrize("button", ["left", "right", "middle", "x1", "x2"])
def test_all_standard_mouse_buttons_roundtrip_through_settings(settings, button):
    updated = replace(settings, hotkeys=HotkeySettings(keyboard="", mouse_button=button))
    apply = Mock()
    service = SettingsService(settings, apply=apply)
    service.save(SettingsDraft(updated, "Keep my writing style."))
    reloaded = load_settings(settings.config_path)
    assert reloaded.hotkeys == HotkeySettings(keyboard="", mouse_button=button)
    assert service.current_settings == updated
    apply.assert_called_once_with(updated)


@pytest.mark.parametrize("paste_keyboard,paste_mouse", [("<f9>", ""), ("", "x2")])
def test_paste_binding_roundtrips_and_keeps_recording_binding(settings, paste_keyboard, paste_mouse):
    updated = replace(settings, hotkeys=HotkeySettings("", "x1", paste_keyboard, paste_mouse))
    service = SettingsService(settings)
    service.save(SettingsDraft(updated, "Keep my writing style."))
    assert load_settings(settings.config_path).hotkeys == updated.hotkeys


@pytest.mark.parametrize("hotkeys", [
    HotkeySettings("", "x1", "", "x1"),
    HotkeySettings("<f8>", "", "<f8>", ""),
    HotkeySettings("<ctrl>+m", "", "m+<ctrl>", ""),
    HotkeySettings("<ctrl>+m", "", "<ctrl>+<shift>+m", ""),
    HotkeySettings("<f8>", "", "not a key", ""),
    HotkeySettings("<f8>", "", "<cmd>", ""),
    HotkeySettings("<f8>", "", "a+b", ""),
])
def test_invalid_paste_binding_never_changes_files_or_listeners(settings, hotkeys):
    original = settings.config_path.read_bytes(), settings.profile_path.read_bytes()
    suspend, apply = Mock(), Mock()
    service = SettingsService(settings, suspend=suspend, apply=apply)
    with pytest.raises(ConfigurationError):
        service.save(SettingsDraft(replace(settings, hotkeys=hotkeys), "Keep my tone."))
    assert original == (settings.config_path.read_bytes(), settings.profile_path.read_bytes())
    suspend.assert_not_called()
    apply.assert_not_called()


@pytest.mark.parametrize("profile", ["", "  ", "x" * 65537, "x\x00y", "bad\ud800"],
                         ids=["empty", "whitespace", "too-large", "nul", "invalid-unicode"])
def test_invalid_style_never_changes_files_or_listeners(settings, profile):
    original = settings.config_path.read_bytes(), settings.profile_path.read_bytes()
    apply, suspend = Mock(), Mock()
    service = SettingsService(settings, apply=apply, suspend=suspend)
    with pytest.raises(ConfigurationError):
        service.save(SettingsDraft(settings, profile))
    assert original == (settings.config_path.read_bytes(), settings.profile_path.read_bytes())
    apply.assert_not_called()
    suspend.assert_not_called()


def test_invalid_shortcut_preserves_settings(settings):
    service = SettingsService(settings)
    updated = replace(settings, hotkeys=HotkeySettings("not a key", ""))
    with pytest.raises(ConfigurationError):
        service.save(SettingsDraft(updated, "Keep my style."))
    assert service.current_settings == settings


def test_apply_failure_restores_exact_previous_files(settings):
    original = settings.config_path.read_bytes(), settings.profile_path.read_bytes()
    apply = Mock(side_effect=[RuntimeError("Shortcut listener could not start."), None])
    suspend, resume = Mock(), Mock()
    service = SettingsService(settings, apply=apply, suspend=suspend, resume=resume)
    updated = replace(settings, hotkeys=HotkeySettings("<f8>", ""))
    with pytest.raises(ConfigurationError, match="listener"):
        service.save(SettingsDraft(updated, "New writing style."))
    assert original == (settings.config_path.read_bytes(), settings.profile_path.read_bytes())
    assert service.current_settings == settings
    resume.assert_called_once()


def test_second_file_write_failure_rolls_back_profile(settings, monkeypatch):
    from voice_to_me import settings as module
    original = settings.config_path.read_bytes(), settings.profile_path.read_bytes()
    original_write = module._atomic_write

    def fail_config(path, content):
        if path == settings.config_path:
            raise PermissionError("fake denial")
        return original_write(path, content)

    monkeypatch.setattr(module, "_atomic_write", fail_config)
    with pytest.raises(ConfigurationError, match="Could not save"):
        SettingsService(settings).save(SettingsDraft(settings, "New writing style."))
    assert original == (settings.config_path.read_bytes(), settings.profile_path.read_bytes())


def test_busy_pipeline_blocks_save_and_capture(settings):
    suspend = Mock()
    service = SettingsService(settings, is_busy=lambda: True, suspend=suspend)
    with pytest.raises(ConfigurationError, match="Finish or cancel"):
        service.save(service.read_draft())
    with pytest.raises(ConfigurationError, match="Finish or cancel"):
        service.suspend_shortcuts()
    suspend.assert_not_called()


def test_capture_suspension_is_idempotent_and_save_preserves_it(settings):
    suspend, resume = Mock(), Mock()
    service = SettingsService(settings, suspend=suspend, resume=resume)
    service.suspend_shortcuts()
    service.suspend_shortcuts()
    service.save(SettingsDraft(settings, "Keep it short."))
    suspend.assert_called_once()
    resume.assert_not_called()
    service.resume_shortcuts()
    service.resume_shortcuts()
    resume.assert_called_once()


def test_empty_or_missing_style_is_editable_in_settings(settings):
    settings.profile_path.write_text("", encoding="utf-8")
    service = SettingsService(settings)
    assert service.read_draft().profile_text == ""
    settings.profile_path.unlink()
    assert "Writing style" in service.read_draft().profile_text
    service.save(SettingsDraft(settings, "Recovered style."))
    assert service.read_draft().profile_text.strip() == "Recovered style."


def test_editor_cannot_redirect_file_writes(settings, tmp_path):
    changed = replace(settings, profile_path=tmp_path / "elsewhere.md")
    with pytest.raises(ConfigurationError, match="file locations"):
        SettingsService(settings).save(SettingsDraft(changed, "Style."))
    assert not changed.profile_path.exists()


def test_model_download_is_only_an_explicit_action(settings, monkeypatch):
    download = Mock(return_value="local/model/folder")
    monkeypatch.setattr("faster_whisper.utils.download_model", download)
    service = SettingsService(settings)
    service.save(SettingsDraft(settings, "Keep it short."))
    download.assert_not_called()
    assert service.download_model("small") == "local/model/folder"
    download.assert_called_once_with("small")


def test_model_download_failure_is_safe(settings, monkeypatch):
    monkeypatch.setattr("faster_whisper.utils.download_model", Mock(side_effect=OSError))
    with pytest.raises(ConfigurationError, match="download failed"):
        SettingsService(settings).download_model("small")


def test_resume_failure_rolls_back_files_runtime_and_current_settings(settings):
    original = settings.config_path.read_bytes(), settings.profile_path.read_bytes()
    apply = Mock()
    resume = Mock(side_effect=[RuntimeError("Shortcut listener failed to resume."), None])
    service = SettingsService(settings, apply=apply, resume=resume)
    updated = replace(settings, hotkeys=HotkeySettings("<f8>", ""))
    with pytest.raises(ConfigurationError, match="resume"):
        service.save(SettingsDraft(updated, "New style."))
    assert service.current_settings == settings
    assert original == (settings.config_path.read_bytes(), settings.profile_path.read_bytes())
    assert [call.args[0] for call in apply.call_args_list] == [updated, settings]
    assert not service._suspended


def test_profile_must_not_overwrite_configuration(settings):
    updated = replace(settings, profile_path=settings.config_path)
    original = settings.config_path.read_bytes()
    with pytest.raises(ConfigurationError, match="different files"):
        SettingsService(updated).save(SettingsDraft(updated, "New style."))
    assert settings.config_path.read_bytes() == original


def test_settings_dialog_save_resumes_its_hold_before_confirming(settings):
    resume = Mock()
    service = SettingsService(settings, resume=resume)
    service.suspend_shortcuts()
    service.save(SettingsDraft(settings, "Keep it short."), resume_after_save=True)
    resume.assert_called_once()
    assert not service._suspended


def test_settings_dialog_resume_failure_rolls_back_while_already_suspended(settings):
    original = settings.config_path.read_bytes(), settings.profile_path.read_bytes()
    resume = Mock(side_effect=[RuntimeError("Shortcut resume failed."), None])
    service = SettingsService(settings, resume=resume)
    service.suspend_shortcuts()
    with pytest.raises(ConfigurationError, match="resume"):
        service.save(SettingsDraft(settings, "New style."), resume_after_save=True)
    assert original == (settings.config_path.read_bytes(), settings.profile_path.read_bytes())
