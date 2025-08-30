import os
import re
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import pygame
from pathlib import Path
import threading
import time
import json
from datetime import datetime
import shutil
import subprocess

# Globals
TRANSCRIPTIONS_DIR = "transcriptions"
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
        characters = conversation_info['characters']
        convo_num = conversation_info['convo_num']
        
        if len(characters) >= 2:
            char1, char2 = characters[0], characters[1]
            ttk.Label(info_frame, text=f"Conversation #{convo_num} between {char1} and {char2}", 
                  font=("Helvetica", 12, "bold")).pack(anchor="w")
        else:
            # Handle monologue case
            char1 = characters[0]
            ttk.Label(info_frame, text=f"Monologue #{convo_num} by {char1}", 
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
        characters = self.conversation_info['characters']
        convo_num = self.conversation_info['convo_num']
        
        if len(characters) >= 2:
            char1, char2 = characters[0], characters[1]
            html += f"<h1>Conversation #{convo_num} between {char1} and {char2}</h1>\n"
        else:
            char1 = characters[0]
            html += f"<h1>Monologue #{convo_num} by {char1}</h1>\n"
        
        for segment in self.transcription['segments']:
            speaker = segment['speaker']
            text = segment['text']
            start_time = self.format_time(segment['start'])
            end_time = self.format_time(segment['end'])
            
            speaker_class = "char1" if speaker == characters[0] else "char2"
            
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
        characters = self.conversation_info['characters']
        convo_num = self.conversation_info['convo_num']
        
        if len(characters) >= 2:
            char1, char2 = characters[0], characters[1]
            default_name = f"{char1}_{char2}_convo{convo_num}{extension}"
        else:
            # Handle monologue case
            char1 = characters[0]
            default_name = f"{char1}_monologue{convo_num}{extension}"
        
        return filedialog.asksaveasfilename(
            initialdir=os.getcwd(),
            initialfile=default_name,
            title=f"Export Transcription as {extension}",
            filetypes=[("All Files", "*.*")],
            defaultextension=extension
        )

DEBUG_STATUS_MATCHING = True  # Set to True to enable status matching debug output

class ConversationPlayer:
    def __init__(self, root):
        self.root = root
        self.root.title("Character Conversation Player")
        self.root.geometry("800x600")
        self.audio_dir = os.getcwd()  # Default to current directory
        
        # Initialize pygame mixer for audio playback
        pygame.mixer.init()
        
        # Track playback state
        self.playing = False
        self.current_playlist = []
        self.current_track_index = 0
        
        # Transcription cache
        self.transcription_cache = {}
        
        # Exclusions: conversations to skip on export/convert
        self.excluded_convo_ids = set()
        
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
        os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)
        
        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def parse_audio_files(self):
        """Parse audio files and organize them into conversations"""
        conversations = {}
        
        # Scan for character directories
        try:
            character_dirs = [d for d in os.listdir(self.audio_dir) 
                             if os.path.isdir(os.path.join(self.audio_dir, d))]
            self.status_var.set(f"Found {len(character_dirs)} character directories")
        except Exception as e:
            messagebox.showerror("Error", f"Could not read directory: {self.audio_dir}\n{str(e)}")
            return {}
        
        if not character_dirs:
            messagebox.showinfo("Info", "No character directories found in the selected directory")
            return {}
            
        # Regular expression to extract information from filenames
        # Format: [number]-[character]-[ID]-[text].ogg
        # Character can have special characters and ID is a hexadecimal value
        filename_pattern = r'(\d+)-([^-]+)-([0-9A-F\.]+)-(.+)\.ogg'
        
        # Process each character directory
        for char_dir in character_dirs:
            char_path = os.path.join(self.audio_dir, char_dir)
            
            # Look for conversation ID directories
            convo_dirs = [d for d in os.listdir(char_path) 
                         if os.path.isdir(os.path.join(char_path, d))]
            
            for convo_id in convo_dirs:
                convo_path = os.path.join(char_path, convo_id)
                
                # Find all .ogg files in this directory
                audio_files = [f for f in os.listdir(convo_path) 
                              if f.lower().endswith('.ogg')]
                
                # Skip if no audio files found
                if not audio_files:
                    continue
                
                # Process each audio file
                convo_parts = []
                for filename in audio_files:
                    match = re.match(filename_pattern, filename)
                    if not match:
                        continue
            
                    part_num, speaker, file_id, text_snippet = match.groups()
                    
                    # Find corresponding transcript file
                    transcript_filename = os.path.splitext(filename)[0] + '.txt'
                    transcript_path = os.path.join(convo_path, transcript_filename)
                    
                    # Use the _load_transcription method to read the transcript file
                    transcript_text = ""
                    if os.path.exists(transcript_path):
                        transcript_text = self._load_transcription(transcript_path)
                    
                    # Apply character name mappings
                    speaker = self.character_mappings.get(speaker, speaker)
                    
                    # Add to conversation parts
                    convo_parts.append({
                'filename': filename,
                'part': int(part_num),
                        'speaker': speaker,
                        'file_id': file_id,
                        'text_snippet': text_snippet,
                        'transcript': transcript_text,
                        'transcript_file': transcript_filename if os.path.exists(transcript_path) else None,
                        'full_path': os.path.join(convo_path, filename)
                    })
                
                # Skip if no valid parts found
                if not convo_parts:
                    continue
                
                # Sort parts by part number
                convo_parts.sort(key=lambda x: x['part'])
                
                # Get unique speakers in this conversation
                speakers = list(set(part['speaker'] for part in convo_parts))
                speakers.sort()  # Sort alphabetically for consistency
                
                # Create a key for this conversation
                convo_key = (tuple(speakers), convo_id)
                
                # Group parts by number to handle variations
                part_groups = {}
                for part in convo_parts:
                    part_num = part['part']
                    if part_num not in part_groups:
                        part_groups[part_num] = []
                    part_groups[part_num].append(part)
                
                # Check for completeness
                unique_parts = sorted(part_groups.keys())
                min_part = min(unique_parts) if unique_parts else 0
                max_part = max(unique_parts) if unique_parts else 0
                expected_parts = list(range(min_part, max_part + 1))
                
                # A conversation is complete if there are no gaps in part numbers
                no_gaps = (len(unique_parts) == len(expected_parts) and 
                          all(p in unique_parts for p in expected_parts))
                
                # Determine missing parts if any
                missing_parts = []
                missing_reasons = []
                
                if not no_gaps:
                    gap_parts = [p for p in expected_parts if p not in unique_parts]
                    if gap_parts:
                        missing_reasons.append(f"Missing parts: {', '.join(map(str, gap_parts))}")
                        missing_parts.extend(gap_parts)
                
                # Add this conversation to our dictionary
                conversations[convo_key] = []
                
                # Add each part to the conversation
                for part in convo_parts:
                    part_info = {
                        'filename': part['filename'],
                        'part': part['part'],
                        'variation': 1,  # Default to 1 as Overwatch doesn't have variations
                        'speaker': part['speaker'],
                        'transcript': part['transcript'],
                        'is_complete': no_gaps,
                        'missing_parts': sorted(missing_parts),
                        'missing_reasons': missing_reasons,
                        'part_groups': part_groups,
                        'full_path': part['full_path']
                    }
                    conversations[convo_key].append(part_info)
        
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
        
        # Add Status File button
        status_button = ttk.Button(dir_frame, text="Select Status File", command=self.select_status_file)
        status_button.grid(row=0, column=4, padx=5, pady=5)
        
        # Add Convert to MP3 button
        convert_button = ttk.Button(dir_frame, text="Convert to MP3", command=self.convert_to_mp3)
        convert_button.grid(row=0, column=5, padx=5, pady=5)
        
        # Add Character Mappings button
        mappings_button = ttk.Button(dir_frame, text="Character Mappings", command=self.edit_character_mappings)
        mappings_button.grid(row=0, column=6, padx=5, pady=5)
        
        # Add Load Exclusions button
        exclusions_button = ttk.Button(dir_frame, text="Load Exclusions", command=self.select_exclusion_file)
        exclusions_button.grid(row=0, column=7, padx=5, pady=5)
        
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
        
        self.play_button = ttk.Button(control_frame, text="Play", command=self.play_conversation)
        self.play_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = ttk.Button(control_frame, text="Stop", command=self.stop_playback)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        # Add view transcription button
        self.view_transcript_button = ttk.Button(control_frame, text="View Transcription", command=self.view_transcription)
        self.view_transcript_button.pack(side=tk.LEFT, padx=5)
        
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
        
        # Bind selection event to show conversation details
        self.convo_listbox.bind("<<ListboxSelect>>", self.show_conversation_details)
    
    def browse_directory(self):
        """Open directory browser dialog"""
        directory = filedialog.askdirectory(initialdir=self.audio_dir, title="Select Audio Files Directory")
        if directory:
            self.dir_var.set(directory)
    
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
        
        try:
            for convo_key in self.conversations.keys():
                # Extract the character pair from the key (first element)
                char_pair = convo_key[0]
                
                # Print debug info
                print(f"Processing convo_key: {convo_key}, char_pair: {char_pair}, type: {type(char_pair)}, len: {len(char_pair)}")
            
                # Add characters to the set
                character_set.update(char_pair)
            
                # For each character in the pair, add relationships with all other characters in the pair
                for i, char1 in enumerate(char_pair):
                    if char1 not in self.character_pairs:
                        self.character_pairs[char1] = set()
                
                    # Add relationships with all other characters in the pair
                    for j, char2 in enumerate(char_pair):
                        if i != j:  # Don't add relationship with self
                            self.character_pairs[char1].add(char2)
        except Exception as e:
            print(f"Error processing conversation keys: {e}")
            print(f"Current convo_key: {convo_key}, char_pair: {char_pair}")
            import traceback
            traceback.print_exc()
            messagebox.showerror("Error", f"Error processing conversation keys: {e}")
            return
            
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
        
        # Add special options at the beginning
        related_chars = ["(ALL)", "(MONOLOGUES)"] + related_chars
        
        self.char2_dropdown.config(values=related_chars)
        
        # Update status
        monologue_count = sum(1 for key in self.conversations.keys() 
                            if len(key[0]) == 1 and char1 in key[0])
        dialog_count = len(related_chars) - 2  # Subtract the special options
        
        self.status_var.set(f"Character {char1} has {dialog_count} dialogs and {monologue_count} monologues")
    
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
                        if len(pair) > 1:
                            other_char = pair[0] if pair[1] == char1 else pair[1]
                        else:
                            # Handle monologues (single character conversations)
                            other_char = "(self)"
                        
                        all_convos.append({
                            'pair': pair,
                            'convo_num': convo_num,
                            'other_char': other_char,
                            'files': files,
                            'topic': topic
                        })
            
            # Sort by other character name, then by conversation number, then by topic
            # Use a safer sorting function that handles non-numeric conversation IDs
            def safe_sort_key(x):
                other_char = x['other_char']
                convo_id = x['convo_num']
                topic = x['topic'] or ""
                # Try to convert to int if possible, otherwise use string comparison
                try:
                    return (other_char, int(convo_id), topic)
                except ValueError:
                    return (other_char, convo_id, topic)
                
            all_convos.sort(key=safe_sort_key)
            
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
                
                # Calculate duration safely with error handling
                try:
                    duration = 0
                    for f in files:
                        if 'full_path' in f and os.path.exists(f['full_path']):
                            duration += os.path.getsize(f['full_path'])
                        elif 'filename' in f:
                            file_path = os.path.join(self.audio_dir, f['filename'])
                            if os.path.exists(file_path):
                                duration += os.path.getsize(file_path)
                    
                    duration /= 100000  # Convert to approximate seconds
                except Exception as e:
                    print(f"Error calculating duration: {e}")
                    duration = 0  # Default to 0 if there's an error
                
                starter = files[0]['speaker']
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
            
        # Check if MONOLOGUES is selected
        elif char2 == "(MONOLOGUES)":
            # Display only monologues (single-character conversations)
            monologues = []
            
            for convo_key, files in self.conversations.items():
                if len(convo_key) >= 2:
                    pair = convo_key[0]
                    convo_num = convo_key[1]
                    topic = convo_key[2] if len(convo_key) > 2 else None
                    
                    # Only include monologues of the selected character
                    if len(pair) == 1 and char1 in pair:
                        monologues.append({
                            'pair': pair,
                            'convo_num': convo_num,
                            'files': files,
                            'topic': topic
                        })
            
            # Sort by conversation number and topic
            # Use a safer sorting function that handles non-numeric conversation IDs
            def safe_sort_key(x):
                convo_id = x['convo_num']
                topic = x['topic'] or ""
                # Try to convert to int if possible, otherwise use string comparison
                try:
                    return (int(convo_id), topic)
                except ValueError:
                    return (convo_id, topic)
                
            monologues.sort(key=safe_sort_key)
            
            if not monologues:
                self.convo_listbox.insert(tk.END, f"No monologues found for {char1}")
                return
                
            # Display monologues
            for mono in monologues:
                convo_num = mono['convo_num']
                files = mono['files']
                topic = mono['topic']
                pair = mono['pair']
                
                if 'part_groups' not in files[0]:
                    continue
                
                part_groups = files[0]['part_groups']
                unique_parts = len(part_groups)
                
                # Create display string
                display_text = f"Monologue {convo_num}"
                
                # Add topic if available
                if topic:
                    display_text += f" ({topic})"
                    
                display_text += f" ({unique_parts} parts)"
                
                # Add to listbox
                self.convo_listbox.insert(tk.END, display_text)
                
                # Store the conversation key - include topic if it exists
                if topic:
                    self.convo_keys.append((pair, convo_num, topic))
                else:
                    self.convo_keys.append((pair, convo_num))
                
            self.status_var.set(f"Showing {len(monologues)} monologues for {char1}")
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
            # Use a safer sorting function that handles non-numeric conversation IDs
            def safe_sort_key(x):
                convo_id = x[0]
                topic = x[1] if len(x) > 1 else ""
                # Try to convert to int if possible, otherwise use string comparison
                try:
                    return (int(convo_id), topic)
                except ValueError:
                    return (convo_id, topic)
                
            sorted_keys = sorted(convo_groups.keys(), key=safe_sort_key)
            
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
                
                # Calculate duration safely with error handling
                try:
                    duration = 0
                    for f in files:
                        if 'full_path' in f and os.path.exists(f['full_path']):
                            duration += os.path.getsize(f['full_path'])
                        elif 'filename' in f:
                            file_path = os.path.join(self.audio_dir, f['filename'])
                            if os.path.exists(file_path):
                                duration += os.path.getsize(file_path)
                    
                    duration /= 100000  # Convert to approximate seconds
                except Exception as e:
                    print(f"Error calculating duration: {e}")
                    duration = 0  # Default to 0 if there's an error
                
                starter = files[0]['speaker']
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
    
    def show_conversation_details(self, event=None):
        """Show conversation details for the selected conversation"""
        # Check if we have a selection
        selection = self.convo_listbox.curselection()
        if not selection:
            return
            
        # Get the selected conversation key
        if not hasattr(self, 'convo_keys') or selection[0] >= len(self.convo_keys):
            return
            
        convo_key = self.convo_keys[selection[0]]
        if convo_key not in self.conversations:
            return
            
        conversation_files = self.conversations[convo_key]
        if not conversation_files:
            return
            
        # Update status with details
        speakers = ", ".join(convo_key[0])
        convo_id = convo_key[1]
        part_count = len(set(f['part'] for f in conversation_files))
        transcript_count = sum(1 for f in conversation_files if f['transcript'])
        
        status_msg = f"Conversation {convo_id} with {speakers}: {part_count} parts"
        
        if transcript_count > 0:
            status_msg += f", {transcript_count} transcripts available"
        else:
            status_msg += ", no transcripts available"
            
        self.status_var.set(status_msg)
    
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
        if 'is_complete' in conversation_files[0] and not conversation_files[0]['is_complete']:
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
        
        # Create playlist with full file paths
        self.current_playlist = []
        for file in sorted(conversation_files, key=lambda x: x['part']):
            if 'full_path' in file:
                self.current_playlist.append(file['full_path'])
                
        if not self.current_playlist:
            messagebox.showerror("Error", "No audio files found for this conversation")
            return
        
        # Start playback thread
        self.playing = True
        self.current_track_index = 0
        threading.Thread(target=self.playback_thread, daemon=True).start()
        
        # Get character names from the pair
        char_pair = convo_key[0]
        speakers = ", ".join(char_pair)
        
        # Get conversation ID
        convo_id = convo_key[1]
        
        # Update UI
        self.status_var.set("Playing...")
        self.now_playing_var.set(f"Now playing: Conversation {convo_id} with {speakers}")
    
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
        # Ensure conversion thread is stopped if active
        self.conversion_running = False
        self.root.destroy()
    
    def view_transcription(self):
        """View the transcription for the selected conversation"""
        # Check if we have a selection
        selection = self.convo_listbox.curselection()
        if not selection:
            messagebox.showinfo("Info", "Please select a conversation to view")
            return
        
        # Get the selected conversation key
        convo_key = self.convo_keys[selection[0]]
        
        # Get files for this conversation
        if convo_key not in self.conversations:
            messagebox.showerror("Error", "Conversation data not found")
            return
            
        conversation_files = self.conversations[convo_key]
        
        # Build transcription data structure
        char_pair = convo_key[0]
        convo_id = convo_key[1]
        
        transcription = {
            'conversation_id': str(convo_key),
        'characters': char_pair,
        'convo_num': convo_id,
            'timestamp': datetime.now().isoformat(),
            'segments': []
        }
            
        # Process each part
        current_time = 0.0
        for file_data in sorted(conversation_files, key=lambda x: x['part']):
            part_num = file_data['part']
            speaker = file_data['speaker']
            transcript_text = file_data['transcript']
                
            # Get file creation date
            try:
                file_path = file_data['full_path']
                file_creation_time = os.path.getctime(file_path)
                file_creation_date = datetime.fromtimestamp(file_creation_time).isoformat()
            except:
                file_creation_date = None
                
            # Create a segment for this file
            if transcript_text:
                segment = {
                    'start': current_time,
                    'end': current_time + 5.0,  # Estimate 5 seconds per line
                    'text': transcript_text,
                    'speaker': speaker,
                    'part': part_num,
                    'file_creation_date': file_creation_date
                }
                transcription['segments'].append(segment)
                current_time += 6.0  # Add a small gap
        
        # If no segments were created, show an error
        if not transcription['segments']:
            messagebox.showinfo("Info", "No transcription data available for this conversation")
            return
            
            # Cache the transcription
            cache_key = str(convo_key)
            self.transcription_cache[cache_key] = transcription
            
        # Create conversation info for the popup
        conversation_info = {
            'characters': char_pair,
            'convo_num': convo_id
        }
        
        # Show transcription popup
        self.show_transcription(transcription, convo_key)
    
    def show_transcription(self, transcription, convo_key):
        """Show the transcription in a popup window"""
        # Create popup window
        popup = TranscriptionPopup(
            self.root,
            f"Transcription - Conversation {convo_key[1]}",
            transcription,
            {
                'characters': convo_key[0],
                'convo_num': convo_key[1]
            }
        )

    def _load_transcription(self, file_path):
        """Load a transcription from a text file, trying multiple encodings"""
        if not os.path.exists(file_path):
            return ""
        
        # Try different encodings in order
        encodings = [
            'utf-8', 'utf-8-sig',  # UTF-8 with and without BOM
            'latin-1', 'cp1252',   # Western European encodings
            'iso-8859-1', 'iso-8859-15',  # ISO Western European
            'utf-16', 'utf-16-le', 'utf-16-be',  # UTF-16 variants
            'cp437'  # DOS/IBM encoding
        ]
        
        last_error = None
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    transcript_text = f.read().replace('\x00', '').strip()
                # If we get here, reading succeeded
                return transcript_text
            except UnicodeDecodeError as e:
                # Keep track of the error but continue trying
                last_error = f"Failed with {encoding}: {str(e)}"
                continue
            except Exception as e:
                # For other exceptions, print and continue
                print(f"Error with {encoding}: {file_path} - {str(e)}")
                continue
        
        # If all encodings fail, try binary read as a last resort
        try:
            with open(file_path, 'rb') as f:
                binary_data = f.read()
                # Try to decode with errors='replace' to substitute invalid chars
                transcript_text = binary_data.decode('utf-8', errors='replace').replace('\x00', '').strip()
                print(f"Used binary fallback for {file_path}")
                return transcript_text
        except Exception as e:
            print(f"Binary fallback failed for {file_path}: {str(e)}")
        
        # If we get here, all methods failed
        print(f"ERROR: Could not read {file_path} with any encoding. Last error: {last_error}")
        return ""

    def export_all_conversations(self):
        """Export all conversations to a single JSON file"""
        if not self.conversations:
            messagebox.showinfo("Info", "No conversations loaded. Please load files first.")
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
        progress_window.geometry("400x100")
        progress_window.transient(self.root)
        progress_window.grab_set()
        
        progress_label = ttk.Label(progress_window, text="Exporting conversations...")
        progress_label.pack(pady=10)
        
        progress_bar = ttk.Progressbar(progress_window, length=300, mode="determinate")
        progress_bar.pack(pady=10)
        
        total_convos = len(self.conversations)
        current_convo = 0
        
        for convo_key, files in self.conversations.items():
            # Skip excluded conversations
            try:
                convo_id_skip = convo_key[1]
                if convo_id_skip in self.excluded_convo_ids:
                    continue
            except Exception:
                pass
            current_convo += 1
            progress_value = int((current_convo / total_convos) * 100)
            progress_bar["value"] = progress_value
            progress_label.config(text=f"Exporting conversation {current_convo} of {total_convos}...")
            progress_window.update()
            
            # Get conversation details
            speakers = convo_key[0]
            convo_id = convo_key[1]
            
            # Gather statuses for this conversation
            statuses = set()
            if hasattr(self, "status_mapping"):
                for file in files:
                    fname = file["filename"]
                    if DEBUG_STATUS_MATCHING:
                        print(f"[DEBUG] Checking file: {fname}")
                    for status_key, status_val in self.status_mapping.items():
                        if DEBUG_STATUS_MATCHING:
                            print(f"[DEBUG]   Against status file key: {status_key} (basename: {os.path.basename(status_key)})")
                        if os.path.basename(status_key) == fname:
                            if DEBUG_STATUS_MATCHING:
                                print(f"[DEBUG]   MATCH: {fname} -> {status_val}")
                            statuses.add(status_val)

            # Create conversation entry
            conversation = {
                "conversation_id": f"{'-'.join(speakers)}_convo{convo_id}",
                "speakers": list(speakers),
                "convo_id": convo_id,
                "is_complete": files[0]['is_complete'] if 'is_complete' in files[0] else True,
                "missing_parts": files[0]['missing_parts'] if 'missing_parts' in files[0] else [],
                "status": list(statuses),
                "lines": []
            }
            
            # Process each part
            for file in sorted(files, key=lambda x: x['part']):
                # Create line entry
                # Replace .ogg with .mp3 in the filename
                original_filename = file['filename']
                mp3_filename = os.path.splitext(original_filename)[0] + ".mp3"
                
                line = {
                    "part": file['part'],
                    "speaker": file['speaker'],
                    "filename": mp3_filename,  # Use MP3 filename instead of OGG
                    "transcript": file['transcript'] if 'transcript' in file else "",
                    "has_transcript": bool(file.get('transcript', ""))
                }
                
                # Add file creation date
                try:
                    file_path = file.get('full_path', os.path.join(self.audio_dir, file['filename']))
                    file_creation_time = os.path.getctime(file_path)
                    line["file_creation_date"] = datetime.fromtimestamp(file_creation_time).isoformat()
                except:
                    line["file_creation_date"] = None
                
                # Add to conversation lines
                conversation["lines"].append(line)
            
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
            
            messagebox.showinfo("Export Complete", f"Successfully exported {total_convos} conversations to {export_filename}")
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

    def _get_speaker_from_filename(self, filename):
        """Extract the speaker from a filename like "1-Ana-00000006250E.0B2-I heard Talon_.ogg" """
        try:
            # Use regex to match the pattern
            pattern = r'\d+-([^-]+)-'
            match = re.search(pattern, filename)
            if match:
                speaker = match.group(1)
                # Apply character name mappings if available
                return self.character_mappings.get(speaker, speaker)
        except:
            pass
        
        # Fallback: Just return the filename itself
        return filename

    def select_status_file(self):
        """Open a dialog to select the status file and parse it"""
        file_path = filedialog.askopenfilename(
            initialdir=self.audio_dir,
            title="Select Status Text File",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if not file_path:
            return
        self.status_file_path = file_path
        self.status_var.set(f"Loaded status file: {os.path.basename(file_path)}")
        self.status_mapping = self.parse_status_file(file_path)

    def parse_status_file(self, file_path):
        """Parse the status file and return a mapping of filename to status"""
        status_map = {}
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Extract filename up to ' CRC:' or ' size:' (handles spaces in filename)
                    filename = None
                    status = None
                    if " CRC:" in line:
                        filename, rest = line.split(" CRC:", 1)
                        rest = "CRC:" + rest
                    elif " size:" in line:
                        filename, rest = line.split(" size:", 1)
                        rest = "size:" + rest
                    else:
                        parts = line.split()
                        if len(parts) < 2:
                            continue
                        filename = parts[0]
                        rest = " ".join(parts[1:])
                    # Status is always the last word
                    status = line.split()[-1]
                    filename = filename.strip()
                    status_map[filename] = status
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse status file: {str(e)}")
        return status_map

    def convert_to_mp3(self):
        """Convert all OGG files to MP3 format and save them in a new directory"""
        if not self.conversations:
            messagebox.showinfo("Info", "No conversations loaded. Please load files first.")
            return
        
        # Check if ffmpeg is installed
        try:
            subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except FileNotFoundError:
            messagebox.showerror("Error", "ffmpeg is not installed or not in the system PATH. Please install ffmpeg to use this feature.")
            return
        
        # Ask user for the output directory
        mp3_dir = filedialog.askdirectory(
            initialdir=self.audio_dir,
            title="Select Directory for MP3 Files"
        )
        
        if not mp3_dir:
            return  # User cancelled
        
        # Create MP3 output directory if it doesn't exist
        os.makedirs(mp3_dir, exist_ok=True)
        
        # Count total files to convert (excluding marked conversations)
        total_files = 0
        for convo_key, files in self.conversations.items():
            try:
                convo_id_skip = convo_key[1]
                if convo_id_skip in self.excluded_convo_ids:
                    continue
            except Exception:
                pass
            total_files += len(files)
        
        # Create progress window
        progress_window = tk.Toplevel(self.root)
        progress_window.title("Converting to MP3")
        progress_window.geometry("400x150")
        progress_window.transient(self.root)
        progress_window.grab_set()
        
        progress_label = ttk.Label(progress_window, text="Converting files to MP3...")
        progress_label.pack(pady=10)
        
        filename_label = ttk.Label(progress_window, text="", wraplength=380)
        filename_label.pack(pady=5)
        
        progress_bar = ttk.Progressbar(progress_window, length=300, mode="determinate")
        progress_bar.pack(pady=10)
        
        cancel_button = ttk.Button(progress_window, text="Cancel", command=progress_window.destroy)
        cancel_button.pack(pady=5)
        
        # Start conversion in a separate thread
        self.conversion_running = True
        threading.Thread(target=self._convert_files_thread, 
                        args=(progress_window, progress_bar, progress_label, filename_label, 
                             cancel_button, total_files, mp3_dir), 
                        daemon=True).start()
    
    def _convert_files_thread(self, progress_window, progress_bar, progress_label, filename_label, 
                            cancel_button, total_files, mp3_dir):
        """Thread for converting files to MP3"""
        current_file = 0
        converted_files = 0
        errors = []
        
        try:
            for convo_key, files in self.conversations.items():
                # Skip excluded conversations
                try:
                    convo_id_skip = convo_key[1]
                    if convo_id_skip in self.excluded_convo_ids:
                        continue
                except Exception:
                    pass
                for file in files:
                    if not self.conversion_running:
                        break
                    
                    current_file += 1
                    progress_value = int((current_file / total_files) * 100)
                    
                    # Update UI in the main thread
                    progress_window.after(0, lambda v=progress_value: progress_bar.configure(value=v))
                    progress_window.after(0, lambda f=file['filename']: 
                                         filename_label.configure(text=f"Converting: {f}"))
                    progress_window.update_idletasks()
                    
                    # Get source file path
                    source_path = file.get('full_path')
                    if not source_path or not os.path.exists(source_path):
                        source_path = os.path.join(self.audio_dir, file['filename'])
                        if not os.path.exists(source_path):
                            errors.append(f"File not found: {file['filename']}")
                            continue
                    
                    # Create target MP3 path - putting all files in the base directory
                    # Just change the extension from .ogg to .mp3
                    target_filename = os.path.splitext(os.path.basename(source_path))[0] + ".mp3"
                    target_path = os.path.join(mp3_dir, target_filename)
                    
                    # Convert file using ffmpeg
                    try:
                        cmd = [
                            "ffmpeg", 
                            "-i", source_path, 
                            "-c:a", "libmp3lame", 
                            "-q:a", "2",  # VBR quality setting
                            "-y",  # Overwrite existing files
                            target_path
                        ]
                        
                        result = subprocess.run(
                            cmd, 
                            stdout=subprocess.PIPE, 
                            stderr=subprocess.PIPE, 
                            check=False
                        )
                        
                        if result.returncode == 0:
                            converted_files += 1
                        else:
                            error_msg = result.stderr.decode('utf-8', errors='replace')
                            errors.append(f"Error converting {file['filename']}: {error_msg[:100]}...")
                    except Exception as e:
                        errors.append(f"Exception converting {file['filename']}: {str(e)}")
            
            # Update UI with final status
            if self.conversion_running:
                progress_window.after(0, lambda: progress_label.configure(
                    text=f"Conversion complete. Converted {converted_files} of {total_files} files."
                ))
                
                # Show error summary if any
                if errors:
                    error_count = len(errors)
                    error_msg = f"{error_count} errors occurred during conversion."
                    first_errors = "\n".join(errors[:5])
                    
                    if error_count > 5:
                        first_errors += f"\n\n... and {error_count - 5} more errors."
                    
                    progress_window.after(0, lambda: filename_label.configure(
                        text=f"{error_msg}\n\nFirst few errors:\n{first_errors}"
                    ))
                else:
                    progress_window.after(0, lambda: filename_label.configure(
                        text=f"All files converted successfully to: {mp3_dir}"
                    ))
                
                # Change cancel button to close
                progress_window.after(0, lambda: cancel_button.configure(text="Close"))
        except Exception as e:
            progress_window.after(0, lambda: messagebox.showerror(
                "Conversion Error", f"Error during conversion process: {str(e)}"
            ))
        finally:
            self.conversion_running = False

    def select_exclusion_file(self):
        """Open a dialog to select a JSON file that lists conversations to exclude, then load it."""
        file_path = filedialog.askopenfilename(
            initialdir=self.audio_dir,
            title="Select Exclusions JSON File",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
        )
        if not file_path:
            return
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Accept either {"deleted_conversations": [...]} or a direct list
            if isinstance(data, dict) and 'deleted_conversations' in data:
                ids = data.get('deleted_conversations', [])
            elif isinstance(data, list):
                ids = data
            else:
                ids = []
            # Normalize to strings
            ids = [str(x) for x in ids]
            self.excluded_convo_ids = set(ids)
            self.status_var.set(f"Loaded {len(self.excluded_convo_ids)} exclusions")
            messagebox.showinfo("Exclusions Loaded", f"Loaded {len(self.excluded_convo_ids)} conversation IDs to exclude.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load exclusions file: {str(e)}")

if __name__ == "__main__":
    root = tk.Tk()
    app = ConversationPlayer(root)
    root.mainloop()
