import os
import json
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
import re

try:
    from .vdf_kv_common import (
        ORDERED_KNOWN_SUFFIXES,
        find_vdf_key_for_filename,
        load_vdf_key_text_map,
    )
except ImportError:
    from vdf_kv_common import (
        ORDERED_KNOWN_SUFFIXES,
        find_vdf_key_for_filename,
        load_vdf_key_text_map,
    )

try:
    from .voiceline_groups import (
        DEFAULT_GROUP_CONFIG_PATH,
        classify_topic,
        load_group_config,
        sort_subject_topics,
    )
except ImportError:
    from voiceline_groups import (
        DEFAULT_GROUP_CONFIG_PATH,
        classify_topic,
        load_group_config,
        sort_subject_topics,
    )

class VoiceLineOrganizer:
    """Organize parsed voice lines with data-driven display groups."""
    def __init__(self, parent):
        self.parent = parent
        
        # Check if parent is a Tk instance or a Frame
        if isinstance(parent, tk.Tk):
            # If it's a Tk root window, set title and use it directly
            self.root = parent
            self.root.title("Voice Line Organizer")
            self.root.geometry("800x600")
            main_frame = ttk.Frame(self.root, padding="10")
        else:
            # If it's a Frame, use it as the main frame
            self.root = parent.winfo_toplevel()  # Get the root window
            main_frame = ttk.Frame(parent, padding="10")
        
        # Variables to store file paths
        self.alias_json_path = tk.StringVar()
        self.topic_alias_json_path = tk.StringVar()
        self.groups_json_path = tk.StringVar()
        self.source_folder_path = tk.StringVar()
        self.output_json_path = tk.StringVar()
        self.vdf_path = tk.StringVar() # Path to VDF file for phantom lines

        # Set default values for debugging
        assets_root = Path(__file__).resolve().parents[2] / "Assets"
        self.alias_json_path.set(str(assets_root / "character_mappings.json"))
        self.topic_alias_json_path.set(str(assets_root / "topic_mappings.json"))
        self.groups_json_path.set(str(DEFAULT_GROUP_CONFIG_PATH))
        
        
        self.source_folder_path.set("C:/Users/mcall/Proton Drive/mcallbosco/My files/Projects/Deadlock/Sound Extraction/2025/May/sounds/vo")
        self.output_json_path.set("C:/Users/mcall/Proton Drive/mcallbosco/My files/Projects/Deadlock/Sound Extraction/2025/May/qwerty.json")
        
        # Options variables
        self.exclude_regular_pings = tk.BooleanVar(value=False)
        
        # Set to store disregarded hero names
        self.disregarded_heroes = set()
        
        # Create the main frame
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create file selection section
        self.create_file_selection_section(main_frame)
        
        # Create options section
        self.create_options_section(main_frame)
        
        # Create processing section
        self.create_processing_section(main_frame)
        
        # Create log section
        self.create_log_section(main_frame)
    
    def create_file_selection_section(self, parent):
        file_frame = ttk.LabelFrame(parent, text="File Selection", padding="10")
        file_frame.pack(fill=tk.X, pady=5)
        
        # Alias JSON selection
        ttk.Label(file_frame, text="Alias JSON:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.alias_json_path, width=50).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="Browse", command=self.browse_alias_json).grid(row=0, column=2, padx=5, pady=5)
        
        # Topic Alias JSON selection
        ttk.Label(file_frame, text="Topic Alias JSON:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.topic_alias_json_path, width=50).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="Browse", command=self.browse_topic_alias_json).grid(row=1, column=2, padx=5, pady=5)

        # Voiceline groups JSON selection
        ttk.Label(file_frame, text="Voiceline Groups JSON:").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.groups_json_path, width=50).grid(row=2, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="Browse", command=self.browse_groups_json).grid(row=2, column=2, padx=5, pady=5)
        
        # Source folder selection
        ttk.Label(file_frame, text="Source Folder:").grid(row=3, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.source_folder_path, width=50).grid(row=3, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="Browse", command=self.browse_source_folder).grid(row=3, column=2, padx=5, pady=5)
        
        # Output JSON selection
        ttk.Label(file_frame, text="Output JSON:").grid(row=4, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.output_json_path, width=50).grid(row=4, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="Browse", command=self.browse_output_json).grid(row=4, column=2, padx=5, pady=5)
    
    def create_options_section(self, parent):
        options_frame = ttk.LabelFrame(parent, text="Options", padding="10")
        options_frame.pack(fill=tk.X, pady=5)
        
        # Checkbox to exclude regular pings
        ttk.Checkbutton(
            options_frame, 
            text="Exclude regular pings (keep only pre_game and post_game pings)", 
            variable=self.exclude_regular_pings
        ).pack(anchor=tk.W, pady=5)
    
    def create_processing_section(self, parent):
        process_frame = ttk.Frame(parent, padding="10")
        process_frame.pack(fill=tk.X, pady=5)

        # Process button
        ttk.Button(process_frame, text="Process Voice Lines", command=self.start_processing_thread).pack(pady=10)

        # Progress bar
        self.progress = ttk.Progressbar(process_frame, orient=tk.HORIZONTAL, length=700, mode='determinate')
        self.progress.pack(pady=10, fill=tk.X)

    def start_processing_thread(self):
        thread = threading.Thread(target=self.process_voice_lines, daemon=True)
        thread.start()
    
    def create_log_section(self, parent):
        log_frame = ttk.LabelFrame(parent, text="Log", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Scrolled text widget for logs
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, width=80, height=15)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # Add scrollbar
        scrollbar = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
    
    def browse_alias_json(self):
        filename = filedialog.askopenfilename(
            title="Select Alias JSON File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.alias_json_path.set(filename)
            self.log(f"Alias JSON file selected: {filename}")
    
    def browse_topic_alias_json(self):
        filename = filedialog.askopenfilename(
            title="Select Topic Alias JSON File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.topic_alias_json_path.set(filename)
            self.log(f"Topic Alias JSON file selected: {filename}")

    def browse_groups_json(self):
        filename = filedialog.askopenfilename(
            title="Select Voiceline Groups JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.groups_json_path.set(filename)
            self.log(f"Voiceline groups JSON selected: {filename}")
    
    def browse_source_folder(self):
        folder = filedialog.askdirectory(title="Select Source Folder")
        if folder:
            self.source_folder_path.set(folder)
            self.log(f"Source folder selected: {folder}")
    
    def browse_output_json(self):
        filename = filedialog.asksaveasfilename(
            title="Save Output JSON As",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.output_json_path.set(filename)
            self.log(f"Output JSON file selected: {filename}")
    
    def log(self, message):
        # Thread-safe log update
        def append():
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
        if threading.current_thread() is threading.main_thread():
            append()
        else:
            self.root.after(0, append)
    

    
    def _validate_inputs(self):
        # Check if all required files and folders are selected
        if not self.alias_json_path.get():
            messagebox.showwarning("Missing Input", "Please select an Alias JSON file.")
            return False
        
        if not self.topic_alias_json_path.get():
            messagebox.showwarning("Missing Input", "Please select a Topic Alias JSON file.")
            return False

        if not self.groups_json_path.get():
            messagebox.showwarning("Missing Input", "Please select a Voiceline Groups JSON file.")
            return False
        
        if not self.source_folder_path.get():
            messagebox.showwarning("Missing Input", "Please select a source folder.")
            return False
        
        if not self.output_json_path.get():
            messagebox.showwarning("Missing Input", "Please select an output JSON file.")
            return False
        
        return True
    
    def _process_file(self, file_path, alias_data, topic_alias_data, valid_speakers):
        try:
            filename = os.path.basename(file_path)
            self.processing_debug_log.append(f"DEBUG: Entered _process_file for: {filename}")
            # Add debug for input args
            self.processing_debug_log.append(f"DEBUG: _process_file args: file_path={file_path}")
            filename_without_ext = os.path.splitext(filename)[0]

            # Special handling for spirit_jar
            if filename_without_ext.startswith("spirit_jar_"):
                speaker = "spirit_jar"
                # Everything after "spirit_jar_"
                base = filename_without_ext[len("spirit_jar_"):]
                # Remove trailing _alt_<number> or _<number>
                base_clean = re.sub(r'_alt_\d+$', '', base)
                base_clean = re.sub(r'_(\d+)$', '', base_clean)
                rel_path = os.path.relpath(file_path, self.source_folder_path.get())

                # Character-addressed urn lines use the form
                # spirit_jar_holder_stalls_<character>_<variation>.
                holder_stalls_prefix = "holder_stalls_"
                if base_clean.startswith(holder_stalls_prefix):
                    subject_alias = base_clean[len(holder_stalls_prefix):]
                    if subject_alias.lower() in valid_speakers:
                        subject = self._get_proper_name(subject_alias, alias_data)
                        topic_proper = "Holder stalls"
                        self.processing_debug_log.append(
                            f"Processed (spirit_jar holder stalls): {filename} -> "
                            f"{speaker}/{subject}/{topic_proper}"
                        )
                        return (speaker, subject, topic_proper, None, rel_path, False)

                # Replace underscores with spaces and capitalize first letter
                subject = base_clean.replace("_", " ").capitalize()
                topic_proper = subject
                self.processing_debug_log.append(f"Processed (spirit_jar): {filename} -> {speaker}/self/{topic_proper}")
                return (speaker, "self", topic_proper, None, rel_path, False)

            # Special handling for newscaster
            if filename_without_ext.startswith("newscaster_"):
                base = filename_without_ext[len("newscaster_"):]
                # Remove trailing _alt_<number> or _<number>
                base_clean = re.sub(r'_alt_\d+$', '', base)
                base_clean = re.sub(r'_(\d+)$', '', base_clean)
                parts = base_clean.split("_")
                rel_path = os.path.relpath(file_path, self.source_folder_path.get())
                speaker = "newscaster"
                # newscaster_headline_01 or newscaster_headline_01_alt_01
                if parts[0] == "headline":
                    return (speaker, "self", "Headline", None, rel_path, False)
                # newscaster_seasonal_headline_05 or newscaster_seasonal_headline_06_alt_01
                if parts[0] == "seasonal" and parts[1] == "headline":
                    return (speaker, "self", "Seasonal headline", None, rel_path, False)
                # newscaster_seasonal_{character}_unlock_01
                if parts[0] == "seasonal" and len(parts) >= 3 and parts[2] == "unlock":
                    subject = self._get_proper_name(parts[1], alias_data)
                    topic_proper = "Seasonal unlock"
                    return (speaker, subject, topic_proper, None, rel_path, False)

            # Special handling for shopkeeper_hotdog
            if filename_without_ext.startswith("shopkeeper_hotdog_"):
                base = filename_without_ext[len("shopkeeper_hotdog_"):]
                # Remove trailing _alt_<number> or _<number>
                base_clean = re.sub(r'_alt_\d+$', '', base)
                base_clean = re.sub(r'_(\d+)$', '', base_clean)
                parts = base_clean.split("_")
                rel_path = os.path.relpath(file_path, self.source_folder_path.get())
                speaker = "shopkeeper_hotdog"
                # seasonal t4 lines: shopkeeper_hotdog_seasonal_t4_{character}_...
                if parts[0] == "seasonal" and len(parts) >= 3 and parts[1] == "t4":
                    # Try to match multi-part character names (e.g., magician_henry)
                    subject = None
                    character_parts_count = 1
                    if len(parts) >= 4:
                        # Try two-part character name first
                        two_part = f"{parts[2]}_{parts[3]}".lower()
                        if two_part in valid_speakers:
                            subject = self._get_proper_name(two_part, alias_data)
                            character_parts_count = 2
                    if subject is None:
                        # Fall back to single-part character name
                        subject = self._get_proper_name(parts[2], alias_data)
                    topic_proper = "Seasonal"
                    return (speaker, subject, topic_proper, None, rel_path, False)
                # t4 lines: shopkeeper_hotdog_t4_{character}_...
                if parts[0] == "t4" and len(parts) >= 2:
                    # Try to match multi-part character names (e.g., magician_henry)
                    subject = None
                    character_parts_count = 1
                    if len(parts) >= 3:
                        # Try two-part character name first
                        two_part = f"{parts[1]}_{parts[2]}".lower()
                        if two_part in valid_speakers:
                            subject = self._get_proper_name(two_part, alias_data)
                            character_parts_count = 2
                    if subject is None:
                        # Fall back to single-part character name
                        subject = self._get_proper_name(parts[1], alias_data)
                    topic = "t4"
                    # The rest after character
                    remaining_start = 1 + character_parts_count
                    if len(parts) > remaining_start:
                        topic_rest = "_".join(parts[remaining_start:])
                        topic_proper = topic_rest.replace("_", " ").capitalize()
                    else:
                        topic_proper = ""
                    return (speaker, subject, f"{topic} {topic_proper}".strip(), None, rel_path, False)
                # buy lines: shopkeeper_hotdog_buy_...
                if parts[0] == "buy":
                    subject = "self"
                    if len(parts) > 1:
                        topic_proper = "Buy " + " ".join(parts[1:]).replace("_", " ")
                    else:
                        topic_proper = "Buy"
                    return (speaker, subject, topic_proper, None, rel_path, False)
                # call_out, close_shop, open_spirit, etc.
                subject = "self"
                topic_proper = " ".join(parts).replace("_", " ").capitalize()
                return (speaker, subject, topic_proper, None, rel_path, False)

            # Special handling for patron_female and patron_male
            if filename_without_ext.startswith(("patron_female_", "patron_male_")):
                speaker = "patron_female" if filename_without_ext.startswith("patron_female_") else "patron_male"
                base = filename_without_ext[len(speaker) + 1:]  # Remove "patron_female_" or "patron_male_"

                # Remove trailing variations
                base_clean = re.sub(r'_alt_\d+$', '', base)
                base_clean = re.sub(r'_(\d+)_alt$', '', base_clean)
                base_clean = re.sub(r'_alt$', '', base_clean)  # Handle _alt without number
                base_clean = re.sub(r'_(\d+)$', '', base_clean)

                parts = base_clean.split("_")
                rel_path = os.path.relpath(file_path, self.source_folder_path.get())

                # Character-based patterns: {topic}_by_{character}. Match the
                # longest suffix so multi-part aliases such as the_boss and
                # grey_talon remain one subject.
                if "by" in parts[1:]:
                    by_index = len(parts) - 1 - parts[::-1].index("by")
                    subject_alias = "_".join(parts[by_index + 1:])
                    if subject_alias.lower() in valid_speakers:
                        subject = self._get_proper_name(subject_alias, alias_data)
                        topic_proper = " ".join(parts[:by_index]).replace("_", " ").capitalize()
                        return (speaker, subject, topic_proper, None, rel_path, False)

                # help_out_{character}
                if len(parts) >= 3 and parts[0] == "help" and parts[1] == "out":
                    subject = self._get_proper_name(parts[2], alias_data)
                    topic_proper = "Help out"
                    return (speaker, subject, topic_proper, None, rel_path, False)

                # praise_{character}
                if len(parts) >= 2 and parts[0] == "praise":
                    subject_alias = "_".join(parts[1:])
                    subject = self._get_proper_name(subject_alias, alias_data)
                    topic_proper = "Praise"
                    return (speaker, subject, topic_proper, None, rel_path, False)

                # For ally/enemy patterns
                if parts[0] in ["ally", "enemy"]:
                    # Try to find a character name starting at index 1
                    # Check from longest possible name down to 1 word (to handle multi-word names like "grey talon")
                    for i in range(len(parts), 1, -1):
                        candidate_parts = parts[1:i]
                        candidate_name_spaces = " ".join(candidate_parts)
                        
                        if candidate_name_spaces in valid_speakers:
                            subject = self._get_proper_name(candidate_name_spaces, alias_data)
                            
                            # Topic is the rest
                            topic_parts = parts[i:]
                            if topic_parts:
                                topic_proper = " ".join(topic_parts).replace("_", " ").capitalize()
                            else:
                                # Just "Ally" or "Enemy" (unlikely alone but safe fallback)
                                topic_proper = parts[0].capitalize()
                            
                            return (speaker, subject, topic_proper, None, rel_path, False)

                    # If no character found, treat as self with full topic
                    # patron_female_ally_blue_guardian_destroyed_01 -> self / "Ally blue guardian destroyed"
                    # patron_female_enemy_core_exposed_01 -> self / "Enemy core exposed"
                    subject = "self"
                    topic_proper = " ".join(parts).replace("_", " ").capitalize()
                    return (speaker, subject, topic_proper, None, rel_path, False)

                # bespoke_ally_{character} or bespoke_ally_{topic}_{character}
                if len(parts) >= 3 and parts[0] == "bespoke" and parts[1] == "ally":
                    # Check if the last part is a valid character (prefer that as subject)
                    if parts[-1].lower() in valid_speakers:
                        subject = self._get_proper_name(parts[-1], alias_data)
                        if len(parts) > 3:
                            topic_proper = " ".join(parts[2:-1]).replace("_", " ").capitalize()
                        else:
                            topic_proper = "Bespoke ally"
                    else:
                        # Fallback to old behavior if last part isn't a character
                        subject = self._get_proper_name(parts[2], alias_data)
                        topic_proper = "Bespoke ally " + " ".join(parts[3:]).replace("_", " ")
                        topic_proper = topic_proper.strip().capitalize()
                    return (speaker, subject, topic_proper, None, rel_path, False)

                # bespoke_enemy_{character} or bespoke_enemy_{topic}_{character}
                if len(parts) >= 3 and parts[0] == "bespoke" and parts[1] == "enemy":
                    # Check if the last part is a valid character (prefer that as subject)
                    if parts[-1].lower() in valid_speakers:
                        subject = self._get_proper_name(parts[-1], alias_data)
                        if len(parts) > 3:
                            topic_proper = " ".join(parts[2:-1]).replace("_", " ").capitalize()
                        else:
                            topic_proper = "Bespoke enemy"
                        return (speaker, subject, topic_proper, None, rel_path, False)
                
                # bespoke_for_{character}
                if len(parts) >= 3 and parts[0] == "bespoke" and parts[1] == "for":
                    candidate = "_".join(parts[2:])
                    if candidate.lower() in valid_speakers:
                        subject = self._get_proper_name(candidate, alias_data)
                        topic_proper = "Bespoke for"
                        return (speaker, subject, topic_proper, None, rel_path, False)

                # All other patron voicelines are self voicelines
                subject = "self"
                topic_proper = " ".join(parts).replace("_", " ").capitalize()
                return (speaker, subject, topic_proper, None, rel_path, False)

            

            # List of keywords for "self" voicelines
            self_keywords = [
                "angry", "close_call", "concerned", "happy", "interrupt", "last_one_standing",
                "leave_base", "leaving_area", "parry", "near_miss", "melee_kill", "sad",
                "see_money", "select", "unselect","killstreak_high","killstreak_mid","killstreak_start",
                "leave_base", "leaving_area","low_health_warning","outnumbered","pick_up_gold", "revenge_kill",
                "pick_up_rejuv", "upgrade_power1", "upgrade_power2", "upgrade_power3",
                "upgrade_power4", "upgrade_power5", "upgrade_power6","use_power1", "use_power2", "use_power3", "use_power4",
                "solo_lasso_kill","kill_anyhero","use_power4_as_enemy", "desperation_power1",
                "desperation_power2", "desperation_power3", "desperation_power4", "desperation_power5", "desperation_power6", "hunt", "hs_select",
                "bespoke_ability_line",
                # New self single-keyword topics
                "start_match", "ap_reminder", "congrats", "be_careful", "end_streak",
                "lose", "lose_early", "lose_late", "enemy_gets_rejuv", "kill_high_networth",
                "boost_past_on_zipline", "respawn",
                # Shop system
                "t1_shop_reminder", "t2_shop_reminder", "t3_shop_reminder", "t4_shop_reminder",
                # Win conditions
                "win", "win_early", "win_late",
                # Tower/objective events
                "tower_got_denied",
                # Enemy observations
                "see_enemy_metal_skin",
                # Character-specific ability reactions
                "catch_team_blackhole", "kill_team_blackhole", "no_allies_help_blackhole", "repeat_blackhole",
                "storm_cloud_1_survives", "storm_cloud_kelvin_survives", "storm_cloud_last_standing", "storm_cloud_team_wipe",
                "high_max_health",
                "nano_kills_turrets",
                "allies_lasso_kill", "allies_no_attack",
                # Kelvin dome ability
                "bad_dome_alone", "bad_dome_rejuvinator", "dome_enemy_core", "dome_own_core",
                "heal_grenade",
                # Objective interactions
                "idol_drop",
                # Krill ability
                "power2_resurface",
                "see_enemy_use_metal_skin",
                # Lash abilities
                "massive_ground_pound",
                "upgrade_power5",
                "win_with_bebop",
                # Bebop abilities
                "hook_gig_mid_ult", "hook_lands",
                "sticky_bomb_invis",
                "uppercut_to_t1", "uppercut_to_t2", "uppercut_to_titan",
                # Warden ultimate ability
                "ult_interrupted", "ult_last_alive", "ult_total_miss",
                #shiv
                "multi_dash",
                # Effort sound variations
                "dash_effort", "melee_efforts", "efforts",
                # Familiar (Rem) asleep state voicelines
                "asleep_congrats",
                "asleep_kill_anyhereo",  # Note: original typo in filename
                "asleep_kill_anyhero",   # Corrected version
                "asleep_killstreak_high",
                "asleep_killstreak_mid",
                "asleep_killstreak_start",
                "asleep_upgrade_power1",
                "asleep_upgrade_power2",
                "asleep_upgrade_power3",
                "asleep_upgrade_power4",
                "asleep_use_power1",
                "asleep_use_power3",
                "asleep_use_power4",
                # Silver things
                "howl",
                "snarl",
                "vote",
                # Historical self topics that otherwise resemble the generic
                # speaker_topic_subject grammar.
                "emote_pain_small", "emote_pain_big", "emote_pain_death",
                "idol_grab", "idol_score", "sell_upgrade", "upgrade_power",
                "monster_idol_drop", "monster_kill_anyhero", "monster_killstreak",
                "monster_power1", "monster_power2", "monster_power3",
                "die_mid_storm_cloud", "die_trade_in_storm_cloud",
                "max_knives", "lots_of_turrets", "out_of_stamina",
                "empty_heal", "low_networth"


            ]
            
            # Parse the filename based on the specified structure
            # Pattern: speaker_ally/enemy_subject_topic_variation
            # Example: astro_ally_operative_kill_01.mp3

            # Extract speaker - try multi-part names first (e.g., "magician_henry")
            parts_initial = filename_without_ext.split("_")
            speaker = parts_initial[0] if len(parts_initial) > 1 else filename_without_ext

            # Try to match longer speaker names (e.g., "magician_henry" instead of just "magician")
            if len(parts_initial) > 1:
                # Try matching the first 2 parts as a speaker name
                if len(parts_initial) >= 2:
                    candidate = f"{parts_initial[0]}_{parts_initial[1]}"
                    if candidate.lower() in valid_speakers:
                        speaker = candidate

            # First, determine if it's an ally or enemy pattern, bespoke, or ping, or self
            is_ping = False
            is_self = False
            # Enhanced self voiceline detection for keywords with underscores
            matched_self_keyword = None
            rest = None
            # Determine how many parts were used for the speaker name
            speaker_parts_count = len(speaker.split("_"))
            if len(parts_initial) > speaker_parts_count:
                joined = "_".join(parts_initial[speaker_parts_count:])
                # Sort self_keywords by length descending to match longest first
                for kw in sorted(self_keywords, key=len, reverse=True):
                    if joined == kw:
                        matched_self_keyword = kw
                        break
                    if joined.startswith(kw + "_"):
                        suffix = joined[len(kw) + 1:]
                        # Accept sequences of digits, alt(_digits), short, or single letter (a-z), including combos like 02_a or 13_alt_01
                        if re.fullmatch(r"(?:\d+|alt(?:_\d+)?|short|[a-z])(?:_(?:\d+|alt(?:_\d+)?|short|[a-z]))*", suffix):
                            matched_self_keyword = kw
                            break
            if matched_self_keyword:
                # Handle self voiceline: [speaker]_[keyword][_variation]
                relationship = None
                rest = joined
                is_self = True
            # Subject-at-end historical patterns. Keep these explicit instead
            # of treating every unknown remainder as a Self topic.
            elif joined.startswith(("killed_by_", "assisted_by_")):
                event = "killed_by" if joined.startswith("killed_by_") else "assisted_by"
                subject_alias = joined[len(event) + 1:]
                subject_alias = re.sub(r'_alt_\d+$', '', subject_alias)
                subject_alias = re.sub(r'_(\d+)_alt$', '', subject_alias)
                subject_alias = re.sub(r'_(\d+)$', '', subject_alias)
                if subject_alias.lower() in valid_speakers:
                    relationship = None
                    rest = f"{subject_alias}_{event}"
                else:
                    relationship = None
                    rest = joined
            # Prefix-based self voiceline detection
            elif len(parts_initial) > 1 and (
                joined.startswith("use_") or joined.startswith("effort_") or joined.startswith("pain_")
            ):
                relationship = None
                rest = joined
                is_self = True
            # Special pattern: sleepy_use_power_{character} -> treat as enemy voiceline
            elif joined.startswith("sleepy_use_power_") and len(joined.split("_")) >= 4:
                # Extract character name (last part before any variations)
                sleepy_parts = joined.split("_")
                # Character name starts at index 3 (after "sleepy_use_power")
                char_and_rest = "_".join(sleepy_parts[3:])
                # Remove variations to find character
                char_clean = re.sub(r'_alt_\d+$', '', char_and_rest)
                char_clean = re.sub(r'_alt$', '', char_clean)
                char_clean = re.sub(r'_(\d+)$', '', char_clean)
                if char_clean.lower() in valid_speakers:
                    # Reformat as enemy pattern
                    relationship = "enemy"
                    rest = f"{char_clean}_sleepy_use_power"
                    # Add back variations if they existed
                    if char_and_rest != char_clean:
                        variation_part = char_and_rest[len(char_clean):]
                        rest += variation_part
                else:
                    # If not a valid character, treat as self voiceline
                    relationship = None
                    rest = joined
                    is_self = True
            # Special pattern: asleep_ping_ or sleepy_ping_ -> reformat to standard ping
            elif joined.startswith(("asleep_ping_", "sleepy_ping_")):
                # Strip the state prefix to get standard ping format
                if joined.startswith("asleep_ping_"):
                    rest = joined[len("asleep_ping_"):]
                else:  # sleepy_ping_
                    rest = joined[len("sleepy_ping_"):]
                relationship = None
                # Check for pre_game or post_game special case
                ping_parts = rest.split('_')
                if (len(ping_parts) == 3 and ping_parts[0] in ["pre", "post"] and ping_parts[1] == "game" and ping_parts[2].isdigit()) or \
                   (len(ping_parts) == 2 and ping_parts[0] in ["pre_game", "post_game"] and ping_parts[1].isdigit()):
                    # Treat as self voiceline
                    is_ping = False
                    is_self = True
                else:
                    is_ping = True
            # IMPORTANT: Check _ping_ BEFORE _ally_/_enemy_ because ping topics may contain 
            # "enemy" in their name (e.g., astro_ping_attack_enemy_avatar)
            # BUT only if the part before _ping_ is a valid speaker (to avoid matching 
            # files like astro_enemy_ghost_ping_with_swap where _ping_ is part of the topic)
            elif "_ping_" in filename_without_ext and filename_without_ext.split("_ping_", 1)[0].lower() in valid_speakers:
                # Handle ping pattern: [speaker]_ping_[topic][_subject][_variation]
                parts = filename_without_ext.split("_ping_", 1)
                relationship = None
                speaker = parts[0]
                rest = parts[1]
                # Check for pre_game or post_game special case
                ping_parts = rest.split('_')
                if (len(ping_parts) == 3 and ping_parts[0] in ["pre", "post"] and ping_parts[1] == "game" and ping_parts[2].isdigit()) or \
                   (len(ping_parts) == 2 and ping_parts[0] in ["pre_game", "post_game"] and ping_parts[1].isdigit()):
                    # Treat as self voiceline
                    is_ping = False
                    is_self = True
                    rest = rest  # already correct for self parsing
                else:
                    is_ping = True
            elif "_ally_" in filename_without_ext:
                relationship = "ally"
                parts = filename_without_ext.split("_ally_", 1)
                speaker = parts[0]
                rest = parts[1]
            elif "_enemy_" in filename_without_ext:
                relationship = "enemy"
                parts = filename_without_ext.split("_enemy_", 1)
                speaker = parts[0]
                rest = parts[1]
            elif "_bespoke_ally_" in filename_without_ext:
                relationship = "ally"
                parts = filename_without_ext.split("_bespoke_ally_", 1)
                speaker = parts[0] + "_bespoke"
                rest = parts[1]
            elif "_bespoke_" in filename_without_ext:
                relationship = None
                parts = filename_without_ext.split("_bespoke_", 1)
                speaker = parts[0] + "_bespoke"
                rest = parts[1]
            elif "_bespoke_enemy_" in filename_without_ext:
                relationship = "enemy"
                parts = filename_without_ext.split("_bespoke_enemy_", 1)
                speaker = parts[0] + "_bespoke"
                rest = parts[1]
            else:
                fallback_parts = parts_initial
                if len(fallback_parts) >= 4:                     # no longer insists on a trailing number
                    speaker = fallback_parts[0]

                    def _strip_variation(tokens):
                        """
                        Remove trailing variation tokens – digits, 'short', 'alt', 'alt##', etc.
                        and return the cleaned token list.
                        """
                        while tokens and (
                            re.fullmatch(r"\d+", tokens[-1]) or
                            tokens[-1] == "short" or
                            tokens[-1].startswith("alt")
                        ):
                            tokens = tokens[:-1]
                        return tokens

                    # Case 1: filename contains explicit '_on_' separator
                    if "on" in fallback_parts[1:]:
                        on_idx = fallback_parts.index("on", 1)
                        topic_raw = "_".join(fallback_parts[1:on_idx]) if on_idx > 1 else fallback_parts[1]

                        subject_tokens = _strip_variation(fallback_parts[on_idx + 1:])
                        subject = "_".join(subject_tokens) if subject_tokens else "self"

                        # Prefer the longest candidate that matches a known hero alias
                        for i in range(len(subject_tokens), 0, -1):
                            candidate = "_".join(subject_tokens[:i])
                            if candidate.lower() in valid_speakers:
                                subject = candidate
                                break

                        self.processing_debug_log.append(
                            f"DEBUG: Fallback (_on_) matched for {filename}: "
                            f"speaker={speaker}, topic_raw={topic_raw}, subject={subject}, "
                            f"fallback_parts={fallback_parts}"
                        )

                    # Case 2: classic speaker_topic_subject pattern
                    else:
                        topic_raw = fallback_parts[1]
                        subject_tokens = _strip_variation(fallback_parts[2:])
                        subject = "_".join(subject_tokens) if subject_tokens else fallback_parts[2]

                        for i in range(len(subject_tokens), 0, -1):
                            candidate = "_".join(subject_tokens[:i])
                            if candidate.lower() in valid_speakers:
                                subject = candidate
                                break

                        self.processing_debug_log.append(
                            f"DEBUG: Fallback matched for {filename}: "
                            f"speaker={speaker}, topic_raw={topic_raw}, subject={subject}, "
                            f"fallback_parts={fallback_parts}"
                        )

                    relationship  = None
                    is_ping       = False
                    is_self       = False
                    rest          = "_".join(fallback_parts[1:])   # keeps any trailing _short / _01 etc.
                    fallback_used = True
                    # continue parsing below
                else:
                    self.processing_debug_log.append(f"Skipped (no pattern match): {filename}")
                    return None

            # Check if speaker is valid
            if speaker.lower() not in valid_speakers:
                self.processing_debug_log.append(f"DEBUG: Disregarded speaker: {speaker} in {filename}")
                self.disregarded_heroes.add(speaker.capitalize())
                return "disregarded"

            # Now parse the rest of the filename
            # Enhanced: handle _alt_<number> and _<number>_alt at the end
            alt_match = re.search(r'_alt_(\d+)$', rest)
            num_alt_match = re.search(r'_(\d+)_alt$', rest)
            if alt_match:
                variation = alt_match.group(1)
                rest_without_variation = rest[:alt_match.start()]
            elif num_alt_match:
                variation = num_alt_match.group(1)
                rest_without_variation = rest[:num_alt_match.start()]
            else:
                # Check for double trailing numbers (e.g., _03_02)
                double_num_match = re.search(r'_(\d+)_(\d+)$', rest)
                if double_num_match:
                    variation = double_num_match.group(2)
                    # Remove both trailing numbers from the topic
                    rest_without_variation = rest[:double_num_match.start()]
                else:
                    # Find the last underscore followed by numbers (variation)
                    match = re.search(r'_(\d+)$', rest)
                    if not match:
                        # If no variation number, treat as single variation "01"
                        variation = "01"
                        rest_without_variation = rest
                    else:
                        variation = match.group(1)
                        rest_without_variation = rest[:match.start()]

            # For bespoke lines, the pattern is topic_subject
            if "_bespoke" in speaker:
                bespoke_parts = rest_without_variation.split('_')
                if len(bespoke_parts) >= 2:
                    topic_raw = "_".join(bespoke_parts[:-1])
                    subject = bespoke_parts[-1]
                else:
                    self.processing_debug_log.append(f"Could not parse bespoke subject/topic in: {filename}")
                    return None
            elif is_ping:
                # Ping: [topic][_subject]
                ping_parts = rest_without_variation.split('_')
                self.processing_debug_log.append(f"DEBUG: ping_parts for '{filename}': {ping_parts}")
                # Special handling for "see" pings: see_<hero>_<rest>.
                # Resolve a longest multi-part hero such as grey_talon.
                found_subject = False
                if len(ping_parts) >= 2 and ping_parts[0] == "see":
                    for subject_end in range(len(ping_parts), 1, -1):
                        candidate = "_".join(ping_parts[1:subject_end])
                        candidate_clean = re.sub(r'_old$', '', candidate, flags=re.IGNORECASE)
                        if candidate_clean.lower() in valid_speakers:
                            subject = candidate_clean
                            suffix = "_".join(ping_parts[subject_end:])
                            topic_raw = "see" + (f"_{suffix}" if suffix else "")
                            self.processing_debug_log.append(
                                f"DEBUG: Special SEE pattern: subject='{subject}', "
                                f"topic_raw='{topic_raw}' for '{filename}'"
                            )
                            found_subject = True
                            break
                if not found_subject:
                    # Check all possible leading substrings for a valid hero alias
                    for i in range(len(ping_parts), 0, -1):
                        candidate = "_".join(ping_parts[:i])
                        # Strip alt/number suffixes for subject matching
                        candidate_clean = re.sub(r'_alt(_\d+)?$', '', candidate)
                        candidate_clean = re.sub(r'_alt\d+$', '', candidate_clean)
                        candidate_clean = re.sub(r'_(\d+)_alt$', '', candidate_clean)
                        candidate_clean = re.sub(r'_(\d+)$', '', candidate_clean)
                        candidate_clean = re.sub(r'_old$', '', candidate_clean, flags=re.IGNORECASE)
                        self.processing_debug_log.append(f"DEBUG: Checking candidate '{candidate}' (cleaned: '{candidate_clean}') against valid_speakers for '{filename}' (leading)")
                        if candidate_clean.lower() in valid_speakers:
                            self.processing_debug_log.append(f"DEBUG: MATCHED candidate '{candidate}' as subject for '{filename}' (leading)")
                            subject = candidate_clean
                            topic_raw = "_".join(ping_parts[i:]) if i < len(ping_parts) else ""
                            found_subject = True
                            break
                        else:
                            self.processing_debug_log.append(f"DEBUG: '{candidate}' is NOT a valid subject for '{filename}' (leading)")
                    # If no leading match, check trailing substrings
                    if not found_subject:
                        for i in range(1, len(ping_parts)):
                            candidate = "_".join(ping_parts[i:])
                            candidate_clean = re.sub(r'_alt(_\d+)?$', '', candidate)
                            candidate_clean = re.sub(r'_alt\d+$', '', candidate_clean)
                            candidate_clean = re.sub(r'_(\d+)_alt$', '', candidate_clean)
                            candidate_clean = re.sub(r'_(\d+)$', '', candidate_clean)
                            candidate_clean = re.sub(r'_old$', '', candidate_clean, flags=re.IGNORECASE)
                            self.processing_debug_log.append(f"DEBUG: Checking candidate '{candidate}' (cleaned: '{candidate_clean}') against valid_speakers for '{filename}' (trailing)")
                            if candidate_clean.lower() in valid_speakers:
                                self.processing_debug_log.append(f"DEBUG: MATCHED candidate '{candidate}' as subject for '{filename}' (trailing)")
                                subject = candidate_clean
                                topic_raw = "_".join(ping_parts[:i])
                                found_subject = True
                                break
                            else:
                                self.processing_debug_log.append(f"DEBUG: '{candidate}' is NOT a valid subject for '{filename}' (trailing)")
                if not found_subject:
                    self.processing_debug_log.append(f"DEBUG: No valid subject found, defaulting to 'self' for '{filename}'")
                    subject = "self"
                    topic_raw = "_".join(ping_parts)
                # Strip _alt and trailing numbers from subject and topic for pings
                subject = re.sub(r'_alt(_\d+)?$', '', subject)
                subject = re.sub(r'_alt\d+$', '', subject)  # Handles _alt01, _alt1, etc.
                subject = re.sub(r'_(\d+)$', '', subject)
                topic_raw = re.sub(r'_alt(_\d+)?$', '', topic_raw)
                topic_raw = re.sub(r'_alt\d+$', '', topic_raw)  # Handles _alt01, _alt1, etc.
                topic_raw = re.sub(r'_(\d+)$', '', topic_raw)
            elif is_self:
                # Self voiceline: [speaker]_[keyword][_variation] or pre/post game ping
                # Remove all trailing _alt_<number>, _<number>_alt, and _<number> patterns to get the base topic
                topic_candidate = rest
                while True:
                    # Remove _alt_<number> at the end
                    alt_match = re.search(r'_alt_\d+$', topic_candidate)
                    if alt_match:
                        topic_candidate = topic_candidate[:alt_match.start()]
                        continue
                    # Remove _<number>_alt at the end
                    num_alt_match = re.search(r'_(\d+)_alt$', topic_candidate)
                    if num_alt_match:
                        topic_candidate = topic_candidate[:num_alt_match.start()]
                        continue
                    # Remove _<number> at the end
                    num_match = re.search(r'_(\d+)$', topic_candidate)
                    if num_match:
                        topic_candidate = topic_candidate[:num_match.start()]
                        continue
                    break
                topic_raw = topic_candidate
                subject = "self"
            else:
                if not locals().get("fallback_used", False):
                    # Resolve the longest known leading subject. This keeps
                    # multi-part aliases such as grey_talon and the_boss intact.
                    subject_tokens = rest_without_variation.split('_')
                    subject = None
                    topic_candidate = None
                    for index in range(len(subject_tokens) - 1, 0, -1):
                        candidate = "_".join(subject_tokens[:index])
                        if candidate.lower() in valid_speakers:
                            subject = candidate
                            topic_candidate = "_".join(subject_tokens[index:])
                            break
                    if subject is None or not topic_candidate:
                        self.processing_debug_log.append(f"Could not parse subject in: {filename}")
                        return None
                    # Strip _alt, _altXX, _XX_alt, _XX from topic for enemy/ally/fallback
                    while True:
                        alt_match = re.search(r'_alt_\d+$', topic_candidate)
                        if alt_match:
                            topic_candidate = topic_candidate[:alt_match.start()]
                            continue
                        num_alt_match = re.search(r'_(\d+)_alt$', topic_candidate)
                        if num_alt_match:
                            topic_candidate = topic_candidate[:num_alt_match.start()]
                            continue
                        alt_num_match = re.search(r'_alt\d+$', topic_candidate)
                        if alt_num_match:
                            topic_candidate = topic_candidate[:alt_num_match.start()]
                            continue
                        num_match = re.search(r'_(\d+)$', topic_candidate)
                        if num_match:
                            topic_candidate = topic_candidate[:num_match.start()]
                            continue
                        break
                    topic_raw = topic_candidate
            
            # Check if subject is a valid hero name, except for "self"
            if subject != "self" and subject.lower() not in valid_speakers:
                self.processing_debug_log.append(f"DEBUG: Disregarded subject: {subject} in {filename}")
                self.disregarded_heroes.add(subject.capitalize())
                return "disregarded"
            
            # Get proper names using alias data
            speaker_proper = self._get_proper_name(speaker, alias_data)
            subject_proper = self._get_proper_name(subject, alias_data)
            
            # Process topic and append relationship
            topic_proper = self._format_topic(topic_raw, topic_alias_data)
            # Replace underscores with spaces and capitalize first character
            topic_proper = topic_proper.replace("_", " ").capitalize()
            if relationship in ("ally", "enemy"):
                topic_proper = f"{topic_proper} ({relationship})"
            
            # Get relative path from source folder
            rel_path = os.path.relpath(file_path, self.source_folder_path.get())
            
            self.processing_debug_log.append(f"Processed: {filename} -> {speaker_proper}/{subject_proper} ({relationship})/{topic_proper}")
            self.processing_debug_log.append(f"DEBUG: Exiting _process_file for: {filename}")
            return (speaker_proper, subject_proper, topic_proper, relationship, rel_path, is_ping)
            
        except Exception as e:
            self.processing_debug_log.append(f"DEBUG: Exception in _process_file for {file_path}: {str(e)}")
            self.processing_debug_log.append(f"Error processing {file_path}: {str(e)}")
        return None
    
    def _get_proper_name(self, alias, alias_data):
        # Get the proper name for an alias
        for proper_name, aliases in alias_data.items():
            if isinstance(aliases, list) and alias.lower() in [a.lower() for a in aliases]:
                return proper_name
        return alias.capitalize()
    
    def _format_topic(self, topic_raw, topic_alias_data):
        # Check if it's a ping
        if topic_raw.startswith("ping"):
            return f"ping_{topic_raw.replace('ping', '')}"
        
        # Check if there's an alias for this topic
        for proper_topic, aliases in topic_alias_data.items():
            if isinstance(aliases, list) and topic_raw.lower() in [a.lower() for a in aliases]:
                return proper_topic
        
        # If no alias found, capitalize and return
        return topic_raw.capitalize()

    def _load_vdf(self, vdf_path):
        if not vdf_path or not os.path.exists(vdf_path):
            return {}
        try:
            return load_vdf_key_text_map(vdf_path)
        except Exception as e:
            self.log(f"Error loading VDF: {e}")
        return {}

    def _find_vdf_match(self, filename, vdf_data):
        if not vdf_data:
            return None
        return find_vdf_key_for_filename(filename, vdf_data)

    @staticmethod
    def _item_filename(item):
        if isinstance(item, dict):
            return str(item.get("filename") or "")
        return os.path.basename(str(item))

    @staticmethod
    def _append_grouped(container, path, topic, item):
        target = container
        for label in path:
            target = target.setdefault(label, {})
        target.setdefault(topic, []).append(item)

    def _place_topic(self, container, scope, topic_key, topic, item):
        path = classify_topic(
            self.group_config,
            scope,
            topic_key,
            self._item_filename(item),
        )
        if path:
            self._append_grouped(container, path, topic, item)
        else:
            container.setdefault(topic, []).append(item)

    def _place_in_result(self, result_data, result, item):
        speaker, subject, topic, relationship, rel_path, is_ping = result

        if speaker not in result_data:
            result_data[speaker] = {}

        subject_key = subject.capitalize() if subject.lower() == "self" else subject
        if subject_key not in result_data[speaker]:
            result_data[speaker][subject_key] = {}

        if is_ping:
            ping_root = self.group_config["pingRoot"]
            topic_key = topic.replace(" ", "_").lower()
            pings = result_data[speaker][subject_key].setdefault(ping_root, {})
            self._place_topic(pings, "ping", topic_key, topic, item)

            # Duplicate a self-addressed ping under Self for compatibility.
            if subject_key.lower() != "self" and subject_key.lower() == speaker.lower():
                self_topics = result_data[speaker].setdefault("Self", {})
                self_pings = self_topics.setdefault(ping_root, {})
                self._place_topic(self_pings, "ping", topic_key, topic, item)
            return

        # Relationship suffixes are part of the classification key. Ally and
        # enemy reactions can intentionally route to separate display groups.
        topic_key = topic.replace(" ", "_").lower()
        self._place_topic(
            result_data[speaker][subject_key],
            "voice",
            topic_key,
            topic,
            item,
        )

    def process_voice_lines(self):
        try:
            # Entry debug
            self.processing_debug_log = []
            self.processing_debug_log.append("DEBUG: Entered process_voice_lines")
            # Validate inputs
            if not self._validate_inputs():
                self.processing_debug_log.append("DEBUG: Input validation failed, exiting process_voice_lines")
                return

            self.sort_debug_log = []
            self.progress['value'] = 0
            
            self.processing_debug_log.append(f"DEBUG: Loading alias data from {self.alias_json_path.get()}")
            with open(self.alias_json_path.get(), 'r') as f:
                alias_data = json.load(f)
            self.processing_debug_log.append("DEBUG: Loaded alias data successfully")
            
            self.processing_debug_log.append(f"DEBUG: Loading topic alias data from {self.topic_alias_json_path.get()}")
            with open(self.topic_alias_json_path.get(), 'r') as f:
                topic_alias_data = json.load(f)
            self.processing_debug_log.append("DEBUG: Loaded topic alias data successfully")

            self.processing_debug_log.append(
                f"DEBUG: Loading voiceline groups from {self.groups_json_path.get()}"
            )
            self.group_config = load_group_config(self.groups_json_path.get())
            self.processing_debug_log.append("DEBUG: Loaded voiceline groups successfully")
            
            valid_speakers = set()
            for name, aliases in alias_data.items():
                if isinstance(aliases, list):
                    valid_speakers.update([a.lower() for a in aliases])
            
            # Load VDF if available (for phantom lines)
            vdf_path = self.vdf_path.get() if hasattr(self, 'vdf_path') else None
            vdf_data = self._load_vdf(vdf_path)
            used_vdf_keys = set()

            self.disregarded_heroes = set()
            
            self.processing_debug_log.append(f"DEBUG: Scanning for mp3 files in {self.source_folder_path.get()}")
            mp3_files = []
            for root, _, files in os.walk(self.source_folder_path.get()):
                for file in files:
                    if file.lower().endswith('.mp3'):
                        mp3_files.append(os.path.join(root, file))
            self.processing_debug_log.append(f"DEBUG: Found {len(mp3_files)} mp3 files")
            
            result_data = {}
            
            total_files = len(mp3_files)
            processed = 0
            disregarded = 0
            
            # 1. Process Real Files
            for file_path in mp3_files:
                self.processing_debug_log.append(f"DEBUG: About to process file: {os.path.basename(file_path)}")
                result = self._process_file(file_path, alias_data, topic_alias_data, valid_speakers)
                
                processed += 1
                def update_progress():
                    self.progress['value'] = (processed / total_files) * 100
                self.root.after(0, update_progress)
                self.root.after(0, self.parent.update_idletasks)
                
                if result is None: continue
                if result == "disregarded":
                    disregarded += 1
                    continue
                
                vdf_key = self._find_vdf_match(os.path.basename(file_path), vdf_data)
                if vdf_key: used_vdf_keys.add(vdf_key)

                speaker, subject, topic, relationship, rel_path, is_ping = result
                self._place_in_result(result_data, result, rel_path)

            # 2. Process Phantom VDF Files
            if vdf_data:
                unused = set(vdf_data.keys()) - used_vdf_keys
                for key in unused:
                    # Filter by suffix and strip it for categorization
                    matching_suffix = None
                    for suffix in ORDERED_KNOWN_SUFFIXES:
                        if key.lower().endswith(suffix):
                            matching_suffix = suffix
                            break
                    
                    if not matching_suffix:
                        continue
                    
                    # Strip suffix to simulate the audio filename format
                    clean_key = key[:-len(matching_suffix)]
                    fake_path = clean_key + ".mp3"
                    result = self._process_file(fake_path, alias_data, topic_alias_data, valid_speakers)
                    
                    if result and result != "disregarded":
                        speaker, subject, topic, relationship, _, is_ping = result
                        # Create phantom entry
                        item = {
                            "filename": "", # Key as filename placeholder -> changed to empty as requested
                            "is_phantom": True,
                            "transcription": vdf_data[key],
                            "officialtranscription": True,
                            "voiceline_id": key
                        }
                        self._place_in_result(result_data, result, item)
            
            for speaker in result_data:
                if "Self" in result_data[speaker]:
                    result_data[speaker]["Self"] = sort_subject_topics(
                        self.group_config,
                        result_data[speaker]["Self"],
                    )

            # Save
            self.processing_debug_log.append(f"DEBUG: Saving result data to {self.output_json_path.get()}")
            with open(self.output_json_path.get(), 'w') as f:
                json.dump(result_data, f, indent=2)
            self.processing_debug_log.append("DEBUG: Saved result data successfully")

            if self.processing_debug_log:
                def insert_processing_log():
                    self.log_text.insert(tk.END, "\n--- Processing Debug Output ---\n")
                    self.log_text.insert(tk.END, "\n".join(self.processing_debug_log) + "\n")
                    self.log_text.insert(tk.END, "--- End Processing Debug Output ---\n\n")
                    self.log_text.see(tk.END)
                self.root.after(0, insert_processing_log)

            self.log(f"\nProcessing complete!")
            self.log(f"Processed {processed} files")
            self.log(f"Output saved to: {self.output_json_path.get()}")

            def show_info():
                messagebox.showinfo("Processing Complete", f"Successfully processed {processed} files.\nOutput saved to: {self.output_json_path.get()}")
            self.root.after(0, show_info)
            
        except Exception as e:
            self.processing_debug_log.append(f"DEBUG: Exception in process_voice_lines: {str(e)}")
            def show_error():
                messagebox.showerror("Error", f"An error occurred: {str(e)}")
            self.root.after(0, show_error)
            self.log(f"ERROR: {str(e)}")

def main():
    root = tk.Tk()
    app = VoiceLineOrganizer(root)
    root.mainloop()

if __name__ == "__main__":
    main()
