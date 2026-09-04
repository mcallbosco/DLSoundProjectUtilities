"""Edit the published version catalog from Historical Content."""

from __future__ import annotations

import json
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from ..publishing.core import R2Publisher


class VersionManagerDialog(tk.Toplevel):
    """Edit public version visibility, ordering, and latest selection."""

    def __init__(self, parent: tk.Misc, publisher: R2Publisher, progress: Callable[[str], None] = print) -> None:
        super().__init__(parent)
        self.progress = progress
        self.publisher = publisher
        self.manifest: dict = {}
        self.dirty = False
        self.busy = False
        self.title(f"Manage published versions — {publisher.settings.game}")
        self.minsize(900, 500)
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
                "This order is used by the version selector. Hidden versions remain available by "
                "an explicit ?version= URL; hidden is not an access-control feature."
            ),
            wraplength=850,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        columns = ("position", "id", "label", "kind", "hidden", "latest", "revision", "updated")
        self.tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        headings = {
            "position": "Order",
            "id": "Version ID",
            "label": "Label",
            "kind": "Kind",
            "hidden": "Hidden",
            "latest": "Latest",
            "revision": "Revision",
            "updated": "Updated",
        }
        widths = {
            "position": 55,
            "id": 190,
            "label": 170,
            "kind": 75,
            "hidden": 65,
            "latest": 60,
            "revision": 65,
            "updated": 190,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], stretch=column in {"id", "label", "updated"})
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(root, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(root)
        buttons.grid(row=2, column=0, columnspan=2, pady=(10, 0), sticky="ew")
        self.buttons: list[ttk.Button] = []
        actions = [
            ("Refresh from R2", self._refresh),
            ("Move up", lambda: self._move(-1)),
            ("Move down", lambda: self._move(1)),
            ("Toggle hidden", self._toggle_hidden),
            ("Make latest", self._make_latest),
            ("Save changes", self._save),
            ("Refresh character routes", self._refresh_character_routes),
            ("Close", self._close),
        ]
        for index, (label, command) in enumerate(actions):
            button = ttk.Button(buttons, text=label, command=command)
            button.pack(side=tk.LEFT, padx=(0 if index == 0 else 6, 0))
            self.buttons.append(button)

        self.status_var = tk.StringVar(value="Loading manifest...")
        ttk.Label(root, textvariable=self.status_var).grid(
            row=3, column=0, columnspan=2, pady=(8, 0), sticky="w"
        )

    def _set_busy(self, busy: bool, status: str) -> None:
        self.busy = busy
        self.status_var.set(status)
        state = tk.DISABLED if busy else tk.NORMAL
        for button in self.buttons:
            button.configure(state=state)

    def _run_background(self, status: str, action: Callable[[], dict], done: Callable[[dict], None]) -> None:
        if self.busy:
            return
        self._set_busy(True, status)

        result_holder: list[dict] = []
        error_holder: list[str] = []

        def run() -> None:
            try:
                result_holder.append(action())
            except Exception as exc:
                error_holder.append(str(exc))

        worker = threading.Thread(target=run, daemon=True)

        def poll() -> None:
            if worker.is_alive():
                self.after(50, poll)
            elif error_holder:
                self._operation_failed(error_holder[0])
            else:
                self._operation_done(result_holder[0], done)

        worker.start()
        self.after(50, poll)

    def _operation_failed(self, message: str) -> None:
        self._set_busy(False, "Operation failed")
        messagebox.showerror("Version manager", message, parent=self)

    def _operation_done(self, result: dict, done: Callable[[dict], None]) -> None:
        done(result)
        self._set_busy(False, "Ready")

    def _refresh(self) -> None:
        if self.dirty and not messagebox.askyesno(
            "Discard changes?",
            "Reloading will discard unsaved version changes. Continue?",
            parent=self,
        ):
            return

        def done(manifest: dict) -> None:
            self.manifest = manifest
            self.dirty = False
            self._populate()

        self._run_background(
            "Loading version manifest from R2...",
            self.publisher.load_game_manifest,
            done,
        )

    def _populate(self, selected_id: str | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        latest = self.manifest.get("latestVersion", "")
        for index, version in enumerate(self.manifest.get("versions", [])):
            version_id = str(version.get("id", ""))
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
                    version.get("contentRevision", ""),
                    version.get("updatedAt", ""),
                ),
            )
            if selected_id == version_id:
                self.tree.selection_set(item)
                self.tree.focus(item)
                self.tree.see(item)
        suffix = " — unsaved changes" if self.dirty else ""
        self.status_var.set(f"{len(self.manifest.get('versions', []))} version(s){suffix}")

    def _selected(self) -> tuple[int, dict] | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Select a version", "Select a version first.", parent=self)
            return None
        version_id = selection[0]
        versions = self.manifest.get("versions", [])
        for index, version in enumerate(versions):
            if version.get("id") == version_id:
                return index, version
        return None

    def _move(self, offset: int) -> None:
        selected = self._selected()
        if not selected:
            return
        index, version = selected
        versions = self.manifest["versions"]
        target = index + offset
        if target < 0 or target >= len(versions):
            return
        versions[index], versions[target] = versions[target], versions[index]
        self.dirty = True
        self._populate(str(version.get("id")))

    def _toggle_hidden(self) -> None:
        selected = self._selected()
        if not selected:
            return
        _index, version = selected
        version_id = str(version.get("id"))
        becoming_hidden = version.get("hidden") is not True
        if becoming_hidden and self.manifest.get("latestVersion") == version_id:
            messagebox.showwarning(
                "Latest version",
                "Promote another version before hiding the current latest version.",
                parent=self,
            )
            return
        version["hidden"] = becoming_hidden
        self.dirty = True
        self._populate(version_id)

    def _make_latest(self) -> None:
        selected = self._selected()
        if not selected:
            return
        _index, version = selected
        version_id = str(version.get("id"))
        if version.get("kind") == "custom":
            messagebox.showwarning(
                "Custom content",
                "Custom content cannot become the latest official game version.",
                parent=self,
            )
            return
        version["hidden"] = False
        self.manifest["latestVersion"] = version_id
        self.dirty = True
        self._populate(version_id)

    def _save(self) -> None:
        if not self.dirty:
            messagebox.showinfo("No changes", "There are no manifest changes to save.", parent=self)
            return
        if not messagebox.askyesno(
            "Save version catalog?",
            "Apply this visibility, order, and latest-version selection to the public manifest?",
            icon=messagebox.WARNING,
            parent=self,
        ):
            return
        payload = json.loads(json.dumps(self.manifest))

        def done(manifest: dict) -> None:
            self.manifest = manifest
            self.dirty = False
            self._populate()
            self.progress("Saved published version visibility, order, and latest selection.")

        self._run_background(
            "Saving public version manifest...",
            lambda: self.publisher.save_game_manifest(payload),
            done,
        )

    def _refresh_character_routes(self) -> None:
        if self.dirty:
            messagebox.showinfo(
                "Unsaved changes",
                "Save or discard the version catalog changes before refreshing character routes.",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "Refresh character routes?",
            "Rebuild the all-version character page list from the published versions?",
            parent=self,
        ):
            return
        payload = json.loads(json.dumps(self.manifest))

        def done(manifest: dict) -> None:
            self.manifest = manifest
            self._populate()
            self.progress("Refreshed the all-version character page list.")

        self._run_background(
            "Refreshing all-version character routes...",
            lambda: self.publisher.save_game_manifest(payload),
            done,
        )

    def _close(self) -> None:
        if self.busy:
            messagebox.showinfo("Please wait", "Wait for the current operation to finish.", parent=self)
            return
        if self.dirty and not messagebox.askyesno(
            "Discard changes?", "Close without saving these changes?", parent=self
        ):
            return
        self.destroy()

