"""Session history widgets; all text stays inside the existing app window."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable
from tkinter import ttk
from typing import Any


def history_snippet(text: str, limit: int = 90) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit - 1].rstrip() + "…"


def history_mode(entry: Any) -> str:
    return "Refined" if entry.refined else "Local"


def history_details(entry: Any) -> str:
    stamp = entry.created_at.astimezone().strftime("%b %d, %H:%M:%S")
    mode = "Refined with Codex CLI" if entry.refined else "Local transcription"
    return f"{mode} · {stamp} · {entry.elapsed_seconds:.1f} s total"


class HistoryPage:
    def __init__(
        self, parent: tk.Misc, *, colors: dict[str, str],
        on_copy: Callable[[int], None], on_clear: Callable[[], None],
    ) -> None:
        self.colors, self.on_copy, self.on_clear = colors, on_copy, on_clear
        self._entries: dict[int, Any] = {}
        self._selected_id: int | None = None
        self._available = True
        self.frame = tk.Frame(parent, bg=colors["background"], padx=24, pady=20)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(2, weight=1)
        tk.Label(
            self.frame, text="History", font=("Segoe UI", 18, "bold"),
            bg=colors["background"], fg=colors["ink"], anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        self._subtitle = tk.Label(
            self.frame,
            text="Last 30 messages in this session. Cleared when you quit.",
            bg=colors["background"], fg=colors["muted"], justify="left", wraplength=520,
        )
        self._subtitle.grid(row=1, column=0, sticky="w", pady=(5, 14))
        self._panes = ttk.Panedwindow(self.frame, orient="vertical")
        self._panes.grid(row=2, column=0, sticky="nsew")
        listing = tk.Frame(self._panes, bg=colors["background"])
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self._list = ttk.Treeview(
            listing, columns=("time", "mode", "message"), show="headings",
            selectmode="browse", height=6,
        )
        for name, label, width in (("time", "Time", 82), ("mode", "Mode", 83),
                                   ("message", "Message", 340)):
            self._list.heading(name, text=label, anchor="w")
            self._list.column(name, width=width, minwidth=60, stretch=name == "message")
        self._list.grid(row=0, column=0, sticky="nsew")
        list_scroll = ttk.Scrollbar(listing, orient="vertical", command=self._list.yview)
        list_scroll.grid(row=0, column=1, sticky="ns")
        self._list.configure(yscrollcommand=list_scroll.set)
        self._list.bind("<<TreeviewSelect>>", self._selection_changed)
        self._list.bind("<Control-c>", self._copy_key)
        self._empty = tk.Label(
            listing, text="Your completed messages will appear here.",
            bg=colors["background"], fg=colors["muted"], anchor="w", wraplength=490,
        )
        self._empty.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._panes.add(listing, weight=1)

        preview = tk.Frame(self._panes, bg=colors["background"], pady=12)
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)
        self._metadata = tk.Label(
            preview, text="Select a message to read it.", bg=colors["background"],
            fg=colors["muted"], anchor="w", justify="left", wraplength=520,
            font=("Segoe UI", 10),
        )
        self._metadata.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._text = tk.Text(
            preview, state="disabled", wrap="word", height=7, font=("Segoe UI", 11),
            bg=colors["surface"], fg=colors["ink"], relief="solid", bd=1,
            padx=12, pady=10, takefocus=True,
        )
        self._text.grid(row=1, column=0, sticky="nsew")
        self._text.bind("<Control-c>", self._copy_key)
        text_scroll = ttk.Scrollbar(preview, orient="vertical", command=self._text.yview)
        text_scroll.grid(row=1, column=1, sticky="ns")
        self._text.configure(yscrollcommand=text_scroll.set)
        self._panes.add(preview, weight=1)

        actions = tk.Frame(self.frame, bg=colors["background"])
        actions.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        actions.columnconfigure(0, weight=1)
        self._copy_button = ttk.Button(actions, text="Copy text", command=self.copy_selected)
        self._copy_button.grid(row=0, column=0, sticky="w")
        self._clear_button = ttk.Button(actions, text="Clear history", command=self.clear)
        self._clear_button.grid(row=0, column=1, sticky="e")
        self._notice = tk.Label(
            self.frame, text="", bg=colors["background"], fg=colors["muted"],
            wraplength=520, anchor="w", justify="left", font=("Segoe UI", 10),
        )
        self._notice.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.frame.bind("<Configure>", self._resize)
        self.render(())

    def _resize(self, event: Any) -> None:
        width = max(120, event.width - 48)
        for label in (self._subtitle, self._metadata, self._empty, self._notice):
            label.configure(wraplength=width)

    def render(self, entries: Iterable[Any]) -> None:
        self._entries = {entry.id: entry for entry in entries}
        children = self._list.get_children()
        if children:
            self._list.delete(*children)
        for entry in self._entries.values():
            self._list.insert("", "end", iid=str(entry.id), values=(
                entry.created_at.astimezone().strftime("%H:%M:%S"), history_mode(entry),
                history_snippet(entry.text),
            ))
        if self._selected_id not in self._entries:
            self._selected_id = next(iter(self._entries), None)
        if self._selected_id is not None:
            self._list.selection_set(str(self._selected_id))
            self._empty.grid_remove()
        else:
            self._empty.grid()
        self._display_selected()
        self.set_notice("")

    def _selection_changed(self, _event: Any = None) -> None:
        selected = self._list.selection()
        try:
            candidate = int(selected[0]) if selected else None
        except (ValueError, TypeError):
            candidate = None
        self._selected_id = candidate if candidate in self._entries else None
        self._display_selected()

    def _display_selected(self) -> None:
        entry = self._entries.get(self._selected_id)
        self._metadata.configure(text=history_details(entry) if entry else "Select a message to read it.")
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        if entry is not None:
            self._text.insert("1.0", entry.text)
        self._text.configure(state="disabled")
        self.set_available(self._available)

    def set_available(self, available: bool) -> None:
        self._available = available
        self._copy_button.configure(
            state="normal" if available and self._selected_id in self._entries else "disabled",
        )
        self._clear_button.configure(state="normal" if available and self._entries else "disabled")

    def copy_selected(self) -> None:
        if self._available and self._selected_id in self._entries:
            self.on_copy(self._selected_id)

    def _copy_key(self, _event: Any = None) -> str:
        self.copy_selected()
        return "break"

    def clear(self) -> None:
        if self._available and self._entries:
            self.on_clear()

    def set_notice(self, message: str, *, error: bool = False) -> None:
        self._notice.configure(text=message, fg=self.colors["error"] if error else self.colors["muted"])

    def clear_memory(self) -> None:
        self.render(())
        self.set_available(False)

    def scroll(self, delta: int) -> None:
        self._list.yview_scroll(delta, "units")
