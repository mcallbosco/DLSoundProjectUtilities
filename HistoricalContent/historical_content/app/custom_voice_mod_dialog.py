"""Operator dialog for deterministic custom voice-mod imports."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from ..custom_voice_mod import (
    CustomVoiceModError,
    CustomVoiceModSettings,
    TranscriptProvenance,
    discover_transcript_provenance,
)
from ..version_catalog import load_local_catalog


class CustomVoiceModDialog(tk.Toplevel):
    """Collect custom import inputs without exposing any transcription controls."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        data_dir: Path,
        game: str,
        suggested_version: str,
        source2viewer_binary: Path,
        extraction_threads: int,
        on_import: Callable[[CustomVoiceModSettings], None],
    ) -> None:
        super().__init__(parent)
        self.title("Import custom voice mod")
        self.geometry("820x500")
        self.minsize(720, 460)
        self.transient(parent)
        self.grab_set()
        self.data_dir = data_dir.expanduser().resolve()
        self.game = game
        self.source2viewer_binary = source2viewer_binary
        self.extraction_threads = extraction_threads
        self.on_import = on_import
        catalog = load_local_catalog(self.data_dir, self.game)
        self.official_versions = [
            str(entry["id"])
            for entry in catalog.get("versions", [])
            if isinstance(entry, dict) and entry.get("kind", "official") == "official"
        ]
        latest = str(catalog.get("latestVersion") or "")
        preferred_base = (
            latest if latest in self.official_versions
            else self.official_versions[0] if self.official_versions
            else suggested_version
        )
        self.transcript_provenance: TranscriptProvenance | None = None
        self.provenance_var = tk.StringVar(
            value=(
                "Select a clean, committed transcript from its Git checkout. The sibling "
                "metadata.json, repository URL, commit, source path, and SHA-256 are detected "
                "automatically."
            )
        )
        self.vars: dict[str, tk.StringVar] = {
            "version": tk.StringVar(value=f"{preferred_base}-russian-voice-mod"),
            "label": tk.StringVar(value="Russian Voice Mod"),
            "base": tk.StringVar(value=preferred_base),
            "vpk": tk.StringVar(),
            "transcript": tk.StringVar(),
            "overrides": tk.StringVar(),
        }
        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)

        notice = (
            "This importer never uses speech-to-text. Voice MP3s are decoded from the selected "
            "mod VPK and correlated to official base records. Missing transcript tokens are "
            "reported and embedded as blank strings; duplicate candidates use the first match. "
            "Warnings do not block publication. Reusing a custom version ID replaces only its "
            "local generated source and preview."
        )
        ttk.Label(frame, text=notice, wraplength=750, foreground="#8b4513").grid(
            row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12),
        )

        rows = [
            ("version", "Custom version ID", None),
            ("label", "Display label", None),
            ("base", "Official base version", "base"),
            ("vpk", "Mod voice VPK", "vpk"),
            ("transcript", "Pinned transcript VDF/TXT", "transcript"),
            ("overrides", "Correlation overrides JSON (optional)", "json"),
        ]
        for offset, (key, label, picker) in enumerate(rows, start=1):
            ttk.Label(frame, text=label).grid(row=offset, column=0, sticky="w", pady=4)
            if picker == "base":
                ttk.Combobox(
                    frame,
                    textvariable=self.vars[key],
                    values=self.official_versions,
                    state="readonly",
                ).grid(row=offset, column=1, sticky="ew", padx=8, pady=4)
            else:
                ttk.Entry(frame, textvariable=self.vars[key]).grid(
                    row=offset, column=1, sticky="ew", padx=8, pady=4,
                )
            if picker and picker != "base":
                ttk.Button(
                    frame,
                    text="Browse...",
                    command=lambda k=key, p=picker: self._browse(k, p),
                ).grid(row=offset, column=2, pady=4)

        ttk.Label(
            frame,
            textvariable=self.provenance_var,
            wraplength=750,
            foreground="#355070",
        ).grid(row=len(rows) + 1, column=0, columnspan=3, sticky="ew", pady=(12, 4))

        ttk.Label(
            frame,
            text=(
                "Audio is copied only to the custom version's Audio folder. Official Russian "
                "localization remains available for Russian character names, while line text is "
                "embedded from this pinned file."
            ),
            wraplength=750,
        ).grid(row=len(rows) + 2, column=0, columnspan=3, sticky="ew", pady=(8, 4))

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(rows) + 3, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Import and validate", command=self._submit).pack(
            side=tk.LEFT, padx=(8, 0),
        )

    def _browse(self, key: str, picker: str) -> None:
        current = Path(self.vars[key].get() or self.data_dir)
        initial = current.parent if current.suffix else current
        if picker == "vpk":
            selected = filedialog.askopenfilename(
                parent=self,
                initialdir=str(initial),
                filetypes=[("Valve package", "*.vpk"), ("All files", "*.*")],
            )
        else:
            filetypes = (
                [("VDF or text", "*.vdf *.txt"), ("All files", "*.*")]
                if picker == "transcript"
                else [("JSON", "*.json"), ("All files", "*.*")]
            )
            selected = filedialog.askopenfilename(
                parent=self,
                initialdir=str(initial),
                filetypes=filetypes,
            )
        if selected:
            self.vars[key].set(selected)
            if key == "transcript":
                try:
                    self.transcript_provenance = discover_transcript_provenance(Path(selected))
                    provenance = self.transcript_provenance
                    self.provenance_var.set(
                        f"Pinned automatically: {provenance.repository} @ "
                        f"{provenance.revision[:12]} — {provenance.source_path} — "
                        f"SHA-256 {provenance.sha256}"
                    )
                except CustomVoiceModError as exc:
                    self.transcript_provenance = None
                    self.provenance_var.set(str(exc))

    def _submit(self) -> None:
        required = ("version", "label", "base", "vpk", "transcript")
        missing = [key for key in required if not self.vars[key].get().strip()]
        if missing:
            messagebox.showerror(
                "Missing import settings",
                "Complete every required custom import field.",
                parent=self,
            )
            return
        try:
            provenance = discover_transcript_provenance(Path(self.vars["transcript"].get()))
        except CustomVoiceModError as exc:
            messagebox.showerror("Transcript provenance is not valid", str(exc), parent=self)
            return
        if not self.source2viewer_binary.expanduser().is_file():
            messagebox.showerror(
                "Source2Viewer is not configured",
                "Select the Source2Viewer CLI in the main Historical Content window first.",
                parent=self,
            )
            return
        settings = CustomVoiceModSettings(
            data_dir=self.data_dir,
            game=self.game,
            version_id=self.vars["version"].get().strip(),
            label=self.vars["label"].get().strip(),
            based_on_version=self.vars["base"].get().strip(),
            source2viewer_binary=self.source2viewer_binary,
            mod_vpk_path=Path(self.vars["vpk"].get().strip()),
            transcript_path=Path(self.vars["transcript"].get().strip()),
            transcript_metadata_path=provenance.metadata_path,
            transcript_repository=provenance.repository,
            transcript_revision=provenance.revision,
            transcript_source_path=provenance.source_path,
            expected_transcript_sha256=provenance.sha256,
            correlation_overrides_path=(
                Path(self.vars["overrides"].get().strip())
                if self.vars["overrides"].get().strip()
                else None
            ),
            extraction_threads=self.extraction_threads,
        )
        self.destroy()
        self.on_import(settings)
