import os
import re
import json
import threading
import subprocess
import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class VoiceLineParserApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Overwatch Voiceline Parser")
        self.root.geometry("900x600")

        # State
        self.source_root = tk.StringVar()
        self.audio_output_dir = tk.StringVar()
        self.json_output_path = tk.StringVar()
        self.workers = tk.IntVar(value=4)
        self.json_only = tk.BooleanVar(value=False)
        self.category_alias_path = tk.StringVar()
        self.category_exclude_path = tk.StringVar()
        self.target_exclude_path = tk.StringVar()

        # UI
        self._build_ui()

        # Internal
        self._log_lock = threading.Lock()

    # UI construction
    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding="10")
        container.pack(fill=tk.BOTH, expand=True)

        # File selectors
        file_frame = ttk.LabelFrame(container, text="Paths", padding="10")
        file_frame.pack(fill=tk.X, padx=5, pady=5)

        # Source root
        ttk.Label(file_frame, text="Source root (characters folder):").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(file_frame, textvariable=self.source_root, width=80).grid(row=0, column=1, padx=5, pady=4)
        ttk.Button(file_frame, text="Browse", command=self._browse_source).grid(row=0, column=2, padx=5, pady=4)

        # Audio output dir
        ttk.Label(file_frame, text="Consolidated MP3 output folder:").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(file_frame, textvariable=self.audio_output_dir, width=80).grid(row=1, column=1, padx=5, pady=4)
        ttk.Button(file_frame, text="Browse", command=self._browse_audio_output).grid(row=1, column=2, padx=5, pady=4)

        # JSON output path
        ttk.Label(file_frame, text="Output JSON path:").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Entry(file_frame, textvariable=self.json_output_path, width=80).grid(row=2, column=1, padx=5, pady=4)
        ttk.Button(file_frame, text="Browse", command=self._browse_json_output).grid(row=2, column=2, padx=5, pady=4)

        # Category alias JSON (optional)
        ttk.Label(file_frame, text="Category alias JSON (optional):").grid(row=3, column=0, sticky=tk.W, pady=4)
        ttk.Entry(file_frame, textvariable=self.category_alias_path, width=80).grid(row=3, column=1, padx=5, pady=4)
        ttk.Button(file_frame, text="Browse", command=self._browse_category_alias).grid(row=3, column=2, padx=5, pady=4)

        # Category exclusion JSON (optional)
        ttk.Label(file_frame, text="Category exclusion JSON (optional):").grid(row=4, column=0, sticky=tk.W, pady=4)
        ttk.Entry(file_frame, textvariable=self.category_exclude_path, width=80).grid(row=4, column=1, padx=5, pady=4)
        ttk.Button(file_frame, text="Browse", command=self._browse_category_exclude).grid(row=4, column=2, padx=5, pady=4)

        # Target exclusion JSON (optional)
        ttk.Label(file_frame, text="Target exclusion JSON (optional):").grid(row=5, column=0, sticky=tk.W, pady=4)
        ttk.Entry(file_frame, textvariable=self.target_exclude_path, width=80).grid(row=5, column=1, padx=5, pady=4)
        ttk.Button(file_frame, text="Browse", command=self._browse_target_exclude).grid(row=5, column=2, padx=5, pady=4)

        # Options
        options_frame = ttk.LabelFrame(container, text="Options", padding="10")
        options_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(options_frame, text="Parallel workers (conversion):").grid(row=0, column=0, sticky=tk.W)
        ttk.Spinbox(options_frame, from_=1, to=16, textvariable=self.workers, width=5).grid(row=0, column=1, sticky=tk.W, padx=6)
        ttk.Checkbutton(options_frame, text="Export JSON only (skip audio conversion)", variable=self.json_only).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=4)

        # Controls
        controls = ttk.Frame(container, padding="5")
        controls.pack(fill=tk.X)
        ttk.Button(controls, text="Run", command=self._start).pack(side=tk.LEFT)

        # Progress
        prog_frame = ttk.Frame(container, padding="5")
        prog_frame.pack(fill=tk.X)
        self.progress = ttk.Progressbar(prog_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.progress.pack(fill=tk.X)

        # Log
        log_frame = ttk.LabelFrame(container, text="Log", padding="8")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # Browsers
    def _browse_source(self) -> None:
        folder = filedialog.askdirectory(title="Select Overwatch source root (folder containing character folders)")
        if folder:
            self.source_root.set(folder)

    def _browse_audio_output(self) -> None:
        folder = filedialog.askdirectory(title="Select MP3 output folder")
        if folder:
            self.audio_output_dir.set(folder)

    def _browse_json_output(self) -> None:
        path = filedialog.asksaveasfilename(title="Save Output JSON", defaultextension=".json", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.json_output_path.set(path)

    def _browse_category_alias(self) -> None:
        path = filedialog.askopenfilename(title="Select Category Alias JSON", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.category_alias_path.set(path)

    def _browse_category_exclude(self) -> None:
        path = filedialog.askopenfilename(title="Select Category Exclusion JSON", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.category_exclude_path.set(path)

    def _browse_target_exclude(self) -> None:
        path = filedialog.askopenfilename(title="Select Target Exclusion JSON", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.target_exclude_path.set(path)

    # Logging
    def _log(self, message: str) -> None:
        with self._log_lock:
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)

    # Run
    def _start(self) -> None:
        if not self._validate_inputs():
            return

        # Check ffmpeg only if we will convert audio
        if not self.json_only.get():
            if not self._ffmpeg_available():
                messagebox.showerror("ffmpeg not found", "ffmpeg must be installed and on PATH.")
                return

        threading.Thread(target=self._run_parser_thread, daemon=True).start()

    def _validate_inputs(self) -> bool:
        if not self.source_root.get():
            messagebox.showwarning("Missing input", "Please select a Source root folder.")
            return False
        if not os.path.isdir(self.source_root.get()):
            messagebox.showwarning("Invalid path", "Source root does not exist.")
            return False
        # audio output dir only required when not JSON-only
        if not self.json_only.get() and not self.audio_output_dir.get():
            messagebox.showwarning("Missing input", "Please select a Consolidated MP3 output folder or enable JSON-only export.")
            return False
        if not self.json_output_path.get():
            messagebox.showwarning("Missing input", "Please select an Output JSON path.")
            return False
        # Optional: validate alias/exclude file existence if provided
        if self.category_alias_path.get() and not os.path.isfile(self.category_alias_path.get()):
            messagebox.showwarning("Invalid path", "Category alias JSON path does not exist.")
            return False
        if self.category_exclude_path.get() and not os.path.isfile(self.category_exclude_path.get()):
            messagebox.showwarning("Invalid path", "Category exclusion JSON path does not exist.")
            return False
        if self.target_exclude_path.get() and not os.path.isfile(self.target_exclude_path.get()):
            messagebox.showwarning("Invalid path", "Target exclusion JSON path does not exist.")
            return False
        # Ensure output dir exists if provided
        if self.audio_output_dir.get():
            Path(self.audio_output_dir.get()).mkdir(parents=True, exist_ok=True)
        Path(os.path.dirname(self.json_output_path.get()) or ".").mkdir(parents=True, exist_ok=True)
        return True

    # Core processing thread
    def _run_parser_thread(self) -> None:
        try:
            self._log("Starting scan...")
            data: dict[str, dict] = {}

            # Load category alias map and exclusion set
            alias_map: dict[str, str] = {}
            exclude_set: set[str] = set()
            target_exclude_exact: set[str] = set()
            target_exclude_prefixes: list[str] = []
            try:
                if self.category_alias_path.get():
                    alias_text = self._read_text_best_effort(Path(self.category_alias_path.get()))
                    loaded = json.loads(alias_text)
                    if isinstance(loaded, dict):
                        # Lowercase keys for case-insensitive match; values kept as provided
                        alias_map = {str(k).lower(): str(v) for k, v in loaded.items()}
                        self._log(f"Loaded category alias map with {len(alias_map)} entries")
                    else:
                        self._log("Category alias file is not an object; ignoring.")
            except Exception as e:
                self._log(f"Error loading category alias: {e}")
            try:
                if self.category_exclude_path.get():
                    exclude_text = self._read_text_best_effort(Path(self.category_exclude_path.get()))
                    loaded_ex = json.loads(exclude_text)
                    if isinstance(loaded_ex, list):
                        exclude_set = {str(x).lower() for x in loaded_ex}
                        self._log(f"Loaded category exclusion list with {len(exclude_set)} entries")
                    else:
                        self._log("Category exclusion file is not a list; ignoring.")
            except Exception as e:
                self._log(f"Error loading category exclusion: {e}")
            try:
                if self.target_exclude_path.get():
                    target_text = self._read_text_best_effort(Path(self.target_exclude_path.get()))
                    loaded_t = json.loads(target_text)
                    if isinstance(loaded_t, list):
                        for raw in loaded_t:
                            s = str(raw).strip().lower()
                            if not s:
                                continue
                            if s.endswith('*'):
                                target_exclude_prefixes.append(s[:-1])
                            else:
                                target_exclude_exact.add(s)
                        self._log(f"Loaded target exclusion list with {len(target_exclude_exact)} exact and {len(target_exclude_prefixes)} wildcard prefixes")
                    else:
                        self._log("Target exclusion file is not a list; ignoring.")
            except Exception as e:
                self._log(f"Error loading target exclusion: {e}")

            ogg_files = self._collect_ogg_files(self.source_root.get())
            total = len(ogg_files)
            if total == 0:
                self._log("No .ogg files found.")
                return
            self._set_progress(0)

            used_mp3_names: set[str] = set()
            skip_conversion = self.json_only.get() or not self.audio_output_dir.get()
            if skip_conversion:
                self._log("JSON-only export enabled: skipping audio conversion and copy.")

            for idx, ogg_path in enumerate(ogg_files, start=1):
                try:
                    rel_parts = self._relative_parts(ogg_path, self.source_root.get())
                    # Expected: [speaker, Default, topic, (optional subtopics...), filename]
                    if len(rel_parts) < 4:
                        # Too shallow
                        continue

                    speaker = rel_parts[0].lower()
                    skin_folder = rel_parts[1]
                    second = skin_folder.lower()
                    is_default = (second == "default")

                    # Folder containing file
                    folder_parts = rel_parts[:-1]
                    # Topic is 3rd-level (index 2)
                    orig_topic = folder_parts[2]
                    orig_subtopics = folder_parts[3:]  # any deeper folders
                    topic_lower = orig_topic.lower()
                    subtopics_lower = [p.lower() for p in orig_subtopics]

                    # Apply category exclusion: skip if topic or any subtopic is excluded
                    if topic_lower in exclude_set or any(s in exclude_set for s in subtopics_lower):
                        self._log(f"Skipping due to excluded category at {speaker}/{orig_topic}")
                        continue

                    # Apply alias mapping (case-insensitive) to topic and subtopics
                    topic_key = alias_map.get(topic_lower, topic_lower)
                    subtopics = [alias_map.get(s, s) for s in subtopics_lower]

                    ogg_file = Path(ogg_path)
                    stem = ogg_file.stem

                    # Transcript file must exist
                    transcript_path = ogg_file.with_suffix('.txt')
                    if not transcript_path.exists():
                        self._log(f"Skipping (no transcript): {ogg_file}")
                        continue

                    raw_transcription = self._read_text_best_effort(transcript_path)
                    transcription = self._clean_transcription(raw_transcription)

                    # Criteria parsing (optional) — match "<base_id>-criteria.txt" where base_id is before first '-'
                    criteria_path = None
                    # Example: 000000068508.0B2-Mind yourself....ogg
                    # Criteria is 000000068508.0B2-criteria.txt
                    # Use the portion before the first '-' which includes the hash and infix
                    base_id = stem.split('-', 1)[0]
                    candidate = ogg_file.parent / f"{base_id}-criteria.txt"
                    if candidate.exists():
                        criteria_path = candidate
                    else:
                        # Fallback: search any file that starts with base_id and ends with -criteria.txt
                        for name in os.listdir(ogg_file.parent):
                            if name.lower().endswith('-criteria.txt') and name.startswith(base_id):
                                criteria_path = ogg_file.parent / name
                                break

                    if criteria_path is not None:
                        self._log(f"Using criteria: {criteria_path.name}")
                        hero_targets, map_name = self._parse_criteria(criteria_path)
                        if hero_targets:
                            self._log(f"  Targets parsed: {', '.join(hero_targets)}")
                        if map_name:
                            self._log(f"  Map parsed: {map_name}")
                    else:
                        hero_targets, map_name = ([], None)

                    # Targets: preserve order, de-dup, apply exclusion (case-insensitive, supports '*' suffix prefix match)
                    seen = set()
                    ordered_targets = []
                    for t in hero_targets:
                        tl = t.strip()
                        if not tl:
                            continue
                        key = tl.lower()
                        if key in seen:
                            continue
                        # Apply target exclusion
                        if self._is_target_excluded(key, target_exclude_exact, target_exclude_prefixes):
                            self._log(f"  Excluding target '{tl}' via target exclusion list")
                            continue
                        seen.add(key)
                        ordered_targets.append(tl)

                    subject = ordered_targets[0].lower() if ordered_targets else "self"

                    # Date from file mtime
                    dt = datetime.datetime.fromtimestamp(os.path.getmtime(ogg_path)).strftime("%Y-%m-%d")

                    # Determine filename for entry and optionally convert/copy
                    if skip_conversion:
                        file_name = ogg_file.name
                    else:
                        mp3_name = self._unique_mp3_name(self._sanitize_filename(stem) + ".mp3", used_mp3_names)
                        mp3_out_path = Path(self.audio_output_dir.get()) / mp3_name
                        self._convert_ogg_to_mp3(ogg_path, str(mp3_out_path))
                        used_mp3_names.add(mp3_name)
                        file_name = mp3_name

                    # Build entry
                    entry = {
                        "filename": file_name,
                        "date": dt,
                        "voiceline_id": self._sanitize_id(stem),
                        "transcription": transcription,
                    }
                    if ordered_targets:
                        entry["targets"] = [h.lower() for h in ordered_targets]
                    elif criteria_path is not None:
                        self._log(f"  No targets/map parsed from {criteria_path.name}")

                    # Insert into structure
                    if is_default:
                        self._insert_entry(data, speaker, subject, topic_key, subtopics, map_name, entry)
                    else:
                        # Nest non-default sets under Skins/{skinName}/{topic/...}
                        self._insert_entry(
                            data,
                            speaker,
                            subject,
                            "Skins",
                            [skin_folder, topic_key, *subtopics],
                            map_name,
                            entry,
                        )

                except Exception as e:
                    self._log(f"Error processing {ogg_path}: {e}")

                # Progress update
                self._set_progress((idx / total) * 100)

            # Save JSON
            with open(self.json_output_path.get(), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            self._log(f"Done. Processed {total} .ogg files. Output saved to {self.json_output_path.get()}")
            messagebox.showinfo("Complete", "Parsing complete.")

        except Exception as e:
            messagebox.showerror("Error", str(e))
            self._log(f"ERROR: {e}")

    # Helpers
    def _collect_ogg_files(self, source_root: str) -> list[str]:
        files: list[str] = []
        for root, _, names in os.walk(source_root):
            for name in names:
                if name.lower().endswith('.ogg'):
                    files.append(os.path.join(root, name))
        return files

    def _relative_parts(self, file_path: str, root: str) -> list[str]:
        rel = os.path.relpath(file_path, root)
        parts = rel.split(os.sep)
        return parts

    def _parse_criteria(self, criteria_path: Path) -> tuple[list[str], str | None]:
        raw_text = self._read_text_best_effort(criteria_path)
        text = self._clean_transcription(raw_text)
        heroes: list[str] = []
        map_name: str | None = None

        # Match anywhere in the file, including indented/nested sections
        for line in text.splitlines():
            line_stripped = line.rstrip()

            # Ignore negated conditions like: NOT (Hero Interaction: Cassidy)
            ls_lower = line_stripped.lstrip().lower()
            if ls_lower.startswith("not"):
                continue

            # Hero Interaction: {hero}
            m_hero = re.search(r"\bHero\s*Interaction:\s*(.+?)\s*$", line_stripped, re.IGNORECASE)
            if m_hero:
                hero = m_hero.group(1).strip()
                if hero:
                    heroes.append(hero)
                continue

            # On Map: {map}.  Ignore anything after the first '.'
            m_map = re.search(r"\bOn\s*Map:\s*([^\.]+)\.", line_stripped, re.IGNORECASE)
            if m_map and not map_name:
                map_raw = m_map.group(1).strip()
                map_name = map_raw.lower()

        return heroes, map_name

    def _insert_entry(
        self,
        data: dict,
        speaker: str,
        subject: str,
        topic: str,
        subtopics: list[str],
        map_name: str | None,
        entry: dict,
    ) -> None:
        # Ensure speaker and subject levels
        speaker_dict = data.setdefault(speaker, {})
        subject_dict = speaker_dict.setdefault(subject, {})

        # Navigate to parent of leaf
        keys = [topic, *subtopics]
        parent_ref = subject_dict
        for key in keys[:-1]:
            parent_ref = parent_ref.setdefault(key, {})
        leaf_key = keys[-1]

        existing = parent_ref.get(leaf_key)

        if map_name:
            # Use dict of map -> list
            if existing is None:
                parent_ref[leaf_key] = {map_name: [entry]}
            elif isinstance(existing, dict):
                existing.setdefault(map_name, []).append(entry)
            elif isinstance(existing, list):
                # Conflict: already plain list at this node; keep list (no _all) and log
                existing.append(entry)
                self._log(f"Warning: map-specific entry added to non-map list at {speaker}/{subject}/{'/'.join(keys)}")
        else:
            # Plain list at leaf
            if existing is None:
                parent_ref[leaf_key] = [entry]
            elif isinstance(existing, list):
                existing.append(entry)
            elif isinstance(existing, dict):
                # Conflict: maps already present; append into a general list under this node name is not allowed without a key.
                # Append into a synthetic 'general' bucket to avoid '_all'.
                existing.setdefault("general", []).append(entry)

    def _ffmpeg_available(self) -> bool:
        try:
            subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return True
        except Exception:
            return False

    def _convert_ogg_to_mp3(self, input_path: str, output_path: str) -> None:
        # ffmpeg -y -i input.ogg -codec:a libmp3lame -b:a 192k output.mp3
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            output_path,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed for {input_path}: {proc.stderr.splitlines()[-1] if proc.stderr else 'unknown error'}")

    def _sanitize_filename(self, name: str) -> str:
        # Keep alnum, dash, underscore, dot; replace others with underscore; lower-case
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
        return sanitized.lower()

    def _sanitize_id(self, name: str) -> str:
        # More permissive but normalized id
        return self._sanitize_filename(name).replace(".", "-")

    def _unique_mp3_name(self, base_name: str, used: set[str]) -> str:
        if base_name not in used and not (Path(self.audio_output_dir.get()) / base_name).exists():
            return base_name
        stem = Path(base_name).stem
        idx = 1
        while True:
            candidate = f"{stem}-{idx}.mp3"
            if candidate not in used and not (Path(self.audio_output_dir.get()) / candidate).exists():
                return candidate
            idx += 1

    def _set_progress(self, value: float) -> None:
        def update():
            self.progress['value'] = value
        self.root.after(0, update)

    def _clean_transcription(self, text: str) -> str:
        # Remove nulls and control characters except common whitespace
        # Keep \n, \r, \t; remove others in \x00-\x1F and \x7F
        cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
        return cleaned.strip()

    def _is_target_excluded(self, key: str, exact: set[str], prefixes: list[str]) -> bool:
        if key in exact:
            return True
        for p in prefixes:
            if key.startswith(p):
                return True
        return False

    def _read_text_best_effort(self, path: Path) -> str:
        # Try a few common encodings without dropping characters
        encodings = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]
        for enc in encodings:
            try:
                return path.read_text(encoding=enc)
            except Exception:
                continue
        # Fallback: binary decode with replacement, then return
        try:
            data = path.read_bytes()
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""


def main() -> None:
    root = tk.Tk()
    app = VoiceLineParserApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()


