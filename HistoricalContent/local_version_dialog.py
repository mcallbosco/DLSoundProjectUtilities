"""Tkinter editor for the persistent local preview version catalog."""

from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

try:
    from .historical_content.version_catalog import (
        apply_local_catalog,
        load_local_catalog,
        recalculate_version_statuses,
    )
except ImportError:
    from historical_content.version_catalog import (
        apply_local_catalog,
        load_local_catalog,
        recalculate_version_statuses,
    )


class LocalVersionManagerDialog(tk.Toplevel):
    """Edit local preview order, visibility, and default selection."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        data_dir: Path,
        game: str,
        progress: Callable[[str], None] = print,
    ) -> None:
        super().__init__(parent)
        self.data_dir = data_dir.expanduser().resolve()
        self.game = game
        self.progress = progress
        self.catalog: dict[str, object] = {}
        self.dirty = False
        self.busy = False
        self.title(f"Manage local versions — {game}")
        self.minsize(780, 460)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        root.rowconfigure(1, weight=1)
        root.columnconfigure(0, weight=1)
        ttk.Label(
            root,
            text=(
                "Order versions for the selector. Official entries alone control adjacent "
                "version comparisons; custom entries can never become latest."
            ),
            wraplength=740,
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        columns = ("position", "id", "label", "kind", "hidden", "latest")
        self.tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        headings = {
            "position": "Order",
            "id": "Version ID",
            "label": "Label",
            "kind": "Kind",
            "hidden": "Hidden",
            "latest": "Default",
        }
        widths = {
            "position": 55,
            "id": 190,
            "label": 240,
            "kind": 80,
            "hidden": 70,
            "latest": 70,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], stretch=column in {"id", "label"})
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(root, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(root)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions = (
            ("Reload", self._refresh),
            ("Move up", lambda: self._move(-1)),
            ("Move down", lambda: self._move(1)),
            ("Toggle hidden", self._toggle_hidden),
            ("Make latest", self._make_latest),
            ("Save and recalculate", self._save),
            ("Recalculate differences", self._recalculate),
            ("Close", self._close),
        )
        self.buttons: list[ttk.Button] = []
        for index, (label, command) in enumerate(actions):
            button = ttk.Button(buttons, text=label, command=command)
            button.pack(side=tk.LEFT, padx=(0 if index == 0 else 6, 0))
            self.buttons.append(button)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

    def _set_busy(self, busy: bool, status: str) -> None:
        self.busy = busy
        self.status_var.set(status)
        for button in self.buttons:
            button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def _refresh(self) -> None:
        if self.dirty and not messagebox.askyesno(
            "Discard changes?",
            "Reloading will discard unsaved local catalog changes. Continue?",
            parent=self,
        ):
            return
        try:
            self.catalog = load_local_catalog(self.data_dir, self.game)
        except Exception as exc:
            messagebox.showerror("Local version manager", str(exc), parent=self)
            return
        self.dirty = False
        self._populate()

    def _populate(self, selected_id: str | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        latest = str(self.catalog.get("latestVersion") or "")
        versions = self.catalog.get("versions", [])
        for index, version in enumerate(versions if isinstance(versions, list) else []):
            version_id = str(version.get("id") or "")
            item = self.tree.insert(
                "",
                tk.END,
                iid=version_id,
                values=(
                    index + 1,
                    version_id,
                    version.get("label", ""),
                    str(version.get("kind") or "official").title(),
                    "Yes" if version.get("hidden") is True else "No",
                    "Yes" if version_id == latest else "No",
                ),
            )
            if selected_id == version_id:
                self.tree.selection_set(item)
                self.tree.focus(item)
                self.tree.see(item)
        suffix = " — unsaved changes" if self.dirty else ""
        self.status_var.set(f"{len(versions)} local version(s){suffix}")

    def _selected(self) -> tuple[int, dict[str, object]] | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Select a version", "Select a version first.", parent=self)
            return None
        versions = self.catalog.get("versions", [])
        for index, version in enumerate(versions if isinstance(versions, list) else []):
            if version.get("id") == selection[0]:
                return index, version
        return None

    def _move(self, offset: int) -> None:
        selected = self._selected()
        if selected is None:
            return
        index, version = selected
        versions = self.catalog["versions"]
        assert isinstance(versions, list)
        target = index + offset
        if target < 0 or target >= len(versions):
            return
        versions[index], versions[target] = versions[target], versions[index]
        self.dirty = True
        self._populate(str(version["id"]))

    def _toggle_hidden(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        _index, version = selected
        version_id = str(version["id"])
        becoming_hidden = version.get("hidden") is not True
        if becoming_hidden and self.catalog.get("latestVersion") == version_id:
            messagebox.showwarning(
                "Default version",
                "Make another version latest before hiding the current default.",
                parent=self,
            )
            return
        version["hidden"] = becoming_hidden
        self.dirty = True
        self._populate(version_id)

    def _make_latest(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        _index, version = selected
        if version.get("kind") == "custom":
            messagebox.showwarning(
                "Custom content",
                "Custom content cannot become the latest official game version.",
                parent=self,
            )
            return
        version["hidden"] = False
        self.catalog["latestVersion"] = str(version["id"])
        self.dirty = True
        self._populate(str(version["id"]))

    def _run(self, status: str, action: Callable[[], object], done: Callable[[], None]) -> None:
        if self.busy:
            return
        self._set_busy(True, status)
        errors: list[str] = []

        def work() -> None:
            try:
                action()
            except Exception as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=work, daemon=True)

        def poll() -> None:
            if thread.is_alive():
                self.after(50, poll)
                return
            self._set_busy(False, "Ready")
            if errors:
                messagebox.showerror("Local version manager", errors[0], parent=self)
                return
            done()

        thread.start()
        self.after(50, poll)

    def _save(self) -> None:
        payload = json.loads(json.dumps(self.catalog))

        def done() -> None:
            self.catalog = load_local_catalog(self.data_dir, self.game)
            self.dirty = False
            self._populate()
            self.progress("Saved local version order and recalculated voiceline differences.")

        self._run(
            "Saving local catalog and recalculating differences...",
            lambda: apply_local_catalog(self.data_dir, self.game, payload, self.progress),
            done,
        )

    def _recalculate(self) -> None:
        if self.dirty:
            messagebox.showinfo(
                "Unsaved order",
                "Save the local catalog before recalculating differences.",
                parent=self,
            )
            return
        self._run(
            "Recalculating adjacent-version differences...",
            lambda: recalculate_version_statuses(
                self.data_dir,
                self.game,
                load_local_catalog(self.data_dir, self.game),
                self.progress,
            ),
            lambda: self.progress("Recalculated local voiceline differences."),
        )

    def _close(self) -> None:
        if self.busy:
            return
        if self.dirty and not messagebox.askyesno(
            "Discard changes?",
            "Close without saving the local version changes?",
            parent=self,
        ):
            return
        self.destroy()
