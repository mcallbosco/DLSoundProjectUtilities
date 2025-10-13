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


def load_config():
    default = {
        "source2viewer_binary": "",
        "vpk_path": "/home/mcall/Apps/SteamFolder/steamapps/common/Deadlock/game/citadel/pak01_dir.vpk",
        "file_filter": "sounds\\vo",
        "status_dir": "",
        "transcriptions_dir": "",
        "retranscribe_on_status": True,
        "character_mappings_file": "",
        "conversations_export_json": ""
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

        # Retranscribe checkbox
        self.retranscribe_var = tk.BooleanVar(value=bool(self.cfg.get("retranscribe_on_status", True)))
        tk.Checkbutton(frm, text="Re-transcribe when status present", variable=self.retranscribe_var).grid(row=6, column=0, columnspan=2, sticky=tk.W)

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

    def log_write(self, s):
        try:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, s)
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)
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
            if not status_dir or not os.path.isdir(status_dir):
                return {}
            candidates = []
            for name in os.listdir(status_dir):
                if name.lower().endswith((".txt", ".log", ".md")):
                    candidates.append(os.path.join(status_dir, name))
            if not candidates:
                return {}
            newest = max(candidates, key=os.path.getmtime)
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
            return status_map
        except Exception:
            return {}

    def _start_conversations_export(self, audio_dir):
        # Export in background to keep UI responsive
        def run_export():
            try:
                if not audio_dir or not os.path.isdir(audio_dir):
                    return

                # Determine output JSON path
                out_path = (self.convos_json_entry.get().strip() if hasattr(self, 'convos_json_entry') else "") or self.cfg.get("conversations_export_json", "")
                if not out_path:
                    out_path = os.path.join(audio_dir, "all_conversations.json")

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
                    trans_dir = self.trans_entry.get().strip() if hasattr(self, 'trans_entry') else self.cfg.get("transcriptions_dir", "")
                    if trans_dir:
                        player.transcriptions_dir = trans_dir

                    # Load newest status file if provided
                    status_dir = self.status_entry.get().strip() if hasattr(self, 'status_entry') else self.cfg.get("status_dir", "")
                    status_map = self._load_status_file(status_dir)
                    player.file_status_map = status_map

                    # Snapshot retranscribe flag
                    try:
                        player._retranscribe_on_status_snapshot = bool(self.retranscribe_var.get())
                    except Exception:
                        player._retranscribe_on_status_snapshot = True

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
                finally:
                    try:
                        temp_root.destroy()
                    except Exception:
                        pass
            except Exception as e:
                self.log_write(f"Error exporting conversations: {e}\n")

        threading.Thread(target=run_export, daemon=True).start()

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
