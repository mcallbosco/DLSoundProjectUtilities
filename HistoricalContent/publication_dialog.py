"""Production publishing controls hosted by the Historical Content application."""

from __future__ import annotations

import json
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, simpledialog, ttk


UTILITIES_DIR = Path(__file__).resolve().parent.parent
if str(UTILITIES_DIR) not in sys.path:
    sys.path.insert(0, str(UTILITIES_DIR))

try:
    from ContentPublisher.credential_store import (
        CredentialStoreError,
        delete_credentials,
        is_supported as credential_saving_supported,
        load_credentials,
        save_credentials,
    )
    from ContentPublisher.publisher import (
        PublishPlan,
        PublisherError,
        PublisherSettings,
        R2Publisher,
        ValidationReport,
        format_bytes,
        validate_publisher_source,
    )
    from ContentPublisher.publisher_gui import VersionManagerDialog
except ImportError:
    from credential_store import (
        CredentialStoreError,
        delete_credentials,
        is_supported as credential_saving_supported,
        load_credentials,
        save_credentials,
    )
    from publisher import (
        PublishPlan,
        PublisherError,
        PublisherSettings,
        R2Publisher,
        ValidationReport,
        format_bytes,
        validate_publisher_source,
    )
    from publisher_gui import VersionManagerDialog

PUBLISHER_DIR = UTILITIES_DIR / "ContentPublisher"
CONFIG_PATH = PUBLISHER_DIR / "config.json"
CREDENTIAL_PATH = PUBLISHER_DIR / "credentials.dpapi"
STATE_DIR = PUBLISHER_DIR / ".state"
DEFAULTS = {
    "bucket": "",
    "endpoint_url": "",
    "cdn_base_url": "https://cdn.vlviewer.com",
    "zone_id": "",
    "concurrency": 12,
    "promote_to_latest": False,
    "hidden": True,
}


def _load_config() -> dict[str, object]:
    result = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict):
                result.update(payload)
        except Exception:
            pass
    return result


def _local_publish_versions(source_dir: Path, game: str) -> tuple[list[dict[str, object]], str]:
    """Return generated versions in local catalog order, followed by uncataloged folders."""
    generated_root = source_dir.parent
    catalog_path = generated_root.parent / "catalogs" / f"{game}.json"
    catalog: dict[str, object] = {}
    if catalog_path.is_file():
        try:
            value = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                catalog = value
        except Exception:
            catalog = {}

    result: list[dict[str, object]] = []
    seen: set[str] = set()
    catalog_versions = catalog.get("versions", [])
    if isinstance(catalog_versions, list):
        for item in catalog_versions:
            if not isinstance(item, dict):
                continue
            version_id = str(item.get("id", "")).strip()
            version_dir = generated_root / version_id
            if not version_id or not version_dir.is_dir():
                continue
            result.append({
                "id": version_id,
                "label": str(item.get("label") or version_id),
                "hidden": item.get("hidden") is True,
                "source": version_dir,
                **{
                    field: item[field]
                    for field in (
                        "kind",
                        "basedOnVersion",
                        "defaultLocalizationLanguage",
                        "transcriptMode",
                        "embeddedTranscriptLanguage",
                        "transcriptSource",
                    )
                    if field in item
                },
            })
            seen.add(version_id)

    if generated_root.is_dir():
        for version_dir in sorted(generated_root.iterdir(), key=lambda path: path.name.casefold()):
            if not version_dir.is_dir() or version_dir.name in seen:
                continue
            result.append({
                "id": version_dir.name,
                "label": version_dir.name,
                "hidden": True,
                "source": version_dir,
            })
    latest = str(catalog.get("latestVersion") or "").strip()
    return result, latest


class BulkVersionSelectionDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, versions: list[dict[str, object]], latest: str) -> None:
        super().__init__(parent)
        self.title("Publish multiple generated versions")
        self.geometry("820x580")
        self.minsize(700, 480)
        self.transient(parent)
        self.result: tuple[list[dict[str, object]], bool] | None = None
        self.versions = versions
        self.latest = latest

        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        root.rowconfigure(1, weight=1)
        root.columnconfigure(0, weight=1)
        ttk.Label(
            root,
            text=(
                "Select generated versions. They are uploaded oldest-to-newest, then the public "
                "manifest is saved in the local newest-to-oldest order."
            ),
            wraplength=780,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.tree = ttk.Treeview(
            root,
            columns=("order", "id", "label", "visibility"),
            show="headings",
            selectmode="extended",
        )
        for column, label, width in (
            ("order", "Order", 55),
            ("id", "Version ID", 220),
            ("label", "Label", 280),
            ("visibility", "Local visibility", 120),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, stretch=column in {"id", "label"})
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(root, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        for index, item in enumerate(versions, start=1):
            version_id = str(item["id"])
            self.tree.insert(
                "",
                tk.END,
                iid=version_id,
                values=(
                    index,
                    version_id,
                    item["label"],
                    "Hidden" if item.get("hidden") is True else "Visible",
                ),
            )
        self.tree.selection_set(tuple(self.tree.get_children()))

        self.hidden_review_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            root,
            text=(
                "Keep selected versions hidden for review "
                "(clear this to apply local visibility and latest version)"
            ),
            variable=self.hidden_review_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 4))
        ttk.Label(root, text=f"Local latest version: {latest or '(not set)'}").grid(
            row=3, column=0, columnspan=2, sticky="w"
        )

        buttons = ttk.Frame(root)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(
            buttons,
            text="Select all",
            command=lambda: self.tree.selection_set(tuple(self.tree.get_children())),
        ).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Clear selection", command=lambda: self.tree.selection_remove(*self.tree.selection())).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Continue", command=self._accept).pack(side=tk.RIGHT, padx=6)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _accept(self) -> None:
        selected_ids = set(self.tree.selection())
        selected = [item for item in self.versions if str(item["id"]) in selected_ids]
        if not selected:
            messagebox.showwarning("No versions selected", "Select at least one generated version.", parent=self)
            return
        self.result = (selected, self.hidden_review_var.get())
        self.destroy()


class PublicationDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        source_dir: Path,
        game_categories_path: Path | None = None,
        game: str,
        version: str,
        label: str,
        progress=None,
    ) -> None:
        super().__init__(parent)
        self.title(f"Publish {game}/{version}")
        self.geometry("1180x690")
        self.minsize(980, 600)
        self.transient(parent)
        self.source_dir = source_dir.resolve()
        self.game_categories_path = (
            game_categories_path.resolve() if game_categories_path is not None else None
        )
        self.game = game
        self.version = version
        self.label = label
        self.external_progress = progress or (lambda _message: None)
        self.config_data = _load_config()
        self.saved_credentials: dict[str, str] = {}
        self._busy = False
        try:
            self.saved_credentials = load_credentials(CREDENTIAL_PATH)
        except CredentialStoreError as exc:
            self.credential_error = str(exc)
        else:
            self.credential_error = ""
        self._build()
        if self.credential_error:
            self._append_log("WARNING: " + self.credential_error)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(1, weight=1)
        root.columnconfigure(3, weight=1)
        root.rowconfigure(6, weight=1)

        ttk.Label(root, text="Generated version source:").grid(row=0, column=0, sticky="w")
        ttk.Label(root, text=str(self.source_dir)).grid(
            row=0, column=1, columnspan=3, sticky="w", padx=6
        )
        ttk.Label(root, text=f"Version: {self.game}/{self.version} — {self.label}").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(5, 10)
        )

        self.bucket_var = tk.StringVar(value=str(self.config_data.get("bucket", "")))
        self.endpoint_var = tk.StringVar(value=str(self.config_data.get("endpoint_url", "")))
        self.cdn_var = tk.StringVar(value=str(self.config_data.get("cdn_base_url", DEFAULTS["cdn_base_url"])))
        self.zone_var = tk.StringVar(value=str(self.config_data.get("zone_id", "")))
        self.concurrency_var = tk.IntVar(value=int(self.config_data.get("concurrency", 12)))

        same_saved_version = (
            str(self.config_data.get("game", "")) == self.game
            and str(self.config_data.get("version", "")) == self.version
        )
        self.hidden_var = tk.BooleanVar(
            value=bool(self.config_data.get("hidden", True)) if same_saved_version else True
        )
        self.promote_var = tk.BooleanVar(
            value=bool(self.config_data.get("promote_to_latest", False)) if same_saved_version else False
        )

        fields = (
            ("R2 bucket", self.bucket_var, 2, 0),
            ("Upload workers", self.concurrency_var, 2, 2),
            ("R2 endpoint URL", self.endpoint_var, 3, 0),
            ("Public CDN base", self.cdn_var, 4, 0),
            ("Cloudflare Zone ID", self.zone_var, 4, 2),
        )
        for label, variable, row, column in fields:
            ttk.Label(root, text=label + ":").grid(row=row, column=column, sticky="w", pady=3)
            if variable is self.concurrency_var:
                widget = ttk.Spinbox(root, from_=1, to=64, textvariable=variable, width=6)
                widget.grid(row=row, column=column + 1, sticky="w", padx=6, pady=3)
            else:
                ttk.Entry(root, textvariable=variable).grid(
                    row=row,
                    column=column + 1,
                    columnspan=3 if row == 3 else 1,
                    sticky="ew",
                    padx=6,
                    pady=3,
                )

        options = ttk.Frame(root)
        options.grid(row=5, column=0, columnspan=4, sticky="ew", pady=5)
        ttk.Checkbutton(
            options,
            text="Hide this version from normal selectors",
            variable=self.hidden_var,
            command=self._hidden_changed,
        ).pack(side=tk.LEFT)
        self.promote_check = ttk.Checkbutton(
            options,
            text="Set this version as latest",
            variable=self.promote_var,
        )
        self.promote_check.pack(side=tk.LEFT, padx=16)
        self._hidden_changed()

        credential_box = ttk.LabelFrame(root, text="Cloudflare credentials", padding=8)
        credential_box.grid(row=6, column=0, columnspan=4, sticky="new", pady=(4, 6))
        credential_box.columnconfigure(1, weight=1)
        credential_box.columnconfigure(3, weight=1)
        self.access_var = tk.StringVar(
            value=os.environ.get("R2_ACCESS_KEY_ID")
            or self.saved_credentials.get("r2_access_key_id", "")
        )
        self.secret_var = tk.StringVar(
            value=os.environ.get("R2_SECRET_ACCESS_KEY")
            or self.saved_credentials.get("r2_secret_access_key", "")
        )
        self.token_var = tk.StringVar(
            value=os.environ.get("CLOUDFLARE_API_TOKEN")
            or self.saved_credentials.get("cloudflare_api_token", "")
        )
        ttk.Label(credential_box, text="R2 Access Key ID:").grid(row=0, column=0, sticky="w")
        ttk.Entry(credential_box, textvariable=self.access_var, show="•").grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(credential_box, text="R2 Secret Key:").grid(row=0, column=2, sticky="w")
        ttk.Entry(credential_box, textvariable=self.secret_var, show="•").grid(row=0, column=3, sticky="ew", padx=6)
        ttk.Label(credential_box, text="Cache purge API token:").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(credential_box, textvariable=self.token_var, show="•").grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=6, pady=(5, 0)
        )
        self.remember_var = tk.BooleanVar(
            value=CREDENTIAL_PATH.is_file() and credential_saving_supported()
        )
        ttk.Checkbutton(
            credential_box,
            text="Remember credentials securely for this Windows user",
            variable=self.remember_var,
            state=tk.NORMAL if credential_saving_supported() else tk.DISABLED,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(7, 0))
        ttk.Button(credential_box, text="Forget saved credentials", command=self._forget).grid(
            row=2, column=3, sticky="e", padx=6, pady=(7, 0)
        )

        buttons = ttk.Frame(root)
        buttons.grid(row=7, column=0, columnspan=4, sticky="ew", pady=5)
        self.buttons: list[ttk.Button] = []
        for label, command in (
            ("Save", self._save_clicked),
            ("Validate locally", self._validate),
            ("Test R2", self._test_connection),
            ("Compare / dry run", self._dry_run),
            ("Publish", self._publish),
            ("Publish multiple...", self._publish_multiple),
            ("Publish game categories", self._publish_categories),
            ("Publish game display names", self._publish_character_names),
            ("Publish version display names", self._publish_version_character_names),
            ("Manage versions...", self._manage_versions),
            ("Clear game content...", self._clear_game_content),
        ):
            button = ttk.Button(buttons, text=label, command=command)
            button.pack(side=tk.LEFT, padx=(0, 6))
            self.buttons.append(button)

        self.log = scrolledtext.ScrolledText(root, height=14, state=tk.DISABLED, wrap=tk.WORD)
        self.log.grid(row=8, column=0, columnspan=4, sticky="nsew", pady=(5, 0))
        root.rowconfigure(8, weight=1)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status_var).grid(row=9, column=0, columnspan=4, sticky="w", pady=(5, 0))

    def _hidden_changed(self) -> None:
        if self.hidden_var.get():
            self.promote_var.set(False)
            self.promote_check.configure(state=tk.DISABLED)
        else:
            self.promote_check.configure(state=tk.NORMAL)

    def _credential_payload(self) -> dict[str, str]:
        return {
            "r2_access_key_id": self.access_var.get().strip(),
            "r2_secret_access_key": self.secret_var.get().strip(),
            "cloudflare_api_token": self.token_var.get().strip(),
        }

    def _save(self, quiet: bool = False) -> None:
        payload = {
            "source_dir": str(self.source_dir),
            "game": self.game,
            "version": self.version,
            "label": self.label,
            "bucket": self.bucket_var.get().strip(),
            "endpoint_url": self.endpoint_var.get().strip(),
            "cdn_base_url": self.cdn_var.get().strip(),
            "zone_id": self.zone_var.get().strip(),
            "concurrency": int(self.concurrency_var.get()),
            "promote_to_latest": self.promote_var.get(),
            "hidden": self.hidden_var.get(),
        }
        CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        credentials = self._credential_payload()
        if self.remember_var.get():
            if bool(credentials["r2_access_key_id"]) != bool(credentials["r2_secret_access_key"]):
                raise CredentialStoreError("Enter both R2 credential values before saving.")
            save_credentials(CREDENTIAL_PATH, credentials)
        else:
            delete_credentials(CREDENTIAL_PATH)
        if not quiet:
            self._append_log("Saved publication settings and credential preference.")

    def _save_clicked(self) -> None:
        try:
            self._save()
        except Exception as exc:
            messagebox.showerror("Save publication settings", str(exc), parent=self)

    def _forget(self) -> None:
        delete_credentials(CREDENTIAL_PATH)
        self.access_var.set("")
        self.secret_var.set("")
        self.token_var.set("")
        self.remember_var.set(False)

    def _settings(self, require_cloud: bool) -> PublisherSettings:
        if not self.source_dir.is_dir():
            raise PublisherError("Generate the version content before publishing.")
        settings = PublisherSettings(
            source_dir=self.source_dir,
            game=self.game,
            version=self.version,
            label=self.label,
            bucket=self.bucket_var.get().strip() if require_cloud else "",
            endpoint_url=self.endpoint_var.get().strip() if require_cloud else "",
            cdn_base_url=self.cdn_var.get(),
            state_dir=STATE_DIR,
            concurrency=int(self.concurrency_var.get()),
            zone_id=self.zone_var.get(),
            promote_to_latest=self.promote_var.get(),
            hidden=self.hidden_var.get(),
        )
        if require_cloud:
            credentials = self._credential_payload()
            os.environ["R2_ACCESS_KEY_ID"] = credentials["r2_access_key_id"]
            os.environ["R2_SECRET_ACCESS_KEY"] = credentials["r2_secret_access_key"]
            if credentials["cloudflare_api_token"]:
                os.environ["CLOUDFLARE_API_TOKEN"] = credentials["cloudflare_api_token"]
            else:
                os.environ.pop("CLOUDFLARE_API_TOKEN", None)
        return settings

    def _append_log(self, message: str) -> None:
        self.external_progress(message)

        def append() -> None:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, message.rstrip() + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)
        self.after(0, append)

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy

        def update() -> None:
            self.status_var.set(status)
            for button in self.buttons:
                button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.after(0, update)

    def _background(self, status: str, action) -> None:
        if self._busy:
            return
        self._set_busy(True, status)

        def run() -> None:
            try:
                action()
            except Exception as exc:
                message = str(exc)
                self._append_log("ERROR: " + message)
                self.after(0, lambda: messagebox.showerror("Publication failed", message, parent=self))
            finally:
                self._set_busy(False, "Ready")
        threading.Thread(target=run, daemon=True).start()

    def _log_validation(self, report: ValidationReport) -> None:
        for warning in report.warnings:
            self._append_log("WARNING: " + warning)
        for error in report.errors:
            self._append_log("ERROR: " + error)
        self._append_log(
            f"Validation {'passed' if report.valid else 'failed'}: {len(report.files):,} files, "
            f"{format_bytes(report.total_bytes)}, {len(report.errors)} error(s), "
            f"{len(report.warnings)} warning(s)."
        )

    def _log_plan(self, plan: PublishPlan) -> None:
        self._log_validation(plan.validation)
        upload_size = sum(item.size for item in plan.upload_records)
        self._append_log(
            f"Plan: {len(plan.upload_new):,} new, "
            f"{len(plan.upload_changed_custom_audio):,} changed custom audio, "
            f"{len(plan.upload_changed_json):,} changed JSON, "
            f"{len(plan.unchanged):,} unchanged, {format_bytes(upload_size)} to upload, "
            f"{len(plan.immutable_conflicts):,} immutable conflict(s)."
        )

    def _validate(self) -> None:
        try:
            settings = self._settings(False)
        except Exception as exc:
            messagebox.showerror("Validation", str(exc), parent=self)
            return
        self._background(
            "Validating local content...",
            lambda: self._log_validation(
                validate_publisher_source(settings)
            ),
        )

    def _test_connection(self) -> None:
        try:
            self._save(quiet=True)
            settings = self._settings(True)
        except Exception as exc:
            messagebox.showerror("R2 connection", str(exc), parent=self)
            return
        self._background("Testing R2...", lambda: R2Publisher(settings, self._append_log).test_connection())

    def _dry_run(self) -> None:
        try:
            self._save(quiet=True)
            settings = self._settings(True)
        except Exception as exc:
            messagebox.showerror("Dry run", str(exc), parent=self)
            return

        def action() -> None:
            plan = R2Publisher(settings, self._append_log).create_plan()
            self._log_plan(plan)
        self._background("Comparing with R2...", action)

    def _publish(self) -> None:
        try:
            self._save(quiet=True)
            settings = self._settings(True)
        except Exception as exc:
            messagebox.showerror("Publish", str(exc), parent=self)
            return
        if not messagebox.askyesno(
            "Confirm publish",
            f"Publish {settings.game}/{settings.version} to {settings.bucket}?\n\n"
            f"Hidden from normal selectors: {'Yes' if settings.hidden else 'No'}",
            icon=messagebox.WARNING,
            parent=self,
        ):
            return

        def action() -> None:
            publisher = R2Publisher(settings, self._append_log)
            plan = publisher.create_plan()
            self._log_plan(plan)
            if not plan.can_publish:
                raise PublisherError("The publish plan is not safe.")
            result = publisher.publish(plan)
            self._append_log(
                f"Published revision {result['contentRevision']}: "
                f"{result['uploaded']:,} uploaded, {result['skipped']:,} skipped."
            )
            self.after(0, lambda: messagebox.showinfo("Publish complete", f"Published {settings.game}/{settings.version}.", parent=self))
        self._background("Publishing...", action)

    def _publish_categories(self) -> None:
        try:
            self._save(quiet=True)
            settings = self._settings(True)
        except Exception as exc:
            messagebox.showerror("Publish categories", str(exc), parent=self)
            return
        if not messagebox.askyesno(
            "Publish game category default",
            f"Publish this categories.json as the inherited default for {settings.game}?",
            icon=messagebox.WARNING,
            parent=self,
        ):
            return
        self._background(
            "Publishing game categories...",
            lambda: R2Publisher(settings, self._append_log).publish_game_default_categories(
                self.game_categories_path
            ),
        )

    def _publish_multiple(self) -> None:
        try:
            self._save(quiet=True)
            base_settings = self._settings(True)
        except Exception as exc:
            messagebox.showerror("Bulk publish", str(exc), parent=self)
            return
        versions, local_latest = _local_publish_versions(self.source_dir, self.game)
        if not versions:
            messagebox.showwarning(
                "No generated versions",
                f"No generated version folders exist beside {self.source_dir}.",
                parent=self,
            )
            return
        selector = BulkVersionSelectionDialog(self, versions, local_latest)
        self.wait_window(selector)
        if selector.result is None:
            return
        selected, hidden_for_review = selector.result
        version_ids = [str(item["id"]) for item in selected]
        visibility_text = (
            "All selected versions will remain hidden for review."
            if hidden_for_review
            else "Local visibility and latest-version settings will be applied."
        )
        if not messagebox.askyesno(
            "Confirm bulk publication",
            f"Publish {len(selected)} generated version(s) to {base_settings.bucket!r}?\n\n"
            + "\n".join(version_ids)
            + f"\n\n{visibility_text}\n\n"
            "Every source is validated before the first upload. Completed versions are resumable if a later upload stops.",
            icon=messagebox.WARNING,
            parent=self,
        ):
            return

        def action() -> None:
            settings_by_id: dict[str, PublisherSettings] = {}
            self._append_log(f"Validating {len(selected)} selected generated version(s)...")
            for item in selected:
                version_id = str(item["id"])
                settings = PublisherSettings(
                    source_dir=Path(item["source"]),
                    game=self.game,
                    version=version_id,
                    label=str(item["label"]),
                    bucket=base_settings.bucket,
                    endpoint_url=base_settings.endpoint_url,
                    cdn_base_url=base_settings.cdn_base_url,
                    state_dir=STATE_DIR,
                    concurrency=base_settings.concurrency,
                    zone_id=base_settings.zone_id,
                    promote_to_latest=False,
                    hidden=True if hidden_for_review else item.get("hidden") is True,
                )
                report = validate_publisher_source(settings)
                self._append_log(f"[{version_id}]")
                self._log_validation(report)
                if not report.valid:
                    raise PublisherError(
                        f"Bulk publication stopped before uploading because {version_id!r} is invalid."
                    )
                settings_by_id[version_id] = settings

            # New versions are inserted at the beginning by the single-version
            # publisher. Uploading oldest-to-newest naturally creates catalog order.
            totals = {"uploaded": 0, "skipped": 0}
            for index, item in enumerate(reversed(selected), start=1):
                version_id = str(item["id"])
                self._append_log(
                    f"Bulk publish {index}/{len(selected)}: {self.game}/{version_id}"
                )
                publisher = R2Publisher(settings_by_id[version_id], self._append_log)
                plan = publisher.create_plan()
                self._log_plan(plan)
                if not plan.can_publish:
                    raise PublisherError(f"The publish plan for {version_id!r} is not safe.")
                result = publisher.publish(plan)
                totals["uploaded"] += int(result["uploaded"])
                totals["skipped"] += int(result["skipped"])

            catalog_publisher = R2Publisher(
                settings_by_id[str(selected[0]["id"])], self._append_log
            )
            if self.game_categories_path and self.game_categories_path.is_file():
                catalog_publisher.publish_game_default_categories(self.game_categories_path)

            manifest = catalog_publisher.load_game_manifest()
            remote_versions = manifest.get("versions", [])
            if not isinstance(remote_versions, list):
                raise PublisherError("The published manifest has an invalid versions list.")
            remote_by_id = {
                str(item.get("id")): item
                for item in remote_versions
                if isinstance(item, dict) and item.get("id")
            }
            selected_set = set(version_ids)
            ordered_selected: list[dict[str, object]] = []
            for local in selected:
                version_id = str(local["id"])
                remote = remote_by_id.get(version_id)
                if remote is None:
                    raise PublisherError(f"Published version is missing from the manifest: {version_id}")
                remote["hidden"] = True if hidden_for_review else local.get("hidden") is True
                ordered_selected.append(remote)
            manifest["versions"] = [
                *ordered_selected,
                *[
                    item for item in remote_versions
                    if isinstance(item, dict) and str(item.get("id")) not in selected_set
                ],
            ]

            if hidden_for_review:
                latest = str(manifest.get("latestVersion") or "")
                latest_entry = next(
                    (
                        item for item in manifest["versions"]
                        if isinstance(item, dict) and str(item.get("id")) == latest
                    ),
                    None,
                )
                if latest_entry is None or latest_entry.get("hidden") is True:
                    manifest["latestVersion"] = next(
                        (
                            str(item.get("id")) for item in manifest["versions"]
                            if isinstance(item, dict)
                            and item.get("hidden") is not True
                            and item.get("kind") != "custom"
                        ),
                        "",
                    )
            else:
                requested_latest = local_latest if local_latest in selected_set else ""
                if requested_latest:
                    latest_entry = remote_by_id.get(requested_latest)
                    if latest_entry and latest_entry.get("hidden") is not True:
                        manifest["latestVersion"] = requested_latest
                if not manifest.get("latestVersion"):
                    manifest["latestVersion"] = next(
                        (
                            str(item.get("id")) for item in manifest["versions"]
                            if isinstance(item, dict)
                            and item.get("hidden") is not True
                            and item.get("kind") != "custom"
                        ),
                        "",
                    )
            catalog_publisher.save_game_manifest(manifest)
            self._append_log(
                f"Bulk publication complete: {len(selected)} version(s), "
                f"{totals['uploaded']:,} uploaded, {totals['skipped']:,} skipped."
            )
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "Bulk publication complete",
                    f"Published {len(selected)} version(s).",
                    parent=self,
                ),
            )

        self._background("Publishing selected versions...", action)

    def _clear_game_content(self) -> None:
        try:
            self._save(quiet=True)
            settings = self._settings(True)
        except Exception as exc:
            messagebox.showerror("Clear game content", str(exc), parent=self)
            return
        if not messagebox.askyesno(
            "Clear game content from R2?",
            f"Delete every object below {settings.game}/ from bucket {settings.bucket!r}?\n\n"
            "Other game namespaces will be preserved. The live website will lose this game until new content is published.",
            icon=messagebox.WARNING,
            parent=self,
        ):
            return
        confirmation = simpledialog.askstring(
            "Type game ID to confirm",
            f"Type {settings.game} to permanently delete the {settings.game}/ namespace:",
            parent=self,
        )
        if confirmation != settings.game:
            messagebox.showinfo("Reset cancelled", "The game ID did not match. Nothing was deleted.", parent=self)
            return

        def action() -> None:
            publisher = R2Publisher(settings, self._append_log)
            objects = publisher.list_game_content()
            total_bytes = sum(int(item.get("Size", 0)) for item in objects)
            self._append_log(
                f"Confirmed reset inventory: {len(objects):,} object(s), {format_bytes(total_bytes)}."
            )
            result = publisher.clear_game_content()
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "Game content cleared",
                    f"Deleted and verified {result['deleted']:,} object(s) below {settings.game}/.",
                    parent=self,
                ),
            )

        self._background(f"Clearing {settings.game}/ from R2...", action)

    def _publish_character_names(self) -> None:
        try:
            self._save(quiet=True)
            settings = self._settings(True)
        except Exception as exc:
            messagebox.showerror("Publish display names", str(exc), parent=self)
            return
        if not messagebox.askyesno(
            "Publish game display names",
            f"Publish character-names.json for {settings.game}?",
            icon=messagebox.WARNING,
            parent=self,
        ):
            return
        self._background(
            "Publishing game display names...",
            lambda: R2Publisher(settings, self._append_log).publish_game_character_names(),
        )

    def _publish_version_character_names(self) -> None:
        try:
            settings = self._settings(True)
        except Exception as exc:
            messagebox.showerror("Publish version display names", str(exc), parent=self)
            return
        overlay_path = settings.source_dir / "character-names-overlay.json"
        if not messagebox.askyesno(
            "Publish version display names",
            f"Publish {overlay_path.name} for version {settings.version!r}?",
            icon=messagebox.WARNING,
            parent=self,
        ):
            return
        self._background(
            "Publishing version display names...",
            lambda: R2Publisher(
                settings,
                self._append_log,
            ).publish_version_character_names(),
        )

    def _manage_versions(self) -> None:
        try:
            self._save(quiet=True)
            settings = self._settings(True)
        except Exception as exc:
            messagebox.showerror("Manage versions", str(exc), parent=self)
            return
        VersionManagerDialog(self, R2Publisher(settings, self._append_log))
