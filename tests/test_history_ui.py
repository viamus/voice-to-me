"""Read-only session history tests with widget fakes and synthetic messages."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from voice_to_me.history import HistoryEntry
from voice_to_me.history_ui import HistoryPage, history_details, history_mode, history_snippet


def entry(identifier=1, text="A synthetic message.", refined=False):
    return HistoryEntry(identifier, datetime(2026, 10, 3, 12, 30, tzinfo=UTC),
                        text, refined, 3.25)


@pytest.fixture
def page():
    history = HistoryPage.__new__(HistoryPage)
    history.colors = {"error": "red", "muted": "gray"}
    history.on_copy, history.on_clear = Mock(), Mock()
    history._entries, history._selected_id, history._available = {}, None, True
    for name in ("_list", "_text", "_metadata", "_empty", "_copy_button", "_clear_button", "_notice"):
        setattr(history, name, Mock())
    history._list.get_children.return_value = ()
    history._list.selection.return_value = ()
    return history


def test_snippets_normalize_whitespace_and_bound_long_text():
    assert history_snippet("Line one.\n\n Line\t two.") == "Line one. Line two."
    assert len(history_snippet("word " * 200)) <= 90
    assert history_snippet("word " * 200).endswith("…")


def test_details_show_timestamp_mode_and_total_duration():
    local = entry()
    assert history_mode(local) == "Local"
    assert "Local transcription" in history_details(local)
    assert "Oct 03" in history_details(local)
    assert "3.2 s total" in history_details(local)
    assert history_mode(entry(refined=True)) == "Refined"
    assert "Refined with Codex CLI" in history_details(entry(refined=True))


def test_render_selects_newest_and_displays_full_text_read_only_without_copying(page):
    newest, older = entry(2, "Full synthetic\nmessage.", True), entry(1)
    page.render((newest, older))
    assert page._selected_id == 2
    assert [call.kwargs["iid"] for call in page._list.insert.call_args_list] == ["2", "1"]
    assert page._list.insert.call_args_list[0].kwargs["values"][1:] == (
        "Refined", "Full synthetic message.",
    )
    page._text.insert.assert_called_once_with("1.0", newest.text)
    page._text.configure.assert_called_with(state="disabled")
    page.on_copy.assert_not_called()
    page.on_clear.assert_not_called()


def test_new_entries_preserve_user_selection_and_removed_selection_resets_cleanly(page):
    page.render((entry(2), entry(1)))
    page._list.selection.return_value = ("1",)
    page._selection_changed()
    page.render((entry(3), entry(2), entry(1)))
    assert page._selected_id == 1
    page.render((entry(3), entry(2)))
    assert page._selected_id == 3
    page.render(())
    assert page._selected_id is None and not page._entries
    page._empty.grid.assert_called_once()
    page._metadata.configure.assert_called_with(text="Select a message to read it.")
    page._copy_button.configure.assert_called_with(state="disabled")
    page._clear_button.configure.assert_called_with(state="disabled")


@pytest.mark.parametrize("selection", [(), ("missing",), ("999",)])
def test_stale_or_invalid_selection_cannot_copy(page, selection):
    page.render((entry(),))
    page._list.selection.return_value = selection
    page._selection_changed()
    page.copy_selected()
    page.on_copy.assert_not_called()
    assert page._selected_id is None


def test_copy_and_clear_are_explicit_and_blocked_when_busy(page):
    page.render((entry(7),))
    page.set_available(False)
    assert page._copy_key() == "break"
    page.clear()
    page.on_copy.assert_not_called()
    page.on_clear.assert_not_called()
    page.set_available(True)
    page.copy_selected()
    page.clear()
    page.on_copy.assert_called_once_with(7)
    page.on_clear.assert_called_once_with()


def test_clear_memory_removes_all_text_and_disables_actions(page):
    page.render((entry(8, "Private synthetic message."),))
    page._text.reset_mock()
    page.clear_memory()
    assert not page._entries and page._selected_id is None
    page._text.delete.assert_called_once_with("1.0", "end")
    page._text.insert.assert_not_called()
    page._text.configure.assert_called_with(state="disabled")
    assert not page._available
