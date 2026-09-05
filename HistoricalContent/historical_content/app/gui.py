#!/usr/bin/env python3
"""Tkinter GUI for baseline generation and local website preview."""

from __future__ import annotations

import json
import os
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .custom_voice_mod_dialog import CustomVoiceModDialog
from .local_version_dialog import LocalVersionManagerDialog
from .publication_dialog import PublicationDialog
from ..baseline import (
    BaselineSettings, create_baseline, load_json, refresh_preview_categories,
    validate_categories,
)
from ..credentials import (
    CredentialStoreError, delete_saved_api_key, load_saved_api_key,
    resolve_api_key, save_api_key,
)
from ..custom_voice_mod import (
    CustomVoiceModSettings, build_custom_voice_mod,
)
from ..preview import (
    PreviewProcesses, restart_preview_worker, seed_preview, start_preview,
)
from ..transcription import SUPPORTED_MODELS
from ..vpk_pipeline import (
    VpkPipelineResult, VpkPipelineSettings, prepare_vpk_export,
)


from ..settings import APP_DIR, CONFIG_PATH, CREDENTIAL_PATH, DEFAULTS, load_config


class HistoricalContentGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VLViewer Historical Content")
        self.geometry("1120x840")
        self.minsize(960, 700)
        self.config_data = load_config()
        self.last_result = None
        self.last_pipeline: VpkPipelineResult | None = None
        self.preview_processes: PreviewProcesses | None = None
        self.active_operation: str | None = None
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        self.vars: dict[str, tk.Variable] = {}
        rows = [
            ("vpkPath", "Main VPK", "vpk"),
            ("source2viewerBinary", "Source2Viewer CLI", "executable"),
            ("transcriptRepo", "Transcript Git repository", "directory"),
            ("dataDir", "Persistent historical workspace", "directory"),
            ("workerDir", "Content Delivery Worker", "directory"),
            ("websiteDir", "Website project", "directory"),
            (
                "predefinedTranscripts",
                "Predefined official transcripts CSV",
                "csv",
            ),
            ("versionId", "Version ID", None),
            ("label", "Display label", None),
            ("game", "Game ID", None),
        ]
        for row, (key, label, picker) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3)
            variable = tk.StringVar(value=str(self.config_data.get(key, "")))
            self.vars[key] = variable
            ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8, pady=3)
            if picker:
                ttk.Button(frame, text="Browse...", command=lambda k=key: self._browse(k)).grid(row=row, column=2)

        options = ttk.Frame(frame)
        options.grid(row=len(rows), column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Label(options, text="Transcription model").pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value=str(self.config_data["model"]))
        ttk.Combobox(
            options, textvariable=self.model_var,
            values=SUPPORTED_MODELS, state="readonly", width=24,
        ).pack(side=tk.LEFT, padx=6)
        ttk.Label(options, text="Workers").pack(side=tk.LEFT, padx=(16, 2))
        self.workers_var = tk.IntVar(value=int(self.config_data["workers"]))
        ttk.Spinbox(options, from_=1, to=16, textvariable=self.workers_var, width=4).pack(side=tk.LEFT)
        ttk.Label(options, text="Extraction threads").pack(side=tk.LEFT, padx=(16, 2))
        self.extraction_threads_var = tk.IntVar(value=int(self.config_data["extractionThreads"]))
        ttk.Spinbox(options, from_=1, to=64, textvariable=self.extraction_threads_var, width=4).pack(side=tk.LEFT)
        self.transcribe_var = tk.BooleanVar(value=bool(self.config_data["transcribeMissing"]))
        ttk.Checkbutton(options, text="Transcribe missing audio", variable=self.transcribe_var).pack(side=tk.LEFT, padx=14)
        self.audio_var = tk.BooleanVar(value=bool(self.config_data["includeAudio"]))
        ttk.Checkbutton(options, text="Include audio in preview", variable=self.audio_var).pack(side=tk.LEFT)

        pipeline_options = ttk.Frame(frame)
        pipeline_options.grid(row=len(rows) + 1, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        self.phantom_var = tk.BooleanVar(value=bool(self.config_data["includePhantom"]))
        ttk.Checkbutton(pipeline_options, text="Include VDF-only lines", variable=self.phantom_var).pack(side=tk.LEFT)
        self.localization_var = tk.BooleanVar(value=bool(self.config_data["extractLocalization"]))
        ttk.Checkbutton(pipeline_options, text="Generate localization", variable=self.localization_var).pack(side=tk.LEFT, padx=14)
        self.icons_var = tk.BooleanVar(value=bool(self.config_data["extractIcons"]))
        ttk.Checkbutton(pipeline_options, text="Extract icons", variable=self.icons_var).pack(side=tk.LEFT)
        self.name_images_var = tk.BooleanVar(value=bool(self.config_data["extractNameImages"]))
        ttk.Checkbutton(
            pipeline_options,
            text="Extract localized names",
            variable=self.name_images_var,
        ).pack(side=tk.LEFT, padx=(14, 4))
        ttk.Label(pipeline_options, text="Max height").pack(side=tk.LEFT)
        self.name_image_height_var = tk.IntVar(value=int(self.config_data["nameImageMaxHeight"]))
        ttk.Spinbox(
            pipeline_options,
            from_=64,
            to=4096,
            textvariable=self.name_image_height_var,
            width=5,
        ).pack(side=tk.LEFT, padx=(4, 0))
        self.force_extract_var = tk.BooleanVar(value=bool(self.config_data["forceReextract"]))
        ttk.Checkbutton(pipeline_options, text="Force audio re-extraction", variable=self.force_extract_var).pack(side=tk.LEFT, padx=14)

        key_frame = ttk.LabelFrame(frame, text="OpenAI credential", padding=8)
        key_frame.grid(row=len(rows) + 2, column=0, columnspan=3, sticky="ew", pady=6)
        key_frame.columnconfigure(1, weight=1)
        ttk.Label(key_frame, text="API key (optional when no text is missing)").grid(row=0, column=0, sticky="w")
        self.api_key_var = tk.StringVar()
        try:
            saved = load_saved_api_key(CREDENTIAL_PATH)
        except CredentialStoreError:
            saved = None
        if saved:
            self.api_key_var.set(saved)
        ttk.Entry(key_frame, textvariable=self.api_key_var, show="•").grid(row=0, column=1, sticky="ew", padx=8)
        self.remember_var = tk.BooleanVar(value=bool(saved))
        ttk.Checkbutton(key_frame, text="Remember securely for this Windows user", variable=self.remember_var).grid(row=0, column=2)
        ttk.Button(key_frame, text="Forget", command=self._forget_key).grid(row=0, column=3, padx=(8, 0))

        primary_buttons = ttk.Frame(frame)
        primary_buttons.grid(row=len(rows) + 3, column=0, columnspan=3, sticky="ew", pady=(6, 2))
        for column in range(3):
            primary_buttons.columnconfigure(column, weight=1, uniform="actions")
        self.create_button = ttk.Button(primary_buttons, text="Process VPK / regenerate content", command=self._create)
        self.create_button.grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        self.preview_button = ttk.Button(
            primary_buttons,
            text="Seed and start website preview",
            command=self._start_preview,
        )
        self.preview_button.grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        self.categories_button = ttk.Button(
            primary_buttons,
            text="Apply categories to preview",
            command=self._apply_categories,
        )
        self.categories_button.grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        self.local_versions_button = ttk.Button(
            primary_buttons,
            text="Manage local versions...",
            command=self._open_local_versions,
        )
        self.local_versions_button.grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        self.custom_mod_button = ttk.Button(
            primary_buttons,
            text="Import custom voice mod...",
            command=self._open_custom_voice_mod,
        )
        self.custom_mod_button.grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        self.publish_button = ttk.Button(
            primary_buttons,
            text="Publish / manage versions...",
            command=self._open_publication,
        )
        self.publish_button.grid(row=1, column=2, sticky="ew", padx=3, pady=3)

        secondary_buttons = ttk.Frame(frame)
        secondary_buttons.grid(row=len(rows) + 4, column=0, columnspan=3, sticky="ew", pady=(2, 6))
        actions = (
            ("Open categories", self._open_categories),
            ("Open voiceline groups", self._open_groups),
            ("Open character mappings", self._open_mappings),
            ("Open filename overrides", self._open_filename_overrides),
            ("Open display names", self._open_character_names),
            ("Open vocabulary", self._open_vocabulary),
            ("Validate categories", self._validate_categories),
            ("Open version workspace", self._open_workspace),
            ("Save settings", self._save),
        )
        for column in range(3):
            secondary_buttons.columnconfigure(column, weight=1, uniform="actions")
        for index, (label, command) in enumerate(actions):
            ttk.Button(secondary_buttons, text=label, command=command).grid(
                row=index // 3, column=index % 3, sticky="ew", padx=3, pady=3,
            )

        self.status_var = tk.StringVar(value="Ready. Select a VPK to get started.")
        ttk.Label(frame, textvariable=self.status_var).grid(row=len(rows) + 5, column=0, columnspan=3, sticky="w")
        self.log = scrolledtext.ScrolledText(frame, height=18, state=tk.DISABLED)
        self.log.grid(row=len(rows) + 6, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        frame.rowconfigure(len(rows) + 6, weight=1)

    def _begin_operation(self, label: str) -> bool:
        if self.active_operation:
            messagebox.showwarning(
                "Operation still running",
                f"Wait for {self.active_operation} to finish before starting {label}.",
            )
            return False
        self.active_operation = label
        self._set_operation_buttons(tk.DISABLED)
        return True

    def _finish_operation(self) -> None:
        self.active_operation = None
        self._set_operation_buttons(tk.NORMAL)

    def _set_operation_buttons(self, state: str) -> None:
        for button in (
            self.create_button, self.preview_button, self.categories_button,
            self.local_versions_button, self.custom_mod_button, self.publish_button,
        ):
            button.configure(state=state)

    def _browse(self, key: str) -> None:
        current = Path(str(self.vars[key].get() or APP_DIR)).expanduser()
        initial = current.parent if current.suffix else current
        if key == "vpkPath":
            selected = filedialog.askopenfilename(
                initialdir=str(initial),
                title="Select the main VPK",
                filetypes=[("Valve package", "*.vpk"), ("All files", "*.*")],
            )
        elif key == "source2viewerBinary":
            selected = filedialog.askopenfilename(
                initialdir=str(initial),
                title="Select Source2Viewer CLI",
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
            )
        elif key == "predefinedTranscripts":
            selected = filedialog.askopenfilename(
                initialdir=str(initial),
                title="Select predefined official transcript CSV",
                filetypes=[
                    ("Comma-separated values", "*.csv"),
                    ("All files", "*.*"),
                ],
            )
        else:
            selected = filedialog.askdirectory(initialdir=str(initial))
        if selected:
            self.vars[key].set(selected)

    def _log(self, message: str) -> None:
        def append() -> None:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, message.rstrip() + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)
            self.status_var.set(message.rstrip().splitlines()[-1])
        self.after(0, append)

    def _settings_payload(self) -> dict[str, object]:
        return {
            **{key: str(variable.get()).strip() for key, variable in self.vars.items()},
            **{
                key: str(self.config_data.get(key, DEFAULTS[key]))
                for key in (
                    "characterMappings", "topicAliases", "voicelineGroups",
                    "conversationOverrides", "transcriptionVocabulary",
                )
            },
            "model": self.model_var.get(),
            "workers": int(self.workers_var.get()),
            "extractionThreads": int(self.extraction_threads_var.get()),
            "transcribeMissing": self.transcribe_var.get(),
            "includeAudio": self.audio_var.get(),
            "includePhantom": self.phantom_var.get(),
            "extractLocalization": self.localization_var.get(),
            "extractIcons": self.icons_var.get(),
            "extractNameImages": self.name_images_var.get(),
            "nameImageMaxHeight": int(self.name_image_height_var.get()),
            "forceReextract": self.force_extract_var.get(),
        }

    def _save(self, quiet: bool = False) -> None:
        payload = self._settings_payload()
        CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        key = self.api_key_var.get().strip()
        try:
            if self.remember_var.get() and key:
                save_api_key(CREDENTIAL_PATH, key)
            elif not self.remember_var.get():
                delete_saved_api_key(CREDENTIAL_PATH)
        except CredentialStoreError as exc:
            messagebox.showerror("Credential storage", str(exc))
            return
        if not quiet:
            messagebox.showinfo("Saved", f"Settings saved to {CONFIG_PATH}")

    def _forget_key(self) -> None:
        delete_saved_api_key(CREDENTIAL_PATH)
        self.api_key_var.set("")
        self.remember_var.set(False)

    def _source_dir(self) -> Path:
        return (
            Path(str(self.vars["dataDir"].get())).expanduser().resolve()
            / "workspaces"
            / str(self.vars["game"].get()).strip()
            / str(self.vars["versionId"].get()).strip()
            / "source"
        )

    def _baseline_settings(
        self,
        source_dir: Path,
        payload: dict[str, object],
        api_key: str | None,
        transcription_vocabulary: Path,
    ) -> BaselineSettings:
        predefined_value = str(payload["predefinedTranscripts"]).strip()
        return BaselineSettings(
            source_dir=source_dir,
            transcript_repo=Path(str(payload["transcriptRepo"])),
            data_dir=Path(str(payload["dataDir"])),
            version_id=str(payload["versionId"]),
            label=str(payload["label"]),
            game=str(payload["game"]),
            model=str(payload["model"]),
            api_key=api_key,
            transcription_vocabulary=transcription_vocabulary,
            predefined_transcripts=(
                Path(predefined_value) if predefined_value else None
            ),
            transcribe_missing=bool(payload["transcribeMissing"]),
            workers=int(payload["workers"]),
            include_audio=bool(payload["includeAudio"]),
        )

    def _vpk_settings(self) -> VpkPipelineSettings:
        payload = self._settings_payload()
        return VpkPipelineSettings(
            source2viewer_binary=Path(str(payload["source2viewerBinary"])),
            vpk_path=Path(str(payload["vpkPath"])),
            data_dir=Path(str(payload["dataDir"])),
            transcript_repo=Path(str(payload["transcriptRepo"])),
            version_id=str(payload["versionId"]),
            game=str(payload["game"]),
            character_mappings=Path(str(payload["characterMappings"])),
            topic_aliases=Path(str(payload["topicAliases"])),
            voiceline_groups=Path(str(payload["voicelineGroups"])),
            conversation_overrides=Path(str(payload["conversationOverrides"])),
            transcription_vocabulary=Path(str(payload["transcriptionVocabulary"])),
            include_phantom=bool(payload["includePhantom"]),
            extract_localization=bool(payload["extractLocalization"]),
            extract_icons=bool(payload["extractIcons"]),
            extract_name_images=bool(payload["extractNameImages"]),
            name_image_max_height=int(payload["nameImageMaxHeight"]),
            extraction_threads=int(payload["extractionThreads"]),
            force_reextract=bool(payload["forceReextract"]),
        )

    def _create(self) -> None:
        if not self._begin_operation("content regeneration"):
            return
        try:
            self._save(quiet=True)
            payload = self._settings_payload()
            api_key = resolve_api_key(self.api_key_var.get(), CREDENTIAL_PATH)
            vpk_settings = self._vpk_settings()
        except Exception as exc:
            self._finish_operation()
            messagebox.showerror("Invalid settings", str(exc))
            return
        def work() -> None:
            try:
                pipeline = prepare_vpk_export(vpk_settings, self._log)
                self.last_pipeline = pipeline
                result = create_baseline(
                    self._baseline_settings(
                        pipeline.source_dir,
                        payload,
                        api_key,
                        pipeline.transcription_vocabulary,
                    ),
                    self._log,
                )
                self.last_result = result
                self._log(
                    f"Version ready: {result.voiceline_count} voicelines, "
                    f"{result.conversation_line_count} conversation lines, "
                    f"{result.audio_count} referenced audio files."
                )
                self.after(0, lambda: messagebox.showinfo(
                    "Version ready",
                    f"Persistent VPK workspace:\n{pipeline.workspace}\n\n"
                    f"Preview content:\n{result.preview_root}\n\n"
                    f"Publisher source:\n{result.publish_source}\n\n"
                    f"Categories:\n{result.categories_path}\n\n"
                    f"Missing transcripts: {result.missing_transcripts}",
                ))
            except Exception as exc:
                error_message = str(exc)
                self._log(f"ERROR: {error_message}")
                self.after(0, lambda: messagebox.showerror("VPK pipeline failed", error_message))
            finally:
                self.after(0, self._finish_operation)
        threading.Thread(target=work, daemon=True).start()

    def _categories_path(self) -> Path:
        return (
            Path(str(self.vars["transcriptRepo"].get())) / "config" /
            str(self.vars["game"].get()) / "versions" /
            str(self.vars["versionId"].get()) / "categories.json"
        )

    def _open_categories(self) -> None:
        path = self._categories_path()
        if not path.is_file():
            messagebox.showwarning("Not generated", "Process the VPK first.")
            return
        os.startfile(path)

    def _game_config_path(self, filename: str) -> Path:
        return (
            Path(str(self.vars["transcriptRepo"].get())).expanduser().resolve()
            / "config"
            / str(self.vars["game"].get()).strip()
            / filename
        )

    def _open_groups(self) -> None:
        path = self._game_config_path("voiceline-groups.json")
        if not path.is_file():
            messagebox.showwarning("Not generated", "Process the VPK once to create the editable group file.")
            return
        os.startfile(path)

    def _open_mappings(self) -> None:
        path = self._game_config_path("character-mappings.json")
        if not path.is_file():
            messagebox.showwarning("Not generated", "Process the VPK once to create the editable mapping file.")
            return
        os.startfile(path)

    def _open_filename_overrides(self) -> None:
        path = (
            self._game_config_path("versions")
            / str(self.vars["versionId"].get()).strip()
            / "audio-filename-overrides.json"
        )
        if not path.is_file():
            messagebox.showwarning(
                "Not generated",
                "Process the VPK once to create the editable filename-override file.",
            )
            return
        os.startfile(path)

    def _open_character_names(self) -> None:
        path = self._game_config_path("character-names.json")
        if not path.is_file():
            messagebox.showwarning(
                "Not generated",
                "Process the VPK once to create the editable display-name file.",
            )
            return
        os.startfile(path)

    def _open_vocabulary(self) -> None:
        path = self._game_config_path("transcription-vocabulary.json")
        if not path.is_file():
            messagebox.showwarning(
                "Not generated",
                "Process the VPK once to create the editable transcription vocabulary.",
            )
            return
        os.startfile(path)

    def _open_workspace(self) -> None:
        path = self._source_dir().parent
        if not path.is_dir():
            messagebox.showwarning("Not generated", "Process the VPK first.")
            return
        os.startfile(path)

    def _validate_categories(self) -> None:
        try:
            path = self._categories_path()
            payload = load_json(path)
            characters: set[str] = set()
            source = self._source_dir()
            voice_path = source / "all_voicelines.json"
            if not voice_path.is_file():
                voice_path = source / "voicelines.json"
            voices = load_json(voice_path)
            if isinstance(voices, dict):
                characters.update(str(key) for key in voices.keys())
            errors, warnings = validate_categories(payload, characters)
            detail = "\n".join([*(f"WARNING: {value}" for value in warnings), *(f"ERROR: {value}" for value in errors)])
            self._log(detail or "Categories are valid.")
            if errors:
                messagebox.showerror("Categories invalid", detail)
            else:
                messagebox.showinfo("Categories valid", detail or "No errors or warnings.")
        except Exception as exc:
            messagebox.showerror("Category validation", str(exc))

    def _start_preview(self) -> None:
        if not self._begin_operation("local preview seeding"):
            return
        preview_root = (
            self.last_result.preview_root if self.last_result else
            Path(str(self.vars["dataDir"].get())) / "preview-content"
        )
        version = f"preview-{self.vars['versionId'].get()}"
        worker_dir = Path(str(self.vars["workerDir"].get())).resolve()
        website_dir = Path(str(self.vars["websiteDir"].get())).resolve()
        game = str(self.vars["game"].get())
        def work() -> None:
            try:
                if self.preview_processes:
                    self.preview_processes.stop()
                    self.preview_processes = None
                seed_preview(worker_dir, preview_root.resolve(), self._log)
                self.preview_processes = start_preview(
                    worker_dir, website_dir, game, self._log
                )
                url = f"http://localhost:3000/?version={version}"
                self._log(f"LOCAL PREVIEW: {url}")
                self.after(2500, lambda: webbrowser.open(url))
            except Exception as exc:
                error_message = str(exc)
                self._log(f"ERROR: {error_message}")
                self.after(
                    0,
                    lambda message=error_message: messagebox.showerror("Preview failed", message),
                )
            finally:
                self.after(0, self._finish_operation)
        threading.Thread(target=work, daemon=True).start()

    def _apply_categories(self) -> None:
        if not self._begin_operation("category refresh"):
            return
        source_dir = self._source_dir()
        transcript_repo = Path(str(self.vars["transcriptRepo"].get()))
        data_dir = Path(str(self.vars["dataDir"].get()))
        version_id = str(self.vars["versionId"].get())
        game = str(self.vars["game"].get())
        worker_dir = Path(str(self.vars["workerDir"].get())).resolve()
        preview_root = data_dir.resolve() / "preview-content"
        def work() -> None:
            try:
                refresh_preview_categories(
                    source_dir=source_dir,
                    transcript_repo=transcript_repo,
                    data_dir=data_dir,
                    version_id=version_id,
                    game=game,
                    progress=self._log,
                )
                if self.preview_processes:
                    if self.preview_processes.worker.poll() is None:
                        self.preview_processes.worker.terminate()
                        self.preview_processes.worker.wait(timeout=10)
                    seed_preview(
                        worker_dir, preview_root, self._log,
                        reset=False, suffix="categories.json",
                    )
                    restart_preview_worker(self.preview_processes, worker_dir, self._log)
                    self._log("Categories updated. Refresh the open website page.")
                else:
                    self._log("Categories updated on disk. Start the website preview when ready.")
            except Exception as exc:
                error_message = str(exc)
                self._log(f"ERROR: {error_message}")
                self.after(
                    0,
                    lambda message=error_message: messagebox.showerror(
                        "Category refresh failed", message
                    ),
                )
            finally:
                self.after(0, self._finish_operation)
        threading.Thread(target=work, daemon=True).start()

    def _open_custom_voice_mod(self) -> None:
        if self.active_operation:
            messagebox.showwarning(
                "Operation still running",
                f"Wait for {self.active_operation} to finish before importing a custom mod.",
            )
            return
        CustomVoiceModDialog(
            self,
            data_dir=Path(str(self.vars["dataDir"].get())),
            game=str(self.vars["game"].get()).strip(),
            suggested_version=str(self.vars["versionId"].get()).strip(),
            source2viewer_binary=Path(str(self.vars["source2viewerBinary"].get())),
            extraction_threads=int(self.extraction_threads_var.get()),
            on_import=self._run_custom_voice_mod_import,
        )

    def _run_custom_voice_mod_import(self, settings: CustomVoiceModSettings) -> None:
        if not self._begin_operation("the custom voice-mod import"):
            return

        def work() -> None:
            try:
                result = build_custom_voice_mod(settings, self._log)
                if result.warnings:
                    warning_lines = [
                        (
                            f"{warning['audioPath']} [{warning['stage']}]: "
                            f"{warning['reason']}"
                        )
                        for warning in result.warnings[:12]
                    ]
                    if len(result.warnings) > len(warning_lines):
                        warning_lines.append(
                            f"...and {len(result.warnings) - len(warning_lines)} more warning(s)."
                        )
                    report_path = result.output_dir / "custom-import-report.json"
                    detail = (
                        "The custom version is ready and publication remains available. Missing "
                        "transcripts were embedded as blank strings; recordings without a safe "
                        "base match were excluded. Speech-to-text was not used.\n\n"
                        + "\n".join(warning_lines)
                        + f"\n\nFull report:\n{report_path}"
                    )
                    self.after(0, lambda message=detail: messagebox.showwarning(
                        "Custom import ready with warnings", message,
                    ))
                else:
                    detail = (
                        f"Custom version ready:\n{result.output_dir}\n\n"
                        f"VPK extraction workspace: {result.extraction_workspace}\n"
                        f"Matched mod audio: {result.audio_files}\n"
                        f"Voice-line records: {result.voiceline_records}\n"
                        f"Conversation records: {result.conversation_records}\n\n"
                        "All transcripts came from the pinned file; speech-to-text was not used."
                    )
                    self.after(0, lambda message=detail: messagebox.showinfo(
                        "Custom voice mod ready", message,
                    ))
            except Exception as exc:
                error_message = str(exc)
                self._log(f"ERROR: {error_message}")
                self.after(0, lambda message=error_message: messagebox.showerror(
                    "Custom voice-mod import failed", message,
                ))
            finally:
                self.after(0, self._finish_operation)

        threading.Thread(target=work, daemon=True).start()

    def _open_publication(self) -> None:
        if self.active_operation:
            messagebox.showwarning(
                "Operation still running",
                f"Wait for {self.active_operation} to finish before opening publication controls.",
            )
            return
        source = (
            Path(str(self.vars["dataDir"].get())).expanduser().resolve()
            / "generated"
            / str(self.vars["versionId"].get()).strip()
        )
        if not source.is_dir():
            messagebox.showwarning(
                "Version not generated",
                "Process the VPK before opening publication controls.",
            )
            return
        PublicationDialog(
            self,
            source_dir=source,
            game_categories_path=self._game_config_path("categories.json"),
            game=str(self.vars["game"].get()).strip(),
            version=str(self.vars["versionId"].get()).strip(),
            label=str(self.vars["label"].get()).strip(),
            progress=self._log,
        )

    def _open_local_versions(self) -> None:
        if self.active_operation:
            messagebox.showwarning(
                "Operation still running",
                f"Wait for {self.active_operation} to finish before managing local versions.",
            )
            return
        data_dir = Path(str(self.vars["dataDir"].get())).expanduser().resolve()
        game = str(self.vars["game"].get()).strip()
        if not (data_dir / "preview-content" / game / "manifest.json").is_file():
            messagebox.showwarning(
                "No local versions",
                "Process at least one VPK before managing local versions.",
            )
            return
        LocalVersionManagerDialog(
            self,
            data_dir=data_dir,
            game=game,
            progress=self._log,
        )

    def _close(self) -> None:
        if self.preview_processes:
            self.preview_processes.stop()
        self.destroy()


def main() -> None:
    HistoricalContentGUI().mainloop()


if __name__ == "__main__":
    main()
