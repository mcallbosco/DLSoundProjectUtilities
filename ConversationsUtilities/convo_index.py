import os
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pygame

# Reuse constants similar to convos.py
TRANSCRIPTIONS_DIR = "transcriptions"


def _load_transcription(file_path):
    """Load a transcription from a text file, trying multiple encodings."""
    if not os.path.exists(file_path):
        return ""

    encodings = [
        "utf-8",
        "utf-8-sig",
        "latin-1",
        "cp1252",
        "iso-8859-1",
        "iso-8859-15",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "cp437",
    ]

    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read().replace("\x00", "").strip()
        except Exception:
            continue

    try:
        with open(file_path, "rb") as f:
            return f.read().decode("utf-8", errors="replace").replace("\x00", "").strip()
    except Exception:
        return ""


def _hex_key_from_convo_id(convo_id):
    """Create a numeric key from a hex-like conversation id for sorting.

    Removes non-hex chars (e.g., dots) and parses as hex. Falls back to string.
    """
    if not isinstance(convo_id, str):
        return (0, str(convo_id))
    cleaned = re.sub(r"[^0-9a-fA-F]", "", convo_id)
    if cleaned == "":
        return (0, convo_id)
    try:
        return (int(cleaned, 16), convo_id)
    except ValueError:
        return (0, convo_id)


class ConversationIndexApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Conversation Index")
        self.root.geometry("800x600")

        self.audio_dir = os.getcwd()
        self.playing = False
        self.current_playlist = []
        self.current_track_index = 0

        self._build_ui()
        pygame.mixer.init()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        top = ttk.Frame(self.root, padding="10")
        top.pack(fill=tk.BOTH, expand=True)

        dir_frame = ttk.LabelFrame(top, text="Audio Files Directory", padding="10")
        dir_frame.pack(fill=tk.X, pady=5)

        self.dir_var = tk.StringVar(value=self.audio_dir)
        ttk.Entry(dir_frame, textvariable=self.dir_var, width=60).grid(row=0, column=0, padx=5, pady=5, sticky=tk.EW)
        ttk.Button(dir_frame, text="Browse...", command=self._browse_dir).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(dir_frame, text="Load", command=self._load_conversations).grid(row=0, column=2, padx=5, pady=5)
        dir_frame.columnconfigure(0, weight=1)

        table_frame = ttk.LabelFrame(top, text="Conversations", padding="10")
        table_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        columns = ("convo_id", "first_line")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        self.tree.heading("convo_id", text="Conversation ID (hex)")
        self.tree.heading("first_line", text="First Line")
        self.tree.column("convo_id", width=220, anchor=tk.W)
        self.tree.column("first_line", width=520, anchor=tk.W)

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # Double-click to play
        self.tree.bind("<Double-1>", self._on_double_click)
        # Right-click context menu for conversation deletion
        self.convo_menu = tk.Menu(self.root, tearoff=0)
        self.convo_menu.add_command(label="Mark Conversation for Deletion", command=self._mark_selected_convos_for_deletion)
        self.convo_menu.add_command(label="Unmark Conversation", command=self._unmark_selected_convos)
        self.tree.bind("<Button-3>", self._on_convo_right_click)

        # Selection change to show voicelines
        self.tree.bind("<<TreeviewSelect>>", self._on_convo_select)

        # Voicelines panel
        lines_frame = ttk.LabelFrame(top, text="Voicelines", padding="10")
        lines_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        line_columns = ("part", "speaker", "filename", "path")
        self.lines_tree = ttk.Treeview(lines_frame, columns=line_columns, show="headings", selectmode="extended")
        self.lines_tree.heading("part", text="Part")
        self.lines_tree.heading("speaker", text="Speaker")
        self.lines_tree.heading("filename", text="Filename")
        self.lines_tree.heading("path", text="Path")
        self.lines_tree.column("part", width=60, anchor=tk.W)
        self.lines_tree.column("speaker", width=120, anchor=tk.W)
        self.lines_tree.column("filename", width=260, anchor=tk.W)
        self.lines_tree.column("path", width=320, anchor=tk.W)

        l_vsb = ttk.Scrollbar(lines_frame, orient="vertical", command=self.lines_tree.yview)
        self.lines_tree.configure(yscrollcommand=l_vsb.set)
        self.lines_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        l_vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # Context menu for deletion
        self.deletion_menu = tk.Menu(self.root, tearoff=0)
        self.deletion_menu.add_command(label="Mark for Deletion", command=self._mark_selected_for_deletion)
        self.deletion_menu.add_command(label="Unmark for Deletion", command=self._unmark_selected)
        self.lines_tree.bind("<Button-3>", self._on_lines_right_click)

        # Deletion list state and export (voicelines)
        self.deletion_set = set()  # store unique keys (full paths)
        self.deletion_list = []    # list of dicts for export

        # Conversation deletion tracking
        self.deleted_convo_ids = set()

        action_bar = ttk.Frame(top)
        action_bar.pack(fill=tk.X, pady=(0, 5))
        self.deletion_count_var = tk.StringVar(value="Marked voicelines for deletion: 0 | Conversations: 0")
        ttk.Label(action_bar, textvariable=self.deletion_count_var).pack(side=tk.LEFT)
        ttk.Button(action_bar, text="Export Deleted Conversations", command=self._export_deleted_conversations).pack(side=tk.RIGHT)
        ttk.Button(action_bar, text="Export Deletion List", command=self._export_deletion_list).pack(side=tk.RIGHT)

        self.status_var = tk.StringVar(value="Select a directory and click Load")
        ttk.Label(top, textvariable=self.status_var).pack(fill=tk.X, pady=5)

    def _browse_dir(self):
        directory = filedialog.askdirectory(initialdir=self.audio_dir, title="Select Audio Files Directory")
        if directory:
            self.dir_var.set(directory)

    def _load_conversations(self):
        base_dir = self.dir_var.get()
        if not os.path.isdir(base_dir):
            messagebox.showerror("Error", f"Invalid directory: {base_dir}")
            return

        self.audio_dir = base_dir
        self.tree.delete(*self.tree.get_children())

        try:
            character_dirs = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
        except Exception as e:
            messagebox.showerror("Error", f"Could not read directory: {base_dir}\n{e}")
            return

        # Filename pattern from convos.py
        filename_pattern = r"(\d+)-([^-]+)-([0-9A-F\.]+)-(.+)\.ogg"

        # Map convo_id -> {first_line: str, best_part: int}
        convo_first_lines = {}

        for char_dir in character_dirs:
            char_path = os.path.join(base_dir, char_dir)
            try:
                convo_dirs = [d for d in os.listdir(char_path) if os.path.isdir(os.path.join(char_path, d))]
            except Exception:
                continue

            for convo_id in convo_dirs:
                convo_path = os.path.join(char_path, convo_id)
                try:
                    audio_files = [f for f in os.listdir(convo_path) if f.lower().endswith(".ogg")]
                except Exception:
                    continue

                if not audio_files:
                    continue

                # Find the earliest part and derive first line
                for filename in audio_files:
                    m = re.match(filename_pattern, filename)
                    if not m:
                        continue
                    part_str, speaker, file_id, text_snippet = m.groups()
                    try:
                        part_num = int(part_str)
                    except ValueError:
                        part_num = 999999

                    transcript_filename = os.path.splitext(filename)[0] + ".txt"
                    transcript_path = os.path.join(convo_path, transcript_filename)
                    transcript_text = _load_transcription(transcript_path) if os.path.exists(transcript_path) else ""

                    first_line_candidate = transcript_text.strip() if transcript_text else text_snippet.strip()
                    if first_line_candidate:
                        current = convo_first_lines.get(convo_id)
                        if current is None or part_num < current["best_part"]:
                            convo_first_lines[convo_id] = {
                                "first_line": first_line_candidate,
                                "best_part": part_num,
                            }

        # Sort by hex value of convo_id
        sorted_items = sorted(convo_first_lines.items(), key=lambda kv: _hex_key_from_convo_id(kv[0]))

        for convo_id, info in sorted_items:
            self.tree.insert("", tk.END, values=(convo_id, info["first_line"]))

        self.status_var.set(f"Loaded {len(sorted_items)} conversations from {base_dir}")

    def _on_convo_select(self, event):
        # Populate voicelines table for selected conversation (first selected row)
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        if not values:
            return
        convo_id = values[0]
        parts = self._collect_parts_for_convo(convo_id, include_speaker=True)
        self._populate_lines_tree(parts)

    def _on_double_click(self, event):
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        values = self.tree.item(item_id, "values")
        if not values:
            return
        convo_id = values[0]
        self._play_conversation(convo_id)

    def _play_conversation(self, convo_id):
        # Stop current playback if any
        if self.playing:
            self.playing = False
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
            time.sleep(0.1)

        # Build playlist: gather all parts across character folders matching convo_id
        base_dir = self.audio_dir
        try:
            character_dirs = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
        except Exception as e:
            messagebox.showerror("Error", f"Could not read directory: {base_dir}\n{e}")
            return

        parts = self._collect_parts_for_convo(convo_id)

        if not parts:
            messagebox.showinfo("Info", f"No audio files found for conversation {convo_id}")
            return

        parts.sort(key=lambda x: (x[0], x[1]))
        self.current_playlist = [p[2] for p in parts]
        self.current_track_index = 0
        self.playing = True
        self.status_var.set(f"Playing conversation {convo_id} ({len(self.current_playlist)} parts)")
        threading.Thread(target=self._playback_thread, daemon=True).start()

    def _collect_parts_for_convo(self, convo_id, include_speaker=False):
        base_dir = self.audio_dir
        try:
            character_dirs = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
        except Exception:
            character_dirs = []
        filename_pattern = r"(\d+)-([^-]+)-([0-9A-F\.]+)-(.+)\.ogg"
        parts = []
        for char_dir in character_dirs:
            convo_path = os.path.join(base_dir, char_dir, convo_id)
            if not os.path.isdir(convo_path):
                continue
            try:
                audio_files = [f for f in os.listdir(convo_path) if f.lower().endswith(".ogg")]
            except Exception:
                continue
            for filename in audio_files:
                m = re.match(filename_pattern, filename)
                if not m:
                    continue
                part_str, speaker, file_id, text_snippet = m.groups()
                try:
                    part_num = int(part_str)
                except ValueError:
                    part_num = 999999
                full_path = os.path.join(convo_path, filename)
                if include_speaker:
                    parts.append((part_num, filename.lower(), full_path, speaker, filename))
                else:
                    parts.append((part_num, filename.lower(), full_path))
        return parts

    def _populate_lines_tree(self, parts):
        # Clear existing
        for iid in self.lines_tree.get_children():
            self.lines_tree.delete(iid)
        # Sort and insert
        parts.sort(key=lambda x: (x[0], x[1]))
        for p in parts:
            if len(p) >= 5:
                part_num, _, full_path, speaker, filename = p
            else:
                part_num, _, full_path = p
                speaker, filename = "", os.path.basename(full_path)
            iid = self.lines_tree.insert("", tk.END, values=(part_num, speaker, filename, full_path))
            if full_path in self.deletion_set:
                self.lines_tree.item(iid, tags=("marked",))
        # Style marked items
        self.lines_tree.tag_configure("marked", foreground="red")

    def _on_lines_right_click(self, event):
        row = self.lines_tree.identify_row(event.y)
        if row:
            if row not in self.lines_tree.selection():
                self.lines_tree.selection_set(row)
            try:
                self.deletion_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.deletion_menu.grab_release()

    def _mark_selected_for_deletion(self):
        sel = self.lines_tree.selection()
        for iid in sel:
            vals = self.lines_tree.item(iid, "values")
            if not vals or len(vals) < 4:
                continue
            part, speaker, filename, path = vals
            if path not in self.deletion_set:
                self.deletion_set.add(path)
                self.deletion_list.append({
                    "part": int(part) if str(part).isdigit() else part,
                    "speaker": speaker,
                    "filename": filename,
                    "path": path,
                })
            self.lines_tree.item(iid, tags=("marked",))
        self._update_deletion_count()

    def _unmark_selected(self):
        sel = self.lines_tree.selection()
        to_remove = set()
        for iid in sel:
            vals = self.lines_tree.item(iid, "values")
            if not vals or len(vals) < 4:
                continue
            path = vals[3]
            if path in self.deletion_set:
                self.deletion_set.remove(path)
                to_remove.add(path)
            self.lines_tree.item(iid, tags=())
        if to_remove:
            self.deletion_list = [d for d in self.deletion_list if d.get("path") not in to_remove]
        self._update_deletion_count()

    def _update_deletion_count(self):
        self.deletion_count_var.set(f"Marked voicelines for deletion: {len(self.deletion_set)} | Conversations: {len(self.deleted_convo_ids)}")

    def _export_deletion_list(self):
        if not self.deletion_list:
            messagebox.showinfo("Info", "No voicelines marked for deletion.")
            return
        from tkinter import filedialog as fd
        import json
        file_path = fd.asksaveasfilename(
            title="Export Deletion List",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="deletion_list.json",
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump({"deletions": self.deletion_list}, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("Success", f"Exported {len(self.deletion_list)} items to {file_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export: {e}")

    def _on_convo_right_click(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            try:
                self.convo_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.convo_menu.grab_release()

    def _mark_selected_convos_for_deletion(self):
        sel = self.tree.selection()
        for iid in list(sel):
            vals = self.tree.item(iid, "values")
            if not vals:
                continue
            convo_id = vals[0]
            if convo_id not in self.deleted_convo_ids:
                self.deleted_convo_ids.add(convo_id)
            # Remove from UI immediately
            self.tree.delete(iid)
        self._update_deletion_count()
        # Clear voicelines panel if the current selection was removed
        if not self.tree.selection():
            self._populate_lines_tree([])

    def _unmark_selected_convos(self):
        # No-op in UI list since removed rows are gone; provide ability to remove from tracking
        # This action removes IDs from the deleted list but does not restore rows (keeps UX simple)
        # User can reload directory to rebuild full list if needed.
        if not self.deleted_convo_ids:
            return
        # Simple dialog to clear all marked conversations
        result = messagebox.askyesno("Unmark", "Unmark ALL marked conversations for deletion?")
        if result:
            self.deleted_convo_ids.clear()
            self._update_deletion_count()

    def _export_deleted_conversations(self):
        if not self.deleted_convo_ids:
            messagebox.showinfo("Info", "No conversations marked for deletion.")
            return
        from tkinter import filedialog as fd
        import json
        file_path = fd.asksaveasfilename(
            title="Export Deleted Conversations",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="deleted_conversations.json",
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump({"deleted_conversations": sorted(self.deleted_convo_ids)}, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("Success", f"Exported {len(self.deleted_convo_ids)} conversation IDs to {file_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export: {e}")

    def _playback_thread(self):
        while self.playing and self.current_track_index < len(self.current_playlist):
            current_file = self.current_playlist[self.current_track_index]
            try:
                pygame.mixer.music.load(current_file)
                pygame.mixer.music.play()
                filename = os.path.basename(current_file)
                self.root.after(0, lambda f=filename: self.status_var.set(f"Playing: {f}"))
                while pygame.mixer.music.get_busy() and self.playing:
                    time.sleep(0.1)
                if self.playing:
                    self.current_track_index += 1
            except Exception as e:
                self.root.after(0, lambda err=str(e): messagebox.showerror("Playback Error", err))
                break
        if self.playing:
            self.playing = False
            self.root.after(0, lambda: self.status_var.set("Playback complete"))

    def _on_close(self):
        self.playing = False
        try:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ConversationIndexApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()


