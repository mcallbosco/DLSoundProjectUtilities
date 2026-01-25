import os
import json
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
import re

# Common VDF key suffixes to check during matching
KNOWN_SUFFIXES = [
    "_announcer",
    "_hero_3d",
    "_ability_3d",
    "_ult_3d",
    "_hero_announcer",
    "_hero_zipline_3d",
    "_ping_2d",
    "_idol",
    "_shopkeeper"
]

class VoiceLineOrganizer:
    # Define multiple special categories as a dict: {category_name: [keywords]}
    special_categories = {
        "Hero Selection": ["select","hs_select", "unselect"],
        "Killstreaks": ["killstreak_high","killstreak_mid","killstreak_start", "killing_streak_high", "killing_streak_low", "killing_streak_medium","killing_streak", "killing_streak_generic_high", "killing_streak_generic_low", "killing_streak_generic_medium", "killstreak_count", "killstreak_title"],
        "Movement": ["leave_base", "leaving_area", "boost_past_on_zipline"],
        # TEMPORARY: include bespoke_ability_line under Use Power until structure stabilizes
        "Use Power": ["use_power1", "use_power2", "use_power3", "use_power4", "bespoke_ability_line", "use_power4_01-imported", "use_power4_as_enemy", "use_power4_end", "use_power4_seasonal", "use_power4_start", "use_power5", "power2_resurface", "power4"],
        "Desperation Use Power" : ["desperation_power1", "desperation_power2", "desperation_power3", "desperation_power4"],
        "Upgrade Power": ["upgrade_power1", "upgrade_power2", "upgrade_power3", "upgrade_power4", "upgrade_power5"],
        
        "Character-Specific Abilities": [
            "bad_dome_alone", "bad_dome_rejuvinator", "big_kelvin", "big_healing",
            "big_healing_by_forge_alt_01__duplicate_id", "big_healing_by_forge_alt_02__duplicate_id",
            "catch_team_blackhole", "dome_enemy_core", "dome_own_core",
            "heal_grenade", "hook_gig_mid_ult", "kill_team_blackhole",
            "nano_kills_turrets", "no_allies_help_blackhole", "sticky_bomb_invis",
            "ult_interrupted", "ult_last_alive", "ult_total_miss",
            "use_power2_on_ally", "use_power2_on_others", "use_power2_on_self",
            "storm_cloud_1_survives", "storm_cloud_kelvin_survives",
            "storm_cloud_last_standing", "storm_cloud_team_wipe",
            "uppercut_to_t1", "uppercut_to_t2", "uppercut_to_titan"

        ],
        "Pick Up": ["see_money","pick_up_gold", "pick_up_rejuv"],
        "Emotions": ["angry", "concerned", "happy", "sad", "congrats","praise"],
        "Combat": ["parry", "near_miss", "melee_kill", "revenge_kill", "last_one_standing", "close_call", "interrupt", "hunt", "kill_anyhero","low_health_warning","outnumbered", "solo_lasso_kill", "be_careful", "ap_reminder", "kill_high_networth", "end_streak", "kill", "catch", "dash_effort", "efforts", "melee_efforts", "multi_dash", "hook", "hook_lands", "help_out", "kill_fat_ghost_(enemy)", "kill_in_lift_(ally)", "kill_mid_laser_(enemy)", "kill_on_ice_path_(enemy)", "kill_post_swap_(enemy)", "kill_when_invisible_(enemy)", "killed_in_lane_(ally)", "killed_in_lane_01-imported_(ally)", "killed_mid_air_(enemy)", "killed_mid_ult_(enemy)", "mid_air_kill_(enemy)"],
        
        "Ally Actions": [
            "allies_lasso_kill", "allies_no_attack", "ally_urn_runner", "big_blackhole_(ally)",
            "big_stun_(ally)", "big_ult_(ally)", "bounce_pad_(ally)", "burns_down_objective_(ally)",
            "clutch_cube_(ally)", "clutch_heal_(ally)", "far_hook_(ally)", "far_shot_(ally)",
            "far_snipe_(ally)", "grab_mid_enemy_ult_(ally)", "kill_in_lift_(ally)",
            "killed_in_lane_(ally)", "killed_in_lane_01-imported_(ally)", "kills_with_hook_(ally)",
            "lasso_victim_(ally)", "missile_stops_ult_(ally)", "multi_kill_ult_(ally)",
            "multikill_(ally)", "see_big_swap_(ally)", "see_massive_stomp_(ally)",
            "steals_rejuv_(ally)", "swap_to_boss_(ally)", "take_objective_with_teather_(ally)",
            "unkillable_(ally)", "uppercut_towards_boss_(ally)", "wall_stun_kill_(ally)",
            
        ],
        
        # New special category for use_* non-ping topics (items, shards, etc.)
        "Item Purchase": [
            "t4", "t4_01", "t4_buys_carpet", "t4_slork"
        ],
        # Shop system reminders
        "Shop System": ["t1_shop_reminder", "t2_shop_reminder", "t3_shop_reminder", "t4_shop_reminder","choose_item", "close_shop","open_gun", "open_spirit", "call_out"],
         
        
        
        "Match Status": [
            "win", "win_early", "win_late", "match_win", "win_with_bebop", "match_start", "introduction", 
            "lose", "lose_early", "lose_late","ally_comeback", "enemy_comeback", "ally_team_wipe", "enemy_team_wipe", 
            "ally_troopers_are_stronger", "enemy_troopers_are_stronger", 
            "networth_update_ahead", "networth_update_behind", "avatar_is_destroyed",
            "lose_objective_while_ahead", "post_game", "update_up_on_money_down_on_obj",
            "high_max_health", "enemy_gets_rejuv"
        ],

        "Global Objectives": [
            "tower_got_denied", "idol_drop",
            "ally_delivered_many_urns", "ally_urn_movement", "ally_urn_delivered", 
            "enemy_delivered_many_urns", "enemy_urn_delivered", "enemy_urn_movement", 
            "urn_delivered", "urn_dropped", "urn_moving", "urn_waiting", 
            "idol_delivered", "idol_dropped", "idol_landed", "idol_moving", "idol_waiting", 
            "rejuv_spawn", "rejuvinator_expired", "ally_rejuvinator_almost_expired", 
            "ally_steal_rejuv", "enemy_steal_rejuv", "mid_spawn", 
            "vanguard_ally_capture", "vanguard_ally_capture_attempt", "vanguard_available", 
            "vanguard_enemy_capture", "vanguard_enemy_capture_attempt", 
            "ally_vanguard_defeated", "enemy_vanguard_defeated", "push_objectives_for_flex_slots",
            "stolen_by_the_boss"
        ],

        "Ally Structures": [
            "ally_blue_guardian_attack", "ally_blue_guardian_destroyed", 
            "ally_blue_walker_attack", "ally_blue_walker_destroyed", 
            "ally_core_attack", "ally_core_critical", "ally_core_exposed", "ally_core_reminder", 
            "ally_first_generator_destroyed", "ally_generator_attack", 
            "ally_green_guardian_attack", "ally_green_guardian_destroyed", 
            "ally_green_walker_attack", "ally_green_walker_destroyed", 
            "ally_guardian_destroyed", 
            "ally_orange_guardian_attack", "ally_orange_guardian_destroyed", 
            "ally_orange_walker_attack", "ally_orange_walker_destroyed", 
            "ally_purple_guardian_attack", "ally_purple_guardian_destroyed", 
            "ally_purple_walker_attack", "ally_purple_walker_destroyed", 
            "ally_titan_attack", "ally_titan_destroyed", "ally_titan_exposed", "ally_titan_reminder", 
            "ally_walker_destroyed", 
            "ally_yellow_guardian_attack", "ally_yellow_guardian_destroyed", 
            "ally_yellow_walker_attack", "ally_yellow_walker_destroyed", 
            "base_breached", "base_defended", "base_guardians_under_attack"
        ],

        "Enemy Structures": [
            "enemy_blue_guardian", "enemy_blue_walker", 
            "enemy_core", "enemy_core_exposed", "enemy_core_healing", 
            "enemy_first_generator_destroyed", 
            "enemy_guardian_destroyed", 
            "enemy_orange_guardian", "enemy_orange_walker", 
            "enemy_purple_guardian", "enemy_purple_walker", 
            "enemy_titan", "enemy_titan_exposed", 
            "enemy_walker_destroyed", 
            "enemy_yellow_guardian", "enemy_yellow_walker", 
            "titan_destroyed"
        ],

        "Enemy Observations": ["see_enemy_metal_skin", "see_enemy_use_metal_skin"],
        
        "Enemy Actions": [
            "blocked_by_wall_(enemy)", "bounce_escape_(enemy)", "charges_(enemy)",
            "damaged_by_snipe_(enemy)", "destroy_turrets_(enemy)", "enemy_urn_runner",
            "flies_away_(enemy)", "grabbed_(enemy)", "hide_from_ult_(enemy)",
            "hooked_(enemy)", "lassoed_(enemy)",
            "lifts_(enemy)", "ping__with_swap_(enemy)",
            "see_missile_(enemy)", "see_ult_(enemy)", "starts_ult_(enemy)",
            "steals_rejuv_(enemy)", "unkillable_(enemy)"
        ],

        "Boons": [
            "grant_boon_armor", "grant_boon_armor_10", "grant_boon_general", 
            "grant_boon_magic", "grant_boon_weapon"
        ],

        "Map Alerts": [
            "broadway_attacked", "orchard_attacked", "park_attacked", "york_attacked"
        ],

        "Patron Specific": [
            "saphire_flame_lines"
        ],

        "Tutorial": [
            "tutorial_1_tasks_left", "tutorial_2_tasks_left", "tutorial_2nd_ap_earned", 
            "tutorial_3_tasks_left", "tutorial_4_tasks_left", "tutorial_boons", 
            "tutorial_buy_mod_reminder", "tutorial_collect_souls", 
            "tutorial_combat_1st_kill_on_atlas", "tutorial_combat_1st_kill_on_dynamo", 
            "tutorial_combat_1st_kill_on_haze", "tutorial_combat_1st_kill_on_inferno", 
            "tutorial_combat_1st_kill_on_kali", "tutorial_combat_1st_kill_on_lash", 
            "tutorial_combat_1st_kill_on_nano", "tutorial_combat_1st_kill_on_orion", 
            "tutorial_combat_companion_dies", "tutorial_combat_companion_need_help", 
            "tutorial_combat_death_info", "tutorial_combat_disarm_info", 
            "tutorial_combat_enemy_atlas_info", "tutorial_combat_enemy_dynamo_info", 
            "tutorial_combat_enemy_haze_info", "tutorial_combat_enemy_lash_info", 
            "tutorial_combat_enemy_orion_info", "tutorial_combat_immobilze_info", 
            "tutorial_combat_melee", "tutorial_combat_melee_short", 
            "tutorial_combat_neutrals_info", "tutorial_combat_out_of_range", 
            "tutorial_combat_parry", "tutorial_combat_silence_info", 
            "tutorial_combat_stun_info", "tutorial_combat_zoom_reminder", 
            "tutorial_congrats_t1_killed", "tutorial_congrats_t2_killed", 
            "tutorial_deny_enemy", "tutorial_destroy_t1", "tutorial_destroy_t2", 
            "tutorial_destroy_titan", "tutorial_dont_fight_t2_yet", "tutorial_fall_back", 
            "tutorial_farm_info", "tutorial_farm_info_alt2", "tutorial_farm_info_short", 
            "tutorial_farm_reminder", "tutorial_final_push", "tutorial_first_shop_short_edit", 
            "tutorial_go_forth_my_child", "tutorial_gold_orb_reminder", "tutorial_heal_reminder_short", 
            "tutorial_healing_info", "tutorial_idol_info", "tutorial_lane_info", 
            "tutorial_level_up", "tutorial_level_up_info", "tutorial_level_up_info_short", 
            "tutorial_lower_shields", "tutorial_mid_info", "tutorial_push_lane", 
            "tutorial_red_orb_reminder", "tutorial_safe_info", "tutorial_shop_ap_only", 
            "tutorial_shop_ap_reminder", "tutorial_shop_info", "tutorial_shop_reminder", 
            "tutorial_single_lane_report_card_bronze", "tutorial_single_lane_report_card_gold", 
            "tutorial_single_lane_report_card_intro", "tutorial_single_lane_report_card_silver", 
            "tutorial_single_lane_walker_down", "tutorial_single_lane_walker_intro", 
            "tutorial_stamina_reminder", "tutorial_starting_ability", "tutorial_t1_shield_is_up", 
            "tutorial_tasks_complete", "tutorial_total_cred_reminder", 
            "tutorial_unlock_power_reminder", "tutorial_use_ap_reminder", "tutorial_welcome", 
            "tutorial_zipline_info", "tutorial_zipline_info_short", "tutorial_zipline_reminder",
            "tutorial_attacked", "tutorial_complete_tasks_to_fight", "greenwich_attacked"
        ],
        
        "Street Brawl Mode": [
            "street_brawl_draw", "street_brawl_loss", "street_brawl_round_complete",
            "street_brawl_round_lose", "street_brawl_round_win", "street_brawl_victory"
        ]
    }
    # Define special categories for pings
    special_ping_categories = {
        "Objective Commands": [
        "attack_enemy",
        "clear_troopers",
        "defend_base",
        "defend_blue",
        "defend_green",
        "defend_purple",
        "defend_yellow",
        "help_with_idol",
        "lets_go_blue",
        "lets_go_blue_alt",
        "lets_go_green",
        "lets_go_green_alt",
        "lets_go_purple",
        "lets_go_purple_alt",
        "lets_go_yellow",
        "lets_go_yellow_alt",
        "push_blue",
        "push_green",
        "push_purple",
        "push_yellow",
        "take_mid",
        "take_shrine"
    ],
    "Ability Status/Usage": [
        "ability1_almost_ready",
        "ability1_not_ready",
        "ability2_almost_ready",
        "ability2_not_ready",
        "ability3_almost_ready",
        "ability3_not_ready",
        "ability4_almost_ready",
        "ability4_not_ready",
        "use_ability1",
        "use_ability2",
        "use_ability3",
        "use_ability4"
    ],
    "Item Status/Usage": [
        "can_heal", # Moved
        "glitch_almost_ready",
        "glitch_not_ready",
        "heal_ready", # Moved
        "health_nova_almost_ready",
        "health_nova_not_ready",
        "item_almost_ready",
        "item_not_ready",
        "kncokdown_almost_ready", # Moved (Original typo)
        "knockdown_almost_ready", # Moved (Corrected version)
        "kncokdown_not_ready",    # Moved (Original typo)
        "knockdown_not_ready",    # Moved (Corrected version)
        "refresher_almost_ready",
        "refresher_not_ready",
        "silence_almost_ready", # Moved
        "silence_not_ready", # Moved
        "stim_pack_almost_ready",
        "stim_pack_not_ready",
        "warp_stone_almost_ready",
        "warp_stone_not_ready",
        "use_glitch",
        "use_health_nova",
        "use_item",
        "use_kncokdown", # Moved (Original typo)
        "use_knockdown", # Moved (Corrected version)
        "use_refresher",
        "use_rupture", # Moved
        "use_silence", # Moved
        "use_stim_pack",
        "use_warp_stone"
    ],
    "Movement and Positioning": [
        "be_back_soon",
        "flank",
        "going_in",
        "going_shop",
        "headed_blue",
        "headed_green",
        "headed_purple",
        "headed_this_way",
        "headed_yellow",
        "leaving_area",
        "lets_hide",
        "meet_here",
        "on_way",
        "request_follow",
        "retreat",
        "returning_to_base",
        "right_back",
        "stay_together",
        "wait"
    ],
    "Enemy Information and Location": [
        "danger_area",
        "in_mid",
        "missing",
        "missing_blue",
        "missing_green",
        "missing_purple",
        "missing_yellow",
        "on_top_of_garage",
        "on_top_of_mid",
        "saw",
        "saw_them",
        "see",
        "see_enemy",
        "see_on_bridge",
        "see_on_roof",
        "theyre_in_mid",
        "theyre_on_top_of_garage",
        "theyre_on_top_of_mid",
        "theyre_under_garage",
        "they_were_here",
        "under_garage",
        "was_here"
    ],
    "Requests and Alerts": [
        "almost_respawn",
        "avatar_under_attack",
        "blue_help",
        "dead",
        "green_help",
        "need_cover",
        "need_heal",
        "need_help_blue",
        "need_help_blue_alt",
        "need_help_green",
        "need_help_green_alt",
        "need_help_purple",
        "need_help_purple_alt",
        "need_help_yellow",
        "need_help_yellow_alt",
        "purple_help",
        "t1_under_attack",
        "t2_under_attack",
        "yellow_help"
    ],
    "Tactical Communication": [
        "attack",
        "careful",
        "gank",
        "ignore",
        "need_plan",
        "no_teamfight",
        "press_advantage",
        "stun"
    ],
    "General Communication / Social": [
        "affermative",
        "good_game",
        "good_job",
        "negative",
        "nice_work",
        "sorry",
        "thanks",
        "thank_you",
        "welcome",
        "well_played",
        "with"
    ],
    "Miscellaneous Status": [
        "check_items",
        "jar_call",
        "rejuv_drop"
    ]
}
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
        self.source_folder_path = tk.StringVar()
        self.output_json_path = tk.StringVar()
        self.vdf_path = tk.StringVar() # Path to VDF file for phantom lines

        # Set default values for debugging
        self.alias_json_path.set(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Assets", "character_mappings.json")))
        self.topic_alias_json_path.set(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Assets", "topic_mappings.json")))
        
        
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
        
        # Source folder selection
        ttk.Label(file_frame, text="Source Folder:").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.source_folder_path, width=50).grid(row=2, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="Browse", command=self.browse_source_folder).grid(row=2, column=2, padx=5, pady=5)
        
        # Output JSON selection
        ttk.Label(file_frame, text="Output JSON:").grid(row=3, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.output_json_path, width=50).grid(row=3, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="Browse", command=self.browse_output_json).grid(row=3, column=2, padx=5, pady=5)
    
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
                subject_raw = filename_without_ext[len("spirit_jar_"):]
                # Remove trailing _alt_<number> or _<number>
                subject_raw = re.sub(r'_alt_\d+$', '', subject_raw)
                subject_raw = re.sub(r'_(\d+)$', '', subject_raw)
                # Replace underscores with spaces and capitalize first letter
                subject = subject_raw.replace("_", " ").capitalize()
                topic_proper = subject
                rel_path = os.path.relpath(file_path, self.source_folder_path.get())
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

                # Character-based patterns: {topic}_by_{character}
                if len(parts) >= 3 and parts[-2] == "by":
                    # patron_female_big_healing_by_astro, patron_female_stolen_by_abrams, patron_female_many_assists_by_astro
                    subject = self._get_proper_name(parts[-1], alias_data)
                    topic_proper = " ".join(parts[:-2]).replace("_", " ").capitalize()
                    return (speaker, subject, topic_proper, None, rel_path, False)

                # help_out_{character}
                if len(parts) >= 3 and parts[0] == "help" and parts[1] == "out":
                    subject = self._get_proper_name(parts[2], alias_data)
                    topic_proper = "Help out"
                    return (speaker, subject, topic_proper, None, rel_path, False)

                # praise_{character}
                if len(parts) >= 2 and parts[0] == "praise":
                    subject = self._get_proper_name(parts[1], alias_data)
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
                "upgrade_power4","use_power1", "use_power2", "use_power3", "use_power4",
                "solo_lasso_kill","kill_anyhero","use_power4_as_enemy", "desperation_power1",
                "desperation_power2", "desperation_power3", "desperation_power4", "hunt", "hs_select",
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
            # Prefix-based self voiceline detection
            elif len(parts_initial) > 1 and (
                joined.startswith("use_") or joined.startswith("effort_") or joined.startswith("pain_")
            ):
                relationship = None
                rest = joined
                is_self = True
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
                # Special handling for "see" pings: see_<hero>_<rest>
                found_subject = False
                if len(ping_parts) >= 3 and ping_parts[0] == "see" and ping_parts[1].lower() in valid_speakers:
                    subject = ping_parts[1]
                    topic_raw = "see_" + "_".join(ping_parts[2:])
                    self.processing_debug_log.append(f"DEBUG: Special SEE pattern: subject='{subject}', topic_raw='{topic_raw}' for '{filename}'")
                    found_subject = True
                else:
                    # Check all possible leading substrings for a valid hero alias
                    for i in range(len(ping_parts), 0, -1):
                        candidate = "_".join(ping_parts[:i])
                        # Strip alt/number suffixes for subject matching
                        candidate_clean = re.sub(r'_alt(_\d+)?$', '', candidate)
                        candidate_clean = re.sub(r'_alt\d+$', '', candidate_clean)
                        candidate_clean = re.sub(r'_(\d+)_alt$', '', candidate_clean)
                        candidate_clean = re.sub(r'_(\d+)$', '', candidate_clean)
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
                    # The first part before underscore is the subject
                    subject_parts = rest_without_variation.split('_', 1)
                    if len(subject_parts) < 2:
                        self.processing_debug_log.append(f"Could not parse subject in: {filename}")
                        return None
                    subject = subject_parts[0]
                    topic_candidate = subject_parts[1]
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
        vdf_data = {}
        if not vdf_path or not os.path.exists(vdf_path):
            return vdf_data
        try:
            with open(vdf_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    m = re.match(r'^"([^"]+)"\s+"([^"]+)"$', line)
                    if m:
                        key, text = m.groups()
                        vdf_data[key.lower()] = text
        except Exception as e:
            self.log(f"Error loading VDF: {e}")
        return vdf_data

    def _find_vdf_match(self, filename, vdf_data):
        if not vdf_data:
            return None
        stem = os.path.splitext(filename)[0].lower()
        if stem in vdf_data: return stem
        for suffix in KNOWN_SUFFIXES:
            candidate = f"{stem}{suffix}"
            if candidate in vdf_data: return candidate
        return None

    def _place_in_result(self, result_data, result, item):
        speaker, subject, topic, relationship, rel_path, is_ping = result
        
        # Initialize speaker if not exists
        if speaker not in result_data:
            result_data[speaker] = {}
        
        # Capitalize 'self' key for consistency
        subject_key = subject.capitalize() if subject.lower() == "self" else subject
        if subject_key not in result_data[speaker]:
            result_data[speaker][subject_key] = {}
        
        # Handle special case for pings
        if is_ping:
            # Store under "Pings" key
            if "Pings" not in result_data[speaker][subject_key]:
                result_data[speaker][subject_key]["Pings"] = {}
            # Check for special ping categories
            topic_key = topic.replace(" ", "_").lower()
            placed_in_ping_category = False
            for cat_name, keywords in VoiceLineOrganizer.special_ping_categories.items():
                if topic_key in keywords:
                    if cat_name not in result_data[speaker][subject_key]["Pings"]:
                        result_data[speaker][subject_key]["Pings"][cat_name] = {}
                    if topic not in result_data[speaker][subject_key]["Pings"][cat_name]:
                        result_data[speaker][subject_key]["Pings"][cat_name][topic] = []
                    result_data[speaker][subject_key]["Pings"][cat_name][topic].append(item)
                    placed_in_ping_category = True
                    break
            if not placed_in_ping_category:
                if topic not in result_data[speaker][subject_key]["Pings"]:
                    result_data[speaker][subject_key]["Pings"][topic] = []
                result_data[speaker][subject_key]["Pings"][topic].append(item)
            # Also store as self ping if subject is not already "Self" and subject == speaker
            if subject_key.lower() != "self" and (subject_key.lower() == speaker.lower()):
                if "Self" not in result_data[speaker]:
                    result_data[speaker]["Self"] = {}
                if "Pings" not in result_data[speaker]["Self"]:
                    result_data[speaker]["Self"]["Pings"] = {}
                # Repeat special ping category logic for self pings
                placed_in_ping_category_self = False
                for cat_name, keywords in VoiceLineOrganizer.special_ping_categories.items():
                    if topic_key in keywords:
                        if cat_name not in result_data[speaker]["Self"]["Pings"]:
                            result_data[speaker]["Self"]["Pings"][cat_name] = {}
                        if topic not in result_data[speaker]["Self"]["Pings"][cat_name]:
                            result_data[speaker]["Self"]["Pings"][cat_name][topic] = []
                        result_data[speaker]["Self"]["Pings"][cat_name][topic].append(item)
                        placed_in_ping_category_self = True
                        break
                if not placed_in_ping_category_self:
                    if topic not in result_data[speaker]["Self"]["Pings"]:
                        result_data[speaker]["Self"]["Pings"][topic] = []
                    result_data[speaker]["Self"]["Pings"][topic].append(item)
        else:
            # For all voicelines (not just self), check if topic is in any special category
            # Use the base topic (without relationship) for category matching
            if relationship in ("ally", "enemy") and topic.endswith(f"({relationship})"):
                base_topic = topic[:-(len(f" ({relationship})"))].strip()
                topic_key_for_category = base_topic.replace(" ", "_").lower()
            else:
                base_topic = topic
                topic_key_for_category = topic.replace(" ", "_").lower()
            placed_in_category = False
            # Route all non-power use_* topics under Item Usage
            if topic_key_for_category.startswith("use_") and not topic_key_for_category.startswith("use_power"):
                if "Item Usage" not in result_data[speaker][subject_key]:
                    result_data[speaker][subject_key]["Item Usage"] = {}
                if topic not in result_data[speaker][subject_key]["Item Usage"]:
                    result_data[speaker][subject_key]["Item Usage"][topic] = []
                result_data[speaker][subject_key]["Item Usage"][topic].append(item)
                placed_in_category = True

            # Route pain_* topics under Emotions -> Pain and effort_* under Emotions -> Effort
            if not placed_in_category:
                if topic_key_for_category == "pain" or topic_key_for_category.startswith("pain_"):
                    if "Emotions" not in result_data[speaker][subject_key]:
                        result_data[speaker][subject_key]["Emotions"] = {}
                    if "Pain" not in result_data[speaker][subject_key]["Emotions"]:
                        result_data[speaker][subject_key]["Emotions"]["Pain"] = {}
                    if topic not in result_data[speaker][subject_key]["Emotions"]["Pain"]:
                        result_data[speaker][subject_key]["Emotions"]["Pain"][topic] = []
                    result_data[speaker][subject_key]["Emotions"]["Pain"][topic].append(item)
                    placed_in_category = True
                elif topic_key_for_category == "effort" or topic_key_for_category.startswith("effort_"):
                    if "Emotions" not in result_data[speaker][subject_key]:
                        result_data[speaker][subject_key]["Emotions"] = {}
                    if "Effort" not in result_data[speaker][subject_key]["Emotions"]:
                        result_data[speaker][subject_key]["Emotions"]["Effort"] = {}
                    if topic not in result_data[speaker][subject_key]["Emotions"]["Effort"]:
                        result_data[speaker][subject_key]["Emotions"]["Effort"][topic] = []
                    result_data[speaker][subject_key]["Emotions"]["Effort"][topic].append(item)
                    placed_in_category = True

            for cat_name, keywords in VoiceLineOrganizer.special_categories.items():
                if topic_key_for_category in keywords:
                    if cat_name not in result_data[speaker][subject_key]:
                        result_data[speaker][subject_key][cat_name] = {}
                    if topic not in result_data[speaker][subject_key][cat_name]:
                        result_data[speaker][subject_key][cat_name][topic] = []
                    result_data[speaker][subject_key][cat_name][topic].append(item)
                    placed_in_category = True
                    break
            if not placed_in_category:
                if topic not in result_data[speaker][subject_key]:
                    result_data[speaker][subject_key][topic] = []
                result_data[speaker][subject_key][topic].append(item)

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
                    for suffix in KNOWN_SUFFIXES:
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
            
            # Custom sort
            def custom_self_sort(topics, debug_log):
                priority = ["Select", "Unselect", "Pre game", "Post game"]
                def sort_key(k):
                    try:
                        idx = priority.index(k)
                        return (idx, k)
                    except ValueError:
                        return (len(priority), k)
                special_keys = list(VoiceLineOrganizer.special_categories.keys())
                keys_to_remove = special_keys + ["Pings"]
                topics_no_special = {k: v for k, v in topics.items() if k not in keys_to_remove}
                sorted_topics = dict(sorted(topics_no_special.items(), key=lambda x: sort_key(x[0])))
                for cat in special_keys:
                    if cat in topics:
                        sorted_topics[cat] = topics[cat]
                if "Pings" in topics:
                    sorted_topics["Pings"] = topics["Pings"]
                return sorted_topics

            for speaker in result_data:
                if "Self" in result_data[speaker]:
                    result_data[speaker]["Self"] = custom_self_sort(result_data[speaker]["Self"], self.sort_debug_log)

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
