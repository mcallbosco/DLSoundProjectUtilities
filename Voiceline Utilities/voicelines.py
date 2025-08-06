import os
import sys
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
import threading
from pathlib import Path

# Import our utilities
from modules.voice_line_organizer import VoiceLineOrganizer
from modules import copy_voice_files
from modules import transcribe_voice_files

class APIKeyDialog(tk.Toplevel):
    def __init__(self, parent, current_key=""):
        super().__init__(parent)
        self.title("OpenAI API Key")
        self.geometry("600x250")  # Increased width and height
        self.resizable(True, True)  # Make dialog resizable
        self.transient(parent)
        self.grab_set()
        
        self.result = None
        
        # Create widgets
        frame = ttk.Frame(self, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Enter your OpenAI API Key:").pack(anchor=tk.W, pady=(0, 5))
        
        # API key entry with show/hide toggle
        key_frame = ttk.Frame(frame)
        key_frame.pack(fill=tk.X, pady=(0, 20))
        
        self.show_key = tk.BooleanVar(value=False)
        self.api_key = tk.StringVar(value=current_key)
        
        # Increased width of the entry field
        self.key_entry = ttk.Entry(key_frame, textvariable=self.api_key, width=70, show="*")
        self.key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        ttk.Checkbutton(key_frame, text="Show", variable=self.show_key, 
                       command=self._toggle_key_visibility).pack(side=tk.RIGHT)
        
        # Information text with increased wraplength
        info_text = (
            "Your API key will be saved in a file named '.open_ai_key' in your home directory.\n"
            "This key is used to access the OpenAI Whisper API for transcription.\n"
            "Your key is never sent anywhere except directly to OpenAI's API.\n"
            "OpenAI API keys are typically around 51 characters long and start with 'sk-'."
        )
        info_label = ttk.Label(frame, text=info_text, wraplength=560, justify=tk.LEFT, foreground="gray")
        info_label.pack(fill=tk.X, pady=(0, 20))
        
        # Buttons
        button_frame = ttk.Frame(frame)
        button_frame.pack(fill=tk.X)
        
        ttk.Button(button_frame, text="Save", command=self._save).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(button_frame, text="Cancel", command=self._cancel).pack(side=tk.RIGHT)
        
        # Set focus to the entry
        self.key_entry.focus_set()
        
        # Center the dialog
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (parent.winfo_width() - width) // 2 + parent.winfo_x()
        y = (parent.winfo_height() - height) // 2 + parent.winfo_y()
        self.geometry(f"{width}x{height}+{x}+{y}")
        
        # Make dialog modal
        self.wait_window(self)
    
    def _toggle_key_visibility(self):
        """Toggle the visibility of the API key"""
        if self.show_key.get():
            self.key_entry.config(show="")
        else:
            self.key_entry.config(show="*")
    
    def _save(self):
        """Save the API key and close the dialog"""
        self.result = self.api_key.get().strip()  # Strip whitespace from the key
        self.destroy()
    
    def _cancel(self):
        """Cancel and close the dialog"""
        self.result = None
        self.destroy()

class VoiceLineUtilitiesGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Voice Line Utilities")
        self.root.geometry("900x700")
        
        # Create notebook (tabbed interface)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Create tabs
        self.organizer_tab = ttk.Frame(self.notebook)
        self.copy_tab = ttk.Frame(self.notebook)
        self.transcribe_tab = ttk.Frame(self.notebook)
        
        self.notebook.add(self.organizer_tab, text="Organize Voice Lines")
        self.notebook.add(self.copy_tab, text="Copy Files")
        self.notebook.add(self.transcribe_tab, text="Transcribe")
        
        # Setup each tab
        self.setup_organizer_tab()
        self.setup_copy_tab()
        self.setup_transcribe_tab()
    
    def setup_organizer_tab(self):
        """Setup the Voice Line Organizer tab"""
        # Create the organizer instance with our tab as the parent
        self.organizer = VoiceLineOrganizer(self.organizer_tab)
    
    def setup_copy_tab(self):
        """Setup the Copy Files tab"""
        frame = ttk.Frame(self.copy_tab, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Variables
        self.copy_input_json = tk.StringVar()
        self.copy_source_folder = tk.StringVar()
        self.copy_output_folder = tk.StringVar()
        self.copy_output_json = tk.StringVar()
        
        # Input JSON selection
        ttk.Label(frame, text="Input JSON:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(frame, textvariable=self.copy_input_json, width=50).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(frame, text="Browse", command=self.browse_copy_input_json).grid(row=0, column=2, padx=5, pady=5)
        
        # Source folder selection
        ttk.Label(frame, text="Source Folder:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(frame, textvariable=self.copy_source_folder, width=50).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(frame, text="Browse", command=self.browse_copy_source_folder).grid(row=1, column=2, padx=5, pady=5)
        
        # Output folder selection
        ttk.Label(frame, text="Output Folder:").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Entry(frame, textvariable=self.copy_output_folder, width=50).grid(row=2, column=1, padx=5, pady=5)
        ttk.Button(frame, text="Browse", command=self.browse_copy_output_folder).grid(row=2, column=2, padx=5, pady=5)
        
        # Output JSON selection (optional)
        ttk.Label(frame, text="Output JSON (optional):").grid(row=3, column=0, sticky=tk.W, pady=5)
        ttk.Entry(frame, textvariable=self.copy_output_json, width=50).grid(row=3, column=1, padx=5, pady=5)
        ttk.Button(frame, text="Browse", command=self.browse_copy_output_json).grid(row=3, column=2, padx=5, pady=5)
        
        # Process button
        ttk.Button(frame, text="Copy Files", command=self.copy_files).grid(row=4, column=0, columnspan=3, pady=20)
        
        # Log section
        log_frame = ttk.LabelFrame(frame, text="Log", padding="10")
        log_frame.grid(row=5, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=10)
        frame.rowconfigure(5, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)
        
        # Scrolled text widget for logs
        self.copy_log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, width=80, height=15)
        self.copy_log_text.pack(fill=tk.BOTH, expand=True)
    
    def setup_transcribe_tab(self):
        """Setup the Transcribe tab"""
        frame = ttk.Frame(self.transcribe_tab, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Variables
        self.transcribe_input_json = tk.StringVar()
        self.transcribe_source_folder = tk.StringVar()
        self.transcribe_output_folder = tk.StringVar()  # New variable for output folder
        self.transcribe_consolidated_json = tk.StringVar()  # New variable for consolidated JSON
        self.transcribe_custom_vocab = tk.StringVar()  # New variable for custom vocabulary
        self.transcribe_force = tk.BooleanVar(value=False)
        
        # Input JSON selection
        ttk.Label(frame, text="Input JSON:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(frame, textvariable=self.transcribe_input_json, width=50).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(frame, text="Browse", command=self.browse_transcribe_input_json).grid(row=0, column=2, padx=5, pady=5)
        
        # Source folder selection
        ttk.Label(frame, text="Source Folder:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(frame, textvariable=self.transcribe_source_folder, width=50).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(frame, text="Browse", command=self.browse_transcribe_source_folder).grid(row=1, column=2, padx=5, pady=5)
        
        # Output folder selection
        ttk.Label(frame, text="Output Folder (optional):").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Entry(frame, textvariable=self.transcribe_output_folder, width=50).grid(row=2, column=1, padx=5, pady=5)
        ttk.Button(frame, text="Browse", command=self.browse_transcribe_output_folder).grid(row=2, column=2, padx=5, pady=5)
        
        # Consolidated JSON selection
        ttk.Label(frame, text="Consolidated JSON (optional):").grid(row=3, column=0, sticky=tk.W, pady=5)
        ttk.Entry(frame, textvariable=self.transcribe_consolidated_json, width=50).grid(row=3, column=1, padx=5, pady=5)
        ttk.Button(frame, text="Browse", command=self.browse_transcribe_consolidated_json).grid(row=3, column=2, padx=5, pady=5)
        
        # Custom vocabulary selection (new)
        ttk.Label(frame, text="Custom Vocabulary (optional):").grid(row=4, column=0, sticky=tk.W, pady=5)
        ttk.Entry(frame, textvariable=self.transcribe_custom_vocab, width=50).grid(row=4, column=1, padx=5, pady=5)
        ttk.Button(frame, text="Browse", command=self.browse_transcribe_custom_vocab).grid(row=4, column=2, padx=5, pady=5)
        
        # Force reprocessing checkbox
        ttk.Checkbutton(
            frame, 
            text="Force reprocessing of files that already have transcriptions", 
            variable=self.transcribe_force
        ).grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=5)

        # Status TXT file selection and process button
        ttk.Label(frame, text="Status TXT File (optional):").grid(row=6, column=0, sticky=tk.W, pady=5)
        self.transcribe_status_txt = tk.StringVar()
        ttk.Entry(frame, textvariable=self.transcribe_status_txt, width=80).grid(row=6, column=1, padx=5, pady=5)
        ttk.Button(frame, text="Browse", command=self.browse_transcribe_status_txt).grid(row=6, column=2, padx=5, pady=5)

        # API key status and management
        api_key_frame = ttk.Frame(frame)
        api_key_frame.grid(row=7, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)
        
        self.api_key_status = tk.StringVar(value="API Key Status: Unknown")
        ttk.Label(api_key_frame, textvariable=self.api_key_status).pack(side=tk.LEFT)
        
        ttk.Button(api_key_frame, text="Check API Key", 
                  command=self.check_api_key).pack(side=tk.RIGHT, padx=5)
        ttk.Button(api_key_frame, text="Edit API Key", 
                  command=self.edit_api_key).pack(side=tk.RIGHT, padx=5)
        
        # Process button
        ttk.Button(frame, text="Transcribe Files", command=self.transcribe_files).grid(row=9, column=0, columnspan=3, pady=20)
        
        # Progress bar
        self.transcribe_progress = ttk.Progressbar(frame, orient=tk.HORIZONTAL, length=700, mode='determinate')
        self.transcribe_progress.grid(row=10, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)
        
        # Current file label
        self.current_file_label = ttk.Label(frame, text="")
        self.current_file_label.grid(row=11, column=0, columnspan=3, sticky=tk.W, pady=5)
        
        # Log section
        log_frame = ttk.LabelFrame(frame, text="Log", padding="10")
        log_frame.grid(row=12, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=10)
        frame.rowconfigure(12, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)
        
        # Scrolled text widget for logs
        self.transcribe_log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, width=80, height=15)
        self.transcribe_log_text.pack(fill=tk.BOTH, expand=True)
        
        # Check API key on startup
        self.check_api_key()
    
    # Copy tab methods
    def browse_copy_input_json(self):
        filename = filedialog.askopenfilename(
            title="Select Input JSON File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.copy_input_json.set(filename)
            self.copy_log("Input JSON file selected: " + filename)
    
    def browse_copy_source_folder(self):
        folder = filedialog.askdirectory(title="Select Source Folder")
        if folder:
            self.copy_source_folder.set(folder)
            self.copy_log("Source folder selected: " + folder)
    
    def browse_copy_output_folder(self):
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.copy_output_folder.set(folder)
            self.copy_log("Output folder selected: " + folder)
    
    def browse_copy_output_json(self):
        filename = filedialog.asksaveasfilename(
            title="Save Output JSON As",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.copy_output_json.set(filename)
            self.copy_log("Output JSON file selected: " + filename)
    
    def copy_log(self, message):
        self.copy_log_text.insert(tk.END, message + "\n")
        self.copy_log_text.see(tk.END)
        self.root.update_idletasks()
    
    def copy_files(self):
        # Validate inputs
        if not self.copy_input_json.get():
            messagebox.showwarning("Missing Input", "Please select an input JSON file.")
            return
        
        if not self.copy_source_folder.get():
            messagebox.showwarning("Missing Input", "Please select a source folder.")
            return
        
        if not self.copy_output_folder.get():
            messagebox.showwarning("Missing Input", "Please select an output folder.")
            return
        
        # Start copying in a separate thread
        threading.Thread(target=self._copy_files_thread, daemon=True).start()
    
    def _copy_files_thread(self):
        try:
            self.copy_log("Starting file copy process...")
            
            # Redirect print statements to our log
            original_print = print
            def custom_print(*args, **kwargs):
                message = " ".join(str(arg) for arg in args)
                self.root.after(0, lambda: self.copy_log(message))
            
            # Replace print with our custom function
            sys.stdout.write = custom_print
            
            # Call the copy function
            copy_voice_files.copy_voice_files(
                self.copy_input_json.get(),
                self.copy_source_folder.get(),
                self.copy_output_folder.get(),
                self.copy_output_json.get() if self.copy_output_json.get() else None
            )
            
            # Restore original print
            sys.stdout.write = original_print
            
            self.root.after(0, lambda: messagebox.showinfo("Copy Complete", "File copying process completed successfully!"))
            
        except Exception as e:
            self.root.after(0, lambda: self.copy_log(f"ERROR: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("Error", f"An error occurred: {str(e)}"))
    
    # Transcribe tab methods
    def browse_transcribe_input_json(self):
        filename = filedialog.askopenfilename(
            title="Select Input JSON File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.transcribe_input_json.set(filename)
            self.transcribe_log("Input JSON file selected: " + filename)
    
    def browse_transcribe_source_folder(self):
        folder = filedialog.askdirectory(title="Select Source Folder")
        if folder:
            self.transcribe_source_folder.set(folder)
            self.transcribe_log("Source folder selected: " + folder)
    
    # New method for browsing output folder
    def browse_transcribe_output_folder(self):
        folder = filedialog.askdirectory(title="Select Output Folder for Transcriptions")
        if folder:
            self.transcribe_output_folder.set(folder)
            self.transcribe_log("Output folder for transcriptions selected: " + folder)
    
    def browse_transcribe_consolidated_json(self):
        filename = filedialog.asksaveasfilename(
            title="Save Consolidated Transcriptions JSON As",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.transcribe_consolidated_json.set(filename)
            self.transcribe_log("Consolidated JSON file will be saved to: " + filename)
    
    def browse_transcribe_custom_vocab(self):
        """Browse for a custom vocabulary JSON file"""
        filename = filedialog.askopenfilename(
            title="Select Custom Vocabulary JSON File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.transcribe_custom_vocab.set(filename)
            self.transcribe_log(f"Custom vocabulary file selected: {filename}")
            
            # Try to load and display vocabulary summary
            try:
                with open(filename, 'r') as f:
                    vocab_data = json.load(f)
                
                if isinstance(vocab_data, dict):
                    # Count total terms
                    total_terms = sum(len(terms) for terms in vocab_data.values() if isinstance(terms, list))
                    categories = len(vocab_data.keys())
                    self.transcribe_log(f"Loaded vocabulary with {total_terms} terms in {categories} categories")
                elif isinstance(vocab_data, list):
                    self.transcribe_log(f"Loaded vocabulary with {len(vocab_data)} terms")
            except Exception as e:
                self.transcribe_log(f"Error loading vocabulary file: {str(e)}")
    
    def transcribe_log(self, message):
        self.transcribe_log_text.insert(tk.END, message + "\n")
        self.transcribe_log_text.see(tk.END)
        self.root.update_idletasks()
    
    def check_api_key(self):
        """Check if the OpenAI API key is valid and create the key file if it doesn't exist"""
        key_path = Path.home() / ".open_ai_key"
        
        # Create the key file if it doesn't exist
        if not key_path.exists():
            try:
                with open(key_path, 'w') as f:
                    f.write("")
                self.transcribe_log(f"Created empty API key file at {key_path}")
                self.api_key_status.set("API Key Status: Not configured (empty key)")
                
                # Prompt the user to enter their API key
                self.root.after(100, self.show_first_time_key_dialog)
                return
            except Exception as e:
                self.api_key_status.set(f"API Key Status: Error creating key file ({str(e)})")
                return
        
        # Check if the key is valid
        try:
            api_key = transcribe_voice_files.load_api_key()
            if api_key:
                # Mask the API key for display
                masked_key = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "****"
                self.api_key_status.set(f"API Key Status: Valid ({masked_key})")
            else:
                self.api_key_status.set("API Key Status: Not configured (empty key)")
                # Prompt the user to enter their API key if it's empty
                self.root.after(100, self.show_first_time_key_dialog)
        except Exception as e:
            self.api_key_status.set(f"API Key Status: Error ({str(e)})")
    
    def show_first_time_key_dialog(self):
        """Show a dialog prompting the user to enter their API key for the first time"""
        response = messagebox.askyesno(
            "API Key Required",
            "An OpenAI API key is required for transcription.\n\n"
            "Would you like to configure your API key now?"
        )
        if response:
            self.edit_api_key()
    
    def edit_api_key(self):
        """Open a dialog to edit the OpenAI API key"""
        # Try to load the current key
        current_key = ""
        try:
            current_key = transcribe_voice_files.load_api_key()
        except:
            pass
        
        # Open the dialog
        dialog = APIKeyDialog(self.root, current_key)
        
        # If a new key was provided, save it
        if dialog.result is not None:
            try:
                # Save the key to the .open_ai_key file
                key_path = Path.home() / ".open_ai_key"
                with open(key_path, 'w') as f:
                    f.write(dialog.result)
                
                # Update the status
                self.check_api_key()
                
                messagebox.showinfo("API Key Saved", f"Your API key has been saved to {key_path}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save API key: {str(e)}")
    
    def transcribe_files(self):
        # Validate inputs
        if not self.transcribe_input_json.get():
            messagebox.showwarning("Missing Input", "Please select an input JSON file.")
            return
        
        if not self.transcribe_source_folder.get():
            messagebox.showwarning("Missing Input", "Please select a source folder.")
            return
        
        # Check API key
        try:
            transcribe_voice_files.load_api_key()
        except Exception as e:
            messagebox.showerror("API Key Error", str(e))
            return
        
        # Reset progress
        self.transcribe_progress['value'] = 0
        self.current_file_label.config(text="")
        
        # Start transcribing in a separate thread
        threading.Thread(target=self._transcribe_files_thread, daemon=True).start()
    
    def _transcribe_files_thread(self):
        try:
            self.transcribe_log("Starting transcription process...")
            
            # Define progress callback
            def progress_callback(file=None, current=None, total=None, status=None, error=None, complete=None, stats=None):
                if file and current is not None and total is not None:
                    # Update progress bar
                    progress_pct = (current / total) * 100
                    self.root.after(0, lambda: self.transcribe_progress.config(value=progress_pct))

                    # Update current file label
                    self.root.after(0, lambda: self.current_file_label.config(text=f"Processing: {file} ({current+1}/{total})"))

                if status:
                    # Log status message
                    self.root.after(0, lambda: self.transcribe_log(status))

                if error:
                    # Log error message
                    self.root.after(0, lambda: self.transcribe_log(f"ERROR: {error}"))

                    # Handle specific error for missing variation number
                    if "Could not find variation number in:" in error:
                        self.root.after(0, lambda: messagebox.showwarning("Missing Variation Number", f"{error}\n\nPlease check the file naming convention."))

                if complete:
                    # Show completion message
                    self.root.after(0, lambda: messagebox.showinfo("Transcription Complete", 
                                                                 f"Successfully transcribed {stats['successful']} files.\n"
                                                                 f"Failed: {stats['failed']}\n"
                                                                 f"Skipped: {stats['skipped']}"))
            
            # Call the transcribe function with all parameters
            consolidated_json_path = self.transcribe_consolidated_json.get() if self.transcribe_consolidated_json.get() else None
            transcribe_voice_files.transcribe_voice_files(
                self.transcribe_input_json.get(),
                self.transcribe_source_folder.get(),
                self.transcribe_force.get(),
                progress_callback,
                output_folder=self.transcribe_output_folder.get() if self.transcribe_output_folder.get() else None,
                consolidated_json_path=consolidated_json_path,
                custom_vocab_file=self.transcribe_custom_vocab.get() if self.transcribe_custom_vocab.get() else None
            )

            # If status TXT is provided and consolidated JSON was written, apply status mapping
            status_txt_path = self.transcribe_status_txt.get()
            if consolidated_json_path and status_txt_path:
                try:
                    # Parse the status TXT file
                    status_map = {}
                    with open(status_txt_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            parts = line.split()
                            if len(parts) < 2:
                                continue
                            path_part = parts[0]
                            status_part = parts[-1]
                            filename = os.path.splitext(os.path.basename(path_part))[0].lower()
                            status_map[filename] = status_part
                    self.transcribe_log(f"Parsed {len(status_map)} entries from status TXT file for status update.")

                    # Load the consolidated JSON
                    with open(consolidated_json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    unmatched = set(status_map.keys())
                    matched = 0

                    # Helper to update status recursively at any depth
                    def update_status_recursive(node):
                        updated = 0
                        if isinstance(node, dict):
                            for v in node.values():
                                updated += update_status_recursive(v)
                        elif isinstance(node, list):
                            for entry in node:
                                if isinstance(entry, dict) and "filename" in entry:
                                    fname = os.path.splitext(entry["filename"])[0].lower()
                                    if fname in status_map:
                                        entry["status"] = status_map[fname]
                                        updated += 1
                                        unmatched.discard(fname)
                        return updated

                    # Traverse and update the JSON structure recursively
                    total_updated = update_status_recursive(data)
                    matched = len(status_map) - len(unmatched)

                    # Save the updated consolidated JSON
                    with open(consolidated_json_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    self.transcribe_log(f"Status applied to {total_updated} files in consolidated JSON. Matched: {matched}, Unmatched: {len(unmatched)}")
                    if unmatched:
                        self.transcribe_log(f"Unmatched filenames: {', '.join(list(unmatched)[:10])}{'...' if len(unmatched) > 10 else ''}")
                except Exception as e:
                    self.transcribe_log(f"ERROR: Failed to apply status to consolidated JSON: {str(e)}")
            
        except Exception as e:
            self.root.after(0, lambda: self.transcribe_log(f"ERROR: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("Error", f"An error occurred: {str(e)}"))

    def browse_transcribe_status_txt(self):
        filename = filedialog.askopenfilename(
            title="Select Status TXT File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if filename:
            self.transcribe_status_txt.set(filename)
            self.transcribe_log(f"Status TXT file selected: {filename}")

    def apply_status_to_json(self):
        status_txt_path = self.transcribe_status_txt.get()
        input_json_path = self.transcribe_input_json.get()
        if not status_txt_path or not input_json_path:
            messagebox.showwarning("Missing Input", "Please select both a status TXT file and an input JSON file.")
            return

        # Parse the status TXT file
        status_map = {}
        try:
            with open(status_txt_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # Example: sounds/vo/mirage/ping/mirage_ping_ignore_viscous.vsnd_c CRC:001380b678 size:20293 UPDATED
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    path_part = parts[0]
                    status_part = parts[-1]
                    filename = os.path.basename(path_part)
                    status_map[filename] = status_part
            self.transcribe_log(f"Parsed {len(status_map)} entries from status TXT file.")
        except Exception as e:
            self.transcribe_log(f"ERROR: Failed to parse status TXT file: {str(e)}")
            messagebox.showerror("Error", f"Failed to parse status TXT file: {str(e)}")
            return

        # Load the input JSON
        try:
            with open(input_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.transcribe_log(f"ERROR: Failed to load input JSON: {str(e)}")
            messagebox.showerror("Error", f"Failed to load input JSON: {str(e)}")
            return

        # Helper to update status recursively
        def update_status_in_list(file_list):
            updated = 0
            for entry in file_list:
                if isinstance(entry, dict) and "filename" in entry:
                    fname = entry["filename"]
                    if fname in status_map:
                        entry["status"] = status_map[fname]
                        updated += 1
                elif isinstance(entry, str):
                    fname = entry
                    if fname in status_map:
                        # Convert to dict if needed
                        idx = file_list.index(entry)
                        file_list[idx] = {"filename": fname, "status": status_map[fname]}
                        updated += 1
            return updated

        # Traverse and update the JSON structure
        total_updated = 0
        for speaker, subjects in data.items():
            for subject, topics in subjects.items():
                for topic, files in topics.items():
                    if topic == "Pings":
                        for ping_type, ping_files in files.items():
                            total_updated += update_status_in_list(ping_files)
                    else:
                        total_updated += update_status_in_list(files)

        self.transcribe_log(f"Updated status for {total_updated} files in JSON.")

        # Ask user where to save the updated JSON
        output_path = filedialog.asksaveasfilename(
            title="Save Updated JSON As",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not output_path:
            self.transcribe_log("Save cancelled by user.")
            return

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.transcribe_log(f"Updated JSON saved to: {output_path}")
            messagebox.showinfo("Success", f"Updated JSON saved to:\n{output_path}")
        except Exception as e:
            self.transcribe_log(f"ERROR: Failed to save updated JSON: {str(e)}")
            messagebox.showerror("Error", f"Failed to save updated JSON: {str(e)}")

def main():
    root = tk.Tk()
    app = VoiceLineUtilitiesGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main() 


#C:\Users\mcall\OneDrive\OLD\2023\Documents\DLSoundProject\DLSoundProject\DLSoundProjectUtilities\Topic Utilities\alias.json
#C:\Users\mcall\OneDrive\OLD\2023\Documents\DLSoundProject\DLSoundProject\DLSoundProjectUtilities\Topic Utilities\topic_alias.json
