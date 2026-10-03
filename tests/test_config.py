from pathlib import Path

import pytest

from voice_to_me.config import CodexSettings, ConfigurationError, load_settings, read_profile


def test_creates_defaults_and_preserves_edits(tmp_path):
    path = tmp_path / "config.toml"
    settings = load_settings(path)
    assert settings.audio.sample_rate == 16000
    assert settings.hotkeys.keyboard == "<ctrl>+<alt>+<space>"
    assert settings.whisper.local_files_only is True
    assert settings.codex.enabled is True
    assert settings.profile_path == tmp_path / "writing-profile.md"
    settings.profile_path.write_text("Perfil editado", encoding="utf-8")
    load_settings(path)
    assert read_profile(settings.profile_path) == "Perfil editado"


@pytest.mark.parametrize("config", [
    '[audio]\nsample_rate = 48000',
    '[audio]\nmax_seconds = 0',
    '[audio]\nminimum_seconds = 5\nmax_seconds = 1',
    '[audio]\ndevice = true',
    '[audio]\nmax_seconds = "180"',
    '[whisper]\nlocal_files_only = false',
    '[whisper]\nmodel = ""',
    '[whisper]\ndevice = "gpu"',
    '[whisper]\ncompute_type = "invalid"',
    '[whisper]\nbeam_size = 0',
    '[whisper]\nbeam_size = true',
    '[codex]\ntimeout_seconds = -1',
    '[codex]\nenabled = "false"',
    '[codex]\nenabled = 1',
    '[codex]\nexecutable = ""',
    '[hotkeys]\nmouse_button = "wheel_up"',
    '[codex]\nreasoning_effort = "invalid"',
    '[codex]\nextra_flags = "--dangerously-bypass-approvals-and-sandbox"',
    '[unknown]\nvalue = 1',
    '[profile]\npath = ""',
    '[profile]\nextra = true',
    '[profile]\npath = "config.toml"',
    'not valid TOML',
])
def test_invalid_config_has_actionable_error(tmp_path, config):
    path = tmp_path / "config.toml"
    path.write_text(config, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Review config.toml"):
        load_settings(path)


@pytest.mark.parametrize("button", ["left", "right", "middle", "x1", "x2"])
def test_all_standard_mouse_buttons_are_valid_config_values(tmp_path, button):
    path = tmp_path / "config.toml"
    path.write_text(f'[hotkeys]\nkeyboard = ""\nmouse_button = "{button}"', encoding="utf-8")
    settings = load_settings(path)
    assert settings.hotkeys.keyboard == ""
    assert settings.hotkeys.mouse_button == button
    assert load_settings(path).hotkeys == settings.hotkeys


def test_relative_and_absolute_profile_paths(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[profile]\npath = "custom.md"', encoding="utf-8")
    assert load_settings(path).profile_path == tmp_path / "custom.md"
    absolute = (tmp_path / "other.md").as_posix()
    path.write_text(f'[profile]\npath = "{absolute}"', encoding="utf-8")
    assert load_settings(path).profile_path == Path(absolute)


def test_profile_bom_and_size(tmp_path):
    path = tmp_path / "style.md"
    path.write_text("Prefira português.", encoding="utf-8-sig")
    assert read_profile(path) == "Prefira português."
    path.write_text("x" * (65536 + 1), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="64 KiB"):
        read_profile(path)


def test_missing_or_empty_profile(tmp_path):
    with pytest.raises(ConfigurationError):
        read_profile(tmp_path / "missing.md")
    path = tmp_path / "empty.md"
    path.write_text(" \n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="empty"):
        read_profile(path)


def test_automatic_backend_and_short_dictation_beam_are_valid(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[whisper]\ndevice = "auto"\ncompute_type = "auto"', encoding="utf-8")
    settings = load_settings(path)
    assert settings.whisper.device == "auto"
    assert settings.whisper.compute_type == "auto"
    assert settings.whisper.beam_size == 1


def test_existing_config_keeps_paste_macro_disabled(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkeys]\nkeyboard = "<f8>"\nmouse_button = "x1"', encoding="utf-8")
    hotkeys = load_settings(path).hotkeys
    assert hotkeys.paste_keyboard == "" and hotkeys.paste_mouse_button == ""


@pytest.mark.parametrize("button", ["left", "right", "middle", "x1", "x2"])
def test_paste_mouse_config_is_independent_of_recording(tmp_path, button):
    path = tmp_path / "config.toml"
    path.write_text(f'[hotkeys]\nkeyboard = "<f8>"\npaste_mouse_button = "{button}"',
                    encoding="utf-8")
    hotkeys = load_settings(path).hotkeys
    assert hotkeys.keyboard == "<f8>" and hotkeys.paste_mouse_button == button


@pytest.mark.parametrize("values", [
    'mouse_button = "x1"\npaste_mouse_button = "x1"',
    'keyboard = "<f8>"\npaste_keyboard = "<f8>"',
    'keyboard = "<ctrl>+m"\npaste_keyboard = "m+<ctrl>"',
    'keyboard = "<ctrl>+m"\npaste_keyboard = "<ctrl>+<shift>+m"',
    'paste_mouse_button = "wheel_up"',
    'paste_keyboard = false',
    'paste_keyboard = "<cmd>"',
    'paste_keyboard = "<ctrl>+<shift>"',
    'paste_keyboard = "a+b"',
])
def test_invalid_or_conflicting_paste_settings_are_rejected(tmp_path, values):
    path = tmp_path / "config.toml"
    path.write_text('[hotkeys]\n' + values, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_settings(path)


def test_codex_can_be_disabled_without_an_executable(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[codex]\nenabled = false\nexecutable = ""', encoding="utf-8")
    assert not load_settings(path).codex.enabled


def test_codex_enabled_defaults_preserve_existing_configs_and_positional_callers(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[codex]\nmodel = "example"', encoding="utf-8")
    assert load_settings(path).codex.enabled
    assert CodexSettings("codex", "example", 5, "low").enabled
