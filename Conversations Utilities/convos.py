import os
import re
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import pygame
from pathlib import Path
import threading
import time
import json
import openai
import io
import uuid
from datetime import datetime

# Globals for transcription
TRANSCRIPTIONS_DIR = "transcriptions"
OPENAI_API_KEY = None  # Will be set by user input
CHARACTER_MAPPINGS_FILE = "character_mappings.json"  # File to store character name mappings

class TranscriptionPopup(tk.Toplevel):
    """Popup window to display transcription results"""
    def __init__(self, parent, title, transcription, conversation_info):
        super().__init__(parent)
        self.title(title)
        self.geometry("800x600")
        self.minsize(600, 400)
        
        self.transcription = transcription
        self.conversation_info = conversation_info
        
        # Configure the grid
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=0)
        
        # Create frames
        info_frame = ttk.Frame(self, padding="10")
        info_frame.grid(row=0, column=0, sticky="ew")
        
        # Add conversation information
        char1, char2 = conversation_info['characters']
        convo_num = conversation_info['convo_num']
        ttk.Label(info_frame, text=f"Conversation #{convo_num} between {char1} and {char2}", 
                  font=("Helvetica", 12, "bold")).pack(anchor="w")
        
        # Add transcription content in a scrollable text widget
        text_frame = ttk.Frame(self, padding="10")
        text_frame.grid(row=1, column=0, sticky="nsew")
        
        # Scrollbar for the text widget
        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Text widget for displaying the transcription
        self.text_widget = tk.Text(text_frame, wrap=tk.WORD, yscrollcommand=scrollbar.set)
        self.text_widget.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.text_widget.yview)
        
        # Insert the transcription text
        self.text_widget.insert(tk.END, self.format_transcription())
        self.text_widget.config(state=tk.DISABLED)  # Make it read-only
        
        # Button frame
        button_frame = ttk.Frame(self, padding="10")
        button_frame.grid(row=2, column=0, sticky="ew")
        
        # Add export buttons
        ttk.Button(button_frame, text="Export as JSON", 
                  command=self.export_json).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Export as Text", 
                  command=self.export_text).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Export as HTML", 
                  command=self.export_html).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Close", 
                  command=self.destroy).pack(side=tk.RIGHT, padx=5)
    
    def format_transcription(self):
        """Format the transcription for display"""
        formatted_text = ""
        
        for segment in self.transcription['segments']:
            speaker = segment['speaker']
            text = segment['text']
            start_time = self.format_time(segment['start'])
            end_time = self.format_time(segment['end'])
            
            formatted_text += f"[{start_time} - {end_time}] {speaker}: {text}\n\n"
        
        return formatted_text
    
    def format_time(self, seconds):
        """Format time in seconds to MM:SS format"""
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"
    
    def export_json(self):
        """Export the transcription as JSON"""
        filename = self.get_export_filename(".json")
        if not filename:
            return
            
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(self.transcription, f, indent=2)
            
        messagebox.showinfo("Export Complete", f"Transcription exported to {filename}")
    
    def export_text(self):
        """Export the transcription as plain text"""
        filename = self.get_export_filename(".txt")
        if not filename:
            return
            
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(self.format_transcription())
            
        messagebox.showinfo("Export Complete", f"Transcription exported to {filename}")
    
    def export_html(self):
        """Export the transcription as HTML"""
        filename = self.get_export_filename(".html")
        if not filename:
            return
            
        # Create simple HTML with basic styling
        html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Conversation Transcription</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
        h1 { color: #333; }
        .segment { margin-bottom: 15px; }
        .time { color: #666; font-size: 0.8em; }
        .speaker { font-weight: bold; color: #0066cc; }
        .speaker.char1 { color: #0066cc; }
        .speaker.char2 { color: #cc6600; }
    </style>
</head>
<body>
"""
        char1, char2 = self.conversation_info['characters']
        convo_num = self.conversation_info['convo_num']
        html += f"<h1>Conversation #{convo_num} between {char1} and {char2}</h1>\n"
        
        for segment in self.transcription['segments']:
            speaker = segment['speaker']
            text = segment['text']
            start_time = self.format_time(segment['start'])
            end_time = self.format_time(segment['end'])
            
            speaker_class = "char1" if speaker == char1 else "char2"
            
            html += f'<div class="segment">\n'
            html += f'    <span class="time">[{start_time} - {end_time}]</span>\n'
            html += f'    <span class="speaker {speaker_class}">{speaker}:</span>\n'
            html += f'    <span class="text">{text}</span>\n'
            html += f'</div>\n'
        
        html += """</body>
</html>"""
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html)
            
        messagebox.showinfo("Export Complete", f"Transcription exported to {filename}")
    
    def get_export_filename(self, extension):
        """Get a filename for exporting"""
        char1, char2 = self.conversation_info['characters']
        convo_num = self.conversation_info['convo_num']
        default_name = f"{char1}_{char2}_convo{convo_num}{extension}"
        
        return filedialog.asksaveasfilename(
            initialdir=os.getcwd(),
            initialfile=default_name,
            title=f"Export Transcription as {extension}",
            filetypes=[("All Files", "*.*")],
            defaultextension=extension
        )

class ConversationPlayer:
    def __init__(self, root):
        self.root = root
        self.root.title("Character Conversation Player")
        self.root.geometry("800x600")
        self.audio_dir = os.getcwd()  # Default to current directory
        self.transcriptions_dir = TRANSCRIPTIONS_DIR  # Default transcriptions directory

        # Initialize pygame mixer for audio playback
        pygame.mixer.init()

        # Track playback state
        self.playing = False
        self.current_playlist = []
        self.current_track_index = 0

        # Transcription cache
        self.transcription_cache = {}

        # Character name mappings - load first before creating widgets
        self.character_mappings = {}
        self.load_character_mappings()

        # Create GUI elements
        self.create_widgets()

        # Load initial data
        self.conversations = {}
        self.characters = []
        self.character_pairs = {}  # Track which characters have conversations together
        self.convo_keys = []  # Track conversation keys for listbox selection

        # Ensure transcriptions directory exists
        os.makedirs(self.transcriptions_dir, exist_ok=True)

        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Check for API key on startup
        self.check_api_key()
    
    def check_api_key(self):
        """Check if OpenAI API key is available"""
        global OPENAI_API_KEY
        
        # First check environment variable
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            OPENAI_API_KEY = api_key
            # No need to set a global client - we'll create it when needed
            return True
        
        # Check for saved key
        key_file = os.path.join(os.getcwd(), ".openai_key")
        if os.path.exists(key_file):
            try:
                with open(key_file, "r") as f:
                    api_key = f.read().strip()
                if api_key:
                    OPENAI_API_KEY = api_key
                    # No need to set a global client - we'll create it when needed
                    return True
            except:
                pass
        
        # No key found
        return False
    
    def set_api_key(self):
        """Set the OpenAI API key"""
        global OPENAI_API_KEY
        
        api_key = simpledialog.askstring("OpenAI API Key", 
                                       "Enter your OpenAI API key:", 
                                       parent=self.root)
        if not api_key:
            return False
        
        # Save key for future use
        OPENAI_API_KEY = api_key
        
        # Save to file for future use
        key_file = os.path.join(os.getcwd(), ".openai_key")
        try:
            with open(key_file, "w") as f:
                f.write(api_key)
        except:
            messagebox.showwarning("Warning", "Could not save API key to file.")
        
        return True
    
    def parse_audio_files(self):
        """Parse audio files and organize them into conversations"""
        conversations = {}
        
        # Get list of audio files
        try:
            files = [f for f in os.listdir(self.audio_dir) if f.endswith(".mp3")]
            self.status_var.set(f"Found {len(files)} audio files")
        except Exception as e:
            messagebox.showerror("Error", f"Could not read directory: {self.audio_dir}\n{str(e)}")
            return {}
        
        if not files:
            messagebox.showinfo("Info", "No MP3 files found in the selected directory")
            return {}
            
        # Regular expression to extract information from filenames
        # Format examples:
        # - [char1]_match_start_[char1]_[char2]_convo[##]_[##]_[##].mp3
        # - [char1]_match_start_[char1]_[char2]_[topic]_convo[##]_[##]_[##].mp3
        
        # First try the pattern with a topic
        pattern_with_topic = r'(\w+)_match_start_(\w+)_(\w+)_(\w+)_convo(\d+)_(\d+)(?:_(\d+))?\.mp3'
        
        # Fallback pattern without a topic
        pattern_without_topic = r'(\w+)_match_start_(\w+)_(\w+)_convo(\d+)_(\d+)(?:_(\d+))?\.mp3'
        
        for filename in files:
            # First try to match the pattern with a topic
            match = re.match(pattern_with_topic, filename)
            if match:
                # Extract character and conversation info with topic
                groups = match.groups()
                starter, char1, char2, topic, convo_num, part_num = groups[:6]
                variation = groups[6] if len(groups) > 6 and groups[6] is not None else "1"
                
                # Apply character name mappings
                starter = self.character_mappings.get(starter, starter)
                char1 = self.character_mappings.get(char1, char1)
                char2 = self.character_mappings.get(char2, char2)
                
            else:
                # Try the pattern without a topic
                match = re.match(pattern_without_topic, filename)
                if match:
                    # Extract character and conversation info without topic
                    groups = match.groups()
                    starter, char1, char2, convo_num, part_num = groups[:5]
                    variation = groups[5] if len(groups) > 5 and groups[5] is not None else "1"
                    topic = None
                    
                    # Apply character name mappings
                    starter = self.character_mappings.get(starter, starter)
                    char1 = self.character_mappings.get(char1, char1)
                    char2 = self.character_mappings.get(char2, char2)
                else:
                    # No match found, skip this file
                    continue
            
            # Create a sorted tuple of characters to normalize character order
            char_pair = tuple(sorted([char1, char2]))
            
            # Create key for this conversation - include topic to make different topics separate conversations
            if topic:
                convo_key = (char_pair, convo_num, topic)
            else:
                convo_key = (char_pair, convo_num)
            
            # Add file to the appropriate conversation
            if convo_key not in conversations:
                conversations[convo_key] = []
            
            conversations[convo_key].append({
                'filename': filename,
                'part': int(part_num),
                'variation': int(variation),
                'characters': (char1, char2),
                'starter': starter,
                'topic': topic
            })
        
        # Group files by their part number to handle variations
        for convo_key, files in conversations.items():
            # Group files by part number
            part_groups = {}
            for file in files:
                part = file['part']
                if part not in part_groups:
                    part_groups[part] = []
                part_groups[part].append(file)
            
            # Sort variations within each part
            for part, variations in part_groups.items():
                variations.sort(key=lambda x: x['variation'])
            
            # Get unique part numbers for completeness check
            unique_parts = sorted(part_groups.keys())
            min_part = min(unique_parts) if unique_parts else 0
            max_part = max(unique_parts) if unique_parts else 0
            expected_parts = list(range(min_part, max_part + 1))
            
            # Determine if the conversation is complete based on three criteria:
            # 1. No gaps in part numbers
            no_gaps = (len(unique_parts) == len(expected_parts) and 
                      all(p in unique_parts for p in expected_parts))
            
            # 2. It starts with part 1
            starts_with_one = (min_part == 1)
            
            # 3. It has more than one part (a complete conversation needs at least two parts)
            has_multiple_parts = (len(unique_parts) > 1)
            
            # A conversation is complete only if all criteria are met
            is_complete = no_gaps and starts_with_one and has_multiple_parts
            
            # Determine the reason for incompleteness
            missing_reasons = []
            missing_parts = []
            
            if not starts_with_one:
                missing_reasons.append(f"Missing parts 1-{min_part-1}")
                missing_parts.extend(list(range(1, min_part)))
            
            if not no_gaps:
                gap_parts = [p for p in expected_parts if p not in unique_parts]
                if gap_parts:
                    missing_reasons.append(f"Missing parts: {', '.join(map(str, gap_parts))}")
                    missing_parts.extend(gap_parts)
            
            if not has_multiple_parts:
                missing_reasons.append("Only one part found")
            
            # Add part_groups to each file for later use
            for file in files:
                file['is_complete'] = is_complete
                file['missing_parts'] = sorted(missing_parts)
                file['missing_reasons'] = missing_reasons
                file['part_groups'] = part_groups
        
        return conversations
    
    def create_widgets(self):
        """Create the GUI elements"""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Directory selection
        dir_frame = ttk.LabelFrame(main_frame, text="Audio Files Directory", padding="10")
        dir_frame.pack(fill=tk.X, pady=5)

        self.dir_var = tk.StringVar(value=self.audio_dir)
        dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var, width=50)
        dir_entry.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W+tk.E)

        browse_button = ttk.Button(dir_frame, text="Browse...", command=self.browse_directory)
        browse_button.grid(row=0, column=1, padx=5, pady=5)

        load_button = ttk.Button(dir_frame, text="Load Files", command=self.load_directory)
        load_button.grid(row=0, column=2, padx=5, pady=5)

        # Add Export All button
        export_button = ttk.Button(dir_frame, text="Export All to JSON", command=self.export_all_conversations)
        export_button.grid(row=0, column=3, padx=5, pady=5)

        # Add Character Mappings button
        mappings_button = ttk.Button(dir_frame, text="Character Mappings", command=self.edit_character_mappings)
        mappings_button.grid(row=0, column=4, padx=5, pady=5)

        # Transcriptions directory selection
        trans_dir_frame = ttk.LabelFrame(main_frame, text="Transcriptions Directory", padding="10")
        trans_dir_frame.pack(fill=tk.X, pady=5)

        self.trans_dir_var = tk.StringVar(value=self.transcriptions_dir)
        trans_dir_entry = ttk.Entry(trans_dir_frame, textvariable=self.trans_dir_var, width=50)
        trans_dir_entry.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W+tk.E)

        trans_browse_button = ttk.Button(trans_dir_frame, text="Browse...", command=self.browse_transcriptions_directory)
        trans_browse_button.grid(row=0, column=1, padx=5, pady=5)
        
        # Character selection
        char_frame = ttk.LabelFrame(main_frame, text="Character Selection", padding="10")
        char_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(char_frame, text="Character 1:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.char1_var = tk.StringVar()
        self.char1_dropdown = ttk.Combobox(char_frame, textvariable=self.char1_var, 
                                          values=[], width=20)
        self.char1_dropdown.grid(row=0, column=1, padx=5, pady=5)
        self.char1_dropdown.bind("<<ComboboxSelected>>", self.update_char2_options)
        
        ttk.Label(char_frame, text="Character 2:").grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
        self.char2_var = tk.StringVar()
        self.char2_dropdown = ttk.Combobox(char_frame, textvariable=self.char2_var, 
                                          values=[], width=20)
        self.char2_dropdown.grid(row=0, column=3, padx=5, pady=5)
        self.char2_dropdown.bind("<<ComboboxSelected>>", self.update_conversation_list)
        
        # Conversation list
        convo_frame = ttk.LabelFrame(main_frame, text="Available Conversations", padding="10")
        convo_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Scrollbar for conversation list
        scrollbar = ttk.Scrollbar(convo_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Conversation listbox
        self.convo_listbox = tk.Listbox(convo_frame, yscrollcommand=scrollbar.set, height=10, font=("Helvetica", 10))
        self.convo_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.convo_listbox.yview)
        
        # Playback controls
        control_frame = ttk.Frame(main_frame, padding="10")
        control_frame.pack(fill=tk.X, pady=5)
        
        # Add variation selection options
        variation_frame = ttk.LabelFrame(main_frame, text="Variation Selection", padding="10")
        variation_frame.pack(fill=tk.X, pady=5)
        
        self.variation_var = tk.StringVar(value="Use Default Variations")
        ttk.Radiobutton(variation_frame, text="Use Default Variations (First of Each Part)", 
                        variable=self.variation_var, value="Use Default Variations",
                        command=self.update_variation_selection).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(variation_frame, text="Select Variations for Each Part", 
                        variable=self.variation_var, value="Select Variations",
                        command=self.update_variation_selection).pack(anchor=tk.W, pady=2)
        
        # Frame for part variation selection (initially hidden)
        self.part_selection_frame = ttk.Frame(variation_frame)
        self.part_selection_frame.pack(fill=tk.X, pady=5)
        
        self.play_button = ttk.Button(control_frame, text="Play", command=self.play_conversation)
        self.play_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = ttk.Button(control_frame, text="Stop", command=self.stop_playback)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        # Add transcribe button
        self.transcribe_button = ttk.Button(control_frame, text="Transcribe", command=self.transcribe_conversation)
        self.transcribe_button.pack(side=tk.LEFT, padx=5)
        
        # API key button
        self.api_key_button = ttk.Button(control_frame, text="Set API Key", command=self.set_api_key)
        self.api_key_button.pack(side=tk.RIGHT, padx=5)
        
        # Status bar
        self.status_var = tk.StringVar()
        self.status_var.set("Select a directory and load files")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, pady=5)
        
        # Now playing info
        self.now_playing_var = tk.StringVar()
        self.now_playing_var.set("No conversation selected")
        now_playing_label = ttk.Label(main_frame, textvariable=self.now_playing_var, 
                                     font=("Helvetica", 10, "italic"))
        now_playing_label.pack(fill=tk.X, pady=5)
        
        # Bind selection event to show variation options
        self.convo_listbox.bind("<<ListboxSelect>>", self.show_variation_options)
    
    def browse_directory(self):
        """Open directory browser dialog"""
        directory = filedialog.askdirectory(initialdir=self.audio_dir, title="Select Audio Files Directory")
        if directory:
            self.dir_var.set(directory)

    def browse_transcriptions_directory(self):
        """Open directory browser dialog for transcriptions"""
        directory = filedialog.askdirectory(initialdir=self.transcriptions_dir, title="Select Transcriptions Directory")
        if directory:
            self.trans_dir_var.set(directory)
            self.transcriptions_dir = directory
            os.makedirs(self.transcriptions_dir, exist_ok=True)
    
    def load_directory(self):
        """Load audio files from the selected directory"""
        new_dir = self.dir_var.get()
        if not os.path.isdir(new_dir):
            messagebox.showerror("Error", f"Invalid directory: {new_dir}")
            return
        
        self.audio_dir = new_dir
        self.status_var.set(f"Loading files from {self.audio_dir}...")
        self.root.update()
        
        # Reset current data
        self.stop_playback()
        self.convo_listbox.delete(0, tk.END)
        self.now_playing_var.set("No conversation selected")
        self.char1_var.set("")
        self.char2_var.set("")
        
        # Make sure character mappings are loaded before parsing files
        if not self.character_mappings:
            self.load_character_mappings()
        
        # Load new data
        self.conversations = self.parse_audio_files()
        
        # Extract unique characters and build relationship mappings
        character_set = set()
        self.character_pairs = {}
        
        for convo_key in self.conversations.keys():
            # Extract the character pair from the key (first element)
            char_pair = convo_key[0]
            
            # Add characters to the set
            character_set.update(char_pair)
            
            # Track which characters have conversations with each other
            char1, char2 = char_pair
            
            if char1 not in self.character_pairs:
                self.character_pairs[char1] = set()
            if char2 not in self.character_pairs:
                self.character_pairs[char2] = set()
                
            self.character_pairs[char1].add(char2)
            self.character_pairs[char2].add(char1)
            
        self.characters = sorted(character_set)
        
        # Update character lists
        self.char1_dropdown.config(values=self.characters)
        self.char2_dropdown.config(values=[])
        
        if self.characters:
            self.status_var.set(f"Loaded {len(self.conversations)} conversations between {len(self.characters)} characters")
        else:
            self.status_var.set("No valid conversation audio files found")
    
    def update_char2_options(self, event=None):
        """Update the second character dropdown based on first character selection"""
        char1 = self.char1_var.get()
        self.char2_var.set("")  # Clear the second character selection
        self.convo_listbox.delete(0, tk.END)
        
        if not char1 or char1 not in self.character_pairs:
            self.char2_dropdown.config(values=[])
            return
            
        # Get all characters that have conversations with the first character
        related_chars = sorted(self.character_pairs[char1])
        
        # Add ALL option at the beginning
        related_chars = ["(ALL)"] + related_chars
        
        self.char2_dropdown.config(values=related_chars)
        
        # Update status
        self.status_var.set(f"Character {char1} has conversations with {len(related_chars)-1} other characters")
    
    def update_conversation_list(self, event=None):
        """Update the conversation list based on selected characters"""
        self.convo_listbox.delete(0, tk.END)
        
        char1 = self.char1_var.get()
        char2 = self.char2_var.get()
        
        if not char1:
            return
        
        # Clear existing keys
        self.convo_keys = []
        
        # Check if ALL is selected
        if char2 == "(ALL)":
            # Display all conversations with this character
            all_convos = []
            
            for convo_key, files in self.conversations.items():
                # Handle both 2-tuple and 3-tuple keys (with and without topic)
                if len(convo_key) >= 2:  # Ensure we have at least char_pair and convo_num
                    pair = convo_key[0]
                    convo_num = convo_key[1]
                    topic = convo_key[2] if len(convo_key) > 2 else None
                    
                    if char1 in pair:
                        # Get the other character in the pair
                        other_char = pair[0] if pair[1] == char1 else pair[1]
                        all_convos.append({
                            'pair': pair,
                            'convo_num': convo_num,
                            'other_char': other_char,
                            'files': files,
                            'topic': topic
                        })
            
            # Sort by other character name, then by conversation number, then by topic
            all_convos.sort(key=lambda x: (x['other_char'], int(x['convo_num']), x['topic'] or ""))
            
            if not all_convos:
                self.convo_listbox.insert(tk.END, f"No conversations found for {char1}")
                return
                
            # Display conversations
            for convo in all_convos:
                pair = convo['pair']
                convo_num = convo['convo_num']
                other_char = convo['other_char']
                files = convo['files']
                topic = convo['topic']
                
                if 'part_groups' not in files[0]:
                    continue
                
                part_groups = files[0]['part_groups']
                unique_parts = len(part_groups)
                total_variations = sum(len(variations) for variations in part_groups.values())
                parts_with_variations = sum(1 for variations in part_groups.values() if len(variations) > 1)
                
                duration = sum(os.path.getsize(os.path.join(self.audio_dir, f['filename'])) for f in files) / 100000
                starter = files[0]['starter']
                is_complete = files[0]['is_complete']
                
                # Create display string
                display_text = f"{char1} & {other_char} - Conversation {convo_num}"
                
                # Add topic if available
                if topic:
                    display_text += f" ({topic})"
                    
                display_text += f" ({unique_parts} parts"
                
                # Add variation info if any
                if parts_with_variations > 0:
                    display_text += f", {total_variations} takes, {parts_with_variations} parts with alternatives"
                
                display_text += f", ~{duration:.1f}s)"
                
                # Add completeness information
                if not is_complete:
                    if 'missing_reasons' in files[0] and files[0]['missing_reasons']:
                        reasons = files[0]['missing_reasons']
                        display_text += f" [INCOMPLETE - {'; '.join(reasons)}]"
                    else:
                        missing = files[0]['missing_parts']
                        display_text += f" [INCOMPLETE - Missing parts: {', '.join(map(str, missing))}]"
                    
                # Add to listbox
                self.convo_listbox.insert(tk.END, display_text)
                
                # Store the conversation key - include topic if it exists
                if topic:
                    self.convo_keys.append((pair, convo_num, topic))
                else:
                    self.convo_keys.append((pair, convo_num))
                
                # Get the current index
                current_index = self.convo_listbox.size() - 1
                
                # Set the color based on completeness
                if not is_complete:
                    self.convo_listbox.itemconfig(current_index, foreground="red")
                else:
                    self.convo_listbox.itemconfig(current_index, foreground="green")
                    
            self.status_var.set(f"Showing all {len(all_convos)} conversations for {char1}")
            
        else:
            # Regular display for a specific character pair
            if not char2:
                return
                
            # Get conversation pairs for these characters
            char_pair = tuple(sorted([char1, char2]))
            
            # Group conversations by conversation number and topic
            convo_groups = {}
            for convo_key, files in self.conversations.items():
                # Handle both 2-tuple and 3-tuple keys (with and without topic)
                if len(convo_key) >= 2:  # Ensure we have at least char_pair and convo_num
                    pair = convo_key[0]
                    convo_num = convo_key[1]
                    topic = convo_key[2] if len(convo_key) > 2 else None
                    
                    if pair == char_pair:
                        # Use topic in the key if it exists
                        group_key = (convo_num, topic) if topic else (convo_num,)
                        convo_groups[group_key] = (convo_key, files)
            
            if not convo_groups:
                self.convo_listbox.insert(tk.END, f"No conversations found between {char1} and {char2}")
                return
                
            # Sort by conversation number and then by topic
            sorted_keys = sorted(convo_groups.keys(), key=lambda x: (int(x[0]), x[1] if len(x) > 1 else ""))
            
            # Add conversations to listbox
            for group_key in sorted_keys:
                convo_key, files = convo_groups[group_key]
                convo_num = convo_key[1]
                topic = convo_key[2] if len(convo_key) > 2 else None
                
                if 'part_groups' not in files[0]:
                    continue
                
                part_groups = files[0]['part_groups']
                unique_parts = len(part_groups)
                total_variations = sum(len(variations) for variations in part_groups.values())
                parts_with_variations = sum(1 for variations in part_groups.values() if len(variations) > 1)
                
                duration = sum(os.path.getsize(os.path.join(self.audio_dir, f['filename'])) for f in files) / 100000
                starter = files[0]['starter']
                is_complete = files[0]['is_complete']
                
                # Create display string
                display_text = f"Conversation {convo_num}"
                
                # Add topic if available
                if topic:
                    display_text += f" ({topic})"
                    
                display_text += f" ({unique_parts} parts"
                
                # Add variation info if any
                if parts_with_variations > 0:
                    display_text += f", {total_variations} takes, {parts_with_variations} parts with alternatives"
                
                display_text += f", ~{duration:.1f}s)"
                
                # Add completeness information
                if not is_complete:
                    if 'missing_reasons' in files[0] and files[0]['missing_reasons']:
                        reasons = files[0]['missing_reasons']
                        display_text += f" [INCOMPLETE - {'; '.join(reasons)}]"
                    else:
                        missing = files[0]['missing_parts']
                        display_text += f" [INCOMPLETE - Missing parts: {', '.join(map(str, missing))}]"
                    
                # Add to listbox
                self.convo_listbox.insert(tk.END, display_text)
                
                # Store the conversation key
                self.convo_keys.append(convo_key)
                
                # Get the current index
                current_index = self.convo_listbox.size() - 1
                
                # Set the color based on completeness
                if not is_complete:
                    self.convo_listbox.itemconfig(current_index, foreground="red")
                else:
                    self.convo_listbox.itemconfig(current_index, foreground="green")
                    
            self.status_var.set(f"Showing {len(convo_groups)} conversations between {char1} and {char2}")
    
    def show_variation_options(self, event=None):
        """Show variation options for the selected conversation"""
        # Clear the current part selection frame
        for widget in self.part_selection_frame.winfo_children():
            widget.destroy()
        
        # Check if we have a selection
        selection = self.convo_listbox.curselection()
        if not selection:
            return
            
        # Check if we have valid data
        if not hasattr(self, 'convo_keys') or selection[0] >= len(self.convo_keys):
            return
            
        convo_key = self.convo_keys[selection[0]]
        if convo_key not in self.conversations:
            return
            
        conversation_files = self.conversations[convo_key]
        if not conversation_files:
            return
            
        # Get the part groups from the first file
        if 'part_groups' not in conversation_files[0]:
            return
            
        part_groups = conversation_files[0]['part_groups']
        
        # If there are no multiple variations for any part, don't show selection
        has_variations = False
        for part, variations in part_groups.items():
            if len(variations) > 1:
                has_variations = True
                break
                
        if not has_variations:
            ttk.Label(self.part_selection_frame, text="No multiple variations found").pack(pady=5)
            return
        
        # Create the UI for part-by-part variation selection
        # Create selection dropdowns for each part that has multiple variations
        self.variation_selections = {}  # Store the selection variables
        
        # Sort parts for display
        sorted_parts = sorted(part_groups.keys())
        
        for part in sorted_parts:
            variations = part_groups[part]
            if len(variations) > 1:
                # Create a frame for this part
                part_frame = ttk.Frame(self.part_selection_frame)
                part_frame.pack(fill=tk.X, pady=2)
                
                ttk.Label(part_frame, text=f"Part {part}:").pack(side=tk.LEFT, padx=(0, 5))
                
                # Create dropdown for variation selection
                var = tk.StringVar()
                var.set(f"Variation 1 (Default)")  # Default to first variation
                
                # Create list of options
                options = []
                for i, variation in enumerate(variations):
                    var_num = variation['variation']
                    file_size = os.path.getsize(os.path.join(self.audio_dir, variation['filename'])) / 1000
                    options.append(f"Variation {var_num} ({file_size:.1f} KB)")
                
                dropdown = ttk.Combobox(part_frame, textvariable=var, values=options, width=30)
                dropdown.pack(side=tk.LEFT, padx=5)
                
                # Store the selection variable
                self.variation_selections[part] = (var, variations)
    
    def play_conversation(self):
        """Play the selected conversation"""
        if self.playing:
            self.stop_playback()
            
        selection = self.convo_listbox.curselection()
        if not selection:
            messagebox.showinfo("Info", "Please select a conversation to play")
            return
        
        char1 = self.char1_var.get()
        if not char1:
            messagebox.showinfo("Info", "Please select at least the first character")
            return
        
        # Get the selected conversation key directly from our stored mapping
        if not hasattr(self, 'convo_keys') or selection[0] >= len(self.convo_keys):
            messagebox.showerror("Error", "Conversation not found")
            return
            
        convo_key = self.convo_keys[selection[0]]
        
        # Get files for this conversation
        if convo_key not in self.conversations:
            messagebox.showerror("Error", "Conversation data not found")
            return
            
        conversation_files = self.conversations[convo_key]
        
        # Warn if conversation is incomplete
        if not conversation_files[0]['is_complete']:
            warning_message = "This conversation is incomplete."
            
            if 'missing_reasons' in conversation_files[0] and conversation_files[0]['missing_reasons']:
                reasons = conversation_files[0]['missing_reasons']
                warning_message += f"\n\nReasons: {'; '.join(reasons)}"
            else:
                missing = conversation_files[0]['missing_parts']
                warning_message += f"\n\nMissing parts: {', '.join(map(str, missing))}"
                
            warning_message += "\n\nPlay anyway?"
            
            result = messagebox.askquestion("Warning", warning_message)
            if result != "yes":
                return
        
        # Get part groups
        part_groups = conversation_files[0]['part_groups']
        
        # Create playlist based on selected variations
        self.current_playlist = []
        
        if self.variation_var.get() == "Use Default Variations":
            # Use the first variation of each part (default)
            for part in sorted(part_groups.keys()):
                variations = part_groups[part]
                if variations:  # Should always be true
                    file = variations[0]  # Take the first variation (already sorted)
                    self.current_playlist.append(os.path.join(self.audio_dir, file['filename']))
        else:
            # Use selected variations if available
            for part in sorted(part_groups.keys()):
                if part in self.variation_selections:
                    var, variations = self.variation_selections[part]
                    # Extract the selected variation index (parse "Variation X" from string)
                    selection_text = var.get()
                    try:
                        selected_var_num = int(selection_text.split()[1])
                        # Find the variation with this number
                        matching_variations = [v for v in variations if v['variation'] == selected_var_num]
                        if matching_variations:
                            file = matching_variations[0]
                        else:
                            file = variations[0]  # Fallback to first if not found
                    except:
                        file = variations[0]  # Fallback to first on parsing error
                else:
                    # If no selection UI for this part (only one variation), take the first
                    variations = part_groups[part]
                    file = variations[0]
                
                self.current_playlist.append(os.path.join(self.audio_dir, file['filename']))
        
        # Start playback thread
        self.playing = True
        self.current_track_index = 0
        threading.Thread(target=self.playback_thread, daemon=True).start()
        
        # Get character names from the pair
        char_pair = convo_key[0]
        char1_name, char2_name = char_pair
        
        # Get conversation number and topic (if available)
        convo_num = convo_key[1]
        topic_text = ""
        if len(convo_key) > 2:
            topic_text = f" ({convo_key[2]})"
        
        # Update UI
        self.status_var.set("Playing...")
        self.now_playing_var.set(f"Now playing: {char1_name} and {char2_name} - Conversation {convo_num}{topic_text}")
    
    def playback_thread(self):
        """Thread for sequential audio playback"""
        while self.playing and self.current_track_index < len(self.current_playlist):
            # Play current track
            current_file = self.current_playlist[self.current_track_index]
            filename = os.path.basename(current_file)
            
            try:
                pygame.mixer.music.load(current_file)
                pygame.mixer.music.play()
                
                # Update status
                self.root.after(0, lambda f=filename: self.status_var.set(f"Playing: {f}"))
                
                # Wait for playback to complete
                while pygame.mixer.music.get_busy() and self.playing:
                    time.sleep(0.1)
                
                # Move to next track if still playing
                if self.playing:
                    self.current_track_index += 1
            except Exception as e:
                self.root.after(0, lambda err=str(e): messagebox.showerror("Playback Error", err))
                break
        
        # Reset status when playback completes
        if self.playing:
            self.playing = False
            self.root.after(0, lambda: self.status_var.set("Playback complete"))
    
    def stop_playback(self):
        """Stop the current playback"""
        self.playing = False
        pygame.mixer.music.stop()
        self.status_var.set("Stopped")
    
    def on_close(self):
        """Handle window close event"""
        self.stop_playback()
        pygame.mixer.quit()
        self.save_character_mappings()  # Save mappings on exit
        self.root.destroy()
    
    def transcribe_conversation(self):
        """Transcribe the selected conversation using OpenAI's Whisper API"""
        # Check for API key
        if not OPENAI_API_KEY:
            if not self.set_api_key():
                messagebox.showerror("API Key Required", 
                                    "OpenAI API key is required for transcription.")
                return
        
        # Check for selection
        selection = self.convo_listbox.curselection()
        if not selection:
            messagebox.showinfo("Info", "Please select a conversation to transcribe")
            return
        
        # Get the selected conversation
        convo_key = self.convo_keys[selection[0]]
        
        # Check if this conversation has already been transcribed
        cache_key = str(convo_key)
        if cache_key in self.transcription_cache:
            self.show_transcription(self.transcription_cache[cache_key], convo_key)
            return
        
        # Get files for this conversation
        if convo_key not in self.conversations:
            messagebox.showerror("Error", "Conversation data not found")
            return
            
        conversation_files = self.conversations[convo_key]
        
        # Get the part groups and determine which files to transcribe
        part_groups = conversation_files[0]['part_groups']
        files_to_transcribe = []
        
        # Use the same logic as playback to determine which files to transcribe
        if self.variation_var.get() == "Use Default Variations":
            # Use the first variation of each part
            for part in sorted(part_groups.keys()):
                variations = part_groups[part]
                if variations:
                    file = variations[0]
                    files_to_transcribe.append(file)
        else:
            # Use selected variations if available
            for part in sorted(part_groups.keys()):
                if part in self.variation_selections:
                    var, variations = self.variation_selections[part]
                    selection_text = var.get()
                    try:
                        selected_var_num = int(selection_text.split()[1])
                        matching_variations = [v for v in variations if v['variation'] == selected_var_num]
                        if matching_variations:
                            file = matching_variations[0]
                        else:
                            file = variations[0]
                    except:
                        file = variations[0]
                else:
                    variations = part_groups[part]
                    file = variations[0]
                
                files_to_transcribe.append(file)
        
        # Get conversation metadata
        char_pair = convo_key[0]
        convo_num = convo_key[1]
        conversation_info = {
            'characters': char_pair,
            'convo_num': convo_num
        }
        
        # Start transcription in a separate thread
        self.status_var.set("Starting transcription...")
        threading.Thread(
            target=self._transcription_thread,
            args=(files_to_transcribe, convo_key, conversation_info),
            daemon=True
        ).start()
    
    def _transcription_thread(self, files, convo_key, conversation_info):
        """Thread for handling the transcription process"""
        try:
            self.root.after(0, lambda: self.status_var.set("Transcribing... This may take a while."))
            
            # Get topic if available
            topic = convo_key[2] if len(convo_key) > 2 else None
            
            # Prepare the transcription data structure
            transcription = {
                'conversation_id': str(convo_key),
                'characters': conversation_info['characters'],
                'convo_num': conversation_info['convo_num'],
                'topic': topic,
                'timestamp': datetime.now().isoformat(),
                'segments': []
            }
            
            # Process each file
            current_time = 0.0
            
            for file_data in files:
                file_path = os.path.join(self.audio_dir, file_data['filename'])
                part_num = file_data['part']
                
                # Get file last modified date
                try:
                    file_modified_time = os.path.getmtime(file_path)
                    file_modified_date = datetime.fromtimestamp(file_modified_time).isoformat()
                except:
                    file_modified_date = None
                
                # Determine the speaker for this part
                filename = file_data['filename']
                part_speaker = self._get_speaker_from_filename(filename)
                
                # Update UI
                self.root.after(0, lambda f=filename: self.status_var.set(f"Transcribing part {part_num}: {f}"))
                
                # Transcribe the file
                file_transcription = self._transcribe_file(file_path)
                
                if file_transcription:
                    # Add segments with adjusted timestamps
                    for segment in file_transcription['segments']:
                        segment['start'] += current_time
                        segment['end'] += current_time
                        segment['speaker'] = part_speaker
                        segment['part'] = part_num
                        segment['file_creation_date'] = file_modified_date
                        transcription['segments'].append(segment)
                    
                    # Update the current time for the next file
                    if file_transcription['segments']:
                        last_segment = file_transcription['segments'][-1]
                        current_time = last_segment['end'] + 1.0  # Add a 1-second gap
                
                # Small delay to allow UI updates
                time.sleep(0.1)
            
            # Cache the transcription
            cache_key = str(convo_key)
            self.transcription_cache[cache_key] = transcription
            
            # Save the transcription to a file
            self._save_transcription(transcription, convo_key)
            
            # Show the transcription
            self.root.after(0, lambda: self.show_transcription(transcription, convo_key))
            
        except Exception as e:
            self.root.after(0, lambda err=str(e): messagebox.showerror("Transcription Error", f"Error transcribing files: {err}"))
        finally:
            self.root.after(0, lambda: self.status_var.set("Transcription complete"))
    
    def _transcribe_file(self, file_path):
        """Transcribe a single audio file using OpenAI's Whisper API"""
        try:
            # Check for cached transcription
            cache_file = os.path.join(self.transcriptions_dir, f"{os.path.basename(file_path)}.json")
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except:
                    pass  # If loading fails, continue with new transcription
            
            # Open the audio file
            with open(file_path, 'rb') as audio_file:
                # Call Whisper API with the updated client
                client = openai.OpenAI(api_key=OPENAI_API_KEY)
                response = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"]
                )
                
                # Convert response to dictionary if it's not already
                if not isinstance(response, dict):
                    response = response.model_dump()
                
                # Process response to extract segments
                segments = []
                
                for segment in response.get('segments', []):
                    segments.append({
                        'start': segment.get('start', 0),
                        'end': segment.get('end', 0),
                        'text': segment.get('text', '').strip()
                    })
                
                result = {
                    'file': os.path.basename(file_path),
                    'text': response.get('text', ''),
                    'segments': segments
                }
                
                # Cache the result
                try:
                    os.makedirs(self.transcriptions_dir, exist_ok=True)
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        json.dump(result, f, indent=2)
                except:
                    pass  # Ignore cache write errors
                
                return result
                
        except Exception as e:
            print(f"Error transcribing {file_path}: {str(e)}")
            return None
    
    def _get_speaker_from_filename(self, filename):
        """Extract the speaker from the filename as the first word before the underscore"""
        # Simply get the first part of the filename before the first underscore
        first_part = filename.split('_')[0]
        
        # Apply character name mappings if available
        speaker = self.character_mappings.get(first_part, first_part)
        
        return speaker
    
    def _save_transcription(self, transcription, convo_key):
        """Save transcription to a file"""
        try:
            os.makedirs(self.transcriptions_dir, exist_ok=True)

            char1, char2 = transcription['characters']
            convo_num = transcription['convo_num']

            # Get topic if available (it's the third element in the convo_key tuple if it exists)
            topic = convo_key[2] if len(convo_key) > 2 else None

            # Add topic to the transcription data
            transcription['topic'] = topic

            # Add file creation date
            transcription['creation_date'] = datetime.now().isoformat()

            # Create filename (include topic if available)
            if topic:
                filename = os.path.join(self.transcriptions_dir, f"{char1}_{char2}_convo{convo_num}_{topic}.json")
            else:
                filename = os.path.join(self.transcriptions_dir, f"{char1}_{char2}_convo{convo_num}.json")

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(transcription, f, indent=2)

            return filename
        except Exception as e:
            print(f"Error saving transcription: {str(e)}")
            return None
    
    def show_transcription(self, transcription, convo_key):
        """Show the transcription in a popup window"""
        # Get conversation metadata
        char_pair = convo_key[0]
        convo_num = convo_key[1]
        conversation_info = {
            'characters': char_pair,
            'convo_num': convo_num
        }
        
        # Create popup window
        popup = TranscriptionPopup(
            self.root,
            f"Transcription - Conversation {convo_num}",
            transcription,
            conversation_info
        )

    def _generate_conversation_summary(self, conversation_data):
        """Generate a single-sentence summary of a conversation using OpenAI API"""
        try:
            # Check if we have an API key
            if not OPENAI_API_KEY:
                return "[Summary not available - API key required]"
            
            # Prepare the conversation text for summarization
            lines_text = []
            for line in conversation_data["lines"]:
                speaker = line["speaker"]
                text = line["transcription"]
                if text and not text.startswith("[Transcription"):  # Only include actual transcriptions
                    lines_text.append(f"{speaker}: {text}")
            
            # If we don't have enough transcribed content, return a placeholder
            if len(lines_text) < 2:
                return "[Not enough transcribed content for summary]"
            
            conversation_text = "\n".join(lines_text)
            
            # Create a prompt for the API with 7-word limit
            prompt = f"""Below is a conversation between {conversation_data['speakers'][0]} and {conversation_data['speakers'][1]}.
Please provide a summary of what this conversation is about in NO MORE THAN 7 WORDS:

{conversation_text}

Summary (maximum 7 words):"""
            
            # Call the OpenAI API
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that summarizes conversations in exactly 7 words or fewer."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=50,
                temperature=0.7
            )
            
            # Extract and process the summary
            summary = response.choices[0].message.content.strip()
            
            # Ensure it's no more than 7 words
            words = summary.split()
            if len(words) > 7:
                summary = " ".join(words[:7])
                # Add period if it doesn't end with punctuation
                if not summary[-1] in ['.', '!', '?']:
                    summary += '.'
            
            return summary
            
        except Exception as e:
            print(f"Error generating summary: {str(e)}")
            return f"[Summary generation failed: {str(e)}]"

    def export_all_conversations(self):
        """Export all conversations to a single JSON file"""
        if not self.conversations:
            messagebox.showinfo("Info", "No conversations loaded. Please load files first.")
            return
        
        # Ask if the user wants to generate transcriptions during export
        transcribe_all = messagebox.askyesno(
            "Transcription Option",
            "Do you want to generate transcriptions for all conversations during export?\n\n"
            "Note: This may take a long time if you have many conversations."
        )
        
        # Ask if the user wants to generate summaries
        generate_summaries = messagebox.askyesno(
            "Summary Option",
            "Do you want to generate a single-sentence summary for each conversation?\n\n"
            "Note: This requires transcriptions and will use the OpenAI API."
        )
        
        # Check for API key if transcription or summaries are requested
        if (transcribe_all or generate_summaries) and not OPENAI_API_KEY:
            if not self.set_api_key():
                messagebox.showerror("API Key Required", 
                                     "OpenAI API key is required for transcription and summaries.")
                return
        
        # Ask user for output file
        export_filename = filedialog.asksaveasfilename(
            initialdir=os.getcwd(),
            initialfile="all_conversations.json",
            title="Export All Conversations",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            defaultextension=".json"
        )
        
        if not export_filename:
            return
            
        # Make sure the filename ends with .json
        if not export_filename.lower().endswith('.json'):
            export_filename += '.json'
        
        self.status_var.set(f"Will export to: {export_filename}")
        self.root.update()
        
        # Prepare the data structure
        export_data = {
            "export_date": datetime.now().isoformat(),
            "total_conversations": len(self.conversations),
            "conversations": []
        }
        
        # Process all conversations
        progress_window = tk.Toplevel(self.root)
        progress_window.title("Export Progress")
        progress_window.geometry("400x150" if transcribe_all or generate_summaries else "400x100")
        progress_window.transient(self.root)
        progress_window.grab_set()
        
        progress_label = ttk.Label(progress_window, text="Exporting conversations...")
        progress_label.pack(pady=10)
        
        status_label = ttk.Label(progress_window, text="")
        if transcribe_all or generate_summaries:
            status_label.pack(pady=5)
        
        progress_bar = ttk.Progressbar(progress_window, length=300, mode="determinate")
        progress_bar.pack(pady=10)
        
        total_convos = len(self.conversations)
        current_convo = 0
        
        for convo_key, files in self.conversations.items():
            current_convo += 1
            progress_value = int((current_convo / total_convos) * 100)
            progress_bar["value"] = progress_value
            progress_label.config(text=f"Exporting conversation {current_convo} of {total_convos}...")
            progress_window.update()
            
            # Get part groups
            part_groups = files[0]['part_groups'] if 'part_groups' in files[0] else {}
            
            # Get is_complete flag
            is_complete = files[0]['is_complete'] if 'is_complete' in files[0] else False
            
            # Get missing parts info
            missing_parts = files[0]['missing_parts'] if 'missing_parts' in files[0] and is_complete is False else []
            
            # Get character names
            char_pair = convo_key[0]
            char1, char2 = char_pair
            
            # Get conversation number and topic
            convo_num = convo_key[1]
            topic = convo_key[2] if len(convo_key) > 2 else None
            
            # Create conversation entry
            conversation = {
                "conversation_id": f"{char1}_{char2}_convo{convo_num}" + (f"_{topic}" if topic else ""),
                "speakers": [char1, char2],
                "convo_id": convo_num,
                "topic": topic,
                "is_complete": is_complete,
                "missing_parts": missing_parts,
                "starter": None,  # Will set after lines are built
                "lines": []
            }
            
            # Process each part and its variations
            for part in sorted(part_groups.keys()):
                variations = part_groups[part]

                for i, variation in enumerate(variations):
                    # Determine the speaker
                    filename = variation['filename']
                    speaker = self._get_speaker_from_filename(filename)

                    # Update status if transcribing
                    if transcribe_all:
                        status_text = f"Processing: {filename}"
                        status_label.config(text=status_text)
                        progress_window.update()

                    # Get transcription if available or generate if requested
                    transcription = None
                    cache_file = os.path.join(self.transcriptions_dir, f"{filename}.json")
                    has_transcription = False

                    if os.path.exists(cache_file):
                        # Use existing transcription
                        try:
                            with open(cache_file, 'r', encoding='utf-8') as f:
                                transcription_data = json.load(f)
                                transcription = transcription_data.get('text', "")
                                has_transcription = True
                        except:
                            transcription = "[Transcription not available]"
                    elif transcribe_all:
                        # Generate new transcription
                        file_path = os.path.join(self.audio_dir, filename)
                        if os.path.exists(file_path):
                            try:
                                status_label.config(text=f"Transcribing: {filename}")
                                progress_window.update()

                                # Transcribe the file
                                transcription_data = self._transcribe_file(file_path)
                                if transcription_data:
                                    transcription = transcription_data.get('text', "")
                                    has_transcription = True
                                else:
                                    transcription = "[Transcription failed]"
                            except Exception as e:
                                transcription = f"[Transcription error: {str(e)}]"
                        else:
                            transcription = "[Transcription not available]"
                    else:
                        transcription = "[Transcription not available]"

                    # Create line entry
                    line = {
                        "part": part,
                        "variation": variation['variation'],
                        "speaker": speaker,
                        "filename": filename,
                        "transcription": transcription,
                        "has_transcription": has_transcription
                    }

                    # Add file last modified date
                    try:
                        file_path = os.path.join(self.audio_dir, filename)
                        file_modified_time = os.path.getmtime(file_path)
                        line["file_creation_date"] = datetime.fromtimestamp(file_modified_time).isoformat()
                    except:
                        line["file_creation_date"] = None

                    # Add to conversation lines
                    conversation["lines"].append(line)

            # Set starter as the speaker of the first line, or "unknown"
            conversation["starter"] = conversation["lines"][0]["speaker"] if conversation["lines"] else "unknown"

            # Generate summary if requested
            if generate_summaries:
                status_label.config(text=f"Generating summary for conversation {current_convo}...")
                progress_window.update()
                conversation["summary"] = self._generate_conversation_summary(conversation)
            else:
                conversation["summary"] = "[Summary not generated]"

            # Add conversation to export data
            export_data["conversations"].append(conversation)
        
        # Close progress window
        progress_window.destroy()
        
        # Show final export file path
        status_label_text = f"Saving to: {export_filename}"
        self.status_var.set(status_label_text)
        self.root.update()
        
        # Write export data to file
        try:
            with open(export_filename, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2)
            
            completion_message = f"Successfully exported {total_convos} conversations"
            if transcribe_all:
                completion_message += " with transcriptions"
            if generate_summaries:
                completion_message += " and summaries"
            completion_message += f" to {export_filename}"
            
            messagebox.showinfo("Export Complete", completion_message)
        except Exception as e:
            error_message = f"Error exporting conversations to {export_filename}:\n{str(e)}"
            messagebox.showerror("Export Error", error_message)
            print(error_message)  # Also print to console for debugging

    def load_character_mappings(self):
        """Load character name mappings from file"""
        try:
            if os.path.exists(CHARACTER_MAPPINGS_FILE):
                with open(CHARACTER_MAPPINGS_FILE, 'r', encoding='utf-8') as f:
                    self.character_mappings = json.load(f)
                    print(f"Loaded {len(self.character_mappings)} character mappings")
            else:
                # Initialize with some common examples
                self.character_mappings = {
                    "tengu": "ivy",  # Example mapping
                }
                self.save_character_mappings()
        except Exception as e:
            print(f"Error loading character mappings: {str(e)}")
            self.character_mappings = {}
    
    def save_character_mappings(self):
        """Save character name mappings to file"""
        try:
            with open(CHARACTER_MAPPINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.character_mappings, f, indent=2)
            print(f"Saved {len(self.character_mappings)} character mappings")
        except Exception as e:
            print(f"Error saving character mappings: {str(e)}")
    
    def edit_character_mappings(self):
        """Open a dialog to edit character name mappings"""
        # Create a new top-level window
        mapping_window = tk.Toplevel(self.root)
        mapping_window.title("Character Name Mappings")
        mapping_window.geometry("500x400")
        mapping_window.transient(self.root)
        mapping_window.grab_set()
        
        # Create a frame for the mappings
        frame = ttk.Frame(mapping_window, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Add instructions
        ttk.Label(frame, text="Enter mappings for character names (original name → preferred name):", 
                 wraplength=480).pack(pady=(0, 10))
        
        # Create a frame for the scrollable list
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Add scrollbar
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Create a canvas for scrolling
        canvas = tk.Canvas(list_frame, yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=canvas.yview)
        
        # Create a frame inside the canvas for the mappings
        mappings_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=mappings_frame, anchor=tk.NW)
        
        # Dictionary to store the entry widgets
        entry_pairs = {}
        
        # Function to add a new mapping row
        def add_mapping_row(original="", preferred=""):
            row = len(entry_pairs)
            
            # Original name entry
            original_var = tk.StringVar(value=original)
            original_entry = ttk.Entry(mappings_frame, textvariable=original_var, width=20)
            original_entry.grid(row=row, column=0, padx=5, pady=2, sticky=tk.W)
            
            # Arrow label
            ttk.Label(mappings_frame, text="→").grid(row=row, column=1, padx=5, pady=2)
            
            # Preferred name entry
            preferred_var = tk.StringVar(value=preferred)
            preferred_entry = ttk.Entry(mappings_frame, textvariable=preferred_var, width=20)
            preferred_entry.grid(row=row, column=2, padx=5, pady=2, sticky=tk.W)
            
            # Delete button
            delete_button = ttk.Button(mappings_frame, text="X", width=2,
                                      command=lambda r=row: delete_mapping_row(r))
            delete_button.grid(row=row, column=3, padx=5, pady=2)
            
            # Store the entries
            entry_pairs[row] = (original_var, preferred_var, original_entry, preferred_entry, delete_button)
            
            # Update the canvas scroll region
            mappings_frame.update_idletasks()
            canvas.config(scrollregion=canvas.bbox(tk.ALL))
        
        # Function to delete a mapping row
        def delete_mapping_row(row):
            if row in entry_pairs:
                # Destroy the widgets
                _, _, original_entry, preferred_entry, delete_button = entry_pairs[row]
                original_entry.destroy()
                preferred_entry.destroy()
                delete_button.destroy()
                
                # Remove from the dictionary
                del entry_pairs[row]
                
                # Update the canvas scroll region
                mappings_frame.update_idletasks()
                canvas.config(scrollregion=canvas.bbox(tk.ALL))
        
        # Add existing mappings
        for original, preferred in self.character_mappings.items():
            add_mapping_row(original, preferred)
        
        # If no mappings exist, add an empty row
        if not self.character_mappings:
            add_mapping_row()
        
        # Add button to add a new mapping
        add_button = ttk.Button(frame, text="Add Mapping", command=lambda: add_mapping_row())
        add_button.pack(pady=5)
        
        # Add buttons for save and cancel
        button_frame = ttk.Frame(frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        # Function to save mappings
        def save_mappings():
            new_mappings = {}
            for _, (original_var, preferred_var, _, _, _) in entry_pairs.items():
                original = original_var.get().strip()
                preferred = preferred_var.get().strip()
                if original and preferred:  # Only save non-empty mappings
                    new_mappings[original] = preferred
            
            self.character_mappings = new_mappings
            self.save_character_mappings()
            mapping_window.destroy()
            messagebox.showinfo("Mappings Saved", f"Saved {len(new_mappings)} character name mappings.")
        
        # Save button
        save_button = ttk.Button(button_frame, text="Save", command=save_mappings)
        save_button.pack(side=tk.RIGHT, padx=5)
        
        # Cancel button
        cancel_button = ttk.Button(button_frame, text="Cancel", command=mapping_window.destroy)
        cancel_button.pack(side=tk.RIGHT, padx=5)
        
        # Make sure the window is properly sized
        mapping_window.update_idletasks()
        canvas.config(scrollregion=canvas.bbox(tk.ALL))

    def update_variation_selection(self):
        """Update variation selection without losing the conversation selection"""
        # Check if we have a selection
        selection = self.convo_listbox.curselection()
        if selection:
            # Re-show variation options for the currently selected conversation
            self.show_variation_options()

if __name__ == "__main__":
    root = tk.Tk()
    app = ConversationPlayer(root)
    root.mainloop()
