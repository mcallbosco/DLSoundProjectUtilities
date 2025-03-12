import os
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
import re

class VoiceLineOrganizer:
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
        ttk.Button(process_frame, text="Process Voice Lines", command=self.process_voice_lines).pack(pady=10)
        
        # Progress bar
        self.progress = ttk.Progressbar(process_frame, orient=tk.HORIZONTAL, length=700, mode='determinate')
        self.progress.pack(pady=10, fill=tk.X)
    
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
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
    
    def process_voice_lines(self):
        try:
            # Validate inputs
            if not self._validate_inputs():
                return
            
            # Reset progress
            self.progress['value'] = 0
            
            # Load alias data
            with open(self.alias_json_path.get(), 'r') as f:
                alias_data = json.load(f)
            
            # Load topic alias data
            with open(self.topic_alias_json_path.get(), 'r') as f:
                topic_alias_data = json.load(f)
            
            # Get all valid speaker names (lowercase)
            valid_speakers = set()
            for name, aliases in alias_data.items():
                if isinstance(aliases, list):
                    valid_speakers.update([a.lower() for a in aliases])
            
            # Clear disregarded heroes set
            self.disregarded_heroes = set()
            
            # Process all MP3 files in the source folder
            mp3_files = []
            for root, _, files in os.walk(self.source_folder_path.get()):
                for file in files:
                    if file.lower().endswith('.mp3'):
                        mp3_files.append(os.path.join(root, file))
            
            # Initialize result data structure
            result_data = {}
            
            # Process each file
            total_files = len(mp3_files)
            processed = 0
            disregarded = 0
            
            for file_path in mp3_files:
                # Process the file
                result = self._process_file(file_path, alias_data, topic_alias_data, valid_speakers)
                
                # Update progress
                processed += 1
                self.progress['value'] = (processed / total_files) * 100
                self.parent.update_idletasks()
                
                # Skip if the file was not processed successfully
                if result is None:
                    continue
                
                # Skip if the file was disregarded
                if result == "disregarded":
                    disregarded += 1
                    continue
                
                # Unpack the result
                speaker, subject, topic, relationship, rel_path = result
                
                # Initialize speaker if not exists
                if speaker not in result_data:
                    result_data[speaker] = {}
                
                # Initialize subject if not exists
                if subject not in result_data[speaker]:
                    result_data[speaker][subject] = {}
                
                # Handle special case for pings
                if topic.startswith("ping_"):
                    # Extract ping type
                    ping_type = topic.replace("ping_", "")
                    
                    # Skip regular pings if exclude_regular_pings is True
                    if self.exclude_regular_pings.get() and ping_type not in ["pre_game", "post_game"]:
                        continue
                    
                    # Initialize Pings category if not exists
                    if "Pings" not in result_data[speaker][subject]:
                        result_data[speaker][subject]["Pings"] = {}
                    
                    # Initialize ping type if not exists
                    if ping_type not in result_data[speaker][subject]["Pings"]:
                        result_data[speaker][subject]["Pings"][ping_type] = []
                    
                    # Add the file path
                    result_data[speaker][subject]["Pings"][ping_type].append(rel_path)
                else:
                    # Initialize topic if not exists
                    if topic not in result_data[speaker][subject]:
                        result_data[speaker][subject][topic] = []
                    
                    # Add the file path
                    result_data[speaker][subject][topic].append(rel_path)
            
            # Save the result to the output JSON file
            with open(self.output_json_path.get(), 'w') as f:
                json.dump(result_data, f, indent=2)
            
            # Log completion
            self.log(f"\nProcessing complete!")
            self.log(f"Processed {processed} files")
            self.log(f"Disregarded {disregarded} files")
            
            if self.disregarded_heroes:
                self.log(f"\nDisregarded hero names (not found in alias data):")
                for hero in sorted(self.disregarded_heroes):
                    self.log(f"  - {hero}")
            
            self.log(f"\nOutput saved to: {self.output_json_path.get()}")
            
            # Show completion message
            messagebox.showinfo("Processing Complete", f"Successfully processed {processed} files.\nOutput saved to: {self.output_json_path.get()}")
            
        except Exception as e:
            messagebox.showerror("Error", f"An error occurred: {str(e)}")
            self.log(f"ERROR: {str(e)}")
    
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
            filename_without_ext = os.path.splitext(filename)[0]
            
            # Parse the filename based on the specified structure
            # Pattern: speaker_ally/enemy_subject_topic_variation
            # Example: astro_ally_operative_kill_01.mp3
            
            # First, determine if it's an ally or enemy pattern
            if "_ally_" in filename_without_ext:
                relationship = "ally"
                parts = filename_without_ext.split("_ally_", 1)
                speaker = parts[0]
                rest = parts[1]
            elif "_enemy_" in filename_without_ext:
                relationship = "enemy"
                parts = filename_without_ext.split("_enemy_", 1)
                speaker = parts[0]
                rest = parts[1]
            else:
                # Skip files that don't match the pattern
                return None
            
            # Check if speaker is valid
            if speaker.lower() not in valid_speakers:
                self.disregarded_heroes.add(speaker.capitalize())
                return "disregarded"
            
            # Now parse the rest of the filename
            # Find the last underscore followed by numbers (variation)
            match = re.search(r'_(\d+)$', rest)
            if not match:
                self.log(f"Could not find variation number in: {filename}")
                return None
            
            variation = match.group(1)
            # Remove the variation part from the rest
            rest_without_variation = rest[:match.start()]
            
            # The first part before underscore is the subject
            subject_parts = rest_without_variation.split('_', 1)
            if len(subject_parts) < 2:
                self.log(f"Could not parse subject in: {filename}")
                return None
            
            subject = subject_parts[0]
            topic_raw = subject_parts[1]
            
            # Check if subject is a valid hero name
            if subject.lower() not in valid_speakers:
                self.disregarded_heroes.add(subject.capitalize())
                return "disregarded"
            
            # Get proper names using alias data
            speaker_proper = self._get_proper_name(speaker, alias_data)
            subject_proper = self._get_proper_name(subject, alias_data)
            
            # Process topic
            topic_proper = self._format_topic(topic_raw, topic_alias_data)
            
            # Get relative path from source folder
            rel_path = os.path.relpath(file_path, self.source_folder_path.get())
            
            self.log(f"Processed: {filename} -> {speaker_proper}/{subject_proper} ({relationship})/{topic_proper}")
            return (speaker_proper, subject_proper, topic_proper, relationship, rel_path)
            
        except Exception as e:
            self.log(f"Error processing {file_path}: {str(e)}")
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

def main():
    root = tk.Tk()
    app = VoiceLineOrganizer(root)
    root.mainloop()

if __name__ == "__main__":
    main() 