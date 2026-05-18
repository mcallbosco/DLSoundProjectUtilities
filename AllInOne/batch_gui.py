#!/usr/bin/env python3
"""
Simple Tkinter GUI to run Source2Viewer-CLI in a temp output folder.

Features:
- Choose Source2Viewer binary (saved to config.json)
- Choose input VPK path
- Set file filter (default: sounds\vo)
- Run the CLI with output directed to a temporary folder inside the current working directory
- Stream stdout/stderr to the UI
- Temp folder is deleted when the program exits
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from datetime import datetime
import sys

CONFIG_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LEGACY_CONFIG_FILE = os.path.join(CONFIG_DIR, "s2v_config.json")
RENAME_MAP_FILE = os.path.join(CONFIG_DIR, "rename_map.json")

# Shared VDF/localization helpers live under Voiceline Utilities/modules.
VOICELINE_UTILS_ROOT = os.path.abspath(os.path.join(CONFIG_DIR, "..", "Voiceline Utilities"))
if VOICELINE_UTILS_ROOT not in sys.path:
    sys.path.insert(0, VOICELINE_UTILS_ROOT)
from modules.vdf_kv_common import ORDERED_KNOWN_SUFFIXES, parse_quoted_kv_line

LOCALIZATION_FILE_PREFIX = "citadel_generated_vo_"
HERO_NAME_FILE_PREFIX = "citadel_gc_hero_names_"
HERO_NAME_OUTPUT_FILE = "hero_name_localizations.json"
LOCALIZATION_LANGUAGE_META = {
    "brazilian": {
        "friendly_name": "Portuguese (Brazil)",
        "native_name": "Português (Brasil)",
        "country_code": "BR",
    },
    "bulgarian": {
        "friendly_name": "Bulgarian",
        "native_name": "Български",
        "country_code": "BG",
    },
    "czech": {
        "friendly_name": "Czech",
        "native_name": "Čeština",
        "country_code": "CZ",
    },
    "danish": {
        "friendly_name": "Danish",
        "native_name": "Dansk",
        "country_code": "DK",
    },
    "dutch": {
        "friendly_name": "Dutch",
        "native_name": "Nederlands",
        "country_code": "NL",
    },
    "english": {
        "friendly_name": "English",
        "native_name": "English",
        "country_code": "US",
    },
    "finnish": {
        "friendly_name": "Finnish",
        "native_name": "Suomi",
        "country_code": "FI",
    },
    "french": {
        "friendly_name": "French",
        "native_name": "Français",
        "country_code": "FR",
    },
    "german": {
        "friendly_name": "German",
        "native_name": "Deutsch",
        "country_code": "DE",
    },
    "greek": {
        "friendly_name": "Greek",
        "native_name": "Ελληνικά",
        "country_code": "GR",
    },
    "hungarian": {
        "friendly_name": "Hungarian",
        "native_name": "Magyar",
        "country_code": "HU",
    },
    "indonesian": {
        "friendly_name": "Indonesian",
        "native_name": "Bahasa Indonesia",
        "country_code": "ID",
    },
    "italian": {
        "friendly_name": "Italian",
        "native_name": "Italiano",
        "country_code": "IT",
    },
    "japanese": {
        "friendly_name": "Japanese",
        "native_name": "日本語",
        "country_code": "JP",
    },
    "koreana": {
        "friendly_name": "Korean",
        "native_name": "한국어",
        "country_code": "KR",
    },
    "latam": {
        "friendly_name": "Spanish (Latin America)",
        "native_name": "Español (Latinoamérica)",
        "country_code": "MX",
    },
    "norwegian": {
        "friendly_name": "Norwegian",
        "native_name": "Norsk",
        "country_code": "NO",
    },
    "polish": {
        "friendly_name": "Polish",
        "native_name": "Polski",
        "country_code": "PL",
    },
    "portuguese": {
        "friendly_name": "Portuguese",
        "native_name": "Português",
        "country_code": "PT",
    },
    "romanian": {
        "friendly_name": "Romanian",
        "native_name": "Română",
        "country_code": "RO",
    },
    "russian": {
        "friendly_name": "Russian",
        "native_name": "Русский",
        "country_code": "RU",
    },
    "schinese": {
        "friendly_name": "Chinese (Simplified)",
        "native_name": "简体中文",
        "country_code": "CN",
    },
    "spanish": {
        "friendly_name": "Spanish",
        "native_name": "Español",
        "country_code": "ES",
    },
    "swedish": {
        "friendly_name": "Swedish",
        "native_name": "Svenska",
        "country_code": "SE",
    },
    "tchinese": {
        "friendly_name": "Chinese (Traditional)",
        "native_name": "繁體中文",
        "country_code": "TW",
    },
    "thai": {
        "friendly_name": "Thai",
        "native_name": "ไทย",
        "country_code": "TH",
    },
    "turkish": {
        "friendly_name": "Turkish",
        "native_name": "Türkçe",
        "country_code": "TR",
    },
    "ukrainian": {
        "friendly_name": "Ukrainian",
        "native_name": "Українська",
        "country_code": "UA",
    },
    "vietnamese": {
        "friendly_name": "Vietnamese",
        "native_name": "Tiếng Việt",
        "country_code": "VN",
    },
}


class LocalizationMetadataError(Exception):
    """Raised when a discovered localization file has incomplete language metadata."""


def load_config():
    default = {
        "source2viewer_binary": "",
        "vpk_path": "/home/mcall/Apps/SteamFolder/steamapps/common/Deadlock/game/citadel/pak01_dir.vpk",
        "game_base_path": "/home/mcall/Apps/SteamFolder/steamapps/common/Deadlock",
        "file_filter": "sounds\\vo",
        "status_dir": "",
        "transcriptions_dir": "",
        "localizations_output_dir": "",
        "retranscribe_on_status": True,
        "character_mappings_file": "",
        "conversations_export_json": "",
        "voicelines_consolidated_json": "",
        "voicelines_custom_vocab": "",
        "voicelines_retranscribe_on_status": True,
        "delete_json_on_vdf_match": False,
        
        "include_phantom_lines": True
    }

    cfg = None

    # Prefer new config.json
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = None

    # Fallback: migrate from legacy if present
    if cfg is None and os.path.exists(LEGACY_CONFIG_FILE):
        try:
            with open(LEGACY_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = None
        # Write migrated config.json even if legacy partially loads
        if cfg is None:
            cfg = dict(default)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    # If still no config, create from defaults
    if cfg is None:
        cfg = dict(default)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    # Ensure keys exist
    for k, v in default.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception as e:
        print("Failed to save config:", e)
        return False


class BatchGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Source2Viewer Batch Utility")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.cfg = load_config()

        self.process = None
        self.tempdir = None

        # Conversations/status state
        self.file_status_map = {}
        # Paths snapshot for post-run utilities
        self._last_convos_json = ""
        self._last_flat_json = ""
        self._last_consolidated_json = ""
        self._last_audio_dir = ""

        self._build_ui()

    def _build_ui(self):
        frm = tk.Frame(self)
        frm.pack(padx=10, pady=8, fill=tk.X)

        tk.Label(frm, text="Source2Viewer Binary:").grid(row=0, column=0, sticky=tk.W)
        self.bin_entry = tk.Entry(frm, width=60)
        self.bin_entry.grid(row=0, column=1, padx=(4, 0), sticky=tk.W)
        self.bin_entry.insert(0, self.cfg.get("source2viewer_binary", ""))
        tk.Button(frm, text="Browse...", command=self.browse_binary).grid(row=0, column=2, padx=6)

        tk.Label(frm, text="VPK input: ").grid(row=1, column=0, sticky=tk.W)
        self.vpk_entry = tk.Entry(frm, width=60)
        self.vpk_entry.grid(row=1, column=1, padx=(4, 0), sticky=tk.W)
        self.vpk_entry.insert(0, self.cfg.get("vpk_path", ""))
        tk.Button(frm, text="Browse...", command=self.browse_vpk).grid(row=1, column=2, padx=6)

        tk.Label(frm, text="File filter: ").grid(row=2, column=0, sticky=tk.W)
        self.filter_entry = tk.Entry(frm, width=60)
        self.filter_entry.grid(row=2, column=1, padx=(4, 0), sticky=tk.W)
        self.filter_entry.insert(0, self.cfg.get("file_filter", "sounds\\vo"))

        # Status directory (newest file used)
        tk.Label(frm, text="Status Dir: ").grid(row=3, column=0, sticky=tk.W)
        self.status_entry = tk.Entry(frm, width=60)
        self.status_entry.grid(row=3, column=1, padx=(4, 0), sticky=tk.W)
        self.status_entry.insert(0, self.cfg.get("status_dir", ""))
        tk.Button(frm, text="Browse...", command=self.browse_status).grid(row=3, column=2, padx=6)

        # Transcriptions directory
        tk.Label(frm, text="Transcriptions Dir: ").grid(row=4, column=0, sticky=tk.W)
        self.trans_entry = tk.Entry(frm, width=60)
        self.trans_entry.grid(row=4, column=1, padx=(4, 0), sticky=tk.W)
        self.trans_entry.insert(0, self.cfg.get("transcriptions_dir", ""))
        tk.Button(frm, text="Browse...", command=self.browse_trans).grid(row=4, column=2, padx=6)

        # Localization output directory
        tk.Label(frm, text="Localizations Output Dir: ").grid(row=5, column=0, sticky=tk.W)
        self.loc_out_entry = tk.Entry(frm, width=60)
        self.loc_out_entry.grid(row=5, column=1, padx=(4, 0), sticky=tk.W)
        self.loc_out_entry.insert(0, self.cfg.get("localizations_output_dir", ""))
        tk.Button(frm, text="Browse...", command=self.browse_localizations_output_dir).grid(row=5, column=2, padx=6)

        # Conversations JSON export path
        tk.Label(frm, text="Convos JSON Path: ").grid(row=6, column=0, sticky=tk.W)
        self.convos_json_entry = tk.Entry(frm, width=60)
        self.convos_json_entry.grid(row=6, column=1, padx=(4, 0), sticky=tk.W)
        self.convos_json_entry.insert(0, self.cfg.get("conversations_export_json", ""))
        tk.Button(frm, text="Save As...", command=self.browse_convos_json).grid(row=6, column=2, padx=6)

        # Voicelines consolidated JSON path
        tk.Label(frm, text="Voicelines Consolidated JSON: ").grid(row=7, column=0, sticky=tk.W)
        self.voi_consolidated_entry = tk.Entry(frm, width=60)
        self.voi_consolidated_entry.grid(row=7, column=1, padx=(4, 0), sticky=tk.W)
        self.voi_consolidated_entry.insert(0, self.cfg.get("voicelines_consolidated_json", ""))
        tk.Button(frm, text="Save As...", command=self.browse_voicelines_consolidated_json).grid(row=7, column=2, padx=6)

        # Voicelines custom vocabulary file
        tk.Label(frm, text="Voicelines Custom Vocabulary: ").grid(row=8, column=0, sticky=tk.W)
        self.voi_vocab_entry = tk.Entry(frm, width=60)
        self.voi_vocab_entry.grid(row=8, column=1, padx=(4, 0), sticky=tk.W)
        self.voi_vocab_entry.insert(0, self.cfg.get("voicelines_custom_vocab", ""))
        tk.Button(frm, text="Browse...", command=self.browse_voicelines_vocab).grid(row=8, column=2, padx=6)

        # Retranscribe checkbox
        self.retranscribe_var = tk.BooleanVar(value=bool(self.cfg.get("retranscribe_on_status", True)))
        tk.Checkbutton(frm, text="Re-transcribe when status present", variable=self.retranscribe_var).grid(row=9, column=0, columnspan=2, sticky=tk.W)

        # Voicelines retranscribe checkbox
        self.voi_retranscribe_var = tk.BooleanVar(value=bool(self.cfg.get("voicelines_retranscribe_on_status", True)))
        tk.Checkbutton(frm, text="Re-transcribe voicelines when status present", variable=self.voi_retranscribe_var).grid(row=10, column=0, columnspan=2, sticky=tk.W)

        # Delete JSON on VDF match checkbox
        self.delete_json_var = tk.BooleanVar(value=bool(self.cfg.get("delete_json_on_vdf_match", False)))
        tk.Checkbutton(frm, text="Delete transcript JSON if VDF match found", variable=self.delete_json_var).grid(row=11, column=0, columnspan=2, sticky=tk.W)

        # Include phantom lines checkbox
        self.include_phantom_var = tk.BooleanVar(value=bool(self.cfg.get("include_phantom_lines", True)))
        tk.Checkbutton(frm, text="Include voicelines without audio (phantom)", variable=self.include_phantom_var).grid(row=12, column=0, columnspan=2, sticky=tk.W)

        btn_frm = tk.Frame(self)
        btn_frm.pack(padx=10, pady=(6, 6), fill=tk.X)
        self.run_btn = tk.Button(btn_frm, text="Run Source2Viewer", command=self.on_run)
        self.run_btn.pack(side=tk.LEFT)
        self.stop_btn = tk.Button(btn_frm, text="Stop", command=self.on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(6, 0))

        # Manual export button
        self.export_btn = tk.Button(btn_frm, text="Export Conversations", command=self.export_conversations_manual)
        self.export_btn.pack(side=tk.RIGHT)

        # Generate summaries (updated only)
        self.summaries_btn = tk.Button(btn_frm, text="Generate Summaries (Updated only)", command=self.generate_summaries_manual)
        self.summaries_btn.pack(side=tk.RIGHT, padx=(6, 0))

        self.save_btn = tk.Button(btn_frm, text="Save Config", command=self.on_save_config)
        self.save_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # Export unmatched voicelines (enabled after run)
        self.unmatched_btn = tk.Button(btn_frm, text="Export Unmatched Voicelines", command=self.export_unmatched_voicelines, state=tk.DISABLED)
        self.unmatched_btn.pack(side=tk.RIGHT, padx=(6, 0))
        # Move processed audio files (enabled after run)
        self.move_btn = tk.Button(btn_frm, text="Move Processed Audio Files", command=self.move_processed_audio_files, state=tk.DISABLED)
        self.move_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # Export category tree for debugging
        self.category_tree_btn = tk.Button(btn_frm, text="Export Category Tree", command=self.export_category_tree)
        self.category_tree_btn.pack(side=tk.RIGHT, padx=(6, 0))

        self.log = scrolledtext.ScrolledText(self, height=20, width=100, state=tk.NORMAL)
        self.log.pack(padx=10, pady=(0, 10), fill=tk.BOTH, expand=True)

    def browse_binary(self):
        p = filedialog.askopenfilename(title="Select Source2Viewer binary")
        if p:
            self.bin_entry.delete(0, tk.END)
            self.bin_entry.insert(0, p)

    def browse_vpk(self):
        p = filedialog.askopenfilename(title="Select input VPK", filetypes=[("VPK files", "*.vpk"), ("All files", "*")])
        if p:
            self.vpk_entry.delete(0, tk.END)
            self.vpk_entry.insert(0, p)

    def browse_status(self):
        p = filedialog.askdirectory(title="Select status directory")
        if p:
            self.status_entry.delete(0, tk.END)
            self.status_entry.insert(0, p)

    def browse_trans(self):
        p = filedialog.askdirectory(title="Select transcriptions directory")
        if p:
            self.trans_entry.delete(0, tk.END)
            self.trans_entry.insert(0, p)

    def browse_localizations_output_dir(self):
        p = filedialog.askdirectory(title="Select localizations output directory")
        if p:
            self.loc_out_entry.delete(0, tk.END)
            self.loc_out_entry.insert(0, p)

    def browse_convos_json(self):
        initial = self.convos_json_entry.get().strip() or os.getcwd()
        p = filedialog.asksaveasfilename(title="Select conversations JSON output", initialfile="all_conversations.json", initialdir=os.path.dirname(initial) if os.path.isdir(os.path.dirname(initial)) else os.getcwd(), defaultextension=".json", filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
        if p:
            self.convos_json_entry.delete(0, tk.END)
            self.convos_json_entry.insert(0, p)

    def browse_voicelines_consolidated_json(self):
        initial = self.voi_consolidated_entry.get().strip() or os.getcwd()
        p = filedialog.asksaveasfilename(title="Select voicelines consolidated JSON output", initialfile="voicelines_consolidated.json", initialdir=os.path.dirname(initial) if os.path.isdir(os.path.dirname(initial)) else os.getcwd(), defaultextension=".json", filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
        if p:
            self.voi_consolidated_entry.delete(0, tk.END)
            self.voi_consolidated_entry.insert(0, p)

    def browse_voicelines_vocab(self):
        p = filedialog.askopenfilename(title="Select custom vocabulary JSON", filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
        if p:
            self.voi_vocab_entry.delete(0, tk.END)
            self.voi_vocab_entry.insert(0, p)

    def log_write(self, s):
        try:
            def _append():
                try:
                    self.log.configure(state=tk.NORMAL)
                    self.log.insert(tk.END, s)
                    self.log.see(tk.END)
                    self.log.configure(state=tk.DISABLED)
                except Exception:
                    pass
            # Always marshal UI updates to the Tk main thread
            if self.winfo_exists():
                self.after(0, _append)
        except Exception:
            pass

    def _maybe_enable_unmatched_button(self):
        try:
            have_audio = bool(self._last_audio_dir and os.path.isdir(self._last_audio_dir))
            have_flat = bool(self._last_flat_json and os.path.isfile(self._last_flat_json))
            if have_audio and have_flat and hasattr(self, 'unmatched_btn'):
                self.after(0, lambda: self.unmatched_btn.configure(state=tk.NORMAL))
            if have_audio and have_flat and hasattr(self, 'move_btn'):
                self.after(0, lambda: self.move_btn.configure(state=tk.NORMAL))
        except Exception:
            pass

    def _apply_rename_map(self, audio_dir):
        """
        Apply user-provided rename mappings to .mp3 files under audio_dir.
        Mapping file path: RENAME_MAP_FILE. Keys and values are basenames.
        Values missing .mp3 will be appended. Conflicts are skipped.
        """
        try:
            if not audio_dir or not os.path.isdir(audio_dir):
                return
            if not os.path.isfile(RENAME_MAP_FILE):
                return

            # Load mapping
            try:
                with open(RENAME_MAP_FILE, 'r', encoding='utf-8') as f:
                    mapping = json.load(f)
            except Exception as e:
                self.log_write(f"[Rename] Failed to load rename map: {e}\n")
                return

            if not isinstance(mapping, dict):
                self.log_write("[Rename] Rename map is not a JSON object; skipping.\n")
                return

            # Normalize mapping to str->str and ensure target has .mp3
            norm_map = {}
            for k, v in mapping.items():
                try:
                    src = str(k).strip()
                    dst = str(v).strip()
                    if not src:
                        continue
                    if not dst.lower().endswith('.mp3'):
                        dst = dst + '.mp3'
                    norm_map[src] = dst
                except Exception:
                    continue

            if not norm_map:
                return

            # Build index of basename -> [fullpaths]
            index = {}
            for root, _, files in os.walk(audio_dir):
                for name in files:
                    if not name.lower().endswith('.mp3'):
                        continue
                    index.setdefault(name, []).append(os.path.join(root, name))

            found = 0
            renamed = 0
            conflicts = 0
            missing = 0

            self.log_write(f"[Rename] Loaded {len(norm_map)} entries from {RENAME_MAP_FILE}\n")

            for src_base, dst_base in norm_map.items():
                paths = index.get(src_base)
                if not paths:
                    missing += 1
                    continue
                found += len(paths)
                for src_path in paths:
                    dst_path = os.path.join(os.path.dirname(src_path), dst_base)
                    try:
                        if os.path.abspath(src_path) == os.path.abspath(dst_path):
                            continue
                        if os.path.exists(dst_path):
                            conflicts += 1
                            self.log_write(f"[Rename][SKIP] Target exists: {os.path.basename(src_path)} -> {os.path.basename(dst_path)}\n")
                            continue
                        os.rename(src_path, dst_path)
                        renamed += 1
                        self.log_write(f"[Rename] {os.path.basename(src_path)} -> {os.path.basename(dst_path)}\n")
                    except Exception as e:
                        conflicts += 1
                        self.log_write(f"[Rename][ERROR] {src_base}: {e}\n")

            self.log_write(f"[Rename] Summary: found {found}, renamed {renamed}, missing {missing}, conflicts {conflicts}.\n")
        except Exception as e:
            # Keep UI quiet unless failure
            try:
                self.log_write(f"[Rename][ERROR] {e}\n")
            except Exception:
                pass

    def _extract_localization_language(self, filename):
        base = os.path.basename(filename)
        base_lower = base.lower()
        if not base_lower.startswith(LOCALIZATION_FILE_PREFIX) or not base_lower.endswith(".txt"):
            return None
        language = base[len(LOCALIZATION_FILE_PREFIX):-4].strip().lower()
        return language or None

    def _extract_hero_name_language(self, filename):
        base = os.path.basename(filename)
        base_lower = base.lower()
        if not base_lower.startswith(HERO_NAME_FILE_PREFIX) or not base_lower.endswith(".txt"):
            return None
        language = base[len(HERO_NAME_FILE_PREFIX):-4].strip().lower()
        return language or None

    def _country_code_to_flag(self, country_code):
        if not country_code or len(country_code) != 2:
            return None
        code = country_code.upper()
        if not (code[0].isalpha() and code[1].isalpha()):
            return None
        return chr(127397 + ord(code[0])) + chr(127397 + ord(code[1]))

    def _flag_to_unicode_points(self, flag_emoji):
        if not flag_emoji:
            return None
        return " ".join(f"U+{ord(ch):04X}" for ch in flag_emoji)

    def _get_language_metadata(self, language):
        language_key = (language or "").strip().lower()
        base_meta = LOCALIZATION_LANGUAGE_META.get(language_key)
        if base_meta is None:
            raise LocalizationMetadataError(
                f"Missing metadata entry for language '{language_key}'."
            )

        friendly_name = (base_meta.get("friendly_name") or "").strip()
        native_name = (base_meta.get("native_name") or "").strip()
        country_code = (base_meta.get("country_code") or "").strip().upper()
        missing_fields = [
            field for field, value in (
                ("friendly_name", friendly_name),
                ("native_name", native_name),
                ("country_code", country_code),
            ) if not value
        ]
        if missing_fields:
            raise LocalizationMetadataError(
                f"Language '{language_key}' is missing required fields: {', '.join(missing_fields)}."
            )

        flag_emoji = self._country_code_to_flag(country_code)
        if not flag_emoji:
            raise LocalizationMetadataError(
                f"Language '{language_key}' has invalid country_code '{country_code}'."
            )
        flag_emoji_unicode = self._flag_to_unicode_points(flag_emoji)

        return {
            "friendly_name": friendly_name,
            "native_name": native_name,
            "country_code": country_code,
            "flag_emoji": flag_emoji,
            "flag_emoji_unicode": flag_emoji_unicode,
        }

    def _show_error_popup_threadsafe(self, title, message):
        done = threading.Event()

        def _show():
            try:
                messagebox.showerror(title, message)
            finally:
                done.set()

        try:
            if self.winfo_exists():
                self.after(0, _show)
                done.wait()
        except Exception:
            pass

    def _parse_localization_tokens(self, file_path):
        tokens = {}
        waiting_for_tokens_block = False
        in_tokens_block = False
        tokens_depth = 0

        with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("//"):
                    continue

                if waiting_for_tokens_block:
                    if line.startswith("{"):
                        in_tokens_block = True
                        tokens_depth = 1
                        waiting_for_tokens_block = False
                    continue

                if in_tokens_block:
                    if line.startswith("{"):
                        tokens_depth += 1
                        continue
                    if line.startswith("}"):
                        tokens_depth -= 1
                        if tokens_depth <= 0:
                            in_tokens_block = False
                        continue
                    parsed = parse_quoted_kv_line(line)
                    if parsed:
                        key, text = parsed
                        key = key.strip().lower()
                        text = text.strip()
                        if key:
                            tokens[key] = text
                    continue

                line_lower = line.lower()
                if line_lower == '"tokens"':
                    waiting_for_tokens_block = True
                    continue
                if line_lower.startswith('"tokens"') and "{" in line:
                    in_tokens_block = True
                    tokens_depth = 1
                    continue

        return tokens

    def _parse_hero_name_tokens(self, file_path):
        """
        Parse hero-name localization file and return:
          { "<hero_id>": "<localized_name>" }

        Includes only base hero tokens:
          hero_<id>:n
        Excludes:
          hero_<id>_search:n
          hero_<id>_sort:n
        """
        tokens = self._parse_localization_tokens(file_path)
        hero_names = {}
        for key, text in tokens.items():
            token_key = (key or "").strip().lower()
            if not token_key.startswith("hero_"):
                continue
            if not token_key.endswith(":n"):
                continue
            if token_key.endswith("_search:n") or token_key.endswith("_sort:n"):
                continue

            hero_id = token_key[len("hero_"):-2].strip().lower()
            if not hero_id:
                continue
            cleaned_text = self._strip_hash_markup(text)
            if not cleaned_text:
                continue
            hero_names[hero_id] = cleaned_text
        return hero_names

    def _strip_hash_markup(self, text):
        """
        Remove inline hash-delimited tags like '#|f|#' from localized hero names.
        """
        if text is None:
            return ""
        cleaned = re.sub(r"#.*?#", "", str(text))
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned

    def _load_character_mapping_indexes(self, character_mappings_path):
        """
        Build canonical and alias indexes from Assets/character_mappings.json.

        Returns:
          canonical_order: [canonical_key_in_json_order]
          canonical_lookup: {normalized_canonical: canonical_original}
          alias_lookup: {normalized_alias: canonical_original}
          alias_collisions: [(alias, first_canonical, second_canonical), ...]
        """
        with open(character_mappings_path, "r", encoding="utf-8") as f:
            mappings = json.load(f)

        if not isinstance(mappings, dict):
            raise ValueError(f"Character mappings file is not a JSON object: {character_mappings_path}")

        canonical_order = []
        canonical_lookup = {}
        alias_lookup = {}
        alias_collisions = []

        for canonical_raw, aliases in mappings.items():
            canonical_original = str(canonical_raw).strip()
            canonical_norm = canonical_original.lower()
            if not canonical_original:
                continue

            canonical_order.append(canonical_original)
            canonical_lookup.setdefault(canonical_norm, canonical_original)

            if isinstance(aliases, list):
                for alias in aliases:
                    alias_norm = str(alias).strip().lower()
                    if not alias_norm:
                        continue
                    existing = alias_lookup.get(alias_norm)
                    if existing is None:
                        alias_lookup[alias_norm] = canonical_original
                    elif existing != canonical_original:
                        alias_collisions.append((alias_norm, existing, canonical_original))

        return canonical_order, canonical_lookup, alias_lookup, alias_collisions

    def _build_hero_name_localization_index(self, hero_names_by_language, ordered_languages, canonical_order, canonical_lookup, alias_lookup):
        """
        Build hero-name localization index:
          { "<character_key>": [[language, localized_name], ...] }
        """
        index = {}
        unmatched_tokens = 0
        duplicate_language_hits = 0

        for language in ordered_languages:
            lang_map = hero_names_by_language.get(language, {})
            for hero_id, localized_name in lang_map.items():
                token_id = str(hero_id).strip().lower()
                canonical_key = canonical_lookup.get(token_id)
                if canonical_key is None:
                    canonical_key = alias_lookup.get(token_id)
                if canonical_key is None:
                    unmatched_tokens += 1
                    continue

                row = index.setdefault(canonical_key, [])
                if any(item[0] == language for item in row):
                    duplicate_language_hits += 1
                    continue
                row.append([language, localized_name])

        ordered_index = {}
        for canonical_key in canonical_order:
            rows = index.get(canonical_key)
            if rows:
                ordered_index[canonical_key] = rows

        stats = {
            "unmatched_tokens": unmatched_tokens,
            "duplicate_language_hits": duplicate_language_hits,
            "emitted_keys": len(ordered_index),
        }
        return ordered_index, stats

    def _write_hero_name_localization_index(self, localization_output_dir, index):
        out_path = os.path.join(localization_output_dir, HERO_NAME_OUTPUT_FILE)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
        self.log_write(
            f"[Hero Names] Wrote hero name localization index with {len(index)} keys to {out_path}\n"
        )

    def _export_hero_name_localizations_from_game_files(self, hero_name_source_dir, localization_output_dir):
        if not localization_output_dir:
            self.log_write("[Hero Names] Output directory not set. Skipping hero-name localization export.\n")
            return

        if not hero_name_source_dir or not os.path.isdir(hero_name_source_dir):
            self.log_write(f"[Hero Names] Source directory not found: {hero_name_source_dir}\n")
            return

        try:
            os.makedirs(localization_output_dir, exist_ok=True)
        except Exception as e:
            self.log_write(f"[Hero Names] Failed to create output directory: {e}\n")
            return

        source_files = []
        for name in sorted(os.listdir(hero_name_source_dir)):
            if self._extract_hero_name_language(name):
                source_files.append(name)

        if not source_files:
            self.log_write(f"[Hero Names] No hero-name localization files found in: {hero_name_source_dir}\n")
            return

        character_mappings_path = os.path.abspath(
            os.path.join(CONFIG_DIR, "..", "Assets", "character_mappings.json")
        )
        if not os.path.isfile(character_mappings_path):
            raise FileNotFoundError(f"Character mappings file not found: {character_mappings_path}")

        canonical_order, canonical_lookup, alias_lookup, alias_collisions = self._load_character_mapping_indexes(
            character_mappings_path
        )
        if alias_collisions:
            self.log_write(
                f"[Hero Names] Alias collisions in character mappings: {len(alias_collisions)} "
                f"(using first canonical key encountered)\n"
            )

        hero_names_by_language = {}
        ordered_languages = []

        for file_name in source_files:
            language = self._extract_hero_name_language(file_name)
            if not language:
                continue
            try:
                self._get_language_metadata(language)
            except LocalizationMetadataError as e:
                raise LocalizationMetadataError(
                    f"Hero-name localization file '{file_name}' is missing supporting info: {e}"
                ) from e

            source_path = os.path.join(hero_name_source_dir, file_name)
            try:
                hero_names = self._parse_hero_name_tokens(source_path)
            except Exception as e:
                self.log_write(f"[Hero Names] Failed to parse {file_name}: {e}\n")
                continue

            hero_names_by_language[language] = hero_names
            ordered_languages.append(language)
            self.log_write(
                f"[Hero Names] Parsed {len(hero_names)} base hero-name tokens from {file_name}\n"
            )

        index, stats = self._build_hero_name_localization_index(
            hero_names_by_language,
            ordered_languages,
            canonical_order,
            canonical_lookup,
            alias_lookup,
        )
        self._write_hero_name_localization_index(localization_output_dir, index)
        self.log_write(
            f"[Hero Names] Export complete. Keys: {stats['emitted_keys']}, "
            f"unmatched tokens: {stats['unmatched_tokens']}, "
            f"duplicate language hits: {stats['duplicate_language_hits']}\n"
        )

    def _build_voiceline_localization_index(self, lines_by_language, ordered_languages):
        """
        Build a lookup keyed by filename:
          { "<voiceline_id>": ["<language>", ...] }
        """
        index = {}
        for language in ordered_languages:
            language_lines = lines_by_language.get(language, {})
            for voiceline_id in language_lines.keys():
                key = voiceline_id[:-4] if voiceline_id.lower().endswith(".mp3") else voiceline_id
                row = index.setdefault(key, [])
                row.append(language)

        # Keep language arrays deterministic and deduplicated.
        order_map = {lang: idx for idx, lang in enumerate(ordered_languages)}
        for filename in list(index.keys()):
            index[filename] = sorted(set(index[filename]), key=lambda lang: order_map.get(lang, 10**9))
        return index

    def _write_voiceline_localization_index(self, localization_output_dir, manifest, lines_by_language):
        ordered_languages = [entry["language"] for entry in manifest.get("languages", [])]
        index = self._build_voiceline_localization_index(lines_by_language, ordered_languages)

        out_path = os.path.join(localization_output_dir, "voiceline_localizations.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

        self.log_write(
            f"[Localization] Wrote filename localization lookup with {len(index)} entries to {out_path}\n"
        )

    def _normalize_localization_lines(self, tokens):
        lines = {}
        source_kind = {}
        collisions = 0
        exact_overrides = 0

        for key, text in tokens.items():
            canonical_key = key
            incoming_kind = "exact"
            for suffix in ORDERED_KNOWN_SUFFIXES:
                if key.endswith(suffix):
                    stripped = key[:-len(suffix)]
                    if stripped:
                        canonical_key = stripped
                        incoming_kind = "suffix"
                    break

            existing_kind = source_kind.get(canonical_key)
            if existing_kind is None:
                lines[canonical_key] = text
                source_kind[canonical_key] = incoming_kind
                continue

            if existing_kind == "suffix" and incoming_kind == "exact":
                lines[canonical_key] = text
                source_kind[canonical_key] = "exact"
                exact_overrides += 1
            else:
                collisions += 1

        return lines, collisions, exact_overrides

    def _extract_hero_icons_for_language(self, binary, vpk_path, language, localization_output_dir):
        """
        Extract panorama/images/heroes/hero_names from vpk_path and place
        the decompiled files into <localization_output_dir>/icons/<language>/.
        Returns the number of files copied, or -1 on hard failure.
        """
        icons_out_dir = os.path.join(localization_output_dir, "icons", language)
        try:
            os.makedirs(icons_out_dir, exist_ok=True)
        except Exception as e:
            self.log_write(f"[Hero Icons] [{language}] Failed to create output directory: {e}\n")
            return -1

        icons_tmp = tempfile.mkdtemp(prefix="s2v_icons_", dir=os.getcwd())
        try:
            icon_filter = "panorama/images/heroes/hero_names"
            cmd = [binary, "-i", vpk_path, "-o", icons_tmp, "-f", icon_filter, "-d"]
            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            except Exception as e:
                self.log_write(f"[Hero Icons] [{language}] Failed to run Source2Viewer: {e}\n")
                return -1

            if proc.returncode != 0:
                self.log_write(f"[Hero Icons] [{language}] Source2Viewer failed (exit {proc.returncode}):\n{proc.stdout}\n")
                return -1

            all_files = []
            for root, _, files in os.walk(icons_tmp):
                for name in files:
                    all_files.append(os.path.join(root, name))

            if not all_files:
                self.log_write(f"[Hero Icons] [{language}] No files extracted.\n")
                return 0

            copied = 0
            for src in all_files:
                dst = os.path.join(icons_out_dir, os.path.basename(src))
                try:
                    shutil.copy2(src, dst)
                    copied += 1
                except Exception as e:
                    self.log_write(f"[Hero Icons] [{language}] Failed to copy {os.path.basename(src)}: {e}\n")

            return copied
        finally:
            try:
                shutil.rmtree(icons_tmp, ignore_errors=True)
            except Exception:
                pass

    def _find_patron_logo_extracted_file(self, tmp_root, team_prefix):
        """
        team_prefix: 'team1_patron_logo' or 'team2_patron_logo' (matches decompiled output basename).
        Returns one path or None.
        """
        needle = team_prefix.lower()
        matches = []
        for root, _, files in os.walk(tmp_root):
            for name in files:
                if needle in name.lower():
                    matches.append(os.path.join(root, name))
        if not matches:
            return None
        matches.sort()
        return matches[0]

    def _extract_patron_logos_for_language(self, binary, vpk_path, language, localization_output_dir):
        """
        Extract panorama HUD objective patron logos (team1 / team2) from vpk_path and write
        team1.png and team2.png next to hero name icons under icons/<language>/.
        Returns 2 if both written, 1 if one, 0 if none (or VPK empty for these assets), -1 on tool failure.
        """
        icons_out_dir = os.path.join(localization_output_dir, "icons", language)
        try:
            os.makedirs(icons_out_dir, exist_ok=True)
        except Exception as e:
            self.log_write(f"[Patron logos] [{language}] Failed to create output directory: {e}\n")
            return -1

        written = 0
        for team_prefix, out_name, vpk_filter in (
            ("team1_patron_logo", "team1.png", "panorama/images/hud/objectives/team1_patron_logo_psd"),
            ("team2_patron_logo", "team2.png", "panorama/images/hud/objectives/team2_patron_logo_psd"),
        ):
            logos_tmp = tempfile.mkdtemp(prefix="s2v_patron_", dir=os.getcwd())
            try:
                cmd = [binary, "-i", vpk_path, "-o", logos_tmp, "-f", vpk_filter, "-d"]
                try:
                    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                except Exception as e:
                    self.log_write(f"[Patron logos] [{language}] Failed to run Source2Viewer: {e}\n")
                    return -1 if written == 0 else written

                if proc.returncode != 0:
                    self.log_write(
                        f"[Patron logos] [{language}] Source2Viewer failed (exit {proc.returncode}) "
                        f"for filter {vpk_filter!r}:\n{proc.stdout}\n"
                    )
                    return -1 if written == 0 else written

                src = self._find_patron_logo_extracted_file(logos_tmp, team_prefix)
                dst = os.path.join(icons_out_dir, out_name)
                if not src:
                    self.log_write(
                        f"[Patron logos] [{language}] No extracted file matching '{team_prefix}' "
                        f"for filter {vpk_filter!r}.\n"
                    )
                    continue
                try:
                    shutil.copy2(src, dst)
                    written += 1
                except Exception as e:
                    self.log_write(f"[Patron logos] [{language}] Failed to copy {out_name} from {src}: {e}\n")
            finally:
                try:
                    shutil.rmtree(logos_tmp, ignore_errors=True)
                except Exception:
                    pass

        if written == 0:
            self.log_write(f"[Patron logos] [{language}] No patron logo files written.\n")

        return written

    def _extract_all_hero_icons(self, binary, main_vpk, game_base, localization_output_dir):
        """
        Extract hero name icons and patron team logos for all languages:
          - English: from the main citadel pak01_dir.vpk → icons/english/
          - Per-language: from game/<citadel_{lang}>/pak01_dir.vpk → icons/{lang}/
        """
        if not localization_output_dir:
            self.log_write("[Hero Icons] Localization output directory not set. Skipping.\n")
            return

        total_copied = 0
        languages_done = []

        # English — from the main VPK
        self.log_write("[Hero Icons] Extracting english from main VPK...\n")
        n = self._extract_hero_icons_for_language(binary, main_vpk, "english", localization_output_dir)
        if n >= 0:
            total_copied += n
            languages_done.append(f"english ({n})")
        p = self._extract_patron_logos_for_language(binary, main_vpk, "english", localization_output_dir)
        if p >= 0:
            total_copied += p
            self.log_write(f"[Patron logos] english: wrote {p}/2 files (team1.png, team2.png).\n")

        # Localization VPKs — discover citadel_{lang} folders under game_base/game/
        if not game_base:
            self.log_write("[Hero Icons] game_base_path not configured; skipping localization icon extraction.\n")
        else:
            game_dir = os.path.join(game_base, "game")
            try:
                entries = sorted(os.listdir(game_dir))
            except Exception as e:
                self.log_write(f"[Hero Icons] Cannot list game directory: {e}\n")
                entries = []

            for entry in entries:
                if not entry.startswith("citadel_"):
                    continue
                language = entry[len("citadel_"):]
                vpk_path = os.path.join(game_dir, entry, "pak01_dir.vpk")
                if not os.path.isfile(vpk_path):
                    self.log_write(f"[Hero Icons] [{language}] No pak01_dir.vpk found, skipping.\n")
                    continue
                n = self._extract_hero_icons_for_language(binary, vpk_path, language, localization_output_dir)
                if n >= 0:
                    total_copied += n
                    languages_done.append(f"{language} ({n})")
                p = self._extract_patron_logos_for_language(binary, vpk_path, language, localization_output_dir)
                if p >= 0:
                    total_copied += p
                    self.log_write(f"[Patron logos] {language}: wrote {p}/2 files (team1.png, team2.png).\n")

        self.log_write(
            f"[Hero Icons] Done. Total files copied: {total_copied} across {len(languages_done)} language(s).\n"
        )
        self.log_write(f"[Hero Icons] Languages: {', '.join(languages_done)}\n")

    def _export_localizations_from_game_files(self, localization_source_dir, localization_output_dir):
        if not localization_output_dir:
            self.log_write("[Localization] Output directory not set. Skipping localization export.\n")
            return

        if not localization_source_dir or not os.path.isdir(localization_source_dir):
            self.log_write(f"[Localization] Source directory not found: {localization_source_dir}\n")
            return

        try:
            os.makedirs(localization_output_dir, exist_ok=True)
        except Exception as e:
            self.log_write(f"[Localization] Failed to create output directory: {e}\n")
            return

        source_files = []
        for name in sorted(os.listdir(localization_source_dir)):
            if self._extract_localization_language(name):
                source_files.append(name)

        if not source_files:
            self.log_write(f"[Localization] No localization files found in: {localization_source_dir}\n")
            return

        manifest = {
            "generated_at": datetime.now().isoformat(),
            "source_directory": os.path.abspath(localization_source_dir),
            "languages": [],
        }
        lines_by_language = {}

        written = 0
        for file_name in source_files:
            language = self._extract_localization_language(file_name)
            if not language:
                continue
            try:
                language_meta = self._get_language_metadata(language)
            except LocalizationMetadataError as e:
                raise LocalizationMetadataError(
                    f"Localization file '{file_name}' is missing supporting info: {e}"
                ) from e

            source_path = os.path.join(localization_source_dir, file_name)
            try:
                tokens = self._parse_localization_tokens(source_path)
                lines, collisions, exact_overrides = self._normalize_localization_lines(tokens)
            except Exception as e:
                self.log_write(f"[Localization] Failed to parse {file_name}: {e}\n")
                continue

            payload = {
                "meta": {
                    "language": language,
                    "friendly_name": language_meta["friendly_name"],
                    "native_name": language_meta["native_name"],
                    "country_code": language_meta["country_code"],
                    "flag_emoji": language_meta["flag_emoji"],
                    "flag_emoji_unicode": language_meta["flag_emoji_unicode"],
                    "source_file": file_name,
                    "generated_at": datetime.now().isoformat(),
                    "entry_count": len(lines),
                    "collision_count": collisions,
                    "exact_override_count": exact_overrides,
                },
                "lines": lines,
            }

            output_file = f"{language}.json"
            output_path = os.path.join(localization_output_dir, output_file)
            try:
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
            except Exception as e:
                self.log_write(f"[Localization] Failed to write {output_file}: {e}\n")
                continue

            manifest["languages"].append({
                "language": language,
                "friendly_name": language_meta["friendly_name"],
                "native_name": language_meta["native_name"],
                "country_code": language_meta["country_code"],
                "flag_emoji": language_meta["flag_emoji"],
                "flag_emoji_unicode": language_meta["flag_emoji_unicode"],
                "output_file": output_file,
                "source_file": file_name,
                "entry_count": len(lines),
                "collision_count": collisions,
                "exact_override_count": exact_overrides,
            })
            lines_by_language[language] = lines
            written += 1
            self.log_write(
                f"[Localization] Wrote {output_file} with {len(lines)} lines "
                f"(collisions: {collisions}, exact overrides: {exact_overrides})\n"
            )

        manifest_path = os.path.join(localization_output_dir, "manifest.json")
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            self.log_write(
                f"[Localization] Wrote manifest with {len(manifest['languages'])} languages to {manifest_path}\n"
            )
        except Exception as e:
            self.log_write(f"[Localization] Failed to write manifest: {e}\n")

        try:
            self._write_voiceline_localization_index(localization_output_dir, manifest, lines_by_language)
        except Exception as e:
            self.log_write(f"[Localization] Failed to write voiceline localization index: {e}\n")

        self.log_write(f"[Localization] Export complete. Language files written: {written}\n")

    def on_save_config(self):
        self.cfg["source2viewer_binary"] = self.bin_entry.get().strip()
        self.cfg["vpk_path"] = self.vpk_entry.get().strip()
        self.cfg["file_filter"] = self.filter_entry.get().strip()
        self.cfg["status_dir"] = self.status_entry.get().strip()
        self.cfg["transcriptions_dir"] = self.trans_entry.get().strip()
        self.cfg["localizations_output_dir"] = self.loc_out_entry.get().strip()
        self.cfg["retranscribe_on_status"] = bool(self.retranscribe_var.get())
        self.cfg["conversations_export_json"] = self.convos_json_entry.get().strip()
        self.cfg["voicelines_consolidated_json"] = self.voi_consolidated_entry.get().strip()
        self.cfg["voicelines_custom_vocab"] = self.voi_vocab_entry.get().strip()
        self.cfg["voicelines_retranscribe_on_status"] = bool(self.voi_retranscribe_var.get())
        self.cfg["delete_json_on_vdf_match"] = bool(self.delete_json_var.get())
        self.cfg["include_phantom_lines"] = bool(self.include_phantom_var.get())
        ok = save_config(self.cfg)
        if ok:
            messagebox.showinfo("Saved", f"Config saved to {CONFIG_FILE}")
        else:
            messagebox.showwarning("Save failed", "Failed to save config")

    def on_run(self):
        binary = self.bin_entry.get().strip()
        vpk = self.vpk_entry.get().strip()
        ffilter = self.filter_entry.get().strip() or "sounds\\vo"

        if not binary:
            messagebox.showerror("Missing binary", "Please select the Source2Viewer binary.")
            return
        if not os.path.exists(binary):
            messagebox.showerror("Binary not found", f"Binary not found: {binary}")
            return
        if not vpk or not os.path.exists(vpk):
            if not messagebox.askyesno("VPK not found", "VPK not found. Continue anyway?"):
                return

        # create temp output folder inside cwd
        try:
            self.tempdir = tempfile.mkdtemp(prefix="s2v_tmp_", dir=os.getcwd())
        except Exception as e:
            messagebox.showerror("Temp dir", f"Failed to create temp folder: {e}")
            return

        localization_output_dir = self.loc_out_entry.get().strip() if hasattr(self, "loc_out_entry") else self.cfg.get("localizations_output_dir", "")

        # Copy citadel_generated_vo from game files to temp folder
        game_base = self.cfg.get("game_base_path", "")
        localization_source_dir = ""
        hero_name_source_dir = ""
        if game_base:
            localization_source_dir = os.path.join(
                game_base, "game", "citadel", "resource", "localization", "citadel_generated_vo"
            )
            hero_name_source_dir = os.path.join(
                game_base, "game", "citadel", "resource", "localization", "citadel_gc_hero_names"
            )
            vo_localization_path = os.path.join(localization_source_dir, "citadel_generated_vo_english.txt")
            if os.path.isfile(vo_localization_path):
                try:
                    dest_path = os.path.join(self.tempdir, "citadel_generated_vo.txt")
                    shutil.copy2(vo_localization_path, dest_path)
                    self.log_write(f"[Pre-run] Copied citadel_generated_vo.txt to temp folder\n")
                except Exception as e:
                    self.log_write(f"[Pre-run] Failed to copy citadel_generated_vo.txt: {e}\n")
            else:
                self.log_write(f"[Pre-run] citadel_generated_vo.txt not found at {vo_localization_path}\n")
            if not os.path.isdir(hero_name_source_dir):
                self.log_write(f"[Pre-run] hero-name localization directory not found at {hero_name_source_dir}\n")
        else:
            self.log_write("[Pre-run] game_base_path is not configured; localization export will be skipped.\n")

        cmd = [binary, "-i", vpk, "-o", self.tempdir, "-f", ffilter, "-d"]

        self.log_write(f"Running: {' '.join(cmd)}\n")

        def target():
            try:
                self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            except Exception as e:
                self.log_write(f"Failed to start process: {e}\n")
                self.process = None
                self.run_btn.configure(state=tk.NORMAL)
                self.stop_btn.configure(state=tk.DISABLED)
                return

            self.run_btn.configure(state=tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL)

            # Collect output but don't display in real-time
            output_lines = []
            for line in self.process.stdout or []:
                output_lines.append(line)

            self.process.wait()
            rc = self.process.returncode

            # Only show output if process failed (non-zero return code)
            if rc != 0:
                self.log_write("Process failed! Output:\n")
                for line in output_lines:
                    self.log_write(line)
            else:
                self.log_write("Process completed successfully.\n")
                try:
                    self._export_localizations_from_game_files(localization_source_dir, localization_output_dir)
                    self._export_hero_name_localizations_from_game_files(hero_name_source_dir, localization_output_dir)
                except LocalizationMetadataError as e:
                    error_msg = f"[Localization] {e}"
                    self.log_write(f"{error_msg}\n")
                    self.log_write("[Localization] Stopping post-run pipeline.\n")
                    self._show_error_popup_threadsafe("Localization Metadata Missing", str(e))
                    self.log_write(f"Process exited with code {rc}\n")
                    self.process = None
                    self.run_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    return
                except Exception as e:
                    self.log_write(f"[Localization] Export step failed: {e}\n")

                # Extract hero icon assets for all languages
                try:
                    self._extract_all_hero_icons(binary, vpk, game_base, localization_output_dir)
                except Exception as e:
                    self.log_write(f"[Hero Icons] Export step failed: {e}\n")

                # Trigger conversations export after successful extraction
                try:
                    audio_dir = os.path.join(self.tempdir or "", "sounds", "vo")
                    # Start export on a worker thread; it will marshal UI updates safely
                    self._start_conversations_export(audio_dir)
                except Exception as e:
                    # Keep UI quiet unless failure
                    self.log_write(f"Conversations export failed to start: {e}\n")

            self.log_write(f"Process exited with code {rc}\n")
            self.process = None
            self.run_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.DISABLED)

        threading.Thread(target=target, daemon=True).start()

    def _load_status_file(self, status_dir):
        try:
            if not status_dir:
                print("[Status] No status directory configured")
                return {}
            if not os.path.isdir(status_dir):
                print(f"[Status] Status directory does not exist: {status_dir}")
                return {}
            candidates = []
            for name in os.listdir(status_dir):
                if name.lower().endswith((".txt", ".log", ".md")):
                    candidates.append(os.path.join(status_dir, name))
            if not candidates:
                print(f"[Status] No .txt/.log/.md files found in: {status_dir}")
                return {}
            
            # Try to parse embedded timestamp from filename (changes_YYYYMMDD_HHMMSS_<hash>.txt pattern)
            def extract_timestamp(filepath):
                basename = os.path.basename(filepath)
                parts = basename.split('_')
                if len(parts) >= 3:
                    try:
                        # Format: changes_YYYYMMDD_HHMMSS_<hash>
                        date_part = parts[1]
                        time_part = parts[2]
                        if len(date_part) == 8 and len(time_part) == 6:
                            timestamp_str = date_part + time_part
                            return int(timestamp_str)
                    except (ValueError, IndexError):
                        pass
                # Fallback to file modification time if parsing fails
                try:
                    return int(os.path.getmtime(filepath))
                except Exception:
                    return 0
            
            newest = max(candidates, key=extract_timestamp)
            print(f"[Status] Using status file: {newest}")
            status_map = {}
            with open(newest, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('---') or line.startswith('Audio changes') or line.startswith('Source commit') or line.startswith('Repository:'):
                        continue
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    path_token = parts[0]
                    base_name = os.path.basename(path_token)
                    stem = os.path.splitext(base_name)[0].lower()
                    status_token = parts[-1]
                    if status_token.isupper() and status_token.isalpha():
                        status_map.setdefault(stem, set()).add(status_token)
            print(f"[Status] Parsed {len(status_map)} file entries from status file")
            return status_map
        except Exception as e:
            print(f"[Status] Exception while loading status file: {e}")
            return {}

    def _start_conversations_export(self, audio_dir):
        # Capture UI values on main thread BEFORE starting background thread
        out_path = (self.convos_json_entry.get().strip() if hasattr(self, 'convos_json_entry') else "") or self.cfg.get("conversations_export_json", "")
        if not out_path:
            out_path = os.path.join(audio_dir, "all_conversations.json")
        trans_dir = self.trans_entry.get().strip() if hasattr(self, 'trans_entry') else self.cfg.get("transcriptions_dir", "")
        status_dir = self.status_entry.get().strip() if hasattr(self, 'status_entry') else self.cfg.get("status_dir", "")
        retranscribe_flag = bool(self.retranscribe_var.get()) if hasattr(self, 'retranscribe_var') else True
        include_phantom_flag = bool(self.include_phantom_var.get()) if hasattr(self, 'include_phantom_var') else True

        # Capture VDF path
        vdf_path = os.path.join(self.tempdir, "citadel_generated_vo.txt") if self.tempdir else None

        # Export in background to keep UI responsive
        def run_export():
            try:
                if not audio_dir or not os.path.isdir(audio_dir):
                    return

                # Apply user rename map before parsing/exports
                try:
                    self._apply_rename_map(audio_dir)
                except Exception:
                    pass

                # Prepare ConversationPlayer headlessly
                convos_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Conversations Utilities"))
                if convos_path not in sys.path:
                    sys.path.insert(0, convos_path)
                try:
                    from convos import ConversationPlayer
                except Exception as e:
                    self.log_write(f"Failed to import ConversationPlayer: {e}\n")
                    return

                temp_root = tk.Tk()
                temp_root.withdraw()
                try:
                    player = ConversationPlayer(temp_root)
                    player.audio_dir = audio_dir
                    if trans_dir:
                        player.transcriptions_dir = trans_dir

                    # Load newest status file if provided
                    self.log_write(f"[Conversations] Loading status from: {status_dir or '(not set)'}\n")
                    status_map = self._load_status_file(status_dir)
                    self.log_write(f"[Conversations] Loaded {len(status_map)} status entries\n")
                    player.file_status_map = status_map

                    # Load VDF if present
                    if vdf_path and os.path.exists(vdf_path):
                        self.log_write(f"[Conversations] Loading VDF from: {vdf_path}\n")
                        try:
                            count = player.load_vdf_from_file(vdf_path)
                            self.log_write(f"[Conversations] Loaded {count} VDF entries\n")
                        except Exception as e:
                            self.log_write(f"[Conversations] Failed to load VDF: {e}\n")

                    # Use captured flags
                    player._retranscribe_on_status_snapshot = retranscribe_flag
                    player.include_phantom = include_phantom_flag

                    # Parse files and export
                    player.conversations = player.parse_audio_files()
                    
                    # Merge VDF data again because parse_audio_files overwrites conversations
                    if player.vdf_loaded:
                        player.merge_vdf_data()
                        
                    if not player.conversations:
                        self.log_write("No conversations found in exported audio.\n")
                        return

                    export_data = {
                        "export_date": datetime.now().isoformat(),
                        "total_conversations": len(player.conversations),
                        "conversations": []
                    }
                    for convo_key, files in player.conversations.items():
                        conversation = player._export_build_conversation(convo_key, files, False, False)
                        export_data["conversations"].append(conversation)

                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with open(out_path, 'w', encoding='utf-8') as f:
                        json.dump(export_data, f, indent=2)

                    self.log_write(f"Exported {len(player.conversations)} conversations to {out_path}\n")
                    # Snapshot conversations export path for later use
                    try:
                        self._last_convos_json = out_path
                        self._maybe_enable_unmatched_button()
                    except Exception:
                        pass
                    # After conversations export completes, start voicelines pipeline
                    try:
                        self._start_voicelines_pipeline(audio_dir)
                    except Exception as e:
                        self.log_write(f"Voicelines pipeline failed to start: {e}\n")
                finally:
                    try:
                        temp_root.destroy()
                    except Exception:
                        pass
            except Exception as e:
                self.log_write(f"Error exporting conversations: {e}\n")

        threading.Thread(target=run_export, daemon=True).start()

    def _start_voicelines_pipeline(self, audio_dir):
        # Capture UI values on main thread BEFORE starting background thread
        consolidated_out = (self.voi_consolidated_entry.get().strip() if hasattr(self, 'voi_consolidated_entry') else "") or self.cfg.get("voicelines_consolidated_json", "")
        trans_dir = (self.trans_entry.get().strip() if hasattr(self, 'trans_entry') else "") or self.cfg.get("transcriptions_dir", "")
        custom_vocab = (self.voi_vocab_entry.get().strip() if hasattr(self, 'voi_vocab_entry') else "") or self.cfg.get("voicelines_custom_vocab", "")
        status_dir = self.status_entry.get().strip() if hasattr(self, 'status_entry') else self.cfg.get("status_dir", "")
        voi_retranscribe_flag = bool(self.voi_retranscribe_var.get()) if hasattr(self, 'voi_retranscribe_var') else True
        delete_json_flag = bool(self.delete_json_var.get()) if hasattr(self, 'delete_json_var') else False
        include_phantom_flag = bool(self.include_phantom_var.get()) if hasattr(self, 'include_phantom_var') else True
        tempdir_snapshot = self.tempdir
        
        # Capture VDF path
        vdf_path = os.path.join(self.tempdir, "citadel_generated_vo.txt") if self.tempdir else None

        # Orchestrate the voicelines organizer -> copy -> transcribe flow
        def run_vo():
            try:
                if not audio_dir or not os.path.isdir(audio_dir):
                    return

                # Snapshot audio dir early
                try:
                    self._last_audio_dir = audio_dir
                    self._maybe_enable_unmatched_button()
                except Exception:
                    pass

                # Prepare temp working paths
                voi_tmp_dir = os.path.join(tempdir_snapshot or os.getcwd(), "voicelines")
                try:
                    os.makedirs(voi_tmp_dir, exist_ok=True)
                except Exception:
                    pass
                organized_json = os.path.join(voi_tmp_dir, "organized.json")
                flat_json = os.path.join(voi_tmp_dir, "flat.json")
                copy_dir = os.path.join(voi_tmp_dir, "copy")

                if not trans_dir:
                    self.log_write("[Voicelines] Transcriptions Dir not set. Skipping voicelines pipeline.\n")
                    return
                # Use captured value or default to temp location
                final_consolidated_out = consolidated_out if consolidated_out else os.path.join(voi_tmp_dir, "voicelines_consolidated.json")

                # Import voicelines modules
                voi_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Voiceline Utilities"))
                if voi_root not in sys.path:
                    sys.path.insert(0, voi_root)

                # Organizer
                assets_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Assets"))
                alias_path = os.path.join(assets_dir, "character_mappings.json")
                topic_alias_path = os.path.join(assets_dir, "topic_alias.json")
                if not os.path.exists(topic_alias_path):
                    alt = os.path.join(assets_dir, "topic_mappings.json")
                    if os.path.exists(alt):
                        topic_alias_path = alt
                try:
                    import importlib as _importlib
                    _voi_mod = _importlib.import_module('modules.voice_line_organizer')
                    VoiceLineOrganizer = _voi_mod.VoiceLineOrganizer
                except Exception as e:
                    self.log_write(f"[Organizer] Import error: {e}\n")
                    return

                temp_root = tk.Tk()
                temp_root.withdraw()
                try:
                    organizer = VoiceLineOrganizer(temp_root)
                    # Disable internal organizer logging for performance
                    try:
                        organizer.log = lambda *args, **kwargs: None
                    except Exception:
                        pass
                    # Disable message boxes from the organizer module
                    try:
                        if hasattr(_voi_mod, 'messagebox') and hasattr(_voi_mod.messagebox, 'showinfo'):
                            _voi_mod.messagebox.showinfo = lambda *args, **kwargs: None
                    except Exception:
                        pass
                    # Null out per-file UI updates
                    try:
                        organizer.root.after = lambda *args, **kwargs: None
                    except Exception:
                        pass
                    try:
                        class _NullProgress:
                            def __setitem__(self, key, value):
                                return None
                        organizer.progress = _NullProgress()
                    except Exception:
                        pass
                    try:
                        class _NullLog:
                            def insert(self, *args, **kwargs):
                                return None
                            def see(self, *args, **kwargs):
                                return None
                            def config(self, *args, **kwargs):
                                return None
                        organizer.log_text = _NullLog()
                    except Exception:
                        pass
                    # Remove per-file progress logging wrapper to avoid overhead
                    organizer.alias_json_path.set(alias_path)
                    organizer.topic_alias_json_path.set(topic_alias_path)
                    organizer.source_folder_path.set(audio_dir)
                    organizer.output_json_path.set(organized_json)
                    if vdf_path:
                        organizer.vdf_path.set(vdf_path)
                    self.log_write("[Organizer] Starting...\n")
                    organizer.process_voice_lines()
                    self.log_write(f"[Organizer] Done. Output: {organized_json}\n")
                finally:
                    try:
                        temp_root.destroy()
                    except Exception:
                        pass

                # Copy
                try:
                    from modules import copy_voice_files as _copy
                except Exception as e:
                    self.log_write(f"[Copy] Import error: {e}\n")
                    return

                copy_stats = {"copied": 0, "last_report": 0}
                class _Writer:
                    def __init__(self, gui, prefix, stats):
                        self._gui = gui
                        self._prefix = prefix
                        self._stats = stats
                    def write(self, s):
                        s = str(s)
                        for line in s.splitlines():
                            if not line:
                                continue
                            text = line.strip()
                            if not text:
                                continue
                            try:
                                if text.startswith("Copied:"):
                                    self._stats["copied"] += 1
                                    # Report every 200 files to reduce UI churn
                                    if self._stats["copied"] - self._stats["last_report"] >= 200:
                                        self._stats["last_report"] = self._stats["copied"]
                                        self._gui.log_write(f"{self._prefix} Copied {self._stats['copied']} files...\n")
                                else:
                                    self._gui.log_write(f"{self._prefix} {text}\n")
                            except Exception:
                                pass
                    def flush(self):
                        pass

                _old_out, _old_err = sys.stdout, sys.stderr
                try:
                    sys.stdout, sys.stderr = _Writer(self, "[Copy]", copy_stats), _Writer(self, "[Copy]", copy_stats)
                    # Fast copy: prefer hardlink/symlink over byte copy to speed up
                    try:
                        orig_copy2 = _copy.shutil.copy2
                        def fast_copy2(src, dst, follow_symlinks=True):
                            try:
                                os.link(src, dst)
                                return dst
                            except Exception:
                                pass
                            try:
                                os.symlink(src, dst)
                                return dst
                            except Exception:
                                pass
                            return orig_copy2(src, dst, follow_symlinks=follow_symlinks)
                        _copy.shutil.copy2 = fast_copy2
                        try:
                            _copy.copy_voice_files(organized_json, audio_dir, copy_dir, flat_json)
                        finally:
                            _copy.shutil.copy2 = orig_copy2
                    except Exception:
                        _copy.copy_voice_files(organized_json, audio_dir, copy_dir, flat_json)
                finally:
                    sys.stdout, sys.stderr = _old_out, _old_err
                # Final copy summary
                try:
                    if copy_stats["copied"]:
                        self.log_write(f"[Copy] Copied total {copy_stats['copied']} files.\n")
                except Exception:
                    pass
                self.log_write(f"[Copy] Done. Copied -> {copy_dir}; Flat JSON -> {flat_json}\n")

                # Snapshot flat.json path for later use
                try:
                    if os.path.isfile(flat_json):
                        self._last_flat_json = flat_json
                        self._maybe_enable_unmatched_button()
                except Exception:
                    pass

                # Status mapping (using pre-captured values from main thread)
                # Always load status map for output purposes
                self.log_write(f"[Voicelines] Loading status from: {status_dir or '(not set)'}\n")
                status_map_sets = self._load_status_file(status_dir)
                self.log_write(f"[Voicelines] Loaded {len(status_map_sets)} status entries\n")

                # Build reprocess_status_map for output (always needed for status in output)
                reprocess_status_map = {}
                if status_map_sets:
                    for stem, sset in status_map_sets.items():
                        if "UPDATED" in sset:
                            reprocess_status_map[stem] = "UPDATED"
                        else:
                            reprocess_status_map[stem] = sorted(list(sset))[0]

                # Only set reprocess_statuses if flag is enabled (controls re-transcription)
                reprocess_statuses = None
                if voi_retranscribe_flag:
                    if status_map_sets:
                        reprocess_statuses = sorted({s for sset in status_map_sets.values() for s in sset})
                        self.log_write(f"[Transcribe] Status filtering enabled. Statuses: {', '.join(reprocess_statuses)}\n")
                else:
                    self.log_write("[Transcribe] Status filtering disabled. Existing JSONs will be reused; only new files will be transcribed.\n")

                # Pre-create expected JSON filenames to avoid accidental reprocess when legacy names exist (e.g., name.json vs name.mp3.json)
                try:
                    if os.path.isdir(trans_dir) and os.path.isfile(flat_json):
                        with open(flat_json, 'r', encoding='utf-8') as f:
                            flat_data = json.load(f)

                        def _collect_filenames(node, acc):
                            if isinstance(node, dict):
                                if 'filename' in node and isinstance(node['filename'], str):
                                    acc.add(node['filename'])
                                for v in node.values():
                                    _collect_filenames(v, acc)
                            elif isinstance(node, list):
                                for item in node:
                                    _collect_filenames(item, acc)
                        names = set()
                        _collect_filenames(flat_data, names)
                        for fn in names:
                            try:
                                expected = os.path.join(trans_dir, f"{fn}.json")
                                if os.path.exists(expected):
                                    continue
                                stem = os.path.splitext(fn)[0]
                                legacy = os.path.join(trans_dir, f"{stem}.json")
                                if os.path.exists(legacy):
                                    try:
                                        # Prefer hardlink/symlink; fall back to copy
                                        try:
                                            os.link(legacy, expected)
                                        except Exception:
                                            try:
                                                os.symlink(legacy, expected)
                                            except Exception:
                                                shutil.copy2(legacy, expected)
                                        self.log_write(f"[Transcribe] Linked legacy transcription for {fn} -> {os.path.basename(expected)}\n")
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                except Exception:
                    pass

                # Transcribe
                try:
                    from modules import transcribe_voice_files as _trans
                except Exception as e:
                    self.log_write(f"[Transcribe] Import error: {e}\n")
                    return

                tr_state = {"total": None, "last_bucket": -1}
                def progress_callback(file=None, current=None, total=None, status=None, error=None, complete=None, stats=None):
                    try:
                        if total is not None and tr_state["total"] is None:
                            tr_state["total"] = total
                        if error:
                            self.log_write(f"[Transcribe][ERROR] {error}\n")
                        # Throttle per-file progress to 10% buckets only
                        if current is not None and (total is not None or tr_state["total"] is not None):
                            t = total if total is not None else tr_state["total"]
                            if t:
                                pct = int(((current + 1) * 100) / t)
                                bucket = (pct // 10) * 10
                                if bucket >= 10 and bucket != tr_state["last_bucket"]:
                                    self.log_write(f"[Transcribe] {bucket}% ({current+1}/{t})\n")
                                    tr_state["last_bucket"] = bucket
                            return
                        # Log non-per-file status lines (setup/summary)
                        if status and (not status.startswith("Processing ")):
                            self.log_write(f"[Transcribe] {status}\n")
                        if complete:
                            self.log_write(f"[Transcribe] Complete. Stats: {stats}\n")
                    except Exception:
                        pass

                self.log_write("[Transcribe] Starting...\n")
                if vdf_path:
                    self.log_write(f"[Transcribe] Using VDF: {vdf_path}\n")
                _trans.transcribe_voice_files(
                    flat_json,
                    copy_dir,
                    force_reprocess=False,
                    progress_callback=progress_callback,
                    output_folder=trans_dir,
                    consolidated_json_path=final_consolidated_out,
                    custom_vocab_file=custom_vocab if custom_vocab else None,
                    reprocess_statuses=reprocess_statuses,
                    reprocess_status_map=reprocess_status_map,
                    vdf_path=vdf_path,
                    include_phantom=include_phantom_flag,
                    delete_json_on_vdf_match=delete_json_flag,
                    alias_path=alias_path,
                    topic_alias_path=topic_alias_path
                )
                self.log_write(f"[Transcribe] Consolidated JSON -> {final_consolidated_out}\n")

                # Snapshot consolidated JSON path for later use
                try:
                    if os.path.isfile(final_consolidated_out):
                        self._last_consolidated_json = final_consolidated_out
                        self._maybe_enable_unmatched_button()
                except Exception:
                    pass

                # Auto-generate coverage report
                try:
                    self._generate_coverage_report(audio_dir, final_consolidated_out, self._last_convos_json)
                except Exception as e:
                    self.log_write(f"[Coverage] Failed to generate coverage report: {e}\n")

            except Exception as e:
                self.log_write(f"[Voicelines] Pipeline error: {e}\n")

        threading.Thread(target=run_vo, daemon=True).start()

    def export_conversations_manual(self):
        # Prefer last run's output folder; else ask for a folder
        audio_dir = os.path.join(self.tempdir or "", "sounds", "vo") if self.tempdir else ""
        if not audio_dir or not os.path.isdir(audio_dir):
            picked = filedialog.askdirectory(title="Select exported conversations audio folder (sounds/vo)")
            if not picked:
                return
            audio_dir = picked
        self._start_conversations_export(audio_dir)

    def _start_generate_summaries(self, audio_dir):
        def run_gen():
            try:
                if not audio_dir or not os.path.isdir(audio_dir):
                    return

                convos_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Conversations Utilities"))
                if convos_path not in sys.path:
                    sys.path.insert(0, convos_path)
                try:
                    from convos import ConversationPlayer
                except Exception as e:
                    self.log_write(f"Failed to import ConversationPlayer: {e}\n")
                    return

                temp_root = tk.Tk()
                temp_root.withdraw()
                try:
                    player = ConversationPlayer(temp_root)
                    player.audio_dir = audio_dir
                    trans_dir = self.trans_entry.get().strip() if hasattr(self, 'trans_entry') else self.cfg.get("transcriptions_dir", "")
                    if trans_dir:
                        player.transcriptions_dir = trans_dir

                    status_dir = self.status_entry.get().strip() if hasattr(self, 'status_entry') else self.cfg.get("status_dir", "")
                    status_map = self._load_status_file(status_dir)
                    player.file_status_map = status_map if status_map else {}

                    # Parse files
                    player.conversations = player.parse_audio_files()
                    if not player.conversations:
                        self.log_write("No conversations found in exported audio.\n")
                        return

                    # Generate summaries only for missing or updated
                    updated_written = 0
                    missing_written = 0

                    def is_convo_updated(files):
                        try:
                            for file in files:
                                stem = os.path.splitext(os.path.basename(file['filename']))[0].lower()
                                if stem in player.file_status_map and player.file_status_map[stem]:
                                    return True
                        except Exception:
                            pass
                        return False

                    for convo_key, files in player.conversations.items():
                        path = player._summary_path(convo_key)
                        exists = os.path.exists(path)
                        updated = is_convo_updated(files)
                        if exists and not updated:
                            continue

                        conversation = player._export_build_conversation(convo_key, files, transcribe_all=False, generate_summaries=False)
                        summary = player._generate_conversation_summary(conversation)
                        player._write_summary(convo_key, summary)
                        if updated and exists:
                            updated_written += 1
                        else:
                            missing_written += 1

                    self.log_write(f"Summaries saved. Updated: {updated_written}, New: {missing_written}.\n")
                finally:
                    try:
                        temp_root.destroy()
                    except Exception:
                        pass
            except Exception as e:
                self.log_write(f"Error generating summaries: {e}\n")

        threading.Thread(target=run_gen, daemon=True).start()

    def generate_summaries_manual(self):
        # Prefer last run's output folder; else ask for a folder
        audio_dir = os.path.join(self.tempdir or "", "sounds", "vo") if self.tempdir else ""
        if not audio_dir or not os.path.isdir(audio_dir):
            picked = filedialog.askdirectory(title="Select exported conversations audio folder (sounds/vo)")
            if not picked:
                return
            audio_dir = picked
        self._start_generate_summaries(audio_dir)

    def _generate_coverage_report(self, audio_dir, consolidated_json, convos_json):
        """Generate coverage report and save to coverage.json in same folder as consolidated JSON."""
        try:
            # Determine output path
            consolidated_dir = os.path.dirname(consolidated_json)
            out_path = os.path.join(consolidated_dir, "coverage.json")

            # Collect all mp3s under audio_dir
            all_rel_paths = []
            total_files = 0
            for root, _, files in os.walk(audio_dir):
                for name in files:
                    if name.lower().endswith(".mp3"):
                        total_files += 1
                        full = os.path.join(root, name)
                        rel = os.path.relpath(full, audio_dir)
                        # Normalize to sounds/vo/... with forward slashes
                        rel_norm = ("sounds/vo/" + rel.replace(os.sep, "/")).replace("//", "/")
                        all_rel_paths.append((os.path.basename(name), rel_norm))

            # Load consolidated voicelines JSON and gather basenames
            voicelines_names = set()
            try:
                with open(consolidated_json, 'r', encoding='utf-8') as f:
                    consolidated_data = json.load(f)
                def _collect_filenames(node, acc):
                    if isinstance(node, dict):
                        if 'filename' in node and isinstance(node['filename'], str):
                            acc.add(os.path.basename(node['filename']))
                        for v in node.values():
                            _collect_filenames(v, acc)
                    elif isinstance(node, list):
                        for item in node:
                            _collect_filenames(item, acc)
                _collect_filenames(consolidated_data, voicelines_names)
            except Exception:
                pass

            # Load conversations JSON (optional) and gather basenames
            convo_names = set()
            if convos_json and os.path.isfile(convos_json):
                try:
                    with open(convos_json, 'r', encoding='utf-8') as f:
                        convo_data = json.load(f)
                    def _collect_conv_filenames(node, acc):
                        if isinstance(node, dict):
                            if 'filename' in node and isinstance(node['filename'], str):
                                acc.add(os.path.basename(node['filename']))
                            for v in node.values():
                                _collect_conv_filenames(v, acc)
                        elif isinstance(node, list):
                            for item in node:
                                _collect_conv_filenames(item, acc)
                    _collect_conv_filenames(convo_data, convo_names)
                except Exception:
                    pass

            matched = voicelines_names.union(convo_names)
            unmatched_paths = sorted({rel for bn, rel in all_rel_paths if bn not in matched})
            matched_count = len(matched)
            unmatched_count = len(unmatched_paths)

            # Organize unmatched files by folder structure
            unmatched_by_folder = {}
            for path in unmatched_paths:
                # Extract folder path (everything except the filename)
                folder = os.path.dirname(path)
                if folder not in unmatched_by_folder:
                    unmatched_by_folder[folder] = []
                unmatched_by_folder[folder].append(os.path.basename(path))

            # Calculate coverage percentage
            coverage_pct = (matched_count / total_files * 100) if total_files > 0 else 0.0

            # Create structured JSON output
            output_data = {
                "summary": {
                    "total_files": total_files,
                    "matched_files": matched_count,
                    "unmatched_files": unmatched_count,
                    "coverage_percentage": round(coverage_pct, 2),
                    "matched_in_voicelines": len(voicelines_names),
                    "matched_in_conversations": len(convo_names)
                },
                "unmatched_by_folder": {k: sorted(v) for k, v in sorted(unmatched_by_folder.items())},
                "unmatched_files": unmatched_paths
            }

            # Write output as JSON
            try:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
            except Exception:
                pass
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2)

            self.log_write(f"[Coverage] Audio files scanned: {total_files}\n")
            self.log_write(f"[Coverage] Matched: {matched_count} ({coverage_pct:.2f}%)\n")
            self.log_write(f"[Coverage] Unmatched: {unmatched_count} files in {len(unmatched_by_folder)} folders\n")
            self.log_write(f"[Coverage] Report -> {out_path}\n")

        except Exception as e:
            self.log_write(f"[Coverage][ERROR] {e}\n")
            raise

    def export_unmatched_voicelines(self):
        # Gather inputs on UI thread, then compute in background
        audio_dir = self._last_audio_dir if (self._last_audio_dir and os.path.isdir(self._last_audio_dir)) else ""
        consolidated_json = self._last_consolidated_json if (self._last_consolidated_json and os.path.isfile(self._last_consolidated_json)) else ""
        convos_json = self._last_convos_json if (self._last_convos_json and os.path.isfile(self._last_convos_json)) else ""

        if not audio_dir:
            picked = filedialog.askdirectory(title="Select exported conversations audio folder (sounds/vo)")
            if not picked:
                return
            audio_dir = picked

        if not consolidated_json:
            picked_consolidated = filedialog.askopenfilename(title="Select voicelines consolidated JSON", filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
            if not picked_consolidated:
                messagebox.showwarning("Missing consolidated JSON", "A consolidated JSON from the voicelines transcribe step is required.")
                return
            consolidated_json = picked_consolidated

        # Optional conversations JSON (skip if not provided)
        if not convos_json:
            picked_convos = filedialog.askopenfilename(title="Select conversations export JSON (optional)", filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
            if picked_convos:
                convos_json = picked_convos

        def run_unmatched():
            try:
                self._generate_coverage_report(audio_dir, consolidated_json, convos_json)
                # Show completion dialog
                try:
                    consolidated_dir = os.path.dirname(consolidated_json)
                    out_path = os.path.join(consolidated_dir, "coverage.json")
                    with open(out_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    summary = data.get("summary", {})
                    matched_count = summary.get("matched_files", 0)
                    total_files = summary.get("total_files", 0)
                    coverage_pct = summary.get("coverage_percentage", 0.0)
                    unmatched_count = summary.get("unmatched_files", 0)
                    self.after(0, lambda: messagebox.showinfo("Export Complete", f"Coverage: {matched_count}/{total_files} ({coverage_pct:.1f}%)\nUnmatched: {unmatched_count} files\nSaved to:\n{out_path}"))
                except Exception:
                    pass
            except Exception as e:
                self.log_write(f"[Coverage] Button export failed: {e}\n")

        threading.Thread(target=run_unmatched, daemon=True).start()

    def move_processed_audio_files(self):
        # Gather inputs
        audio_dir = self._last_audio_dir if (self._last_audio_dir and os.path.isdir(self._last_audio_dir)) else ""
        consolidated_json = self._last_consolidated_json if (self._last_consolidated_json and os.path.isfile(self._last_consolidated_json)) else ""
        convos_json = self._last_convos_json if (self._last_convos_json and os.path.isfile(self._last_convos_json)) else ""

        if not audio_dir:
            picked = filedialog.askdirectory(title="Select exported conversations audio folder (sounds/vo)")
            if not picked:
                return
            audio_dir = picked

        if not consolidated_json:
            picked_consolidated = filedialog.askopenfilename(title="Select voicelines consolidated JSON", filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
            if not picked_consolidated:
                messagebox.showwarning("Missing consolidated JSON", "A consolidated JSON from the voicelines transcribe step is required.")
                return
            consolidated_json = picked_consolidated

        # Optional convos JSON
        if not convos_json:
            picked_convos = filedialog.askopenfilename(title="Select conversations export JSON (optional)", filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
            if picked_convos:
                convos_json = picked_convos

        # Destination folder
        dest_dir = filedialog.askdirectory(title="Select destination folder for processed audio files")
        if not dest_dir:
            return

        def run_move():
            try:
                # Build processed basenames set from consolidated voicelines JSON
                processed_basenames = set()
                try:
                    with open(consolidated_json, 'r', encoding='utf-8') as f:
                        consolidated_data = json.load(f)
                    def _collect_filenames(node, acc):
                        if isinstance(node, dict):
                            if 'filename' in node and isinstance(node['filename'], str):
                                acc.add(os.path.basename(node['filename']))
                            for v in node.values():
                                _collect_filenames(v, acc)
                        elif isinstance(node, list):
                            for item in node:
                                _collect_filenames(item, acc)
                    _collect_filenames(consolidated_data, processed_basenames)
                except Exception:
                    pass

                # Include conversation filenames if available
                if convos_json and os.path.isfile(convos_json):
                    try:
                        with open(convos_json, 'r', encoding='utf-8') as f:
                            convo_data = json.load(f)
                        def _collect_conv_filenames(node, acc):
                            if isinstance(node, dict):
                                if 'filename' in node and isinstance(node['filename'], str):
                                    acc.add(os.path.basename(node['filename']))
                                for v in node.values():
                                    _collect_conv_filenames(v, acc)
                            elif isinstance(node, list):
                                for item in node:
                                    _collect_conv_filenames(item, acc)
                        _collect_conv_filenames(convo_data, processed_basenames)
                    except Exception:
                        pass

                # Walk audio_dir and select actual files to move by basename membership
                files_to_move = []
                for root, _, files in os.walk(audio_dir):
                    for name in files:
                        if name.lower().endswith('.mp3') and name in processed_basenames:
                            files_to_move.append(os.path.join(root, name))

                if not files_to_move:
                    self.log_write("[Move] No processed files found to move.\n")
                    try:
                        self.after(0, lambda: messagebox.showinfo("Move", "No processed files found to move."))
                    except Exception:
                        pass
                    return

                # Ensure destination exists
                try:
                    os.makedirs(dest_dir, exist_ok=True)
                except Exception:
                    pass

                def _unique_dest_path(base_dir, filename):
                    stem, ext = os.path.splitext(filename)
                    candidate = os.path.join(base_dir, filename)
                    if not os.path.exists(candidate):
                        return candidate
                    i = 1
                    while True:
                        cand = os.path.join(base_dir, f"{stem}_{i}{ext}")
                        if not os.path.exists(cand):
                            return cand
                        i += 1

                moved = 0
                errors = 0
                for src in files_to_move:
                    try:
                        dest_path = _unique_dest_path(dest_dir, os.path.basename(src))
                        shutil.move(src, dest_path)
                        moved += 1
                        if moved % 200 == 0:
                            self.log_write(f"[Move] Moved {moved}/{len(files_to_move)}...\n")
                    except Exception as e:
                        errors += 1
                        self.log_write(f"[Move][ERROR] {src}: {e}\n")

                self.log_write(f"[Move] Completed. Moved {moved} files to {dest_dir}. Errors: {errors}.\n")
                try:
                    self.after(0, lambda: messagebox.showinfo("Move Complete", f"Moved {moved} files to:\n{dest_dir}\nErrors: {errors}"))
                except Exception:
                    pass
            except Exception as e:
                self.log_write(f"[Move][ERROR] {e}\n")

        threading.Thread(target=run_move, daemon=True).start()

    def on_stop(self):
        if self.process:
            try:
                self.process.terminate()
                # give it a second to die, then kill
                for _ in range(10):
                    if self.process.poll() is not None:
                        break
                    time.sleep(0.1)
                if self.process.poll() is None:
                    self.process.kill()
            except Exception as e:
                self.log_write(f"Failed to stop process: {e}\n")

    def export_category_tree(self):
        """Export a tree file showing all voiceline categories for debugging."""
        try:
            # Import from voicelines module
            voi_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Voiceline Utilities"))
            if voi_root not in sys.path:
                sys.path.insert(0, voi_root)
            
            from voicelines import export_category_tree as do_export
            
            # Prompt for input JSON (the organized voicelines output)
            input_json_path = filedialog.askopenfilename(
                title="Select Organized Voicelines JSON",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
            )
            if not input_json_path:
                return
            
            # Prompt for save location
            output_path = filedialog.asksaveasfilename(
                title="Save Category Tree As",
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
                initialfile="category_tree.txt"
            )
            if not output_path:
                return
            
            # Call the export function with both paths
            success = do_export(input_json_path, output_path)
            
            if success:
                self.log_write(f"[Category Tree] Exported to: {output_path}\n")
                messagebox.showinfo("Export Complete", f"Category tree exported to:\n{output_path}")
            else:
                self.log_write("[Category Tree] Export failed.\n")
                messagebox.showerror("Export Failed", "Failed to export category tree.")
        except Exception as e:
            self.log_write(f"[Category Tree] Error: {e}\n")
            messagebox.showerror("Error", f"Failed to export category tree: {e}")

    def on_close(self):
        if self.process:
            if not messagebox.askyesno("Quit", "A process is running. Quit and terminate it?"):
                return
            try:
                self.process.terminate()
            except Exception:
                pass

        # delete tempdir if exists
        if self.tempdir and os.path.exists(self.tempdir):
            try:
                shutil.rmtree(self.tempdir)
                print(f"Deleted tempdir: {self.tempdir}")
            except Exception as e:
                print(f"Failed to delete tempdir {self.tempdir}: {e}")

        self.destroy()


def main():
    app = BatchGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
