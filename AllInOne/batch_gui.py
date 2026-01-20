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


def load_config():
    default = {
        "source2viewer_binary": "",
        "vpk_path": "/home/mcall/Apps/SteamFolder/steamapps/common/Deadlock/game/citadel/pak01_dir.vpk",
        "file_filter": "sounds\\vo",
        "status_dir": "",
        "transcriptions_dir": "",
        "retranscribe_on_status": True,
        "character_mappings_file": "",
        "conversations_export_json": "",
        "voicelines_consolidated_json": "",
        "voicelines_custom_vocab": "",
        "voicelines_retranscribe_on_status": True
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

        # Conversations JSON export path
        tk.Label(frm, text="Convos JSON Path: ").grid(row=5, column=0, sticky=tk.W)
        self.convos_json_entry = tk.Entry(frm, width=60)
        self.convos_json_entry.grid(row=5, column=1, padx=(4, 0), sticky=tk.W)
        self.convos_json_entry.insert(0, self.cfg.get("conversations_export_json", ""))
        tk.Button(frm, text="Save As...", command=self.browse_convos_json).grid(row=5, column=2, padx=6)

        # Voicelines consolidated JSON path
        tk.Label(frm, text="Voicelines Consolidated JSON: ").grid(row=6, column=0, sticky=tk.W)
        self.voi_consolidated_entry = tk.Entry(frm, width=60)
        self.voi_consolidated_entry.grid(row=6, column=1, padx=(4, 0), sticky=tk.W)
        self.voi_consolidated_entry.insert(0, self.cfg.get("voicelines_consolidated_json", ""))
        tk.Button(frm, text="Save As...", command=self.browse_voicelines_consolidated_json).grid(row=6, column=2, padx=6)

        # Voicelines custom vocabulary file
        tk.Label(frm, text="Voicelines Custom Vocabulary: ").grid(row=7, column=0, sticky=tk.W)
        self.voi_vocab_entry = tk.Entry(frm, width=60)
        self.voi_vocab_entry.grid(row=7, column=1, padx=(4, 0), sticky=tk.W)
        self.voi_vocab_entry.insert(0, self.cfg.get("voicelines_custom_vocab", ""))
        tk.Button(frm, text="Browse...", command=self.browse_voicelines_vocab).grid(row=7, column=2, padx=6)

        # Retranscribe checkbox
        self.retranscribe_var = tk.BooleanVar(value=bool(self.cfg.get("retranscribe_on_status", True)))
        tk.Checkbutton(frm, text="Re-transcribe when status present", variable=self.retranscribe_var).grid(row=8, column=0, columnspan=2, sticky=tk.W)

        # Voicelines retranscribe checkbox
        self.voi_retranscribe_var = tk.BooleanVar(value=bool(self.cfg.get("voicelines_retranscribe_on_status", True)))
        tk.Checkbutton(frm, text="Re-transcribe voicelines when status present", variable=self.voi_retranscribe_var).grid(row=9, column=0, columnspan=2, sticky=tk.W)

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

    def on_save_config(self):
        self.cfg["source2viewer_binary"] = self.bin_entry.get().strip()
        self.cfg["vpk_path"] = self.vpk_entry.get().strip()
        self.cfg["file_filter"] = self.filter_entry.get().strip()
        self.cfg["status_dir"] = self.status_entry.get().strip()
        self.cfg["transcriptions_dir"] = self.trans_entry.get().strip()
        self.cfg["retranscribe_on_status"] = bool(self.retranscribe_var.get())
        self.cfg["conversations_export_json"] = self.convos_json_entry.get().strip()
        self.cfg["voicelines_consolidated_json"] = self.voi_consolidated_entry.get().strip()
        self.cfg["voicelines_custom_vocab"] = self.voi_vocab_entry.get().strip()
        self.cfg["voicelines_retranscribe_on_status"] = bool(self.voi_retranscribe_var.get())
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

                    # Use captured retranscribe flag
                    player._retranscribe_on_status_snapshot = retranscribe_flag

                    # Parse files and export
                    player.conversations = player.parse_audio_files()
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
        tempdir_snapshot = self.tempdir

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
                _trans.transcribe_voice_files(
                    flat_json,
                    copy_dir,
                    force_reprocess=False,
                    progress_callback=progress_callback,
                    output_folder=trans_dir,
                    consolidated_json_path=final_consolidated_out,
                    custom_vocab_file=custom_vocab if custom_vocab else None,
                    reprocess_statuses=reprocess_statuses,
                    reprocess_status_map=reprocess_status_map
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
