#!/usr/bin/env python3
"""Tkinter interface for validating and publishing VLViewer content versions."""

from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable

try:
    from .publisher import (
        PublishPlan,
        PublisherError,
        PublisherSettings,
        R2Publisher,
        ValidationReport,
        format_bytes,
        validate_version_source,
    )
except ImportError:
    from publisher import (
        PublishPlan,
        PublisherError,
        PublisherSettings,
        R2Publisher,
        ValidationReport,
        format_bytes,
        validate_version_source,
    )

try:
    from .credential_store import (
        CredentialStoreError,
        delete_credentials,
        is_supported as credential_saving_supported,
        load_credentials,
        save_credentials,
    )
    from .dependencies import install_requirements, missing_modules
except ImportError:
    from credential_store import (
        CredentialStoreError,
        delete_credentials,
        is_supported as credential_saving_supported,
        load_credentials,
        save_credentials,
    )
    from dependencies import install_requirements, missing_modules


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
CREDENTIAL_PATH = APP_DIR / "credentials.dpapi"
STATE_DIR = APP_DIR / ".state"
DEFAULT_CONFIG = {
    "source_dir": "",
    "game": "deadlock",
    "version": "",
    "label": "",
    "bucket": "",
    "endpoint_url": "",
    "cdn_base_url": "https://cdn.vlviewer.com",
    "zone_id": "",
    "concurrency": 12,
    "promote_to_latest": True,
    "hidden": False,
}


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.is_file():
        try:
            value = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                config.update(value)
        except Exception:
            pass
    return config


class PublisherGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("VLViewer Content Publisher")
        self.minsize(920, 720)
        self.config_data = load_config()
        self.saved_credentials: dict[str, str] = {}
        self.credential_load_error = ""
        try:
            self.saved_credentials = load_credentials(CREDENTIAL_PATH)
        except CredentialStoreError as exc:
            self.credential_load_error = str(exc)
        self._busy = False
        self._build_ui()
        if self.credential_load_error:
            self._append_log(
                "WARNING: Saved credentials could not be loaded: "
                + self.credential_load_error
            )

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(4, weight=1)

        source_box = ttk.LabelFrame(root, text="Content version", padding=10)
        source_box.grid(row=0, column=0, columnspan=3, sticky="ew")
        source_box.columnconfigure(1, weight=1)

        self.source_var = tk.StringVar(value=self.config_data.get("source_dir", ""))
        self.game_var = tk.StringVar(value=self.config_data.get("game", "deadlock"))
        self.version_var = tk.StringVar(value=self.config_data.get("version", ""))
        self.label_var = tk.StringVar(value=self.config_data.get("label", ""))

        ttk.Label(source_box, text="Version folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(source_box, textvariable=self.source_var).grid(
            row=0, column=1, padx=6, sticky="ew"
        )
        ttk.Button(source_box, text="Browse...", command=self._browse_source).grid(
            row=0, column=2
        )

        ttk.Label(source_box, text="Game key:").grid(row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Entry(source_box, textvariable=self.game_var, width=24).grid(
            row=1, column=1, padx=6, pady=(7, 0), sticky="w"
        )

        ttk.Label(source_box, text="Version ID:").grid(row=2, column=0, sticky="w", pady=(7, 0))
        version_row = ttk.Frame(source_box)
        version_row.grid(row=2, column=1, columnspan=2, padx=6, pady=(7, 0), sticky="ew")
        version_row.columnconfigure(0, weight=1)
        ttk.Entry(version_row, textvariable=self.version_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(version_row, text="Use today's date", command=self._suggest_version).grid(
            row=0, column=1, padx=(6, 0)
        )

        ttk.Label(source_box, text="Display label:").grid(
            row=3, column=0, sticky="w", pady=(7, 0)
        )
        ttk.Entry(source_box, textvariable=self.label_var).grid(
            row=3, column=1, columnspan=2, padx=6, pady=(7, 0), sticky="ew"
        )

        cloud_box = ttk.LabelFrame(root, text="Cloudflare R2 and CDN", padding=10)
        cloud_box.grid(row=1, column=0, columnspan=3, pady=(10, 0), sticky="ew")
        cloud_box.columnconfigure(1, weight=1)
        cloud_box.columnconfigure(3, weight=1)

        self.bucket_var = tk.StringVar(value=self.config_data.get("bucket", ""))
        self.endpoint_var = tk.StringVar(value=self.config_data.get("endpoint_url", ""))
        self.cdn_var = tk.StringVar(
            value=self.config_data.get("cdn_base_url", "https://cdn.vlviewer.com")
        )
        self.zone_var = tk.StringVar(value=self.config_data.get("zone_id", ""))
        self.concurrency_var = tk.StringVar(
            value=str(self.config_data.get("concurrency", 12))
        )
        self.promote_var = tk.BooleanVar(
            value=bool(self.config_data.get("promote_to_latest", True))
        )
        self.hidden_var = tk.BooleanVar(value=bool(self.config_data.get("hidden", False)))

        ttk.Label(cloud_box, text="R2 bucket:").grid(row=0, column=0, sticky="w")
        ttk.Entry(cloud_box, textvariable=self.bucket_var).grid(
            row=0, column=1, padx=6, sticky="ew"
        )
        ttk.Label(cloud_box, text="Upload workers:").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(
            cloud_box, from_=1, to=64, textvariable=self.concurrency_var, width=8
        ).grid(row=0, column=3, padx=6, sticky="w")

        ttk.Label(cloud_box, text="R2 endpoint URL:").grid(
            row=1, column=0, sticky="w", pady=(7, 0)
        )
        ttk.Entry(cloud_box, textvariable=self.endpoint_var).grid(
            row=1, column=1, columnspan=3, padx=6, pady=(7, 0), sticky="ew"
        )

        ttk.Label(cloud_box, text="Public CDN base:").grid(
            row=2, column=0, sticky="w", pady=(7, 0)
        )
        ttk.Entry(cloud_box, textvariable=self.cdn_var).grid(
            row=2, column=1, padx=6, pady=(7, 0), sticky="ew"
        )
        ttk.Label(cloud_box, text="Cloudflare Zone ID:").grid(
            row=2, column=2, sticky="w", pady=(7, 0)
        )
        ttk.Entry(cloud_box, textvariable=self.zone_var).grid(
            row=2, column=3, padx=6, pady=(7, 0), sticky="ew"
        )
        self.promote_checkbox = ttk.Checkbutton(
            cloud_box,
            text="Set this version as latest after publishing",
            variable=self.promote_var,
        )
        self.promote_checkbox.grid(row=3, column=0, columnspan=2, pady=(8, 0), sticky="w")
        ttk.Checkbutton(
            cloud_box,
            text="Hide this version from normal version selectors",
            variable=self.hidden_var,
            command=self._on_hidden_toggle,
        ).grid(row=3, column=2, columnspan=2, pady=(8, 0), sticky="w")
        self._on_hidden_toggle()

        credential_box = ttk.LabelFrame(root, text="Credentials", padding=10)
        credential_box.grid(row=2, column=0, columnspan=3, pady=(10, 0), sticky="ew")
        credential_box.columnconfigure(1, weight=1)
        credential_box.columnconfigure(3, weight=1)

        self.access_key_var = tk.StringVar(
            value=os.environ.get("R2_ACCESS_KEY_ID")
            or self.saved_credentials.get("r2_access_key_id", "")
        )
        self.secret_key_var = tk.StringVar(
            value=os.environ.get("R2_SECRET_ACCESS_KEY")
            or self.saved_credentials.get("r2_secret_access_key", "")
        )
        self.api_token_var = tk.StringVar(
            value=os.environ.get("CLOUDFLARE_API_TOKEN")
            or self.saved_credentials.get("cloudflare_api_token", "")
        )
        self.remember_credentials_var = tk.BooleanVar(
            value=CREDENTIAL_PATH.is_file() and credential_saving_supported()
        )

        ttk.Label(credential_box, text="R2 Access Key ID:").grid(row=0, column=0, sticky="w")
        ttk.Entry(credential_box, textvariable=self.access_key_var, show="•").grid(
            row=0, column=1, padx=6, sticky="ew"
        )
        ttk.Label(credential_box, text="R2 Secret Key:").grid(row=0, column=2, sticky="w")
        ttk.Entry(credential_box, textvariable=self.secret_key_var, show="•").grid(
            row=0, column=3, padx=6, sticky="ew"
        )
        ttk.Label(credential_box, text="Cache purge API token:").grid(
            row=1, column=0, sticky="w", pady=(7, 0)
        )
        ttk.Entry(credential_box, textvariable=self.api_token_var, show="•").grid(
            row=1, column=1, columnspan=3, padx=6, pady=(7, 0), sticky="ew"
        )
        ttk.Checkbutton(
            credential_box,
            text="Remember credentials securely for this Windows user",
            variable=self.remember_credentials_var,
            state=tk.NORMAL if credential_saving_supported() else tk.DISABLED,
        ).grid(row=2, column=0, columnspan=3, pady=(8, 0), sticky="w")
        ttk.Button(
            credential_box,
            text="Forget saved credentials",
            command=self._forget_saved_credentials,
        ).grid(row=2, column=3, padx=6, pady=(8, 0), sticky="e")

        button_row = ttk.Frame(root)
        button_row.grid(row=3, column=0, columnspan=3, pady=10, sticky="ew")
        self.action_buttons: list[ttk.Button] = []
        actions = [
            ("Save settings", self._save_config),
            ("Install/repair requirements", self._install_requirements),
            ("Validate locally", self._validate),
            ("Test R2 connection", self._test_connection),
            ("Compare / dry run", self._dry_run),
            ("Publish", self._publish),
            ("Manage versions...", self._manage_versions),
        ]
        for index, (label, command) in enumerate(actions):
            button = ttk.Button(button_row, text=label, command=command)
            button.pack(side=tk.LEFT, padx=(0 if index == 0 else 6, 0))
            self.action_buttons.append(button)

        output_pane = ttk.Panedwindow(root, orient=tk.VERTICAL)
        output_pane.grid(row=4, column=0, columnspan=3, sticky="nsew")

        summary_frame = ttk.LabelFrame(output_pane, text="Summary", padding=6)
        self.summary = ttk.Treeview(
            summary_frame,
            columns=("value",),
            show="tree headings",
            height=8,
        )
        self.summary.heading("#0", text="Item")
        self.summary.heading("value", text="Value")
        self.summary.column("#0", width=300, stretch=True)
        self.summary.column("value", width=180, anchor="e")
        self.summary.pack(fill=tk.BOTH, expand=True)
        output_pane.add(summary_frame, weight=1)

        log_frame = ttk.LabelFrame(output_pane, text="Activity", padding=6)
        self.log = scrolledtext.ScrolledText(log_frame, height=14, wrap=tk.WORD, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True)
        output_pane.add(log_frame, weight=2)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status_var, anchor="w").grid(
            row=5, column=0, columnspan=3, pady=(8, 0), sticky="ew"
        )

    def _browse_source(self) -> None:
        selected = filedialog.askdirectory(
            title="Select the existing game version folder",
            initialdir=self.source_var.get() or None,
        )
        if selected:
            self.source_var.set(selected)

    def _suggest_version(self) -> None:
        game = self.game_var.get().strip().lower() or "game"
        today = date.today()
        self.version_var.set(f"{game}-{today.isoformat()}")
        self.label_var.set(today.strftime("%B %d, %Y").replace(" 0", " "))

    def _config_payload(self) -> dict:
        return {
            "source_dir": self.source_var.get().strip(),
            "game": self.game_var.get().strip(),
            "version": self.version_var.get().strip(),
            "label": self.label_var.get().strip(),
            "bucket": self.bucket_var.get().strip(),
            "endpoint_url": self.endpoint_var.get().strip(),
            "cdn_base_url": self.cdn_var.get().strip(),
            "zone_id": self.zone_var.get().strip(),
            "concurrency": int(self.concurrency_var.get().strip() or "12"),
            "promote_to_latest": self.promote_var.get(),
            "hidden": self.hidden_var.get(),
        }

    def _save_config(self) -> None:
        try:
            payload = self._config_payload()
            CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self._update_saved_credentials()
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self._append_log(f"Saved non-secret settings to {CONFIG_PATH}.")

    def _credential_payload(self) -> dict[str, str]:
        return {
            "r2_access_key_id": self.access_key_var.get().strip(),
            "r2_secret_access_key": self.secret_key_var.get().strip(),
            "cloudflare_api_token": self.api_token_var.get().strip(),
        }

    def _update_saved_credentials(self) -> None:
        if not self.remember_credentials_var.get():
            delete_credentials(CREDENTIAL_PATH)
            self._append_log("Saved credential file removed; current fields remain in memory.")
            return
        credentials = self._credential_payload()
        access_key = credentials["r2_access_key_id"]
        secret_key = credentials["r2_secret_access_key"]
        if bool(access_key) != bool(secret_key):
            raise CredentialStoreError(
                "Enter both the R2 Access Key ID and R2 Secret Key before saving credentials."
            )
        if not access_key and not credentials["cloudflare_api_token"]:
            raise CredentialStoreError("Enter credentials before enabling credential saving.")
        save_credentials(CREDENTIAL_PATH, credentials)
        self._append_log(
            "Saved credentials with Windows DPAPI for the current Windows user."
        )

    def _forget_saved_credentials(self) -> None:
        if not messagebox.askyesno(
            "Forget saved credentials",
            "Delete the encrypted saved credentials and clear the credential fields?",
        ):
            return
        delete_credentials(CREDENTIAL_PATH)
        self.remember_credentials_var.set(False)
        self.access_key_var.set("")
        self.secret_key_var.set("")
        self.api_token_var.set("")
        for name in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CLOUDFLARE_API_TOKEN"):
            os.environ.pop(name, None)
        self._append_log("Forgot saved credentials and cleared the current fields.")

    def _install_requirements(self) -> None:
        missing = missing_modules()
        prompt = (
            "The required R2 modules are missing: " + ", ".join(missing)
            if missing
            else "The publisher requirements are already installed. Reinstall/repair them?"
        )
        if not messagebox.askyesno("Publisher requirements", prompt):
            return

        def action() -> None:
            try:
                install_requirements(self._append_log)
            except Exception as exc:
                raise PublisherError(f"Could not install publisher requirements: {exc}") from exc
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "Publisher requirements",
                    "The R2 upload requirements are installed.",
                ),
            )

        self._run_background("Installing publisher requirements...", action)

    def _settings(self, require_cloud: bool) -> PublisherSettings:
        if require_cloud:
            missing = missing_modules()
            if missing:
                raise PublisherError(
                    "R2 upload support is not installed. Click "
                    "'Install/repair requirements', then try again."
                )
        try:
            concurrency = int(self.concurrency_var.get().strip() or "12")
        except ValueError as exc:
            raise PublisherError("Upload workers must be a whole number.") from exc
        settings = PublisherSettings(
            source_dir=Path(self.source_var.get().strip()),
            game=self.game_var.get(),
            version=self.version_var.get(),
            label=self.label_var.get(),
            bucket=self.bucket_var.get() if require_cloud else "",
            endpoint_url=self.endpoint_var.get() if require_cloud else "",
            cdn_base_url=self.cdn_var.get(),
            zone_id=self.zone_var.get(),
            state_dir=STATE_DIR,
            concurrency=concurrency,
            promote_to_latest=self.promote_var.get(),
            hidden=self.hidden_var.get(),
        )
        if require_cloud:
            os.environ["R2_ACCESS_KEY_ID"] = self.access_key_var.get().strip()
            os.environ["R2_SECRET_ACCESS_KEY"] = self.secret_key_var.get().strip()
            token = self.api_token_var.get().strip()
            if token:
                os.environ["CLOUDFLARE_API_TOKEN"] = token
            else:
                os.environ.pop("CLOUDFLARE_API_TOKEN", None)
        return settings

    def _on_hidden_toggle(self) -> None:
        if self.hidden_var.get():
            self.promote_var.set(False)
            self.promote_checkbox.configure(state=tk.DISABLED)
        else:
            self.promote_checkbox.configure(state=tk.NORMAL)

    def _append_log(self, message: str) -> None:
        def append() -> None:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, message.rstrip() + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)

        self.after(0, append)

    def _replace_summary(self, rows: list[tuple[str, str]]) -> None:
        def replace() -> None:
            self.summary.delete(*self.summary.get_children())
            for key, value in rows:
                self.summary.insert("", tk.END, text=key, values=(value,))

        self.after(0, replace)

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy

        def update() -> None:
            self.status_var.set(status)
            state = tk.DISABLED if busy else tk.NORMAL
            for button in self.action_buttons:
                button.configure(state=state)

        self.after(0, update)

    def _run_background(self, status: str, action: Callable[[], None]) -> None:
        if self._busy:
            return
        self._set_busy(True, status)

        def run() -> None:
            try:
                action()
            except PublisherError as exc:
                message = str(exc)
                self._append_log(f"ERROR: {message}")
                self.after(
                    0,
                    lambda message=message: messagebox.showerror("Publisher error", message),
                )
            except Exception as exc:
                message = str(exc)
                self._append_log(f"UNEXPECTED ERROR: {message}")
                self.after(
                    0,
                    lambda message=message: messagebox.showerror("Unexpected error", message),
                )
            finally:
                self._set_busy(False, "Ready")

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _validation_rows(report: ValidationReport) -> list[tuple[str, str]]:
        return [
            ("Validation", "Passed" if report.valid else "Failed"),
            ("Files", f"{len(report.files):,}"),
            ("Total size", format_bytes(report.total_bytes)),
            ("JSON files (mutable)", f"{report.json_file_count:,}"),
            ("Binary files (immutable)", f"{report.immutable_file_count:,}"),
            ("Referenced audio", f"{report.referenced_audio_count:,}"),
            ("Audio files", f"{report.audio_file_count:,}"),
            ("Unreferenced audio", f"{report.orphan_audio_count:,}"),
            ("Errors / warnings", f"{len(report.errors)} / {len(report.warnings)}"),
        ]

    def _show_validation(self, report: ValidationReport) -> None:
        self._replace_summary(self._validation_rows(report))
        for warning in report.warnings:
            self._append_log(f"WARNING: {warning}")
        for error in report.errors:
            self._append_log(f"ERROR: {error}")
        if report.valid:
            self._append_log(
                f"Validation passed: {len(report.files):,} files, {format_bytes(report.total_bytes)}."
            )

    def _validate(self) -> None:
        try:
            settings = self._settings(require_cloud=False)
        except PublisherError as exc:
            messagebox.showerror("Publisher error", str(exc))
            return

        def action() -> None:
            self._append_log(f"Validating {settings.source_dir}...")
            report = validate_version_source(settings.source_dir)
            self._show_validation(report)

        self._run_background("Validating local content...", action)

    def _test_connection(self) -> None:
        try:
            settings = self._settings(require_cloud=True)
        except PublisherError as exc:
            messagebox.showerror("Publisher error", str(exc))
            return

        def action() -> None:
            publisher = R2Publisher(settings, self._append_log)
            publisher.test_connection()

        self._run_background("Testing R2 connection...", action)

    @staticmethod
    def _plan_rows(plan: PublishPlan) -> list[tuple[str, str]]:
        upload_bytes = sum(item.size for item in plan.upload_records)
        rows = PublisherGUI._validation_rows(plan.validation)
        rows.extend(
            [
                ("New files to upload", f"{len(plan.upload_new):,}"),
                ("Changed JSON to replace", f"{len(plan.upload_changed_json):,}"),
                ("Unchanged files to skip", f"{len(plan.unchanged):,}"),
                ("Upload size", format_bytes(upload_bytes)),
                ("Immutable binary conflicts", f"{len(plan.immutable_conflicts):,}"),
                ("Remote-only JSON retained", f"{len(plan.remote_only_json):,}"),
            ]
        )
        return rows

    def _show_plan(self, plan: PublishPlan) -> None:
        self._replace_summary(self._plan_rows(plan))
        self._show_validation_messages_only(plan.validation)
        for item, _remote in plan.immutable_conflicts:
            self._append_log(f"ERROR: immutable binary differs remotely: {item.relative_path}")
        if plan.remote_only_json:
            self._append_log(
                f"WARNING: {len(plan.remote_only_json)} remote-only JSON object(s) are not deleted automatically."
            )
        self._append_log(
            f"Dry run complete: {len(plan.upload_new):,} new, "
            f"{len(plan.upload_changed_json):,} changed JSON, "
            f"{len(plan.unchanged):,} unchanged."
        )

    def _show_validation_messages_only(self, report: ValidationReport) -> None:
        for warning in report.warnings:
            self._append_log(f"WARNING: {warning}")
        for error in report.errors:
            self._append_log(f"ERROR: {error}")

    def _dry_run(self) -> None:
        try:
            settings = self._settings(require_cloud=True)
        except PublisherError as exc:
            messagebox.showerror("Publisher error", str(exc))
            return

        def action() -> None:
            self._append_log(f"Comparing {settings.game}/{settings.version} with R2...")
            publisher = R2Publisher(settings, self._append_log)
            plan = publisher.create_plan()
            self._show_plan(plan)

        self._run_background("Building publish plan...", action)

    def _publish(self) -> None:
        try:
            settings = self._settings(require_cloud=True)
        except PublisherError as exc:
            messagebox.showerror("Publisher error", str(exc))
            return
        version = self.version_var.get().strip()
        bucket = self.bucket_var.get().strip()
        confirmed = messagebox.askyesno(
            "Confirm publish",
            f"Publish version {version!r} to R2 bucket {bucket!r}?\n\n"
            "Existing JSON may be replaced. Existing binary object paths will be protected.\n\n"
            f"Hidden from selectors: {'Yes' if settings.hidden else 'No'}",
            icon=messagebox.WARNING,
        )
        if not confirmed:
            return

        def action() -> None:
            publisher = R2Publisher(settings, self._append_log)
            plan = publisher.create_plan()
            self._show_plan(plan)
            if not plan.can_publish:
                raise PublisherError("The dry-run plan is not safe to publish.")
            result = publisher.publish(plan)
            self._append_log(
                f"Success: revision {result['contentRevision']}, "
                f"{result['uploaded']:,} uploaded, {result['skipped']:,} skipped."
            )
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "Publish complete",
                    f"Published {settings.game}/{settings.version}\n"
                    f"Content revision: {result['contentRevision']}\n"
                    f"Uploaded: {result['uploaded']:,}\nSkipped: {result['skipped']:,}",
                ),
            )

        self._run_background("Publishing content...", action)

    def _manage_versions(self) -> None:
        try:
            settings = self._settings(require_cloud=True)
        except PublisherError as exc:
            messagebox.showerror("Publisher error", str(exc))
            return
        publisher = R2Publisher(settings, self._append_log)
        VersionManagerDialog(self, publisher)


class VersionManagerDialog(tk.Toplevel):
    """Edit public version visibility, ordering, and latest selection."""

    def __init__(self, parent: PublisherGUI, publisher: R2Publisher) -> None:
        super().__init__(parent)
        self.parent = parent
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

        columns = ("position", "id", "label", "hidden", "latest", "revision", "updated")
        self.tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        headings = {
            "position": "Order",
            "id": "Version ID",
            "label": "Label",
            "hidden": "Hidden",
            "latest": "Latest",
            "revision": "Revision",
            "updated": "Updated",
        }
        widths = {
            "position": 55,
            "id": 190,
            "label": 170,
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
        index, version = selected
        version_id = str(version.get("id"))
        version["hidden"] = False
        versions = self.manifest["versions"]
        if index != 0:
            versions.insert(0, versions.pop(index))
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
            self.parent._append_log("Saved published version visibility, order, and latest selection.")

        self._run_background(
            "Saving public version manifest...",
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


if __name__ == "__main__":
    PublisherGUI().mainloop()
